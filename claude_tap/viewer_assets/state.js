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
