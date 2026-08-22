
/* ─── Model helpers ─── */
function modelPriority(m) {
  const l = m.toLowerCase();
  if (l.includes('opus')) return 0;
  if (l.includes('sonnet')) return 1;
  if (l.includes('haiku')) return 3;
  return 2;
}
function compareSidebarModelOrder(a, b) {
  const ma = a.request?.body?.model || 'unknown';
  const mb = b.request?.body?.model || 'unknown';
  const pa = modelPriority(ma), pb = modelPriority(mb);
  if (pa !== pb) return pa - pb;
  const modelDiff = ma.localeCompare(mb);
  if (modelDiff) return modelDiff;
  return compareTurns(captureTurnValue(a), captureTurnValue(b));
}
function modelColor(m) {
  const l = m.toLowerCase();
  if (l.includes('opus')) return 'var(--purple)';
  if (l.includes('sonnet')) return 'var(--blue)';
  if (l.includes('haiku')) return 'var(--green)';
  return 'var(--text-tertiary)';
}
function modelBadge(m) {
  const l = m.toLowerCase();
  if (l.includes('opus')) return { bg: 'var(--purple-bg)', fg: 'var(--purple)' };
  if (l.includes('sonnet')) return { bg: 'var(--blue-bg)', fg: 'var(--blue)' };
  if (l.includes('haiku')) return { bg: 'var(--green-bg)', fg: 'var(--green)' };
  return { bg: 'var(--bg)', fg: 'var(--text-tertiary)' };
}

function sessionTextSnippet(text, maxLen = 56) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxLen) return normalized;
  return normalized.slice(0, Math.max(0, maxLen - 3)).trimEnd() + '...';
}

function naturalTextFromPromptPayload(payload) {
  if (typeof payload === 'string') return cleanUserPromptText(payload);
  if (Array.isArray(payload)) {
    /* Human-first, the same rule preferredUserTextForMessage applies across
       separate content blocks. A decoded array is the same shape inside one
       block, so taking the first readable item let a leading injection title
       and badge the whole message while the question after it went unread:
       `[{"prompt":"Perform a web search for the query: pricing"},
         {"prompt":"What does that cost?"}]`. */
    let first = '';
    for (const item of payload) {
      const text = naturalTextFromPromptPayload(item);
      if (!text) continue;
      if (classifyUserInputOrigin(text).origin === 'human') return text;
      if (!first) first = text;
    }
    return first;
  }
  if (!payload || typeof payload !== 'object') return '';
  for (const key of ['prompt', 'request', 'instruction', 'message', 'query', 'text', 'title']) {
    if (typeof payload[key] !== 'string') continue;
    const text = cleanUserPromptText(payload[key]);
    if (text) return text;
  }
  if (payload.content !== undefined) return naturalTextForSessionContent(payload.content);
  return '';
}

/* ─── User input provenance ───
 *
 * The Anthropic/OpenAI wire format has exactly two roles, so every non-model
 * message arrives as `role: "user"` regardless of who actually wrote it. A CLI
 * harness uses that same channel for recap requests, next-prompt suggestions,
 * web-search dispatch, subagent briefs, and context-compaction handoffs, none
 * of which the human typed. Pasted files and diffs are a third case: the human
 * did supply them, but they are payload rather than speech.
 *
 * Provenance is inferred from content, so it is a judgement and not a fact the
 * wire format carries. Every pattern below is anchored to a fixed template
 * prefix that a harness emits verbatim, which is why matching is cheap and
 * false positives are rare; free-form prose is never claimed as harness text.
 * Verified against 89 distinct user texts across the local Claude sessions:
 * 36 harness, 31 payload, 22 prose.
 */
/* Wrapper tags a harness opens a user-role message with. Shared by the
   classifier and `cleanUserPromptText` so the two cannot disagree about which
   openers are injected: one blanking a message the other calls human prose
   costs the message its badge and can cost its turn a session title. */
const INJECTED_WRAPPER_TAGS = new Set([
  'additional_metadata',
  'artifacts',
  'codex_internal_context',
  'environment_context',
  'local-command-caveat',
  'session_context',
  'skills',
  'slash_commands',
  'subagents',
  'system-reminder',
  'user_information',
]);

/* Injected openers that are not tags. Kept beside the tag set for the same
   reason: `cleanUserPromptText` blanks each of these too. */
const INJECTED_TEXT_PREFIXES = [
  '# AGENTS.md instructions',
  '<INSTRUCTIONS>',
  '# Files mentioned by the user:',
];

/* Injected forms the cleaner blanks that are neither a wrapper tag nor a fixed
   prefix. This is the list `cleanUserPromptText` blanks on, so the classifier
   reads it too rather than restating the patterns: a form the cleaner discards
   but the classifier calls prose loses both its title and its badge, and the
   message renders as an empty human turn. */
