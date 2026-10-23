"use strict";

const CHUNK_SIZE = 8;
const STALE_HOURS = 20;
const CRITERIA = ["base", "weekly_bbw", "daily_bbw", "rs", "contraction", "overhead", "eps"];
const VERDICTS = { candidate: "Candidate", manual: "Check manually", reject: "Reject", error: "Error" };
const VERDICT_ORDER = { candidate: 0, manual: 1, reject: 2, error: 3 };
const ICONS = { pass: "✓", warn: "!", fail: "✕", na: "–" };
const STATUS_TEXT = { pass: "Pass", warn: "Borderline", fail: "Fail", na: "n/a" };

const $ = (sel, root = document) => root.querySelector(sel);
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

const state = {
  config: null,
  password: store.get("bpd.password", ""),
  lists: [],
  tickers: new Map(),
  thresholds: {},
  current: store.get("bpd.current", "all"),
  view: store.get("bpd.view", "board"),
  filter: { verdict: null, changed: false, mismatch: false, doubt: false, search: "" },
  sort: { key: "phase", dir: 1 },
  drawer: null,
  details: new Map(),
  busy: false,
  importSheets: [],
};

let PHASES = [];
let PHASE_HELP = {};

// ================================================================ API

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.password) headers["X-App-Password"] = state.password;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.json) };
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    askPassword();
    throw new Error("Password required");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function askPassword(msg) {
  const dlg = $("#auth-dialog");
  $("#auth-error").hidden = !msg;
  $("#auth-error").textContent = msg || "";
  if (!dlg.open) dlg.showModal();
  $("#password").focus();
}

// ================================================================ state helpers

function phaseClass(p) {
  return p ? "ph-" + p.replace(/\+/g, "p").replace(/-/g, "m") : "none";
}
function phaseBadge(p, extraClass = "") {
  return p ? `<span class="ph ${phaseClass(p)} ${extraClass}">${escapeHtml(p)}</span>` : `<span class="ph none ${extraClass}">–</span>`;
}

function rowsInView() {
  const rows = [...state.tickers.values()];
  if (state.current === "all") return rows;
  const id = Number(state.current);
  return rows.filter((r) => r.lists.includes(id));
}

function isChanged(r) {
  if (!r.prev_phase || !r.phase_changed_at) return false;
  return Date.now() - new Date(r.phase_changed_at).getTime() < 8 * 86400e3;
}
function isStale(r) {
  return !r.evaluated_at || Date.now() - new Date(r.evaluated_at).getTime() > STALE_HOURS * 3600e3;
}
function mismatch(r) {
  return r.manual_phase && r.phase && r.manual_phase !== r.phase;
}
function doubtful(r) {
  return (r.summary?.phase?.doubts || []).length > 0;
}

