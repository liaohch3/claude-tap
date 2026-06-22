
/* ─── Detail ─── */
// Persist section collapse state across turn switches.
// Key: section title text, Value: true = open, false = collapsed.
const sectionCollapseState = {};
let detailViewMode = 'default';
let traceFormatMode = 'json';
let detailLoadToken = 0;

function saveSectionStates() {
  const d = $('#detail');
  if (!d) return;
  d.querySelectorAll('.section').forEach(sec => {
    const titleEl = sec.querySelector('.section-header .title');
    const bodyEl = sec.querySelector('.section-body');
    if (titleEl && bodyEl) {
      sectionCollapseState[titleEl.textContent] = bodyEl.classList.contains('open');
    }
  });
}

function restoreSectionStates() {
  const d = $('#detail');
  if (!d) return;
  d.querySelectorAll('.section').forEach(sec => {
    const titleEl = sec.querySelector('.section-header .title');
    const bodyEl = sec.querySelector('.section-body');
    const chevron = sec.querySelector('.chevron');
    if (!titleEl || !bodyEl || !chevron) return;
    const key = titleEl.textContent;
    if (key in sectionCollapseState) {
      const shouldBeOpen = sectionCollapseState[key];
      if (shouldBeOpen && !bodyEl.classList.contains('open')) {
        bodyEl.classList.add('open');
        chevron.classList.add('open');
      } else if (!shouldBeOpen && bodyEl.classList.contains('open')) {
        bodyEl.classList.remove('open');
        chevron.classList.remove('open');
      }
    }
  });
}

function setDetailViewMode(mode) {
  detailViewMode = mode;
  const entry = filtered[activeIdx];
  if (!entry) return;
  renderDetailForEntry(entry);
}

function detailTabButton(mode, label) {
  const active = detailViewMode === mode;
  return `<button type="button" role="tab" aria-selected="${active ? 'true' : 'false'}" data-tab="${mode}" class="detail-tab ${active ? 'active' : ''}" onclick="setDetailViewMode('${mode}')"><span>${esc(label)}</span></button>`;
}

function renderDetailViewTabs() {
  return `<div class="detail-inspector-bar" role="tablist" aria-label="Detail view mode"><div class="detail-tabs">
    ${detailTabButton('default', t('tab_default'))}
    ${detailTabButton('trace', t('tab_trace'))}
  </div></div>`;
}

function setTraceFormatMode(mode) {
  if (!['json', 'yaml', 'pretty'].includes(mode)) return;
  traceFormatMode = mode;
  const entry = filtered[activeIdx];
  if (!entry) return;
  renderDetailForEntry(entry);
}

function renderTraceFormatControls() {
  const modes = [
    ['json', t('format_json')],
    ['yaml', t('format_yaml')],
    ['pretty', t('format_pretty')],
  ];
  return `<div class="trace-format-bar" role="toolbar" aria-label="Trace format">${modes.map(([mode, label]) => {
    const active = traceFormatMode === mode;
    return `<button type="button" data-format="${mode}" class="trace-format-btn ${active ? 'active' : ''}" aria-pressed="${active ? 'true' : 'false'}" onclick="setTraceFormatMode('${mode}')">${esc(label)}</button>`;
  }).join('')}</div>`;
}

async function renderDetailForEntry(entry) {
  if (!shouldFetchRemoteEntry(entry)) {
    renderDetail(resolveEntryForDetail(entry));
    return;
  }
  const token = ++detailLoadToken;
  currentDetailRequestId = entry.request_id;
  currentDetailEntryKey = entryStableKey(entry);
  $('#detail').innerHTML = '<div class="empty-state" role="status" aria-live="polite"></div>';
  try {
    const resolved = await resolveEntryForDetailAsync(entry);
    if (token !== detailLoadToken) return;
    renderDetail(resolved);
  } catch (err) {
    console.error('Failed to load trace record:', err);
    if (token !== detailLoadToken) return;
    renderDetail(resolveEntryForDetail(entry));
  }
}