const INJECTED_BLANK_PATTERNS = [
  { kind: 'attachment', re: /^<\/?image(_input)?(\s+[^>]*)?>$/i },
  { kind: 'suggestion', re: /^\[SUGGESTION MODE:/i },
  { kind: 'context', re: /^(web page content|page content|网页内容)\s*[:：]/i },
  { kind: 'attachment', re: /^\[Image:\s*(original|source)/i },
];

/* Text a content block carries as input: `text` when it says something, and
   `output` otherwise. Stopping at `text` merely because it is a string of the
   right type loses the content of the blocks that leave it empty and put the
   readable text under `output`, e.g.
   `{type: 'input_text', text: '', output: 'Perform a web search for...'}`.
   Losing it on one side only would change that message's title, badge and
   grouping as a capture crosses LAZY_THRESHOLD and the decision moves between
   the two mirrors. Mirrored by `_block_input_text` in viewer.py. */
function blockInputText(block) {
  const text = typeof block?.text === 'string' ? block.text : '';
  if (text.trim()) return text;
  return typeof block?.output === 'string' ? block.output : text;
}

function injectedWrapperTag(value) {
  const firstTag = value.match(/^<([A-Za-z_-]+)(?:\s|>)/);
  return firstTag && INJECTED_WRAPPER_TAGS.has(firstTag[1].toLowerCase()) ? firstTag[1].toLowerCase() : '';
}

const HARNESS_INPUT_PATTERNS = [
  { kind: 'reminder', re: /^<system-reminder>/i },
  /* The `(?=\s|>)` boundary is the one `injectedWrapperTag` enforces. A prefix-only
     match claimed any longer tag that happens to start the same way -- someone's own
     `<local-command-caveats>` or `<command-nameplate>` -- and since the cleaner keeps
     that text, the turn was displayed with a harness badge it had not earned. */
  { kind: 'reminder', re: /^<(?:local-command-caveat|command-(?:name|message|args))(?=\s|>)/i },
  { kind: 'reminder', re: /^Caveat: The messages below were generated/i },
  { kind: 'interrupt', re: /^\[Request interrupted/i },
  { kind: 'notification', re: /^\[SYSTEM NOTIFICATION - NOT USER INPUT\]/i },
  { kind: 'recap', re: /^The user stepped away and is coming back/i },
  { kind: 'suggestion', re: /^\[SUGGESTION MODE:/i },
  { kind: 'subagent', re: /^CRITICAL: Respond with TEXT ONLY/i },
  { kind: 'subagent', re: /^Briefly inform the user about the task result/i },
  /* `^Analyze if this message indicates` used to sit here. Unlike every other
     entry it is ordinary English, so a human asking "Analyze if this message
     indicates fraud or a billing mistake" was badged as harness-injected and lost
     its group title. The other openers earn their place by being unmistakable
     template text; this one only matched a template's opening words, and no
     installed CLI build carries the full wording to anchor a longer pattern
     against. A missed injection costs a badge; a false positive relabels what the
     user actually typed, so this stays out until the emitted text can be quoted
     from a capture. Mirrored in viewer.py's _HARNESS_PATTERNS. */
  { kind: 'compaction', re: /^This session is being continued from a previous conversation/i },
  { kind: 'websearch', re: /^Perform a web search for the query:/i },
  { kind: 'attachment', re: /^\[Image(\s*#\d+)?\]|^\[Image:\s*(original|source)/i },
];

/* Payload openers, not prose. Deliberately narrow: a line that merely contains
   braces or a keyword can still be someone asking a question about code, so
   each pattern has to match at the very start of the text.

   Identifiers and digits are spelled out instead of left to `\w` and `\d`.
   Those two escapes are ASCII-only here and Unicode-aware in the `viewer.py`
   mirror, so `def 处理():` would read as prose in the browser and as payload in
   lazy metadata -- the same paste changing its badge, title and grouping as a
   capture crosses LAZY_THRESHOLD. `\p{...}` is what the `u` flag buys. */
const PAYLOAD_INPUT_PATTERNS = [
  /^diff --git /,
  /^@@ -[0-9]+/,
  /^#!\/usr\/bin\/env /,
  /* An import is payload only when the statement ends where a source line would.
     `import pandas and plot the data` and `from the import list, drop numpy` are
     someone talking, and a prefix-only match would badge that prose as pasted. */
  /^import\s+[\p{L}\p{N}_.]+(?:\s+as\s+[\p{L}\p{N}_]+)?(?:\s*,\s*[\p{L}\p{N}_.]+(?:\s+as\s+[\p{L}\p{N}_]+)?)*[ \t]*(?:\r?\n|$)/u,
  /^from\s+[\p{L}\p{N}_.]+\s+import\s+(?:[(*]|[\p{L}\p{N}_.]+(?:\s+as\s+[\p{L}\p{N}_]+)?(?:\s*,\s*[\p{L}\p{N}_.]+(?:\s+as\s+[\p{L}\p{N}_]+)?)*)[ \t]*(?:\r?\n|$)/u,
  /* `__future__` is unmistakable, so this one needs no line boundary. */
  /^from __future__ import /,
  /^:root\s*\{/,
  /^\/\*[\s─=-]/,
  /^"""/,
  /* `async` is a prefix rather than its own alternative so it covers `async def`
     too; spelling out only `async function` left a pasted coroutine reading as
     prose while its sync form read as payload. */
  /^\s*(?:async\s+)?(?:function|const|let|var|class|def)\s+[\p{L}\p{N}_$]+\s*[({=]/u,
  /* The base-less Python form `class Foo:` needs the colon as a suffix, but a
     bare colon after keyword-plus-word also matches English: `class action: can
     I join the settlement?` and `function calls: why are they slow?` were badged
     as pasted code. So the colon forms are spelled out separately, each with the
     syntax a declaration carries and prose does not -- end of line for a class
     header, an annotation that goes on to assign or terminate for a binding. */
  /^\s*class\s+[\p{L}\p{N}_$]+\s*:[ \t]*(?:\r?\n|$)/u,
  /^\s*(?:const|let|var)\s+[\p{L}\p{N}_$]+\s*:[^\n]*?[=;]/u,
  /^\s*[0-9]+\t/,
];

/* Returns 'harness' | 'payload' | 'human' plus a kind for harness text.
   The caller decides what to do with each; this function never discards. */
function classifyUserInputOrigin(text) {
  const value = String(text || '').trim();
  if (!value) return { origin: 'human', kind: '' };
  for (const { kind, re } of HARNESS_INPUT_PATTERNS) {
    if (re.test(value)) return { origin: 'harness', kind };
  }
  if (injectedWrapperTag(value)) return { origin: 'harness', kind: 'context' };
  for (const prefix of INJECTED_TEXT_PREFIXES) {
    if (value.startsWith(prefix)) return { origin: 'harness', kind: 'context' };
  }
  for (const { kind, re } of INJECTED_BLANK_PATTERNS) {
    if (re.test(value)) return { origin: 'harness', kind };
  }
  for (const re of PAYLOAD_INPUT_PATTERNS) {
    if (re.test(value)) return { origin: 'payload', kind: '' };
  }
  return { origin: 'human', kind: '' };
}

function cleanUserPromptText(text) {
  let value = String(text || '').trim();
  if (!value) return '';
  if (value.length >= 2 && value[0] === '"' && value[value.length - 1] === '"') {
    try {
      const decoded = JSON.parse(value);
      if (typeof decoded === 'string' && decoded.trim()) value = decoded.trim();
    } catch (_) {
      // Keep the original text when it only looks JSON-quoted.
    }
  }
  if (/^[{[]/.test(value)) {
    try {
      const decoded = JSON.parse(value);
      const prompt = naturalTextFromPromptPayload(decoded);
      if (prompt) return prompt;
    } catch (_) {
      // Keep scanning the original text when it only looks like JSON.
    }
  }
  const userRequest = value.match(/<USER_REQUEST>\s*([\s\S]*?)\s*<\/USER_REQUEST>/i);
  if (userRequest) return userRequest[1].trim();
  const codexRequest = value.match(/^#+\s*My request for Codex:\s*([\s\S]*?)\s*$/im);
  if (codexRequest) return codexRequest[1].trim();
  const session = value.match(/^<session>\s*([\s\S]*?)\s*<\/session>$/i);
  if (session) {
    const unquoted = session[1].trim();
    const stripped = unquoted.replace(/^\[Image #\d+\]\s*/i, '').trim();
    return stripped || unquoted;
  }
  if (injectedWrapperTag(value)) return '';
  if (INJECTED_TEXT_PREFIXES.some(prefix => value.startsWith(prefix))) return '';
  if (INJECTED_BLANK_PATTERNS.some(({ re }) => re.test(value))) return '';
  return value.replace(/^\[Image #\d+\]\s*/i, '').trim();
}

function imageLookupKey(text) {
  let value = String(text || '').trim();
  const session = value.match(/^<session>\s*([\s\S]*?)\s*<\/session>$/i);
  if (session) value = session[1].trim();
  return value.replace(/^\[Image #\d+\]\s*/i, '').trim();
}

let sessionTooltipEl = null;
function sessionTooltip() {
  if (sessionTooltipEl) return sessionTooltipEl;
  sessionTooltipEl = document.createElement('div');
  sessionTooltipEl.id = 'session-full-input-tooltip';
  sessionTooltipEl.className = 'session-hover-tooltip';
  document.body.appendChild(sessionTooltipEl);
  return sessionTooltipEl;
}

function positionSessionTooltip(trigger, tooltip) {
  const rect = trigger.getBoundingClientRect();
  let left = rect.right + 10;
  let maxWidth = Math.min(520, window.innerWidth - left - 12);
  if (maxWidth < 240) {
    left = 12;
    maxWidth = Math.max(180, window.innerWidth - 24);
  }
  tooltip.style.width = maxWidth + 'px';
  tooltip.style.left = left + 'px';
  tooltip.style.top = '0px';
  const desiredTop = rect.top;
  const height = tooltip.offsetHeight || 0;
  const top = Math.max(12, Math.min(desiredTop, window.innerHeight - height - 12));
  tooltip.style.top = top + 'px';
}

/* Whether the clamp is actually hiding something, which the snippet length
   cannot tell us: `-webkit-line-clamp: 2` plus `overflow-wrap: anywhere` can cut
   an unbroken 48-character path that never earned the snippet's "..." once the
   badge and counters take their share of a 300px sidebar. */
function isTitleClipped(header) {
  const name = header?.querySelector?.('.group-name');
  if (!name) return false;
  return name.scrollHeight > name.clientHeight + 1 || name.scrollWidth > name.clientWidth + 1;
}

function showSessionTooltip(trigger) {
  const fullText = trigger?.dataset?.fullUserInput || '';
  if (!fullText) return;
  if (!trigger.dataset.snippetTruncated && !isTitleClipped(trigger)) return;
  const tooltip = sessionTooltip();
  tooltip.textContent = fullText;
  tooltip.classList.add('visible');
  trigger.setAttribute('aria-describedby', tooltip.id);
  positionSessionTooltip(trigger, tooltip);
}

function hideSessionTooltip(trigger) {
  if (!sessionTooltipEl) return;
  sessionTooltipEl.classList.remove('visible');
  if (trigger) trigger.removeAttribute('aria-describedby');
}

function bindSessionInputTooltip(header, fullText, snippet) {
  const original = String(fullText || '').trim();
  if (!original) return;
  header.dataset.fullUserInput = original;
  /* Bind whatever the snippet did: the clamp truncates titles the snippet left
     whole, and only layout knows which ones. `showSessionTooltip` re-checks at
     hover time, once the row has actually been measured. */
  if (snippet.endsWith('...')) header.dataset.snippetTruncated = '1';
  header.tabIndex = 0;
  header.addEventListener('mouseenter', () => showSessionTooltip(header));
  header.addEventListener('focus', () => showSessionTooltip(header));
  header.addEventListener('mouseleave', () => hideSessionTooltip(header));
  header.addEventListener('blur', () => hideSessionTooltip(header));
}

function naturalTextForSessionContent(content) {
  if (content === undefined || content === null) return '';
  if (typeof content === 'string') return cleanUserPromptText(content);
  if (!Array.isArray(content)) {
    if (typeof content === 'object') {
      if (content.type === 'tool_result' || content.type === 'function_call_output') return '';
      if (typeof content.text === 'string' || typeof content.output === 'string') {
        return cleanUserPromptText(blockInputText(content));
      }
      if (content.content !== undefined) return naturalTextForSessionContent(content.content);
    }
    return '';
  }
  let fallback = '';
  for (const block of content) {
    let text = '';
    if (typeof block === 'string') {
      text = block;
    } else if (block && typeof block === 'object') {
      if (block.type === 'tool_result' || block.type === 'function_call_output') continue;
      if (block.type === 'message') text = naturalTextForSessionContent(block.content);
      else text = blockInputText(block);
    }
    const prompt = cleanUserPromptText(text);
    if (!prompt) continue;
    if (classifyUserInputOrigin(prompt).origin === 'human') {
      return prompt;
    }
    if (!fallback) fallback = prompt;
  }
  return fallback;
}

/* Raw text of the blocks a user-role message actually carries as input, in wire
   order. Tool results are excluded: they are output the caller sent back, so
   counting them makes an injected message read as ordinary prose. */
function eligibleUserTextBlocks(content) {
  if (content === undefined || content === null) return [];
  if (typeof content === 'string') return [content];
  if (!Array.isArray(content)) {
    if (!content || typeof content !== 'object') return [];
    if (content.type === 'tool_result' || content.type === 'function_call_output') return [];
    if (typeof content.text === 'string' || typeof content.output === 'string') {
      return [blockInputText(content)];
    }
    if (content.content !== undefined) return eligibleUserTextBlocks(content.content);
    return [];
  }
  const texts = [];
  for (const block of content) {
    if (typeof block === 'string') {
      texts.push(block);
      continue;
    }
    if (!block || typeof block !== 'object') continue;
    if (block.type === 'tool_result' || block.type === 'function_call_output') continue;
    if (block.type === 'message') texts.push(...eligibleUserTextBlocks(block.content));
    else texts.push(blockInputText(block));
  }
  return texts.filter(text => String(text || '').trim());
}

/* Title and provenance for one user-role message, decided together from the same
   blocks so a sidebar title and a detail badge can never describe different text.
 *
 * Blocks are read in wire order and human prose wins, so a pasted diff followed by
 * a question is titled by the question.
 *
 * `text` and `origin` are deliberately independent. Cleaning blanks the injections
 * that carry no readable request -- a sidebar group headed `[SUGGESTION MODE:` is
 * noise, and such a turn belongs to the query it follows rather than starting one
 * (see #235) -- while injections that do say something, a recap or a web search,
 * keep their text and title their own group. Either way provenance is read off the
 * raw text, so the detail pane badges the injection even when it gives no title. */
function preferredUserTextForMessage(message) {
  let fallback = null;
  for (const raw of eligibleUserTextBlocks(message?.content)) {
    const value = String(raw || '').trim();
    if (!value) continue;
    const cleaned = cleanUserPromptText(raw);
    /* A blank cleaned result means cleaning stripped the block down to nothing:
       classify the raw text so the provenance survives, but leave the title empty. */
    const classified = classifyUserInputOrigin(cleaned || value);
    if (cleaned && classified.origin === 'human') return { text: cleaned, ...classified };
    /* Keep the first block's provenance, but do not let a badge-only injection
       lock in an empty title when a later block -- a pasted diff, a harness
       request that does say something -- survives cleaning. Such a turn would
       otherwise read as untitled and merge into the group before it. */
    if (!fallback) fallback = { text: cleaned, ...classified };
    else if (!fallback.text && cleaned) fallback = { ...fallback, text: cleaned };
  }
  return fallback || { text: '', origin: 'human', kind: '' };
}

function contentTextForSession(content) {
  if (content === undefined || content === null) return '';
  if (typeof content === 'string') return content.trim();
  if (!Array.isArray(content)) {
    if (typeof content === 'object') {
      if (typeof content.text === 'string') return content.text.trim();
      if (typeof content.output === 'string') return content.output.trim();
      if (content.content !== undefined) return contentTextForSession(content.content);
    }
    return '';
  }
  return content.map(block => {
    if (!block || typeof block !== 'object') return String(block || '').trim();
    if (block.type === 'text' || block.type === 'input_text' || block.type === 'output_text') return block.text || '';
    if (block.type === 'message') return contentTextForSession(block.content);
    if (block.type === 'thinking') return block.thinking || '';
    if (block.type === 'tool_use' || block.type === 'function_call') return `[${block.name || block.type}]`;
    if (block.type === 'tool_result') return contentTextForSession(block.content);
    if (block.type === 'function_call_output') return block.output || '';
    if (typeof block.text === 'string') return block.text;
    if (typeof block.output === 'string') return block.output;
    return '';
  }).filter(Boolean).join('\n').trim();
}

function isToolResultOnlyMessage(message) {
  const content = message?.content;
  if (!Array.isArray(content) || content.length === 0) return false;
  return content.every(block => block && typeof block === 'object'
    && (block.type === 'tool_result' || block.type === 'function_call_output'));
}

function latestUserInputText(entry) {
  return latestUserInputInfo(entry).userText;
}

const PROTOBUF_CONTENT_TYPE_TOKENS = [
  'application/proto',
  'application/x-protobuf',
  'application/connect+proto',
  'application/grpc',
];

function looksLikeBinaryText(text) {
  const sample = String(text || '').slice(0, 256);
  if (!sample) return false;
  if (sample.includes('\uFFFD')) return true;
  let control = 0;
  for (let i = 0; i < sample.length; i++) {
    const code = sample.charCodeAt(i);
    if (code < 32 && code !== 9 && code !== 10 && code !== 13) control += 1;
  }
  return control / sample.length >= 0.1;
}

function isProtobufNoiseEntry(entry) {
  if (entry?.transport === 'cursor-transcript') return false;
  const path = String(entry?.request?.path || '');
  if (path.startsWith('/cursor/transcript/')) return false;
  const headers = entry?.request?.headers || {};
  const contentType = String(headers['Content-Type'] || headers['content-type'] || '').toLowerCase();
  if (PROTOBUF_CONTENT_TYPE_TOKENS.some((token) => contentType.includes(token))) {
    return true;
  }
  const body = entry?.request?.body;
  if (body && typeof body === 'object' && (body._encoding === 'protobuf' || body._encoding === 'binary')) {
    return true;
  }
  return typeof body === 'string' && looksLikeBinaryText(body);
}

function stubSessionUserText(entry) {
  const text = String(entry?._session_user_text || '').trim();
  return text && !looksLikeBinaryText(text) ? text : '';
}

/* Provenance for a stub title. Python already decided this against the full
   message; re-reading the title text is a strictly worse guess, since the block
   the title came from is no longer visible here. Falls back to reading the text
   for stubs written before the origin was serialized. */
function stubSessionUserOrigin(entry, text) {
  const stored = String(entry?._session_user_origin || '').trim();
  if (stored) return stored;
  return classifyUserInputOrigin(text).origin || 'human';
}

/* Serializing the origin only helps if the scans read it before classifying.
   `buildStubEntry` reconstructs `body.messages` from the title, so both loops
   below find a message and return their own verdict on that bare string --
   reaching the stub fallback only when the title is empty, which is exactly when
   there is nothing to badge. Answer for a stub up front instead. */
function stubUserInputInfo(entry) {
  if (!entry?._isStub) return null;
  const text = stubSessionUserText(entry);
  if (!text) return null;
  const messageCount = Math.max(getMessages(entry?.request?.body).length, 1);
  return { userText: text, userIndex: 0, messageCount, origin: stubSessionUserOrigin(entry, text) };
}

function firstUserInputInfo(entry) {
  if (isProtobufNoiseEntry(entry)) {
    return { userText: '', userIndex: -1, messageCount: 0 };
  }
  const stub = stubUserInputInfo(entry);
  if (stub) return stub;
  const body = entry?.request?.body;
  if (typeof body === 'string') {
    const text = naturalTextForSessionContent(body);
    return text ? { userText: text, userIndex: 0, messageCount: 1 } : { userText: '', userIndex: -1, messageCount: 0 };
  }
  const msgs = getMessages(body);
  /* Two passes: a group is named after what the human asked, so prefer prose the
     human typed. Harness injections and pasted payloads still name the group
     when that is all the turn has, since an untitled group is worse than one
     titled by its only content. */
  let fallback = null;
  for (let i = 0; i < msgs.length; i++) {
    const message = msgs[i];
    if (message?.role !== 'user' || isToolResultOnlyMessage(message)) continue;
    const { text, origin } = preferredUserTextForMessage(message);
    if (!text || looksLikeBinaryText(text)) continue;
    if (origin === 'human') return { userText: text, userIndex: i, messageCount: msgs.length, origin };
    if (!fallback) fallback = { userText: text, userIndex: i, messageCount: msgs.length, origin };
  }
  if (fallback) return fallback;
  /* No stub branch here: `stubUserInputInfo` above already answered for every
     entry carrying a title, and only stubs carry one. */
  return { userText: '', userIndex: -1, messageCount: msgs.length, origin: 'human' };
}

function latestUserInputInfo(entry) {
  if (isProtobufNoiseEntry(entry)) {
    return { userText: '', userIndex: -1, messageCount: 0, origin: 'human' };
  }
  const stub = stubUserInputInfo(entry);
  if (stub) return stub;
  const msgs = getMessages(entry?.request?.body);
  /* Newest first, so a cumulative request carrying human turn A then human turn B
     is titled by B rather than by the oldest prompt in its history. A turn left with
     no title -- an injection carrying no readable request -- is passed over, which
     groups an injected-only follow-up under the query it follows (see #235). */
  for (let i = msgs.length - 1; i >= 0; i--) {
    const message = msgs[i];
    if (message?.role !== 'user' || isToolResultOnlyMessage(message)) continue;
    const { text, origin } = preferredUserTextForMessage(message);
    if (!text || looksLikeBinaryText(text)) continue;
    return { userText: text, userIndex: i, messageCount: msgs.length, origin };
  }
  /* As above: a stub was answered before this loop ran. */
  return { userText: '', userIndex: -1, messageCount: msgs.length, origin: 'human' };
}

function codexAppSessionInfo(entry) {
  const metadata = entry?.request?.body?.metadata || {};
  const headers = entry?.request?.headers || {};
  const sessionId = metadata.codex_app_session_id || headers['x-codex-app-session-id'] || '';
  if (!sessionId) return null;
  const first = firstUserInputInfo(entry);
  const userText = first.userText || entry._session_user_text || '';
  return {
    sessionId,
    userText,
    userIndex: first.userIndex,
    messageCount: first.messageCount,
    /* The stub fallback carries the provenance Python chose, the way
       `firstUserInputInfo` reads its own stub. */
    origin: first.userText ? first.origin : stubSessionUserOrigin(entry, userText),
  };
}

function finalResponseText(entry) {
  const output = getResponseOutput(entry);
  const outputText = contentTextForSession(output?.content);
  if (outputText) return outputText;
  const payload = getResponsePayload(entry);
  return contentTextForSession(payload?.output || payload?.content || payload?.message || payload);
}

function isContinuationWithoutUserInput(entry) {
  if (previousResponseIdForDiff(entry)) return true;
  const input = entry?.request?.body?.input;
  return Array.isArray(input) && input.length > 0 && input.every(isResponseToolResultItem);
}

function isTitleGenerationEntry(entry) {
  const sys = extractSystem(entry?.request?.body) || '';
  return /generate a concise/i.test(sys) && /single\s+"title"\s+field/i.test(sys);
}

function sessionRootTurn(entry) {
  const rootTurn = parseInt(String(captureTurnValue(entry) ?? '').split('.')[0], 10);
  return isNaN(rootTurn) ? null : rootTurn;
}

function isCursorTranscriptEntry(entry) {
  if (entry?.transport === 'cursor-transcript') return true;
  return String(entry?.request?.path || '').startsWith('/cursor/transcript/');
}

function cursorTurnOf(entry) {
  const turn = Number(entry?.request?.body?.cursor_turn);
  return Number.isFinite(turn) ? turn : null;
}

function shouldContinueSessionGroup(entry, info, currentGroup) {
  if (!currentGroup || !info.userText || currentGroup.userText !== info.userText) return false;
  if (isTitleGenerationEntry(entry)) return false;
  if (currentGroup.metadataOnly) return true;
  if (info.messageCount > 1 && info.userIndex === currentGroup.userIndex) return true;
  // Cursor transcript steps repeat the same single-user prompt across tool
  // steps within one cursor_turn; different user turns with identical text
  // (e.g. repeated "continue") must stay separate groups.
  if (isCursorTranscriptEntry(entry) && info.userIndex === currentGroup.userIndex) {
    const turn = cursorTurnOf(entry);
    if (turn != null && currentGroup.cursorTurn != null && turn !== currentGroup.cursorTurn) {
      return false;
    }
    return true;
  }
  return false;
}

/* The provenance travels with the title rather than being derived again from it.
   A turn whose first block is a cleaner-blanked harness block and whose title
   comes from a later pasted diff is harness-originated, but re-classifying the
   title alone reads that diff as `payload` -- so the detail badge would say
   reminder while the group header above it said pasted. */
function sessionKeyForEntry(entry, currentGroup) {
  const codexApp = codexAppSessionInfo(entry);
  const info = latestUserInputInfo(entry);
  const metadataOnly = isTitleGenerationEntry(entry);
  const rootTurn = sessionRootTurn(entry);
  if (codexApp) {
    const userText = info.userText || codexApp.userText;
    const userIndex = info.userText ? info.userIndex : codexApp.userIndex;
    const origin = info.userText ? info.origin : codexApp.origin;
    if (userText) {
      if (shouldContinueSessionGroup(entry, { ...info, userText, userIndex }, currentGroup)) {
        return { key: currentGroup.key, userText, userIndex, origin, metadataOnly, rootTurn };
      }
      return {
        key: 'codexapp-user:' + codexApp.sessionId + ':' + userIndex + ':' + userText,
        userText,
        userIndex,
        origin,
        metadataOnly,
        rootTurn,
      };
    }
    return {
      key: 'codexapp:' + codexApp.sessionId,
      userText: codexApp.userText,
      userIndex: codexApp.userIndex,
      origin: codexApp.origin,
      metadataOnly,
      rootTurn,
    };
  }
  if (info.userText) {
    if (shouldContinueSessionGroup(entry, info, currentGroup)) {
      return {
        key: currentGroup.key,
        userText: info.userText,
        userIndex: info.userIndex,
        origin: info.origin,
        metadataOnly,
        rootTurn,
      };
    }
    return {
      key: 'user:' + sessionTurnDiscriminator(entry) + ':' + info.userIndex + ':' + info.userText,
      userText: info.userText,
      userIndex: info.userIndex,
      origin: info.origin,
      metadataOnly,
      rootTurn,
    };
  }
  /* No title of its own, so no provenance to carry: the group keeps whatever the
     titling entry established. */
  if (currentGroup) {
    return { key: currentGroup.key, userText: '', userIndex: currentGroup.userIndex, metadataOnly, rootTurn };
  }
  const rootTurnText = String(captureTurnValue(entry) ?? '').split('.')[0];
  if (rootTurnText) return { key: 'turn:' + rootTurnText, userText: '', userIndex: -1, metadataOnly, rootTurn };
  return { key: 'request:' + (entry?.request_id || ''), userText: '', userIndex: -1, metadataOnly, rootTurn: null };
}

function sessionTurnDiscriminator(entry) {
  const rootTurn = String(captureTurnValue(entry) ?? '').split('.')[0];
  if (rootTurn) return 'turn:' + rootTurn;
  return 'request:' + (entry?.request_id || '');
}

function buildSessionGroups(items) {
  const groups = [];
  const groupsByKey = new Map();
  const titleGenerationItems = [];

  function canMergeNonContiguousSession(info) {
    return typeof info.key === 'string' && info.key.startsWith('codexapp:');
  }

  function createGroup(info, item) {
    const group = {
      key: info.key,
      userText: info.userText,
      userIndex: info.userIndex,
      origin: info.origin,
      metadataOnly: !!info.metadataOnly,
      rootTurn: info.rootTurn,
      cursorTurn: cursorTurnOf(item.entry),
      firstOrder: item.order,
      responseText: '',
      items: [],
    };
    groups.push(group);
    if (canMergeNonContiguousSession(info)) groupsByKey.set(info.key, group);
    return group;
  }

  function addItemToGroup(group, item, info) {
    if (!group.userText && info.userText) {
      group.userText = info.userText;
      group.userIndex = info.userIndex;
      /* The title and its provenance come from the same entry, so they move
         together -- otherwise a group titled later would keep the origin of the
         untitled entry that opened it. */
      group.origin = info.origin;
    }
    if (group.rootTurn == null && info.rootTurn != null) group.rootTurn = info.rootTurn;
    if (group.cursorTurn == null) group.cursorTurn = cursorTurnOf(item.entry);
    if (!info.metadataOnly) group.metadataOnly = false;
    const responseText = finalResponseText(item.entry);
    if (responseText) group.responseText = responseText;
    group.items.push(item);
  }

  function bestTitleGenerationGroup(item, info) {
    let best = null;
    groups.forEach(group => {
      if (info.userText && group.userText && info.userText !== group.userText) return;
      let distance;
      if (info.rootTurn != null && group.rootTurn != null) {
        distance = Math.abs(info.rootTurn - group.rootTurn);
        if (distance > 2) return;
      } else {
        distance = Math.abs(item.order - group.firstOrder);
      }
      const futurePenalty = item.order < group.firstOrder ? 0.25 : 0;
      const score = distance + futurePenalty;
      if (!best || score < best.score) best = { group, score };
    });
    return best ? best.group : null;
  }

  items.forEach((rawItem, order) => {
    const item = { ...rawItem, order };
    if (isTitleGenerationEntry(item.entry)) {
      titleGenerationItems.push(item);
      return;
    }
    const current = groups[groups.length - 1] || null;
    const info = sessionKeyForEntry(item.entry, current);
    let group = current && current.key === info.key ? current : null;
    if (!group && canMergeNonContiguousSession(info)) group = groupsByKey.get(info.key) || null;
    if (!group) group = createGroup(info, item);
    addItemToGroup(group, item, info);
  });

  titleGenerationItems.forEach(item => {
    const info = sessionKeyForEntry(item.entry, null);
    const group = bestTitleGenerationGroup(item, info) || createGroup(info, item);
    addItemToGroup(group, item, info);
  });

  groups.forEach(group => {
    group.items.sort((a, b) => a.order - b.order);
  });
  return groups;
}

function sessionGroupKey(group, groupIdx) {
  return 'session:' + groupIdx + ':' + group.key;
}

function sessionRowsForItems(items) {
  const rows = [];
  buildSessionGroups(items).forEach((group, groupIdx) => {
    const groupKey = sessionGroupKey(group, groupIdx);
    rows.push({ type: 'group', group, groupIdx, groupKey });
    if (!collapsedGroups.has(groupKey)) {
      group.items.forEach(item => rows.push({ type: 'entry', ...item }));
    }
  });
  return rows;
}

function sidebarItemsForMode() {
  const items = filtered.map((entry, idx) => ({ entry, idx }));
  if (sidebarOrderMode === 'model') return items.sort((a, b) => compareSidebarModelOrder(a.entry, b.entry)).map(item => ({ type: 'entry', ...item }));
  if (sidebarOrderMode === 'session') return sessionRowsForItems(items);
  return items.map(item => ({ type: 'entry', ...item }));
}

/* ─── Visual order helpers ─── */
function buildVisualOrder() {
  if (virtualMode) {
    visualOrder = vsFilteredItems.filter(item => item.type !== 'group').map(item => item.idx);
    return;
  }
  visualOrder = [];
  document.querySelectorAll('.sidebar-item').forEach(el => {
    // Skip items whose parent container is collapsed (display: none)
    if (el.parentElement && el.parentElement.style.display === 'none') return;
    visualOrder.push(parseInt(el.dataset.idx));
  });
}

function visualNavigate(delta) {
  if (!visualOrder.length) return;
  const pos = visualOrder.indexOf(activeIdx);
  if (pos === -1) { selectEntry(visualOrder[0]); return; }
  const next = Math.max(0, Math.min(pos + delta, visualOrder.length - 1));
  selectEntry(visualOrder[next]);
}

/* ─── Sidebar ─── */
const collapsedGroups = new Set(); // Track collapsed model groups

/* ─── Task type fingerprinting ─── */
const TASK_COLORS = [
  { color: 'var(--blue)',   bg: 'var(--blue-bg)' },
  { color: 'var(--green)',  bg: 'var(--green-bg)' },
  { color: 'var(--purple)', bg: 'var(--purple-bg)' },
  { color: 'var(--amber)',  bg: 'var(--amber-bg)' },
  { color: 'var(--cyan)',   bg: 'var(--cyan-bg)' },
  { color: 'var(--orange)', bg: 'var(--orange-bg)' },
  { color: 'var(--red)',    bg: 'var(--red-bg)' },
  { color: 'var(--indigo)', bg: 'var(--purple-bg)' },
];
const taskFingerprintCache = new Map();

function getTaskFingerprint(e) {
  const rid = e.request_id;
  if (taskFingerprintCache.has(rid)) return taskFingerprintCache.get(rid);
  const body = e.request?.body;
  if (!body) { taskFingerprintCache.set(rid, null); return null; }
  // Extract full system prompt text
  let sysText = '';
  if (typeof body.system === 'string') sysText = body.system;
  else if (Array.isArray(body.system)) {
    sysText = body.system.map(s => typeof s === 'string' ? s : (s.text || '')).join('\n');
  } else if (typeof body.instructions === 'string') {
    sysText = body.instructions;
  } else if (Array.isArray(body.messages)) {
    // OpenAI chat-completions schema: system prompt lives in messages[0..n] with role="system"
    sysText = body.messages
      .filter(m => m && m.role === 'system')
      .map(m => typeof m.content === 'string' ? m.content : (Array.isArray(m.content) ? m.content.map(c => c?.text || '').join('\n') : ''))
      .join('\n');
  }
  // Tool names sorted for stable fingerprint
  const requestTools = getRequestTools(body);
  const tools = requestTools.length ? requestTools : getRequestTools(getResponsePayload(e));
  const toolKey = tools.map(toolDisplayName).sort().join(',');
  // Build fingerprint from full system prompt + tool set
  const fp = sysText + '|' + toolKey;
  // Derive a short label from system prompt content
  const lower = sysText.toLowerCase();
  let label = '';
  if (!sysText && !tools.length) label = 'simple';
  // Self-identification phrases ("You are X") win over generic substring matches,
  // since other agents may mention "Claude Code" inside their own prompt body.
  else if (lower.includes('you are opencode')) label = 'OpenCode';
  else if (lower.includes('you are hermes')) label = 'Hermes';
  else if (lower.includes('you are codex')) label = 'Codex';
  else if (lower.includes('operating inside pi') || lower.includes('pi, a coding agent harness')) label = 'Pi';
  else if (lower.includes('claude code')) label = 'Claude Code';
  else if (lower.includes('claude agent')) label = 'Claude Agent';
  else if (lower.includes('openclaw')) label = 'OpenClaw';
  else if (lower.includes('subagent') || lower.includes('sub-agent')) label = 'Subagent';
  else if (lower.includes('bash')) label = 'Bash';
  else if (lower.includes('explore')) label = 'Explore';
  else if (lower.includes('plan')) label = 'Plan';
  else if (sysText) {
    const firstLine = sysText.split('\n').find(l => {
      const line = l.trim().toLowerCase();
      return line && !line.startsWith('x-anthropic-billing-header:');
    }) || '';
    label = firstLine.slice(0, 20).trim() || (tools.length + ' tools');
  } else {
    label = tools.length + ' tools';
  }
  const result = { fp, label };
  taskFingerprintCache.set(rid, result);
  return result;
}

function getTaskColor(fp) {
  if (!fp) return TASK_COLORS[0];
  // Hash with sampling for long strings (system prompts can be very large)
  let hash = 0;
  const step = fp.length > 1000 ? Math.floor(fp.length / 500) : 1;
  for (let i = 0; i < fp.length; i += step) hash = ((hash << 5) - hash + fp.charCodeAt(i)) | 0;
  hash = ((hash << 5) - hash + fp.length) | 0; // include length for extra differentiation
  return TASK_COLORS[Math.abs(hash) % TASK_COLORS.length];
}

function createSidebarItem(e, i) {
  const item = document.createElement('div');
  const statusCode = getResponseStatus(e);
  const failed = statusCode >= 400;
  item.className = 'sidebar-item' + (failed ? ' is-error' : '');
  item.dataset.idx = i;
  const u = getUsage(e);
  const inTok = u?.input_tokens || 0, outTok = u?.output_tokens || 0;
  const model = e.request?.body?.model || '';
  const shortModel = model.replace(/^claude-/, '').replace(/-\d{8}$/, '');
  const badge = modelBadge(model);
  const timeStr = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '';
  // Task type coloring
  const taskInfo = getTaskFingerprint(e);
  const taskColor = taskInfo ? getTaskColor(taskInfo.fp) : TASK_COLORS[0];
  item.style.borderLeftColor = failed ? 'var(--red)' : taskColor.color;
  const taskBadgeHtml = taskInfo && taskInfo.label ? `<span class="si-task" style="background:${taskColor.bg};color:${taskColor.color}" title="${esc(taskInfo.label)}">${esc(taskInfo.label)}</span>` : '';
  const errorDot = failed ? `<span class="si-error-dot" title="HTTP ${statusCode}"></span>` : '';
  item.innerHTML = `
    <div class="si-row1">
      <span class="si-turn-wrap"><span class="si-turn">${t('turn')} ${displayTurnLabel(e)}</span>${errorDot}</span>
      ${taskBadgeHtml}
      <span class="si-model" style="background:${badge.bg};color:${badge.fg}">${esc(shortModel)}</span>
    </div>
    <div class="si-row2">
      <span class="si-tok">${(inTok + outTok).toLocaleString()} ${t('tok')}</span>
      <span class="si-dur">${fmtDuration(e.duration_ms || 0)}</span>
      <span class="si-time">${esc(timeStr)}</span>
    </div>
    <div class="si-path">${esc((e.request?.method || '') + ' ' + (e.request?.path || ''))}</div>`;
  item.onclick = () => selectEntry(i);
  return item;
}

function createSessionGroupHeader(group, groupIdx, groupKey, onToggle) {
  const firstEntry = group.items[0]?.entry;
  const taskInfo = firstEntry ? getTaskFingerprint(firstEntry) : null;
  const taskColor = taskInfo ? getTaskColor(taskInfo.fp) : TASK_COLORS[groupIdx % TASK_COLORS.length];
  const isCollapsed = collapsedGroups.has(groupKey);
  const label = sessionTextSnippet(group.userText, 48);
  /* The title falls back to harness or pasted text only when the turn has no
     human prose. Mark that case so the reader does not read an injected recap
     request as something they typed. The origin travels with the title from
     `preferredUserTextForMessage`; re-deriving it from the title alone would
     disagree with the detail badge whenever a harness turn is titled by a later
     block, such as a cleaner-blanked reminder followed by a pasted diff. */
  const origin = group.origin || classifyUserInputOrigin(group.userText).origin;
  const originTag = origin === 'human'
    ? ''
    : `<span class="group-origin origin-${origin}" title="${esc(t(origin === 'harness' ? 'origin_harness_hint' : 'origin_payload_hint'))}">${esc(t(origin === 'harness' ? 'origin_harness' : 'origin_payload'))}</span>`;
  const header = document.createElement('div');
  header.className = 'sidebar-group-header';
  header.innerHTML = `<span class="group-dot" style="background:${taskColor.color}"></span><span class="group-name">${esc(t('sort_session'))} ${groupIdx + 1}${label ? ' - ' + esc(label) : ''}</span>${originTag}<span class="group-count">${group.items.length}</span><span class="group-chevron${isCollapsed ? '' : ' open'}">&#9654;</span>`;
  bindSessionInputTooltip(header, group.userText, label);
  header.onclick = () => {
    hideSessionTooltip(header);
    onToggle(header);
  };
  return header;
}

function renderSidebar(preserveDetail) {
  const sb = $('#sidebar');
  const prevScrollTop = sb.scrollTop;
  updateSidebarSortControls();

  // Use virtual scroll for large filtered sets
  if (filtered.length > LAZY_THRESHOLD) {
    virtualMode = true;
    sb.classList.add('virtual-scroll');
    vsFilteredItems = sidebarItemsForMode();
    buildVisualOrder();
    vsInitSidebar(sb, prevScrollTop, preserveDetail);
    return;
  }

  // Standard DOM-based sidebar for small traces
  virtualMode = false;
  sb.classList.remove('virtual-scroll');
  sb.innerHTML = '';
  if (sidebarOrderMode === 'turn') {
    let lastTs = null;
    filtered.forEach((e, i) => {
      const gap = _timeGapHtml(e, lastTs);
      if (gap) sb.insertAdjacentHTML('beforeend', gap);
      lastTs = e.timestamp ? new Date(e.timestamp).getTime() : null;
      sb.appendChild(createSidebarItem(e, i));
    });
    buildVisualOrder();
    _restoreSelection(preserveDetail);
    if (preserveDetail) sb.scrollTop = prevScrollTop;
    return;
  }

  if (sidebarOrderMode === 'session') {
    const groups = buildSessionGroups(filtered.map((entry, idx) => ({ entry, idx })));
    groups.forEach((group, groupIdx) => {
      const groupKey = sessionGroupKey(group, groupIdx);
      const isCollapsed = collapsedGroups.has(groupKey);
      const content = document.createElement('div');
      if (isCollapsed) content.style.display = 'none';
      group.items.forEach(({ entry: e, idx: i }) => content.appendChild(createSidebarItem(e, i)));
      const header = createSessionGroupHeader(group, groupIdx, groupKey, headerEl => {
        const nowCollapsed = content.style.display !== 'none';
        content.style.display = nowCollapsed ? 'none' : '';
        headerEl.querySelector('.group-chevron').classList.toggle('open');
        if (nowCollapsed) collapsedGroups.add(groupKey); else collapsedGroups.delete(groupKey);
        buildVisualOrder();
        updateMobileNav();
      });
      sb.appendChild(header);
      sb.appendChild(content);
    });
    buildVisualOrder();
    _restoreSelection(preserveDetail);
    if (preserveDetail) sb.scrollTop = prevScrollTop;
    return;
  }

  const groups = new Map();
  filtered.forEach((e, i) => {
    const model = e.request?.body?.model || 'unknown';
    if (!groups.has(model)) groups.set(model, []);
    groups.get(model).push({ entry: e, idx: i });
  });
  const sorted = [...groups.keys()].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  if (sorted.length <= 1) {
    // Insert time gap indicators between entries
    let lastTs = null;
    filtered.forEach((e, i) => {
      const gap = _timeGapHtml(e, lastTs);
      if (gap) sb.insertAdjacentHTML('beforeend', gap);
      lastTs = e.timestamp ? new Date(e.timestamp).getTime() : null;
      sb.appendChild(createSidebarItem(e, i));
    });
  } else {
    sorted.forEach(model => {
      const items = groups.get(model);
      const shortModel = model.replace(/^claude-/, '').replace(/-\d{8}$/, '');
      const color = modelColor(model);
      const isCollapsed = collapsedGroups.has(model);
      const header = document.createElement('div');
      header.className = 'sidebar-group-header';
      header.innerHTML = `<span class="group-dot" style="background:${color}"></span><span class="group-name">${esc(shortModel)}</span><span class="group-count">${items.length}</span><span class="group-chevron${isCollapsed ? '' : ' open'}">&#9654;</span>`;
      const content = document.createElement('div');
      if (isCollapsed) content.style.display = 'none';
      items.forEach(({ entry: e, idx: i }) => content.appendChild(createSidebarItem(e, i)));
      header.onclick = () => {
        const nowCollapsed = content.style.display !== 'none';
        content.style.display = nowCollapsed ? 'none' : '';
        header.querySelector('.group-chevron').classList.toggle('open');
        if (nowCollapsed) collapsedGroups.add(model); else collapsedGroups.delete(model);
        buildVisualOrder();
        updateMobileNav();
      };
      sb.appendChild(header);
      sb.appendChild(content);
    });
  }
  buildVisualOrder();
  _restoreSelection(preserveDetail);
  if (preserveDetail) sb.scrollTop = prevScrollTop;
}

function _restoreSelection(preserveDetail) {
  let restoredIdx = -1;
  if (preserveDetail && currentDetailEntryKey) {
    restoredIdx = filtered.findIndex(e => entryStableKey(e) === currentDetailEntryKey);
  }
  if (restoredIdx < 0 && preserveDetail && currentDetailRequestId) {
    restoredIdx = filtered.findIndex(e => e.request_id === currentDetailRequestId);
  }
  if (restoredIdx >= 0) {
    selectEntry(restoredIdx, { force: false });
  } else if (activeIdx >= 0 && activeIdx < filtered.length) {
    selectEntry(activeIdx, { force: !preserveDetail });
  } else if (filtered.length) {
    let defaultIdx = 0;
    for (let i = 0; i < filtered.length; i++) {
      const m = (filtered[i].request?.body?.model || '').toLowerCase();
      if (m.includes('opus') || m.includes('sonnet')) { defaultIdx = i; break; }
    }
    selectEntry(defaultIdx);
  } else {
    activeIdx = -1; $('#detail').innerHTML = `<div class="empty-state">${t('empty_state')}</div>`;
  }
}

/* ─── Time gap indicator ─── */
function _timeGapHtml(entry, lastTs) {
  if (!lastTs || !entry.timestamp) return '';
  const ts = new Date(entry.timestamp).getTime();
  const gapMs = ts - lastTs;
  if (gapMs < 60000) return ''; // less than 1 min
  let label;
  if (gapMs < 3600000) label = Math.round(gapMs / 60000) + ' min gap';
  else label = (gapMs / 3600000).toFixed(1) + ' hr gap';
  return `<div class="time-gap"><span class="tg-line"></span>${label}<span class="tg-line"></span></div>`;
}

/* ─── Position indicator ─── */
function updatePositionIndicator() {
  const pi = $('#position-indicator');
  if (!pi) return;
  if (!filtered.length || activeIdx < 0) {
    pi.style.display = 'none';
    return;
  }
  pi.style.display = 'flex';
  const entry = filtered[activeIdx];
  const turnNum = displayTurnLabel(entry);
  const pos = activeIdx + 1;
  const total = filtered.length;
  const pct = total > 1 ? ((pos - 1) / (total - 1)) * 100 : 0;
  $('#pi-current').textContent = turnNum;
  $('#pi-total').textContent = `of ${total}`;
  $('#pi-fill').style.width = pct + '%';
}

/* ─── Virtual scroll ─── */
let _vsRafPending = false;

function vsInitSidebar(sb, prevScrollTop, preserveDetail) {
  sb.innerHTML = '';
  const spacer = document.createElement('div');
  spacer.className = 'vs-spacer';
  spacer.style.height = (vsFilteredItems.length * VS_ITEM_HEIGHT) + 'px';
  sb.appendChild(spacer);

  // Throttled scroll handler
  sb.onscroll = () => {
    if (!_vsRafPending) {
      _vsRafPending = true;
      requestAnimationFrame(() => {
        _vsRafPending = false;
        vsRenderVisible();
      });
    }
  };

  if (preserveDetail) sb.scrollTop = prevScrollTop;
  vsRenderVisible();
  _restoreSelection(preserveDetail);
}

function vsRenderVisible() {
  const sb = $('#sidebar');
  if (!sb) return;
  const spacer = sb.querySelector('.vs-spacer');
  if (!spacer) return;

  const scrollTop = sb.scrollTop;
  const viewHeight = sb.clientHeight;
  const startIdx = Math.max(0, Math.floor(scrollTop / VS_ITEM_HEIGHT) - VS_BUFFER);
  const endIdx = Math.min(vsFilteredItems.length, Math.ceil((scrollTop + viewHeight) / VS_ITEM_HEIGHT) + VS_BUFFER);

  spacer.querySelectorAll('.sidebar-item, .sidebar-group-header').forEach(el => el.remove());

  for (let i = startIdx; i < endIdx; i++) {
    const row = vsFilteredItems[i];
    let item;
    if (row.type === 'group') {
      item = createSessionGroupHeader(row.group, row.groupIdx, row.groupKey, () => {
        if (collapsedGroups.has(row.groupKey)) collapsedGroups.delete(row.groupKey);
        else collapsedGroups.add(row.groupKey);
        renderSidebar(true);
        updateMobileNav();
      });
    } else {
      const { entry, idx } = row;
      item = createSidebarItem(entry, idx);
      item.classList.toggle('active', idx === activeIdx);
    }
    item.style.position = 'absolute';
    item.style.left = '0';
    item.style.right = '0';
    item.style.top = (i * VS_ITEM_HEIGHT) + 'px';
    item.style.height = VS_ITEM_HEIGHT + 'px';
    spacer.appendChild(item);
  }
}

function vsScrollToIdx(idx) {
  const sb = $('#sidebar');
  if (!sb || !virtualMode) return;
  const pos = vsFilteredItems.findIndex(item => item.idx === idx);
  if (pos < 0) return;
  const itemTop = pos * VS_ITEM_HEIGHT;
  const itemBottom = itemTop + VS_ITEM_HEIGHT;
  if (itemTop < sb.scrollTop) {
    sb.scrollTop = itemTop;
  } else if (itemBottom > sb.scrollTop + sb.clientHeight) {
    sb.scrollTop = itemBottom - sb.clientHeight;
  }
}

function selectEntry(idx, opts) {
  if (idx < 0 || idx >= filtered.length) return;
  const force = !opts || opts.force !== false;
  const entry = filtered[idx];
  const entryKey = entryStableKey(entry);
  activeIdx = idx;
  if (virtualMode) {
    vsRenderVisible();
  } else {
    document.querySelectorAll('.sidebar-item').forEach(el => {
      el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
    });
  }
  // Skip re-render if same entry and force=false (preserves scroll position in live mode)
  if (!force && currentDetailEntryKey === entryKey) {
    // Just update sidebar highlight, keep detail as-is
  } else {
    renderDetailForEntry(entry);
  }
  if (virtualMode) {
    vsScrollToIdx(idx);
  } else {
    const active = document.querySelector('.sidebar-item.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }
  updatePositionIndicator();
  mobileShowDetail();
  updateMobileNav();
}
