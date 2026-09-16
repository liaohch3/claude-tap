
/* ─── Path & filter ─── */
function getPath(e) { return (e.request?.path || '/unknown').replace(/\?.*$/, ''); }

/*
 * Collapse high-cardinality synthetic paths into one filter chip.
 * Cursor transcript steps use unique /cursor/transcript/{id}/turn/N/step/M paths;
 * showing each as a primary chip floods the header and breaks layout.
 */
function filterPathKey(p) {
  if (typeof p === 'string' && p.startsWith('/cursor/transcript/')) return '/cursor/transcript/';
  return p;
}

function renderApp(preserveDetail) {
  $('#drop-zone').style.display = 'none';
  $('#sidebar-wrap').style.display = 'flex';
  $('#search-bar').style.display = '';
  $('#sidebar-sort').style.display = '';
  $('#position-indicator').style.display = '';
  $('#sidebar').style.display = '';
  $('#detail').style.display = '';
  $('#stats').style.display = '';
  $('#path-filter').style.display = '';
  const pathCounts = {};
  entries.filter(isNavigableTraceEntry).forEach(e => {
    const p = filterPathKey(getPath(e));
    pathCounts[p] = (pathCounts[p] || 0) + 1;
  });
  const paths = Object.keys(pathCounts).sort();
  if (activePaths.size === 0) {
    /* If the trace has main conversation paths, hide auxiliary setup calls by default. */
    const primaryPaths = paths.filter(isPathPrimary);
    if (primaryPaths.length > 0) primaryPaths.forEach(p => activePaths.add(p));
    else paths.forEach(p => activePaths.add(p));
  } else if ([...activePaths].some(p => p.startsWith('/cursor/transcript/') && p !== '/cursor/transcript/')) {
    /* Migrate older per-step chip selections into the collapsed key. */
    let collapsed = false;
    for (const p of [...activePaths]) {
      if (p.startsWith('/cursor/transcript/') && p !== '/cursor/transcript/') {
        activePaths.delete(p);
        collapsed = true;
      }
    }
    if (collapsed) activePaths.add('/cursor/transcript/');
  }
  renderPathFilter(paths, pathCounts);
  renderTracePathBar();
  applyFilter(preserveDetail);
}

function renderTracePathBar() {
  const bar = $('#trace-path-bar');
  if (!TRACE_JSONL_PATH && !TRACE_HTML_PATH) return;
  const copyIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
  let html = '';
  if (TRACE_JSONL_PATH) {
    html += `<span class="tp-label">JSONL</span><span class="tp-val" title="${esc(TRACE_JSONL_PATH)}">${esc(TRACE_JSONL_PATH)}</span><button class="tp-copy" title="Copy path" data-copy-path="${esc(TRACE_JSONL_PATH)}">${copyIcon}</button>`;
  }
  if (TRACE_JSONL_PATH && TRACE_HTML_PATH) html += '<span class="tp-sep"></span>';
  if (TRACE_HTML_PATH) {
    html += `<span class="tp-label">HTML</span><span class="tp-val" title="${esc(TRACE_HTML_PATH)}">${esc(TRACE_HTML_PATH)}</span><button class="tp-copy" title="Copy path" data-copy-path="${esc(TRACE_HTML_PATH)}">${copyIcon}</button>`;
  }
  bar.innerHTML = html;
  bar.querySelectorAll('.tp-copy').forEach(btn => {
    btn.addEventListener('click', () => copyPath(btn.dataset.copyPath || '', btn));
  });
  bar.style.display = 'flex';
}
function copyPath(text, btn) {
  copyToClipboard(text, btn, '✓');
}

function getResponseStatus(e) {
  return Number(e?.response?.status || 0);
}

function getResponseErrorMessage(e) {
  const message = e?.response?.body?.error?.message;
  if (typeof message === 'string' && message.trim()) return message.trim();
  return 'Unknown error';
}

function onCopySuccess(btn, copiedLabel = null) {
  if (!btn) return;
  const orig = btn.innerHTML;
  btn.textContent = copiedLabel || t('copied');
  setTimeout(() => { btn.innerHTML = orig; }, 1500);
}

function fallbackCopyText(text) {
  return new Promise((resolve, reject) => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-9999px';
      ta.style.left = '-9999px';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) resolve();
      else reject(new Error('execCommand(copy) failed'));
    } catch (err) {
      reject(err);
    }
  });
}

