
/* ─── Diff ─── */
function isMainTurn(e) {
  const b = e?.request?.body;
  if (!b) return false;
  const hasSys = (b.system && (typeof b.system === 'string' ? b.system.length > 0 : b.system.length > 0))
      || (typeof b.instructions === 'string' && b.instructions.length > 0)
      || !!geminiSystemInstruction(b);
  const msgs = getMessages(b);
  return hasSys || msgs.length > 1;
}

function _msgHash(msg) {
  let c = msg?.content;
  // Strip cache_control from content items (Claude Code adds/removes these between turns)
  if (Array.isArray(c)) {
    c = c.map(item => {
      if (item && typeof item === 'object' && 'cache_control' in item) {
        const { cache_control, ...rest } = item;
        return rest;
      }
      return item;
    });
  }
  const text = typeof c === 'string' ? c : JSON.stringify(c || '');
  // Simple hash: role + first 500 chars of content (200 was too short for Claude Code
  // subagents that share long system-reminder prefixes but differ in task content)
  return (msg?.role || '') + ':' + text.slice(0, 500);
}

function _getMsgHashes(entry) {
  const resolved = resolveEntryForDetail(entry);
  const msgs = getMessages(resolved?.request?.body);
  return msgs.map(_msgHash);
}

function _isPrefixOf(shorter, longer) {
  if (shorter.length === 0 || longer.length < shorter.length) return false;
  for (let i = 0; i < shorter.length; i++) {
    if (shorter[i] !== longer[i]) return false;
  }
  return true;
}

function responseIdForDiff(entry) {
  const resolved = resolveEntryForDetail(entry);
  return getResponsePayload(resolved)?.id || resolved?.response?.body?.id || '';
}

function previousResponseIdForDiff(entry) {
  const resolved = resolveEntryForDetail(entry);
  return resolved?.request?.body?.previous_response_id || getResponsePayload(resolved)?.previous_response_id || '';
}

function codexThreadKey(entry) {
  const resolved = resolveEntryForDetail(entry);
  const metadata = resolved?.request?.body?.client_metadata || {};
  let turnMetadata = metadata['x-codex-turn-metadata'];
  if (typeof turnMetadata === 'string') {
    try { turnMetadata = JSON.parse(turnMetadata); } catch(e) { turnMetadata = null; }
  }
  if (!turnMetadata || typeof turnMetadata !== 'object') return '';
  const threadId = turnMetadata.thread_id || '';
  const sessionId = turnMetadata.session_id || '';
  if (!threadId && !sessionId) return '';
  return `${sessionId}:${threadId}`;
}

function findPrevByResponseId(idx) {
  const previousId = previousResponseIdForDiff(filtered[idx]);
  if (!previousId) return -1;
  for (let i = idx - 1; i >= 0; i--) {
    if (responseIdForDiff(filtered[i]) === previousId) return i;
  }
  return -1;
}

function findPrevByCodexThread(idx) {
  const key = codexThreadKey(filtered[idx]);
  if (!key) return -1;
  for (let i = idx - 1; i >= 0; i--) {
    if (codexThreadKey(filtered[i]) === key) return i;
  }
  return -1;
}

function findNextByResponseId(idx) {
  const currentId = responseIdForDiff(filtered[idx]);
  if (!currentId) return -1;
  for (let i = idx + 1; i < filtered.length; i++) {
    if (previousResponseIdForDiff(filtered[i]) === currentId) return i;
  }
  return -1;
}

function findNextByCodexThread(idx) {
  const key = codexThreadKey(filtered[idx]);
  if (!key) return -1;
  for (let i = idx + 1; i < filtered.length; i++) {
    if (codexThreadKey(filtered[i]) === key) return i;
  }
  return -1;
}

