
/* ─── Renderers ─── */
function chatMessageContentToText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map(part => {
      if (typeof part === 'string') return part;
      if (!part || typeof part !== 'object') return '';
      if (part.type === 'text' || part.type === 'input_text' || part.type === 'output_text') return part.text || '';
      if (typeof part.text === 'string') return part.text;
      return JSON.stringify(part);
    }).filter(Boolean).join('\n');
  }
  if (content === undefined || content === null) return '';
  return JSON.stringify(content);
}

function normalizeDisplayContentBlocks(content) {
  if (typeof content === 'string') return content.trim() ? [{ type: 'text', text: content }] : [];
  if (content === undefined || content === null) return [];
  if (!Array.isArray(content)) return [{ type: 'raw', value: content }];
  return content.map(block => {
    if (typeof block === 'string') return { type: 'text', text: block };
    if (!block || typeof block !== 'object') return { type: 'raw', value: block };
    return block;
  }).filter(block => hasDisplayContent([block]));
}

function isChatInstructionRole(role) {
  return role === 'system' || role === 'developer';
}

function chatInstructionMessages(body) {
  if (!Array.isArray(body?.messages)) return [];
  return body.messages.filter(m => m && isChatInstructionRole(m.role));
}

function parseToolCallArguments(args) {
  if (args === undefined || args === null || args === '') return {};
  if (typeof args !== 'string') return args;
  try { return JSON.parse(args); } catch(e) { return args; }
}

function chatToolCallToContentBlock(call) {
  const fn = call?.function || {};
  return {
    type: 'tool_use',
    id: call?.id || '',
    name: fn.name || call?.name || 'tool_use',
    input: parseToolCallArguments(fn.arguments),
  };
}

function normalizeChatMessageForDisplay(msg) {
  if (!msg || typeof msg !== 'object') return { role: 'unknown', content: '' };
  const role = msg.role || 'unknown';
  if (role === 'tool') {
    return {
      ...msg,
      content: [{ type: 'tool_result', tool_use_id: msg.tool_call_id || '', content: msg.content || '' }],
    };
  }
  const content = [];
  if (Array.isArray(msg.content)) content.push(...msg.content);
  else if (typeof msg.content === 'string') {
    if (msg.content.trim()) content.push({ type: 'text', text: msg.content });
  } else if (msg.content !== undefined && msg.content !== null) {
    content.push(msg.content);
  }
  if (Array.isArray(msg.tool_calls)) {
    content.push(...msg.tool_calls.map(chatToolCallToContentBlock));
  }
  return { ...msg, content };
}

function looksLikeGeminiRequest(value) {
  return !!(value && typeof value === 'object' && (
    Array.isArray(value.contents)
    || !!value.systemInstruction
  ));
}

function geminiRequest(body) {
  if (!body || typeof body !== 'object') return {};
  if (looksLikeGeminiRequest(body.request)) return body.request;
  return looksLikeGeminiRequest(body) ? body : {};
}

function isGeminiRequestBody(body) {
  return looksLikeGeminiRequest(geminiRequest(body));
}

function geminiTextFromParts(parts) {
  if (!Array.isArray(parts)) return '';
  return parts
    .filter(part => part && typeof part === 'object' && typeof part.text === 'string')
    .map(part => part.text)
    .join('\n');
}

function geminiSystemInstruction(body) {
  const instruction = geminiRequest(body).systemInstruction;
  if (!instruction || typeof instruction !== 'object') return '';
  return geminiTextFromParts(instruction.parts).trim();
}

function geminiRole(role) {
  return role === 'model' ? 'assistant' : (typeof role === 'string' && role ? role : 'user');
}

function geminiFunctionResponseContent(response) {
  const payload = response?.response;
  const output = payload && typeof payload === 'object' && Object.prototype.hasOwnProperty.call(payload, 'output')
    ? payload.output
    : payload;
  if (typeof output === 'string') return output;
  if (output === undefined || output === null) return '';
  return JSON.stringify(output, null, 2);
}

function geminiPartContentBlocks(part) {
  const blocks = [];
  if (!part || typeof part !== 'object') return blocks;
  if (typeof part.text === 'string' && part.text.trim()) {
    if (part.thought === true) blocks.push({ type: 'thinking', thinking: part.text });
    else blocks.push({ type: 'text', text: part.text });
  }
  if (part.functionCall && typeof part.functionCall === 'object') {
    const call = part.functionCall;
    blocks.push({
      type: 'tool_use',
      id: call.id || '',
      name: call.name || 'tool_use',
      input: call.args && typeof call.args === 'object' ? call.args : {},
    });
  }
  if (part.functionResponse && typeof part.functionResponse === 'object') {
    const response = part.functionResponse;
    blocks.push({
      type: 'tool_result',
      tool_use_id: response.id || response.name || '',
      content: geminiFunctionResponseContent(response),
    });
  }
  return blocks;
}