function filteredRows() {
  const q = state.filter.search.trim().toLowerCase();
  return rowsInView().filter((r) => {
    if (state.filter.verdict && r.summary?.verdict !== state.filter.verdict) return false;
    if (state.filter.changed && !isChanged(r)) return false;
    if (state.filter.mismatch && !mismatch(r)) return false;
    if (state.filter.doubt && !doubtful(r)) return false;
    if (q) {
      const hay = [r.symbol, r.name, r.industry, r.note, r.tags].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function upsertRows(rows) {
  for (const r of rows) if (r) state.tickers.set(r.symbol, r);
}

// ================================================================ loading

async function loadState() {
  const data = await api("/api/state");
  state.lists = data.lists;
  state.thresholds = data.thresholds || {};
  state.tickers = new Map(data.tickers.map((r) => [r.symbol, r]));
  if (state.current !== "all" && !state.lists.some((l) => String(l.id) === String(state.current))) state.current = "all";
  render();
}

// ================================================================ render

function render() {
  renderSidebar();
  renderHead();
  renderFilters();
  const rows = filteredRows();
  $("#board").hidden = state.view !== "board";
  $("#table-view").hidden = state.view !== "table";
  document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === state.view)));
  const empty = $("#empty");
  if (!rowsInView().length) {
    empty.hidden = false;
    empty.innerHTML = state.tickers.size
      ? "This list is empty. Add tickers above."
      : "No names yet. Add tickers above or <b>Import</b> your watchlist workbook.";
  } else empty.hidden = true;
  if (state.view === "board") renderBoard(rows); else renderTable(rows);
  renderFreshness();
  if (state.drawer) highlightActive();
}

function renderSidebar() {
  const nav = $("#lists");
  const total = state.tickers.size;
  const item = (id, name, count) =>
    `<button class="list-btn" data-list="${id}" aria-current="${String(state.current) === String(id)}"><span>${escapeHtml(name)}</span><span class="count">${count}</span></button>`;
  nav.innerHTML = item("all", "All names", total) + '<div class="list-sep"></div>' + state.lists.map((l) => item(l.id, l.name, l.count)).join("");
  nav.querySelectorAll(".list-btn").forEach((b) => b.addEventListener("click", () => {
    state.current = b.dataset.list;
    store.set("bpd.current", state.current);
    $("#sidebar").classList.remove("open");
    render();
  }));
}

function currentList() {
  return state.current === "all" ? null : state.lists.find((l) => String(l.id) === String(state.current));
}

function renderHead() {
  const l = currentList();
  $("#view-title").textContent = l ? l.name : "All names";
  $("#view-count").textContent = `${rowsInView().length} names`;
  $("#rename-list").hidden = !l;
  $("#delete-list").hidden = !l;
  $("#add-input").placeholder = l ? `Add tickers to ${l.name}: AAPL, MSFT, BRK/B…` : "Add tickers: AAPL, MSFT, BRK/B…";
}

function renderFilters() {
  const rows = rowsInView();
  const count = (fn) => rows.filter(fn).length;
  const chips = [
    ...Object.entries(VERDICTS).filter(([k]) => k !== "error").map(([k, label]) => ({
      on: state.filter.verdict === k, label: `<span class="dot ${k}"></span>${label}`, n: count((r) => r.summary?.verdict === k),
      toggle: () => { state.filter.verdict = state.filter.verdict === k ? null : k; },
    })),
    { on: state.filter.changed, label: "Phase changed", n: count(isChanged), toggle: () => { state.filter.changed = !state.filter.changed; } },
    { on: state.filter.doubt, label: "Unsure phase", n: count(doubtful), toggle: () => { state.filter.doubt = !state.filter.doubt; } },
    { on: state.filter.mismatch, label: "Differs from my phase", n: count(mismatch), toggle: () => { state.filter.mismatch = !state.filter.mismatch; } },
  ];
  const el = $("#filters");
  el.innerHTML = "";
  for (const c of chips) {
    const b = document.createElement("button");
    b.className = "chip";
    b.type = "button";
    b.setAttribute("aria-pressed", String(c.on));
    b.innerHTML = `${c.label} <span class="count">${c.n}</span>`;
    b.addEventListener("click", () => { c.toggle(); render(); });
    el.appendChild(b);
  }
}

function renderFreshness() {
  const rows = rowsInView().filter((r) => r.evaluated_at);
  const el = $("#freshness");
  if (!rows.length) { el.textContent = ""; return; }
  const oldest = Math.min(...rows.map((r) => new Date(r.evaluated_at).getTime()));
  const stale = rowsInView().filter(isStale).length;
  el.innerHTML = `Data ${timeAgo(oldest)}${stale ? ` · <span class="stale">${stale} stale</span>` : ""}`;
}

// ---------------------------------------------------------------- board

function cardHtml(r) {
  const s = r.summary || {};
  const ph = s.phase || {};
  const bits = [];
  if (ph.base_phase) bits.push(`<span>${ph.weeks_in_phase}w in ${ph.base_phase}</span>`);
  if (s.verdict && s.verdict !== "error") bits.push(`<span class="setup" title="BPD setup: ${VERDICTS[s.verdict]}"><span class="dot ${s.verdict}"></span>${s.score ?? ""}</span>`);
  if (isChanged(r)) bits.push(`<span class="pill changed" title="Changed since previous evaluation">from ${escapeHtml(r.prev_phase)}</span>`);
  if (doubtful(r)) bits.push(`<span class="pill doubt" title="${escapeAttr(ph.doubts.join("; "))}">unsure</span>`);
  if (mismatch(r)) bits.push(`<span class="pill mine" title="Your phase">you: ${escapeHtml(r.manual_phase)}</span>`);
  if (s.verdict === "error") bits.push(`<span class="error-text">${escapeHtml(s.error || "error")}</span>`);
  if (!r.evaluated_at) bits.push('<span class="muted">not evaluated</span>');
  return `<button class="card-t" data-sym="${escapeAttr(r.symbol)}" type="button">
    <span class="card-top"><span class="card-sym">${escapeHtml(r.symbol)}</span></span>
    <span class="card-name">${escapeHtml(r.name || r.industry || "")}</span>
    <span class="card-meta">${bits.join("")}</span>
  </button>`;
}

function renderBoard(rows) {
  const board = $("#board");
  const groups = new Map([...PHASES, "–"].map((p) => [p, []]));
  for (const r of rows) groups.get(r.phase && groups.has(r.phase) ? r.phase : "–").push(r);
  const sortCards = (a, b) =>
    (VERDICT_ORDER[a.summary?.verdict] ?? 9) - (VERDICT_ORDER[b.summary?.verdict] ?? 9) ||
    (b.summary?.score ?? -1) - (a.summary?.score ?? -1) || a.symbol.localeCompare(b.symbol);
  board.innerHTML = [...groups.entries()].filter(([p, list]) => p !== "–" || list.length).map(([p, list]) => `
    <div class="col ${p === "–" ? "" : phaseClass(p)}">
      <div class="col-head" style="${p === "–" ? "--ph: var(--axis)" : ""}">
        <span class="ph">${p === "–" ? "Not evaluated" : escapeHtml(p)}</span><span class="count">${list.length}</span>
        <div class="col-help">${escapeHtml(PHASE_HELP[p] || "No phase yet - press Re-evaluate")}</div>
      </div>
      <div class="col-body">${list.sort(sortCards).map(cardHtml).join("")}</div>
    </div>`).join("");
  board.querySelectorAll(".card-t").forEach((c) => c.addEventListener("click", () => openDrawer(c.dataset.sym)));
}

// ---------------------------------------------------------------- table

const COLUMNS = [
  { key: "symbol", label: "Ticker" },
  { key: "phase", label: "Phase" },
  { key: "weeks", label: "Weeks", num: true },
  { key: "changed", label: "Previous" },
  { key: "manual", label: "My phase" },
  { key: "rs", label: "RS vs EMA52", num: true },
  { key: "verdict", label: "Setup" },
  { key: "score", label: "Score", num: true },
  ...CRITERIA.map((k) => ({ key: k, crit: true })),
  { key: "dist_pivot", label: "vs pivot", num: true },
  { key: "note", label: "Note" },
  { key: "evaluated", label: "Updated" },
];

function sortValue(r, key) {
  const s = r.summary || {};
  switch (key) {
    case "symbol": return [r.symbol];
    case "phase": return [r.phase ? PHASES.indexOf(r.phase) : 99, VERDICT_ORDER[s.verdict] ?? 9, -(s.score ?? -1)];
    case "weeks": return [s.phase?.weeks_in_phase ?? -1];
    case "changed": return [isChanged(r) ? 0 : 1, r.prev_phase || "~"];
    case "manual": return [r.manual_phase ? PHASES.indexOf(r.manual_phase) : 99];
    case "rs": return [s.phase?.rs_margin_pct ?? -999];
    case "verdict": return [VERDICT_ORDER[s.verdict] ?? 9, -(s.score ?? -1)];
    case "score": return [s.score ?? -1];
    case "dist_pivot": return [s.dist_pivot ?? -999];
    case "note": return [r.note || "~"];
    case "evaluated": return [r.evaluated_at || ""];
    default: {
      const order = { pass: 0, warn: 1, fail: 2, na: 3 };
      const c = s.criteria?.[key];
      return [c ? order[c.status] : 4, -(s.score ?? -1)];
    }
  }
}

function compareRows(a, b) {
  const { key, dir } = state.sort;
  const va = sortValue(a, key), vb = sortValue(b, key);
  for (let i = 0; i < va.length; i++) {
    if (va[i] < vb[i]) return -dir;
    if (va[i] > vb[i]) return dir;
  }
  return a.symbol.localeCompare(b.symbol);
}

function renderTable(rows) {
  const thead = $("#table thead");
  thead.innerHTML = "<tr>" + COLUMNS.map((c) => {
    const label = c.crit ? state.config.criteria[c.key] : c.label;
    const sort = state.sort.key === c.key ? ` aria-sort="${state.sort.dir === 1 ? "ascending" : "descending"}"` : "";
    return `<th data-key="${c.key}" class="${c.num ? "num" : ""}"${sort}>${escapeHtml(label)}</th>`;
  }).join("") + "</tr>";
  thead.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const col = COLUMNS.find((c) => c.key === th.dataset.key);
    if (state.sort.key === col.key) state.sort.dir *= -1;
    else state.sort = { key: col.key, dir: col.num ? -1 : 1 };
    render();
  }));
  const tbody = $("#table tbody");
  tbody.innerHTML = rows.sort(compareRows).map(rowHtml).join("");
  tbody.querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", (ev) => {
    if (ev.target.closest("a")) return;
    openDrawer(tr.dataset.sym);
  }));
  tbody.querySelectorAll("[data-tip]").forEach(attachTip);
}

