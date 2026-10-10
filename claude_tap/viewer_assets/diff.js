
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
    // Object key order is not a parameter change; array order and scalar values are.
    const ov = JSON.stringify(sortedDeepValue(oldB[k])), nv = JSON.stringify(sortedDeepValue(newB[k]));
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
  return typeof normalized === 'string' ? normalized : JSON.stringify(sortedDeepValue(normalized), null, 2);
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


/* ─── Cache Diagnostics ─── */

/* Anthropic-style prompt caching bills two separate buckets, so a turn that
   writes to the cache (cache_creation > 0) while also reading from it
   (cache_read > 0) is the normal incremental-extension path: the client
   appends the new tail to a cache it just reused.  Only a write with no
   accompanying read means the prefix was actually cold. */
function isColdCacheWrite(usage) {
  if (!usage) return false;
  const created = usage.cache_creation_input_tokens || 0;
  if (created === 0) return false;
  // Engines that embed cache reads inside input_tokens (OpenAI, Gemini) do not
  // report a write counter at all, so a non-zero value cannot be interpreted.
  if (usage._cache_read_in_input) return false;
  return (usage.cache_read_input_tokens || 0) === 0;
}

/* Whether the trace shows this turn opening its conversation, rather than the
   capture merely starting here.

   A cold write with no captured predecessor has two very different causes: the
   conversation really did begin, or the capture began mid-session — a proxy
   attached to a running client, or a resumed session whose earlier cache had
   already expired.  The two are indistinguishable from the absence of a
   predecessor alone, so the message list decides: a conversation that starts
   here carries only its opening user turn, while a resumed one replays the
   history it accumulated.

   A Bedrock Converse cachePoint standing as its own user message is a
   breakpoint marker, not a conversational turn.  Counting it would make a
   genuine first request look like a resumed history. */
/* A block that carries nothing but the marker: `{cachePoint: {...}}` with no
   sibling key.  A block that merely *has* a marker alongside real content is not
   one of these — that marker is an attribute of content that must still be
   compared. */
function isMarkerOnlyBlock(block) {
  if (!block || typeof block !== 'object' || Array.isArray(block)) return false;
  if (!block.cachePoint) return false;
  const keys = Object.keys(block);
  return keys.length === 1 && keys[0] === 'cachePoint';
}

function isMarkerOnlyCachePointMessage(msg) {
  const content = msg?.content;
  if (!Array.isArray(content) || content.length === 0) return false;
  return content.every(isMarkerOnlyBlock);
}

/* Whether a request's own message list is a conversation's opening turn. */
function requestOpensConversation(entry) {
  const resolved = resolveEntryForDetail(entry) || entry;
  const msgs = getMessages(resolved?.request?.body || {})
    .filter(msg => !isMarkerOnlyCachePointMessage(msg));
  if (msgs.length === 0) return false;
  return msgs.length === 1 && msgs[0]?.role === 'user';
}

/* Whether the capture contains the beginning of this entry's conversation.

   A first cache write does not have to be the first turn. A session stays below
   the provider's minimum cacheable size for as long as its prompt is short, so
   the opening turns write nothing and `findCachePredecessor` rightly skips them
   — they never established a cache. By the time one turn finally crosses the
   threshold, its own message list holds the whole replayed exchange, so asking
   only about the current message count called it a mid-session capture and the
   card fell back to unknown.

   Looking for the opening turn among the earlier same-session entries settles it
   from the trace instead: if the capture saw the session start and no earlier
   turn established a cache, there is no unseen predecessor and this is the
   session's initial write. Without a session key the earlier turns cannot be
   attributed to this conversation, so only the entry's own list is consulted. */
function traceStartsConversation(entry) {
  if (requestOpensConversation(entry)) return true;

  const session = cacheSessionKey(entry);
  if (!session) return false;
  const idx = findEntryIdxInAll(entry);
  if (idx <= 0) return false;

  for (let i = idx - 1; i >= 0; i--) {
    const cand = entries[i];
    if (!isNavigableTraceEntry(cand)) continue;
    if (cacheSessionKey(cand) !== session) continue;
    if (requestOpensConversation(cand)) return true;
  }
  return false;
}

/* Whether any earlier turn of this same session established a cache.

   Distinct from `findCachePredecessor`, which reports the turn a miss should be
   measured against and keeps an unattributable neighbor as a fallback.  Here the
   question is narrower and answerable: is there a turn the trace positively
   places in this conversation that left a cache behind?  If not, an unconfirmed
   predecessor cannot be the cache this write failed to hit. */
function earlierSessionTurnCached(entry) {
  const session = cacheSessionKey(entry);
  if (!session || typeof entries === 'undefined') return false;
  const idx = findEntryIdxInAll(entry);
  if (idx <= 0) return false;
  for (let i = idx - 1; i >= 0; i--) {
    const cand = entries[i];
    if (!isNavigableTraceEntry(cand)) continue;
    if (cacheSessionKey(cand) !== session) continue;
    if (participatesInCache(getUsage(cand))) return true;
  }
  return false;
}

/* A turn that neither read nor wrote the cache never established one, so it
   cannot be the predecessor whose expiry or edit explains a later cold write. */
function participatesInCache(usage) {
  if (!usage) return false;
  if (usage._cache_read_in_input) return false;
  return (usage.cache_creation_input_tokens || 0) > 0
    || (usage.cache_read_input_tokens || 0) > 0;
}