function geminiMessages(body) {
  const contents = geminiRequest(body).contents;
  if (!Array.isArray(contents)) return [];
  return contents.map(item => {
    if (!item || typeof item !== 'object') return null;
    const blocks = (item.parts || []).flatMap(geminiPartContentBlocks);
    if (!blocks.length) return null;
    let role = geminiRole(item.role);
    if (blocks.every(block => block.type === 'tool_result')) role = 'tool';
    return { role, content: blocks };
  }).filter(Boolean);
}

function flattenGeminiTools(tools) {
  if (!Array.isArray(tools)) return [];
  const flattened = [];
  for (const group of tools) {
    if (!group || typeof group !== 'object') continue;
    const declarations = group.functionDeclarations;
    if (!Array.isArray(declarations)) continue;
    for (const decl of declarations) {
      if (!decl || typeof decl !== 'object') continue;
      flattened.push({
        name: decl.name || '',
        description: decl.description || '',
        input_schema: decl.parametersJsonSchema || decl.parameters || {},
      });
    }
  }
  return flattened;
}

function getRequestTools(body) {
  const direct = Array.isArray(body?.tools) ? body.tools : [];
  const geminiTools = flattenGeminiTools(geminiRequest(body).tools);
  return geminiTools.length ? geminiTools : direct;
}

function hasDisplayContent(content) {
  if (typeof content === 'string') return content.trim().length > 0;
  if (!Array.isArray(content)) return content !== undefined && content !== null;
  return content.some(block => {
    if (!block || typeof block !== 'object') return false;
    if (block.type === 'text' || block.type === 'input_text' || block.type === 'output_text') return !!(block.text || '').trim();
    if (block.type === 'image' || block.type === 'input_image') {
      const source = block.source || {};
      return !!(block.image_url || block.file_id || source.data || source.url || source.media_type || source.file_id);
    }
    if (block.type === 'thinking') return !!(block.thinking || '').trim();
    if (block.type === 'tool_use') return true;
    if (block.type === 'tool_result') {
      const rc = block.content;
      if (typeof rc === 'string') return rc.trim().length > 0 || !!block.tool_use_id;
      if (Array.isArray(rc)) return rc.length > 0;
      return rc !== undefined && rc !== null;
    }
    return JSON.stringify(block) !== '{}';
  });
}

function extractSystem(body) {
  if (!body) return null;
  const parts = [];
  if (typeof body.system === 'string' && body.system.trim()) parts.push(body.system);
  if (Array.isArray(body.system)) {
    const systemText = body.system.map(b => typeof b === 'string' ? b : b.type === 'text' ? (b.text || '') : JSON.stringify(b)).filter(Boolean).join('\n\n');
    if (systemText.trim()) parts.push(systemText);
  }
  // Responses API: instructions field holds the system prompt
  if (typeof body.instructions === 'string' && body.instructions.trim()) parts.push(body.instructions);
  const chatInstructions = chatInstructionMessages(body)
    .map(m => chatMessageContentToText(m.content))
    .filter(text => text.trim());
  parts.push(...chatInstructions);
  const geminiSystem = geminiSystemInstruction(body);
  if (geminiSystem) parts.push(geminiSystem);
  return parts.length ? parts.join('\n\n') : null;
}

function extractSystemBlocks(body) {
  if (!body) return [];
  const blocks = [];
  if (typeof body.system === 'string' && body.system.trim()) {
    blocks.push(...normalizeDisplayContentBlocks(body.system));
  }
  if (Array.isArray(body.system)) {
    blocks.push(...normalizeDisplayContentBlocks(body.system));
  }
  if (typeof body.instructions === 'string' && body.instructions.trim()) {
    blocks.push(...normalizeDisplayContentBlocks(body.instructions));
  }
  for (const message of chatInstructionMessages(body)) {
    blocks.push(...normalizeDisplayContentBlocks(message.content));
  }
  const geminiSystem = geminiSystemInstruction(body);
  if (geminiSystem) {
    blocks.push(...normalizeDisplayContentBlocks(geminiSystem));
  }
  return blocks;
}

function parseSseDataFrames(text) {
  if (typeof text !== 'string' || !text.includes('data:')) return [];
  const events = [];
  let dataLines = [];
  const flush = () => {
    if (!dataLines.length) return;
    const raw = dataLines.join('\n');
    dataLines = [];
    if (raw === '[DONE]') return;
    let data = raw;
    try { data = JSON.parse(raw); } catch(e) {}
    events.push({ event: 'message', data });
  };
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
      continue;
    }
    if (!line.trim()) flush();
  }
  flush();
  return events;
}

function getResponseEvents(entry) {
  const sse = entry?.response?.sse_events;
  if (Array.isArray(sse) && sse.length > 0) return sse;
  const ws = entry?.response?.ws_events;
  if (Array.isArray(ws) && ws.length > 0) return ws;
  return parseSseDataFrames(entry?.response?.body);
}

function getEventType(event) {
  if (!event || typeof event !== 'object') return '';
  return typeof event.event === 'string' ? event.event : (typeof event.type === 'string' ? event.type : '');
}

