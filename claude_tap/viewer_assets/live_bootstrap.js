/* ─── Live Mode SSE Support ─── */
let liveEventSource = null;
let liveConnected = false;
let currentDetailRequestId = null;
let currentDetailEntryKey = null;
let liveRecords = [];
let viewingDate = null; // null = live, string = historical date
let liveRenderTimer = null;
const liveSeenIds = new Set();

function initLiveMode() {
  const statusEl = $('#live-status');
  statusEl.style.display = 'flex';
  updateLiveStatus('connecting');

  liveEventSource = new EventSource('/events');

  liveEventSource.onopen = () => {
    liveConnected = true;
    updateLiveStatus('connected', liveRecords.length);
  };

  liveEventSource.onmessage = (event) => {
    try {
      const record = JSON.parse(event.data);
      // Deduplicate: SSE replays full history on each reconnect
      const id = record.request_id || record.req_id;
      if (id && liveSeenIds.has(id)) return;
      if (id) liveSeenIds.add(id);

      liveRecords.push(record);
      if (!viewingDate) {
        entries.push(...normalizeDisplayTurns(expandLiveWebSocketResponseEntries([record]), false));
        updateLiveStatus('connected', entries.length);
        clearTimeout(liveRenderTimer);
        liveRenderTimer = setTimeout(() => renderApp(true), 50);
      }
    } catch (e) {
      console.error('Failed to parse SSE data:', e);
    }
  };

  // EventSource auto-reconnects on transient errors;
  // only update status indicator here.
  liveEventSource.onerror = () => {
    liveConnected = false;
    updateLiveStatus('disconnected');
  };
}

function updateLiveStatus(status, count) {
  const el = $('#live-status');
  if (!el) return;
  const colors = {
    connecting: { bg: 'var(--amber-bg)', color: 'var(--amber)', dot: 'var(--amber)' },
    connected: { bg: 'var(--green-bg)', color: 'var(--green)', dot: 'var(--green)' },
    disconnected: { bg: 'var(--red-bg)', color: 'var(--red)', dot: 'var(--red)' }
  };
  const c = colors[status] || colors.connecting;
  const labels = { connecting: 'Connecting...', connected: 'Live', disconnected: 'Disconnected' };
  const countText = count !== undefined ? ` (${count})` : '';
  el.style.background = c.bg;
  el.style.color = c.color;
  el.innerHTML = `<span style="width:6px;height:6px;border-radius:50%;background:${c.dot};${status === 'connected' ? 'animation:pulse 2s infinite;' : ''}"></span>${labels[status]}${countText}`;
}

function renderLiveWaitingState() {
  $('#drop-zone').style.display = 'none';
  $('#sidebar-wrap').style.display = 'flex';
  $('#date-picker').style.display = 'flex';
  ['search-bar','sidebar-sort','tool-filter','position-indicator','sidebar'].forEach(id => {
    const el = $('#' + id);
    if (el) el.style.display = 'none';
  });
  $('#detail').style.display = '';
  $('#detail').innerHTML = '<div class="empty-state" role="status" aria-live="polite"><div style="font-size:48px;margin-bottom:16px;">📡</div><h2 style="margin-bottom:8px;color:var(--text);font-size:18px;">Waiting for API calls...</h2><p style="color:var(--text-secondary);">Start using Claude Code to see traces here in real-time</p></div>';
  $('#stats').style.display = 'none';
  $('#path-filter').style.display = 'none';
  updateHistoryDeleteButton();
}

function updateHistoryDeleteButton() {
  const btn = $('#history-delete-btn');
  const text = $('#history-delete-text');
  const sel = $('#date-select');
  if (!btn || !sel) return;
  const canDelete = sel.value && sel.value !== 'live';
  btn.disabled = !canDelete;
  btn.title = canDelete ? t('history_delete_title') : t('history_delete_live_title');
  btn.setAttribute('aria-label', btn.title);
  if (text) text.textContent = t('history_delete_btn');
}

function setHistoryDeleteStatus(message, tone = '') {
  const el = $('#history-delete-status');
  if (!el) return;
  el.className = `history-delete-status ${tone}`.trim();
  el.textContent = message || '';
  el.style.display = message ? 'block' : 'none';
}

async function fetchDates(preferredValue = null) {
  try {
    const resp = await fetch('/api/dates');
    const data = await resp.json();
    const sel = $('#date-select');
    if (!sel || !data.dates) return;
    const previous = preferredValue || sel.value || 'live';
    sel.innerHTML = '<option value="live">Live (current session)</option>';
    for (const d of data.dates) {
      sel.insertAdjacentHTML('beforeend', `<option value="${d}">${d}</option>`);
    }
    if (data.has_legacy) {
      sel.insertAdjacentHTML('beforeend', '<option value="legacy">Legacy (flat)</option>');
    }
    if ([...sel.options].some(option => option.value === previous)) sel.value = previous;
    const picker = $('#date-picker');
    if (picker) picker.style.display = 'flex';
    updateHistoryDeleteButton();
  } catch (e) {
    console.error('Failed to fetch dates:', e);
  }
}

