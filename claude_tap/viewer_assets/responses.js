function isCodexResponsesWebSocketEntry(entry) {
  if (entry?.transport !== 'websocket') return false;
  const path = entry?.request?.path || '';
  return path.endsWith('/backend-api/codex/responses') || path.endsWith('/v1/responses') || path === '/responses';
}

function isResponsesPath(path) {
  return path === '/responses' || path === '/v1/responses' || path.endsWith('/backend-api/codex/responses');
}

function getResponseIdFromEvent(event) {
  const data = getEventData(event);
  return data?.response?.id || data?.item?.id || data?.item_id || '';
}

function responseEventMatchesId(event, responseId) {
  if (!responseId) return false;
  const data = getEventData(event);
  if (data?.response?.id === responseId) return true;
  const itemId = data?.item?.id || data?.item_id || '';
  return itemId.includes(responseId.slice(5, 25));
}

function splitWebSocketResponseEvents(events) {
  const groups = [];
  let current = null;
  for (const event of events || []) {
    const type = getEventType(event);
    if (type === 'response.created') {
      if (current && current.events.length) groups.push(current);
      current = { responseId: getResponseIdFromEvent(event), events: [event] };
      continue;
    }
    if (!current) continue;
    if (responseEventMatchesId(event, current.responseId) || type.startsWith('response.')) {
      current.events.push(event);
    }
    if (type === 'response.completed') {
      groups.push(current);
      current = null;
    }
  }
  if (current && current.events.length) groups.push(current);
  return groups.filter(group => group.events.some(event => getEventType(event) === 'response.completed'));
}

function completedResponseFromEvents(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    if (getEventType(events[i]) !== 'response.completed') continue;
    const data = getEventData(events[i]);
    if (data?.response && typeof data.response === 'object') return data.response;
  }
  return null;
}

function hasOutputItemEvent(events) {
  return events.some(event => getEventType(event) === 'response.output_item.done');
}

function isDisplayableWebSocketResponseGroup(group) {
  const completed = completedResponseFromEvents(group.events);
  if (!completed) return false;
  const created = responseCreatedFromEvents(group.events);
  const isGenerateFalse = completed.generate === false || created?.generate === false;
  return !(isGenerateFalse && !hasOutputItemEvent(group.events) && (completed.usage?.output_tokens || 0) === 0);
}

function responseCreatedFromEvents(events) {
  for (const event of events) {
    if (getEventType(event) !== 'response.created') continue;
    const data = getEventData(event);
    if (data?.response && typeof data.response === 'object') return data.response;
  }
  return null;
}

function mergeWebSocketResponseGroups(groups) {
  const events = [];
  for (const group of groups) events.push(...group.events);
  return { responseId: groups.map(group => group.responseId).filter(Boolean).join('+'), events };
}

function parseResponseToolArguments(args) {
  if (args === undefined || args === null || args === '') return {};
  if (typeof args !== 'string') return args;
  try { return JSON.parse(args); } catch(e) { return args; }
}

function toolSearchOutputContent(item) {
  const names = [];
  if (Array.isArray(item?.tools)) {
    for (const namespace of item.tools) {
      if (!namespace || typeof namespace !== 'object') continue;
      const namespaceName = typeof namespace.name === 'string' ? namespace.name : '';
      if (namespaceName) names.push(namespaceName);
      if (!Array.isArray(namespace.tools)) continue;
      for (const tool of namespace.tools) {
        if (!tool || typeof tool !== 'object' || typeof tool.name !== 'string' || !tool.name) continue;
        names.push(namespaceName ? `${namespaceName}.${tool.name}` : tool.name);
      }
    }
  }
  if (names.length) return ['tool_search_output', ...names].join('\n');
  if (Array.isArray(item?.tools)) return JSON.stringify(item.tools, null, 2);
  if (item?.output !== undefined) return typeof item.output === 'string' ? item.output : JSON.stringify(item.output, null, 2);
  return JSON.stringify(item || {}, null, 2);
}

function responseCallToolName(item) {
  if (item?.type === 'tool_search_call') return 'tool_search';
  if (typeof item?.name === 'string' && item.name) return item.name;
  if (typeof item?.type === 'string' && item.type.endsWith('_call')) return item.type.slice(0, -'_call'.length);
  return '';
}

function isResponseCallItem(item) {
  return !!(item && typeof item === 'object' && typeof item.type === 'string' && item.type.endsWith('_call'));
}