/* The tool list a cache comparison should walk, in prompt order.

   `getRequestTools` is built for display, so it deduplicates by name, merges in
   tools observed only in responses, and drops the standalone `cachePoint` marker
   that Bedrock Converse uses to declare a tool-scoped breakpoint.  All three are
   right for a tool panel and wrong here: the cache is keyed on the bytes the
   request actually sent, in the order it sent them, and the marker is the only
   thing that says how far the cached tool prefix reaches.

   Converse also nests its specs under `body.toolConfig.tools`, where nothing
   above would find them.  Without this a tool-scoped Converse cache reads as
   unknown extent, so a tool-schema edit is reported as unknown, or a later
   history change is blamed for a miss the earlier tool change caused. */
function cacheToolList(body) {
  const converse = body?.toolConfig?.tools;
  if (Array.isArray(converse) && converse.length) return { tools: converse, converse: true };
  /* `body.tools` verbatim when the request carries it, for the reason above: two
     definitions can share a display name, and deduplicating them drops every one
     after the first, so editing or removing a later duplicate inside the cached
     prefix left `toolsChanged` false and the cold write was reported as unknown
     or blamed on a later segment. `getRequestTools` still covers the shapes that
     keep tools elsewhere -- Gemini nests them, and the Responses API can imply
     them from `input`. */
  if (Array.isArray(body?.tools)) return { tools: body.tools, converse: false };
  return { tools: getRequestTools(body), converse: false };
}

/* Collect every cache_control breakpoint declared in a request body, in prompt
   order.  Anthropic caches the prefix *up to* each breakpoint, so the last one
   marks the end of the cached region: edits after it cannot invalidate
   anything, and the TTL that matters is the longest one declared.

   `blockIndex` records where inside a message the breakpoint sat, because a
   breakpoint on an early content block leaves the later blocks of that same
   message uncached.  It is -1 for scopes whose unit is the whole item.

   Bedrock Converse declares breakpoints differently: a standalone
   `{cachePoint: {type: 'default'}}` block rather than a property on the block
   it follows.  Such a marker caches everything ahead of it, so it belongs to
   the preceding block. */
function cacheBreakpoints(body) {
  const toolInfo = cacheToolList(body);
  const out = [];
  const push = (scope, index, control, blockIndex = -1) => {
    if (control && typeof control === 'object') out.push({ scope, index, control, blockIndex });
  };
  const system = body?.system;
  if (Array.isArray(system)) {
    system.forEach((block, i) => {
      push('system', i, block?.cache_control);
      if (block?.cachePoint) push('system', i, block.cachePoint);
    });
  }
  toolInfo.tools.forEach((tool, i) => {
    push('tools', i, tool?.cache_control);
    // Converse's marker stands on its own, so it caches the specs ahead of it.
    if (tool?.cachePoint) push('tools', i - 1, tool.cachePoint);
  });
  getMessages(body).forEach((msg, i) => {
    const content = msg?.content;
    if (Array.isArray(content)) {
      content.forEach((block, b) => {
        push('messages', i, block?.cache_control, b);
        // A cachePoint block is the marker itself, so the cached prefix ends at
        // the block before it.
        if (block?.cachePoint) push('messages', i, block.cachePoint, b - 1);
      });
    }
  });
  return out;
}

/* Number of leading messages covered by the cached prefix.  A breakpoint caches
   everything up to and including its own position, so the count is the highest
   message-scope breakpoint index plus one; 0 means no breakpoint reaches the
   message list and none of the messages are cached.

   A Bedrock cachePoint standing as a message's *first* block covers nothing of
   that message, so the prefix ends with the message before it. */
function cachedMsgCount(body) {
  let last = -1;
  for (const bp of cacheBreakpoints(body)) {
    if (bp.scope !== 'messages') continue;
    const covered = bp.blockIndex < 0 ? bp.index - 1 : bp.index;
    if (covered > last) last = covered;
  }
  return last + 1;
}

/* Which prompt segments a request asks to be cached, as an ordered prefix.

   Anthropic hashes the prompt in a fixed order — tools, then system, then
   messages — and a breakpoint caches everything before it.  So a breakpoint
   anywhere implies the segments ahead of it are cached too, which is what makes
   `cache_read === 0` such a strong signal: it means even the *first* segment
   missed, and therefore an edit in a later segment cannot be the cause.

   `toolCount` and `systemCount` say how many leading items of each segment the
   cache actually covers, so a comparison can stop there: a breakpoint on tool 3
   leaves tools 4..n outside the cache, and editing one of those cannot be the
   cause of a cold write.

   When the captured body declares no breakpoint at all — a proxy that strips
   cache_control, or a capture that omits it — the *position* of the breakpoint is
   unknown, and with it the extent of every segment, so no segment is reported as
   covered and no structural comparison happens.  Guessing would be worse than
   declining: if the real breakpoint sat on a tool, the system prompt followed it
   and was never cached, and a later system edit would then be named as the
   confident cause of a miss that expiry or eviction actually caused. */
