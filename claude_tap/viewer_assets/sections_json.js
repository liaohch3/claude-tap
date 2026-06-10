
/* ─── Section ─── */
function encodeCopyText(text) {
  return btoa(unescape(encodeURIComponent(text)));
}

function section(title, body, defaultOpen = true, copyText = null, badge = null) {
  const oc = defaultOpen ? 'open' : '', ac = defaultOpen ? 'chevron open' : 'chevron';
  let extra = '';
  if (badge) extra += `<span class="badge">${esc(badge)}</span>`;
  if (copyText !== null) extra += `<button class="copy-btn" data-copy="${encodeCopyText(copyText)}">${t('copy')}</button>`;
  return `<div class="section"><div class="section-header"><span class="${ac}">&#9654;</span><span class="title">${title}</span>${extra}</div><div class="section-body ${oc}">${body}</div></div>`;
}

function bindSections(container) {
  container.querySelectorAll('.section-header').forEach(h => {
    h.addEventListener('click', ev => {
      if (ev.target.classList.contains('copy-btn')) {
        const text = decodeURIComponent(escape(atob(ev.target.dataset.copy)));
        copyToClipboard(text, ev.target);
        return;
      }
      h.nextElementSibling.classList.toggle('open');
      h.querySelector('.chevron').classList.toggle('open');
    });
  });
  container.querySelectorAll('.trace-copy-btn').forEach(btn => {
    btn.addEventListener('click', ev => {
      ev.stopPropagation();
      const text = decodeURIComponent(escape(atob(btn.dataset.copy)));
      copyToClipboard(text, btn);
    });
  });
  container.querySelectorAll('.tool-block-header').forEach(h => {
    h.addEventListener('click', () => {
      h.nextElementSibling.classList.toggle('open');
      h.querySelector('.tb-arrow').classList.toggle('open');
    });
  });
  container.querySelectorAll('.tool-input-toggle').forEach(btn => {
    btn.addEventListener('click', ev => {
      ev.stopPropagation();
      const wrapper = btn.closest('.tool-input-readable');
      const view = wrapper?.querySelector('.tool-input-view');
      if (!view) return;
      const expanded = view.classList.toggle('expanded');
      const dataKey = expanded ? 'decoded' : 'raw';
      view.textContent = decodeURIComponent(escape(atob(view.dataset[dataKey] || '')));
      btn.textContent = expanded ? t('string_show_raw') : '↵';
      btn.title = expanded ? t('string_show_raw') : t('string_expand_escapes');
      btn.setAttribute('aria-label', expanded ? t('string_show_raw') : t('string_expand_escapes'));
      btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
  });
  container.querySelectorAll('.tool-input-copy').forEach(btn => {
    btn.addEventListener('click', ev => {
      ev.stopPropagation();
      const wrapper = btn.closest('.tool-input-readable');
      const view = wrapper?.querySelector('.tool-input-view');
      if (!view) return;
      const dataKey = view.classList.contains('expanded') ? 'decoded' : 'raw';
      const text = decodeURIComponent(escape(atob(view.dataset[dataKey] || '')));
      copyToClipboard(text, btn, '✓');
    });
  });
}

/* ─── JSON tree view ─── */
let _jtId = 0;

function renderJSONTree(obj, depth = 0) {
  if (depth > 50) return '<span class="json-punct">…</span>';

  if (obj === null) return '<span class="jnull">null</span>';
  if (typeof obj === 'boolean') return `<span class="jb">${obj}</span>`;
  if (typeof obj === 'number') return `<span class="jn">${obj}</span>`;
  if (typeof obj === 'string') return `<span class="js">${esc(JSON.stringify(obj))}</span>`;
  if (typeof obj !== 'object') return esc(String(obj));

  const isArray = Array.isArray(obj);
  const keys = isArray ? obj.map((_, i) => i) : Object.keys(obj);
  const len = keys.length;

  if (len === 0) return `<span class="json-punct">${isArray ? '[]' : '{}'}</span>`;

  const id = 'jt' + (_jtId++);
  const bracket = isArray ? '[' : '{';
  const closeBracket = isArray ? ']' : '}';
  const summary = `${len} ${isArray ? 'item' : 'key'}${len !== 1 ? 's' : ''}`;

  let items = '';
  keys.forEach((key, i) => {
    const comma = i < len - 1 ? '<span class="json-punct">,</span>' : '';
    const k = isArray ? '' : `<span class="jk">${esc(JSON.stringify(key))}</span><span class="json-punct">: </span>`;
    items += `<div class="jt-line">${k}${renderJSONTree(obj[key], depth + 1)}${comma}</div>`;
  });

  return `<span class="jt-toggle jt-open" onclick="toggleJT(event,'${id}')">▼</span>`
    + `<span class="json-punct">${bracket}</span>`
    + `<span class="jt-summary" id="${id}s">… ${summary}</span>`
    + `<div class="jt-children jt-open" id="${id}">${items}</div>`
    + `<div class="jt-close" id="${id}c"><span class="json-punct">${closeBracket}</span></div>`;
}

function toggleJT(event, id) {
  event.stopPropagation();
  const node = document.getElementById(id);
  const summary = document.getElementById(id + 's');
  const closeLine = document.getElementById(id + 'c');
  const toggle = event.currentTarget;
  const open = node.classList.contains('jt-open');
  if (open) {
    node.classList.remove('jt-open');
    toggle.classList.remove('jt-open');
    toggle.textContent = '▶';
    if (summary) summary.classList.add('jt-show');
    if (closeLine) closeLine.classList.add('jt-hidden');
  } else {
    node.classList.add('jt-open');
    toggle.classList.add('jt-open');
    toggle.textContent = '▼';
    if (summary) summary.classList.remove('jt-show');
    if (closeLine) closeLine.classList.remove('jt-hidden');
  }
}

function copyRequestBody(btn) {
  let e = filtered[activeIdx]; if (!e) return;
  e = resolveEntryForDetail(e);
  copyToClipboard(JSON.stringify(e.request?.body, null, 2), btn);
}
function copyCurl(btn) {
  let e = filtered[activeIdx]; if (!e) return;
  e = resolveEntryForDetail(e);
  const method = e.request?.method || 'POST', path = e.request?.path || '/v1/messages';
  const headers = e.request?.headers || {}, body = e.request?.body;
  const base = e.upstream_base_url || 'https://api.anthropic.com';
  let cmd = `curl -X ${method} '${base}${path}'`;
  for (const [k, v] of Object.entries(headers)) {
    const kl = k.toLowerCase();
    if (kl === 'host' || kl === 'content-length' || kl === 'accept-encoding') continue;
    cmd += ` \\\n  -H '${k}: ${v}'`;
  }
  if (body) cmd += ` \\\n  -d '${JSON.stringify(body).replace(/'/g, "'\\''")}'`;
  copyToClipboard(cmd, btn);
}
