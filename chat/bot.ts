// Cardano Card in iMessage (Photon Spectrum): text a request, approve the escrow with Face ID (passkey),
// then follow the Masumi job from quote to order. The buyer agent (buyer_agent.py) does the hiring and paying.
import { Spectrum, app as appCard, edit, type Space } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { terminal } from "spectrum-ts/providers/terminal";
import {
  generateAuthenticationOptions, generateRegistrationOptions,
  verifyAuthenticationResponse, verifyRegistrationResponse,
} from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";

const BUYER = process.env.BUYER_URL ?? "http://127.0.0.1:8788";
const PUBLIC = process.env.PUBLIC_URL ?? "http://localhost:8789";
const RP = new URL(PUBLIC).hostname;
const digits = (s: string) => s.replace(/\D/g, "").slice(-10);
const OWNERS = (process.env.OWNER_PHONES ?? "").split(",").map(digits).filter(Boolean);
const SCAN = "https://preprod.cardanoscan.io/transaction/";
const KEYS = `${import.meta.dir}/data/passkeys.json`;
const BUY = /\b(?:buy|get|order)\s+(?:me\s+)?(.+?)(?:\s+(?:under|below|max|up to)\s+\$?(\d+(?:\.\d{1,2})?))?[.!?]*$/i;

type Quote = { merchant: string; items: { name: string }[]; subtotal_cents: number; authorization_ceiling_cents: number };
type Job = { id: string; space: Space; token: string; max: number; card?: any; quote?: Quote; approved?: boolean; challenge?: string; stage: string };
type Key = { id: string; publicKey: string; counter: number };
const jobs = new Map<string, Job>();
const keys: Record<string, Key[]> = (await Bun.file(KEYS).exists()) ? await Bun.file(KEYS).json() : {};
const usd = (cents: number) => `$${(cents / 100).toFixed(2)}`;
const item = (q?: Quote) => q?.items.map((i) => i.name).join(", ").slice(0, 80) ?? "your order";

const buyer = async (path: string, body?: object): Promise<any> => {
  const r = await fetch(BUYER + path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${path} ${r.status} ${await r.text()}`);
  return r.json();
};

// One card per job, edited in place as the job moves (new query string so iMessage refetches the preview).
async function showCard(job: Job, stage: string) {
  job.stage = stage;
  const card = appCard(`${PUBLIC}/a/${job.token}?s=${stage}`);
  if (!job.card) job.card = await job.space.send(card);
  else await job.space.send(edit(card, job.card)).catch(() => {});
}

async function follow(job: Job) {
  const said = new Set<string>();
  const say = async (key: string, text: string) => {
    if (!said.has(key)) { said.add(key); await job.space.send(text); }
  };
  for (;;) {
    const v = await buyer(`/jobs/${job.id}`).catch(() => null);
    const p = v?.phase, tadA = v ? v.fee_lovelace / 1e6 : 0;
    if (p === "awaiting_quote_approval" && v.quote.subtotal_cents > job.max * 100)
      return say("pricey", `Best match is ${item(v.quote)} at ${usd(v.quote.subtotal_cents)}, over your $${job.max} limit. Not buying.`);
    if (p === "awaiting_quote_approval" && !said.has("quote")) {
      job.quote = v.quote;
      await say("quote", `Found it: ${item(v.quote)} — ${usd(v.quote.subtotal_cents)} on ${v.quote.merchant} (card hold up to ${usd(v.quote.authorization_ceiling_cents)} for fees and tax).\n` +
        `To buy it, lock ${tadA} tADA in Masumi escrow. If the purchase fails, it's refunded.`);
      await showCard(job, "approve");
    }
    if (job.approved) await say("approved", `✅ Approved with Face ID. Locking ${tadA} tADA in escrow on Cardano…`);
    if (v?.lock_txs?.length) {
      await say("locked", `🔒 Escrow locked on Cardano Preprod\n${SCAN}${v.lock_txs[0]}`);
      if (job.stage === "approved") await showCard(job, "locked");
    }
    if (p === "purchasing") await say("buying", `🛒 Funds confirmed. Checking out on ${job.quote?.merchant ?? "Amazon"}…`);
    if (["submitting_result", "result_submitted", "paid"].includes(p) && v.purchase?.order_id) {
      await say("ordered", `📦 Ordered! Order ${String(v.purchase.order_id).slice(0, 8)}… Proof of purchase is going on-chain.`);
      if (job.stage !== "ordered") await showCard(job, "ordered");
    }
    if (p === "result_submitted" || p === "paid") return say("done", "⛓️ Result recorded on Cardano. Cardano Card is paid when escrow unlocks.");
    if (p === "refund_due" || p === "refunded") {
      await say("declined", `❌ Purchase didn't go through (${v.purchase?.reason ?? v.purchase?.decline_code ?? "declined"}). Nothing was charged. Refunding your ${tadA} tADA…`);
      if (job.stage !== "refunded") await showCard(job, "refunded");
    }
    if (p === "refunded") return say("refunded", "↩️ Escrow refunded to your wallet.");
    if (p === "quote_rejected")
      return say("stop", "Couldn't find a clean match under your limit, so nothing was bought. Try being more specific, like adding \"from Amazon\".");
    if (["quote_expired", "manual_review", "payment_creation_unknown", "expired"].includes(p))
      return say("stop", `Stopped at ${p.replaceAll("_", " ")}. No card charge was made.`);
    await Bun.sleep(3000);
  }
}