function cachedScopes(body) {
  const bps = cacheBreakpoints(body);
  if (bps.length === 0) {
    // No segment is known to be covered, so every comparison below is skipped
    // and the diagnosis falls through to TTL or `unknown`.
    return { tools: false, system: false, msgs: 0, bps };
  }
  const lastIndex = scope => {
    let last = -1;
    for (const bp of bps) {
      if (bp.scope === scope && bp.index > last) last = bp.index;
    }
    return last;
  };
  const msgs = cachedMsgCount(body);
  const toolIdx = lastIndex('tools');
  const sysIdx = lastIndex('system');
  /* A message-scope breakpoint caches every earlier segment even when it
     covers zero messages — a Bedrock cachePoint as the first block of the
     first message is the example.  `msgs > 0` would mark tools and system
     uncached in that case and hide a later tool/system edit. */
  const hasMessageBp = bps.some(bp => bp.scope === 'messages');
  // A breakpoint in a later segment caches every earlier segment in full.  The
  // count is over the same list the comparison walks, so a Converse cachePoint
  // marker counts as a position; normalizeCacheable strips it from both sides,
  // so including it cannot invent a difference.
  //
  // `tools` means the tool segment is inside the cached prefix, not that a
  // non-empty list was observed.  A message- or system-level breakpoint still
  // caches an empty tool list, so adding the first tool or removing the last
  // one has to be compared rather than skipped.
  const coveredByLater = sysIdx >= 0 || hasMessageBp;
  const toolCount = coveredByLater ? cacheToolList(body).tools.length : toolIdx + 1;
  const systemBlocks = Array.isArray(body?.system) ? body.system.length : 0;
  const systemCount = hasMessageBp ? systemBlocks : sysIdx + 1;
  /* Whether the count is a boundary the request *declared* or just the size of a
     segment cached whole.  A breakpoint in a later segment caches this one
     entirely, so its count follows from the content -- adding the first tool
     moves it from 0 to 1, and that is the edit, not a change of extent.  Only a
     breakpoint inside the segment makes the count a bound in its own right. */
  return {
    tools: toolIdx >= 0 || coveredByLater,
    system: sysIdx >= 0 || hasMessageBp,
    msgs,
    toolCount,
    systemCount,
    toolBounded: toolIdx >= 0 && !coveredByLater,
    systemBounded: sysIdx >= 0 && !hasMessageBp,
    bps,
  };
}

/* Cache lifetime for a turn's write, in ms.

   The tier is declared on the request (`cache_control.ttl`), which is where it
   is observable for every provider; Anthropic additionally echoes the tier it
   billed under `usage.cache_creation`.  Both sources describe the same chain,
   and the question a caller asks is how long *anything* in it stays live, so the
   answer is the longest lifetime either one names.

   Taking only the response would be wrong in the mixed case that Anthropic
   documents and bills for: a turn reusing a 1-hour prefix while appending a new
   5-minute tail reports only the newly written 5-minute tokens under
   `cache_creation`, because that is all it wrote.  Reading that as the whole
   chain's age makes a 1-hour prefix look expired after six minutes, so an edit
   inside that still-live prefix is suppressed and the card reports TTL expiry
   for a miss the edit caused.

   Returns 0 when neither source names a tier, so callers can decline to make a
   confident expiry claim instead of assuming the 5-minute default. */
function cacheTtlMs(usage, body) {
  let longest = 0;
  const detail = usage && usage.cache_creation;
  if (detail && typeof detail === 'object') {
    if (detail.ephemeral_1h_input_tokens) longest = 3600000;
    else if (detail.ephemeral_5m_input_tokens) longest = 300000;
  }
  for (const bp of cacheBreakpoints(body)) {
    const ms = parseCacheTtl(bp.control.ttl);
    if (ms > longest) longest = ms;
  }
  return longest;
}

/* Parse a cache_control TTL such as "5m" or "1h" into ms.  An ephemeral
   breakpoint with no explicit ttl uses Anthropic's 5-minute default. */
function parseCacheTtl(ttl) {
  if (ttl === undefined || ttl === null || ttl === '') return 300000;
  if (typeof ttl === 'number') return ttl > 0 ? ttl * 1000 : 0;
  const m = String(ttl).trim().match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|h)?$/i);
  if (!m) return 0;
  const value = parseFloat(m[1]);
  const unit = (m[2] || 's').toLowerCase();
  const scale = { ms: 1, s: 1000, m: 60000, h: 3600000 }[unit];
  return value > 0 ? value * scale : 0;
}

/* Locate an entry in `entries`.  Detail views are handed a *resolved* entry
   (a fresh object built from the stub), so identity comparison never matches
   and the stable key has to be used instead. */
function findEntryIdxInAll(entry) {
  if (!entry || typeof entries === 'undefined') return -1;
  const direct = entries.indexOf(entry);
  if (direct >= 0) return direct;
  if (typeof entryStableKey !== 'function') return -1;
  const key = entryStableKey(entry);
  if (!key) return -1;
  for (let i = 0; i < entries.length; i++) {
    if (entryStableKey(entries[i]) === key) return i;
  }
  return -1;
}

/* Model of an entry, for deciding whether two turns share a cache.  Prompt
   caches are per-model, so a switch means the new model starts cold no matter
   how similar the prompts look. */