function responseCallInput(item) {
  if (Object.prototype.hasOwnProperty.call(item, 'arguments')) return parseResponseToolArguments(item.arguments);
  const input = {};
  for (const [key, value] of Object.entries(item || {})) {
    if (['id', 'type', 'status', 'call_id', 'name', 'execution'].includes(key)) continue;
    input[key] = value;
  }
  return input;
}

function isResponseToolResultItem(item) {
  if (!item || typeof item !== 'object' || typeof item.type !== 'string') return false;
  return item.type === 'tool_search_output' || item.type.endsWith('_call_output');
}

function responseToolResultContent(item) {
  if (item?.type === 'tool_search_output') return toolSearchOutputContent(item);
  if (Object.prototype.hasOwnProperty.call(item || {}, 'output')) {
    return typeof item.output === 'string' ? item.output : JSON.stringify(item.output, null, 2);
  }
  const content = {};
  for (const [key, value] of Object.entries(item || {})) {
    if (['id', 'type', 'status', 'call_id', 'execution'].includes(key)) continue;
    content[key] = value;
  }
  return JSON.stringify(content, null, 2);
}

function responseInputItemToMessage(item) {
  if (!item || typeof item !== 'object') return item;
  if (isResponseCallItem(item)) {
    return {
      type: 'message',
      role: 'assistant',
      content: [{
        type: 'tool_use',
        id: item.call_id || item.id || '',
        name: responseCallToolName(item),
        input: responseCallInput(item)
      }]
    };
  }
  if (isResponseToolResultItem(item)) {
    return {
      type: 'message',
      role: 'tool',
      content: [{ type: 'tool_result', tool_use_id: item.call_id || '', content: responseToolResultContent(item) }]
    };
  }
  return item;
}

function normalizeWebSocketDerivedInput(input) {
  if (!Array.isArray(input)) return input;
  return input.map(responseInputItemToMessage);
}

function webSocketOutputMessages(events) {
  const messages = [];
  const items = [];
  for (const event of events || []) {
    if (getEventType(event) !== 'response.output_item.done') continue;
    const data = getEventData(event);
    if (data?.item && Number.isInteger(data.output_index)) items.push({ outputIndex: data.output_index, item: data.item });
  }
  items.sort((a, b) => a.outputIndex - b.outputIndex);
  for (const { item } of items) {
    if (isResponseCallItem(item)) {
      messages.push({
        type: 'message',
        role: 'assistant',
        content: [{
          type: 'tool_use',
          id: item.call_id || item.id || '',
          name: responseCallToolName(item),
          input: responseCallInput(item)
        }]
      });
    } else if (item.type === 'message') {
      messages.push({
        type: 'message',
        role: item.role || 'assistant',
        content: Array.isArray(item.content) ? item.content : []
      });
    }
  }
  return messages;
}

function responseBodyOutputMessages(output) {
  const messages = [];
  if (!Array.isArray(output)) return messages;
  for (const item of output) {
    if (!item || typeof item !== 'object') continue;
    if (isResponseCallItem(item)) {
      messages.push({
        type: 'message',
        role: 'assistant',
        content: [{
          type: 'tool_use',
          id: item.call_id || item.id || '',
          name: responseCallToolName(item),
          input: responseCallInput(item)
        }]
      });
      continue;
    }
    if (item.type === 'message') {
      messages.push({
        type: 'message',
        role: item.role || 'assistant',
        content: Array.isArray(item.content) ? item.content : []
      });
    }
  }
  return messages;
}

function isToolResultInputItem(item) {
  if (!item || typeof item !== 'object') return false;
  if (isResponseToolResultItem(item)) return true;
  if (item.role !== 'tool') return false;
  const content = Array.isArray(item.content) ? item.content : [];
  return content.some(block => block?.type === 'tool_result');
}

function messageKey(message) {
  try { return JSON.stringify(message); } catch(e) { return String(message); }
}

function uniqueMessages(messages) {
  const seen = new Set();
  const unique = [];
  for (const message of messages) {
    const key = messageKey(message);
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(message);
  }
  return unique;
}

function regularMessagesFromBody(body) {
  const normalized = normalizeWebSocketDerivedInput(Array.isArray(body?.input) ? body.input : []);
  return normalized.filter(item => !isToolResultInputItem(item));
}

