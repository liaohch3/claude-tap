/* ─── i18n ─── */
const I18N = typeof __CLAUDE_TAP_I18N__ !== 'undefined' ? __CLAUDE_TAP_I18N__ : { en: {} };
function detectLang() {
  const supported = ['en','zh-CN','ja','ko','fr','ar','de','ru'];
  const nav = navigator.language || navigator.userLanguage || 'en';
  if (supported.includes(nav)) return nav;
  const prefix = nav.split('-')[0];
  if (prefix === 'zh') return 'zh-CN';
  const match = supported.find(s => s.startsWith(prefix));
  return match || 'en';
}
let currentLang = safeLocalStorageGet('claude-tap-lang') || detectLang();
function t(key) {
  const en = I18N.en || {};
  return (I18N[currentLang] || en)[key] || en[key] || key;
}
function formatText(key, values = {}) {
  return Object.entries(values).reduce((text, [name, value]) => {
    return text.replaceAll(`{${name}}`, String(value));
  }, t(key));
}
function setLang(lang) {
  currentLang = lang;
  safeLocalStorageSet('claude-tap-lang', lang);
  document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
  document.documentElement.lang = lang;
  $('#lang-select').value = lang;
  updateStaticTexts();
  if (filtered.length) { applyFilter(); selectEntry(activeIdx >= 0 ? activeIdx : 0); }
}
function updateStaticTexts() {
  const e = id => document.getElementById(id);
  if (e('logo-text')) e('logo-text').textContent = t('title');
  if (e('logo-version')) {
    const rawVersion = String(CLAUDE_TAP_VERSION || '').trim();
    if (rawVersion) {
      e('logo-version').textContent = rawVersion.startsWith('v') ? rawVersion : `v${rawVersion}`;
      e('logo-version').style.display = 'inline-flex';
    } else {
      e('logo-version').style.display = 'none';
    }
  }
  document.title = t('title') + ' Viewer';
  if (e('label-turns')) e('label-turns').textContent = t('stats_turns');
  const tokenLabel = e('label-tokens');
  if (tokenLabel) {
    tokenLabel.textContent = t('stats_tokens');
    if (tokenLabel.parentElement) {
      tokenLabel.parentElement.title = t('stats_tokens_hint');
      tokenLabel.parentElement.setAttribute('aria-label', t('stats_tokens_hint'));
    }
  }
  if (e('label-time')) e('label-time').textContent = t('stats_time');
  if (e('drop-title')) e('drop-title').textContent = t('drop_title');
  if (e('drop-desc')) e('drop-desc').textContent = t('drop_desc');
  if (e('drop-btn-label')) e('drop-btn-label').textContent = t('drop_btn');
  if (e('empty-trace-title')) e('empty-trace-title').textContent = t('empty_trace_title');
  if (e('empty-trace-desc')) e('empty-trace-desc').textContent = t('empty_trace_desc');
  if (e('empty-trace-count')) e('empty-trace-count').textContent = t('empty_trace_count');
  if (e('empty-trace-hint')) e('empty-trace-hint').textContent = t('empty_trace_hint');
  if (e('search-input')) e('search-input').placeholder = t('search_placeholder');
  if (e('date-label')) e('date-label').textContent = t('history_date');
  if (e('sidebar-sort-label')) e('sidebar-sort-label').textContent = t('sort_label');
  updateHistoryDeleteButton();
  updateSidebarSortControls();
  renderViewerActions();
}

