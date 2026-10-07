// Operator page: polls ops/status every 5 s with the bearer token and drives the card kill switch.
const $ = (id) => document.getElementById(id);
const url = (path) => new URL(path, location.href.replace(/\/ops[^/]*$/, "/ops"));
let token = sessionStorage.getItem("ops_token") || new URLSearchParams(location.hash.slice(1)).get("token") || "";
if (location.hash) history.replaceState(null, "", location.pathname);

const GOOD = new Set(["paid", "result_submitted", "completed", "refunded"]);
const BAD = new Set(["manual_review", "payment_creation_unknown", "expired", "failed"]);
const WARN = new Set(["refund_due", "refund_authorizing", "reconciling", "quote_rejected", "quote_expired"]);
const badge = (phase) => `<span class="badge ${GOOD.has(phase) ? "good" : BAD.has(phase) ? "bad" : WARN.has(phase) ? "warn" : "live"}">${phase}</span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const t = (unix) => unix ? new Date(unix * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"}) : "—";
const ago = (unix) => unix ? `${Math.max(0, Math.round((Date.now() / 1000 - unix) / 60))} min ago` : "—";
const dl = (id, rows) => { $(id).innerHTML = rows.map(([k, v, cls]) => `<dt>${esc(k)}</dt><dd class="${cls || ""}">${v}</dd>`).join(""); };
const okText = (p, good, bad) => p == null ? "—" : !p.ok ? `<span class="bad">${esc(p.error)}</span>` : good;

async function api(path, options = {}) {
  const r = await fetch(url(path), {...options, headers: {"Authorization": "Bearer " + token, "Content-Type": "application/json", ...(options.headers || {})}});
  if (r.status === 401) throw new Error("Token rejected");
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function render(s) {
  $("clock").textContent = `${t(s.now)} · up ${Math.floor(s.uptime_s / 60)} min`;
  $("alerts").innerHTML = s.alerts.map((a) => `<div class="alert ${a.level}">${esc(a.text)}</div>`).join("");
  const on = s.card.enabled;
  $("card-state").textContent = on ? "ON" : "OFF"; $("card-state").className = "big " + (on ? "on" : "off");
  $("card-note").textContent = s.card.changed_at ? `${on ? "Enabled" : "Disabled"} ${ago(s.card.changed_at)}${s.card.note ? " — " + s.card.note : ""}` : "Never switched (defaults to ON)";
  $("card-toggle").textContent = on ? "Switch the real card OFF" : "Switch the real card back ON";
  $("card-toggle").className = on ? "danger" : "safe";
  const c = s.config;
  dl("runtime", [["mode", esc(c.mode)], ["escrow rail", esc(c.escrow_rail)], ["purchasing", esc(c.purchase_backend)], ["agent", `<code>…${esc((c.agent_identifier || "").slice(-16))}</code>`], ["seller vkey", `<code>${esc((c.seller_vkey || "").slice(0, 12))}…</code>`], ["prefixes", esc(c.path_prefixes || "/")], ["jobs", s.jobs.length]]);
  const a = s.probes.agentcard;
  dl("agentcard", a ? [["env", `<b class="${c.agentcard_env === "prod" ? "bad" : "ok"}">${esc(c.agentcard_env)}</b>`], ["probe", okText(a, a.wired ? `<span class="${a.status === 200 ? "ok" : "bad"}">${a.status} · ${a.merchants ?? "?"} merchants</span>` : "not wired")], ["user", `<code>${esc(a.user_id || "—")}</code>`], ["checked", ago(a.at)]] : [["probe", "skipped"]]);
  const h = s.probes.hosted;
  dl("hosted", h ? [["probe", okText(h, h.wired ? '<span class="ok">reachable</span>' : "not wired")], ["url", esc(h.url || "—")], ["checked", ago(h.at)]] : [["probe", "skipped"]]);
  $("payments").innerHTML = (h?.payments || []).map((p) => {
    const expired = p.state === "FundsOrDatumInvalid" && /without on-chain lock/.test(p.error || "");
    const state = expired ? '<span class="badge">expired unfunded</span>' : badge(p.state || "?");
    return `<li>${state} next <code>${esc(p.next || "—")}</code>${p.error && !expired ? ` <span class="bad">${esc(p.error)}</span>` : ""}${p.tx ? ` · tx <code>${esc(p.tx.slice(0, 12))}…</code>` : ""}</li>`;
  }).join("");
  const k = s.probes.sokosumi;
  dl("sokosumi", k ? [["listed", okText(k, k.listed ? '<span class="ok">yes</span>' : '<span class="bad">no</span>')], ["agent id", `<code>${esc((k.id || "—").slice(0, 8))}</code>`], ["credits", esc(k.credits ?? "—")], ["agents on site", esc(k.agents_total ?? "—")], ["checked", ago(k.at)]] : [["probe", "skipped"]]);
  const n = s.probes.node;
  dl("node", n ? [["health", okText(n, `<span class="${n.status === 200 ? "ok" : "bad"}">${n.status}</span>`)], ["url", esc(n.url || "—")], ["checked", ago(n.at)]] : [["probe", "skipped"]]);
  $("jobs").querySelector("tbody").innerHTML = s.jobs.map((j) => {
    const p = j.purchase;
    const purchase = !p ? "—" : p.status === "success" ? `<span class="ok">order ${esc(p.order_id?.slice(0, 8))} · $${esc(p.total_usd)}</span>` : `<span class="${p.status === "failed" ? "warn" : ""}">${esc(p.status)}${p.reason ? " · " + esc(p.reason) : ""}</span>`;
    const resolve = ["reconciling", "manual_review"].includes(j.phase) ? `<br><button class="danger resolve" data-job="${esc(j.id)}">Resolve: no charge → refund</button>` : "";
    return `<tr><td><code>${esc(j.id.slice(0, 8))}</code>${j.simulated ? ' <span class="badge">sim</span>' : ""}</td><td>${badge(j.phase)}${resolve}</td><td>${esc(j.escrow_state)}</td><td>${purchase}</td><td>${esc((j.ask || "").slice(0, 60))} <span class="muted">$${esc(j.budget_usd)}</span></td><td title="${esc(j.last_message)}">${t(j.updated)}</td></tr>`;
  }).join("") || '<tr><td colspan="6" class="muted">No jobs yet</td></tr>';
  document.querySelectorAll("button.resolve").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.job;
    if (!confirm(`Resolve job ${id.slice(0, 8)} as NO CHARGE? Only do this after checking the card shows no authorization for it. The buyer's escrow will be refunded.`)) return;
    const note = prompt("Why (required, goes in the log)", "card shows no charge");
    if (!note || note.length < 3) return;
    try { await api(`ops/jobs/${id}/resolve`, {method: "POST", body: JSON.stringify({note})}); await refresh(); } catch (e) { $("error").textContent = e.message; }
  }));
  $("events").innerHTML = s.events.map((e) => `<li><span class="muted">${t(e.at)}</span><code>${esc(e.job_id.slice(0, 8))}</code><span>${badge(e.phase)} ${esc(e.message)}</span></li>`).join("");
  $("opslog").innerHTML = s.ops_log.map((e) => `<li><span class="muted">${t(e.at)}</span><span></span><span>${esc(e.message)}</span></li>`).join("") || '<li class="muted">No switches yet</li>';
}