function findPrevSameModel(idx) {
  const target = filtered[idx];
  const targetHashes = _getMsgHashes(target);

  // Strategy 1: exact Responses state link when the previous response is visible.
  const linkedIdx = findPrevByResponseId(idx);
  if (linkedIdx >= 0) return { idx: linkedIdx, isFallback: false };

  // Strategy 2: Codex WebSocket turns can contain hidden generate=false
  // prefetch responses. When previous_response_id points at one of those
  // hidden frames, use the nearest visible entry in the same Codex thread.
  const codexThreadIdx = findPrevByCodexThread(idx);
  if (codexThreadIdx >= 0) return { idx: codexThreadIdx, isFallback: false };

  // Strategy 3: find the best prefix match (longest prefix)
  let bestIdx = -1;
  let bestLen = 0;
  for (let i = idx - 1; i >= 0; i--) {
    const candidateHashes = _getMsgHashes(filtered[i]);
    if (candidateHashes.length > 0 && _isPrefixOf(candidateHashes, targetHashes)) {
      if (candidateHashes.length > bestLen) {
        bestLen = candidateHashes.length;
        bestIdx = i;
      }
    }
  }
  if (bestIdx >= 0) return { idx: bestIdx, isFallback: false };

  // Strategy 4: fallback to same model + isMainTurn (original behavior)
  const model = target?.request?.body?.model;
  const main = isMainTurn(target);
  for (let i = idx - 1; i >= 0; i--) {
    if (filtered[i]?.request?.body?.model === model && isMainTurn(filtered[i]) === main)
      return { idx: i, isFallback: true };
  }
  return { idx: -1, isFallback: false };
}

function showDiff(btn) {
  showDiffForIdx(activeIdx, btn);
}

function findNextSameModel(idx) {
  const current = filtered[idx];
  const currentHashes = _getMsgHashes(current);

  const linkedIdx = findNextByResponseId(idx);
  if (linkedIdx >= 0) return linkedIdx;

  const codexThreadIdx = findNextByCodexThread(idx);
  if (codexThreadIdx >= 0) return codexThreadIdx;

  // Strategy 3: find the next entry whose messages start with current's messages as prefix
  let bestIdx = -1;
  let bestLen = Infinity;
  for (let i = idx + 1; i < filtered.length; i++) {
    const candidateHashes = _getMsgHashes(filtered[i]);
    if (currentHashes.length > 0 && _isPrefixOf(currentHashes, candidateHashes)) {
      // Pick the closest (smallest) extension
      if (candidateHashes.length < bestLen) {
        bestLen = candidateHashes.length;
        bestIdx = i;
      }
    }
  }
  if (bestIdx >= 0) return bestIdx;

  // Strategy 2: fallback to same model + isMainTurn
  const model = current?.request?.body?.model;
  const main = isMainTurn(current);
  for (let i = idx + 1; i < filtered.length; i++) {
    if (filtered[i]?.request?.body?.model === model && isMainTurn(filtered[i]) === main) return i;
  }
  return -1;
}

function _buildDiffTargetOptions(curIdx) {
  // Collect all previous entries grouped by model for the dropdown
  const options = []; // { label, filteredIdx }
  const modelGroups = {}; // model -> [{label, filteredIdx}]
  for (let i = curIdx - 1; i >= 0; i--) {
    const e = filtered[i];
    const model = e?.request?.body?.model || 'unknown';
    const turn = displayTurnLabel(e);
    if (!modelGroups[model]) modelGroups[model] = [];
    modelGroups[model].push({ label: `${t('turn')} ${turn}`, filteredIdx: i, model });
  }
  // Flatten: for each model group (sorted by first appearance), add entries
  const seenModels = [];
  for (let i = curIdx - 1; i >= 0; i--) {
    const model = filtered[i]?.request?.body?.model || 'unknown';
    if (!seenModels.includes(model)) seenModels.push(model);
  }
  for (const model of seenModels) {
    for (const item of modelGroups[model] || []) {
      options.push(item);
    }
  }
  return options;
}