function writeClipboardText(text) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    return navigator.clipboard.writeText(text).catch(() => fallbackCopyText(text));
  }
  return fallbackCopyText(text);
}

function copyToClipboard(text, btn, copiedLabel = null) {
  writeClipboardText(text).then(() => {
    onCopySuccess(btn, copiedLabel);
  }).catch(err => {
    console.error('Clipboard copy failed:', err);
    if (!btn) return;
    const orig = btn.innerHTML;
    btn.textContent = '!';
    setTimeout(() => { btn.innerHTML = orig; }, 1500);
  });
}

let pathFilterExpanded = false;

/*
 * Path priority tiers:
 *   primary   – core AI conversation endpoints, always visible
 *   secondary – useful auxiliary APIs (MCP, models, token counting), collapsed behind "+N more"
 *   noise     – plugin manifests, bot APIs, asset downloads, version checks — hidden by default
 */
const PRIMARY_PATH_PREFIXES = ['/cursor/transcript/', '/v1/messages', '/v1/responses', '/backend-api/codex/responses', '/v1/chat/completions', '/v1/completions', '/v1beta/models', '/v1alpha/models', '/v1internal:generateContent', '/v1internal:streamGenerateContent'];
const SECONDARY_PATH_PREFIXES = ['/v1/mcp', '/v1/models', '/v1/embeddings', '/v1/files', '/responses', '/models', '/chat/completions', '/completions', '/files', '/search', '/fetch', '/usages', '/feedback'];
function isBedrockInvokePath(p) {
  return p.startsWith('/model/') && (p.endsWith('/invoke') || p.endsWith('/invoke-with-response-stream'));
}
function pathTier(p) {
  if (isBedrockInvokePath(p)) return 0;
  if (PRIMARY_PATH_PREFIXES.some(pfx => p.startsWith(pfx))) return 0;
  if (SECONDARY_PATH_PREFIXES.some(pfx => p.startsWith(pfx))) return 1;
  return 2;
}
function isPathPrimary(p) { return pathTier(p) === 0; }

function renderPathFilter(paths, pathCounts) {
  const c = $('#path-filter'); c.innerHTML = '';
  const buckets = [[], [], []]; /* primary, secondary, noise */
  paths.forEach(p => buckets[pathTier(p)].push(p));
  buckets.forEach(b => b.sort((a, bb) => pathCounts[bb] - pathCounts[a]));

  /* Always show primary */
  buckets[0].forEach(p => c.appendChild(makeFilterChip(p, pathCounts[p])));

  const hiddenPaths = [...buckets[1], ...buckets[2]];
  if (hiddenPaths.length > 0) {
    if (pathFilterExpanded) {
      buckets[1].forEach(p => c.appendChild(makeFilterChip(p, pathCounts[p])));
      buckets[2].forEach(p => c.appendChild(makeFilterChip(p, pathCounts[p])));
    }
    const toggle = document.createElement('button');
    toggle.className = 'filter-chip-toggle';
    toggle.textContent = pathFilterExpanded ? t('filter_less') : `+${hiddenPaths.length} ${t('filter_more')}`;
    toggle.onclick = () => { pathFilterExpanded = !pathFilterExpanded; renderPathFilter(paths, pathCounts); };
    c.appendChild(toggle);
  }
}

function makeFilterChip(p, count) {
  const chip = document.createElement('button');
  chip.className = 'filter-chip' + (activePaths.has(p) ? ' active' : '');
  /* Truncate long paths: show last 40 chars with ellipsis */
  const label = p.length > 40 ? '\u2026' + p.slice(-39) : p;
  chip.innerHTML = `<span title="${esc(p)}">${esc(label)}</span><span class="chip-count">${count}</span>`;
  chip.title = p;
  chip.onclick = () => {
    if (activePaths.has(p)) activePaths.delete(p); else activePaths.add(p);
    if (activePaths.size === 0) activePaths.add(p);
    chip.classList.toggle('active');
    applyFilter();
  };
  return chip;
}

function turnSortSegments(value) {
  if (value === undefined || value === null || value === '') return [0];
  return String(value).split('.').map(part => {
    const num = Number(part);
    return Number.isFinite(num) ? num : 0;
  });
}

function compareTurns(a, b) {
  const aa = turnSortSegments(a);
  const bb = turnSortSegments(b);
  const len = Math.max(aa.length, bb.length);
  for (let i = 0; i < len; i++) {
    const diff = (aa[i] || 0) - (bb[i] || 0);
    if (diff) return diff;
  }
  return String(a ?? '').localeCompare(String(b ?? ''));
}

