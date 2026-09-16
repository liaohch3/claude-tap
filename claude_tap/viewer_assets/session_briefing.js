/* ─── Session briefing ───
   Python wrote EMBEDDED_SESSION_BRIEFING. This file only formats that object
   and jumps to the named turn. It does not recompute cost, cache, or sizes. */

function sessionBriefingPayload() {
  return (typeof EMBEDDED_SESSION_BRIEFING !== 'undefined' && EMBEDDED_SESSION_BRIEFING
    && typeof EMBEDDED_SESSION_BRIEFING === 'object')
    ? EMBEDDED_SESSION_BRIEFING
    : null;
}

function briefingTurnButton(turn) {
  return `<button type="button" class="briefing-turn" data-turn="${esc(String(turn))}">Turn ${esc(String(turn))}</button>`;
}

function briefingMoney(usd) {
  const amount = Number(usd);
  if (!Number.isFinite(amount)) return '';
  return `$${amount.toFixed(2)}`;
}

function briefingHasContent(payload) {
  if (!payload) return false;
  const cost = payload.cost || {};
  const cache = payload.cache || {};
  const tools = Array.isArray(payload.tool_results) ? payload.tool_results : [];
  return cost.usd != null || cost.unpriced === true || cache.break_turn != null || tools.length > 0;
}

function briefingCostLine(cost) {
  if (!cost) return '';
  if (cost.unpriced === true) {
    return `<div class="session-briefing-line"><span class="briefing-dot cost"></span>${esc(t('briefing_cost_unpriced'))}</div>`;
  }
  if (cost.usd == null) return '';
  const money = briefingMoney(cost.usd) + (cost.partial === true ? '+' : '');
  return `<div class="session-briefing-line"><span class="briefing-dot cost"></span>${
    formatText('briefing_cost', { cost: money })
  }</div>`;
}

/* A cold write only earns the "miss from here on" wording when no later turn
   reads from cache again. Otherwise it is a one-off rebuild and gets said so. */
function briefingCacheLine(cache) {
  if (!cache || cache.break_turn == null) return '';
  const turn = briefingTurnButton(cache.break_turn);
  const sustained = cache.sustained === true;
  const named = cache.reason && t(cache.reason) !== cache.reason;
  if (named) {
    const key = sustained ? 'briefing_cache_reason' : 'briefing_cache_rebuild_reason';
    return `<div class="session-briefing-line"><span class="briefing-dot cache"></span>${
      formatText(key, { turn, reason: t(cache.reason) })
    }</div>`;
  }
  const key = sustained ? 'briefing_cache' : 'briefing_cache_rebuild';
  return `<div class="session-briefing-line"><span class="briefing-dot cache"></span>${
    formatText(key, { turn })
  }</div>`;
}

function briefingToolsLine(tools) {
  if (!tools.length) return '';
  const items = tools.map(item => {
    const label = `${item.name || 'tool'} ${item.size_kb} KB`;
    return item.turn != null
      ? `<button type="button" class="briefing-turn" data-turn="${esc(String(item.turn))}">${esc(label)}</button>`
      : esc(label);
  }).join(' · ');
  return `<div class="session-briefing-line"><span class="briefing-dot tools"></span>${
    formatText('briefing_tools', { items })
  }</div>`;
}

function jumpToBriefingTurn(turn) {
  const wanted = Number(turn);
  if (!Number.isFinite(wanted)) return;
  const pool = (typeof entries !== 'undefined' && Array.isArray(entries)) ? entries : [];
  const entry = pool.find(item => Number(item && item.turn) === wanted);
  if (!entry) return;
  const visible = (typeof filtered !== 'undefined' && Array.isArray(filtered)) ? filtered : pool;
  const idx = visible.indexOf(entry);
  if (idx >= 0 && typeof selectEntry === 'function') selectEntry(idx);
  else if (typeof renderDetailForEntry === 'function') renderDetailForEntry(entry);
}

function renderSessionBriefing() {
  const host = document.getElementById('session-briefing');
  if (!host) return;
  const payload = sessionBriefingPayload();
  if (!briefingHasContent(payload) || document.body.classList.contains('embed-hide-header')) {
    host.hidden = true;
    host.classList.remove('is-visible');
    host.innerHTML = '';
    return;
  }
  const costLine = briefingCostLine(payload.cost);
  const cacheLine = briefingCacheLine(payload.cache);
  const toolsLine = briefingToolsLine(Array.isArray(payload.tool_results) ? payload.tool_results : []);
  host.innerHTML = `<div class="session-briefing-head">`
    + `<span class="session-briefing-title">${esc(t('briefing_title'))}</span>`
    + `<span class="session-briefing-meta">${esc(t('briefing_meta'))}</span>`
    + `</div>${costLine}${cacheLine}${toolsLine}`;
  host.querySelectorAll('.briefing-turn').forEach(button => {
    button.addEventListener('click', () => jumpToBriefingTurn(button.dataset.turn));
  });
  host.hidden = false;
  host.classList.add('is-visible');
}