function rowHtml(r) {
  const s = r.summary || {};
  const ph = s.phase || {};
  const cells = [];
  cells.push(`<td><span class="sym">${escapeHtml(r.symbol)}</span><span class="name">${escapeHtml(r.name || "")}</span></td>`);
  const phTip = [PHASE_HELP[r.phase], ...(ph.flags || []), ...(ph.doubts || []).map((d) => `Unsure: ${d}`)].filter(Boolean).join(" · ");
  cells.push(`<td><span data-tip="${escapeAttr(phTip)}">${phaseBadge(r.phase)}${doubtful(r) ? ' <span class="pill doubt">unsure</span>' : ""}</span></td>`);
  cells.push(`<td class="num">${ph.weeks_in_phase ?? "–"}</td>`);
  cells.push(`<td>${isChanged(r) ? `<span class="pill changed">from ${escapeHtml(r.prev_phase)}</span>` : (r.prev_phase ? `<span class="muted">${escapeHtml(r.prev_phase)}</span>` : "")}</td>`);
  cells.push(`<td>${r.manual_phase ? phaseBadge(r.manual_phase) : '<span class="muted">–</span>'}</td>`);
  cells.push(`<td class="num">${ph.rs_margin_pct == null ? "–" : (ph.rs_margin_pct > 0 ? "+" : "") + ph.rs_margin_pct.toFixed(1) + "%"}</td>`);
  cells.push(`<td>${s.verdict ? `<span class="setup" data-tip="${escapeAttr(s.verdict === "error" ? s.error : reasonText(s))}"><span class="dot ${s.verdict}"></span>${VERDICTS[s.verdict]}</span>` : '<span class="muted">–</span>'}</td>`);
  cells.push(`<td class="num">${s.score ?? "–"}</td>`);
  for (const k of CRITERIA) {
    const c = s.criteria?.[k];
    cells.push(c ? `<td>${statusHtml(c.status, c.value)}</td>` : "<td class='muted'>–</td>");
  }
  cells.push(`<td class="num">${s.dist_pivot == null ? "–" : pct(s.dist_pivot, 1, true)}</td>`);
  cells.push(`<td class="note" title="${escapeAttr(r.note || "")}">${escapeHtml(r.note || "")}</td>`);
  cells.push(`<td class="${isStale(r) ? "stale" : "muted"}">${r.evaluated_at ? timeAgo(new Date(r.evaluated_at).getTime()) : "never"}</td>`);
  return `<tr data-sym="${escapeAttr(r.symbol)}" class="${state.drawer === r.symbol ? "active" : ""}">${cells.join("")}</tr>`;
}

function reasonText(s) {
  const parts = [];
  if (s.reasons?.fail?.length) parts.push(`Failed: ${s.reasons.fail.join(", ")}`);
  if (s.reasons?.check?.length) parts.push(`Check: ${s.reasons.check.join(", ")}`);
  return parts.join(" · ") || "All setup criteria pass";
}

function statusHtml(status, value) {
  return `<span class="st ${status}"><i aria-label="${STATUS_TEXT[status]}">${ICONS[status]}</i>${escapeHtml(value ?? "")}</span>`;
}

function highlightActive() {
  document.querySelectorAll(".card-t, #table tbody tr").forEach((el) => {
    el.classList.toggle("active", el.dataset.sym === state.drawer);
  });
}

// ================================================================ drawer

async function openDrawer(symbol) {
  state.drawer = symbol;
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  highlightActive();
  renderDrawer(state.tickers.get(symbol), state.details.get(symbol));
  try {
    const full = await api(`/api/tickers/${encodeURIComponent(symbol)}`);
    state.details.set(symbol, full.detail);
    delete full.detail;
    upsertRows([full]);
    if (state.drawer === symbol) renderDrawer(state.tickers.get(symbol), state.details.get(symbol));
  } catch (e) {
    setMessage(e.message, true);
  }
}

function closeDrawer() {
  state.drawer = null;
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  highlightActive();
}