function getEventData(event) {
  if (!event || typeof event !== 'object') return null;
  let data = Object.prototype.hasOwnProperty.call(event, 'data') ? event.data : event;
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch(e) { return null; }
  }
  return data && typeof data === 'object' ? data : null;
}

function getResponsePayload(entry) {
  const body = entry?.response?.body;
  if (body && typeof body === 'object') return body;
  const response = entry?.response;
  if (!response || typeof response !== 'object') return null;
  if (response.output || response.content || response.usage || response.previous_response_id || response.id || response.error) return response;
  return null;
}

function getResponsesContinuationInfo(entry) {
  if (entry?.derived_from_websocket) return null;
  const body = entry?.request?.body;
  if (!body || typeof body !== 'object') return null;
  const path = entry?.request?.path || '';
  const looksLikeResponses = path.endsWith('/v1/responses') || path === '/responses' || body.type === 'response.create' || Array.isArray(body.input);
  if (!looksLikeResponses) return null;
  const payload = getResponsePayload(entry) || {};
  const previousId = payload.previous_response_id || body.previous_response_id;
  if (!previousId) return null;
  const userMessages = getMessages(body).filter(m => m.role === 'user');
  if (userMessages.length > 0) return null;
  const headers = entry?.request?.headers || {};
  return {
    response_id: payload.id || '',
    previous_response_id: previousId,
    prompt_cache_key: body.prompt_cache_key || '',
    session_id: headers.session_id || headers['session-id'] || '',
    codex_version: headers.version || headers['x-codex-version'] || '',
  };
}

// Normalize messages from Chat Completions (body.messages) or Responses API (body.input)
function getMessages(body) {
  if (!body) return [];
  if (Array.isArray(body.messages) && body.messages.length > 0) {
    return body.messages
      .filter(m => !isChatInstructionRole(m?.role))
      .map(normalizeChatMessageForDisplay)
      .filter(m => hasDisplayContent(m.content));
  }
  if (isGeminiRequestBody(body)) {
    return geminiMessages(body).filter(m => hasDisplayContent(m.content));
  }
  if (Array.isArray(body.input)) {
    const normalizedInput = normalizeWebSocketDerivedInput(body.input);
    const messages = normalizedInput.filter(item => {
      if (!item || typeof item !== 'object') return false;
      if (typeof item.role !== 'string' || !item.role) return false;
      return item.type === undefined || item.type === 'message';
    }).map(item => ({
      role: item.role || 'user',
      content: Array.isArray(item.content)
        ? item.content.map(c => {
            if (!c || typeof c !== 'object') return c;
            if (c.type === 'input_text' || c.type === 'output_text' || c.type === 'text') return { type: c.type, text: c.text };
            if (c.type === 'tool_use') return c;
            if (c.type === 'tool_result') return c;
            return c;
          })
        : item.content
    }));
    if (shouldPrependResponsesInstructions(body, messages)) {
      return [{ role: 'developer', content: [{ type: 'text', text: body.instructions }] }, ...messages];
    }
    return messages;
  }
  return [];
}

function shouldPrependResponsesInstructions(body, messages) {
  if (!Array.isArray(body?.input) || body.input.length === 0) return false;
  if (!Array.isArray(messages) || messages.length === 0) return false;
  if (messages.some(m => m.role === 'developer' || m.role === 'system')) return false;
  if (!messages.some(m => m.role === 'user')) return false;
  return typeof body.instructions === 'string' && body.instructions.trim();
}

function shouldRenderRequestContext(entry, body, msgs, respOutput) {
  if (!entry || !body || !Array.isArray(msgs) || msgs.length === 0) return false;
  const method = entry?.request?.method || '';
  if (!entry.derived_from_websocket && entry.transport !== 'websocket' && method !== 'WEBSOCKET') return false;
  const path = getPath(entry);
  if (!path.includes('/codex/responses') && !path.includes('/v1/responses')) return false;
  if (!Array.isArray(body.input) || body.input.length === 0) return false;
  return true;
}

