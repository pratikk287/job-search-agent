// Job Search: two tabs (Main, Sheet), one-click Auto-fill & apply, live scan progress. Plain JS over the local API.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let HEALTH = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r;
}
const post = (p, body = {}) => api(p, { method: "POST", body });
const put = (p, body) => api(p, { method: "PUT", body });
const del = p => api(p, { method: "DELETE" });

function toast(msg, ms = 4200) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
}
async function run(btn, fn) {
  const label = btn?.textContent; if (btn) { btn.disabled = true; btn.textContent = "Working…"; }
  try { return await fn(); } catch (e) { toast(e.message, 7000); } finally { if (btn) { btn.disabled = false; btn.textContent = label; } }
}
async function copy(text) { try { await navigator.clipboard.writeText(text); } catch (_) { toast("Copy failed. Select the text and copy it by hand."); } }

const ICON = {
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>',
  auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
};

// ------------------------------------------------------------------ theme (auto / light / dark)
function themeMode() { try { return localStorage.getItem("theme") || "auto"; } catch (_) { return "auto"; } }
function applyTheme() {
  const m = themeMode(), root = document.documentElement;
  if (m === "auto") delete root.dataset.theme; else root.dataset.theme = m;
  $("#themeBtn").innerHTML = ICON[m === "dark" ? "moon" : m === "light" ? "sun" : "auto"];
  $("#themeBtn").title = `Appearance: ${m}`;
}
$("#themeBtn").onclick = () => {
  const next = { auto: "light", light: "dark", dark: "auto" }[themeMode()];
  try { localStorage.setItem("theme", next); } catch (_) {}
  applyTheme(); toast(`Appearance: ${next === "auto" ? "follow system" : next}`, 1600);
};
$("#gearBtn").innerHTML = ICON.gear;

// ------------------------------------------------------------------ panels
function openPanel(id, html) { const d = $(id); if (html !== undefined) $(id === "#drawer" ? "#drawerBody" : "#settingsBody").innerHTML = html; d.classList.remove("hidden"); }
function closePanels() { $$(".drawer, .modal").forEach(d => d.classList.add("hidden")); stopApplyWatch(); renderCurrent(); }
document.addEventListener("click", e => {
  if (e.target.closest("[data-modal-close]")) { $("#modal").classList.add("hidden"); return; }
  if (e.target.closest("[data-close]")) closePanels();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closePanels(); });