function extractModelFromPath(path) {
  if (!path || typeof path !== 'string') return '';
  const bedrockMatch = path.match(/\/model\/([^/]+)/i);
  if (bedrockMatch) return bedrockMatch[1];
  /* Any `/models/<name>` segment, at whatever depth. Requiring `/v1/models/`
     directly missed Vertex, which nests the name under the publisher:
     `/v1/projects/p/locations/l/publishers/anthropic/models/claude-opus-4-7:rawPredict`.
     Every such turn reported no model, so a model switch looked like one cache
     chain and collected a structural or TTL diagnosis it had not earned. The
     backend's `_model_from_path` already matches the segment generically. */
  const modelsMatch = path.match(/\/models?\/([^/:?]+)/i);
  if (modelsMatch) return modelsMatch[1];
  return '';
}

function cacheModelOf(entry) {
  const resolved = resolveEntryForDetail(entry) || entry;
  const bodyModel = resolved?.request?.body?.model || entry?.request?.body?.model || '';
  if (bodyModel) return bodyModel;
  const path = resolved?.request?.path || entry?.request?.path || '';
  return extractModelFromPath(path);
}

/* Conversation a turn belongs to, or '' when the capture does not say.

   Two concurrent sessions can share an identical system prompt, so without this
   the nearest earlier turn may come from an unrelated conversation whose
   timestamps say nothing about this one's cache.  An explicit cache key takes
   precedence, since that is what the provider actually keys the cache on. */
function cacheSessionKey(entry) {
  const resolved = resolveEntryForDetail(entry) || entry;
  const body = resolved?.request?.body || {};
  if (body.prompt_cache_key) return `key:${body.prompt_cache_key}`;
  const headers = resolved?.request?.headers || {};
  const header = headers['X-Claude-Code-Session-Id'] || headers['x-claude-code-session-id']
    || headers['x-codex-app-session-id'] || headers.session_id || headers['session-id'] || '';
  if (header) return `session:${header}`;
  const codex = typeof codexThreadKey === 'function' ? codexThreadKey(resolved) : '';
  return codex ? `thread:${codex}` : '';
}

/* Whether positive evidence ties a candidate to this entry's cache chain, as
   opposed to the two merely sitting next to each other in the capture.

   Kept separate from the search because the two callers see different payloads.
   In remote dashboard mode every candidate is a metadata stub whose headers and
   message bodies are synthesized, so nothing can be confirmed and the search has
   to settle for `exact: false`; once the real predecessor has been fetched, the
   same question is worth asking again against the payload that just arrived. */
function cachePredecessorIsExact(entry, cand) {
  if (!entry || !cand) return false;
  const prevId = previousResponseIdForDiff(entry);
  if (prevId && responseIdForDiff(cand) === prevId) return true;
  const threadKey = codexThreadKey(entry);
  if (threadKey && codexThreadKey(cand) === threadKey) return true;
  const session = cacheSessionKey(entry);
  if (session && cacheSessionKey(cand) === session) return true;
  /* `_msgHash` truncates at 500 characters so the sidebar prefix search can
     stay cheap.  Exactness cannot use that shortcut: two unrelated turns that
     share a long system-reminder prefix would look identical and then support
     a confident structural or TTL diagnosis against the wrong cache chain.
     Full normalized content is the evidence; a truncated-hash match is not. */
  const candPrefix = cacheMessageExactPrefix(cand);
  return candPrefix.length > 0 && _isPrefixOf(candPrefix, cacheMessageExactPrefix(entry));
}

function cacheMessageExactPrefix(entry) {
  const resolved = resolveEntryForDetail(entry);
  return getMessages(resolved?.request?.body).map(msg => JSON.stringify(normalizeCacheableMessage({
    role: msg?.role || '',
    content: msg?.content,
  })));
}

/* Find the turn whose cache the given entry was expected to reuse.

   Unlike findPrevSameModel this walks the *unfiltered* history, because the
   sidebar filters are a viewing choice: hiding a turn must not change what
   caused a cache miss.  Candidates must share the model and the conversation,
   and must have taken part in caching themselves; the search stops at the
   nearest one that qualifies — that is the turn whose cache was live.

   Returns { entry, idx, exact } where `exact` marks a predecessor confirmed by
   positive evidence — a Responses-state link, a shared session, or a message
   prefix — rather than by adjacency alone.  Claude Code rewrites its message
   tail every turn, so the prefix test alone would reject genuine predecessors;
   a shared session identifier is equally conclusive.

   `skipIdxs` continues this same walk after a dashboard fetch rejects a
   fallback: stubs omit session headers, so the nearest unlabeled neighbor may
   belong to another conversation, and the older exact match is still ahead. */
function findCachePredecessor(entry, skipIdxs) {
  const idx = findEntryIdxInAll(entry);
  if (idx <= 0) return { entry: null, idx: -1, exact: false };
  const model = cacheModelOf(entry);
  const session = cacheSessionKey(entry);
  const skip = skipIdxs && typeof skipIdxs.has === 'function' ? skipIdxs : null;

  let bestFallback = null;

  for (let i = idx - 1; i >= 0; i--) {
    if (skip && skip.has(i)) continue;
    const cand = entries[i];
    if (!isNavigableTraceEntry(cand)) continue;
    if (cacheModelOf(cand) !== model) continue;
    if (!participatesInCache(getUsage(cand))) continue;
    const candSession = cacheSessionKey(cand);
    const confirmed = cachePredecessorIsExact(entry, cand);

    if (session) {
      if (candSession === session) {
        return { entry: cand, idx: i, exact: true };
      }
      if (!candSession && !bestFallback) {
        bestFallback = { entry: cand, idx: i, exact: confirmed };
      }
    } else {
      /* No session key: the nearest cache-bearing neighbor may be another
         interleaved conversation. Keep it only as a fallback and prefer a
         later positively linked candidate. */
      if (confirmed) return { entry: cand, idx: i, exact: true };
      if (!bestFallback) bestFallback = { entry: cand, idx: i, exact: false };
    }
  }
  if (bestFallback) return bestFallback;
  return { entry: null, idx: -1, exact: false };
}