function normalizeUsage(usage) {
  if (!usage || typeof usage !== 'object') return null;
  const normalized = { ...usage };
  if ((normalized.input_tokens === undefined || normalized.input_tokens === null || normalized.input_tokens === 0) && usage.prompt_tokens) {
    normalized.input_tokens = usage.prompt_tokens;
  }
  if ((normalized.input_tokens === undefined || normalized.input_tokens === null || normalized.input_tokens === 0) && usage.promptTokenCount) {
    normalized.input_tokens = usage.promptTokenCount;
  }
  if ((normalized.input_tokens === undefined || normalized.input_tokens === null || normalized.input_tokens === 0) && usage.inputTokens) {
    normalized.input_tokens = usage.inputTokens;
  }
  if ((normalized.output_tokens === undefined || normalized.output_tokens === null || normalized.output_tokens === 0) && usage.completion_tokens) {
    normalized.output_tokens = usage.completion_tokens;
  }
  if ((normalized.output_tokens === undefined || normalized.output_tokens === null || normalized.output_tokens === 0) && usage.candidatesTokenCount) {
    normalized.output_tokens = usage.candidatesTokenCount;
  }
  if ((normalized.output_tokens === undefined || normalized.output_tokens === null || normalized.output_tokens === 0) && usage.outputTokens) {
    normalized.output_tokens = usage.outputTokens;
  }
  if ((normalized.total_tokens === undefined || normalized.total_tokens === null || normalized.total_tokens === 0) && usage.totalTokens) {
    normalized.total_tokens = usage.totalTokens;
  }
  if (normalized.cache_read_input_tokens === undefined) {
    /* Cache tokens derived from OpenAI/Gemini-style details fields are already
       counted inside input_tokens/prompt_tokens.  Mark them so the cache hit
       rate denominator can avoid double-counting. */
    let embeddedCached = usage.cached_tokens;
    if (embeddedCached === undefined) embeddedCached = usage.cachedContentTokenCount;
    if (embeddedCached === undefined && usage.input_tokens_details && typeof usage.input_tokens_details === 'object') {
      embeddedCached = usage.input_tokens_details.cached_tokens;
    }
    if (embeddedCached === undefined && usage.prompt_tokens_details && typeof usage.prompt_tokens_details === 'object') {
      embeddedCached = usage.prompt_tokens_details.cached_tokens;
    }
    if (embeddedCached !== undefined && embeddedCached !== null) {
      normalized.cache_read_input_tokens = embeddedCached;
      normalized._cache_read_in_input = true;
    } else if (usage.cacheReadInputTokens !== undefined && usage.cacheReadInputTokens !== null) {
      normalized.cache_read_input_tokens = usage.cacheReadInputTokens;
      normalized._cache_read_in_input = false;
    }
  } else {
    /* Native cache_read_input_tokens (Claude/Anthropic/Bedrock) is a separate
       bucket not included in input_tokens.  But if the caller already set
       _cache_read_in_input (e.g. lazy-loading stub with model-based inference),
       respect the pre-set value. */
    if (normalized._cache_read_in_input === undefined) {
      normalized._cache_read_in_input = false;
    }
  }
  if (normalized.cache_creation_input_tokens === undefined && usage.cacheWriteInputTokens !== undefined && usage.cacheWriteInputTokens !== null) {
    normalized.cache_creation_input_tokens = usage.cacheWriteInputTokens;
  }
  return normalized;
}

// Extract token usage from response.body.usage or SSE response.completed event
function getUsage(entry) {
  const u = getResponsePayload(entry)?.usage;
  if (u) return normalizeUsage(u);
  const geminiUsage = geminiUsageFromPayloads(entry);
  if (geminiUsage) return geminiUsage;
  const events = getResponseEvents(entry);
  for (let i = events.length - 1; i >= 0; i--) {
    if (getEventType(events[i]) !== 'response.completed') continue;
    const data = getEventData(events[i]);
    if (data?.response?.usage) return normalizeUsage(data.response.usage);
  }
  return null;
}

function normalizeResponseOutput(output) {
  if (!Array.isArray(output) || output.length === 0) return null;
  const content = [];
  for (const item of output) {
    if (!item || typeof item !== 'object') continue;
    if (item.type === 'message' && Array.isArray(item.content)) {
      for (const c of item.content) {
        if (c?.type === 'output_text') content.push({ type: 'text', text: c.text });
        else content.push(c);
      }
    } else if (isResponseCallItem(item)) {
      content.push({ type: 'tool_use', id: item.call_id || item.id || '', name: responseCallToolName(item), input: responseCallInput(item) });
    } else if (item.type === 'reasoning') {
      if (!item.summary) continue;
      const summaryText = Array.isArray(item.summary) ? item.summary.map(s => s?.text || '').join('\n') : (item.summary.text || JSON.stringify(item.summary));
      if (summaryText.trim()) content.push({ type: 'thinking', thinking: summaryText });
    }
  }
  return content.length > 0 ? { content } : null;
}

function normalizeBedrockConverseContent(blocks) {
  if (!Array.isArray(blocks) || blocks.length === 0) return [];
  const content = [];
  for (const block of blocks) {
    if (!block || typeof block !== 'object') continue;
    if (typeof block.text === 'string') {
      appendMergeableResponseBlock(content, { type: 'text', text: block.text });
      continue;
    }
    const reasoning = block.reasoningContent;
    if (reasoning && typeof reasoning === 'object') {
      const reasoningText = reasoning.text || reasoning.reasoningText?.text || '';
      const signature = reasoning.signature || reasoning.reasoningText?.signature || '';
      const thinking = { type: 'thinking', thinking: reasoningText };
      if (signature) thinking.signature = signature;
      if (reasoningText.trim() || signature) content.push(thinking);
      continue;
    }
    const toolUse = block.toolUse;
    if (toolUse && typeof toolUse === 'object') {
      content.push({
        type: 'tool_use',
        id: toolUse.toolUseId || '',
        name: toolUse.name || 'tool_use',
        input: toolUse.input && typeof toolUse.input === 'object' ? toolUse.input : {},
      });
      continue;
    }
    if (block.type) content.push(block);
  }
  return content;
}