function renderDrawer(r, detail) {
  if (!r) return;
  const s = r.summary || {};
  const ph = s.phase || {};
  const body = $("#drawer-body");
  const l = currentList();
  const sub = [r.industry, r.tags, s.last_close != null ? `last ${s.last_close} (${s.last_date})` : null].filter(Boolean).map(escapeHtml).join(" · ");
  body.innerHTML = `
    <div class="d-head">
      <div class="d-title">
        <h2>${escapeHtml(r.symbol)} <span class="sub">${escapeHtml(r.name || "")}</span></h2>
        <div class="sub">${sub}</div>
      </div>
      <div class="d-actions">
        <button class="btn small" id="d-reeval" type="button">Re-evaluate</button>
        ${l ? `<button class="btn ghost small" id="d-remove" type="button">Remove from ${escapeHtml(l.name)}</button>` : `<button class="btn ghost small danger" id="d-delete" type="button">Delete</button>`}
        <button class="icon-btn" id="d-close" type="button" aria-label="Close">✕</button>
      </div>
    </div>
    <div class="d-body">
      <section class="d-section phase-box">
        <h3>Phase</h3>
        <div>${phaseBadge(r.phase, "phase-big")}</div>
        <div class="muted">${escapeHtml(PHASE_HELP[r.phase] || (s.verdict === "error" ? s.error : "Not evaluated yet"))}</div>
        ${ph.base_phase ? `<div class="kv">
          <span><b>${ph.weeks_in_phase}</b> weeks in ${escapeHtml(ph.base_phase)}${ph.prior_phase ? `, before that ${escapeHtml(ph.prior_phase)}` : ""}</span>
          <span>10W vs 30W EMA <b>${signed(ph.ema_spread_pct)}%</b></span>
          <span>10W slope <b>${signed(ph.ema10_slope_pct)}%</b></span>
          <span>RS vs its 52W EMA <b>${signed(ph.rs_margin_pct)}%</b> ${ph.rs_ema_rising ? "(EMA rising)" : "(EMA falling)"}</span>
          <span>Weekly BBW position <b>${ph.bbw_pos == null ? "–" : pct(ph.bbw_pos)}</b> (3-wk low ${ph.bbw_pos_min == null ? "–" : pct(ph.bbw_pos_min)})</span>
        </div>` : ""}
        ${ph.doubts?.length ? `<div><b>Unsure - worth a manual look:</b><ul class="notes-list doubts">${ph.doubts.map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul></div>` : ""}
        ${ph.flags?.length ? `<div><b>Watch:</b><ul class="notes-list">${ph.flags.map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul></div>` : ""}
        ${r.prev_phase ? `<div class="muted small">Previous evaluation: ${escapeHtml(r.prev_phase)}${r.phase_changed_at ? `, changed ${timeAgo(new Date(r.phase_changed_at).getTime())}` : ""}</div>` : ""}
      </section>

      <section class="d-section">
        <h3>Phase history (52 weeks)</h3>
        ${historyHtml(detail?.phase_history)}
      </section>

      <section class="d-section">
        <h3>My phase &amp; note</h3>
        <div class="mine-row">
          <select id="d-manual" aria-label="My phase">
            <option value="">– not set –</option>
            ${PHASES.map((p) => `<option value="${escapeAttr(p)}" ${r.manual_phase === p ? "selected" : ""}>${escapeHtml(p)}</option>`).join("")}
          </select>
          <span class="muted small">${r.manual_phase ? `${r.manual_phase_source === "import" ? "imported" : "set"} ${r.manual_phase_at ? new Date(r.manual_phase_at).toLocaleDateString() : ""}` : "Your call is saved with the date and used to tune the classifier."}</span>
        </div>
        <div class="note-box" style="margin-top:8px">
          <textarea id="d-note" placeholder="Note (saved for the team)">${escapeHtml(r.note || "")}</textarea>
        </div>
      </section>

      <section class="d-section">
        <h3>Lists</h3>
        <div class="list-checks">
          ${state.lists.map((li) => `<label><input type="checkbox" data-list="${li.id}" ${r.lists.includes(li.id) ? "checked" : ""}> ${escapeHtml(li.name)}</label>`).join("") || '<span class="muted">No lists yet</span>'}
        </div>
      </section>

      <section class="d-section">
        <h3>Charts</h3>
        <div class="charts" id="d-charts">${detail ? "" : '<span class="muted">Loading…</span>'}</div>
      </section>

      <section class="d-section">
        <h3>BPD setup · ${s.verdict ? `<span class="setup"><span class="dot ${s.verdict}"></span>${VERDICTS[s.verdict]}</span> ${s.score != null ? `· score ${s.score}` : ""}` : "not evaluated"}</h3>
        ${detail?.criteria ? `<ul class="crit-list">${CRITERIA.filter((k) => detail.criteria[k]).map((k) => {
          const c = detail.criteria[k];
          return `<li><span class="st ${c.status}"><i aria-label="${STATUS_TEXT[c.status]}">${ICONS[c.status]}</i></span>
            <div><div class="crit-name">${escapeHtml(state.config.criteria[k])} · ${escapeHtml(c.value)}</div><div class="crit-detail">${escapeHtml(c.detail)}</div></div></li>`;
        }).join("")}</ul>` : ""}
      </section>

      <section class="d-section links">
        <a href="${escapeAttr(s.stockcharts_url || `https://stockcharts.com/h-sc/ui?s=${encodeURIComponent(r.symbol)}`)}" target="_blank" rel="noopener">StockCharts</a>
        <a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(r.symbol.replace("-", "."))}" target="_blank" rel="noopener">TradingView</a>
        <a href="https://finance.yahoo.com/quote/${encodeURIComponent(r.symbol)}" target="_blank" rel="noopener">Yahoo Finance</a>
      </section>
    </div>`;

  $("#d-close").addEventListener("click", closeDrawer);
  $("#d-reeval").addEventListener("click", () => reevaluate([r.symbol]));
  $("#d-remove")?.addEventListener("click", async () => {
    const data = await api(`/api/lists/${l.id}/remove`, { method: "POST", json: { symbols: [r.symbol] } });
    state.lists = data.lists;
    r.lists = r.lists.filter((x) => x !== l.id);
    closeDrawer();
    render();
  });
  $("#d-delete")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Click again to delete"; return; }
    await api("/api/tickers/delete", { method: "POST", json: { symbols: [r.symbol] } });
    state.tickers.delete(r.symbol);
    closeDrawer();
    await loadState();
  });
  $("#d-manual").addEventListener("change", async (ev) => {
    const v = ev.target.value;
    const row = await api(`/api/tickers/${encodeURIComponent(r.symbol)}`, { method: "PATCH", json: v ? { manual_phase: v } : { clear_manual_phase: true } });
    upsertRows([row]);
    render();
    renderDrawer(state.tickers.get(r.symbol), state.details.get(r.symbol));
  });
  $("#d-note").addEventListener("change", async (ev) => {
    const row = await api(`/api/tickers/${encodeURIComponent(r.symbol)}`, { method: "PATCH", json: { note: ev.target.value } });
    upsertRows([row]);
    render();
  });
  body.querySelectorAll(".list-checks input").forEach((cb) => cb.addEventListener("change", async () => {
    const id = Number(cb.dataset.list);
    const data = await api(`/api/lists/${id}/${cb.checked ? "add" : "remove"}`, { method: "POST", json: { symbols: [r.symbol] } });
    state.lists = data.lists;
    r.lists = cb.checked ? [...new Set([...r.lists, id])] : r.lists.filter((x) => x !== id);
    render();
  }));
  body.querySelectorAll("[data-tip]").forEach(attachTip);
  if (detail?.charts) drawCharts($("#d-charts"), detail.charts, r.symbol);
}

function historyHtml(hist) {
  if (!hist?.length) return '<span class="muted">–</span>';
  const cells = hist.slice(-52);
  const pad = 52 - cells.length;
  const html = Array(pad).fill("<span></span>").join("") + cells.map((h) =>
    `<span class="${phaseClass(h.phase)}" data-tip="${escapeAttr(`${h.week}: ${h.phase || "–"}`)}"></span>`).join("");
  return `<div class="history">${html}</div><div class="history-axis"><span>${cells[0].week}</span><span>now</span></div>`;
}

function drawCharts(root, c, symbol) {
  root.innerHTML = "";
  root.appendChild(chartBox(
    "Weekly price with 10W / 30W EMA, base and volume by price",
    [["Close", "var(--series-1)"], ["10W EMA", "var(--series-2)"], ["30W EMA", "var(--muted)"], ["Base", "area"]],
    (svg) => drawChart(svg, {
      dates: c.weekly.dates, main: c.weekly.close, mainLabel: "Close",
      second: c.weekly.ema10 ? { label: "10W EMA", values: c.weekly.ema10 } : null,
      refs: [c.weekly.ema30 ? { label: "30W EMA", values: c.weekly.ema30 } : null, c.weekly.pivot ? { label: "Pivot", values: c.weekly.dates.map(() => c.weekly.pivot) } : null].filter(Boolean),
      shadeFrom: c.weekly.base_start, vbp: c.vbp, fmt: (v) => v.toFixed(2),
    }), true));
  root.appendChild(chartBox("Weekly BB Width (20,2) with CHAN(125)", [["BBW", "var(--series-1)"], ["Channel", "var(--muted)"]],
    (svg) => drawChart(svg, {
      dates: c.weekly.dates, main: c.weekly.bbw, mainLabel: "BBW",
      refs: [{ label: "Chan high", values: c.weekly.chan_hi }, { label: "Chan low", values: c.weekly.chan_lo }],
      shadeFrom: c.weekly.base_start, fmt: (v) => v.toFixed(1),
    })));
  root.appendChild(chartBox(`RS ${symbol}:$SPX with 52W EMA (weekly)`, [["RS", "var(--series-1)"], ["52W EMA", "var(--muted)"]],
    (svg) => drawChart(svg, {
      dates: c.rs.dates, main: c.rs.rs, mainLabel: "RS",
      refs: c.rs.ema52 ? [{ label: "52W EMA", values: c.rs.ema52 }] : [], fmt: (v) => v.toFixed(3),
    })));
  root.appendChild(chartBox("Daily BB Width (20,2) with CHAN(125)", [["BBW", "var(--series-1)"], ["Channel", "var(--muted)"]],
    (svg) => drawChart(svg, {
      dates: c.daily.dates, main: c.daily.bbw, mainLabel: "BBW",
      refs: [{ label: "Chan high", values: c.daily.chan_hi }, { label: "Chan low", values: c.daily.chan_lo }], fmt: (v) => v.toFixed(1),
    }), true));
}

function chartBox(title, keys, draw, wide = false) {
  const box = document.createElement("div");
  box.className = "chart" + (wide ? " wide" : "");
  const keyHtml = keys.map(([label, color]) =>
    color === "area" ? `<span class="key area">${label}</span>` : `<span class="key" style="--key:${color}">${label}</span>`).join("");
  box.innerHTML = `<div class="chart-title">${escapeHtml(title)} ${keyHtml}</div>`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  box.appendChild(svg);
  requestAnimationFrame(() => draw(svg));
  return box;
}

// Minimal SVG line chart: a highlighted series, an optional second series, thin grey
// reference lines, optional shaded base and volume-by-price bars, hover crosshair.
function drawChart(svg, opt) {
  const W = svg.clientWidth || 400, H = svg.clientHeight || 150;
  const pad = { l: 4, r: 46, t: 8, b: 18 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const n = opt.dates.length;
  const series = [opt.main, opt.second?.values || [], ...opt.refs.map((r) => r.values)];
  const all = series.flat().filter((v) => v != null);
  if (!all.length) return;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi === lo) { hi += 1; lo -= 1; }
  const span = hi - lo;
  lo -= span * 0.05; hi += span * 0.05;
  const x = (i) => pad.l + (i / Math.max(n - 1, 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const parts = [];
  for (const t of niceTicks(lo, hi, 4)) {
    parts.push(`<line class="gridline" x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/>`);
    parts.push(`<text x="${W - pad.r + 6}" y="${y(t) + 3}">${opt.fmt(t)}</text>`);
  }
  opt.dates.forEach((d, i) => {
    if (i > 0 && opt.dates[i - 1].slice(0, 4) !== d.slice(0, 4)) parts.push(`<text x="${x(i)}" y="${H - 4}" text-anchor="middle">${d.slice(0, 4)}</text>`);
  });
  if (opt.shadeFrom) {
    const si = opt.dates.findIndex((d) => d >= opt.shadeFrom);
    if (si >= 0) parts.push(`<rect class="shade" x="${x(si)}" y="${pad.t}" width="${W - pad.r - x(si)}" height="${H - pad.t - pad.b}"/>`);
  }
  if (opt.vbp) {
    const { edges, volume } = opt.vbp;
    const maxV = Math.max(...volume);
    const maxW = (W - pad.l - pad.r) * 0.25;
    volume.forEach((v, i) => {
      const y1 = y(edges[i + 1]), y2 = y(edges[i]);
      const w = maxV ? (v / maxV) * maxW : 0;
      parts.push(`<rect class="vbp" x="${W - pad.r - w}" y="${y1 + 1}" width="${w}" height="${Math.max(y2 - y1 - 2, 1)}"/>`);
    });
  }
  for (const ref of opt.refs) parts.push(`<path class="line-ref" d="${pathD(ref.values, x, y)}"/>`);
  if (opt.second) parts.push(`<path class="line-2" d="${pathD(opt.second.values, x, y)}"/>`);
  parts.push(`<path class="line-main" d="${pathD(opt.main, x, y)}"/>`);
  parts.push(`<line class="crosshair" y1="${pad.t}" y2="${H - pad.b}" visibility="hidden"/>`);
  parts.push(`<circle class="hover-dot" r="4" visibility="hidden"/>`);
  parts.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="transparent" class="hit"/>`);
  svg.innerHTML = parts.join("");

  const cross = $(".crosshair", svg), dot = $(".hover-dot", svg), tip = $("#tooltip"), hit = $(".hit", svg);
  hit.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))));
    const v = opt.main[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    if (v != null) { dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(v)); dot.setAttribute("visibility", "visible"); }
    let html = `<div class="t-date">${opt.dates[i]}</div><div>${opt.mainLabel}: <b>${v == null ? "–" : opt.fmt(v)}</b></div>`;
    if (opt.second && opt.second.values[i] != null) html += `<div>${opt.second.label}: ${opt.fmt(opt.second.values[i])}</div>`;
    for (const ref of opt.refs) if (ref.values[i] != null) html += `<div>${ref.label}: ${opt.fmt(ref.values[i])}</div>`;
    showTip(html, ev.clientX, ev.clientY);
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true;
  });
}