function renderViewerActions() {
  const wrap = $('#viewer-actions');
  if (!wrap) return;
  const exports = TRACE_SESSION_EXPORTS && typeof TRACE_SESSION_EXPORTS === 'object' ? TRACE_SESSION_EXPORTS : {};
  const downloadIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>`;
  const links = [];
  if (typeof exports.jsonl === 'string' && exports.jsonl) {
    links.push(`<a class="export-menu-item" href="${esc(exports.jsonl)}" download>${esc(t('export_jsonl'))}</a>`);
  }
  if (typeof exports.compact === 'string' && exports.compact) {
    links.push(`<a class="export-menu-item" href="${esc(exports.compact)}" download>${esc(t('export_compact'))}</a>`);
  }
  if (typeof exports.log === 'string' && exports.log) {
    links.push(`<a class="export-menu-item" href="${esc(exports.log)}" download>${esc(t('export_log'))}</a>`);
  }
  if (typeof exports.html === 'string' && exports.html) {
    links.push(`<a class="export-menu-item" href="${esc(exports.html)}" download>${esc(t('export_html'))}</a>`);
  }
  if (!links.length) {
    wrap.innerHTML = '';
    wrap.style.display = 'none';
    return;
  }
  wrap.innerHTML = `<details class="export-menu">
    <summary class="viewer-action">${downloadIcon}${esc(t('export_menu'))}</summary>
    <div class="export-menu-list">${links.join('')}</div>
  </details>`;
  wrap.style.display = 'inline-flex';
}

/* ─── Theme ─── */
function initTheme() {
  const saved = safeLocalStorageGet('claude-tap-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = EMBED_QUERY_OPTIONS.theme || saved || (prefersDark ? 'dark' : 'light');
  applyTheme(theme);
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  safeLocalStorageSet('claude-tap-theme', next);
}
function applyTheme(theme) {
  if (theme === 'dark') {
    document.documentElement.dataset.theme = 'dark';
    $('#theme-toggle').textContent = '\u2600'; // sun
  } else {
    delete document.documentElement.dataset.theme;
    $('#theme-toggle').textContent = '\u263E'; // moon
  }
}

/* ─── Init ─── */
function initEmbedMode() {
  if (!EMBED_QUERY_OPTIONS.enabled) return;
  document.documentElement.dataset.embedMode = 'true';
  document.body.classList.add('embed-mode');
  if (EMBED_QUERY_OPTIONS.hideHeader) document.body.classList.add('embed-hide-header');
  if (EMBED_QUERY_OPTIONS.hidePath) document.body.classList.add('embed-hide-path');
  if (EMBED_QUERY_OPTIONS.hideHistory) document.body.classList.add('embed-hide-history');
  if (EMBED_QUERY_OPTIONS.hideControls) document.body.classList.add('embed-hide-controls');
  if (EMBED_QUERY_OPTIONS.compact) document.body.classList.add('embed-compact');
}

function initLang() {
  document.documentElement.dir = currentLang === 'ar' ? 'rtl' : 'ltr';
  document.documentElement.lang = currentLang;
  $('#lang-select').value = currentLang;
  updateStaticTexts();
}

function initCommonUi() {
  initEmbedMode();
  initTheme();
  initLang();
  initGlobalSearch();
}

function initFileDropZone() {
  const dropInner = $('#drop-inner'), fileInput = $('#file-input');
  if (!dropInner || !fileInput || dropInner.dataset.fileDropInitialized === 'true') return;
  dropInner.dataset.fileDropInitialized = 'true';
  ['dragover','dragenter'].forEach(e => dropInner.addEventListener(e, ev => {
    ev.preventDefault();
    dropInner.classList.add('dragover');
  }));
  ['dragleave','drop'].forEach(e => dropInner.addEventListener(e, () => dropInner.classList.remove('dragover')));
  dropInner.addEventListener('drop', ev => {
    ev.preventDefault();
    if (ev.dataTransfer.files.length) loadFile(ev.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) loadFile(fileInput.files[0]);
  });
}

function renderEmptyTraceState() {
  entries = [];
  filtered = [];
  activeIdx = -1;
  $('#sidebar-wrap').style.display = 'none';
  $('#detail').style.display = 'none';
  $('#stats').style.display = 'none';
  $('#path-filter').style.display = 'none';
  renderTracePathBar();
  const dropZone = $('#drop-zone');
  dropZone.style.display = 'flex';
  dropZone.innerHTML = `
    <div class="drop-zone-inner empty-trace-state" id="drop-inner" role="status" aria-live="polite">
      <h2 id="empty-trace-title">${esc(t('empty_trace_title'))}</h2>
      <p id="empty-trace-desc">${esc(t('empty_trace_desc'))}</p>
      <div class="empty-trace-meta">
        <span class="empty-trace-pill" id="empty-trace-count">${esc(t('empty_trace_count'))}</span>
        <span class="empty-trace-pill" id="empty-trace-hint">${esc(t('empty_trace_hint'))}</span>
      </div>
      <label for="file-input" id="drop-btn-label">${esc(t('drop_btn'))}</label>
      <input type="file" id="file-input" accept=".jsonl,.json,.ctap">
    </div>`;
  initFileDropZone();
}