function normalizeBedrockConverseOutput(body) {
  const message = body?.output?.message;
  if (!message || typeof message !== 'object') return null;
  const content = normalizeBedrockConverseContent(message.content);
  return content.length > 0 ? { content } : null;
}

function normalizeChatCompletionsChoiceOutput(body) {
  if (!Array.isArray(body?.choices) || body.choices.length === 0) return null;
  const content = [];
  for (const choice of body.choices) {
    const message = choice?.message || choice?.delta;
    if (!message || typeof message !== 'object') continue;
    if (typeof message.reasoning_content === 'string' && message.reasoning_content.trim()) {
      appendMergeableResponseBlock(content, { type: 'thinking', thinking: message.reasoning_content });
    }
    if (typeof message.thinking === 'string' && message.thinking.trim()) {
      appendMergeableResponseBlock(content, { type: 'thinking', thinking: message.thinking });
    }
    const normalized = normalizeChatMessageForDisplay({ role: 'assistant', ...message });
    for (const block of normalizeDisplayContentBlocks(normalized.content)) {
      appendMergeableResponseBlock(content, block);
    }
  }
  return content.length > 0 ? { content } : null;
}

function appendMergeableResponseBlock(content, block) {
  if (!block || typeof block !== 'object') return;
  const prev = content.length ? content[content.length - 1] : null;
  if (prev && prev.type === block.type) {
    if (block.type === 'thinking') {
      prev.thinking = (prev.thinking || '') + (block.thinking || '');
      return;
    }
    if (block.type === 'text' || block.type === 'input_text' || block.type === 'output_text') {
      prev.text = (prev.text || '') + (block.text || '');
      return;
    }
  }
  content.push(block);
}

function reconstructOutputFromEvents(events) {
  if (!Array.isArray(events) || events.length === 0) return null;
  const outputItems = [];
  for (const ev of events) {
    if (getEventType(ev) !== 'response.output_item.done') continue;
    const data = getEventData(ev);
    const item = data?.item;
    const outputIndex = data?.output_index;
    if (!item || typeof item !== 'object' || !Number.isInteger(outputIndex)) continue;
    outputItems.push({ outputIndex, item });
  }
  if (outputItems.length === 0) return null;
  return normalizeResponseOutput(
    outputItems
      .sort((a, b) => a.outputIndex - b.outputIndex)
      .map(({ item }) => item)
  );
}

function geminiResponsePayloads(entry) {
  const body = entry?.response?.body;
  if (typeof body === 'string') {
    return parseSseDataFrames(body)
      .map(event => event.data)
      .filter(data => data && typeof data === 'object');
  }
  if (body && typeof body === 'object') return [body];
  return [];
}

function geminiUsageFromPayloads(entry) {
  if (!isGeminiRequestBody(entry?.request?.body)) return null;
  let usage = null;
  for (const payload of geminiResponsePayloads(entry)) {
    const response = payload?.response && typeof payload.response === 'object' ? payload.response : payload;
    if (response?.usageMetadata) usage = response.usageMetadata;
  }
  return usage ? normalizeUsage(usage) : null;
}

function geminiResponseOutput(entry) {
  if (!isGeminiRequestBody(entry?.request?.body)) return null;
  const content = [];
  for (const payload of geminiResponsePayloads(entry)) {
    const response = payload?.response && typeof payload.response === 'object' ? payload.response : payload;
    const candidates = response?.candidates;
    if (!Array.isArray(candidates)) continue;
    for (const candidate of candidates) {
      const parts = candidate?.content?.parts;
      if (!Array.isArray(parts)) continue;
      for (const part of parts) {
        if (!part || typeof part !== 'object') continue;
        if (typeof part.text === 'string') {
          if (part.thought === true && part.text.trim()) {
            appendMergeableResponseBlock(content, { type: 'thinking', thinking: part.text });
          } else if (part.text.trim()) {
            appendMergeableResponseBlock(content, { type: 'text', text: part.text });
          }
        }
        if (part.functionCall && typeof part.functionCall === 'object') {
          const call = part.functionCall;
          content.push({
            type: 'tool_use',
            id: call.id || '',
            name: call.name || 'tool_use',
            input: call.args && typeof call.args === 'object' ? call.args : {},
          });
        }
      }
    }
  }
  return content.length ? { content } : null;
}