function renderDetail(e) {
  saveSectionStates();
  currentDetailRequestId = e.request_id;
  currentDetailEntryKey = entryStableKey(e);
  const d = $('#detail');
  const reqBody = e.request?.body, respBody = e.response?.body, usage = getUsage(e);
  const statusCode = getResponseStatus(e);
  const isError = statusCode >= 400;
  const errorMessage = getResponseErrorMessage(e);
  let html = '';

  if (isError) {
    html += `<div class="error-banner"><div class="eb-icon">&#9888;</div><div class="eb-content"><div class="eb-title">HTTP ${statusCode}</div><div class="eb-message">${esc(errorMessage)}</div></div></div>`;
  }
  const continuation = getResponsesContinuationInfo(e);
  if (continuation) html += renderResponsesContinuationNotice(continuation);

  const tools = getRequestTools(reqBody);
  const sysPrompt = extractSystem(reqBody);
  const sysBlocks = extractSystemBlocks(reqBody);
  const respOutput = getResponseOutput(e);
  const msgs = getMessages(reqBody);
  const contextOnly = shouldRenderRequestContext(e, reqBody, msgs, respOutput);
  const streamEvents = getResponseEvents(e);
  html += renderDetailViewTabs();

  if (detailViewMode === 'default') {
    const actionBarHtml = `<div class="action-bar">
      <button class="act-btn" onclick="copyRequestBody(this)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>${t('btn_request_json')}</button>
      <button class="act-btn" onclick="copyCurl(this)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>${t('btn_curl')}</button>
      <button class="act-btn" onclick="showDiff(this)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v18M3 12h18"/><path d="M3 6h18M3 18h18" opacity=".4"/></svg>${t('btn_diff')}</button>
    </div>`;
    const toolsSection = tools && tools.length
      ? section(t('section_tools'), renderTools(tools), false, null, tools.length + ' ' + t('badge_tools'))
      : '';
    const systemSection = sysPrompt ? section(t('section_system'), renderSystemPrompt(sysBlocks, sysPrompt), true, sysPrompt) : '';
    const messagesSection = msgs && msgs.length
      ? section(contextOnly ? t('section_context') : t('section_messages'), renderMessages(msgs), true, null, msgs.length + ' ' + t('badge_messages'))
      : '';
    const responseSection = respOutput?.content || contextOnly
      ? section(t('section_response'), renderResponseContent(respOutput, contextOnly), true)
      : '';
    const streamSection = streamEvents.length
      ? section(t('section_sse'), renderSSEEvents(streamEvents), false, null, streamEvents.length + ' ' + t('badge_events'))
      : '';
    const jsonSection = section(t('section_json'), `<div class="json-view">${renderJSONTree(e)}</div>`, false, JSON.stringify(e, null, 2));
    html += actionBarHtml;
    if (usage) html += renderTokenUsage(usage);
    html += toolsSection + systemSection + messagesSection + responseSection;
    if (streamEvents.length) html += streamSection;
    html += jsonSection;
  } else if (detailViewMode === 'trace') {
    html += renderTraceDetail(e, { reqBody, sysPrompt, msgs, tools, respOutput, contextOnly, streamEvents, usage });
  }

  d.innerHTML = html;
  bindSections(d);
  restoreSectionStates();
  if (globalSearchState.open && globalSearchState.query) {
    const target = getTargetForGlobalMatch(globalSearchState.currentMatch);
    const localIndex = target && target.entryKey === entryStableKey(e) ? target.localIndex : 0;
    applyGlobalSearchHighlights(localIndex);
  }
}

function renderTraceBlock(title, payload, badge = '') {
  const badgeHtml = badge ? `<span class="trace-badge">${esc(badge)}</span>` : '';
  const copyText = tracePayloadText(payload);
  const copyBtn = `<button class="trace-copy-btn" type="button" data-copy="${encodeCopyText(copyText)}">${t('copy')}</button>`;
  return `<div class="trace-block"><div class="trace-block-title"><span class="trace-title">${esc(title)}</span><span class="trace-actions">${badgeHtml}${copyBtn}</span></div>${renderTracePayload(payload)}</div>`;
}

function renderTraceDetail(entry, ctx) {
  const inputPayload = {
    system: ctx.sysPrompt || undefined,
    messages: ctx.msgs || [],
    tools: ctx.tools || [],
  };
  const responsePayload = {
    status: getResponseStatus(entry),
    output: ctx.respOutput?.content || [],
    usage: ctx.usage || {},
    body: getResponsePayload(entry) || {},
  };
  const metadata = {
    request_id: entry.request_id || '',
    display_turn: entry.display_turn ?? '',
    capture_turn: entry.capture_turn ?? entry.turn ?? '',
    turn: entry.turn ?? '',
    record_index: entry.record_index ?? '',
    websocket_response_index: entry.websocket_response_index ?? '',
    duration_ms: entry.duration_ms ?? 0,
    transport: entry.transport || 'http',
    upstream_base_url: entry.upstream_base_url || '',
    method: entry.request?.method || '',
    path: entry.request?.path || '',
    model: ctx.reqBody?.model || '',
    status: getResponseStatus(entry),
  };

  let html = renderTraceFormatControls();
  if (ctx.usage) html += renderTokenUsage(ctx.usage);
  html += '<div class="trace-grid">';
  html += renderTraceBlock(
    t('tok_input'),
    inputPayload,
    `${(ctx.msgs || []).length} ${t('badge_messages')}`
  );
  html += renderTraceBlock(
    t('tok_output'),
    responsePayload,
    `${getResponseStatus(entry)}`
  );
  if (ctx.streamEvents.length) {
    html += renderTraceBlock(
      t('section_sse'),
      ctx.streamEvents,
      `${ctx.streamEvents.length} ${t('badge_events')}`
    );
  }
  html += renderTraceBlock(
    t('section_metadata'),
    metadata
  );
  html += '</div>';
  return html;
}