function showDiffForIdx(curIdx, triggerBtn, manualPrevIdx) {
  const prevResult = manualPrevIdx !== undefined
    ? { idx: manualPrevIdx, isFallback: false }
    : findPrevSameModel(curIdx);
  const prevIdx = prevResult.idx;
  const isFallback = prevResult.isFallback;

  if (prevIdx < 0) {
    if (triggerBtn) {
      const orig = triggerBtn.innerHTML;
      triggerBtn.textContent = t('no_prev');
      setTimeout(() => triggerBtn.innerHTML = orig, 1500);
    }
    return;
  }
  // Remove existing overlay if any
  document.querySelector('.diff-overlay')?.remove();

  const prevEntry = resolveEntryForDetail(filtered[prevIdx]);
  const curEntry = resolveEntryForDetail(filtered[curIdx]);
  const oldBody = prevEntry.request?.body || {};
  const newBody = curEntry.request?.body || {};
  const diff = structuralDiff(oldBody, newBody);
  const html = renderStructuralDiff(diff);

  // Check if prev/next diff pairs exist
  const hasPrev = findPrevSameModel(prevIdx).idx >= 0;
  const nextChainIdx = findNextSameModel(curIdx);
  const hasNext = nextChainIdx >= 0 && findPrevSameModel(nextChainIdx).idx >= 0;

  // Build dropdown options for manual selection
  const targetOptions = _buildDiffTargetOptions(curIdx);
  const optionsHtml = targetOptions.map(opt => {
    const selected = opt.filteredIdx === prevIdx ? ' selected' : '';
    const modelShort = (opt.model || '').replace('claude-', '').replace(/-\d{8}$/, '');
    return `<option value="${opt.filteredIdx}"${selected}>${opt.label} (${modelShort})</option>`;
  }).join('');
  const autoMark = manualPrevIdx === undefined && !isFallback ? ` [${t('diff_select_auto')}]` : '';
  const selectHtml = targetOptions.length > 0
    ? `<div class="diff-target-select"><span>${t('diff_select_target')}</span><select class="diff-target-dropdown">${optionsHtml}</select></div>`
    : '';

  const warningHtml = isFallback
    ? `<div class="diff-fallback-banner"><span class="dfb-icon">⚠️</span><span>${t('diff_fallback_warning')}</span></div>`
    : '';

  const overlay = document.createElement('div');
  overlay.className = 'diff-overlay';
  overlay.innerHTML = `<div class="diff-modal">
    <div class="diff-header">
      <button class="diff-nav-btn diff-nav-prev">&#9664;</button>
      <span class="diff-title">${t('turn')} ${displayTurnLabel(filtered[prevIdx])} &rarr; ${t('turn')} ${displayTurnLabel(filtered[curIdx])}</span>
      <button class="diff-nav-btn diff-nav-next" ${hasNext ? '' : 'disabled'}>&#9654;</button>
      ${selectHtml}
      <button class="diff-close">&#10005;</button>
    </div>
    ${warningHtml}
    <div class="diff-body">${html}</div>
  </div>`;

  // Dynamic nav button updater — recalculates from current filtered state
  function updateNavButtons() {
    const prevBtn = overlay.querySelector('.diff-nav-prev');
    const nextBtn = overlay.querySelector('.diff-nav-next');
    if (!prevBtn || !nextBtn) return;
    prevBtn.disabled = findPrevSameModel(prevIdx).idx < 0;
    const ni = findNextSameModel(curIdx);
    nextBtn.disabled = !(ni >= 0 && findPrevSameModel(ni).idx >= 0);
  }
  updateNavButtons();

  // In live mode, periodically refresh button state as filtered[] changes
  let navInterval = null;
  if (typeof LIVE_MODE !== 'undefined' && LIVE_MODE) {
    navInterval = setInterval(updateNavButtons, 500);
  }

  const close = () => {
    if (navInterval) clearInterval(navInterval);
    overlay.remove();
    document.removeEventListener('keydown', escHandler);
  };
  overlay.querySelector('.diff-close').onclick = close;
  overlay.onclick = e => { if (e.target === overlay) close(); };
  // Navigate to prev/next diff pair
  overlay.querySelector('.diff-nav-prev').onclick = () => {
    updateNavButtons();
    if (!overlay.querySelector('.diff-nav-prev').disabled) {
      close();
      selectEntry(filtered.indexOf(filtered[prevIdx]));
      showDiffForIdx(prevIdx);
    }
  };
  overlay.querySelector('.diff-nav-next').onclick = () => {
    updateNavButtons();
    const nextIdx = findNextSameModel(curIdx);
    if (nextIdx >= 0 && !overlay.querySelector('.diff-nav-next').disabled) {
      close();
      selectEntry(filtered.indexOf(filtered[nextIdx]));
      showDiffForIdx(nextIdx);
    }
  };
  // Manual target selection dropdown
  const dropdown = overlay.querySelector('.diff-target-dropdown');
  if (dropdown) {
    dropdown.onchange = () => {
      const selectedIdx = parseInt(dropdown.value, 10);
      showDiffForIdx(curIdx, null, selectedIdx);
    };
  }
  document.body.appendChild(overlay);
  const escHandler = e => {
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') { overlay.querySelector('.diff-nav-prev').click(); }
    if (e.key === 'ArrowRight') { overlay.querySelector('.diff-nav-next').click(); }
  };
  document.addEventListener('keydown', escHandler);
}