// Extract response output from response.body.content or SSE response.completed event
function getResponseOutput(entry) {
  const geminiOutput = geminiResponseOutput(entry);
  if (geminiOutput) return geminiOutput;
  const body = getResponsePayload(entry);
  if (body?.content) return body;
  const fromBedrockConverse = normalizeBedrockConverseOutput(body);
  if (fromBedrockConverse) return fromBedrockConverse;
  const fromChoices = normalizeChatCompletionsChoiceOutput(body);
  if (fromChoices) return fromChoices;
  const fromBody = normalizeResponseOutput(body?.output);
  if (fromBody) return fromBody;
  const events = getResponseEvents(entry);
  const fromItems = reconstructOutputFromEvents(events);
  if (fromItems) return fromItems;
  for (let i = events.length - 1; i >= 0; i--) {
    if (getEventType(events[i]) !== 'response.completed') continue;
    const data = getEventData(events[i]);
    const normalized = normalizeResponseOutput(data?.response?.output);
    if (normalized) return normalized;
  }
  return null;
}
function renderSystemPrompt(blocks, copyText = '') {
  const normalized = normalizeDisplayContentBlocks(blocks);
  if (normalized.length <= 1 && copyText) return `<div class="pre-text">${esc(copyText)}</div>`;
  return `<div class="system-prompt-blocks">${renderContent(normalized, 'system', { frameBlocks: normalized.length > 1 })}</div>`;
}

function renderResponsesContinuationNotice(info) {
  const rows = [
    ['previous_response_id', info.previous_response_id],
    ['response_id', info.response_id],
    ['prompt_cache_key', info.prompt_cache_key],
    ['session_id', info.session_id],
    ['codex_version', info.codex_version],
  ].filter(([, value]) => value);
  const meta = rows.length
    ? `<div class="cb-meta">${rows.map(([key, value]) => `<div class="cb-key">${esc(key)}</div><div class="cb-val">${esc(value)}</div>`).join('')}</div>`
    : '';
  return `<div class="continuation-banner"><div class="cb-icon">&#9888;</div><div class="cb-content"><div class="cb-title">${esc(t('continuation_title'))}</div><div class="cb-message">${esc(t('continuation_message'))}</div>${meta}</div></div>`;
}

function renderMessages(msgs) {
  return msgs.map(m => {
    const role = m.role || 'unknown';
    const cls = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : role === 'tool' ? 'tool_result' : (role === 'developer' || role === 'system') ? 'system' : 'system';
    const blockCount = normalizeDisplayContentBlocks(m.content).length;
    const rendered = renderContent(m.content, role, { frameBlocks: blockCount > 1 });
    if (!rendered.trim()) return '';
    return `<div class="msg ${cls}"><div class="msg-role">${esc(role)}</div>${rendered}</div>`;
  }).filter(Boolean).join('');
}

function contentHasImagePlaceholder(content) {
  if (typeof content === 'string') return /\[Image #\d+\]/i.test(content);
  if (!Array.isArray(content)) return content && typeof content === 'object' && contentHasImagePlaceholder(content.content || content.text || content.output || '');
  return content.some(block => {
    if (typeof block === 'string') return contentHasImagePlaceholder(block);
    if (!block || typeof block !== 'object') return false;
    return contentHasImagePlaceholder(block.text || block.output || block.content || '');
  });
}

function wrapContentBlock(inner, block, index, total, options = {}) {
  const frameBlock = !!options.frameBlocks && total > 1;
  const classes = ['content-block'];
  if (frameBlock) classes.push('block-framed');
  if (options.extraClass) classes.push(...String(options.extraClass).split(/\s+/).filter(Boolean));
  return `<div class="${classes.join(' ')}">${inner}</div>`;
}

function isInlineImageUrl(url) {
  return typeof url === 'string' && /^data:image\//i.test(url);
}

function imageSourceFromBlock(block) {
  if (!block || typeof block !== 'object') return null;
  const source = block.source || {};
  if (
    (block.type === 'image' || block.type === 'input_image' || source.type === 'base64') &&
    source.type === 'base64' &&
    source.data
  ) {
    return { src: `data:${source.media_type || 'image/png'};base64,${source.data}`, alt: source.media_type || 'image' };
  }
  if (
    (block.type === 'image' || block.type === 'input_image' || source.type === 'url') &&
    source.type === 'url' &&
    isInlineImageUrl(source.url)
  ) {
    return { src: source.url, alt: source.media_type || 'image' };
  }
  const imageUrl = block.image_url;
  if (typeof imageUrl === 'string' && isInlineImageUrl(imageUrl)) return { src: imageUrl, alt: 'image' };
  if (imageUrl && typeof imageUrl === 'object' && isInlineImageUrl(imageUrl.url)) return { src: imageUrl.url, alt: 'image' };
  if (block.data && (block.media_type || block.mime_type)) {
    return { src: `data:${block.media_type || block.mime_type};base64,${block.data}`, alt: block.media_type || block.mime_type || 'image' };
  }
  return null;
}

function imageBlocksForContent(content) {
  const images = [];
  const visit = value => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (typeof value !== 'object') return;
    if (imageSourceFromBlock(value)) images.push(value);
    if (value.type === 'message' || value.type === 'tool_result' || value.type === 'function_call_output') visit(value.content);
  };
  visit(content);
  return images;
}

function imageSourceKey(block) {
  const source = imageSourceFromBlock(block);
  return source ? source.src.slice(0, 128) + ':' + source.src.length : '';
}