function pathD(values, x, y) {
  let d = "", pen = false;
  values.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
  return ticks;
}

// ================================================================ actions

function parseTickers(text) {
  const out = [];
  // "BRK/B" is one class-share symbol; "NWSA/NWS" is two tickers
  const parts = text.toUpperCase().split(/[\s,;|]+/)
    .flatMap((tok) => (/^[A-Z]{1,5}[/.][A-Z]$/.test(tok) ? [tok] : tok.split("/")));
  for (let p of parts) {
    p = p.replace(/^\$/, "");
    if (!p || p === "TICKER" || p === "SYMBOL") continue;
    p = p.replace(/^([A-Z]{1,5})[/.]([A-Z])$/, "$1-$2");
    if (/^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$/.test(p)) out.push(p);
  }
  return [...new Set(out)];
}

async function addTickers(ev) {
  ev.preventDefault();
  const symbols = parseTickers($("#add-input").value);
  if (!symbols.length) return;
  const l = currentList();
  try {
    let fresh;
    if (l) {
      const data = await api(`/api/lists/${l.id}/add`, { method: "POST", json: { symbols } });
      state.lists = data.lists;
      fresh = data.added;
    } else {
      const data = await api("/api/import/commit", { method: "POST", json: { sheets: [{ tickers: symbols }] } });
      state.lists = data.lists;
      fresh = symbols;
    }
    $("#add-input").value = "";
    await loadState();
    const toEval = fresh.filter((s) => !state.tickers.get(s)?.evaluated_at);
    if (toEval.length) await reevaluate(toEval);
  } catch (e) {
    setMessage(e.message, true);
  }
}