// ------------------------------------------------------------------ tabs
let TAB = "main";
function showTab(name) {
  TAB = name;
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$(".tab").forEach(s => s.classList.toggle("on", s.id === name));
  renderCurrent();
}
function renderCurrent() { return TAB === "sheet" ? renderSheet() : renderMain(); }
$$("#tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ------------------------------------------------------------------ scan with live progress bar
let scanWatching = false, scanWasRunning = false;
const dismissedKey = "scanDismissed";
$("#scanBtn").onclick = () => run($("#scanBtn"), async () => { await post("/api/scan"); try { sessionStorage.removeItem(dismissedKey); } catch (_) {} watchScan(); });

function scanBarHtml(s) {
  const pct = s.total ? Math.min(100, Math.round(100 * s.done / s.total)) : 0;
  if (s.running) {
    return `<div class="top"><div><div class="t">${esc(s.phase || "Scanning")}</div><div class="s">${s.stop ? "Stopping…" : esc(s.label || "")}</div></div>
      <div class="row"><span class="muted small">${s.total ? `${s.done} of ${s.total}` : ""}</span><button class="btn danger" id="scanStop" ${s.stop ? "disabled" : ""}>Stop</button></div></div>
      <div class="track ${s.total ? "" : "indet"}"><i style="width:${pct}%"></i></div>`;
  }
  const n = s.summary?.new ?? 0, stopped = s.phase === "Stopped";
  const LAB = { company: "Company sites", linkedin: "LinkedIn", naukri: "Naukri & Indeed", portals: "Other portals" };
  const mixTxt = s.summary?.mix ? Object.entries(s.summary.mix).filter(([, v]) => v.searches).map(([g, v]) => `${LAB[g]} ${v.share}%`).join(" · ") : "";
  return `<div class="top"><div><div class="t">${stopped ? "Scan stopped" : "Scan complete"}</div>
      <div class="s">${n} new job${n === 1 ? "" : "s"} found${mixTxt ? " · searches: " + esc(mixTxt) : ""}${s.summary?.errors?.length ? ` · ${s.summary.errors.length} source${s.summary.errors.length === 1 ? "" : "s"} had problems` : ""}</div></div>
    <button class="btn primary" id="scanDone">${stopped ? "OK" : "View jobs"}</button></div><div class="track done"><i style="width:${stopped ? pct : 100}%"></i></div>`;
}
async function watchScan() {
  if (scanWatching) return; scanWatching = true;
  try {
    for (;;) {
      const s = await api("/api/scan");
      const bar = $("#scanBar");
      const finished = !s.running && (s.phase === "Complete" || s.phase === "Stopped");
      let dismissed = false; try { dismissed = sessionStorage.getItem(dismissedKey) === (s.started_at || ""); } catch (_) {}
      if (s.running || (finished && !dismissed)) {
        bar.innerHTML = scanBarHtml(s); bar.classList.remove("hidden");
        const stop = $("#scanStop"); if (stop) stop.onclick = () => run(stop, () => post("/api/scan/stop"));
        const done = $("#scanDone"); if (done) done.onclick = () => { try { sessionStorage.setItem(dismissedKey, s.started_at || ""); } catch (_) {} bar.classList.add("hidden"); showTab("main"); };
      } else bar.classList.add("hidden");
      if (s.running) { scanWasRunning = true; $("#scanBtn").disabled = true; } else { $("#scanBtn").disabled = false; if (scanWasRunning) { scanWasRunning = false; renderCurrent(); } }
      if (!s.running) break;
      await new Promise(r => setTimeout(r, 1200));
    }
  } finally { scanWatching = false; }
}

// ------------------------------------------------------------------ main tab: jobs
let FILTER = "best", QUERY = "";
const PICKED = new Set();
const scoreClass = s => s >= 85 ? "a" : s >= 70 ? "b" : s >= 55 ? "c" : "d";
const ageTxt = d => d == null ? "" : d < 1 ? "today" : d < 2 ? "yesterday" : `${Math.round(d)} days ago`;
const isApplied = j => ["applied", "interviewing", "offer"].includes(j.status);

function tagsFor(j) {
  const t = [];
  if (isApplied(j)) t.push('<span class="chip good">Applied</span>');
  else if (j.app_status === "executing") t.push('<span class="chip info">Form opened</span>');
  if (j.source_type === "employer") t.push('<span class="chip">Company site</span>');
  if (j.people) t.push(`<span class="chip">${j.people} people</span>`);
  if (j.verification_status === "closed") t.push('<span class="chip bad">Closed</span>');
  return t.join("");
}

async function renderMain() {
  const el = $("#main");
  if (!HEALTH) HEALTH = await api("/api/health");
  const jobs = await api("/api/jobs?max_age=10");
  const min = HEALTH.settings.min_score_to_shortlist;
  const applied = jobs.filter(isApplied), open = jobs.filter(j => !isApplied(j)), best = open.filter(j => j.score >= min);
  const list = (FILTER === "best" ? best : FILTER === "applied" ? applied : open)
    .filter(j => !QUERY || `${j.title} ${j.company} ${j.city}`.toLowerCase().includes(QUERY.toLowerCase()));
  const issues = (HEALTH.issues || []).filter(i => !i.resolved);
  el.innerHTML = `
    ${issues.length ? `<div class="banner">Before applying, please confirm: ${issues.map(i => esc(i.field.replace(/_/g, " "))).join(", ")}. <a href="#" id="fixIssues">Open settings</a></div>` : ""}
    <div class="head"><div><h2>Jobs</h2><div class="sub">${best.length} strong match${best.length === 1 ? "" : "es"} · ${applied.length} applied · posted in the last 10 days</div></div>
      <div class="row"><button class="btn" id="addLink">Add a job link</button></div></div>
    <div class="tools"><div class="seg" id="filters">
        <button data-f="best" class="${FILTER === "best" ? "on" : ""}">Best matches ${best.length}</button>
        <button data-f="all" class="${FILTER === "all" ? "on" : ""}">All ${open.length}</button>
        <button data-f="applied" class="${FILTER === "applied" ? "on" : ""}">Applied ${applied.length}</button></div>
      <input type="search" id="q" placeholder="Search title, company, city" value="${esc(QUERY)}"></div>
    ${list.length ? `<div class="selbar ${PICKED.size ? "on" : ""}"><label class="row"><input type="checkbox" id="pickAll" ${list.every(j => PICKED.has(j.id)) ? "checked" : ""}> <span>${PICKED.size ? PICKED.size + " selected" : "Select all " + list.length}</span></label>
        <span class="row">${PICKED.size ? '<button class="btn" id="pickClear">Clear</button><button class="btn danger" id="pickDelete">Delete selected</button>' : '<span class="muted small">Tick jobs to delete several at once</span>'}</span></div>
      <div class="group">${list.map(j => `<div class="jrow ${PICKED.has(j.id) ? "picked" : ""}" data-id="${esc(j.id)}" tabindex="0" role="button">
        <input type="checkbox" class="pick" data-pick="${esc(j.id)}" ${PICKED.has(j.id) ? "checked" : ""} aria-label="Select ${esc(j.title)}">
        <span class="score ${scoreClass(j.score)}">${Math.round(j.score)}</span>
        <span><span class="jtitle">${esc(j.title)}</span><span class="jsub">${esc(j.company)} · ${esc(j.city || "India")}${j.age_days != null ? " · " + ageTxt(j.age_days) : ""}</span></span>
        <span class="jtags">${tagsFor(j)}</span>
        <button class="rowdel" data-deljob="${esc(j.id)}" title="Delete this job" aria-label="Delete ${esc(j.title)}">${ICON.trash}</button><span class="chev">›</span></div>`).join("")}</div>`
      : `<div class="card empty"><b>${jobs.length ? "Nothing here" : "No jobs yet"}</b>${jobs.length ? "Try another filter or clear the search." : "Press <b style='display:inline;font-size:inherit'>Scan for jobs</b> at the top right."}</div>`}`;
  $$("#filters button").forEach(b => b.onclick = () => { FILTER = b.dataset.f; renderMain(); });
  $("#q").oninput = e => { QUERY = e.target.value; clearTimeout(renderMain._t); renderMain._t = setTimeout(() => { renderMain().then(() => { const q = $("#q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }); }, 220); };
  $$(".jrow").forEach(r => { r.onclick = () => openJob(r.dataset.id); r.onkeydown = e => { if (e.key === "Enter" && e.target === r) openJob(r.dataset.id); }; });
  $$("[data-pick]").forEach(c => { c.onclick = e => e.stopPropagation(); c.onchange = () => { c.checked ? PICKED.add(c.dataset.pick) : PICKED.delete(c.dataset.pick); renderMain(); }; });
  $$("[data-deljob]").forEach(b => b.onclick = async e => { e.stopPropagation(); const j = list.find(x => x.id === b.dataset.deljob);
    if (!confirm(`Delete "${j.title}" at ${j.company}? It will not come back on the next scan.`)) return;
    await post("/api/jobs/delete", { ids: [j.id] }); PICKED.delete(j.id); toast("Job deleted"); renderMain(); });
  const pa = $("#pickAll"); if (pa) pa.onchange = () => { list.forEach(j => pa.checked ? PICKED.add(j.id) : PICKED.delete(j.id)); renderMain(); };
  const pc = $("#pickClear"); if (pc) pc.onclick = () => { PICKED.clear(); renderMain(); };
  const pd = $("#pickDelete"); if (pd) pd.onclick = () => { const n = PICKED.size;
    if (confirm(`Delete ${n} job${n === 1 ? "" : "s"}? They will not come back on the next scan.`)) run(pd, async () => { await post("/api/jobs/delete", { ids: [...PICKED] }); PICKED.clear(); toast(`${n} deleted`); renderMain(); }); };
  const fix = $("#fixIssues"); if (fix) fix.onclick = e => { e.preventDefault(); openSettings(); };
  $("#addLink").onclick = openAddLink;
  refreshSheetCount();
}

function openAddLink() {
  $("#modalBody").innerHTML = `<h3>Add a job link</h3><p class="muted small">Paste a link to any job (LinkedIn, Naukri, a company careers page). It must have been posted in the last 10 days.</p>
    <input id="linkUrl" placeholder="https://…" style="margin:8px 0 16px"><div class="row" style="justify-content:flex-end"><button class="btn" data-modal-close>Cancel</button><button class="btn primary" id="linkGo">Add</button></div>`;
  $("#modal").classList.remove("hidden"); $("#linkUrl").focus();
  $("#linkGo").onclick = e => run(e.target, async () => {
    const r = await post("/api/jobs/import", { url: $("#linkUrl").value.trim() });
    $("#modal").classList.add("hidden"); toast(r.new ? "Added" : "Already in your list"); await renderMain(); openJob(r.id);
  });
}

// ------------------------------------------------------------------ job panel
let applyWatch = null;
function stopApplyWatch() { applyWatch = null; }

function stepsHtml(session) {
  if (!session || !session.steps?.length) return "";
  const icon = s => s === "done" ? '<span class="ico done">✓</span>' : s === "failed" ? '<span class="ico failed">!</span>'
    : s === "running" ? '<span class="ico running"></span>' : s === "waiting" ? '<span class="ico wait">?</span>' : s === "skipped" ? '<span class="ico">–</span>' : '<span class="ico">·</span>';
  return `<div class="section-title">Progress</div><ul class="group steps">${session.steps.map(s => `<li>${icon(s.state)}<div><div>${esc(s.label)}</div>${s.detail ? `<div class="muted small">${esc(s.detail)}</div>` : ""}</div></li>`).join("")}</ul>`;
}

async function openJob(id) {
  const [j, li, review] = await Promise.all([api(`/api/jobs/${id}`), api("/api/linkedin"), api(`/api/jobs/${id}/email-review`)]);
  const session = await api(`/api/jobs/${id}/apply`);
  const ppl = li.filter(r => r.job_id === id);
  const sb = j.score_breakdown || { parts: {}, caps: [] };
  const vn = j.verification_notes || { notes: [], flags: [] };
  const mails = (j.actions || []).filter(a => ["email", "followup_email"].includes(a.kind) && a.status !== "rejected");
  const applied = isApplied(j), formOpen = j.actions.some(a => a.kind === "application" && a.status === "executing");
  const rev = review.filter(r => !r.sent);
  const showReview = rev.length && HEALTH?.integrations?.smtp && (!session.steps?.length || session.finished);
  const html = `
    <div class="p-head"><span class="muted small">${esc(j.source_type === "employer" ? "On the company's own site" : "Found on " + (j.publisher || j.source))}</span><button class="icon" data-close aria-label="Close">&times;</button></div>
    <div class="p-title"><span class="score lg ${scoreClass(j.score)}">${Math.round(j.score)}</span><div><h2>${esc(j.title)}</h2>
      <div class="muted">${esc(j.company)} · ${esc(j.location || j.city || "")}${j.age_days != null ? " · " + ageTxt(j.age_days) : ""}</div></div></div>
    <div class="row" style="margin-top:8px">${tagsFor(j)}${j.salary_min_lpa || j.salary_max_lpa ? `<span class="chip">${j.salary_min_lpa ?? "?"}–${j.salary_max_lpa ?? "?"} LPA</span>` : ""}
      ${vn.flags?.length ? '<span class="chip bad">Check this listing</span>' : ""}</div>
    <div class="cta">
      ${applied ? '<span class="chip good" style="font-size:14px;padding:6px 14px">✓ Applied</span>' : `<button class="btn primary big" id="applyBtn">Auto-fill &amp; apply</button>`}
      ${j.url ? `<a class="btn" href="${esc(j.url)}" target="_blank" rel="noopener">Open job page</a>` : ""}
    </div>
    <div id="stepsBox">${stepsHtml(session)}</div>
    ${!applied && (formOpen || session.finished && session.steps?.length) ? `<div class="card" style="margin-top:12px"><div class="row space"><span class="small muted">Review the form in Chrome and press <b>Submit</b> there.</span>
      <button class="btn" id="submittedBtn">I submitted it</button></div></div>` : ""}

    ${showReview ? `<div class="card review"><div class="rv-title">Email HR and the hiring team</div>
      <p class="muted small" style="margin:2px 0 10px">Addresses marked <b>guess</b> are built from the company's email format and may be wrong. Fix or type the right one, untick anyone you do not want to email, then press Send.</p>
      ${rev.map(r => `<div class="rv" data-cid="${r.contact_id}"><label class="rv-top"><input type="checkbox" class="rv-send" ${r.email && r.ok ? "checked" : ""}>
          <b>${esc(r.name || "Unknown")}</b><span class="chip ${r.who === "hr" ? "info" : "good"}">${r.who === "hr" ? "HR" : "Hiring team"}</span></label>
        <div class="muted small rv-role">${esc(r.title || "")}</div>
        <input class="rv-email" type="email" value="${esc(r.email)}" placeholder="name@company.com" autocomplete="off" spellcheck="false">
        <div class="small ${r.email && !r.ok ? "bad-t" : "muted"}">${r.email ? `<b>${["public", "verified"].includes(r.status) ? "Known address" : "Guess"}.</b> ` : ""}${esc(r.reason)}</div></div>`).join("")}
      <div class="row" style="margin-top:12px"><button class="btn primary" id="revSend">Send emails</button><button class="btn" id="revSkip">Skip emails</button></div></div>` : ""}

    ${ppl.length ? `<div class="section-title">HR and hiring team</div><div class="group">${ppl.map(p => `<div class="item"><div class="row space"><div><b>${esc(p.name)}</b>
      <div class="muted small">${esc(p.title || "")}</div></div><span class="chip ${p.who === "hr" ? "info" : "good"}">${p.who === "hr" ? "HR" : "Hiring team"}</span></div></div>`).join("")}
      <div class="item"><a href="#" id="toSheet">Open their LinkedIn notes in the Sheet ›</a></div></div>` : ""}

    ${mails.length ? `<div class="section-title">Emails</div><div class="group">${mails.map(a => {
      const bounced = a.result && a.result.bounced;
      return `<div class="item" data-mail="${a.id}"><div class="row space"><div><b>To ${esc(a.recipient)}</b><div class="muted small">${esc(a.subject || "")}</div></div>
        ${a.status === "done" ? (bounced ? '<span class="chip bad">Bounced</span>' : '<span class="chip good">Sent</span>') : a.status === "failed" ? '<span class="chip bad">Not sent</span>' : '<span class="chip warn">Draft</span>'}</div>
        ${a.status === "failed" && a.result?.error ? `<div class="small" style="color:var(--red);margin-top:6px">${esc(a.result.error)}</div>` : ""}
        ${["draft", "failed", "approved"].includes(a.status) ? `<details><summary class="small">Read the email</summary><div class="body desc small">${esc(a.body || "")}</div></details>
          <div class="row" style="margin-top:8px"><button class="btn primary" data-send="${a.id}">Send</button><button class="btn danger" data-delmail="${a.id}">Delete</button></div>` : ""}</div>`; }).join("")}</div>` : ""}

    <details><summary>Why this score</summary><div class="body">${Object.entries(sb.parts || {}).map(([k, p]) => `<div class="row" style="margin:6px 0"><span style="width:84px" class="muted small">${esc(k[0].toUpperCase() + k.slice(1))}</span>
      <div class="bar-meter"><i style="width:${100 * p.points / p.max}%"></i></div><span class="small" style="width:44px">${p.points}/${p.max}</span><span class="small muted" style="flex-basis:100%;margin-left:92px;margin-top:-4px">${esc(p.reason)}</span></div>`).join("")}
      ${sb.parts?.skills ? `<div class="small" style="margin-top:8px"><b>On your resume:</b> ${(sb.parts.skills.matched || []).map(s => `<span class="chip good">${esc(s)}</span>`).join(" ") || "none"}<br><b>Asked for, not on your resume:</b> ${(sb.parts.skills.missing || []).map(s => `<span class="chip warn">${esc(s)}</span>`).join(" ") || "none"}</div>` : ""}
      <ul class="plain small muted">${(vn.notes || []).map(n => `<li>${esc(n)}</li>`).join("")}${(vn.flags || []).map(n => `<li style="color:var(--red)">${esc(n)}</li>`).join("")}</ul></div></details>
    <details><summary>Job description</summary><div class="body desc">${esc(j.description || "No description.")}</div></details>
    <div class="row" style="margin-top:22px"><button class="btn danger" id="skipBtn">Delete this job</button></div>`;
  openPanel("#drawer", html);

  const ab = $("#applyBtn"); if (ab) ab.onclick = () => openApplyDialog(j);
  $$(".rv").forEach(r => $(".rv-email", r).oninput = () => { $(".rv-send", r).checked = !!$(".rv-email", r).value.trim(); });
  const rs = $("#revSend"); if (rs) rs.onclick = () => run(rs, async () => {
    const emails = $$(".rv").map(r => ({ contact_id: +r.dataset.cid, email: $(".rv-email", r).value.trim(), send: $(".rv-send", r).checked }));
    const r = await post(`/api/jobs/${id}/email-team`, { emails }); toast(r.detail, 9000); openJob(id); });
  const rk = $("#revSkip"); if (rk) rk.onclick = () => run(rk, async () => { await post(`/api/jobs/${id}/email-team`, { emails: [] }); toast("No emails sent for this job"); openJob(id); });
  const sub = $("#submittedBtn"); if (sub) sub.onclick = () => run(sub, async () => { await post(`/api/jobs/${id}/submitted`); toast("Marked as applied"); openJob(id); });
  const sk = $("#skipBtn"); if (sk) sk.onclick = () => run(sk, async () => { if (!confirm("Delete this job from your list? It will not come back on the next scan.")) return; await post("/api/jobs/delete", { ids: [id] }); closePanels(); toast("Job deleted"); });
  const ts = $("#toSheet"); if (ts) ts.onclick = e => { e.preventDefault(); closePanels(); showTab("sheet"); };
  $$("[data-send]").forEach(b => b.onclick = () => run(b, async () => {
    await post(`/api/actions/${b.dataset.send}/approve`, { confirm: true });
    const a = await post(`/api/actions/${b.dataset.send}/execute`);
    toast(a.status === "done" ? "Email sent" : (a.result?.error || "Not sent")); openJob(id);
  }));
  $$("[data-delmail]").forEach(b => b.onclick = () => run(b, async () => { await del(`/api/actions/${b.dataset.delmail}`); openJob(id); }));
  if (session.steps?.length && !session.finished) watchApply(id);
}

function openApplyDialog(j) {
  const smtp = HEALTH?.integrations?.smtp;
  $("#modalBody").innerHTML = `<h3>Auto-fill &amp; apply</h3><div class="muted small">${esc(j.company)} · ${esc(j.title)}</div>
    <ul class="checks">
      <li><span class="tick">✓</span><span>Open the application form in Chrome and fill it in. <b>You press Submit.</b></span></li>
      <li><span class="tick">✓</span><span>Find the 2–3 most relevant people (HR and the hiring team) and save a personal LinkedIn note for each to the Sheet.</span></li>
      <li><label class="row" style="align-items:flex-start;flex-wrap:nowrap"><input type="checkbox" id="mEmail" ${smtp ? "checked" : "disabled"}>
        <span>Email the same people too, straight from my Gmail. <span class="muted">When no real address is known it is guessed from the company's format; bounces are tracked and fixed for next time.</span>${smtp ? "" : ' <span class="muted">Connect Gmail in Settings first.</span>'}</span></label></li>
      <li><label class="row" style="align-items:flex-start;flex-wrap:nowrap"><input type="checkbox" id="mReview" ${smtp ? "checked" : "disabled"}>
        <span>Let me check or fix the email addresses before they are sent. <span class="muted">Recommended: guessed addresses can be wrong. Untick to send straight away.</span></span></label></li>
      <li><label class="row" style="align-items:flex-start;flex-wrap:nowrap"><input type="checkbox" id="mTailor">
        <span>Customise my resume and cover letter for this job. <span class="muted">Off: your original resume is used.</span></span></label></li>
      <li><span class="tick"></span><label style="flex:1"><span class="small muted">Resume to use</span>
        <select id="mResume"><option value="A" ${j.resume_key === "A" ? "selected" : ""}>Product Marketing</option><option value="B" ${j.resume_key === "B" ? "selected" : ""}>Category &amp; Marketplace</option></select></label></li>
    </ul>
    <div class="row" style="justify-content:flex-end"><button class="btn" data-modal-close>Cancel</button><button class="btn primary" id="goApply">Auto-fill &amp; apply</button></div>`;
  $("#modal").classList.remove("hidden");
  $("#goApply").onclick = e => run(e.target, async () => {
    await post(`/api/jobs/${j.id}/apply`, { tailor: $("#mTailor").checked, resume_key: $("#mResume").value, email: $("#mEmail").checked, review: $("#mReview").checked });
    $("#modal").classList.add("hidden"); await openJob(j.id); watchApply(j.id);
  });
}

async function watchApply(id) {
  const token = applyWatch = {};
  for (;;) {
    const s = await api(`/api/jobs/${id}/apply`);
    if (applyWatch !== token) return;
    const box = $("#stepsBox"); if (!box) return;
    box.innerHTML = stepsHtml(s);
    if (s.finished) { renderMain(); refreshSheetCount(); openJob(id); return; }
    await new Promise(r => setTimeout(r, 1500));
  }
}

// ------------------------------------------------------------------ sheet tab: LinkedIn people, grouped by job
let SHEET_FILTER = "todo";
async function refreshSheetCount() {
  try { const rows = await api("/api/linkedin"); const n = rows.filter(r => !r.note_sent_at).length; $("#sheetCount").textContent = n || ""; } catch (_) {}
}
const stageOf = r => r.replied_at ? ["replied", "Replied", "good"] : r.message_sent_at ? ["msg", "Message sent", "info"] : r.note_sent_at ? ["note", "Waiting for them to accept", "warn"] : ["todo", "To connect", ""];
async function renderSheet() {
  const [rows, pats] = await Promise.all([api("/api/linkedin"), api("/api/email-patterns")]);
  const todo = rows.filter(r => !r.note_sent_at), waiting = rows.filter(r => r.note_sent_at && !r.replied_at), replied = rows.filter(r => r.replied_at);
  $("#sheetCount").textContent = todo.length || "";
  const shown = SHEET_FILTER === "todo" ? todo : SHEET_FILTER === "done" ? rows.filter(r => r.note_sent_at) : rows;
  const byJob = {}; shown.forEach(r => (byJob[r.job_id] = byJob[r.job_id] || []).push(r));
  const date = s => s ? s.slice(5, 10) : "";
  const person = r => {
    const [stage, stageLbl, stageCls] = stageOf(r);
    const known = ["public", "verified"].includes(r.email_status);
    const emailLine = r.email_sent ? `<span class="chip good">Email sent</span> <span class="muted small">${esc(r.email_guess || "")}</span>`
      : `<span class="muted small">Email</span><input class="em-in" type="email" data-email value="${esc(r.email_guess || "")}" placeholder="Add an email address" autocomplete="off" spellcheck="false">
         ${r.email_guess ? `<span class="chip ${known ? "good" : r.email_ok ? "warn" : "bad"}" title="${esc(r.email_reason || "")}">${known ? "Known" : r.email_ok ? "Guess" : "Blocked"}</span>` : ""}
         <button class="btn" data-sendmail ${HEALTH?.integrations?.smtp ? "" : "disabled"}>Send email</button>`;
    return `<div class="pcard ${stage}" data-li="${r.id}">
      <div class="pc-main"><span class="avatar">${esc((r.name || "?").trim()[0] || "?")}</span>
        <div class="pc-who"><div><b>${esc(r.name)}</b> <span class="chip ${r.who === "hr" ? "info" : "good"}">${r.who === "hr" ? "HR" : "Hiring team"}</span> <span class="chip ${stageCls}">${stageLbl}${stage !== "todo" ? " · " + date(r.replied_at || r.message_sent_at || r.note_sent_at) : ""}</span></div>
          <div class="muted small">${esc(r.title || "")}</div></div>
        <button class="rowdel" data-del title="Remove this person" aria-label="Remove">${ICON.trash}</button></div>
      <div class="pc-note"><div class="small muted">Connection note <span data-count>${(r.note || "").length}</span>/200</div><div class="note-txt" data-notetxt>${esc(r.note || "")}</div></div>
      <div class="pc-actions">
        ${stage === "todo" ? `<a class="btn primary" href="${esc(r.linkedin_url)}" target="_blank" rel="noopener" data-open>1. Open profile (note is copied)</a><button class="btn" data-mark="note_sent_at">2. I sent the note</button>`
          : stage === "note" ? `<a class="btn" href="${esc(r.linkedin_url)}" target="_blank" rel="noopener">Open profile</a><button class="btn" data-copy="message">Copy follow-up message</button><button class="btn primary" data-mark="message_sent_at">I sent the message</button>`
          : `<a class="btn" href="${esc(r.linkedin_url)}" target="_blank" rel="noopener">Open profile</a>${stage === "msg" ? '<button class="btn primary" data-mark="replied_at">They replied</button>' : ""}`}
        <button class="btn plain" data-copy="note">Copy note</button><details class="edit"><summary class="small">Edit text</summary>
          <textarea data-f="note" style="min-height:64px">${esc(r.note || "")}</textarea><div class="small muted" style="margin-top:6px">Message to send after they accept</div><textarea data-f="message">${esc(r.message || "")}</textarea></details></div>
      <div class="pc-email">${emailLine}</div></div>`;
  };
  $("#sheet").innerHTML = `
    <div class="head"><div><h2>Sheet</h2><div class="sub">The HR and hiring-team people for jobs you applied to, and where each one stands.</div></div>
      <a class="btn" href="/api/outreach-sheet">Download Excel</a></div>
    <div class="steps-help card"><div><b>1</b> Open the profile. The note is copied for you.</div><div><b>2</b> Press Connect, paste the note, send it, then come back and press "I sent the note".</div><div><b>3</b> When they accept, send the follow-up message. You can also email them from here.</div></div>
    <div class="stats"><div class="stat"><b>${todo.length}</b><span>To connect</span></div><div class="stat"><b>${waiting.length}</b><span>Waiting for reply</span></div><div class="stat"><b>${replied.length}</b><span>Replied</span></div><div class="stat"><b>${rows.length}</b><span>Total</span></div></div>
    <div class="tools"><div class="seg" id="sheetFilters">
      <button data-f="todo" class="${SHEET_FILTER === "todo" ? "on" : ""}">To connect ${todo.length}</button>
      <button data-f="done" class="${SHEET_FILTER === "done" ? "on" : ""}">Note sent ${rows.length - todo.length}</button>
      <button data-f="all" class="${SHEET_FILTER === "all" ? "on" : ""}">All ${rows.length}</button></div></div>
    ${Object.keys(byJob).length ? Object.values(byJob).map(g => `<div class="jgroup"><div class="job-head"><span>${esc(g[0].company)} <span class="muted" style="font-weight:400">· ${esc(g[0].job_title)}</span></span>
        <span class="row"><span class="muted small">${g.filter(r => r.note_sent_at).length} of ${g.length} connected</span><a href="#" data-openjob="${esc(g[0].job_id)}" class="small">Open job ›</a></span></div>
      <div class="pgrid">${g.map(person).join("")}</div></div>`).join("")
      : `<div class="card empty"><b>${rows.length ? "All done" : "Nothing here yet"}</b>${rows.length ? "Every note has been sent." : "When you press Auto-fill &amp; apply on a job, its HR and hiring-team people appear here."}</div>`}
    <details class="card" style="margin-top:14px"><summary>I found a real email address</summary><div class="body">
      <p class="muted small" style="margin-top:0">Add one real address from a company and the app learns that company's email format for every later guess.</p>
      <div class="form"><label>Company<input id="keCompany" placeholder="Meesho"></label><label>Email<input id="keEmail" placeholder="name@company.com"></label>
        <label>Name (optional)<input id="keName"></label><label>Title (optional)<input id="keTitle"></label></div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="keAdd">Add</button></div><div id="keResult" class="small" style="margin-top:8px"></div>
      ${pats.length ? `<table style="margin-top:10px"><tr><th>Company</th><th>Format</th><th>Real example</th><th>Bounced guesses</th></tr>${pats.map(p => `<tr><td>${esc(p.domain)}</td><td>${esc(p.pattern || "unknown")}</td><td>${esc(p.example || "none")}</td><td>${p.bounces}</td></tr>`).join("")}</table>` : ""}</div></details>`;
  $$("#sheetFilters button").forEach(b => b.onclick = () => { SHEET_FILTER = b.dataset.f; renderSheet(); });
  $$("[data-openjob]").forEach(a => a.onclick = e => { e.preventDefault(); openJob(a.dataset.openjob); });
  const byId = Object.fromEntries(rows.map(r => [r.id, r]));
  $$("[data-li]").forEach(card => {
    const id = card.dataset.li, row = byId[id], save = f => put(`/api/linkedin/${id}`, f);
    $$("textarea[data-f]", card).forEach(t => {
      t.oninput = () => { if (t.dataset.f === "note") { const c = $("[data-count]", card); c.textContent = t.value.length; c.style.color = t.value.length > 200 ? "var(--red)" : ""; $("[data-notetxt]", card).textContent = t.value; } };
      t.onchange = () => save({ [t.dataset.f]: t.value }).catch(e => toast(e.message, 6000));
    });
    $$("[data-copy]", card).forEach(b => b.onclick = async () => { await copy($(`textarea[data-f="${b.dataset.copy}"]`, card).value); toast(b.dataset.copy === "note" ? "Note copied. Paste it when you connect." : "Message copied."); });
    const op = $("[data-open]", card); if (op) op.onclick = async () => { await copy($('textarea[data-f="note"]', card).value); toast("Note copied. Paste it when you press Connect."); };
    $$("[data-mark]", card).forEach(b => b.onclick = async () => { await save({ [b.dataset.mark]: true }); renderSheet(); });
    $("[data-del]", card).onclick = async () => { if (confirm("Remove this person from the Sheet?")) { await del(`/api/linkedin/${id}`); renderSheet(); } };
    const em = $("[data-email]", card);
    if (em) {
      em.onchange = async () => { try { await save({ email: em.value }); toast(em.value.trim() ? "Email saved. It is now treated as a known address." : "Email removed"); renderSheet(); } catch (e) { toast(e.message, 6000); } };
      const sm = $("[data-sendmail]", card);
      sm.onclick = () => run(sm, async () => {
        if (!em.value.trim()) { toast("Type an email address first."); return; }
        const r = await post(`/api/jobs/${row.job_id}/email-team`, { emails: [{ contact_id: row.contact_id, email: em.value.trim(), send: true }] });
        toast(r.detail, 8000); renderSheet(); });
    }
  });
  $("#keAdd").onclick = e => run(e.target, async () => {
    const r = await post("/api/known-email", { company: $("#keCompany").value, email: $("#keEmail").value, name: $("#keName").value, title: $("#keTitle").value });
    $("#keResult").innerHTML = `Learned <b>${esc(r.domain)}</b>: <b>${esc(r.pattern || "format unclear from one address")}</b>. ` +
      (r.trust.ok ? `A draft email for <i>${esc(r.job)}</i> is ready on that job.` : `<span style="color:var(--red)">${esc(r.trust.reason)}</span>`);
  });
}

// ------------------------------------------------------------------ settings panel
async function openSettings() { openPanel("#settingsDrawer", '<div class="muted">Loading…</div>'); await renderSettings(); }
$("#gearBtn").onclick = openSettings;

// ------------------------------------------------------------------ settings
async function renderSettings() {
  const [p, h, companies, cr] = await Promise.all([api("/api/profile"), api("/api/health"), api("/api/companies"), api("/api/credits")]);
  HEALTH = h;
  const prof = p.profile, s = h.settings, integ = h.integrations;
  const ok = b => b ? '<span class="chip good">configured</span>' : '<span class="chip">not configured</span>';
  $("#settingsBody").innerHTML = `
    <h2>Confirm your details</h2>
    ${p.issues.length ? p.issues.map(i => `<div class="banner ${i.resolved ? "info" : ""}">${esc(i.detail)}${i.resolved ? " (resolved)" : ""}</div>`).join("") : ""}
    <div class="card form">
      <label>Official current title (used on forms)<select id="sTitle"><option value="">Not confirmed: leave form field blank</option>
        ${prof.current_title_candidates.map(t => `<option ${t === prof.current_title ? "selected" : ""}>${esc(t)}</option>`).join("")}
        ${prof.current_title && !prof.current_title_candidates.includes(prof.current_title) ? `<option selected>${esc(prof.current_title)}</option>` : ""}</select></label>
      <label>Or type the exact title<input id="sTitleFree" placeholder="as on your offer letter"></label>
      <label>Total experience (years)<input id="sExp" type="number" step="0.1" value="${prof.total_experience_years}"></label>
      <label>Post-MBA experience (years)<input id="sPost" type="number" step="0.1" value="${prof.post_mba_experience_years}"></label>
      <label>Notice period<input id="sNotice" value="${esc(prof.notice_period || "")}" placeholder="e.g. 60 days"></label>
      <label>LinkedIn URL<input id="sLi" value="${esc(prof.linkedin_url || "")}"></label>
    </div>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="sSaveProf">Save details</button></div>

    <h2>Application form answers</h2>
    <p class="muted small">Used when pre-filling forms. Date of birth, address, ID numbers, gender/diversity questions and consent boxes are never filled.</p>
    <div class="card form">
      <label>Expected CTC (LPA)<input id="aExp" type="number" step="0.5" value="${esc(prof.expected_ctc_lpa ?? "")}"></label>
      <label>Current CTC fixed (LPA)<input id="aFix" type="number" step="0.5" value="${esc(prof.current_ctc?.fixed_lpa ?? "")}"></label>
      <label>Current CTC variable (LPA)<input id="aVar" type="number" step="0.5" value="${esc(prof.current_ctc?.variable_lpa ?? "")}"></label>
      <label>Also open to (cities, comma separated)<input id="aOpen" value="${esc((prof.open_locations || []).join(", "))}"></label>
      <label>MBA score<input id="aMba" value="${esc((prof.education || []).find(e => e.degree.startsWith("MBA"))?.score || "")}"></label>
      <label>BSc score<input id="aBsc" value="${esc((prof.education || []).find(e => e.degree.startsWith("BSc"))?.score || "")}"></label>
      <label>12th<input id="a12" value="${esc(prof.school_scores?.["12th"] || "")}"></label>
      <label>10th<input id="a10" value="${esc(prof.school_scores?.["10th"] || "")}"></label>
      <label>Work mode<input id="aMode" value="${esc(prof.work_mode || "")}"></label>
      <label>Willing to relocate<select id="aRel"><option value="true" ${prof.willing_to_relocate ? "selected" : ""}>Yes</option><option value="false" ${prof.willing_to_relocate === false ? "selected" : ""}>No</option></select></label>
    </div>
    <h3 style="margin-top:14px">Relevant experience in years (for "Relevant experience in ..." questions)</h3>
    <p class="muted small" style="margin-top:0">Left blank, those questions are skipped for you to answer. Enter only what you can stand behind.</p>
    <div class="card form">
      ${[["product_management", "Product management"], ["product_marketing", "Product marketing / GTM"], ["growth_marketing", "Growth / digital marketing"],
         ["category_marketplace", "Category / marketplace / e-commerce"], ["marketing", "Marketing overall"]].map(([k, l]) =>
        `<label>${l}<input data-rel="${k}" type="number" step="0.1" value="${esc(prof.relevant_experience?.[k] ?? "")}"></label>`).join("")}
    </div>
    <label class="small muted" style="display:block;margin-top:8px">Reason for leaving<textarea id="aWhy" style="min-height:60px">${esc(prof.reason_for_leaving || "")}</textarea></label>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="aSave">Save form answers</button></div>

    <h2>Search mix and credits</h2>
    <div class="card">
      <p class="small muted" style="margin-top:0">Company career sites are free and you apply on the employer's own form, so they get a fixed share of all searches (${cr.company_sites} company sites are scanned).
        The rest is split between LinkedIn (Apify), Naukri and Indeed (Firecrawl) and other portals (JSearch, Adzuna, Jooble).</p>
      <div class="form"><label>Company sites' share of searches<select id="mShare">${[50, 60, 70, 80, 90].map(v => `<option value="${v / 100}" ${Math.round((cr.company_share_setting || 0.5) * 100) === v ? "selected" : ""}>${v}%</option>`).join("")}</select></label>
        <label>When credits run low<select id="mAuto"><option value="true" ${cr.auto_balance ? "selected" : ""}>Shift more searches to company sites automatically</option><option value="false" ${cr.auto_balance ? "" : "selected"}>Keep my setting</option></select></label></div>
      <div class="small" style="margin:12px 0 4px">Right now company sites get <b>${Math.round(cr.company_share_now * 100)}%</b>${cr.company_share_now > (cr.company_share_setting || 0.5) + 0.01 ? " (raised automatically because a credit pool is low)" : ""}.
        Next scan: ${(() => { const g = cr.plan || {}, n = k => g[k] || 0; return `LinkedIn ${n("apify")}, Naukri &amp; Indeed ${n("naukri") + n("indeed")}, other portals ${n("jsearch") + n("adzuna") + n("jooble")} searches.`; })()}</div>
      <table style="margin-top:8px"><tr><th>Service</th><th>Left</th><th>Out of</th><th>Resets</th></tr>
        ${[["apify", "Apify (LinkedIn)"], ["firecrawl", "Firecrawl (Naukri, Indeed, finding people)"], ["jsearch", "JSearch (other portals)"], ["jooble", "Jooble (other portals)"]].map(([k, l]) => {
          const c = cr.credits[k]; if (!c) return `<tr><td>${l}</td><td colspan="3" class="muted small">Not connected. Add the key in <code>.env</code></td></tr>`;
          const f = v => c.unit === "USD" ? "$" + v : v;
          return `<tr><td>${l}</td><td><b>${f(c.remaining)}</b></td><td>${f(c.limit)} ${c.unit === "USD" ? "this month" : esc(c.unit)}</td><td class="small muted">${esc(c.resets || (k === "jooble" ? "never (lifetime)" : "monthly"))}</td></tr>`; }).join("")}
        <tr><td>Adzuna (other portals)</td><td colspan="3" class="muted small">${h.integrations.adzuna ? "Connected" : "Not connected. Add the key in <code>.env</code>"}</td></tr></table>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="mSave">Save mix</button></div>
    </div>

    <h2>Search settings</h2>
    <div class="card form">
      <label>Maximum job age (days, max 10)<input id="sAge" type="number" min="1" max="10" value="${s.max_job_age_days}"></label>
      <label>Auto-shortlist at score ≥<input id="sMin" type="number" value="${s.min_score_to_shortlist}"></label>
      <label>Follow up after (days)<input id="sFu" type="number" value="${s.followup_after_days}"></label>
      <label>Max emails per day<input id="sMax" type="number" value="${s.max_emails_per_day}"></label>
      <label>People to contact per role (2 or 3)<input id="sPeople" type="number" min="2" max="3" value="${s.people_per_job || 3}"></label>
      <label>Portal search keywords (one per line)<textarea id="sKw">${esc(s.search_keywords.join("\n"))}</textarea></label>
      <label>Cities (one per line)<textarea id="sCities">${esc(s.scan_cities.join("\n"))}</textarea></label>
    </div>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="sSaveSet">Save search settings</button></div>

    <h2>Sign in to job sites</h2>
    <div class="card"><p style="margin-top:0" class="small">Needed once per site so pre-fill can open your applications. Opens a normal Chrome window where
      <b>Continue with Google works</b>. Sign in, then close that window (pre-fill can't run while it's open). Your passwords are never seen or stored by this app.</p>
      <div class="row">${[["linkedin", "LinkedIn"], ["naukri", "Naukri"], ["shine", "Shine"], ["indeed", "Indeed"], ["iimjobs", "iimjobs"], ["wellfound", "Wellfound"], ["cutshort", "Cutshort"], ["michaelpage", "Michael Page"]].map(([s, l]) => `<button class="btn" data-signin="${s}">${l}</button>`).join("")}</div></div>

    <h2>Gmail</h2>
    <div class="card" id="gmailCard"></div>

    <h2>Integrations</h2>
    <div class="card"><table>
      <tr><td>Claude (better drafts)</td><td>${ok(integ.claude)}</td><td class="small muted">ANTHROPIC_API_KEY. Without it, drafts use templates built from your resume.</td></tr>
      <tr><td>JSearch: LinkedIn, Naukri, Indeed, Glassdoor via Google for Jobs</td><td>${ok(integ.jsearch)}</td><td class="small muted">RAPIDAPI_KEY</td></tr>
      <tr><td>Apify: LinkedIn jobs</td><td>${ok(integ.apify)}</td><td class="small muted">APIFY_TOKEN. Free plan = $5 a month; each search is capped at about $${(s.apify_results_per_query || 25) * 0.002}.</td></tr>
      <tr><td>Adzuna India</td><td>${ok(integ.adzuna)}</td><td class="small muted">ADZUNA_APP_ID, ADZUNA_APP_KEY (free)</td></tr>
      <tr><td>Jooble</td><td>${ok(integ.jooble)}</td><td class="small muted">JOOBLE_API_KEY (free, 500 requests in total)</td></tr>
      <tr><td>Naukri + Indeed via Firecrawl (low volume)</td><td>${ok(integ.naukri)}</td><td class="small muted">FIRECRAWL_API_KEY. ~${(s.naukri_max_searches_per_scan||0)+(s.indeed_max_searches_per_scan||0)+(s.naukri_max_detail_pages_per_scan||12)+(s.indeed_max_detail_pages_per_scan||8)} credits max per scan.</td></tr>
      <tr><td>Apollo.io recruiter emails</td><td>${ok(integ.apollo)}</td><td class="small muted">APOLLO_API_KEY. Search is free; each email revealed uses 1 Apollo credit (max ${s.apollo_max_reveals_per_job || 3} per job).</td></tr>
      <tr><td>Hunter.io recruiter emails</td><td>${ok(integ.hunter)}</td><td class="small muted">HUNTER_API_KEY</td></tr>
      <tr><td>Send approved emails (SMTP)</td><td>${ok(integ.smtp)}</td><td class="small muted">SMTP_USER, SMTP_PASSWORD (Gmail app password)</td></tr>
      <tr><td>Reply tracking (IMAP)</td><td>${ok(integ.imap)}</td><td class="small muted">IMAP_HOST + the SMTP credentials</td></tr>
    </table><p class="small muted">Keys go in <code>.env</code> in the project folder (see <code>.env.example</code>), then restart the app.</p></div>

    <h2>Delete</h2>
    <div class="card"><div class="row space"><div>Delete all drafts and approvals<div class="muted small">Removes every draft email, queued application and pending item. Emails already sent stay, so bounces and replies keep working.</div></div>
      <button class="btn danger" id="delApprovals">Delete</button></div></div>

    <h2>Company career sites scanned (${companies.length})</h2>
    <div class="card">
      <div class="form"><label>Company<input id="coName"></label><label>Careers page URL<input id="coUrl" placeholder="https://company.com/careers"></label>
        <label>Email domain<input id="coDom" placeholder="company.com"></label></div>
      <div class="row" style="margin-top:8px"><button class="btn" id="coAdd">Add company</button></div>
      <p class="small">${companies.map(c => `<span class="chip">${esc(c.name)} · ${esc(c.ats)} <a href="#" data-delco="${esc(c.name)}">×</a></span>`).join(" ")}</p>
    </div>`;
  renderGmail();
  $("#delApprovals").onclick = e => { if (confirm("Delete all drafts and approvals? This cannot be undone.")) run(e.target, async () => { const r = await del("/api/actions"); toast(`Deleted ${r.deleted} item${r.deleted === 1 ? "" : "s"}`); renderSettings(); }); };
  document.querySelectorAll("[data-signin]").forEach(b => b.onclick = () => run(b, async () => {
    await post("/api/browser/signin", { site: b.dataset.signin }); toast("Chrome opened. Sign in there, then close that window.", 6000); }));
  $("#sSaveProf").onclick = e => run(e.target, async () => {
    await put("/api/profile", { current_title: $("#sTitleFree").value.trim() || $("#sTitle").value, total_experience_years: +$("#sExp").value,
      post_mba_experience_years: +$("#sPost").value, notice_period: $("#sNotice").value, linkedin_url: $("#sLi").value });
    toast("Saved"); HEALTH = null; renderSettings();
  });
  $("#aSave").onclick = e => run(e.target, async () => {
    const fix = +$("#aFix").value, vr = +$("#aVar").value;
    await put("/api/profile", { expected_ctc_lpa: +$("#aExp").value, current_ctc: { total_lpa: fix + vr, fixed_lpa: fix, variable_lpa: vr },
      open_locations: $("#aOpen").value.split(",").map(x => x.trim()).filter(Boolean), work_mode: $("#aMode").value,
      willing_to_relocate: $("#aRel").value === "true", reason_for_leaving: $("#aWhy").value,
      school_scores: { "12th": $("#a12").value, "10th": $("#a10").value },
      relevant_experience: Object.fromEntries([...document.querySelectorAll("[data-rel]")].map(i => [i.dataset.rel, i.value === "" ? null : +i.value])), degree_scores: { mba: $("#aMba").value, bsc: $("#aBsc").value } });
    toast("Saved"); HEALTH = null; renderSettings();
  });
  $("#mSave").onclick = e => run(e.target, async () => { await put("/api/settings", { company_share: +$("#mShare").value, auto_balance: $("#mAuto").value === "true" }); toast("Saved"); HEALTH = null; renderSettings(); });
  $("#sSaveSet").onclick = e => run(e.target, async () => {
    const lines = id => $(id).value.split("\n").map(x => x.trim()).filter(Boolean);
    await put("/api/settings", { max_job_age_days: +$("#sAge").value, min_score_to_shortlist: +$("#sMin").value,
      followup_after_days: +$("#sFu").value, max_emails_per_day: +$("#sMax").value, people_per_job: Math.min(3, Math.max(2, +$("#sPeople").value || 3)), search_keywords: lines("#sKw"), scan_cities: lines("#sCities") });
    toast("Saved"); HEALTH = null; renderSettings();
  });
  $("#coAdd").onclick = e => run(e.target, async () => {
    const r = await post("/api/companies", { name: $("#coName").value, careers_url: $("#coUrl").value, domain: $("#coDom").value });
    toast(`Added ${r.added.name} (${r.added.ats})`); renderSettings();
  });
  document.querySelectorAll("[data-delco]").forEach(a => a.onclick = ev => { ev.preventDefault();
    run(null, async () => { await api(`/api/companies/${encodeURIComponent(a.dataset.delco)}`, { method: "DELETE" }); renderSettings(); }); });
}

async function renderGmail() {
  const g = await api("/api/gmail"), el = $("#gmailCard");
  if (g.connected) {
    el.innerHTML = `<div class="row space"><div><span class="chip good">connected</span> Approved emails send from <b>${esc(g.email)}</b>.
      Replies are tracked from its inbox.</div><div class="row"><button class="btn" id="gTest">Send me a test email</button>
      <button class="btn danger" id="gOff">Disconnect</button></div></div>`;
    $("#gTest").onclick = e => run(e.target, async () => { const r = await post("/api/gmail/test"); toast(`Test email sent to ${r.sent_to}. Check your inbox.`); });
    $("#gOff").onclick = e => run(e.target, async () => { await post("/api/gmail/disconnect"); toast("Disconnected"); renderGmail(); });
    return;
  }
  el.innerHTML = `${g.problem ? `<div class="banner">${esc(g.problem)}</div>` : ""}
    <p style="margin-top:0">Connect your Gmail so approved emails go out from your address. Google needs an <b>app password</b> for this
      (a separate 16-letter code just for this app; your normal password is never used).</p>
    <ol class="small">
      <li>Turn on <a href="https://myaccount.google.com/signinoptions/twosv" target="_blank" rel="noopener">2-Step Verification</a> if it isn't on.</li>
      <li>Make sure Google shows <b>${esc(g.email || "your Gmail address")}</b> at the top right (switch account if not), then open <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">App passwords</a>, type <i>Job Search Agent</i>, click <b>Create</b>, and copy the 16 letters.</li>
      <li>Paste them below and press Connect. The app checks the login with Gmail before saving it.</li>
    </ol>
    <div class="form"><label>Gmail address<input id="gEmail" value="${esc(g.email || "")}" placeholder="you@gmail.com"></label>
      <label>App password (16 letters)<input id="gPwd" type="password" autocomplete="off" placeholder="abcd efgh ijkl mnop"></label></div>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="gConnect">Connect Gmail</button></div>`;
  $("#gConnect").onclick = e => run(e.target, async () => {
    const r = await post("/api/gmail/connect", { email: $("#gEmail").value, app_password: $("#gPwd").value });
    toast(r.message, 6000); HEALTH = null; renderGmail();
  });
}

// ------------------------------------------------------------------ boot
applyTheme();
(async () => {
  try { HEALTH = await api("/api/health"); } catch (_) {}
  await renderMain();
  watchScan();
})();