/* ─── Cost display ───
   Cost is computed in Python (claude_tap/pricing.py) from a vendored LiteLLM
   price table, then embedded either on each metadata record (lazy mode) or in
   EMBEDDED_COST_INDEX keyed by entry request id. The viewer only looks values
   up, formats them and sums them; it holds no price table and does no rate
   arithmetic. Traces opened by drag-and-drop never pass through Python, so no
   cost is available for them and the cost stats stay hidden. */

function pricingMeta() {
  return typeof EMBEDDED_PRICING_META !== 'undefined' && EMBEDDED_PRICING_META ? EMBEDDED_PRICING_META : null;
}

/* The upstream price file changes several times a day, so the fetch date alone
   does not identify the table a figure came from. Show the short commit next to
   it when the snapshot carries one. */
function pricingStamp(meta) {
  const parts = [];
  if (meta?.as_of) parts.push(meta.as_of);
  if (meta?.upstream_commit) parts.push(String(meta.upstream_commit).slice(0, 12));
  return parts.length ? ` (${parts.join(' @ ')})` : '';
}

function entryCostRecord(entry) {
  if (!entry || typeof entry !== 'object') return null;
  if (entry.subscription === true) return { subscription: true };
  if (typeof entry.cost === 'number') {
    return { cost: entry.cost, uncached_cost: entry.uncached_cost, saved: entry.saved };
  }
  /* Live mode and the records API serve raw records with no generated index, so
     Python attaches the costs to the record; a WebSocket record split into
     several entries carries one keyed set per derived entry. */
  const carried = entry._cost_index;
  if (carried && typeof carried === 'object') {
    const own = carried[entry.request_id];
    if (own && (typeof own.cost === 'number' || own.subscription === true)) return own;
    const keys = Object.keys(carried);
    if (keys.length === 1 && (typeof carried[keys[0]].cost === 'number' || carried[keys[0]].subscription === true)) {
      return carried[keys[0]];
    }
  }
  const index = typeof EMBEDDED_COST_INDEX !== 'undefined' ? EMBEDDED_COST_INDEX : null;
  if (!index) return null;
  const priced = index[entry.request_id];
  if (!priced) return null;
  if (priced.subscription === true) return priced;
  return typeof priced.cost === 'number' ? priced : null;
}

/* A subscription or quota turn -- a ChatGPT plan, or a Google Code Assist
   account quota -- is not a priced turn: those tokens were covered by the plan,
   so a dollar figure for them was never billed. It is also not an unknown-price
   turn, so it is reported separately rather than inflating the "no known price"
   count. */
function isSubscriptionEntry(entry) {
  return entryCostRecord(entry)?.subscription === true;
}

function entryCost(entry) {
  const record = entryCostRecord(entry);
  if (!record || record.subscription === true) return null;
  return record;
}

function formatCostUsd(amount) {
  if (typeof amount !== 'number' || !Number.isFinite(amount)) return '';
  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-$' : '$';
  if (abs === 0) return '$0.00';
  /* toFixed(4) rounds values below $0.00005 to $0.0000, which reports a paid
     turn as free. Keep enough digits that a nonzero charge stays visible. */
  if (abs < 0.0001) return sign + abs.toFixed(6);
  if (abs < 0.01) return sign + abs.toFixed(4);
  return sign + abs.toFixed(2);
}