async function reevaluate(symbols) {
  if (state.busy) return;
  symbols = symbols || rowsInView().map((r) => r.symbol);
  if (!symbols.length) { setMessage("Nothing to evaluate in this view."); return; }
  state.busy = true;
  $("#reevaluate").disabled = true;
  const progress = $("#progress");
  progress.hidden = false;
  let done = 0, failed = 0;
  const setProgress = () => {
    $("#progress-bar").style.width = `${(100 * done) / symbols.length}%`;
    $("#progress-text").textContent = `Evaluating ${done} / ${symbols.length}`;
  };
  setProgress();
  setMessage("");
  try {
    for (let i = 0; i < symbols.length; i += CHUNK_SIZE) {
      const chunk = symbols.slice(i, i + CHUNK_SIZE);
      let data = null;
      for (let attempt = 0; attempt < 2 && !data; attempt++) {
        try {
          data = await api("/api/evaluate", { method: "POST", json: { symbols: chunk } });
        } catch (e) {
          if (e.message === "Password required") throw e;
          if (attempt === 1) { failed += chunk.length; setMessage(`Some tickers failed: ${e.message}`, true); }
          else await new Promise((r) => setTimeout(r, 3000));
        }
      }
      if (data) {
        upsertRows(data.tickers);
        for (const s of chunk) state.details.delete(s);
      }
      done += chunk.length;
      setProgress();
      render();
      if (state.drawer && chunk.includes(state.drawer)) openDrawer(state.drawer);
    }
    const rows = symbols.map((s) => state.tickers.get(s)).filter(Boolean);
    const changed = rows.filter(isChanged).length;
    setMessage(`Updated ${rows.length - failed} names${changed ? ` · ${changed} changed phase` : ""}.`);
  } catch (e) {
    setMessage(e.message, true);
  } finally {
    state.busy = false;
    $("#reevaluate").disabled = false;
    setTimeout(() => { progress.hidden = true; }, 800);
  }
}

