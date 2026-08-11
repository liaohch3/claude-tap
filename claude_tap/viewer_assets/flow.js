/* ─── Per-turn flow graph ─── */
let activeFlowRecords = [];
let activeFlowGraph = null;
let activeFlowNodeMap = new Map();
let flowSelectedNodeId = '';

/* Hermes metadata is deliberately read from capture only.  The viewer must
 * not turn a plausible prompt or a tool name into a parent/child relation:
 * the capture writer is the source of truth for session lineage. */
const FLOW_HERMES_LINEAGE_KEYS = [
  'hermes_root_session_id',
  'hermes_leaf_session_id',
  'hermes_parent_session_id',
  'hermes_session_source',
  'hermes_session_resolution',
  'hermes_root_turn',
  'hermes_root_capture_turn',
];

function flowCapture(entry) {
  return entry?.capture && typeof entry.capture === 'object' ? entry.capture : {};
}

function flowCaptureText(entry, key) {
  const value = flowCapture(entry)?.[key];
  return value === undefined || value === null ? '' : String(value).trim();
}

function flowHermesLineage(entry) {
  const capture = flowCapture(entry);
  const rootSessionId = flowCaptureText(entry, 'hermes_root_session_id') || flowCaptureText(entry, 'hermes_session_id');
  const leafSessionId = flowCaptureText(entry, 'hermes_leaf_session_id');
  const parentSessionId = flowCaptureText(entry, 'hermes_parent_session_id');
  const source = flowCaptureText(entry, 'hermes_session_source').toLowerCase();
  const resolution = flowCaptureText(entry, 'hermes_session_resolution').toLowerCase();
  const hasMetadata = FLOW_HERMES_LINEAGE_KEYS.some(key => Object.prototype.hasOwnProperty.call(capture, key));
  const resolutionBlocksChildren = resolution === 'ambiguous' || resolution === 'unresolved';
  const sourceSuggestsChild = source === 'subagent' || source === 'leaf' || source === 'child' || source.includes('subagent');
  const hasNonRootLeaf = Boolean(leafSessionId && rootSessionId && leafSessionId !== rootSessionId);
  const isChild = Boolean(
    hasMetadata && rootSessionId && !resolutionBlocksChildren &&
    ((resolution === 'exact' && hasNonRootLeaf && sourceSuggestsChild) ||
      (resolution !== 'root_only' && hasNonRootLeaf && sourceSuggestsChild))
  );
  return {
    hasMetadata,
    rootSessionId,
    leafSessionId,
    parentSessionId,
    source,
    resolution,
    resolutionBlocksChildren,
    isChild,
  };
}

function flowHasHermesLineage(entry) {
  const lineage = flowHermesLineage(entry);
  return lineage.hasMetadata && Boolean(lineage.rootSessionId);
}

function flowHermesTurnKey(entry) {
  const captureTurn = flowCaptureText(entry, 'hermes_root_turn') || flowCaptureText(entry, 'hermes_root_capture_turn');
  if (captureTurn) return captureTurn.includes('.') ? captureTurn.split('.')[0] : captureTurn;
  return flowTurnKey(entry);
}

function flowTurnKey(entry) {
  const value = displayTurnValue(entry);
  if (value === undefined || value === null || value === '') return '';
  const displayValue = String(value);
  if (displayValue.includes('.')) return displayValue.split('.')[0];
  const captureValue = String(captureTurnValue(entry) ?? '');
  return captureValue.includes('.') ? captureValue.split('.')[0] : displayValue;
}