function msgContentEqual(a, b) {
  // Compare by role + text representation, ignoring metadata like cache_control
  return a.role === b.role && msgToText(a) === msgToText(b);
}

function msgToText(m) {
  const c = m.content;
  if (typeof c === 'string') return c;
  if (!Array.isArray(c)) return JSON.stringify(c, null, 2);
  return c.map(b => {
    if (b.type === 'text' || b.type === 'input_text' || b.type === 'output_text') return b.text || '';
    if (b.type === 'thinking') return '[thinking]\n' + (b.thinking || '');
    if (b.type === 'tool_use') return '[tool_use: ' + (b.name || '') + ']\n' + JSON.stringify(b.input, null, 2);
    if (b.type === 'tool_result') {
      const rc = b.content;
      if (typeof rc === 'string') return '[tool_result]\n' + rc;
      if (Array.isArray(rc)) return '[tool_result]\n' + rc.map(x => x.type === 'text' ? x.text : JSON.stringify(x)).join('\n');
      return '[tool_result]\n' + JSON.stringify(b, null, 2);
    }
    return JSON.stringify(b, null, 2);
  }).join('\n');
}

function structuralDiff(oldB, newB) {
  const d = { unchangedMsgs: 0, newMsgs: [], removedMsgs: [], modifiedMsgs: [],
    systemChanged: false, oldSystemLen: 0, newSystemLen: 0, oldSystemText: '', newSystemText: '',
    toolsChanged: false, oldToolCount: 0, newToolCount: 0,
    addedTools: [], removedTools: [], addedToolDetails: [], removedToolDetails: [], fieldChanges: [] };
  // Messages — compare by role+content (ignore cache_control etc.)
  const om = getMessages(oldB), nm = getMessages(newB);
  // Common prefix
  let common = 0;
  for (let i = 0; i < Math.min(om.length, nm.length); i++) {
    if (msgContentEqual(om[i], nm[i])) common++; else break;
  }
  d.unchangedMsgs = common;
  // Common suffix
  let suffix = 0;
  for (let i = 0; i < Math.min(om.length - common, nm.length - common); i++) {
    if (msgContentEqual(om[om.length - 1 - i], nm[nm.length - 1 - i])) suffix++; else break;
  }
  const oldTail = om.slice(common, om.length - suffix);
  const newTail = nm.slice(common, nm.length - suffix);
  d.suffixMsgs = suffix;
  // Try to pair changed messages by role
  let oi = 0, ni = 0;
  while (oi < oldTail.length && ni < newTail.length) {
    if (oldTail[oi].role === newTail[ni].role) {
      if (msgContentEqual(oldTail[oi], newTail[ni])) { d.unchangedMsgs++; }
      else { d.modifiedMsgs.push({ old: oldTail[oi], new: newTail[ni] }); }
      oi++; ni++;
    } else if (oi + 1 < oldTail.length && oldTail[oi + 1].role === newTail[ni].role) {
      d.removedMsgs.push(oldTail[oi]); oi++;
    } else if (ni + 1 < newTail.length && oldTail[oi].role === newTail[ni + 1].role) {
      d.newMsgs.push(newTail[ni]); ni++;
    } else {
      d.removedMsgs.push(oldTail[oi]); d.newMsgs.push(newTail[ni]);
      oi++; ni++;
    }
  }
  while (oi < oldTail.length) { d.removedMsgs.push(oldTail[oi]); oi++; }
  while (ni < newTail.length) { d.newMsgs.push(newTail[ni]); ni++; }
  // System
  const oldSys = extractSystem(oldB) || '', newSys = extractSystem(newB) || '';
  d.systemChanged = oldSys !== newSys;
  d.oldSystemLen = oldSys.length;
  d.newSystemLen = newSys.length;
  d.oldSystemText = oldSys;
  d.newSystemText = newSys;
  // Tools — find added/removed by name
  const oldToolEntries = getRequestTools(oldB);
  const newToolEntries = getRequestTools(newB);
  const oldTools = oldToolEntries.map(toolDisplayName);
  const newTools = newToolEntries.map(toolDisplayName);
  const oldToolMap = new Map(oldToolEntries.map(tool => [toolDisplayName(tool), tool]));
  const newToolMap = new Map(newToolEntries.map(tool => [toolDisplayName(tool), tool]));
  const oldSet = new Set(oldTools), newSet = new Set(newTools);
  d.addedTools = newTools.filter(n => !oldSet.has(n));
  d.removedTools = oldTools.filter(n => !newSet.has(n));
  d.addedToolDetails = d.addedTools.map(name => newToolMap.get(name)).filter(Boolean);
  d.removedToolDetails = d.removedTools.map(name => oldToolMap.get(name)).filter(Boolean);
  d.toolsChanged = d.addedTools.length > 0 || d.removedTools.length > 0 || oldTools.length !== newTools.length;
  d.oldToolCount = oldTools.length;
  d.newToolCount = newTools.length;
  // Other fields
  const skip = new Set(['messages', 'system', 'tools', 'input', 'instructions']);
  const allKeys = new Set([...Object.keys(oldB), ...Object.keys(newB)]);
  for (const k of allKeys) {
    if (skip.has(k)) continue;
    const ov = JSON.stringify(oldB[k]), nv = JSON.stringify(newB[k]);
    if (ov !== nv) d.fieldChanges.push({ key: k, oldVal: oldB[k], newVal: newB[k], added: ov === undefined, removed: nv === undefined });
  }
  return d;
}