// ---------------------------------------------------------------- lists

async function newList(ev) {
  ev.preventDefault();
  const name = $("#new-list-name").value.trim();
  if (!name) return;
  const data = await api("/api/lists", { method: "POST", json: { name } });
  state.lists = data.lists;
  state.current = String(data.id);
  store.set("bpd.current", state.current);
  $("#new-list-name").value = "";
  render();
}

async function renameList() {
  const l = currentList();
  const h1 = $("#view-title");
  h1.contentEditable = "true";
  h1.focus();
  document.getSelection().selectAllChildren(h1);
  const finish = async () => {
    h1.contentEditable = "false";
    const name = h1.textContent.trim();
    if (name && name !== l.name) {
      const data = await api(`/api/lists/${l.id}`, { method: "PATCH", json: { name } });
      state.lists = data.lists;
    }
    render();
  };
  h1.addEventListener("blur", finish, { once: true });
  h1.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); h1.blur(); } });
}

async function deleteList(ev) {
  const btn = ev.currentTarget;
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = "Click again to delete the list (names stay in All)";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Delete list"; }, 4000);
    return;
  }
  const l = currentList();
  const data = await api(`/api/lists/${l.id}`, { method: "DELETE" });
  state.lists = data.lists;
  state.current = "all";
  store.set("bpd.current", "all");
  btn.dataset.armed = "";
  btn.textContent = "Delete list";
  await loadState();
}

// ---------------------------------------------------------------- import

