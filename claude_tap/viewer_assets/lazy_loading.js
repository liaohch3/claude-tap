/* ─── Lazy loading infrastructure ─── */
const LAZY_THRESHOLD = 50;
let lazyMode = false;
let rawLines = null; // array of raw JSON strings, populated on first access
const entryCache = new Map(); // index -> parsed full entry
const remoteEntryPromises = new Map(); // index -> pending record fetch

function getRawLines() {
  if (rawLines) return rawLines;
  const el = document.getElementById('trace-raw');
  if (!el) return [];
  const text = el.textContent;
  // Free DOM node memory — we no longer need the script element
  el.remove();
  rawLines = text.split('\n').filter(l => l.trim());
  return rawLines;
}

function hasEmbeddedRawLines() {
  return !!rawLines || !!document.getElementById('trace-raw');
}

function buildStubEntry(meta, rawIdx) {
  // Build an entry object with the same shape as real entries so existing
  // sidebar rendering code works unchanged. Nested paths are constructed
  // to satisfy property access patterns (e.g. entry.request.body.model).
  const usage = {};
  if (meta.input_tokens) usage.input_tokens = meta.input_tokens;
  if (meta.output_tokens) usage.output_tokens = meta.output_tokens;
  const hasCacheCreate = meta.cache_creation_input_tokens !== undefined && meta.cache_creation_input_tokens !== null;
  if (meta.cache_read_input_tokens) {
    usage.cache_read_input_tokens = meta.cache_read_input_tokens;
    /* Infer cache embedding style from model name so the cache hit rate
       denominator is correct in lazy/dashboard mode.  Claude/Anthropic and
       Bedrock keep cache_read as a separate bucket; OpenAI/Gemini embed it. */
    const m = (meta.model || '').toLowerCase();
    usage._cache_read_in_input = !(hasCacheCreate || m.includes('claude') || m.includes('anthropic') || m.includes('bedrock'));
  }
  if (meta.cache_creation_input_tokens) usage.cache_creation_input_tokens = meta.cache_creation_input_tokens;

  // Build a minimal system field to support task fingerprinting
  const body = { model: meta.model || '' };
  if (meta.codex_app_session_id) {
    body.metadata = { codex_app_session_id: meta.codex_app_session_id };
  }
  if (typeof meta.request_generate === 'boolean') body.generate = meta.request_generate;
  if (meta.has_system && meta.sys_hint) {
    body.system = meta.sys_hint;
  }
  if (meta.tool_names && meta.tool_names.length) {
    body.tools = meta.tool_names.map(n => ({ name: n }));
  }

  // Build minimal response content for tool filter
  const respContent = [];
  if (meta.response_tool_names && meta.response_tool_names.length) {
    meta.response_tool_names.forEach(n => respContent.push({ type: 'tool_use', name: n }));
  }

  const responseBody = {
    usage: usage,
    content: respContent.length ? respContent : undefined,
    error: meta.error_message ? { message: meta.error_message } : undefined,
  };
  if (typeof meta.response_generate === 'boolean') responseBody.generate = meta.response_generate;
  if (meta.response_output_count) responseBody.output = Array.from({ length: meta.response_output_count }, () => ({}));

  return {
    _isStub: true,
    _rawIdx: rawIdx,
    _entry_index: rawIdx,
    turn: meta.turn,
    request_id: meta.request_id || '',
    timestamp: meta.timestamp || '',
    duration_ms: meta.duration_ms || 0,
    transport: meta.transport || '',
    _session_user_text: meta.session_user_text || '',
    request: {
      method: meta.method || '',
      path: meta.path || '',
      headers: meta.codex_app_session_id ? { 'x-codex-app-session-id': meta.codex_app_session_id } : {},
      body: body,
    },
    response: {
      status: meta.status || 0,
      body: responseBody,
    },
  };
}

function toolDisplayName(td) {
  if (!td || typeof td !== 'object') return '';
  const candidates = [
    td.name,
    td.function && typeof td.function === 'object' ? td.function.name : null,
    td.id,
    td.type
  ];
  for (const value of candidates) {
    if (typeof value === 'string' && value) return value;
  }
  return '';
}