/* How far into one message the cached prefix reaches: the furthest block a
   breakpoint sat on, or -1 when none did.  A breakpoint on an early block
   leaves the rest of that message outside the cache, and an earlier one is
   already covered by the prefix it implies. */
function lastBlockBound(scopes, msgIdx) {
  let last = -1;
  for (const bp of scopes.bps || []) {
    if (bp.scope === 'messages' && bp.index === msgIdx && bp.blockIndex > last) last = bp.blockIndex;
  }
  return last;
}

/* Whether the two requests asked for the same region to be cached.

   Every named cause rests on comparing like with like: the same segments, to the
   same depth, on both sides.  When the extents differ the content comparison is
   no longer evidence — bounding to the shorter one compares only the survivors
   and finds them equal, so the truncation that actually broke the chain goes
   unmentioned, while blaming the truncation needs to know which of several
   declared breakpoints the provider matched, and the trace does not record that.

   Rather than reason about who cached less, the diagnosis declines to name a
   cause at all unless both sides drew the same boundary — including the block
   the boundary landed on inside the last cached message. */
function cachedRegionsAgree(prevScopes, curScopes) {
  if (prevScopes.msgs !== curScopes.msgs) return false;
  if (prevScopes.tools !== curScopes.tools || prevScopes.system !== curScopes.system) return false;
  /* Only a boundary the request declared has to match.  A segment cached whole by
     a later breakpoint has no boundary of its own -- its count is content, and
     comparing counts there would report every added tool as a change of extent
     and so never name the edit. */
  if (prevScopes.toolBounded !== curScopes.toolBounded) return false;
  if (prevScopes.systemBounded !== curScopes.systemBounded) return false;
  if (prevScopes.toolBounded && prevScopes.toolCount !== curScopes.toolCount) return false;
  if (prevScopes.systemBounded && prevScopes.systemCount !== curScopes.systemCount) return false;
  const lastMsg = curScopes.msgs - 1;
  return lastMsg < 0 || lastBlockBound(prevScopes, lastMsg) === lastBlockBound(curScopes, lastMsg);
}

/* Compare the two request bodies over the region the cache actually covers.

   Anthropic hashes the prompt as an ordered chain — tools, then system, then
   messages — so the first segment that differs is the one that broke the cache
   and everything after it is irrelevant.  Reporting in that order matters: a
   turn that edited both its system prompt and its message history had its cache
   invalidated by the system prompt, and naming the later change would send the
   reader looking in the wrong place.

   Only content the request asked to cache is compared.  Appending a new user
   message beyond the last breakpoint is the normal path and must not be
   reported as an invalidating edit.  Within the cached region any difference
   counts, including a modification at the same position — a shared prefix does
   not mean an unchanged prefix.

   Tool definitions are compared in full (name, description and input_schema),
   because a schema edit that keeps every name invalidates the cache just as
   surely as adding a tool.

   The caller has already established that both sides cached the same extent, so
   the counts here bound the comparison rather than differing between the two. */
function diffCachedRegion(prevBody, curBody, prevScopes, curScopes) {
  /* `historyFrom` is the index of the first message that differs, so the caller
     can tell whether an earlier message checkpoint was itself unchanged. */
  const out = { systemChanged: false, toolsChanged: false, historyChanged: false, historyFrom: -1 };

  if (prevScopes.tools && curScopes.tools) {
    let prevTools = cacheToolList(prevBody).tools;
    let curTools = cacheToolList(curBody).tools;
    // A later-segment breakpoint caches the whole tool list, including an
    // empty one.  Bounding to the declared count would hide a first-tool add
    // or last-tool remove.  Only a tool-scope breakpoint needs that bound.
    if (!(prevScopes.system && curScopes.system) && curScopes.toolCount) {
      prevTools = prevTools.slice(0, curScopes.toolCount);
      curTools = curTools.slice(0, curScopes.toolCount);
    }
    out.toolsChanged = JSON.stringify(normalizeCacheable(prevTools))
      !== JSON.stringify(normalizeCacheable(curTools));
    if (out.toolsChanged) return out;
  }

  if (prevScopes.system && curScopes.system) {
    let prevSys = prevBody?.system;
    let curSys = curBody?.system;
    if (curScopes.systemCount && Array.isArray(prevSys) && Array.isArray(curSys)) {
      prevSys = prevSys.slice(0, curScopes.systemCount);
      curSys = curSys.slice(0, curScopes.systemCount);
    }
    out.systemChanged = JSON.stringify(normalizeCacheable(prevSys))
      !== JSON.stringify(normalizeCacheable(curSys));
    if (out.systemChanged) return out;
  }

  if (curScopes.msgs > 0) {
    const prevMsgs = getMessages(prevBody);
    const curMsgs = getMessages(curBody);
    const lastMsg = curScopes.msgs - 1;
    // The boundary can land inside the last cached message, leaving its later
    // blocks outside the cache; both sides agree on where, so one bound serves.
    const blockBound = lastBlockBound(curScopes, lastMsg);
    for (let i = 0; i < curScopes.msgs; i++) {
      let a = prevMsgs[i];
      let b = curMsgs[i];
      // A cached prefix that lost messages was truncated, which invalidates it
      // even when every surviving message still matches.
      if (a === undefined || b === undefined) { out.historyChanged = true; out.historyFrom = i; break; }
      if (i === lastMsg && blockBound >= 0 && Array.isArray(a?.content) && Array.isArray(b?.content)) {
        a = { ...a, content: a.content.slice(0, blockBound + 1) };
        b = { ...b, content: b.content.slice(0, blockBound + 1) };
      }
      if (JSON.stringify(normalizeCacheableMessage(a)) !== JSON.stringify(normalizeCacheableMessage(b))) {
        out.historyChanged = true;
        out.historyFrom = i;
        break;
      }
    }
  }
  return out;
}