function normalizeDiffValue(value) {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { return normalizeDiffValue(JSON.parse(trimmed)); } catch(e) { return value; }
    }
    return value;
  }
  if (Array.isArray(value)) return value.map(normalizeDiffValue);
  if (value && typeof value === 'object') {
    const normalized = {};
    for (const [key, child] of Object.entries(value)) normalized[key] = normalizeDiffValue(child);
    return normalized;
  }
  return value;
}

function formatDiffValue(value) {
  if (value === undefined) return '';
  const normalized = normalizeDiffValue(value);
  return typeof normalized === 'string' ? normalized : JSON.stringify(normalized, null, 2);
}

function renderParamChange(f) {
  const oldText = formatDiffValue(f.oldVal);
  const newText = formatDiffValue(f.newVal);
  const badgeClass = f.added ? 'add' : f.removed ? 'del' : 'change';
  const badgeText = f.added ? t('diff_added') : f.removed ? t('diff_removed') : t('diff_changed');
  return `<details class="diff-param-change" open><summary><span class="diff-param-key">${esc(f.key)}</span><span class="ds-badge ${badgeClass}">${badgeText}</span></summary><div class="diff-param-body">${renderLineDiff(oldText, newText)}</div></details>`;
}

function renderDiffToolDetail(tool, badgeClass, badgeText) {
  const name = toolDisplayName(tool) || 'unknown';
  const desc = toolDescription(tool);
  const nested = Array.isArray(tool?.tools) && tool.tools.length
    ? `<div class="diff-tool-nested"><strong>${esc(tool.tools.length + ' ' + t('badge_tools'))}</strong><ul>${tool.tools.map(child => `<li><span class="diff-tool-name">${esc(toolDisplayName(child) || 'unknown')}</span>${toolDescription(child) ? ` - ${esc(toolDescription(child).split('\n')[0])}` : ''}</li>`).join('')}</ul></div>`
    : '';
  return `<details class="diff-tool-detail"><summary><span class="diff-tool-name">${esc(name)}</span><span class="ds-badge ${badgeClass}">${badgeText}</span></summary><div class="diff-tool-body">${desc ? `<div class="diff-tool-desc">${esc(desc)}</div>` : ''}${nested}<pre class="diff-tool-json">${esc(JSON.stringify(tool, null, 2))}</pre></div></details>`;
}