function toolResultMessagesFromInput(input) {
  const normalized = normalizeWebSocketDerivedInput(Array.isArray(input) ? input : []);
  return normalized.filter(isToolResultInputItem);
}

function toolUseIdsFromMessage(message) {
  const content = Array.isArray(message?.content) ? message.content : [];
  return content
    .filter(block => block?.type === 'tool_use')
    .map(block => block.id || block.tool_use_id || '')
    .filter(Boolean);
}

function toolResultIdsFromMessage(message) {
  const content = Array.isArray(message?.content) ? message.content : [];
  return content
    .filter(block => block?.type === 'tool_result')
    .map(block => block.tool_use_id || block.id || '')
    .filter(Boolean);
}

function interleaveWebSocketOutputGroups(outputGroups, toolResults) {
  const resultPool = (toolResults || []).map(message => ({
    message,
    ids: toolResultIdsFromMessage(message),
    used: false
  }));
  const messages = [];
  for (const outputMessages of outputGroups || []) {
    messages.push(...outputMessages);
    const callIds = outputMessages.flatMap(toolUseIdsFromMessage);
    for (const callId of callIds) {
      const matched = resultPool.find(result => !result.used && result.ids.includes(callId));
      if (!matched) continue;
      matched.used = true;
      messages.push(matched.message);
    }
  }
  messages.push(...resultPool.filter(result => !result.used).map(result => result.message));
  return messages;
}

function buildWebSocketHistoryInput(input, priorGroups, priorRequestBodies) {
  const normalized = normalizeWebSocketDerivedInput(Array.isArray(input) ? input : []);
  const regularMessages = uniqueMessages([
    ...(priorRequestBodies || []).flatMap(regularMessagesFromBody),
    ...normalized.filter(item => !isToolResultInputItem(item))
  ]);
  const toolResults = uniqueMessages([
    ...(priorRequestBodies || []).flatMap(body => toolResultMessagesFromInput(body?.input)),
    ...normalized.filter(isToolResultInputItem)
  ]);
  const outputGroups = (priorGroups || []).map(group => webSocketOutputMessages(group.events));
  const priorOutputKeys = new Set(outputGroups.flat().map(messageKey));
  const contextMessages = regularMessages.filter(message => !priorOutputKeys.has(messageKey(message)));
  const interleavedOutputs = interleaveWebSocketOutputGroups(outputGroups, toolResults);
  return uniqueMessages([...contextMessages, ...interleavedOutputs]);
}

function requestBodiesForWebSocketEntry(entry) {
  const events = entry?.request?.ws_events;
  if (Array.isArray(events) && events.length) {
    return events
      .map(event => getEventData(event))
      .filter(event => event && typeof event === 'object');
  }
  return [];
}

function previousResponseIdForGroup(group) {
  const created = responseCreatedFromEvents(group.events);
  const completed = completedResponseFromEvents(group.events);
  return created?.previous_response_id || completed?.previous_response_id || '';
}

function requestBodyForWebSocketGroup(entry, groups, idx) {
  const requestBodies = requestBodiesForWebSocketEntry(entry);
  const previousId = previousResponseIdForGroup(groups[idx]);
  if (previousId) {
    const matched = requestBodies.find(body => body?.previous_response_id === previousId);
    if (matched) return matched;
  }
  const nonPrefetchBodies = requestBodies.filter(body => body?.generate !== false);
  if (nonPrefetchBodies[idx]) return nonPrefetchBodies[idx];
  if (requestBodies[idx]) return requestBodies[idx];
  return idx === 0 ? bodyWithoutToolResultOnlyInput(entry.request?.body) : entry.request?.body;
}

function requestSourceForWebSocketResponseEntry(entry, groups, idx, priorHistoryInput = []) {
  const hasRequestEvents = Array.isArray(entry.request?.ws_events) && entry.request.ws_events.length > 0;
  if (Array.isArray(priorHistoryInput) && priorHistoryInput.length && !hasRequestEvents && entry.request?.body) {
    return entry.request.body;
  }
  return requestBodyForWebSocketGroup(entry, groups, idx);
}

function bodyWithoutToolResultOnlyInput(body) {
  const next = cloneJson(body || {}) || {};
  if (Array.isArray(next.input) && next.input.every(isToolResultInputItem)) {
    next.input = [];
  }
  return next;
}