function buildSessionImageRegistry() {
  if (sessionImageRegistryCache && sessionImageRegistrySize === entries.length) return sessionImageRegistryCache;
  const registry = new Map();
  const sourceEntries = lazyMode
    ? expandWebSocketResponseEntries(getRawLines().map(line => { try { return JSON.parse(line); } catch { return null; } }).filter(Boolean))
    : entries.map(getFullEntry);
  for (const entry of sourceEntries) {
    const messages = getMessages(entry?.request?.body);
    for (const message of messages) {
      if (message?.role !== 'user') continue;
      const images = imageBlocksForContent(message.content);
      if (!images.length) continue;
      const key = imageLookupKey(naturalTextForSessionContent(message.content));
      if (!key) continue;
      const bucket = registry.get(key) || [];
      const seen = new Set(bucket.map(imageSourceKey));
      for (const image of images) {
        const imageKey = imageSourceKey(image);
        if (!imageKey || seen.has(imageKey)) continue;
        seen.add(imageKey);
        bucket.push(image);
      }
      registry.set(key, bucket);
    }
  }
  sessionImageRegistryCache = registry;
  sessionImageRegistrySize = entries.length;
  return registry;
}

function recoveredImagesForContent(content) {
  if (!contentHasImagePlaceholder(content) || imageBlocksForContent(content).length) return [];
  const key = imageLookupKey(naturalTextForSessionContent(content));
  if (!key) return [];
  return buildSessionImageRegistry().get(key) || [];
}

function renderImageElement(src, alt = 'image') {
  return `<img class="content-image message-image" src="${esc(src)}" alt="${esc(alt)}" loading="lazy" />`;
}

function renderImageElementForBlock(block) {
  const source = imageSourceFromBlock(block);
  if (!source) return '';
  return renderImageElement(source.src, source.alt);
}

function renderImageBlock(block, index = 0, total = 1, options = {}) {
  const inner = renderImageElementForBlock(block);
  return inner ? wrapContentBlock(inner, block, index, total, { ...options, extraClass: 'image-block' }) : '';
}

function renderContent(content, role, options = {}) {
  const blocks = normalizeDisplayContentBlocks(content);
  const renderedBlocks = blocks.map((block, index) => {
    if (block.type === 'text' || block.type === 'input_text' || block.type === 'output_text') {
      const txt = block.text || '';
      if (!txt.trim()) return '';
      return wrapContentBlock(`<div class="content-block-text">${esc(txt)}</div>`, block, index, blocks.length, options);
    }
    if (block.type === 'thinking') {
      const thinking = block.thinking || '';
      if (!thinking.trim()) return '';
      return wrapContentBlock(`<span class="thinking-label">thinking</span><div class="pre-text">${esc(thinking)}</div>`, block, index, blocks.length, options);
    }
    if (block.type === 'tool_use') {
      const label = block.id ? `${block.name || 'tool_use'} (${block.id})` : (block.name || 'tool_use');
      return wrapContentBlock(`<span class="tool-use-label">${esc(label)}</span>${renderToolInput(block.input)}`, block, index, blocks.length, options);
    }
    if (block.type === 'tool_result') {
      const rc = block.content;
      if (typeof rc === 'string') {
        return wrapContentBlock(`<span class="tool-use-label">result (${esc(block.tool_use_id || '')})</span><div class="pre-text">${esc(rc)}</div>`, block, index, blocks.length, options);
      }
      if (Array.isArray(rc)) {
        const parts = rc.map(c => {
          if (c.type === 'text') return `<div class="pre-text">${esc(c.text)}</div>`;
          if (c.type === 'image' || c.type === 'input_image') {
            const renderedImage = renderImageElementForBlock(c);
            if (renderedImage) return renderedImage;
          }
          return `<pre>${esc(JSON.stringify(c))}</pre>`;
        }).join('');
        return wrapContentBlock(`<span class="tool-use-label">result</span>${parts}`, block, index, blocks.length, options);
      }
      return wrapContentBlock(`<pre>${esc(JSON.stringify(block, null, 2))}</pre>`, block, index, blocks.length, options);
    }
    if (block.type === 'image' || block.type === 'input_image') {
      const renderedImage = renderImageBlock(block, index, blocks.length, options);
      if (renderedImage) return renderedImage;
      const source = block.source || {};
      const fileId = block.file_id || source.file_id;
      const hasUrl = block.image_url || source.url;
      const label = fileId ? `image: file_id ${fileId}` : hasUrl ? 'image: url' : `image: ${source.media_type || 'unknown'}`;
      return wrapContentBlock(`<span class="content-image-placeholder">${esc(label)}</span>`, block, index, blocks.length, options);
    }
    if (block.type === 'raw') return wrapContentBlock(`<pre>${esc(JSON.stringify(block.value, null, 2))}</pre>`, block, index, blocks.length, options);
    return wrapContentBlock(`<pre>${esc(JSON.stringify(block, null, 2))}</pre>`, block, index, blocks.length, options);
  }).join('');
  const recovered = role === 'user'
    ? recoveredImagesForContent(content).map((block, index, images) => renderImageBlock(block, index, images.length)).join('')
    : '';
  return renderedBlocks + recovered;
}