function applyFilter(preserveDetail) {
  filtered = entries.filter(e => isNavigableTraceEntry(e) && activePaths.has(filterPathKey(getPath(e))));
  if (searchQuery) filtered = filtered.filter(e => matchSearch(e, searchQuery));
  if (activeTools) {
    filtered = filtered.filter(e => {
      const ro = getResponseOutput(e);
      const rc = ro?.content;
      if (!Array.isArray(rc)) return false;
      return rc.some(b => b.type === 'tool_use' && activeTools.has(b.name));
    });
  }
  filtered.sort((a, b) => compareTurns(captureTurnValue(a), captureTurnValue(b)));
  let totalTokens = 0, totalDuration = 0;
  let sumInput = 0, sumOutput = 0, sumCacheRead = 0, sumCacheCreate = 0;
  let sumCacheDenominator = 0;
  let sumCost = 0, sumSaved = 0, pricedTurns = 0, unpricedTurns = 0, subscriptionTurns = 0;
  filtered.forEach(e => {
    totalDuration += e.duration_ms || 0;
    const u = getUsage(e);
    const costRecord = entryCostRecord(e);
    const priced = costRecord && costRecord.subscription !== true ? costRecord : null;
    if (priced) {
      sumCost += priced.cost;
      /* Per-entry savings are signed: a 5-minute cache write costs 1.25x
         uncached input, so a write-only turn is genuinely more expensive than
         an uncached one. Only the total is clamped for display. */
      if (typeof priced.saved === 'number') sumSaved += priced.saved;
      pricedTurns++;
    } else if (costRecord?.subscription === true) {
      subscriptionTurns++;
    } else if (u) {
      unpricedTurns++;
    }
    if (u) {
      const inputTokens = u.input_tokens || 0;
      const cacheRead = u.cache_read_input_tokens || 0;
      const cacheCreate = u.cache_creation_input_tokens || 0;
      totalTokens += inputTokens + (u.output_tokens || 0);
      sumInput += inputTokens;
      sumOutput += u.output_tokens || 0;
      sumCacheRead += cacheRead;
      sumCacheCreate += cacheCreate;
      if (inputTokens || cacheRead || cacheCreate) {
        if (u._cache_read_in_input) {
          sumCacheDenominator += inputTokens;
        } else {
          sumCacheDenominator += inputTokens + cacheRead + cacheCreate;
        }
      }
    }
  });
  $('#stat-turns').textContent = filtered.length;
  $('#stat-tokens').textContent = totalTokens.toLocaleString();
  $('#stat-duration').textContent = fmtDuration(totalDuration);
  // Token breakdown in header
  if (totalTokens > 0) {
    $('#stat-input').textContent = sumInput.toLocaleString();
    $('#stat-input-group').style.display = 'flex';
    $('#stat-output').textContent = sumOutput.toLocaleString();
    $('#stat-output-group').style.display = 'flex';
    if (sumCacheRead) { $('#stat-cache-read').textContent = sumCacheRead.toLocaleString(); $('#stat-cache-read-group').style.display = 'flex'; }
    else { $('#stat-cache-read-group').style.display = 'none'; }
    if (sumCacheCreate) { $('#stat-cache-write').textContent = sumCacheCreate.toLocaleString(); $('#stat-cache-write-group').style.display = 'flex'; }
    else { $('#stat-cache-write-group').style.display = 'none'; }
    if (sumCacheRead && sumCacheDenominator > 0) {
      const hitRate = Math.round(sumCacheRead / sumCacheDenominator * 100);
      $('#stat-cache-hit-rate').textContent = hitRate + '%';
      $('#stat-cache-hit-rate-group').style.display = 'flex';
    } else {
      $('#stat-cache-hit-rate-group').style.display = 'none';
    }
  } else {
    ['stat-input-group','stat-output-group','stat-cache-read-group','stat-cache-write-group','stat-cache-hit-rate-group'].forEach(id => $('#'+id).style.display = 'none');
  }
  renderCostStats(sumCost, sumSaved, pricedTurns, unpricedTurns, subscriptionTurns);
  renderToolFilter();
  renderSidebar(preserveDetail);
  updatePositionIndicator();
}

function renderCostStats(sumCost, sumSaved, pricedTurns, unpricedTurns, subscriptionTurns = 0) {
  const costGroup = $('#stat-cost-group');
  const savedGroup = $('#stat-saved-group');
  if (!costGroup || !savedGroup) return;
  if (!pricedTurns) {
    costGroup.style.display = 'none';
    savedGroup.style.display = 'none';
    return;
  }
  const meta = pricingMeta();
  /* Say outright that some turns are missing from the total rather than
     leaving a partial figure looking complete. The count goes in the visible
     label, not only a tooltip. Subscription turns are named separately: those
     tokens were covered by a monthly plan, which is a different reason for
     absence than a model the table cannot price. */
  const partial = unpricedTurns > 0 || subscriptionTurns > 0;
  const sourceNote = meta?.source ? `${meta.source}${pricingStamp(meta)}` : '';
  const notes = [];
  if (unpricedTurns > 0) notes.push(formatText('cost_partial', { n: unpricedTurns }));
  if (subscriptionTurns > 0) notes.push(formatText('cost_subscription', { n: subscriptionTurns }));

  $('#stat-cost').textContent = formatCostUsd(sumCost) + (partial ? '+' : '');
  costGroup.title = [t('cost_tooltip'), sourceNote, ...notes].filter(Boolean).join(' · ');
  costGroup.style.display = 'flex';

  /* Total savings are clamped: a net-negative total is an artifact of a window
     that captured cache writes but not the reads they paid for. */
  $('#stat-saved').textContent = formatCostUsd(Math.max(0, sumSaved)) + (partial ? '+' : '');
  savedGroup.title = [t('saved_tooltip'), sourceNote, ...notes].filter(Boolean).join(' · ');
  savedGroup.style.display = 'flex';
}