function renderStructuralDiff(d) {
  let html = '';
  // ── Messages ──
  const totalNew = d.newMsgs.length, totalRm = d.removedMsgs.length, totalMod = d.modifiedMsgs.length;
  const badges = [];
  if (totalNew > 0) badges.push(`<span class="ds-badge add">+${totalNew} ${t('diff_new')}</span>`);
  if (totalRm > 0) badges.push(`<span class="ds-badge del">-${totalRm} ${t('diff_removed')}</span>`);
  if (totalMod > 0) badges.push(`<span class="ds-badge change">${totalMod} ${t('diff_changed')}</span>`);
  if (!totalNew && !totalRm && !totalMod) badges.push(`<span class="ds-badge same">${t('diff_no_change')}</span>`);
  html += `<div class="diff-section"><div class="diff-section-header">${t('section_messages')} ${badges.join(' ')}</div><div class="diff-section-body">`;
  if (d.unchangedMsgs > 0) {
    html += `<div class="diff-unchanged-bar"><span class="dub-dot"></span><strong>${d.unchangedMsgs}</strong> ${t('diff_unchanged')} (${t('diff_msg_range')}${d.unchangedMsgs})</div>`;
  }
  d.removedMsgs.forEach(m => {
    const role = m.role || 'unknown';
    const cls = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : role === 'tool' ? 'tool_result' : 'system';
    html += `<div class="diff-removed-msg" data-label="${t('diff_removed').toUpperCase()}"><div class="msg ${cls}"><div class="msg-role">${esc(role)}</div>${renderContent(m.content, role)}</div></div>`;
  });
  d.modifiedMsgs.forEach(pair => {
    const role = pair.old.role || 'unknown';
    const cls = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : role === 'tool' ? 'tool_result' : 'system';
    const oldText = msgToText(pair.old), newText = msgToText(pair.new);
    html += `<div class="diff-modified-msg"><div class="msg ${cls}"><div class="msg-role">${esc(role)} <span class="ds-badge change" style="font-size:9px;vertical-align:middle">${t('diff_changed')}</span></div>${renderLineDiff(oldText, newText)}</div></div>`;
  });
  d.newMsgs.forEach(m => {
    const role = m.role || 'unknown';
    const cls = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : role === 'tool' ? 'tool_result' : 'system';
    html += `<div class="diff-new-msg" data-label="${t('diff_new').toUpperCase()}"><div class="msg ${cls}"><div class="msg-role">${esc(role)}</div>${renderContent(m.content, role)}</div></div>`;
  });
  if (d.suffixMsgs > 0) {
    html += `<div class="diff-unchanged-bar"><span class="dub-dot"></span><strong>${d.suffixMsgs}</strong> ${t('diff_unchanged')} (${t('diff_trailing')})</div>`;
  }
  if (totalNew === 0 && totalRm === 0 && totalMod === 0 && d.unchangedMsgs === 0) html += `<div style="color:var(--text-tertiary);font-size:12px">${t('no_messages')}</div>`;
  html += `</div></div>`;

  // ── Parameters ──
  if (d.fieldChanges.length > 0) {
    html += `<div class="diff-section"><div class="diff-section-header">${t('diff_params')} <span class="ds-badge change">${d.fieldChanges.length} ${t('diff_changed')}</span></div><div class="diff-section-body">`;
    html += d.fieldChanges.map(renderParamChange).join('');
    html += `</div></div>`;
  }

  // ── System Prompt ──
  if (d.systemChanged) {
    const lenDiff = d.newSystemLen - d.oldSystemLen;
    const lenStr = lenDiff > 0 ? `+${lenDiff}` : `${lenDiff}`;
    html += `<div class="diff-section"><div class="diff-section-header">${t('diff_system')} <span class="ds-badge change">${t('diff_changed')} (${fmtChars(d.oldSystemLen)} &rarr; ${fmtChars(d.newSystemLen)}, ${lenStr} ${t('diff_chars')})</span></div>`;
    html += `<div class="diff-section-body">${renderLineDiff(d.oldSystemText, d.newSystemText)}</div></div>`;
  } else {
    html += `<div class="diff-section"><div class="diff-section-header">${t('diff_system')} <span class="ds-badge same">${fmtChars(d.newSystemLen)}, ${t('diff_unchanged_lbl')}</span></div></div>`;
  }

  // ── Tools ──
  if (d.toolsChanged) {
    html += `<div class="diff-section"><div class="diff-section-header">${t('diff_tools')} <span class="ds-badge change">${d.oldToolCount} &rarr; ${d.newToolCount}</span></div>`;
    if (d.addedTools.length || d.removedTools.length) {
      html += `<div class="diff-section-body">`;
      d.addedToolDetails.forEach(tool => { html += renderDiffToolDetail(tool, 'add', t('diff_added')); });
      d.removedToolDetails.forEach(tool => { html += renderDiffToolDetail(tool, 'del', t('diff_removed')); });
      html += `</div>`;
    }
    html += `</div>`;
  } else {
    html += `<div class="diff-section"><div class="diff-section-header">${t('diff_tools')} <span class="ds-badge same">${d.newToolCount} ${t('diff_tools_unchanged')}</span></div></div>`;
  }
  return html;
}