function buildWebSocketResponseEntry(entry, groups, idx, priorHistoryInput = []) {
  const group = groups[idx];
  const completed = completedResponseFromEvents(group.events) || {};
  const created = responseCreatedFromEvents(group.events) || {};
  const priorGroups = groups.slice(0, idx);
  const priorRequestBodies = priorGroups.map((_, priorIdx) => requestBodyForWebSocketGroup(entry, groups, priorIdx));
  const requestSource = requestSourceForWebSocketResponseEntry(entry, groups, idx, priorHistoryInput);
  const requestBody = cloneJson(requestSource || {}) || {};
  const source = Object.keys(completed).length ? completed : created;
  for (const key of ['model', 'instructions', 'tools', 'previous_response_id', 'prompt_cache_key', 'tool_choice', 'parallel_tool_calls', 'text', 'reasoning']) {
    if (source[key] !== undefined && source[key] !== null) requestBody[key] = source[key];
  }
  if (Array.isArray(priorHistoryInput) && priorHistoryInput.length) {
    const currentInput = normalizeWebSocketDerivedInput(Array.isArray(requestBody.input) ? requestBody.input : []);
    requestBody.input = uniqueMessages([...priorHistoryInput, ...currentInput]);
  } else {
    requestBody.input = buildWebSocketHistoryInput(requestBody.input, priorGroups, priorRequestBodies);
  }
  const createdAt = completed.created_at || created.created_at;
  const completedAt = completed.completed_at;
  const durationMs = createdAt && completedAt ? Math.max(0, (completedAt - createdAt) * 1000) : entry.duration_ms;
  const splitFromSingleRecord = groups.length > 1;
  const captureTurn = splitFromSingleRecord ? `${entry.turn || '?'}.${idx + 1}` : entry.turn;
  return {
    ...cloneJson(entry),
    request_id: splitFromSingleRecord ? `${entry.request_id || 'ws'}:${idx + 1}` : entry.request_id,
    turn: captureTurn,
    capture_turn: captureTurn,
    duration_ms: durationMs,
    timestamp: createdAt ? new Date(createdAt * 1000).toISOString() : entry.timestamp,
    derived_from_websocket: splitFromSingleRecord || entry.derived_from_websocket === true,
    websocket_response_index: idx + 1,
    request: {
      ...(cloneJson(entry.request) || {}),
      body: requestBody,
    },
    response: {
      ...(cloneJson(entry.response) || {}),
      body: cloneJson(completed),
      ws_events: group.events,
    },
  };
}

function createWebSocketResponseHistoryStore() {
  return new Map();
}

function webSocketHistoryInputForResponse(historyByResponseId, responseId) {
  const chain = [];
  const seen = new Set();
  let cursor = responseId;
  while (cursor && historyByResponseId.has(cursor) && !seen.has(cursor)) {
    seen.add(cursor);
    const node = historyByResponseId.get(cursor);
    chain.push(node);
    cursor = node.previousResponseId || '';
  }
  chain.reverse();
  return uniqueMessages(chain.flatMap(node => [
    ...(node.requestInput || []),
    ...(node.outputMessages || [])
  ]));
}

function storeWebSocketResponseHistory(historyByResponseId, responseId, previousResponseId, requestBody, outputMessages) {
  if (!responseId) return;
  const requestInput = normalizeWebSocketDerivedInput(Array.isArray(requestBody?.input) ? requestBody.input : []);
  historyByResponseId.set(responseId, {
    previousResponseId: previousResponseId || '',
    requestInput,
    outputMessages: outputMessages || []
  });
}

function isDisplayableResponsesEntry(entry) {
  const body = entry?.request?.body || {};
  const payload = getResponsePayload(entry) || {};
  const output = Array.isArray(payload.output) ? payload.output : [];
  const isGenerateFalse = body.generate === false || payload.generate === false;
  return !(isGenerateFalse && output.length === 0 && (payload.usage?.output_tokens || 0) === 0);
}