function renderToolFilter() {
  const tf = $('#tool-filter');
  // Collect all tool names from all entries (not just filtered)
  const toolSet = new Set();
  entries.forEach(e => {
    if (e._isStub) {
      // Use stub's response content (already has tool_use blocks from metadata)
      const rc = e.response?.body?.content;
      if (Array.isArray(rc)) rc.forEach(b => { if (b.type === 'tool_use' && b.name) toolSet.add(b.name); });
    } else {
      const ro = getResponseOutput(e);
      const rc = ro?.content;
      if (Array.isArray(rc)) rc.forEach(b => { if (b.type === 'tool_use' && b.name) toolSet.add(b.name); });
    }
  });
  if (toolSet.size === 0) { tf.style.display = 'none'; return; }
  tf.style.display = '';
  const tools = [...toolSet].sort();
  tf.innerHTML = '<div style="margin-bottom:3px;font-weight:600;color:var(--text-secondary)">Tools</div>' +
    tools.map(name => {
      const active = activeTools ? activeTools.has(name) : false;
      return `<button class="filter-chip${active ? ' active' : ''}" style="font-size:10px;padding:2px 6px;margin:1px" onclick="toggleToolFilter('${name.replace(/'/g, "\\'")}')">${name}</button>`;
    }).join('') +
    (activeTools ? `<button class="filter-chip" style="font-size:10px;padding:2px 6px;margin:1px;color:var(--red)" onclick="clearToolFilter()">✕ Clear</button>` : '');
}
function toggleToolFilter(name) {
  if (!activeTools) activeTools = new Set();
  if (activeTools.has(name)) { activeTools.delete(name); if (activeTools.size === 0) activeTools = null; }
  else activeTools.add(name);
  applyFilter();
}
function clearToolFilter() { activeTools = null; applyFilter(); }

function setSidebarOrderMode(mode) {
  if (!SIDEBAR_ORDER_MODES.includes(mode)) return;
  if (sidebarOrderMode === mode) return;
  sidebarOrderMode = mode;
  safeLocalStorageSet('claude-tap-sidebar-order', mode);
  updateSidebarSortControls();
  renderSidebar(true);
  updatePositionIndicator();
  if (virtualMode && activeIdx >= 0) {
    vsScrollToIdx(activeIdx);
  } else {
    const activeItem = document.querySelector('.sidebar-item.active');
    if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });
  }
}

