// The approval / status page opened from the chat, styled like the launch video (light grid, Inter, masumi pink).
import type { Stage } from "./cards";

export type PageView = {
  stage: Stage; item: string; detail: string; merchant: string; price: string; upTo: boolean; hold: [string, string]; escrow: string;
  og: string; tx?: string;
};

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const STEPS = ["Quote", "Face ID", "Escrow", "Checkout"];
const at: Record<Stage, number> = { approve: 1, approved: 2, locked: 3, checkout: 3, ordered: 5, refunding: 4, refunded: 5 };
const headline: Record<Stage, string> = {
  approve: "Approve this purchase", approved: "Approved. Locking escrow…", locked: "Escrow locked on Cardano",
  checkout: "Placing your order…", ordered: "Ordered", refunding: "Refunding your escrow…", refunded: "Refunded",
};
const FACE_ID = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V6.5A2.5 2.5 0 0 1 6.5 4H8M16 4h1.5A2.5 2.5 0 0 1 20 6.5V8M20 16v1.5a2.5 2.5 0 0 1-2.5 2.5H16M8 20H6.5A2.5 2.5 0 0 1 4 17.5V16"/><path d="M9 9.2v1.3M15 9.2v1.3M12.3 9.2v3.6h-.9M9.6 15.4c1.4 1.1 3.4 1.1 4.8 0"/></svg>`;