function flowRecordOrder(entry) {
  const entryIndex = Number(entry?._entry_index ?? entry?._rawIdx);
  if (Number.isFinite(entryIndex)) return entryIndex;
  const recordIndex = Number(entry?.record_index);
  if (Number.isFinite(recordIndex)) return recordIndex;
  const timestamp = Date.parse(entry?.timestamp || '');
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function flowRecordsForEntry(entry, source = entries) {
  const key = flowTurnKey(entry);
  const lineage = flowHermesLineage(entry);
  const candidates = (source || []).filter(item => {
    if (!item || !isNavigableTraceEntry(item) || !isDisplayTurnCandidate(item)) return false;
    if (lineage.hasMetadata && lineage.rootSessionId) {
      const candidateLineage = flowHermesLineage(item);
      if (candidateLineage.rootSessionId !== lineage.rootSessionId) return false;
      const candidateTurn = flowHermesTurnKey(item);
      const targetTurn = flowHermesTurnKey(entry);
      if (!targetTurn || !candidateTurn || candidateTurn === targetTurn) return true;
      const targetHasExplicitRootTurn = Boolean(flowCaptureText(entry, 'hermes_root_turn') || flowCaptureText(entry, 'hermes_root_capture_turn'));
      const candidateHasExplicitRootTurn = Boolean(flowCaptureText(item, 'hermes_root_turn') || flowCaptureText(item, 'hermes_root_capture_turn'));
      if (targetHasExplicitRootTurn || candidateHasExplicitRootTurn) return false;
      if (candidateLineage.resolutionBlocksChildren) return false;
      /* A subagent request can receive its own capture turn while still
       * belonging to the parent's root session.  Exact parent metadata is
       * sufficient to join it; ambiguous resolutions are intentionally not. */
      return candidateLineage.isChild && !candidateLineage.resolutionBlocksChildren &&
        (candidateLineage.parentSessionId === lineage.rootSessionId ||
          candidateLineage.parentSessionId === lineage.leafSessionId || lineage.isChild);
    }
    if (!key) return entryStableKey(item) === entryStableKey(entry);
    return flowTurnKey(item) === key;
  });
  if (!candidates.some(item => entryStableKey(item) === entryStableKey(entry))) candidates.push(entry);
  return candidates
    .map(item => resolveEntryForDetail(item))
    .sort((a, b) => flowRecordOrder(a) - flowRecordOrder(b));
}

async function resolveFlowRecordsForEntryAsync(entry) {
  const key = flowTurnKey(entry);
  const lineage = flowHermesLineage(entry);
  const candidates = (entries || []).filter(item => {
    if (!item || !isNavigableTraceEntry(item) || !isDisplayTurnCandidate(item)) return false;
    if (lineage.hasMetadata && lineage.rootSessionId) {
      const candidateLineage = flowHermesLineage(item);
      if (candidateLineage.rootSessionId !== lineage.rootSessionId) return false;
      const candidateTurn = flowHermesTurnKey(item);
      const targetTurn = flowHermesTurnKey(entry);
      if (!targetTurn || !candidateTurn || candidateTurn === targetTurn) return true;
      const targetHasExplicitRootTurn = Boolean(flowCaptureText(entry, 'hermes_root_turn') || flowCaptureText(entry, 'hermes_root_capture_turn'));
      const candidateHasExplicitRootTurn = Boolean(flowCaptureText(item, 'hermes_root_turn') || flowCaptureText(item, 'hermes_root_capture_turn'));
      if (targetHasExplicitRootTurn || candidateHasExplicitRootTurn) return false;
      if (candidateLineage.resolutionBlocksChildren) return false;
      return candidateLineage.isChild && !candidateLineage.resolutionBlocksChildren &&
        (candidateLineage.parentSessionId === lineage.rootSessionId ||
          candidateLineage.parentSessionId === lineage.leafSessionId || lineage.isChild);
    }
    if (!key) return entryStableKey(item) === entryStableKey(entry);
    return flowTurnKey(item) === key;
  });
  if (!candidates.some(item => entryStableKey(item) === entryStableKey(entry))) candidates.push(entry);
  const records = await Promise.all(candidates.map(item => resolveEntryForDetailAsync(item)));
  return records.sort((a, b) => flowRecordOrder(a) - flowRecordOrder(b));
}

function flowContentText(value) {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  if (Array.isArray(value)) return value.map(flowContentText).filter(Boolean).join('\n');
  if (typeof value !== 'object') return String(value);
  if (typeof value.text === 'string') return value.text;
  if (typeof value.output_text === 'string') return value.output_text;
  if (Object.prototype.hasOwnProperty.call(value, 'content')) return flowContentText(value.content);
  if (Object.prototype.hasOwnProperty.call(value, 'output')) return flowContentText(value.output);
  try { return JSON.stringify(value); } catch (_) { return String(value); }
}

function flowPreview(value, maxLength = 150) {
  const text = flowContentText(value)
    .replace(/[（(]\s*delegation\s+id\s*:\s*`?deleg_[^`)）\s]+`?\s*[)）]/gi, '')
    .replace(/\b(?:call|deleg)_[A-Za-z0-9_-]+\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!text) return '';
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function flowReadableValue(value, maxLength = 150) {
  let normalized = value;
  if (typeof normalized === 'string') {
    try { normalized = JSON.parse(normalized); } catch (_) { return flowPreview(normalized, maxLength); }
  }
  if (!normalized || typeof normalized !== 'object') return flowPreview(normalized, maxLength);
  if (Array.isArray(normalized)) {
    const parts = normalized.slice(0, 3).map(item => flowReadableValue(item, maxLength)).filter(text => text && text !== t('no_content'));
    return parts.length ? flowPreview(parts.join(' · '), maxLength) : t('no_content');
  }
  const preferredKeys = ['command', 'path', 'file_path', 'query', 'url', 'pattern', 'prompt', 'error', 'message', 'description', 'summary', 'text', 'output', 'content', 'result'];
  for (const key of preferredKeys) {
    if (!Object.prototype.hasOwnProperty.call(normalized, key)) continue;
    const text = flowContentText(normalized[key]);
    if (text.trim()) return flowPreview(text, maxLength);
  }
  const readable = Object.entries(normalized)
    .filter(([key, item]) => !/(^|_)(id|call_id|tool_use_id)$/i.test(key) && ['string', 'number', 'boolean'].includes(typeof item))
    .slice(0, 3)
    .map(([key, item]) => `${key}: ${item}`)
    .join(' · ');
  return readable ? flowPreview(readable, maxLength) : t('no_content');
}

function flowRequestResults(entry) {
  const body = entry?.request?.body;
  const results = [];
  const seen = new Map();
  const addResult = (id, payload, name = '') => {
    const resultId = String(id || '');
    const key = resultId || `${name || 'tool'}:${flowContentText(payload)}`;
    const existing = seen.get(key);
    if (existing) {
      if (!existing.name && name) existing.name = name;
      return;
    }
    const result = { id: resultId, name: name || '', payload };
    seen.set(key, result);
    results.push(result);
  };
  for (const message of getMessages(body)) {
    const role = String(message?.role || '').toLowerCase();
    const content = Array.isArray(message?.content) ? message.content : [message?.content];
    let hasStructuredResult = false;
    for (const block of content) {
      if (!block || typeof block !== 'object' || block.type !== 'tool_result') continue;
      hasStructuredResult = true;
      addResult(block.tool_use_id || block.call_id, block.content ?? block.output ?? block, block.name);
    }
    if ((role === 'tool' || role === 'function') && !hasStructuredResult) {
      addResult(message.tool_call_id || message.call_id || message.id, message.content ?? message.output ?? message, message.name);
    }
  }
  if (Array.isArray(body?.input)) {
    for (const item of body.input) {
      if (!item || typeof item !== 'object') continue;
      if (item.type === 'function_call_output' || item.type === 'tool_result' || item.type === 'computer_call_output') {
        addResult(item.call_id || item.tool_use_id || item.id, item.output ?? item.content ?? item, item.name);
      }
    }
  }
  if (Array.isArray(body?.messages)) {
    for (const message of body.messages) {
      const blocks = Array.isArray(message?.content) ? message.content : [];
      for (const block of blocks) {
        const result = block?.toolResult;
        if (!result || typeof result !== 'object') continue;
        addResult(result.toolUseId || result.tool_use_id, result.content ?? result, result.name);
      }
    }
  }
  for (const content of geminiRequest(body).contents || []) {
    for (const part of content?.parts || []) {
      const response = part?.functionResponse;
      if (!response || typeof response !== 'object') continue;
      addResult(response.id || response.name, response.response ?? response, response.name);
    }
  }
  return results;
}

function flowOutputParts(entry) {
  const output = getResponseOutput(entry);
  const content = Array.isArray(output?.content) ? output.content : [];
  const calls = [];
  const messages = [];
  const reasoning = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') continue;
    if (block.type === 'tool_use' || block.type === 'function_call') {
      const id = String(block.id || block.call_id || '');
      const name = String(block.name || block.function?.name || '').trim();
      const input = block.input ?? parseToolCallArguments(block.arguments ?? block.function?.arguments);
      const emptyInput = input === undefined || input === null || (typeof input === 'object' && !Array.isArray(input) && Object.keys(input).length === 0);
      if (!id && !name && emptyInput) continue;
      calls.push({
        id,
        name: name || 'tool',
        input,
      });
    } else if (block.type === 'text' || block.type === 'output_text') {
      if (block.text) messages.push(block.text);
    } else if (block.type === 'thinking' || block.type === 'reasoning') {
      const thinking = block.thinking || block.text || block.summary;
      const text = flowContentText(thinking).trim();
      if (text) reasoning.push(text);
    }
  }
  return { calls, messages, reasoning, text: [...reasoning, ...messages].join('\n') };
}

function flowDelegationName(name) {
  return String(name || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
}

function flowIsDelegationCall(call) {
  const name = flowDelegationName(call?.name);
  return name === 'delegate_task' || name === 'delegatetask' || name === 'delegate' || name === 'spawn_agent' || name === 'spawn_subagent';
}

function flowDelegationValue(input, keys) {
  if (!input || typeof input !== 'object') return undefined;
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(input, key) && input[key] !== undefined && input[key] !== null && input[key] !== '') {
      return input[key];
    }
  }
  return undefined;
}

function flowDelegationSummary(value, maxLength = 150) {
  let normalized = value;
  if (typeof normalized === 'string') {
    try { normalized = JSON.parse(normalized); } catch (_) { return flowPreview(normalized, maxLength); }
  }
  if (Array.isArray(normalized)) {
    const parts = normalized.map(item => flowDelegationSummary(item, maxLength)).filter(Boolean);
    return flowPreview(parts.join(' · '), maxLength);
  }
  if (!normalized || typeof normalized !== 'object') return flowPreview(normalized, maxLength);
  const preferred = ['task', 'prompt', 'goal', 'description', 'summary', 'text', 'content', 'output', 'result'];
  for (const key of preferred) {
    const text = flowDelegationSummary(normalized[key], maxLength);
    if (text) return text;
  }
  const goals = normalized.goals;
  if (goals !== undefined) {
    const text = flowDelegationSummary(goals, maxLength);
    if (text) return text;
  }
  return flowReadableValue(normalized, maxLength);
}

function flowDelegationDescriptor(call, resultPayload) {
  const input = call?.input;
  let result = resultPayload && typeof resultPayload === 'object' ? resultPayload : {};
  if (typeof resultPayload === 'string') {
    try {
      const parsed = JSON.parse(resultPayload);
      if (parsed && typeof parsed === 'object') result = parsed;
    } catch (_) { /* retain the raw result in the payload */ }
  }
  const delegationId = String(
    flowDelegationValue(input, ['delegation_id', 'delegationId', 'task_id', 'taskId']) ??
      flowDelegationValue(result, ['delegation_id', 'delegationId', 'task_id', 'taskId']) ?? ''
  ).trim();
  const goals = flowDelegationValue(input, ['goals', 'tasks', 'goal', 'task', 'prompt', 'description', 'summary']) ??
    flowDelegationValue(result, ['goals', 'tasks', 'goal', 'task', 'prompt', 'description', 'summary']);
  const summary = flowDelegationSummary(goals ?? input);
  return {
    delegationId,
    goals,
    summary: summary || t('flow_no_input'),
    input,
    result: resultPayload,
  };
}

function flowDelegationGoalTexts(descriptor) {
  const goals = descriptor?.goals;
  if (Array.isArray(goals)) return goals.map(goal => flowDelegationSummary(goal, 180)).filter(Boolean);
  const text = flowDelegationSummary(goals, 180);
  return text ? [text] : [];
}

function flowGoalMatchesInput(descriptor, inputSummary) {
  const input = String(inputSummary || '').toLowerCase().replace(/\s+/g, ' ').trim();
  if (!input) return false;
  return flowDelegationGoalTexts(descriptor).some(goal => {
    const normalized = String(goal).toLowerCase().replace(/\s+/g, ' ').trim();
    if (!normalized) return false;
    return normalized.includes(input) || input.includes(normalized);
  });
}

function flowDelegationSummaryForInput(descriptor, inputSummary) {
  const input = String(inputSummary || '').toLowerCase().replace(/\s+/g, ' ').trim();
  const goal = flowDelegationGoalTexts(descriptor).find(item => {
    const normalized = String(item).toLowerCase().replace(/\s+/g, ' ').trim();
    return normalized && input && (normalized.includes(input) || input.includes(normalized));
  });
  return goal || descriptor?.summary || '';
}

function flowAgentOutputSummary(records) {
  for (let i = records.length - 1; i >= 0; i--) {
    const output = flowOutputParts(records[i]);
    const finalText = output.messages.join('\n').trim();
    if (finalText) return flowPreview(finalText, 150);
    const reasoningText = output.reasoning.join('\n').trim();
    if (reasoningText) return flowPreview(reasoningText, 150);
    if (output.calls.length) {
      const calls = output.calls.map(call => call.name).filter(Boolean);
      if (calls.length) return flowPreview(calls.join(', '), 150);
    }
    const results = flowRequestResults(records[i]);
    if (results.length) {
      const resultText = results.map(result => flowReadableValue(result.payload, 150)).filter(Boolean).join(' · ');
      if (resultText) return flowPreview(resultText, 150);
    }
    const error = getResponseErrorMessage(records[i]);
    if (error) return flowPreview(error, 150);
  }
  return t('no_content');
}

function flowAgentInputSummary(records) {
  for (const record of records) {
    const modules = flowInputModules(record, new Set(), new Map(), true);
    const user = modules.find(module => module.label === t('flow_user')) || modules[0];
    if (user?.preview) return user.preview;
  }
  return t('flow_no_input');
}

function flowAgentStatus(records) {
  const statuses = records.map(record => getResponseStatus(record)).filter(status => Number.isFinite(status));
  const status = statuses.length ? statuses[statuses.length - 1] : 0;
  if (status >= 400) return `HTTP ${status}`;
  if (records.some(record => getResponseOutput(record))) return t('flow_agent_complete');
  return t('flow_agent_pending');
}

function flowAgentGroupKey(lineage, index) {
  return lineage.leafSessionId || lineage.parentSessionId || `agent-${index + 1}`;
}

function flowResultKey(result) {
  return result.id || `${result.name || 'tool'}:${flowContentText(result.payload)}`;
}

function flowInputModules(entry, seenResultKeys = new Set(), callOrigins = new Map(), preferUser = false) {
  const toolResults = flowRequestResults(entry);
  const newResults = toolResults.filter(result => !seenResultKeys.has(flowResultKey(result)));
  toolResults.forEach(result => seenResultKeys.add(flowResultKey(result)));
  if (newResults.length && !preferUser) {
    return newResults.map(result => {
      const origin = callOrigins.get(result.id) || callOrigins.get(result.name) || null;
      const toolName = origin?.toolName || result.name || t('section_tools');
      return {
        type: 'input',
        label: t('flow_result'),
        source: origin ? `${t('flow_from')} ${t('flow_turn')} ${origin.turnLabel} · ${toolName}` : toolName,
        preview: flowReadableValue(result.payload, 120),
        payload: { call_id: result.id, tool: toolName, result: result.payload },
      };
    });
  }
  const messages = getMessages(entry?.request?.body);
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message?.role !== 'user' && message?.role !== 'human') continue;
    const blocks = Array.isArray(message.content) ? message.content : [message.content];
    const userBlocks = blocks.filter(block => !(block && typeof block === 'object' && block.type === 'tool_result'));
    const text = flowContentText(userBlocks).trim();
    if (text) return [{ type: 'input', label: t('flow_user'), source: '', preview: flowPreview(text, 120), payload: message }];
  }
  const body = entry?.request?.body;
  return [{ type: 'input', label: t('section_context'), source: '', preview: flowReadableValue(body, 120) || t('flow_no_input'), payload: body }];
}

function flowInternalTurnLabel(entry, index, rootTurn) {
  const value = String(displayTurnLabel(entry));
  return value.includes('.') ? value : `${rootTurn}.${index + 1}`;
}

function flowOutputModules(entry, output) {
  const status = getResponseStatus(entry);
  if (status >= 400) {
    const error = getResponseErrorMessage(entry);
    return [{ type: 'output', label: `HTTP ${status}`, source: '', preview: error || `HTTP ${status}`, payload: getResponsePayload(entry) || entry?.response }];
  }
  const modules = [];
  if (output.reasoning.length) {
    modules.push({ type: 'output', label: t('flow_reasoning'), source: '', preview: flowPreview(output.reasoning.join('\n'), 120), payload: output.reasoning });
  }
  if (output.messages.length) {
    modules.push({ type: 'output', label: t('section_response'), source: '', preview: flowPreview(output.messages.join('\n'), 120), payload: output.messages });
  }
  if (output.calls.length) {
    const counts = new Map();
    output.calls.forEach(call => counts.set(call.name, (counts.get(call.name) || 0) + 1));
    const summary = [...counts].map(([name, count]) => count > 1 ? `${name} ×${count}` : name).join(', ');
    modules.push({ type: 'output', label: t('section_tools'), source: '', preview: summary, payload: output.calls });
  }
  if (!modules.length) modules.push({ type: 'output', label: t('tok_output'), source: '', preview: t('no_content'), payload: null });
  return modules;
}

function flowRequestFingerprint(entry) {
  const body = entry?.request?.body || {};
  const messages = getMessages(body);
  const latest = [...messages].reverse().find(message => message?.role === 'user' || message?.role === 'human');
  return [entry?.request?.path || '', body?.model || '', flowContentText(latest?.content)].join('|');
}

function flowAgentDelegationId(entry) {
  return flowCaptureText(entry, 'hermes_delegation_id') || flowCaptureText(entry, 'delegation_id') || flowCaptureText(entry, 'delegationId');
}

function flowAgentNodeFromRecords(records, index, descriptor, parentToolId, inferredJoin = false) {
  const sorted = [...records].sort((a, b) => flowRecordOrder(a) - flowRecordOrder(b));
  const first = sorted[0] || {};
  const last = sorted[sorted.length - 1] || first;
  const lineage = flowHermesLineage(first);
  const usages = sorted.map(record => getUsage(record) || {});
  const inputTokens = usages.reduce((sum, usage) => sum + Number(usage.input_tokens || 0), 0);
  const outputTokens = usages.reduce((sum, usage) => sum + Number(usage.output_tokens || 0), 0);
  const duration = sorted.reduce((sum, record) => sum + Number(record?.duration_ms || 0), 0);
  const status = getResponseStatus(last);
  const outputSummary = flowAgentOutputSummary(sorted);
  const taskSummary = descriptor?.summary || flowAgentInputSummary(sorted);
  const payload = {
    task: taskSummary,
    output: sorted.map(record => ({
      request_id: record?.request_id || '',
      response: getResponsePayload(record),
      output: flowOutputParts(record),
    })),
    metadata: {
      hermes_root_session_id: lineage.rootSessionId,
      hermes_leaf_session_id: lineage.leafSessionId,
      hermes_parent_session_id: lineage.parentSessionId,
      hermes_session_source: lineage.source,
      hermes_session_resolution: lineage.resolution,
      hermes_root_turn: flowCaptureText(first, 'hermes_root_turn') || flowCaptureText(first, 'hermes_root_capture_turn'),
      delegation_id: descriptor?.delegationId || flowAgentDelegationId(first),
      inferred_join: inferredJoin,
      parent_tool_id: parentToolId || '',
    },
    delegation: descriptor ? { input: descriptor.input, result: descriptor.result } : null,
  };
  return {
    id: `flow-agent-${index}`,
    type: 'agent',
    title: `${t('flow_child_agent')} ${index + 1}`,
    summary: taskSummary,
    resultSummary: outputSummary,
    inputSummary: flowAgentInputSummary(sorted),
    outputSummary,
    status,
    statusLabel: status >= 400 ? `HTTP ${status}` : flowAgentStatus(sorted),
    duration,
    inputTokens,
    outputTokens,
    source: t('flow_subagent'),
    parentToolId: parentToolId || '',
    inferredJoin,
    lineage,
    payload,
  };
}

function flowBuildAgentBranches(sorted, childRecords, stages, allResults, nodes) {
  if (!childRecords.length) return [];
  const grouped = new Map();
  childRecords.forEach((entry, index) => {
    const lineage = flowHermesLineage(entry);
    const key = flowAgentGroupKey(lineage, index);
    if (!grouped.has(key)) grouped.set(key, { records: [], lineage });
    grouped.get(key).records.push(entry);
  });
  const delegates = [];
  stages.forEach(stage => {
    stage.tools.filter(tool => tool.isDelegate).forEach(tool => delegates.push({ stage, tool }));
  });
  const branches = [];
  [...grouped.values()].forEach((group, index) => {
    const first = group.records.slice().sort((a, b) => flowRecordOrder(a) - flowRecordOrder(b))[0];
    const childLineage = flowHermesLineage(first);
    const childDelegationId = flowAgentDelegationId(first);
    const childInputSummary = flowAgentInputSummary(group.records);
    const parentMatches = delegates.filter(candidate => {
      const rootLineage = candidate.stage.turn.lineage || flowHermesLineage(candidate.stage._entry || {});
      return childLineage.parentSessionId &&
        (childLineage.parentSessionId === rootLineage.leafSessionId || childLineage.parentSessionId === rootLineage.rootSessionId);
    });
    const idMatch = childDelegationId
      ? delegates.find(candidate => candidate.tool.delegation?.delegationId && childDelegationId === candidate.tool.delegation.delegationId)
      : null;
    const goalMatches = parentMatches.filter(candidate => flowGoalMatchesInput(candidate.tool.delegation, childInputSummary));
    const prior = candidates => candidates
      .filter(candidate => flowRecordOrder(candidate.stage._entry || {}) <= flowRecordOrder(first))
      .sort((a, b) => flowRecordOrder(b.stage._entry || {}) - flowRecordOrder(a.stage._entry || {}));
    let match = idMatch || prior(goalMatches)[0] || prior(parentMatches)[0] || null;
    let inferredJoin = false;
    if (!match && delegates.length) {
      const firstOrder = flowRecordOrder(first);
      const previousDelegates = delegates.filter(candidate => flowRecordOrder(candidate.stage._entry || {}) <= firstOrder);
      const pool = previousDelegates.length ? previousDelegates : delegates;
      match = pool.reduce((best, candidate) => {
        const distance = Math.abs(flowRecordOrder(candidate.stage._entry || {}) - firstOrder);
        return !best || distance < best.distance ? { ...candidate, distance } : best;
      }, null);
      inferredJoin = true;
    }
    if (!match && stages.length) {
      const firstOrder = flowRecordOrder(first);
      const stage = stages.reduce((best, candidate) => {
        const distance = Math.abs(flowRecordOrder(candidate._entry || {}) - firstOrder);
        return !best || distance < best.distance ? { stage: candidate, distance } : best;
      }, null)?.stage;
      match = stage ? { stage, tool: null } : null;
      inferredJoin = true;
    }
    if (match && !idMatch) inferredJoin = true;
    const descriptor = match?.tool?.delegation || null;
    const childDescriptor = descriptor
      ? { ...descriptor, summary: flowDelegationSummaryForInput(descriptor, childInputSummary) }
      : null;
    const node = flowAgentNodeFromRecords(group.records, index, childDescriptor, match?.tool?.id, inferredJoin);
    node.parentTurnLabel = match?.stage?.turn?.turnLabel || '';
    node.parentToolName = match?.tool?.title || (match ? 'delegate_task' : '');
    branches.push(node);
    nodes.push(node);
    if (match?.stage) {
      if (!match.stage.agents) match.stage.agents = [];
      match.stage.agents.push(node);
    }
  });
  return branches;
}

function buildFlowGraph(records) {
  const sorted = [...(records || [])].sort((a, b) => flowRecordOrder(a) - flowRecordOrder(b));
  if (!sorted.length) return { turn: '', stages: [], nodes: [], totals: {} };
  const lineageEnabled = sorted.some(flowHasHermesLineage);
  const childRecords = lineageEnabled ? sorted.filter(entry => flowHermesLineage(entry).isChild) : [];
  const laneRecords = lineageEnabled ? sorted.filter(entry => !flowHermesLineage(entry).isChild) : sorted;
  const mainRecords = laneRecords.length ? laneRecords : sorted;
  const allResults = new Map();
  for (const entry of sorted) {
    for (const result of flowRequestResults(entry)) {
      if (result.id) allResults.set(result.id, result);
      if (result.name) allResults.set(result.name, result);
    }
  }
  const rootTurn = flowTurnKey(mainRecords[0]) || String(displayTurnLabel(mainRecords[0]));
  const stages = [];
  const nodes = [];
  let previousFingerprint = '';
  let previousFailed = false;
  let retryAttempt = 1;
  const seenInputResults = new Set();
  const callOrigins = new Map();
  mainRecords.forEach((entry, index) => {
    const usage = getUsage(entry) || {};
    const status = getResponseStatus(entry);
    const output = flowOutputParts(entry);
    const fingerprint = flowRequestFingerprint(entry);
    const inferredRetry = !!(index > 0 && previousFailed && fingerprint && fingerprint === previousFingerprint);
    retryAttempt = inferredRetry ? retryAttempt + 1 : 1;
    const model = entry?.request?.body?.model || getResponsePayload(entry)?.model || 'model';
    const turnLabel = flowInternalTurnLabel(entry, index, rootTurn);
    const inputModules = flowInputModules(entry, seenInputResults, callOrigins, index === 0).map((module, moduleIndex) => ({
      ...module,
      id: `flow-input-module-${index}-${moduleIndex}`,
      type: 'input_module',
      title: module.label,
      turnLabel,
    }));
    const outputModules = flowOutputModules(entry, output).map((module, moduleIndex) => ({
      ...module,
      id: `flow-output-module-${index}-${moduleIndex}`,
      type: 'output_module',
      title: module.label,
      turnLabel,
    }));
    const turnNode = {
      id: `flow-turn-${index}`,
      type: status >= 400 ? 'error' : 'turn',
      title: `${t('flow_turn')} ${turnLabel}`,
      turnLabel,
      model,
      inputModules,
      outputModules,
      status,
      statusLabel: entry?.transport === 'websocket' && status === 101 ? 'WebSocket' : `HTTP ${status}`,
      duration: Number(entry?.duration_ms || 0),
      inputTokens: Number(usage.input_tokens || 0),
      outputTokens: Number(usage.output_tokens || 0),
      inferredRetry,
      attempt: retryAttempt,
      lineage: flowHermesLineage(entry),
      payload: {
        input: inputModules.map(module => module.payload),
        output: outputModules.map(module => module.payload),
        metadata: {
          model,
          path: entry?.request?.path || '',
          status,
          duration_ms: Number(entry?.duration_ms || 0),
          input_tokens: Number(usage.input_tokens || 0),
          output_tokens: Number(usage.output_tokens || 0),
          hermes_root_session_id: flowHermesLineage(entry).rootSessionId,
          hermes_leaf_session_id: flowHermesLineage(entry).leafSessionId,
          hermes_parent_session_id: flowHermesLineage(entry).parentSessionId,
          hermes_session_source: flowHermesLineage(entry).source,
          hermes_session_resolution: flowHermesLineage(entry).resolution,
          hermes_root_turn: flowCaptureText(entry, 'hermes_root_turn') || flowCaptureText(entry, 'hermes_root_capture_turn'),
        },
      },
    };
    const tools = output.calls.map((call, toolIndex) => {
      const result = (call.id ? allResults.get(call.id) : null) || allResults.get(call.name) || null;
      const delegation = flowIsDelegationCall(call) ? flowDelegationDescriptor(call, result?.payload) : null;
      const node = {
        id: `flow-tool-${index}-${toolIndex}`,
        type: 'tool',
        title: call.name,
        summary: delegation?.summary || flowReadableValue(call.input, 120),
        result: result?.payload,
        resultSummary: result ? (delegation ? flowDelegationSummary(result.payload, 120) : flowReadableValue(result.payload, 120)) : '',
        pending: !result,
        isDelegate: Boolean(delegation),
        delegation,
        payload: { call_id: call.id, input: call.input, result: result?.payload },
      };
      const origin = { turnLabel, toolName: call.name };
      if (call.id) callOrigins.set(call.id, origin);
      callOrigins.set(call.name, origin);
      nodes.push(node);
      return node;
    });
    nodes.push(...inputModules, ...outputModules);
    nodes.push(turnNode);
    stages.push({ turn: turnNode, tools, agents: [], _entry: entry });
    previousFingerprint = fingerprint;
    previousFailed = status >= 400;
  });
  const agents = flowBuildAgentBranches(sorted, childRecords, stages, allResults, nodes);
  return {
    turn: rootTurn,
    stages,
    nodes,
    totals: {
      calls: stages.length,
      tools: stages.reduce((sum, stage) => sum + stage.tools.length, 0),
      duration: stages.reduce((sum, stage) => sum + stage.turn.duration, 0),
      inputTokens: stages.reduce((sum, stage) => sum + stage.turn.inputTokens, 0),
      outputTokens: stages.reduce((sum, stage) => sum + stage.turn.outputTokens, 0),
      agents: agents.length,
    },
    agents,
  };
}

function flowMetric(label, value) {
  return `<span class="flow-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></span>`;
}

function flowModuleButton(module, direction) {
  const source = module.source ? `<span class="flow-module-source">${esc(module.source)}</span>` : '';
  return `<button type="button" class="flow-io-module flow-${direction}-module" data-flow-node="${esc(module.id)}" onclick="selectFlowNode('${esc(module.id)}')">
    <span class="flow-module-heading"><strong>${esc(module.label)}</strong>${source}</span>
    <span class="flow-module-preview">${esc(module.preview)}</span>
  </button>`;
}

function flowNodeButton(node, className = '') {
  const metrics = [];
  if (node.type === 'turn' || node.type === 'error') {
    metrics.push(`${(node.inputTokens + node.outputTokens).toLocaleString()} ${t('tok')}`);
    metrics.push(`${Math.round(node.duration).toLocaleString()} ms`);
    metrics.push(node.statusLabel);
  } else if (node.type === 'agent') {
    metrics.push(`${(node.inputTokens + node.outputTokens).toLocaleString()} ${t('tok')}`);
    metrics.push(`${Math.round(node.duration).toLocaleString()} ms`);
  }
  const badges = [];
  if (node.inferredRetry) badges.push(`<span class="flow-node-badge retry">${esc(t('flow_retry'))} ${node.attempt}</span>`);
  if (node.pending) badges.push(`<span class="flow-node-badge pending">${esc(t('flow_pending'))}</span>`);
  if (node.type === 'turn' || node.type === 'error') {
    return `<div class="flow-node ${esc(className)} flow-node-turn ${node.type === 'error' ? 'flow-node-error' : ''}" data-turn="${esc(node.turnLabel)}">
      <button type="button" class="flow-turn-heading" data-flow-node="${esc(node.id)}" onclick="selectFlowNode('${esc(node.id)}')"><strong>${esc(node.title)}</strong></button>
      <div class="flow-turn-io flow-turn-input"><span class="flow-io-label">${esc(t('tok_input'))}</span><div class="flow-module-list">${node.inputModules.map(module => flowModuleButton(module, 'input')).join('')}</div></div>
      <div class="flow-turn-io flow-turn-output"><span class="flow-io-label">${esc(t('tok_output'))}</span><div class="flow-module-list">${node.outputModules.map(module => flowModuleButton(module, 'output')).join('')}</div></div>
      ${metrics.length ? `<span class="flow-node-meta">${metrics.map(esc).join('<i></i>')}</span>` : ''}
      ${badges.join('')}
    </div>`;
  }
  if (node.type === 'agent') {
    const relation = node.inferredJoin ? t('flow_inferred_relation') : `${t('flow_parent_to_child')} · ${node.parentToolName || t('flow_delegate')}`;
    return `<button type="button" class="flow-node ${esc(className)} flow-node-agent flow-agent-node" data-flow-agent="true" data-flow-agent-leaf="${esc(node.lineage?.leafSessionId || '')}" data-flow-node="${esc(node.id)}" onclick="selectFlowNode('${esc(node.id)}')">
      <span class="flow-node-kind">${esc(t('flow_child_agent'))}</span>
      <strong class="flow-node-title">${esc(node.title)}</strong>
      <span class="flow-agent-relation">${esc(relation)}</span>
      <span class="flow-agent-task"><span>${esc(t('flow_task'))}</span>${esc(node.summary || t('flow_no_input'))}</span>
      <span class="flow-agent-output"><span>${esc(t('flow_return'))}</span>${esc(node.outputSummary || t('no_content'))}</span>
      <span class="flow-agent-status"><span>${esc(t('flow_status'))}</span>${esc(node.statusLabel)}</span>
      ${metrics.length ? `<span class="flow-node-meta">${metrics.map(esc).join('<i></i>')}</span>` : ''}
    </button>`;
  }
  const kindLabel = node.type === 'tool' ? t('section_tools') : node.title;
  return `<button type="button" class="flow-node ${esc(className)} flow-node-${esc(node.type)}" data-flow-node="${esc(node.id)}" onclick="selectFlowNode('${esc(node.id)}')">
    <span class="flow-node-kind">${esc(kindLabel)}</span>
    <strong class="flow-node-title">${esc(node.title)}</strong>
    ${node.summary ? `<span class="flow-node-summary">${esc(node.summary)}</span>` : ''}
    ${node.resultSummary ? `<span class="flow-node-result"><span>${esc(t('flow_result'))}</span>${esc(node.resultSummary)}</span>` : ''}
    ${metrics.length ? `<span class="flow-node-meta">${metrics.map(esc).join('<i></i>')}</span>` : ''}
    ${badges.join('')}
  </button>`;
}

function renderFlowStage(stage, index) {
  const mainLaneLabel = stage.turn.lineage?.hasMetadata
    ? `<div class="flow-main-lane-label">${esc(t('flow_parent_agent'))}</div>`
    : '';
  const tools = stage.tools.length
    ? `<div class="flow-branch-label">${esc(stage.tools.length > 1 ? t('flow_parallel_tools') : t('section_tools'))}</div>
       <div class="flow-tool-branches ${stage.tools.length > 1 ? 'parallel' : ''}">${stage.tools.map(tool => flowNodeButton(tool)).join('')}</div>`
    : '';
  return `<div class="flow-stage" data-flow-stage="${index}">
    ${index > 0 ? '<div class="flow-arrow" aria-hidden="true"></div>' : ''}
    ${mainLaneLabel}
    ${flowNodeButton(stage.turn)}
    ${tools ? `<div class="flow-arrow" aria-hidden="true"></div>${tools}` : ''}
    ${stage.agents?.length ? `<div class="flow-arrow flow-agent-arrow" aria-hidden="true"></div>
      <div class="flow-agent-relation-group" data-parent-tool="${esc(stage.agents.map(agent => agent.parentToolId).filter(Boolean).join(','))}">
        <div class="flow-branch-label">${esc(stage.agents.length > 1 ? t('flow_parallel_agents') : t('flow_delegate'))}</div>
        <div class="flow-agent-branches ${stage.agents.length > 1 ? 'parallel' : ''}">${stage.agents.map(agent => flowNodeButton(agent)).join('')}</div>
      </div>` : ''}
  </div>`;
}

function renderFlowNodeDetails(node) {
  if (!node) return `<div class="flow-detail-empty">${esc(t('flow_select'))}</div>`;
  const meta = [];
  if (node.turnLabel) meta.push([t('flow_turn'), node.turnLabel]);
  if (node.source) meta.push([t('flow_from'), node.source]);
  if (node.model) meta.push([t('flow_model'), node.model]);
  if (node.status !== undefined) meta.push(['HTTP', node.status]);
  if (node.duration !== undefined) meta.push([t('flow_duration'), `${Math.round(node.duration).toLocaleString()} ms`]);
  if (node.inputTokens !== undefined) meta.push([t('tok_input'), `${node.inputTokens.toLocaleString()} ${t('tok')}`]);
  if (node.outputTokens !== undefined) meta.push([t('tok_output'), `${node.outputTokens.toLocaleString()} ${t('tok')}`]);
  return `<div class="flow-detail-heading"><span>${esc(node.type)}</span><strong>${esc(node.title)}</strong></div>
    ${meta.length ? `<div class="flow-detail-meta">${meta.map(([key, value]) => `<div><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join('')}</div>` : ''}
    <div class="flow-detail-payload">${renderTracePrettyValue(flowBoundDetailValue(node.payload))}</div>`;
}

function flowBoundDetailValue(value, depth = 0) {
  if (typeof value === 'string') {
    if (value.length <= 4000) return value;
    return `${value.slice(0, 4000)}\n... [${(value.length - 4000).toLocaleString()} more characters; full value is available in Trace]`;
  }
  if (value === null || value === undefined || typeof value !== 'object') return value;
  if (depth >= 7) return '[Nested value; open Trace for full content]';
  if (Array.isArray(value)) {
    const items = value.length > 16
      ? [...value.slice(0, 3), `[${value.length - 15} earlier items omitted]`, ...value.slice(-12)]
      : value;
    return items.map(item => flowBoundDetailValue(item, depth + 1));
  }
  const result = {};
  const keys = Object.keys(value);
  for (const key of keys.slice(0, 24)) result[key] = flowBoundDetailValue(value[key], depth + 1);
  if (keys.length > 24) result._flow_notice = `${keys.length - 24} fields omitted; open Trace for full content`;
  return result;
}

function selectFlowNode(nodeId) {
  if (!activeFlowNodeMap.has(nodeId)) return;
  flowSelectedNodeId = nodeId;
  document.querySelectorAll('[data-flow-node]').forEach(node => {
    const selected = node.dataset.flowNode === nodeId;
    node.classList.toggle('selected', selected);
    node.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  document.querySelectorAll('.flow-node-turn').forEach(turn => {
    turn.classList.toggle('selected', turn.querySelector('.flow-turn-heading')?.dataset.flowNode === nodeId);
  });
  const details = document.querySelector('.flow-details');
  if (details) details.innerHTML = renderFlowNodeDetails(activeFlowNodeMap.get(nodeId));
}

function initializeFlowSelection() {
  if (!activeFlowGraph) return;
  if (activeFlowNodeMap.has(flowSelectedNodeId)) selectFlowNode(flowSelectedNodeId);
}

function renderFlowDetail(entry) {
  const matchingActiveRecords = activeFlowRecords.length && flowTurnKey(activeFlowRecords[0]) === flowTurnKey(entry)
    ? activeFlowRecords
    : flowRecordsForEntry(entry);
  const previousTurn = activeFlowGraph?.turn;
  activeFlowGraph = buildFlowGraph(matchingActiveRecords);
  if (previousTurn !== activeFlowGraph.turn) flowSelectedNodeId = '';
  activeFlowNodeMap = new Map(activeFlowGraph.nodes.map(node => [node.id, node]));
  if (!activeFlowGraph.stages.length) return `<div class="empty-state">${esc(t('flow_empty'))}</div>`;
  const totals = activeFlowGraph.totals;
  const summary = `<div class="flow-summary">
    <div class="flow-summary-title"><span>${esc(t('flow_turn'))}</span><strong>${esc(activeFlowGraph.turn)}</strong></div>
    <div class="flow-summary-metrics">
      ${flowMetric(t('flow_calls'), totals.calls.toLocaleString())}
      ${flowMetric(t('section_tools'), totals.tools.toLocaleString())}
      ${flowMetric(t('flow_duration'), `${Math.round(totals.duration).toLocaleString()} ms`)}
      ${flowMetric(t('section_usage'), `${(totals.inputTokens + totals.outputTokens).toLocaleString()} ${t('tok')}`)}
    </div>
  </div>`;
  return `${summary}<div class="flow-layout">
    <div class="flow-canvas" aria-label="${esc(t('tab_flow'))}">
      ${activeFlowGraph.stages.map(renderFlowStage).join('')}
    </div>
    <aside class="flow-details" aria-live="polite">${renderFlowNodeDetails(activeFlowNodeMap.get(flowSelectedNodeId))}</aside>
  </div>`;
}