function updateSidebarSortControls() {
  document.querySelectorAll('.sidebar-sort-btn').forEach(btn => {
    const mode = btn.dataset.sortMode;
    btn.textContent = mode === 'turn' ? t('sort_turn') : mode === 'session' ? t('sort_session') : t('sort_model');
    const active = mode === sidebarOrderMode;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

/* ─── Search ─── */
function onSearch(value) {
  searchQuery = value.toLowerCase().trim();
  $('#search-clear').style.display = searchQuery ? '' : 'none';
  applyFilter();
}
function clearSearch() {
  searchQuery = '';
  $('#search-input').value = '';
  $('#search-clear').style.display = 'none';
  applyFilter();
}
function toolMatchesSearch(td, q) {
  if (toolDisplayName(td).toLowerCase().includes(q)) return true;
  if (toolDescription(td).toLowerCase().includes(q)) return true;
  if (!Array.isArray(td?.tools)) return false;
  return td.tools.some(tool => toolMatchesSearch(tool, q));
}
function matchSearch(e, q) {
  if (e._isStub) {
    // In lazy mode: search metadata fields only (fast, no parsing)
    const model = e.request?.body?.model || '';
    if (model.toLowerCase().includes(q)) return true;
    const path = e.request?.path || '';
    if (path.toLowerCase().includes(q)) return true;
    const sys = e.request?.body?.system || '';
    if (sys.toLowerCase().includes(q)) return true;
    const turn = String(displayTurnLabel(e));
    if (turn.includes(q)) return true;
    const stubRequestTools = getRequestTools(e.request?.body);
    const tools = stubRequestTools.length ? stubRequestTools : getRequestTools(getResponsePayload(e));
    for (const td of tools) {
      if (toolMatchesSearch(td, q)) return true;
    }
    const rc = e.response?.body?.content;
    if (Array.isArray(rc)) {
      for (const block of rc) {
        if ((block.name || '').toLowerCase().includes(q)) return true;
      }
    }
    return false;
  }
  const body = e.request?.body;
  if ((body?.model || '').toLowerCase().includes(q)) return true;
  const sys = extractSystem(body) || '';
  if (sys.toLowerCase().includes(q)) return true;
  const msgs = getMessages(body);
  for (const m of msgs) {
    const mc = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
    if (mc.toLowerCase().includes(q)) return true;
  }
  const requestTools = getRequestTools(body);
  const tools = requestTools.length ? requestTools : getRequestTools(getResponsePayload(e));
  for (const td of tools) {
    if (toolMatchesSearch(td, q)) return true;
  }
  const ro = getResponseOutput(e);
  const rc = ro?.content;
  if (Array.isArray(rc)) {
    for (const block of rc) {
      if ((block.text || block.name || '').toLowerCase().includes(q)) return true;
    }
  }
  return false;
}

/* ─── Global search (Cmd/Ctrl+F) ─── */
function initGlobalSearch() {
  const input = $('#global-search-input');
  $('#global-search-prev').onclick = () => navigateGlobalSearch(-1);
  $('#global-search-next').onclick = () => navigateGlobalSearch(1);
  $('#global-search-close').onclick = () => closeGlobalSearch();
  input.addEventListener('input', () => {
    globalSearchState.query = input.value.trim();
    scheduleGlobalSearchRecalc();
  });
}

function openGlobalSearch() {
  normalizeFiltersForGlobalSearch();
  globalSearchState.open = true;
  $('#global-search-overlay').classList.add('open');
  const input = $('#global-search-input');
  input.value = globalSearchState.query;
  input.focus();
  input.select();
  recalcGlobalSearchMatches();
}

function closeGlobalSearch() {
  globalSearchState.open = false;
  globalSearchState.query = '';
  globalSearchState.queries = [];
  globalSearchState.matchCounts = [];
  globalSearchState.totalMatches = 0;
  globalSearchState.currentMatch = -1;
  if (globalSearchState.recalcTimer) {
    cancelAnimationFrame(globalSearchState.recalcTimer);
    globalSearchState.recalcTimer = 0;
  }
  $('#global-search-input').value = '';
  $('#global-search-overlay').classList.remove('open');
  clearGlobalSearchHighlights($('#detail'));
  updateGlobalSearchCount();
}

function normalizeFiltersForGlobalSearch() {
  // Global search must be able to move across all entries.
  let changed = false;
  const allPaths = new Set(entries.filter(isNavigableTraceEntry).map(e => filterPathKey(getPath(e))));
  if (activePaths.size !== allPaths.size || [...allPaths].some(p => !activePaths.has(p))) {
    activePaths = allPaths;
    changed = true;
  }
  if (activeTools) { activeTools = null; changed = true; }
  if (searchQuery) {
    searchQuery = '';
    if ($('#search-input')) $('#search-input').value = '';
    if ($('#search-clear')) $('#search-clear').style.display = 'none';
    changed = true;
  }
  if (changed) applyFilter(true);
}

function uniqueSearchQueries(values) {
  const out = [];
  const seen = new Set();
  values.forEach(value => {
    const q = String(value || '').toLowerCase();
    if (!q || seen.has(q)) return;
    seen.add(q);
    out.push(q);
  });
  return out;
}

function buildGlobalSearchQueries(query) {
  const base = String(query || '').toLowerCase().trim();
  if (!base) return [];
  const variants = [base];
  const keyMatch = base.match(/^([a-z0-9_$.-]+):(\s*.*)$/i);
  if (keyMatch) {
    const key = keyMatch[1];
    const tail = keyMatch[2] || '';
    const compactTail = tail.trimStart();
    variants.push(`"${key}":${compactTail}`);
    variants.push(`"${key}":${tail}`);
    variants.push(`"${key}": ${compactTail}`);
    variants.push(`${key}":${compactTail}`);
    variants.push(`${key}":${tail}`);
    variants.push(`${key}": ${compactTail}`);
  }
  variants.slice().forEach(value => {
    if (value.includes('"')) variants.push(value.replaceAll('"', '\\"'));
  });
  return uniqueSearchQueries(variants);
}

function buildGlobalHighlightQueries(query) {
  const base = String(query || '').toLowerCase().trim();
  if (!base) return [];
  const variants = buildGlobalSearchQueries(base);
  const keyMatch = base.match(/^([a-z0-9_$.-]+):(\s*.*)$/i);
  if (keyMatch) {
    const key = keyMatch[1];
    variants.push(`"${key}"`, key);
  }
  return uniqueSearchQueries(variants);
}

function getEntrySearchText(entry) {
  const cacheKey = entryStableKey(entry);
  if (!cacheKey) return '';
  if (globalSearchState.textCache.has(cacheKey)) return globalSearchState.textCache.get(cacheKey);
  if (entry._isStub) {
    const lines = getRawLines();
    const raw = lines[entry._rawIdx] || '';
    const text = raw.toLowerCase();
    globalSearchState.textCache.set(cacheKey, text);
    return text;
  }
  const resolved = entry;
  const body = resolved.request?.body;
  const parts = [];
  parts.push(body?.model || '');
  parts.push(extractSystem(body) || '');
  getMessages(body).forEach(m => parts.push(msgToText(m)));
  const requestTools = getRequestTools(body);
  const tools = requestTools.length ? requestTools : getRequestTools(getResponsePayload(resolved));
  tools.forEach(td => parts.push(JSON.stringify(td)));
  const output = getResponseOutput(resolved);
  if (output?.content) parts.push(msgToText({ role: 'assistant', content: output.content }));
  getResponseEvents(resolved).forEach(ev => parts.push(JSON.stringify(ev)));
  parts.push(JSON.stringify(resolved, null, 2));
  const text = parts.join('\n').toLowerCase();
  globalSearchState.textCache.set(cacheKey, text);
  return text;
}

function countOneQueryInText(text, query) {
  let count = 0;
  let from = 0;
  while (query) {
    const idx = text.indexOf(query, from);
    if (idx === -1) return count;
    count += 1;
    from = idx + query.length;
  }
  return count;
}

function countMatchesInText(text, queries) {
  if (!text || !queries.length) return 0;
  return Math.max(...queries.map(query => countOneQueryInText(text, query)));
}

function scheduleGlobalSearchRecalc() {
  if (globalSearchState.recalcTimer) cancelAnimationFrame(globalSearchState.recalcTimer);
  globalSearchState.recalcTimer = requestAnimationFrame(() => {
    globalSearchState.recalcTimer = 0;
    recalcGlobalSearchMatches();
  });
}

function flushGlobalSearchRecalc() {
  if (!globalSearchState.recalcTimer) return;
  cancelAnimationFrame(globalSearchState.recalcTimer);
  globalSearchState.recalcTimer = 0;
  recalcGlobalSearchMatches();
}

function recalcGlobalSearchMatches() {
  clearGlobalSearchHighlights($('#detail'));
  const queries = buildGlobalSearchQueries(globalSearchState.query);
  globalSearchState.queries = queries;
  if (!queries.length) {
    globalSearchState.matchCounts = [];
    globalSearchState.totalMatches = 0;
    globalSearchState.currentMatch = -1;
    updateGlobalSearchCount();
    return;
  }
  const counts = [];
  let total = 0;
  entries.forEach(entry => {
    if (!isNavigableTraceEntry(entry)) return;
    const c = countMatchesInText(getEntrySearchText(entry), queries);
    if (c > 0) counts.push({ entryKey: entryStableKey(entry), requestId: entry.request_id, count: c });
    total += c;
  });
  globalSearchState.matchCounts = counts;
  globalSearchState.totalMatches = total;
  globalSearchState.currentMatch = total > 0 ? 0 : -1;
  updateGlobalSearchCount();
  revealCurrentSearchMatch();
}

function updateGlobalSearchCount() {
  const total = globalSearchState.totalMatches;
  const current = total > 0 ? (globalSearchState.currentMatch + 1) : 0;
  const label = total > 0 ? `${current} of ${total} matches` : '0 of 0';
  $('#global-search-count').textContent = label;
}

function getTargetForGlobalMatch(globalIndex) {
  let seen = 0;
  for (const row of globalSearchState.matchCounts) {
    if (globalIndex < seen + row.count) {
      return { entryKey: row.entryKey, requestId: row.requestId, localIndex: globalIndex - seen };
    }
    seen += row.count;
  }
  return null;
}

function findFilteredIdxByEntryKey(entryKey, requestId) {
  let idx = filtered.findIndex(entry => entryStableKey(entry) === entryKey);
  if (idx >= 0) return idx;
  return filtered.findIndex(entry => entry.request_id === requestId);
}

function ensureEntryVisibleForSearch(target) {
  let idx = findFilteredIdxByEntryKey(target.entryKey, target.requestId);
  if (idx < 0) {
    normalizeFiltersForGlobalSearch();
    idx = findFilteredIdxByEntryKey(target.entryKey, target.requestId);
  }
  if (idx < 0) return -1;
  const model = filtered[idx]?.request?.body?.model || 'unknown';
  if (collapsedGroups.has(model)) {
    collapsedGroups.delete(model);
    renderSidebar(true);
    idx = findFilteredIdxByEntryKey(target.entryKey, target.requestId);
  }
  return idx;
}

function navigateGlobalSearch(delta) {
  flushGlobalSearchRecalc();
  if (!globalSearchState.totalMatches) return;
  const total = globalSearchState.totalMatches;
  globalSearchState.currentMatch = (globalSearchState.currentMatch + delta + total) % total;
  updateGlobalSearchCount();
  revealCurrentSearchMatch();
}

function revealCurrentSearchMatch() {
  const target = getTargetForGlobalMatch(globalSearchState.currentMatch);
  if (!target) return;
  const filteredIdx = ensureEntryVisibleForSearch(target);
  if (filteredIdx < 0) return;
  const sameEntry = currentDetailEntryKey === target.entryKey;
  selectEntry(filteredIdx, { force: !sameEntry });
  applyGlobalSearchHighlights(target.localIndex);
}

function applyGlobalSearchHighlights(targetLocalIndex) {
  const detail = $('#detail');
  if (!detail) return;
  clearGlobalSearchHighlights(detail);
  const queries = buildGlobalHighlightQueries(globalSearchState.query);
  if (!queries.length) return;
  const marks = highlightSearchInContainer(detail, queries);
  autoExpandSearchMatches(marks);
  if (!marks.length) return;
  let idx = targetLocalIndex;
  if (idx < 0 || idx >= marks.length) idx = 0;
  marks.forEach((mark, i) => mark.classList.toggle('current', i === idx));
  marks[idx].scrollIntoView({ block: 'center' });
}

function clearGlobalSearchHighlights(container) {
  if (!container) return;
  container.querySelectorAll('mark.global-search-hit').forEach(mark => {
    mark.replaceWith(document.createTextNode(mark.textContent || ''));
  });
  container.normalize();
}

function findNextSearchMatch(text, queries, from) {
  let best = null;
  queries.forEach(query => {
    const idx = text.indexOf(query, from);
    if (idx === -1) return;
    if (!best || idx < best.index || (idx === best.index && query.length > best.query.length)) {
      best = { index: idx, query };
    }
  });
  return best;
}

function highlightSearchInContainer(container, queries) {
  if (!queries.length) return [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (parent.closest('#global-search-overlay')) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  const marks = [];
  textNodes.forEach(node => {
    const value = node.nodeValue;
    const lower = value.toLowerCase();
    let from = 0;
    let found = findNextSearchMatch(lower, queries, from);
    if (!found) return;
    const frag = document.createDocumentFragment();
    while (found) {
      if (found.index > from) frag.appendChild(document.createTextNode(value.slice(from, found.index)));
      const mark = document.createElement('mark');
      mark.className = 'global-search-hit';
      mark.textContent = value.slice(found.index, found.index + found.query.length);
      frag.appendChild(mark);
      marks.push(mark);
      from = found.index + found.query.length;
      found = findNextSearchMatch(lower, queries, from);
    }
    if (from < value.length) frag.appendChild(document.createTextNode(value.slice(from)));
    node.parentNode.replaceChild(frag, node);
  });
  return marks;
}

function autoExpandSearchMatches(marks) {
  marks.forEach(mark => {
    const sectionBody = mark.closest('.section-body');
    if (sectionBody && !sectionBody.classList.contains('open')) {
      sectionBody.classList.add('open');
      sectionBody.previousElementSibling?.querySelector('.chevron')?.classList.add('open');
    }
    let toolBody = mark.closest('.tool-block-body');
    while (toolBody) {
      if (!toolBody.classList.contains('open')) {
        toolBody.classList.add('open');
        toolBody.previousElementSibling?.querySelector('.tb-arrow')?.classList.add('open');
      }
      toolBody = toolBody.parentElement?.closest('.tool-block-body');
    }
  });
}