let timer;
async function refresh() {
  try {
    render(await api("ops/status"));
    $("conn").textContent = "connected"; $("conn").className = "pill on"; $("error").textContent = "";
    $("main").hidden = false; $("connect").hidden = true;
  } catch (e) {
    $("conn").textContent = e.message; $("conn").className = "pill off";
    if (e.message === "Token rejected") { sessionStorage.removeItem("ops_token"); token = ""; $("main").hidden = true; $("connect").hidden = false; clearInterval(timer); }
    else $("error").textContent = e.message;
  }
}

$("connect").addEventListener("submit", (ev) => { ev.preventDefault(); token = $("token").value.trim(); sessionStorage.setItem("ops_token", token); $("token").value = ""; start(); });
$("card-toggle").addEventListener("click", async () => {
  const enabling = $("card-toggle").classList.contains("safe");
  if (!confirm(enabling ? "Switch Mason's real card back ON? New purchases will charge it." : "Switch the real card OFF? Every new purchase will fail as card_disabled and refund the buyer.")) return;
  const note = prompt("Note for the log (optional)") || "";
  try { await api("ops/card", {method: "POST", body: JSON.stringify({enabled: enabling, note})}); await refresh(); } catch (e) { $("error").textContent = e.message; }
});
function start() { clearInterval(timer); if (!token) { $("connect").hidden = false; return; } refresh(); timer = setInterval(refresh, 5000); }
start();