function renderTracePayload(payload) {
  if (traceFormatMode === 'pretty') return `<div class="trace-pretty">${renderTracePrettyValue(payload)}</div>`;
  const text = traceFormatMode === 'yaml' ? toTraceYaml(payload) : JSON.stringify(payload, null, 2);
  return `<pre class="trace-code" data-format="${traceFormatMode}">${esc(text)}</pre>`;
}

function tracePayloadText(payload) {
  return traceFormatMode === 'yaml' ? toTraceYaml(payload) : JSON.stringify(payload, null, 2);
}

function isTraceScalar(value) {
  return value === null || value === undefined || typeof value !== 'object';
}

function yamlKey(key) {
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? key : JSON.stringify(key);
}

function yamlScalar(value, indent) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  const str = String(value);
  if (str === '') return '""';
  if (str.includes('\n')) {
    const pad = ' '.repeat(indent + 2);
    return `|\n${str.split('\n').map(line => pad + line).join('\n')}`;
  }
  if (/^[A-Za-z0-9_./:@+-]+$/.test(str) && !/^(true|false|null|yes|no|on|off)$/i.test(str)) return str;
  return JSON.stringify(str);
}

function toTraceYaml(value, indent = 0) {
  const pad = ' '.repeat(indent);
  if (isTraceScalar(value)) return pad + yamlScalar(value, indent);
  if (Array.isArray(value)) {
    if (!value.length) return pad + '[]';
    return value.map(item => {
      if (isTraceScalar(item)) return `${pad}- ${yamlScalar(item, indent)}`;
      return `${pad}-\n${toTraceYaml(item, indent + 2)}`;
    }).join('\n');
  }
  const keys = Object.keys(value).filter(key => value[key] !== undefined);
  if (!keys.length) return pad + '{}';
  return keys.map(key => {
    const item = value[key];
    if (isTraceScalar(item)) return `${pad}${yamlKey(key)}: ${yamlScalar(item, indent)}`;
    return `${pad}${yamlKey(key)}:\n${toTraceYaml(item, indent + 2)}`;
  }).join('\n');
}

function renderTracePrettyScalar(value) {
  if (value === null || value === undefined) return `<span class="trace-value">${value === undefined ? 'undefined' : 'null'}</span>`;
  if (typeof value === 'number' || typeof value === 'boolean') return `<span class="trace-value">${esc(String(value))}</span>`;
  const str = String(value);
  if (str.length > 90 || str.includes('\n')) return `<pre class="trace-string-block">${esc(str)}</pre>`;
  return `<span class="trace-value">${esc(JSON.stringify(str))}</span>`;
}

function renderTracePrettyValue(value, depth = 0) {
  if (isTraceScalar(value)) return renderTracePrettyScalar(value);
  if (Array.isArray(value)) {
    if (!value.length) return '<div class="trace-empty">[]</div>';
    return `<div class="trace-pretty-array">${value.map((item, index) => `<div class="trace-array-item"><span class="trace-index">#${index}</span>${renderTracePrettyValue(item, depth + 1)}</div>`).join('')}</div>`;
  }
  const keys = Object.keys(value).filter(key => value[key] !== undefined);
  if (!keys.length) return '<div class="trace-empty">{}</div>';
  return `<div class="trace-pretty-object">${keys.map(key => {
    const item = value[key];
    if (isTraceScalar(item)) return `<div class="trace-kv"><span class="trace-key">${esc(key)}</span>${renderTracePrettyScalar(item)}</div>`;
    return `<div class="trace-nested"><div class="trace-nested-title">${esc(key)}</div>${renderTracePrettyValue(item, depth + 1)}</div>`;
  }).join('')}</div>`;
}