function stitchDirectResponsesEntry(entry, historyByResponseId) {
  const body = entry?.request?.body;
  const payload = getResponsePayload(entry) || {};
  if (!body || typeof body !== 'object') return entry;
  const previousId = body.previous_response_id || payload.previous_response_id || '';
  if (!isDisplayableResponsesEntry(entry)) {
    const responseId = payload.id || '';
    if (responseId) {
      storeWebSocketResponseHistory(
        historyByResponseId,
        responseId,
        previousId,
        body,
        responseBodyOutputMessages(payload.output),
      );
    }
    return null;
  }
  let nextEntry = entry;
  let requestBody = body;
  if (previousId) {
    const priorHistoryInput = webSocketHistoryInputForResponse(historyByResponseId, previousId);
    if (Array.isArray(priorHistoryInput) && priorHistoryInput.length) {
      requestBody = cloneJson(body) || {};
      const currentInput = normalizeWebSocketDerivedInput(Array.isArray(requestBody.input) ? requestBody.input : []);
      requestBody.input = uniqueMessages([...priorHistoryInput, ...currentInput]);
      nextEntry = {
        ...cloneJson(entry),
        request: {
          ...(cloneJson(entry.request) || {}),
          body: requestBody,
        },
      };
    }
  }
  const responseId = payload.id || '';
  if (responseId) {
    storeWebSocketResponseHistory(
      historyByResponseId,
      responseId,
      previousId,
      requestBody,
      responseBodyOutputMessages(payload.output),
    );
  }
  return nextEntry;
}

function expandWebSocketResponseEntries(rawEntries, historyByResponseId = createWebSocketResponseHistoryStore()) {
  const expanded = [];
  for (const entry of rawEntries || []) {
    if (!isCodexResponsesWebSocketEntry(entry)) {
      expanded.push(entry);
      continue;
    }
    const groups = splitWebSocketResponseEvents(entry.response?.ws_events || []);
    if (!groups.length) {
      const stitchedEntry = stitchDirectResponsesEntry(entry, historyByResponseId);
      if (stitchedEntry) expanded.push(stitchedEntry);
      continue;
    }
    const displayableGroups = groups
      .map((group, idx) => ({ group, idx }))
      .filter(({ group }) => isDisplayableWebSocketResponseGroup(group));
    if (!displayableGroups.length) continue;
    for (const { group, idx } of displayableGroups) {
      const previousId = previousResponseIdForGroup(group);
      const priorHistoryInput = previousId ? webSocketHistoryInputForResponse(historyByResponseId, previousId) : [];
      const responseEntry = buildWebSocketResponseEntry(entry, groups, idx, priorHistoryInput);
      expanded.push(responseEntry);

      const completed = completedResponseFromEvents(group.events) || {};
      const created = responseCreatedFromEvents(group.events) || {};
      const responseId = completed.id || created.id || '';
      if (responseId) {
        const outputMessages = webSocketOutputMessages(group.events);
        const requestBody = requestSourceForWebSocketResponseEntry(entry, groups, idx, priorHistoryInput);
        storeWebSocketResponseHistory(historyByResponseId, responseId, previousId, requestBody, outputMessages);
      }
    }
  }
  return expanded;
}

let liveWebSocketResponseHistoryById = createWebSocketResponseHistoryStore();

function expandLiveWebSocketResponseEntries(rawEntries, reset = false) {
  if (reset) liveWebSocketResponseHistoryById = createWebSocketResponseHistoryStore();
  return expandWebSocketResponseEntries(rawEntries, liveWebSocketResponseHistoryById);
}

let displayTurnPrimary = 0;
let displayTurnSub = 0;
let previousDisplayTurnInfo = null;
const DISPLAY_TURN_PRIMARY_PATH_PREFIXES = ['/cursor/transcript/', '/messages', '/v1/messages', '/v1/responses', '/backend-api/codex/responses', '/chat/completions', '/v1/chat/completions', '/v2/chat/completions', '/completions', '/v1/completions', '/v1internal:generateContent', '/v1internal:streamGenerateContent'];

function displayTurnPath(entry) {
  return (entry?.request?.path || '/unknown').replace(/\?.*$/, '');
}

function isDisplayTurnCandidate(entry) {
  if (!entry || typeof entry !== 'object') return false;
  if (entry.transport === 'cursor-transcript') return true;
  const path = displayTurnPath(entry);
  if (path.includes('/count_tokens') || path.endsWith('/models') || path.includes('/models?')) return false;
  if (isResponsesPath(path) && !isDisplayableResponsesEntry(entry)) return false;
  if (entry.derived_from_websocket) return true;
  if (isResponsesPath(path)) {
    return true;
  }
  if (path.startsWith('/model/') && (path.endsWith('/invoke') || path.endsWith('/invoke-with-response-stream'))) {
    return true;
  }
  return DISPLAY_TURN_PRIMARY_PATH_PREFIXES.some(prefix => path.startsWith(prefix));
}