export function page(v: PageView) {
  const refund = v.stage === "refunding" || v.stage === "refunded";
  const steps = [...STEPS, refund ? "Refunded" : "Ordered"].map((name, i) => {
    const cls = i < at[v.stage] ? (i === 3 && refund ? "skip" : "done") : i === at[v.stage] ? "now" : "";
    return `<li class="${cls}"><i></i><span>${name}</span></li>`;
  }).join("");
  const title = v.stage === "approve" ? `Approve ${v.upTo ? "up to " : ""}${v.price} · ${v.item}` : `${headline[v.stage]} · ${v.item}`;
  const action = v.stage === "approve"
    ? `<button id="go">${FACE_ID}<span>Approve with Face ID</span></button>
       <p class="fine">Your passkey approves it on this device. The ${esc(v.escrow)} stays in Masumi escrow until the order goes through, and comes back if it doesn't.</p>`
    : v.tx ? `<a class="ghost" href="https://preprod.cardanoscan.io/transaction/${v.tx}">View escrow on Cardanoscan <b>${v.tx.slice(0, 8)}…${v.tx.slice(-6)}</b></a>` : "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>${esc(title)}</title>
<meta property="og:title" content="${esc(title)}"><meta property="og:site_name" content="Cardano Card">
<meta property="og:description" content="Masumi escrow on Cardano · refunded if the purchase fails">
<meta property="og:image" content="${v.og}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#F5F5F5;--panel:#fff;--line:#E3E3E3;--text:#0A0A0A;--muted:#6E6E6E;--accent:#FA008C;--cardano:#0033AD;--ok:#0E8F63}
*{box-sizing:border-box;margin:0}
body{font-family:Inter,-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100dvh;-webkit-font-smoothing:antialiased;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;background-image:linear-gradient(rgba(0,0,0,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.045) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 35%,#000 30%,transparent 100%);mask-image:radial-gradient(ellipse 80% 60% at 50% 35%,#000 30%,transparent 100%);pointer-events:none}
body:after{content:"";position:fixed;inset:0;background:radial-gradient(ellipse 70% 45% at 50% 40%,rgba(250,0,140,.07),transparent 70%);pointer-events:none}
main{position:relative;z-index:1;max-width:440px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 28px) 16px 40px}
.rise{opacity:0;animation:rise .7s cubic-bezier(.16,1,.3,1) forwards}
@keyframes rise{from{opacity:0;transform:translateY(14px);filter:blur(6px)}to{opacity:1;transform:none;filter:none}}
.brand{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:500;letter-spacing:-.4px}
.glyph{width:30px;height:20px;border-radius:4px;background:var(--cardano);position:relative;box-shadow:0 4px 12px rgba(0,51,173,.25)}
.glyph:before{content:"";position:absolute;left:4px;top:5px;width:7px;height:5px;border-radius:1.5px;background:rgba(255,255,255,.88)}
.glyph:after{content:"";position:absolute;left:4px;bottom:3px;width:18px;height:1.5px;border-radius:1px;background:rgba(255,255,255,.45)}
.eyebrow{margin-top:34px;font-size:15px;font-weight:500;color:var(--accent)}
h1{margin-top:8px;font-size:30px;line-height:1.08;font-weight:600;letter-spacing:-1.1px}
.detail{margin-top:8px;color:var(--muted);font-size:15px}
.card{margin-top:24px;background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:0 24px 60px rgba(0,0,0,.06)}
.top{display:flex;justify-content:space-between;align-items:baseline}
.chip{font-size:13px;font-weight:500;padding:5px 11px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.price{font-size:48px;font-weight:600;letter-spacing:-2.2px;line-height:1}.price small{display:block;font-size:14px;font-weight:500;letter-spacing:0;color:var(--muted);margin-bottom:6px}
.rows{margin-top:18px;border-top:1px solid var(--line)}
.rows div{display:flex;justify-content:space-between;padding:13px 0;border-bottom:1px solid var(--line);font-size:15px}
.rows div:last-child{border-bottom:0;padding-bottom:0}
.rows dt{color:var(--muted)}.rows dd{font-weight:500}
ol{list-style:none;padding:0;margin-top:26px;display:grid;grid-template-columns:repeat(5,1fr);position:relative}
ol:before,ol:after{content:"";position:absolute;left:10%;top:6px;height:2px}
ol:before{right:10%;background:var(--line)}ol:after{width:calc(80% * var(--p));background:var(--text)}
li{position:relative;display:flex;flex-direction:column;align-items:center;gap:8px;font-size:12px;color:var(--muted);font-weight:500}
li i{width:14px;height:14px;border-radius:7px;background:#D4D4D4;position:relative;z-index:1;box-shadow:0 0 0 4px var(--bg)}
li.done i{background:var(--text)}li.done,li.now{color:var(--text)}li.skip i{background:var(--muted)}
li.now i{background:var(--accent);animation:pulse 1.6s ease-out infinite}
li:last-child.done i{background:var(--ok)}
@keyframes pulse{0%{box-shadow:0 0 0 4px var(--bg),0 0 0 4px rgba(250,0,140,.35)}100%{box-shadow:0 0 0 4px var(--bg),0 0 0 14px rgba(250,0,140,0)}}
button,.ghost{margin-top:26px;width:100%;height:58px;border:0;border-radius:16px;display:flex;align-items:center;justify-content:center;gap:10px;font:600 17px Inter,-apple-system,sans-serif;letter-spacing:-.2px;text-decoration:none}
button{background:var(--text);color:#fff;box-shadow:0 14px 34px rgba(0,0,0,.18);transition:transform .15s}
button:active{transform:scale(.98)}button svg{width:24px;height:24px}
button[disabled]{opacity:.6}
.ghost{background:var(--panel);border:1px solid var(--line);color:var(--text);font-size:15px;font-weight:500}
.ghost b{font-weight:500;color:var(--cardano)}
.fine{margin-top:14px;font-size:13px;line-height:1.45;color:var(--muted);text-align:center}
.ok{margin-top:26px;display:none;flex-direction:column;align-items:center;gap:10px;text-align:center}
.ok svg{width:64px;height:64px}.ok circle{stroke:var(--ok);stroke-dasharray:166;stroke-dashoffset:166;animation:draw .6s cubic-bezier(.16,1,.3,1) forwards}
.ok path{stroke:var(--ok);stroke-dasharray:48;stroke-dashoffset:48;animation:draw .4s .45s cubic-bezier(.16,1,.3,1) forwards}
@keyframes draw{to{stroke-dashoffset:0}}
.ok b{font-size:20px;font-weight:600;letter-spacing:-.4px}.ok span{color:var(--muted);font-size:15px}
.err{margin-top:12px;text-align:center;font-size:14px;color:var(--accent)}
footer{margin-top:36px;display:flex;align-items:center;justify-content:center;gap:16px;color:var(--muted);font-size:12px}
footer img{height:15px;opacity:.85}
</style></head><body><main>
<div class="brand rise"><span class="glyph"></span>Cardano Card</div>
<p class="eyebrow rise" style="animation-delay:.05s">${headline[v.stage]}</p>
<h1 class="rise" style="animation-delay:.1s">${esc(v.item)}</h1>
${v.detail ? `<p class="detail rise" style="animation-delay:.14s">${esc(v.detail)}</p>` : ""}
<section class="card rise" style="animation-delay:.18s">
  <div class="top"><span class="price">${v.upTo ? "<small>up to</small>" : ""}${v.price}</span><span class="chip">${esc(v.merchant)}</span></div>
  <dl class="rows"><div><dt>${esc(v.hold[0])}</dt><dd>${esc(v.hold[1])}</dd></div>
  <div><dt>Escrow</dt><dd>${esc(v.escrow)} · Cardano</dd></div><div><dt>If it fails</dt><dd>Full refund</dd></div></dl>
</section>
<ol class="rise" style="animation-delay:.24s;--p:${Math.min(at[v.stage], 4) / 4}">${steps}</ol>
<div class="rise" style="animation-delay:.3s" id="act">${action}</div>
<div class="ok" id="ok"><svg viewBox="0 0 56 56" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="28" cy="28" r="26"/><path d="M17 29l7 7 15-16"/></svg>
<b>Approved</b><span>You can head back to Messages.</span></div>
<p class="err" id="err"></p>
<footer class="rise" style="animation-delay:.36s">Secured by<img src="/assets/masumi.webp" alt="Masumi"><img src="/assets/cardano.svg" alt="Cardano"></footer>
</main>
<script src="/webauthn.js"></script><script>
const go=document.getElementById('go'),err=document.getElementById('err'),base=location.pathname.replace(/\\/$/,'');
go&&(go.onclick=async()=>{err.textContent='';go.disabled=true;try{
 const {mode,options}=await (await fetch(base+'/options',{method:'POST'})).json();
 const f=mode==='register'?SimpleWebAuthnBrowser.startRegistration:SimpleWebAuthnBrowser.startAuthentication;
 const r=await (await fetch(base+'/verify',{method:'POST',body:JSON.stringify(await f({optionsJSON:options}))})).json();
 if(!r.ok)throw new Error('not approved');
 document.getElementById('act').style.display='none';document.getElementById('ok').style.display='flex';
}catch(e){go.disabled=false;err.innerHTML=window.PublicKeyCredential?'Face ID was cancelled. Tap to try again.':'Face ID isn’t available here. <a href="x-safari-'+location.href+'">Open in Safari</a>'}});
const stage=${JSON.stringify(v.stage)};
stage!=='approve'&&stage!=='refunded'&&stage!=='ordered'&&setInterval(async()=>{try{const s=await (await fetch(base+'/state')).json();if(s.stage!==stage)location.reload()}catch(e){}},4000);
</script></body></html>`;
}