function valueHasReadableEscapes(value) {
  if (typeof value === 'string') {
    return value.includes('\n')
      || value.includes('\r')
      || value.includes('\t')
      || /\\(?:r\\n|n|r|t|"|u[0-9a-fA-F]{4})/.test(value);
  }
  if (Array.isArray(value)) return value.some(valueHasReadableEscapes);
  if (value && typeof value === 'object') return Object.values(value).some(valueHasReadableEscapes);
  return false;
}

function decodeEscapedTextForView(value) {
  if (typeof value !== 'string') return '';
  return value
    .replace(/\\r\\n/g, '\n')
    .replace(/\\n/g, '\n')
    .replace(/\\r/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\"/g, '"')
    .replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) => {
      try { return String.fromCharCode(parseInt(hex, 16)); }
      catch { return `\\u${hex}`; }
    })
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n');
}

function renderToolInput(input) {
  const raw = JSON.stringify(input, null, 2) || '';
  if (!valueHasReadableEscapes(input)) return `<pre>${esc(raw)}</pre>`;
  const decoded = decodeEscapedTextForView(raw);
  const copyIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
  return `<div class="tool-input-readable">`
    + `<span class="tool-input-actions">`
    + `<button type="button" class="tool-input-btn tool-input-copy" title="${esc(t('copy'))}" aria-label="${esc(t('copy'))}">${copyIcon}</button>`
    + `<button type="button" class="tool-input-btn tool-input-toggle" aria-expanded="false" title="${esc(t('string_expand_escapes'))}" aria-label="${esc(t('string_expand_escapes'))}">↵</button>`
    + `</span>`
    + `<pre class="tool-input-view" data-raw="${encodeCopyText(raw)}" data-decoded="${encodeCopyText(decoded)}">${esc(raw)}</pre>`
    + `</div>`;
}

function renderTools(tools) {
  return tools.map(td => {
    const name = toolDisplayName(td) || 'unknown', desc = toolDescription(td);
    const shortDesc = desc.split('\n')[0].substring(0, 120);
    const schema = toolSchema(td);
    const props = schema.properties || {};
    const required = new Set(schema.required || []);
    let paramsHtml = '';
    const keys = Object.keys(props);
    if (keys.length) {
      paramsHtml = `<div class="tb-params-title">${t('params')}</div>` + keys.map(k => {
        const p = props[k], type = p.type || (p.enum ? 'enum' : ''), pdesc = p.description || '';
        const req = required.has(k) ? `<span class="tb-prequired">${t('required')}</span>` : '';
        const typeTag = type ? `<span class="tb-ptype">${esc(type)}</span>` : '';
        const descLine = pdesc ? `<div class="tb-pdesc">${esc(pdesc)}</div>` : '';
        return `<div class="tb-param"><div class="tb-param-row1"><span class="tb-pname">${esc(k)}</span>${typeTag}${req}</div>${descLine}</div>`;
      }).join('');
    }
    return `<div class="tool-block"><div class="tool-block-header"><span class="tb-arrow">&#9654;</span><span class="tb-name">${esc(name)}</span><span class="tb-desc">${esc(shortDesc)}</span></div><div class="tool-block-body">${desc ? `<div class="tb-full-desc">${esc(desc)}</div>` : ''}${paramsHtml}</div></div>`;
  }).join('');
}

function renderResponseContent(body, contextOnly = false) {
  if (!body?.content) {
    const msg = contextOnly ? t('response_context_only') : t('no_content');
    return `<em style="color:var(--text-tertiary)">${msg}</em>`;
  }
  return renderContent(body.content, 'assistant');
}

function renderTokenUsage(u) {
  const items = [
    { label: t('tok_input'), val: u.input_tokens || 0, color: 'var(--blue)' },
    { label: t('tok_output'), val: u.output_tokens || 0, color: 'var(--green)' },
    { label: t('tok_cache_read'), val: u.cache_read_input_tokens || 0, color: 'var(--cyan)' },
    { label: t('tok_cache_create'), val: u.cache_creation_input_tokens || 0, color: 'var(--amber)' },
  ];
  return `<div class="token-bar">${items.map(i =>
    `<div class="tok-item"><span class="tok-dot" style="background:${i.color}"></span><span class="tok-label">${i.label}</span><span class="tok-val">${i.val.toLocaleString()}</span></div>`
  ).join('')}</div>`;
}

function renderSSEEvents(events) {
  return events.map(e => {
    const eventType = getEventType(e) || 'event';
    const payload = Object.prototype.hasOwnProperty.call(e, 'data') ? e.data : e;
    const data = typeof payload === 'string' ? payload : JSON.stringify(payload);
    const short = data.length > 200 ? data.substring(0, 200) + '...' : data;
    return `<div class="sse-event"><span class="sse-type">${esc(eventType)}</span><span class="sse-data">${esc(short)}</span></div>`;
  }).join('');
}