function lineDiff(oldText, newText) {
  const ol = oldText.split('\n'), nl = newText.split('\n');
  let pre = 0;
  while (pre < ol.length && pre < nl.length && ol[pre] === nl[pre]) pre++;
  let suf = 0;
  while (suf < ol.length - pre && suf < nl.length - pre && ol[ol.length - 1 - suf] === nl[nl.length - 1 - suf]) suf++;
  const result = [];
  const addCtx = (start, end, lines) => {
    const count = end - start;
    if (count <= 0) return;
    if (count <= 4) { for (let i = start; i < end; i++) result.push({ type: 'ctx', text: lines[i] }); }
    else { result.push({ type: 'ctx', text: lines[start] }); result.push({ type: 'ctx', text: lines[start + 1] }); result.push({ type: 'fold', count: count - 4 }); result.push({ type: 'ctx', text: lines[end - 2] }); result.push({ type: 'ctx', text: lines[end - 1] }); }
  };
  addCtx(0, pre, ol);
  const oldEnd = ol.length - suf, newEnd = nl.length - suf;
  const dels = [], adds = [];
  for (let i = pre; i < oldEnd; i++) dels.push(ol[i]);
  for (let i = pre; i < newEnd; i++) adds.push(nl[i]);
  const paired = Math.min(dels.length, adds.length);
  for (let i = 0; i < paired; i++) result.push({ type: 'change', oldText: dels[i], newText: adds[i] });
  for (let i = paired; i < dels.length; i++) result.push({ type: 'del', text: dels[i] });
  for (let i = paired; i < adds.length; i++) result.push({ type: 'add', text: adds[i] });
  addCtx(ol.length - suf, ol.length, ol);
  return result;
}

function charHighlight(text, hiStart, hiEnd, hiClass) {
  if (hiStart >= hiEnd || hiStart >= text.length) return esc(text);
  return esc(text.substring(0, hiStart)) + `<span class="${hiClass}">${esc(text.substring(hiStart, hiEnd))}</span>` + esc(text.substring(hiEnd));
}

function renderLineDiff(oldText, newText) {
  const lines = lineDiff(oldText, newText);
  let html = '<div class="sbs-diff">';
  html += '<div class="sbs-header old">OLD</div><div class="sbs-header new">NEW</div>';
  for (const ln of lines) {
    if (ln.type === 'fold') {
      html += `<div class="sbs-fold">... ${ln.count} lines ...</div>`;
      continue;
    }
    if (ln.type === 'ctx') {
      html += `<div class="sbs-cell ctx">${esc(ln.text)}</div>`;
      html += `<div class="sbs-cell ctx">${esc(ln.text)}</div>`;
    } else if (ln.type === 'change') {
      const o = ln.oldText, n = ln.newText;
      let cp = 0;
      while (cp < o.length && cp < n.length && o[cp] === n[cp]) cp++;
      let cs = 0;
      while (cs < o.length - cp && cs < n.length - cp && o[o.length - 1 - cs] === n[n.length - 1 - cs]) cs++;
      html += `<div class="sbs-cell del">${charHighlight(o, cp, o.length - cs, 'sys-diff-del-hi')}</div>`;
      html += `<div class="sbs-cell add">${charHighlight(n, cp, n.length - cs, 'sys-diff-add-hi')}</div>`;
    } else if (ln.type === 'del') {
      html += `<div class="sbs-cell del">${esc(ln.text)}</div>`;
      html += `<div class="sbs-cell empty"></div>`;
    } else if (ln.type === 'add') {
      html += `<div class="sbs-cell empty"></div>`;
      html += `<div class="sbs-cell add">${esc(ln.text)}</div>`;
    }
  }
  html += '</div>';
  return html;
}

function truncJson(v) {
  if (v === undefined) return '';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > 80 ? s.substring(0, 77) + '...' : s;
}