async function onDateChange(value) {
  setHistoryDeleteStatus('');
  updateHistoryDeleteButton();
  if (value === 'live') {
    viewingDate = null;
    activePaths.clear();
    entries = normalizeDisplayTurns(expandLiveWebSocketResponseEntries(liveRecords.slice(), true), true);
    if (entries.length) renderApp(true);
    else renderLiveWaitingState();
    return;
  }
  viewingDate = value;
  try {
    const resp = await fetch('/api/traces/' + encodeURIComponent(value));
    activePaths.clear();
    entries = normalizeDisplayTurns(expandWebSocketResponseEntries(await resp.json()), true);
    renderApp();
  } catch (e) {
    console.error('Failed to load traces for date:', value, e);
  }
}

async function deleteSelectedTraceDate() {
  const sel = $('#date-select');
  if (!sel || !sel.value || sel.value === 'live') return;
  const value = sel.value;
  const label = sel.options[sel.selectedIndex]?.textContent || value;
  if (!window.confirm(formatText('history_delete_confirm', { date: label }))) return;

  const btn = $('#history-delete-btn');
  if (btn) btn.disabled = true;
  setHistoryDeleteStatus(t('history_delete_working'), 'warn');
  try {
    const resp = await fetch('/api/traces/' + encodeURIComponent(value), { method: 'DELETE' });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || resp.statusText || String(resp.status));

    const deleted = Number(data.deleted_files || 0);
    setHistoryDeleteStatus(
      deleted > 0
        ? formatText('history_delete_done', { count: deleted })
        : formatText('history_delete_empty', { date: label }),
      deleted > 0 ? 'ok' : 'warn'
    );
    await fetchDates('live');
    viewingDate = null;
    activePaths.clear();
    entries = normalizeDisplayTurns(expandLiveWebSocketResponseEntries(liveRecords.slice(), true), true);
    if (entries.length) renderApp(true);
    else renderLiveWaitingState();
  } catch (e) {
    console.error('Failed to delete trace history:', value, e);
    setHistoryDeleteStatus(formatText('history_delete_failed', { error: e.message || e }), 'error');
  } finally {
    updateHistoryDeleteButton();
  }
}

function isCompactBlobRef(value) {
  return value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    value.__claude_tap_blob_ref__ &&
    value.__claude_tap_blob_ref__.version === 1 &&
    value.__claude_tap_blob_ref__.kind === 'json' &&
    typeof value.__claude_tap_blob_ref__.hash === 'string';
}

function loadCompactBlobRef(value, blobs, cache) {
  const ref = value.__claude_tap_blob_ref__;
  if (!cache.has(ref.hash)) {
    const blob = blobs[ref.hash];
    if (!blob || blob.kind !== (ref.kind || 'json')) throw new Error(`Missing compact trace blob: ${ref.hash}`);
    cache.set(ref.hash, blob.payload);
  }
  return cache.get(ref.hash);
}

function parseCompactRefPath(path) {
  if (typeof path !== 'string' || !path.startsWith('/')) return null;
  return path.slice(1).split('/').map(part => part.replaceAll('~1', '/').replaceAll('~0', '~'));
}

function materializeCompactRefPath(value, path, blobs, cache) {
  if (!path.length) return isCompactBlobRef(value) ? loadCompactBlobRef(value, blobs, cache) : value;
  const [key, ...rest] = path;
  if (Array.isArray(value)) {
    const index = Number(key);
    if (!Number.isInteger(index) || index < 0 || index >= value.length) return value;
    const replacement = materializeCompactRefPath(value[index], rest, blobs, cache);
    if (replacement === value[index]) return value;
    const out = value.slice();
    out[index] = replacement;
    return out;
  }
  if (value && typeof value === 'object') {
    if (!Object.prototype.hasOwnProperty.call(value, key)) return value;
    const replacement = materializeCompactRefPath(value[key], rest, blobs, cache);
    if (replacement === value[key]) return value;
    return { ...value, [key]: replacement };
  }
  return value;
}

const LEGACY_COMPACT_BLOB_PATHS = [
  ['request', 'body', 'instructions'],
  ['request', 'body', 'tools'],
  ['response', 'body', 'instructions'],
  ['response', 'body', 'tools'],
];

const LEGACY_COMPACT_ITEM_BLOB_PATHS = [
  ['request', 'body', 'input'],
  ['request', 'body', 'messages'],
];