async function approve(job: Job) {
  await buyer(`/jobs/${job.id}/approve`, {});
  job.approved = true;
  await showCard(job, "approved");
}

const page = (job: Job) => {
  const q = job.quote, s = job.stage;
  const title = s === "approve" ? `Approve ${usd(q?.subtotal_cents ?? 0)} · ${item(q)}`
    : s === "ordered" ? `📦 Ordered · ${item(q)}` : s === "refunded" ? `↩️ Refunded · ${item(q)}`
    : s === "locked" ? `🔒 Escrow locked · ${item(q)}` : `✅ Approved · ${item(q)}`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><meta property="og:title" content="${title}">
<meta property="og:description" content="Cardano Card · Masumi escrow on Cardano · refunded if the purchase fails">
<style>body{font:17px -apple-system,system-ui;background:#F5F5F5;color:#111;margin:0;padding:28px 20px}
.c{background:#fff;border-radius:22px;padding:24px;box-shadow:0 10px 40px #0001}h1{font-size:22px;margin:0 0 6px}
.m{color:#666;font-size:15px}.r{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #eee}
button{width:100%;margin-top:22px;padding:16px;border:0;border-radius:14px;background:#FA008C;color:#fff;font:600 17px -apple-system}
#s{margin-top:14px;text-align:center;color:#666}</style></head><body><div class="c">
<div class="m">Cardano Card</div><h1>${item(q)}</h1>
<div class="r"><span class="m">Price</span><b>${usd(q?.subtotal_cents ?? 0)} · ${q?.merchant ?? ""}</b></div>
<div class="r"><span class="m">Card ceiling</span><b>${usd(q?.authorization_ceiling_cents ?? 0)}</b></div>
<div class="r"><span class="m">Escrow</span><b>10 tADA · Masumi</b></div>
${s === "approve" ? `<button id="b">Approve with Face ID</button>` : `<h2 style="text-align:center;margin-top:22px">${title}</h2>`}
<p id="s"></p></div><script src="/webauthn.js"></script><script>
const b=document.getElementById('b'),s=document.querySelectorAll('#s')[0];
b&&(b.onclick=async()=>{try{
const {mode,options}=await (await fetch(location.pathname+'/options',{method:'POST'})).json();
const f=mode==='register'?SimpleWebAuthnBrowser.startRegistration:SimpleWebAuthnBrowser.startAuthentication;
const r=await (await fetch(location.pathname+'/verify',{method:'POST',body:JSON.stringify(await f({optionsJSON:options}))})).json();
s.textContent=r.ok?'✅ Approved. Back to Messages.':'Not approved';if(r.ok)b.remove();
}catch(e){s.innerHTML='Face ID unavailable here. <a href="'+location.href+'" target="_blank">Open in Safari</a>'}})
</script></body></html>`;
};

const byToken = (t: string) => [...jobs.values()].find((j) => j.token === t);
Bun.serve({
  port: Number(process.env.PORT ?? 8789),
  routes: {
    "/webauthn.js": () => new Response(Bun.file(`${import.meta.dir}/node_modules/@simplewebauthn/browser/dist/bundle/index.umd.min.js`)),
    "/a/:t": (req) => {
      const job = byToken(req.params.t);
      return job ? new Response(page(job), { headers: { "content-type": "text/html" } }) : new Response("Not found", { status: 404 });
    },
    "/a/:t/options": {
      POST: async (req) => {
        const job = byToken(req.params.t);
        if (!job || job.approved || job.stage !== "approve") return Response.json({ error: "closed" }, { status: 409 });
        const mine = keys[RP] ?? [];
        const options = mine.length
          ? await generateAuthenticationOptions({ rpID: RP, userVerification: "required", allowCredentials: mine.map((k) => ({ id: k.id })) })
          : await generateRegistrationOptions({ rpName: "Cardano Card", rpID: RP, userName: "Cardano Card owner", attestationType: "none",
              authenticatorSelection: { residentKey: "preferred", userVerification: "required" } });
        job.challenge = options.challenge;
        return Response.json({ mode: mine.length ? "authenticate" : "register", options });
      },
    },
    "/a/:t/verify": {
      POST: async (req) => {
        const job = byToken(req.params.t);
        if (!job?.challenge || job.approved) return Response.json({ ok: false }, { status: 409 });
        const response: any = await req.json(), mine = (keys[RP] ??= []);
        const expect = { expectedChallenge: job.challenge, expectedOrigin: PUBLIC, expectedRPID: RP, requireUserVerification: true };
        job.challenge = undefined;
        try {
          const key = mine.find((k) => k.id === response.id);
          if (key) {
            const r = await verifyAuthenticationResponse({ ...expect, response,
              credential: { id: key.id, publicKey: isoBase64URL.toBuffer(key.publicKey), counter: key.counter } });
            if (!r.verified) return Response.json({ ok: false });
            key.counter = r.authenticationInfo.newCounter;
          } else {
            const r = await verifyRegistrationResponse({ ...expect, response });
            if (!r.verified || !r.registrationInfo) return Response.json({ ok: false });
            const c = r.registrationInfo.credential;
            mine.push({ id: c.id, publicKey: isoBase64URL.fromBuffer(c.publicKey), counter: c.counter });
          }
          await Bun.write(KEYS, JSON.stringify(keys));
          await approve(job);
          return Response.json({ ok: true });
        } catch (e) {
          console.error("approval failed", e);
          return Response.json({ ok: false }, { status: 400 });
        }
      },
    },
  },
});

const cloud = Boolean(process.env.SPECTRUM_PROJECT_ID);
const spectrum = await Spectrum({ providers: [cloud ? imessage.config() : terminal.config()] });
console.log(`Cardano Card chat on ${cloud ? "iMessage" : "terminal"} · approvals at ${PUBLIC}`);

for await (const [space, message] of spectrum.messages) {
  if (message.content.type !== "text") continue;
  const text = message.content.text.trim();
  const sender = message.sender?.id ?? "";
  if (cloud && !OWNERS.includes(digits(sender))) {
    console.log(`ignored message from ${sender}`);
    continue;
  }
  const open = [...jobs.values()].find((j) => j.space.id === space.id && j.stage === "approve" && !j.approved);
  if (!cloud && open && /^\/?approve$/i.test(text)) {
    await approve(open);
    continue;
  }
  const m = text.match(BUY);
  if (!m) {
    await space.send("Text me what to buy, like: buy a pack of Trident gum under $5");
    continue;
  }
  const max = Number(m[2] ?? 20);
  await message.react("👍").catch(() => {});
  await space.send(`On it. Asking Cardano Card on Masumi for a quote on "${m[1]}" (max $${max}).`);
  await space.responding(async () => {
    // The budget must cover the card's authorization ceiling (~$11 over the item price), so the price cap goes in the ask.
    const { job_id } = await buyer("/jobs", { ask: `${m[1]} under $${max}`, max_total_usd: max + 12 });
    const job: Job = { id: job_id, space, token: crypto.randomUUID().replaceAll("-", ""), stage: "quote", max };
    jobs.set(job_id, job);
    follow(job).catch((e) => space.send(`Something went wrong following the job: ${e.message}`));
  }).catch((e) => space.send(`Couldn't start the job: ${e.message}`));
}