async function importParse(file) {
  const box = $("#import-sheets");
  box.innerHTML = '<span class="muted">Reading…</span>';
  try {
    const buf = await file.arrayBuffer();
    const data = await api(`/api/import/parse?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: buf,
    });
    state.importSheets = data.sheets.map((s) => ({ ...s, on: true, list_name: s.phase ? "" : s.name }));
    renderImport();
  } catch (e) {
    box.innerHTML = `<span class="error-text">${escapeHtml(e.message)}</span>`;
  }
}

function renderImport() {
  const box = $("#import-sheets");
  const phaseOpts = (sel) => `<option value="">– no –</option>` + PHASES.map((p) => `<option ${sel === p ? "selected" : ""}>${escapeHtml(p)}</option>`).join("");
  box.innerHTML = `<div class="import-row head"><span></span><span>Sheet</span><span>Names</span><span>Add to list</span><span>Sets my phase</span></div>` +
    state.importSheets.map((s, i) => `<div class="import-row" data-i="${i}">
      <input type="checkbox" ${s.on ? "checked" : ""} aria-label="Import ${escapeAttr(s.name)}">
      <span>${escapeHtml(s.name)}</span>
      <span class="muted">${s.tickers.length}</span>
      <input type="text" value="${escapeAttr(s.list_name)}" placeholder="(no list)">
      <select>${phaseOpts(s.phase)}</select>
    </div>`).join("");
  box.querySelectorAll(".import-row[data-i]").forEach((row) => {
    const s = state.importSheets[Number(row.dataset.i)];
    row.querySelector("input[type=checkbox]").addEventListener("change", (e) => { s.on = e.target.checked; importSummary(); });
    row.querySelector("input[type=text]").addEventListener("input", (e) => { s.list_name = e.target.value; });
    row.querySelector("select").addEventListener("change", (e) => { s.phase = e.target.value || null; });
  });
  importSummary();
}

function importSummary() {
  const on = state.importSheets.filter((s) => s.on);
  const names = new Set(on.flatMap((s) => s.tickers));
  $("#import-summary").textContent = on.length ? `${names.size} unique names from ${on.length} sheets` : "";
  $("#import-commit").disabled = !on.length;
}

async function importCommit() {
  const sheets = state.importSheets.filter((s) => s.on).map((s) => ({
    list_name: s.list_name || null, phase: s.phase || null, tickers: s.tickers, meta: s.meta || {},
  }));
  $("#import-commit").disabled = true;
  try {
    const data = await api("/api/import/commit", { method: "POST", json: { sheets } });
    $("#import-dialog").close();
    await loadState();
    const missing = [...state.tickers.values()].filter((r) => !r.evaluated_at).map((r) => r.symbol);
    setMessage(`Imported ${data.tickers} names.`);
    if (missing.length) await reevaluate(missing);
  } catch (e) {
    $("#import-summary").textContent = e.message;
  } finally {
    $("#import-commit").disabled = false;
  }
}

// ---------------------------------------------------------------- settings

function renderThresholds() {
  const root = $("#threshold-groups");
  root.innerHTML = "";
  for (const group of state.config.thresholds) {
    const g = document.createElement("div");
    g.className = "threshold-group";
    g.innerHTML = `<h3>${escapeHtml(group.group)}</h3>`;
    for (const f of group.fields) {
      const val = state.thresholds[f.key] ?? f.default;
      const row = document.createElement("label");
      row.className = "threshold-row";
      row.innerHTML = `<span>${escapeHtml(f.label)} <span class="muted">(${f.unit})</span></span><input type="number" step="any" data-key="${f.key}" data-default="${f.default}" value="${val}">`;
      const input = $("input", row);
      input.classList.toggle("changed", val !== f.default);
      input.addEventListener("input", () => input.classList.toggle("changed", parseFloat(input.value) !== f.default));
      g.appendChild(row);
    }
    root.appendChild(g);
  }
}

async function saveThresholds() {
  const overrides = {};
  document.querySelectorAll("#threshold-groups input").forEach((i) => {
    const v = parseFloat(i.value);
    if (!Number.isNaN(v) && v !== parseFloat(i.dataset.default)) overrides[i.dataset.key] = v;
  });
  const data = await api("/api/settings/thresholds", { method: "PUT", json: overrides });
  state.thresholds = data.thresholds;
  $("#settings-dialog").close();
  setMessage("Thresholds saved. Press Re-evaluate to apply them.");
}

// ---------------------------------------------------------------- CSV

function exportCsv() {
  const header = ["Ticker", "Name", "Industry", "Phase", "Weeks in phase", "Previous phase", "My phase", "RS vs 52W EMA %",
    "Setup", "Score", "vs pivot", ...CRITERIA.map((k) => state.config.criteria[k]), "Phase flags", "Phase doubts", "Note", "Updated"];
  const rows = filteredRows().sort(compareRows).map((r) => {
    const s = r.summary || {}, ph = s.phase || {};
    return [r.symbol, r.name || "", r.industry || "", r.phase || "", ph.weeks_in_phase ?? "", r.prev_phase || "", r.manual_phase || "",
      ph.rs_margin_pct ?? "", VERDICTS[s.verdict] || "", s.score ?? "", s.dist_pivot == null ? "" : pct(s.dist_pivot, 1, true),
      ...CRITERIA.map((k) => { const c = s.criteria?.[k]; return c ? `${STATUS_TEXT[c.status]} ${c.value}` : ""; }),
      (ph.flags || []).join("; "), (ph.doubts || []).join("; "), r.note || "", r.evaluated_at || ""];
  });
  const csv = [header, ...rows].map((row) => row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `bpd-watchlist-${(currentList()?.name || "all").replace(/\W+/g, "-")}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ================================================================ utils

function attachTip(el) {
  el.addEventListener("mouseenter", (ev) => { if (el.dataset.tip) showTip(escapeHtml(el.dataset.tip), ev.clientX, ev.clientY); });
  el.addEventListener("mousemove", (ev) => { if (el.dataset.tip) showTip(null, ev.clientX, ev.clientY); });
  el.addEventListener("mouseleave", () => { $("#tooltip").hidden = true; });
}

function showTip(html, cx, cy) {
  const tip = $("#tooltip");
  if (html !== null) tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = cx + 14, top = cy + 14;
  if (left + r.width > window.innerWidth - 8) left = cx - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = cy - r.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

function pct(v, digits = 0, signedOut = false) {
  const s = (v * 100).toFixed(digits) + "%";
  return signedOut && v > 0 ? "+" + s : s;
}
function signed(v) {
  return v == null ? "–" : (v > 0 ? "+" : "") + v.toFixed(1);
}
function timeAgo(ms) {
  const s = (Date.now() - ms) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const escapeAttr = escapeHtml;

function setMessage(text, isError = false) {
  const el = $("#message");
  el.hidden = !text;
  el.textContent = text;
  el.classList.toggle("error", isError);
}

// ================================================================ init

async function init() {
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view;
    store.set("bpd.view", state.view);
    render();
  }));
  $("#search").addEventListener("input", (e) => { state.filter.search = e.target.value; render(); });
  $("#reevaluate").addEventListener("click", () => reevaluate());
  $("#add-form").addEventListener("submit", addTickers);
  $("#new-list").addEventListener("submit", newList);
  $("#rename-list").addEventListener("click", renameList);
  $("#delete-list").addEventListener("click", deleteList);
  $("#export").addEventListener("click", exportCsv);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.drawer) closeDrawer(); });
  $("#menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#open-import").addEventListener("click", () => { state.importSheets = []; $("#import-sheets").innerHTML = ""; importSummary(); $("#import-dialog").showModal(); });
  $("#import-file").addEventListener("change", (e) => { if (e.target.files[0]) importParse(e.target.files[0]); e.target.value = ""; });
  $("#import-commit").addEventListener("click", importCommit);
  $("#open-settings").addEventListener("click", () => { renderThresholds(); $("#settings-dialog").showModal(); });
  $("#reset-thresholds").addEventListener("click", () => {
    document.querySelectorAll("#threshold-groups input").forEach((i) => { i.value = i.dataset.default; i.classList.remove("changed"); });
  });
  $("#save-thresholds").addEventListener("click", () => saveThresholds().catch((e) => setMessage(e.message, true)));
  $("#auth").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    state.password = $("#password").value;
    try {
      await api("/api/auth-check");
      store.set("bpd.password", state.password);
      $("#auth-dialog").close();
      await loadState();
    } catch (e) {
      $("#auth-error").hidden = false;
      $("#auth-error").textContent = e.message === "Password required" ? "Wrong password." : e.message;
    }
  });

  try {
    state.config = await api("/api/config");
  } catch (e) {
    setMessage(`Cannot reach the server: ${e.message}`, true);
    return;
  }
  PHASES = state.config.phases.map((p) => p.key);
  PHASE_HELP = Object.fromEntries(state.config.phases.map((p) => [p.key, p.help]));
  if (!state.config.persistent) {
    $("#banner").hidden = false;
    $("#banner").textContent = "Storage on this deployment is temporary - set DATABASE_URL to a Postgres database so the watchlist is kept.";
  }
  if (state.config.auth_required && !state.password) { askPassword(); return; }
  try {
    await loadState();
  } catch (e) {
    if (e.message !== "Password required") setMessage(e.message, true);
  }
}

init();