function getCompactPath(value, path) {
  let node = value;
  for (const key of path) {
    if (!node || typeof node !== 'object' || Array.isArray(node) || !Object.prototype.hasOwnProperty.call(node, key)) {
      return undefined;
    }
    node = node[key];
  }
  return node;
}

function legacyCompactRefPaths(record) {
  const paths = [];
  for (const path of LEGACY_COMPACT_BLOB_PATHS) {
    if (isCompactBlobRef(getCompactPath(record, path))) paths.push(path);
  }
  for (const path of LEGACY_COMPACT_ITEM_BLOB_PATHS) {
    const value = getCompactPath(record, path);
    if (!Array.isArray(value)) continue;
    value.forEach((item, index) => {
      if (isCompactBlobRef(item)) paths.push([...path, String(index)]);
    });
  }
  return paths;
}

function materializeCompactRecord(payload, blobs, cache) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  const marker = payload.__claude_tap_compact_record__;
  if (!marker) return payload;
  if (marker.version !== 1) throw new Error(`Unsupported compact trace record version: ${marker.version}`);
  let record = payload.record;
  let refPaths = Array.isArray(marker.refs)
    ? marker.refs.map(ref => parseCompactRefPath(ref && ref.path)).filter(Boolean)
    : [];
  if (!refPaths.length && record && typeof record === 'object' && !Array.isArray(record)) {
    refPaths = legacyCompactRefPaths(record);
  }
  for (const path of refPaths) {
    record = materializeCompactRefPath(record, path, blobs, cache);
  }
  return record && typeof record === 'object' && !Array.isArray(record) ? record : null;
}

function materializeCompactTraceBundle(bundle) {
  const marker = bundle && typeof bundle === 'object' ? bundle.__claude_tap_compact_trace__ : null;
  if (!marker) return null;
  if (marker.version !== 1) throw new Error(`Unsupported compact trace bundle version: ${marker.version}`);
  const records = Array.isArray(bundle.records) ? bundle.records : [];
  const blobs = bundle.blobs && typeof bundle.blobs === 'object' ? bundle.blobs : {};
  const cache = new Map();
  return records.map(record => materializeCompactRecord(record, blobs, cache)).filter(Boolean);
}

function parseTraceText(text) {
  const trimmed = text.trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    const compactRecords = materializeCompactTraceBundle(parsed);
    if (compactRecords) return compactRecords;
    if (Array.isArray(parsed)) return parsed.filter(item => item && typeof item === 'object' && !Array.isArray(item));
  } catch {
    // Fall through to JSONL parsing.
  }
  return trimmed.split('\n').map(line => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);
}

if (typeof LIVE_MODE !== 'undefined' && LIVE_MODE) {
  entries = typeof EMBEDDED_TRACE_DATA !== 'undefined' ? normalizeDisplayTurns(expandLiveWebSocketResponseEntries(EMBEDDED_TRACE_DATA, true), true) : [];
  document.addEventListener('DOMContentLoaded', () => {
    initCommonUi();
    initLiveMode();
    fetchDates();
    if (entries.length) renderApp();
    else {
      renderLiveWaitingState();
    }
  });
} else if (typeof EMBEDDED_TRACE_COMPACT_DATA !== 'undefined') {
  entries = normalizeDisplayTurns(expandWebSocketResponseEntries(materializeCompactTraceBundle(EMBEDDED_TRACE_COMPACT_DATA) || []), true);
  document.addEventListener('DOMContentLoaded', () => {
    initCommonUi();
    if (entries.length) renderApp();
    else renderEmptyTraceState();
  });
} else if (typeof EMBEDDED_TRACE_META !== 'undefined') {
  // Lazy mode: build stub entries from metadata
  lazyMode = true;
  entries = normalizeDisplayTurns(EMBEDDED_TRACE_META.map((meta, i) => buildStubEntry(meta, i)), true);
  document.addEventListener('DOMContentLoaded', () => {
    initCommonUi();
    if (entries.length) renderApp();
    else renderEmptyTraceState();
  });
} else if (typeof EMBEDDED_TRACE_DATA !== 'undefined') {
  entries = normalizeDisplayTurns(expandWebSocketResponseEntries(EMBEDDED_TRACE_DATA), true);
  document.addEventListener('DOMContentLoaded', () => {
    initCommonUi();
    if (entries.length) renderApp();
    else renderEmptyTraceState();
  });
} else {
  document.addEventListener('DOMContentLoaded', () => {
    initCommonUi();
    initFileDropZone();
  });
}

function loadFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    entries = normalizeDisplayTurns(expandWebSocketResponseEntries(parseTraceText(reader.result)), true);
    if (!entries.length) { alert('No valid entries found / 未找到有效条目'); return; }
    renderApp();
  };
  reader.readAsText(file);
}