function toolDescription(td) {
  if (!td || typeof td !== 'object') return '';
  const desc = td.description || (td.function && typeof td.function === 'object' ? td.function.description : '');
  return typeof desc === 'string' ? desc : '';
}

function toolSchema(td) {
  if (!td || typeof td !== 'object') return {};
  return td.input_schema || td.parameters || (td.function && typeof td.function === 'object' ? td.function.parameters : null) || {};
}

function getFullEntry(entry) {
  if (!entry._isStub) return entry;
  const idx = entry._rawIdx;
  if (entryCache.has(idx)) return entryCache.get(idx);
  const lines = getRawLines();
  if (idx < 0 || idx >= lines.length) return entry;
  try {
    const full = JSON.parse(lines[idx]);
    entryCache.set(idx, full);
    return full;
  } catch (e) {
    console.error('Failed to parse entry at index', idx, e);
    return entry;
  }
}

function shouldFetchRemoteEntry(entry) {
  return !!(entry && entry._isStub && TRACE_RECORDS_API && !hasEmbeddedRawLines());
}

function remoteRecordUrl(idx) {
  const sep = TRACE_RECORDS_API.includes('?') ? '&' : '?';
  return `${TRACE_RECORDS_API}${sep}offset=${encodeURIComponent(idx)}&limit=1`;
}

async function fetchRemoteEntry(entry) {
  if (!shouldFetchRemoteEntry(entry)) return getFullEntry(entry);
  const idx = entry._rawIdx;
  if (entryCache.has(idx)) return entryCache.get(idx);
  if (!remoteEntryPromises.has(idx)) {
    remoteEntryPromises.set(idx, fetch(remoteRecordUrl(idx))
      .then(async resp => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const payload = await resp.json();
        const record = Array.isArray(payload.records) ? payload.records[0] : null;
        if (!record || typeof record !== 'object') return entry;
        entryCache.set(idx, record);
        return record;
      })
      .catch(err => {
        remoteEntryPromises.delete(idx);
        throw err;
      }));
  }
  return remoteEntryPromises.get(idx);
}

function withDisplayFields(full, entry) {
  return {
    ...full,
    _entry_index: entry._entry_index,
    display_turn: entry.display_turn,
    capture_turn: entry.capture_turn,
    record_index: entry.record_index,
    websocket_response_index: entry.websocket_response_index,
  };
}

function resolveEntryForDetail(entry) {
  if (!entry || !entry._isStub) return entry;
  return withDisplayFields(getFullEntry(entry), entry);
}

async function resolveEntryForDetailAsync(entry) {
  if (!entry || !entry._isStub) return entry;
  return withDisplayFields(await fetchRemoteEntry(entry), entry);
}

/* ─── Virtual scroll state ─── */
let virtualMode = false;
const VS_ITEM_HEIGHT = 68;
const VS_BUFFER = 10;
let vsFilteredItems = []; // {entry, idx} pairs for virtual scroll

const globalSearchState = {
  open: false,
  query: '',
  queries: [],
  matchCounts: [],
  totalMatches: 0,
  currentMatch: -1,
  textCache: new Map(),
  recalcTimer: 0,
};
const TRACE_JSONL_PATH = typeof __TRACE_JSONL_PATH__ !== 'undefined' ? __TRACE_JSONL_PATH__ : '';
const TRACE_HTML_PATH = typeof __TRACE_HTML_PATH__ !== 'undefined' ? __TRACE_HTML_PATH__ : '';
const TRACE_RECORDS_API = typeof __TRACE_RECORDS_API__ !== 'undefined' ? __TRACE_RECORDS_API__ : '';
const CLAUDE_TAP_VERSION = typeof __CLAUDE_TAP_VERSION__ !== 'undefined' ? __CLAUDE_TAP_VERSION__ : '';
const TRACE_SESSION_EXPORTS = typeof __TRACE_SESSION_EXPORTS__ !== 'undefined' ? __TRACE_SESSION_EXPORTS__ : null;
