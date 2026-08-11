const $ = s => document.querySelector(s);
const EMBED_QUERY_OPTIONS = parseEmbedQueryOptions();
let entries = [], filtered = [], activeIdx = -1, activePaths = new Set(), searchQuery = '', activeTools = null;
let sessionImageRegistryCache = null, sessionImageRegistrySize = -1;
let visualOrder = []; // filtered indices in sidebar visual (DOM) order, excludes collapsed items
const SIDEBAR_ORDER_MODES = ['model', 'turn', 'session'];
function safeLocalStorageGet(key) {
  try { return window.localStorage.getItem(key); } catch(e) { return null; }
}
function safeLocalStorageSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch(e) {}
}
const savedSidebarOrderMode = safeLocalStorageGet('claude-tap-sidebar-order');
let sidebarOrderMode = SIDEBAR_ORDER_MODES.includes(savedSidebarOrderMode) ? savedSidebarOrderMode : 'model';

function readBooleanQuery(params, key) {
  const value = params.get(key);
  return value === '1' || value === 'true' || value === '';
}

function parseEmbedQueryOptions() {
  const params = new URLSearchParams(window.location.search || '');
  const enabled = readBooleanQuery(params, 'embed') || readBooleanQuery(params, 'iframe');
  const theme = params.get('theme') === 'dark' ? 'dark' : params.get('theme') === 'light' ? 'light' : null;
  return {
    enabled,
    hideHeader: enabled && readBooleanQuery(params, 'hideHeader'),
    hidePath: enabled && readBooleanQuery(params, 'hidePath'),
    hideHistory: enabled && readBooleanQuery(params, 'hideHistory'),
    hideControls: enabled && readBooleanQuery(params, 'hideControls'),
    compact: enabled && params.get('density') === 'compact',
    theme,
  };
}

function cloneJson(value) {
  if (value === undefined || value === null) return value;
  try { return JSON.parse(JSON.stringify(value)); } catch(e) { return value; }
}

let viewerViewState = null;
let viewerViewStateLoadedKey = null;

function viewerViewStateKey() {
  let traceIdentity = '';
  if (typeof TRACE_JSONL_PATH !== 'undefined') traceIdentity = TRACE_JSONL_PATH || '';
  if (!traceIdentity && typeof TRACE_HTML_PATH !== 'undefined') traceIdentity = TRACE_HTML_PATH || '';
  if (!traceIdentity) traceIdentity = window.location.pathname + window.location.search;
  const date = typeof viewingDate !== 'undefined' && viewingDate ? viewingDate : 'live';
  return `claude-tap-view-state:${traceIdentity}:${date}`;
}

function safeSessionStorageGet(key) {
  try { return window.sessionStorage.getItem(key); } catch(e) { return null; }
}
function safeSessionStorageSet(key, value) {
  try { window.sessionStorage.setItem(key, value); } catch(e) {}
}

function restoreViewerViewState() {
  const stateKey = viewerViewStateKey();
  if (viewerViewStateLoadedKey === stateKey) return !!viewerViewState;
  viewerViewStateLoadedKey = stateKey;
  viewerViewState = null;
  const raw = safeSessionStorageGet(stateKey);
  if (!raw) return false;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return false;
    viewerViewState = parsed;
    if (typeof currentDetailRequestId !== 'undefined') currentDetailRequestId = parsed.requestId || null;
    if (typeof currentDetailEntryKey !== 'undefined') currentDetailEntryKey = parsed.entryKey || null;
    return true;
  } catch(e) {
    return false;
  }
}

function persistViewerViewState() {
  const sidebar = $('#sidebar');
  viewerViewState = {
    requestId: typeof currentDetailRequestId !== 'undefined' ? currentDetailRequestId : null,
    entryKey: typeof currentDetailEntryKey !== 'undefined' ? currentDetailEntryKey : null,
    sidebarScrollTop: sidebar ? sidebar.scrollTop : 0,
  };
  safeSessionStorageSet(viewerViewStateKey(), JSON.stringify(viewerViewState));
}

function savedViewerSidebarScrollTop() {
  const value = Number(viewerViewState?.sidebarScrollTop);
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function entryStableKey(entry) {
  if (!entry) return '';
  const requestId = entry.request_id || entry.req_id || '';
  const parts = [requestId || 'entry'];
  const entryIndex = entry._entry_index ?? entry._rawIdx;
  const websocketIndex = entry.websocket_response_index;
  const recordIndex = entry.record_index;
  const captureTurn = entry.capture_turn ?? entry.turn;
  if (entryIndex !== undefined && entryIndex !== null && entryIndex !== '') {
    parts.push(`idx:${entryIndex}`);
  } else if (websocketIndex !== undefined && websocketIndex !== null && websocketIndex !== '') {
    parts.push(`ws:${websocketIndex}`);
  } else if (recordIndex !== undefined && recordIndex !== null && recordIndex !== '') {
    parts.push(`record:${recordIndex}`);
  } else if (captureTurn !== undefined && captureTurn !== null && captureTurn !== '') {
    parts.push(`turn:${captureTurn}`);
  }
  return parts.join('|');
}