function displayTurnRequestBody(entry) {
  let body = entry?.request?.body;
  if (typeof body !== 'string') return body;
  try { return JSON.parse(body); } catch (_) { return body; }
}

function displayTurnText(value) {
  if (typeof value === 'string') {
    let text = value.replace(/\s+/g, ' ').trim();
    const session = text.match(/^<session>\s*(.*?)\s*<\/session>$/i);
    if (session) text = session[1].trim();
    if (/^\[SUGGESTION MODE:/i.test(text)) return '';
    return text;
  }
  if (Array.isArray(value)) return value.map(displayTurnText).filter(Boolean).join('\n').trim();
  if (!value || typeof value !== 'object') return '';
  if (value.type === 'tool_result' || value.type === 'function_call_output') return '';
  if (typeof value.text === 'string') return displayTurnText(value.text);
  if (typeof value.input_text === 'string') return displayTurnText(value.input_text);
  if (typeof value.output_text === 'string') return displayTurnText(value.output_text);
  if (value.content !== undefined) return displayTurnText(value.content);
  if (value.input !== undefined) return displayTurnText(value.input);
  return '';
}

function displayTurnFingerprint(value) {
  let hash = 2166136261;
  const text = typeof value === 'string' ? value : (() => {
    try { return JSON.stringify(value); } catch (_) { return String(value); }
  })();
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function displayTurnHasContinuation(value) {
  if (Array.isArray(value)) return value.some(displayTurnHasContinuation);
  if (!value || typeof value !== 'object') return false;
  const role = String(value.role || value.author?.role || '').toLowerCase();
  if (role === 'tool' || role === 'function') return true;
  if (value.tool_call_id || (Array.isArray(value.tool_calls) && value.tool_calls.length)) return true;
  if (['tool_result', 'function_call_output', 'tool_use', 'function_call'].includes(value.type)) return true;
  return Object.values(value).some(displayTurnHasContinuation);
}

function displayTurnCompactItems(items) {
  if (!Array.isArray(items) || !items.length) return null;
  const refs = items.map(item => item?.__claude_tap_blob_ref__);
  if (!refs.every(ref => ref && typeof ref.hash === 'string')) return null;
  return {
    key: refs.map(ref => `${ref.hash}:${ref.bytes || ''}`).join('|'),
    count: refs.length,
  };
}

function isDisplayTurnTitleGeneration(entry) {
  const body = displayTurnRequestBody(entry);
  return /generate a concise.*title/i.test(String(body?.system || ''));
}

function displayTurnUserInfo(entry) {
  const body = displayTurnRequestBody(entry);
  const metadataKey = String(body?._display_turn_key || '').trim();
  if (metadataKey) {
    const ordinal = Number(body?._display_turn_ordinal) || 1;
    return {
      key: `metadata:${metadataKey}`,
      userText: '',
      userOrdinal: ordinal,
      cursorTurn: Number.isFinite(Number(body?.cursor_turn)) ? Number(body.cursor_turn) : null,
      hasContinuation: true,
    };
  }
  const messages = [];
  const appendMessages = (items) => {
    if (!Array.isArray(items)) return;
    items.forEach(item => {
      if (!item || typeof item !== 'object') return;
      const role = String(item.role || item.author?.role || '').toLowerCase();
      if (role !== 'user' && role !== 'human') return;
      const content = item.content ?? item.parts ?? item.text ?? item.input;
      const text = displayTurnText(content);
      if (text) messages.push({ text, raw: content });
    });
  };
  if (body && typeof body === 'object') {
    appendMessages(body.messages);
    appendMessages(body.input);
    appendMessages(body.contents);
    appendMessages(body.prompt ? [{ role: 'user', content: body.prompt }] : null);
  }
  if (!messages.length && body && typeof body === 'object') {
    const compact = displayTurnCompactItems(body.messages) || displayTurnCompactItems(body.input) || displayTurnCompactItems(body.contents);
    if (compact) {
      return {
        key: `compact:${compact.key}`,
        userText: '',
        userOrdinal: compact.count,
        cursorTurn: Number.isFinite(Number(body.cursor_turn)) ? Number(body.cursor_turn) : null,
        hasContinuation: true,
      };
    }
  }
  const stubText = String(entry?._session_user_text || '').replace(/\s+/g, ' ').trim();
  if (!messages.length && stubText) messages.push({ text: stubText, raw: stubText });
  if (!messages.length && typeof body === 'string') {
    const text = displayTurnText(body);
    if (text) messages.push({ text, raw: body });
  }
  if (!messages.length) return { key: '', userText: '', userOrdinal: 0, cursorTurn: null, hasContinuation: false };

  const last = messages[messages.length - 1];
  const cursorTurn = Number(body?.cursor_turn);
  const hasContinuation = !!(
    entry?.previous_response_id ||
    body?.previous_response_id ||
    messages.length > 1 ||
    displayTurnHasContinuation(body?.messages) ||
    displayTurnHasContinuation(body?.input) ||
    isDisplayTurnTitleGeneration(entry) ||
    (Array.isArray(body?.input) && body.input.some(item => item?.type === 'function_call_output' || item?.type === 'tool_result'))
  );
  return {
    key: `${messages.length}:${displayTurnFingerprint(last.text)}`,
    userText: last.text,
    userOrdinal: messages.length,
    cursorTurn: Number.isFinite(cursorTurn) ? cursorTurn : null,
    hasContinuation,
  };
}

function displayTurnIsNewPrimary(info, previous) {
  if (!previous) return true;
  if (info.cursorTurn !== null && previous.cursorTurn !== null && info.cursorTurn !== previous.cursorTurn) return true;
  if (info.key && previous.key && info.key !== previous.key) return true;
  if (info.key && previous.key) {
    // A new prompt normally changes either the text fingerprint or the
    // number of user messages in the submitted conversation. Identical
    // latest-only prompts without continuation metadata are separate turns.
    if (info.userOrdinal !== previous.userOrdinal) return true;
    if (!info.hasContinuation && !previous.hasContinuation) return true;
    return false;
  }
  const currentRoot = String(info.captureTurn ?? '');
  const previousRoot = String(previous.captureTurn ?? '');
  return !!currentRoot && !!previousRoot && currentRoot !== previousRoot;
}

function normalizeDisplayTurns(rawEntries, reset = true) {
  if (reset) {
    displayTurnPrimary = 0;
    displayTurnSub = 0;
    previousDisplayTurnInfo = null;
  }
  return (rawEntries || []).map((entry, idx) => {
    if (!entry || typeof entry !== 'object') return entry;
    if (entry._entry_index === undefined) entry._entry_index = idx;
    if (entry.capture_turn === undefined && entry.turn !== undefined) entry.capture_turn = entry.turn;
    if (!isDisplayTurnCandidate(entry)) {
      if (reset) delete entry.display_turn;
      return entry;
    }
    const userInfo = displayTurnUserInfo(entry);
    const info = { ...userInfo, captureTurn: entry.capture_turn ?? entry.turn };
    const titleGeneration = isDisplayTurnTitleGeneration(entry);
    if (titleGeneration) {
      // Title generation is a metadata request for the surrounding session,
      // not a new user turn. Keep it inside the current primary turn even
      // when its compacted message reference differs.
      if (displayTurnPrimary === 0) {
        displayTurnPrimary = 1;
        displayTurnSub = 1;
      } else {
        displayTurnSub += 1;
      }
    } else if (displayTurnPrimary > 0 && !previousDisplayTurnInfo) {
      // A title request can be the first captured record for a session. The
      // first real model request that follows it still belongs to turn 1.
      displayTurnSub += 1;
    } else if (displayTurnIsNewPrimary(info, previousDisplayTurnInfo)) {
      displayTurnPrimary += 1;
      displayTurnSub = 1;
    } else {
      displayTurnSub += 1;
    }
    entry.display_turn = `${displayTurnPrimary}.${displayTurnSub}`;
    if (!titleGeneration) previousDisplayTurnInfo = info;
    return entry;
  });
}

function displayTurnValue(entry) {
  return entry?.display_turn ?? entry?.turn;
}

function displayTurnLabel(entry) {
  const value = displayTurnValue(entry);
  return value === undefined || value === null || value === '' ? '?' : value;
}

function isNavigableTraceEntry(entry) {
  if (isResponsesPath(displayTurnPath(entry)) && !isDisplayableResponsesEntry(entry)) return false;
  return true;
}

function captureTurnValue(entry) {
  return entry?.capture_turn ?? entry?.turn;
}