/* Sort keys for a deterministic comparison without touching any value.

   A field that merely shares a marker's name is ordinary payload -- a tool
   schema property called `cache_control`, or the same key inside a cached
   `tool_use` input.  Erasing those made an edited payload compare equal to its
   predecessor, so the structural invalidation they caused fell through to the
   unknown diagnosis. */
function sortedDeepValue(value) {
  if (Array.isArray(value)) return value.map(sortedDeepValue);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = sortedDeepValue(value[k]);
    return out;
  }
  return value;
}

/* Drop the markers from one block's own keys, leaving everything below intact.
   That top level is where the protocol puts them: on a tool spec, a system
   block, or a message content block. */
function stripCacheMarkers(block) {
  if (!block || typeof block !== 'object' || Array.isArray(block)) return sortedDeepValue(block);
  const out = {};
  for (const k of Object.keys(block).sort()) {
    if (k === 'cache_control' || k === 'cachePoint') continue;
    out[k] = sortedDeepValue(block[k]);
  }
  return out;
}

/* Normalize a tool or system segment before comparing it: each element is a
   block whose own keys may carry a marker.

   Bedrock states a breakpoint as its own `{cachePoint: ...}` element rather than
   a property, so stripping the key in place would leave an empty `{}` holding
   that slot.  Moving such a marker would then shift the placeholder and read as
   a content edit: identical prompts would be reported as a definite tool, system
   or history modification.  Where the breakpoints sit is `cachedScopes`' job;
   this comparison is only about the content, so marker-only elements are dropped
   outright.  A block with a marker *and* real content keeps its place. */
function normalizeCacheable(value) {
  if (Array.isArray(value)) return value.filter(block => !isMarkerOnlyBlock(block)).map(stripCacheMarkers);
  return stripCacheMarkers(value);
}

/* Normalize a message: its markers sit on the content blocks, not on the
   payload the blocks carry. */
function normalizeCacheableMessage(msg) {
  if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return sortedDeepValue(msg);
  const out = {};
  for (const k of Object.keys(msg).sort()) {
    if (k === 'cache_control' || k === 'cachePoint') continue;
    out[k] = k === 'content' ? normalizeCacheable(msg[k]) : sortedDeepValue(msg[k]);
  }
  return out;
}

/* Explain why a turn had to rebuild its prompt cache from scratch.
   Returns null when the cache behaved normally, so callers can skip the card.

   Causes are ordered by how directly they are observable, but only among causes
   that are still *candidates*.  A structural edit inside the cached region is
   visible in the captured payloads and actionable, so it outranks an idle gap —
   yet once the gap already exceeds the cache lifetime, the entry was gone before
   the edit could matter, and the trace cannot say which of the two caused the
   cold write.  So expiry is ruled out first, and when it cannot be, the card
   names no single cause.

   Expiry itself is only claimable against a predecessor confirmed by positive
   evidence.  An adjacency-only candidate from an interleaved conversation has
   its own cache chain, and its timestamp and TTL describe that chain, not this
   one. */
function diagnoseCacheInvalidation(curEntry, prevEntry, prevIsExact) {
  if (!curEntry) return null;
  if (!isColdCacheWrite(getUsage(curEntry))) return null;

  // No captured predecessor. That is only evidence of a first write when the
  // trace shows the conversation itself starting here: a capture that begins
  // mid-session, or that resumed an idle one, has an earlier cache it cannot see.
  if (!prevEntry) {
    return traceStartsConversation(curEntry)
      ? cacheDiagnosticNamed('cache_miss_initial')
      : cacheDiagnosticUnknown();
  }

  const curResolved = resolveEntryForDetail(curEntry);
  const prevResolved = resolveEntryForDetail(prevEntry);
  // Stub entries carry synthesized bodies (lazy/dashboard mode without raw
  // lines), so comparing them would invent differences that never existed.
  const structureAvailable = !curResolved?._isStub && !prevResolved?._isStub;
  const curBody = curResolved?.request?.body || {};
  const prevBody = prevResolved?.request?.body || {};

  const curScopes = cachedScopes(curBody);
  const prevScopes = cachedScopes(prevBody);

  const curTs = curEntry.timestamp ? new Date(curEntry.timestamp).getTime() : 0;
  const prevTs = prevEntry.timestamp ? new Date(prevEntry.timestamp).getTime() : 0;
  const ttlMs = cacheTtlMs(getUsage(prevEntry), prevBody);
  const expired = !!(curTs && prevTs && ttlMs > 0 && (curTs - prevTs) >= ttlMs);

  if (structureAvailable && prevIsExact && !expired
    && cachedRegionsAgree(prevScopes, curScopes)) {
    const diff = diffCachedRegion(prevBody, curBody, prevScopes, curScopes);
    /* One invariant decides whether a change may be named as the cause: if some
       checkpoint *ahead* of it was declared on both sides and its own segment did
       not change, that entry should have matched and produced cache reads.  Zero
       reads say it did not, so the trace contradicts blaming the later change and
       the honest answer is `unknown`.

       The segments are checked in prompt-hash order, so "ahead" means every
       segment before the changed one.  Messages are the same argument one step
       further in, at block granularity: a breakpoint at message A ahead of an
       edit at message B is an earlier checkpoint that should have hit. */
    const declaredOnBothSides = (scope) => (prevScopes.bps || []).some(bp => bp.scope === scope)
      && (curScopes.bps || []).some(bp => bp.scope === scope);
    const sharedMsgBpBefore = (limit) => {
      if (limit <= 0) return false;
      const covered = (scopes) => (scopes.bps || [])
        .filter(bp => bp.scope === 'messages')
        .map(bp => (bp.blockIndex < 0 ? bp.index - 1 : bp.index))
        .filter(idx => idx >= 0 && idx < limit);
      const prevIdxs = covered(prevScopes);
      return prevIdxs.length > 0 && covered(curScopes).some(idx => prevIdxs.includes(idx));
    };
    /* Segments in prompt-hash order.  `scope` is what an unchanged one would have
       cached; `insideHit` is the same test applied *within* a segment, which only
       messages need -- tools and system are compared whole, so a change there has
       nothing of its own ahead of it. */
    const SEGMENTS = [
      { changed: diff.toolsChanged, reason: 'cache_miss_tools', scope: 'tools' },
      { changed: diff.systemChanged, reason: 'cache_miss_system', scope: 'system' },
      {
        // A message edit is only nameable when the messages were cached at all.
        changed: diff.historyChanged && curScopes.msgs > 0,
        reason: 'cache_miss_history',
        scope: 'messages',
        insideHit: () => sharedMsgBpBefore(diff.historyFrom),
      },
    ];
    for (let i = 0; i < SEGMENTS.length; i++) {
      const seg = SEGMENTS[i];
      if (!seg.changed) continue;
      // The earliest changed segment is the one that broke the chain, so it is the
      // only one worth naming -- unless a checkpoint ahead of it should have hit.
      const aheadShouldHaveHit = SEGMENTS.slice(0, i)
        .some(earlier => !earlier.changed && declaredOnBothSides(earlier.scope));
      return aheadShouldHaveHit || (seg.insideHit ? seg.insideHit() : false)
        ? cacheDiagnosticUnknown()
        : cacheDiagnosticNamed(seg.reason);
    }
  }

  if (expired && prevIsExact) {
    // The gap exceeds the lifetime the predecessor declared, and that
    // predecessor is confirmed to own this cache chain.
    const minutes = Math.round(ttlMs / 60000);
    return {
      reasonKey: 'cache_miss_ttl',
      reasonText: t('cache_miss_ttl').replace('{minutes}', String(minutes)),
      lowConfidence: !structureAvailable,
    };
  }

  /* Nothing above could speak: an unconfirmed predecessor owns some other cache
     chain, so neither its body nor its TTL is evidence about this write.  But
     the absence of evidence in it is not the absence of evidence: the trace can
     still show this conversation opening here with nothing of its own behind it,
     and that is the definition of an initial write.

     `findCachePredecessor` keeps a headerless cache-bearing neighbor as a
     fallback precisely because it cannot rule it out, so merely having one used
     to turn a provable `cache_miss_initial` into `unknown` -- the diagnosis then
     depended on whether an unrelated conversation happened to be interleaved in
     the capture.

     Both conditions have to come from the trace rather than from the caller's
     `prevIsExact` flag, since a candidate that shares the session is the same
     chain no matter what was passed in, and the fallback is only irrelevant when
     no same-session turn cached anything ahead of this one. */
  if (!prevIsExact && cacheSessionKey(curEntry)
    && cacheSessionKey(prevEntry) !== cacheSessionKey(curEntry)
    && !earlierSessionTurnCached(curEntry)
    && traceStartsConversation(curEntry)) {
    return cacheDiagnosticNamed('cache_miss_initial');
  }

  return cacheDiagnosticUnknown();
}

function cacheDiagnosticMarkup(diag) {
  const cls = diag.lowConfidence ? ' low-confidence' : '';
  return `<div class="cache-diag-card${cls}" data-reason="${esc(diag.reasonKey)}">`
    + `<span class="cache-diag-icon">&#128161;</span>`
    + `<span class="cache-diag-title">${t('cache_diag_title')}</span> `
    + `<span class="cache-diag-desc">${esc(diag.reasonText)}</span></div>`;
}

/* Render the cache diagnostic card for an entry, or '' when the cache behaved
   normally.  Shared by the message and trace detail views.

   In remote dashboard mode the predecessor is still a stub, and diagnosing
   against a synthesized body would report `unknown` for a miss the real payload
   explains.  Detail rendering is synchronous, so the card is emitted with a
   placeholder and `upgradeCacheDiagnostic` replaces it once the predecessor
   arrives. */
function renderCacheDiagnostic(entry) {
  if (typeof diagnoseCacheInvalidation !== 'function') return '';
  if (!entry || !isColdCacheWrite(getUsage(entry))) return '';
  const prev = findCachePredecessor(entry);
  if (prev.entry && typeof shouldFetchRemoteEntry === 'function' && shouldFetchRemoteEntry(prev.entry)) {
    return `<div class="cache-diag-card low-confidence" data-reason="cache_miss_pending"`
      + ` data-pending-idx="${esc(String(prev.idx))}" data-pending-exact="${prev.exact ? '1' : '0'}">`
      + `<span class="cache-diag-icon">&#128161;</span>`
      + `<span class="cache-diag-title">${t('cache_diag_title')}</span> `
      + `<span class="cache-diag-desc">${esc(t('cache_miss_pending'))}</span></div>`;
  }
  const diag = diagnoseCacheInvalidation(entry, prev.entry, prev.exact);
  if (!diag) return '';
  return cacheDiagnosticMarkup(diag);
}

function replacePendingCacheDiagnostic(host, diag) {
  if (!host?.isConnected) return;
  if (!diag) {
    host.remove();
    return;
  }
  host.outerHTML = cacheDiagnosticMarkup(diag);
}

/* The two verdicts the diagnosis returns.  `unknown` is the honest answer when
   the trace cannot single out a cause, and is marked low-confidence so the card
   reads as a description of the evidence rather than a finding; a named reason
   has been established from both request bodies and is not hedged. */
function cacheDiagnosticUnknown() {
  return { reasonKey: 'cache_miss_unknown', reasonText: t('cache_miss_unknown'), lowConfidence: true };
}

function cacheDiagnosticNamed(reasonKey) {
  return { reasonKey, reasonText: t(reasonKey), lowConfidence: false };
}

/* Replace a pending card once the predecessor payload has been fetched.  Called
   after the detail pane renders; a no-op when nothing is pending.

   Dashboard stubs omit Claude session headers, so the nearest unlabeled
   fallback may belong to another conversation.  If the fetched payload fails
   the exactness check, this continues the existing predecessor walk — it does
   not start a second search — until an exact candidate is fetched or the
   history is exhausted.  A records-API reject or timeout replaces the
   forever-pending card with the unknown state; inventing a cause from a
   missing payload would be worse. */
async function upgradeCacheDiagnostic(entry, root) {
  const host = (root || document).querySelector('.cache-diag-card[data-reason="cache_miss_pending"]');
  if (!host || !entry) return;
  const rejected = new Set();
  let idx = Number(host.dataset.pendingIdx);
  let pendingExact = host.dataset.pendingExact === '1';

  while (true) {
    const prevStub = typeof entries !== 'undefined' && Number.isInteger(idx) ? entries[idx] : null;
    if (!prevStub) {
      replacePendingCacheDiagnostic(host, cacheDiagnosticUnknown());
      return;
    }
    let prevEntry;
    try {
      prevEntry = await resolveEntryForDetailAsync(prevStub);
    } catch (err) {
      console.error('Failed to load cache predecessor:', err);
      replacePendingCacheDiagnostic(host, cacheDiagnosticUnknown());
      return;
    }
    // The pane may have moved on to another entry while the fetch was in flight.
    if (!host.isConnected) return;
    /* Re-ask whether this really is the predecessor, now that there is a payload to
       ask against.  `data-pending-exact` was decided from the stub, and a Claude
       stub carries no session header and no message bodies, so the search had
       nothing to confirm and recorded `exact: false` for an otherwise-correct
       candidate.  Forwarding that verdict would make the fetch pointless: every
       structural and TTL diagnosis is gated on it, so a multi-message Claude turn
       would stay `unknown` in dashboard mode however complete the payload is.

       The stub's `1` is kept as a floor rather than recomputed away: it was reached
       on evidence the stub did carry, and the entry passed to this function may
       itself still be a stub whose synthesized body cannot reproduce it. */
    const exact = pendingExact
      || cachePredecessorIsExact(resolveEntryForDetail(entry) || entry, prevEntry);
    if (exact) {
      replacePendingCacheDiagnostic(host, diagnoseCacheInvalidation(entry, prevEntry, true));
      return;
    }

    rejected.add(idx);
    const next = findCachePredecessor(entry, rejected);
    if (!next.entry || rejected.has(next.idx)) {
      replacePendingCacheDiagnostic(host, diagnoseCacheInvalidation(entry, null, false));
      return;
    }
    if (typeof shouldFetchRemoteEntry !== 'function' || !shouldFetchRemoteEntry(next.entry)) {
      replacePendingCacheDiagnostic(
        host,
        diagnoseCacheInvalidation(entry, resolveEntryForDetail(next.entry) || next.entry, next.exact),
      );
      return;
    }
    idx = next.idx;
    pendingExact = next.exact;
  }
}
