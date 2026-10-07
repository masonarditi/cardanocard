// Cardano Card in iMessage (Photon Spectrum): text a request, approve the escrow with Face ID (passkey),
// then follow the Masumi job from quote to order. The buyer agent (buyer_agent.py) does the hiring and paying.
import { Spectrum, app as appCard, contact, edit, markdown, richlink, type Space } from "spectrum-ts";
import { effect, imessage } from "spectrum-ts/providers/imessage";
import { terminal } from "spectrum-ts/providers/terminal";
import { telegram } from "spectrum-ts/providers/telegram";
import { whatsappBusiness } from "spectrum-ts/providers/whatsapp-business";
import {
  generateAuthenticationOptions, generateRegistrationOptions,
  verifyAuthenticationResponse, verifyRegistrationResponse,
} from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { avatar, statusCard, type Stage } from "./cards";
import { page } from "./page";

for (const event of ["unhandledRejection", "uncaughtException"])
  process.on(event, (e: any) => console.log(`send failed: ${e?.message ?? e}`));
const BUYER = process.env.BUYER_URL ?? "http://127.0.0.1:8788";
const PUBLIC = process.env.PUBLIC_URL ?? "http://localhost:8789";
const RP = new URL(PUBLIC).hostname;
const digits = (s: string) => s.replace(/\D/g, "").slice(-10);
const OWNERS = (process.env.OWNER_PHONES ?? "").split(",").map(digits).filter(Boolean);
const TG_OWNERS = (process.env.OWNER_TELEGRAM_IDS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
const owner = (platform: string, id: string) => platform === "telegram" ? TG_OWNERS.includes(id) : OWNERS.includes(digits(id));
const VERBOSE = process.env.CHAT_VERBOSE !== "0";
const clock = (at: number) => new Date(at * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Singapore" });
const SANDBOX = (process.env.AGENTCARD_ENV ?? "sandbox") !== "prod";
const CITY = (({ city, state }) => city ? `${city}, ${state}` : "you")(JSON.parse(process.env.DELIVERY_ADDRESS ?? "{}"));
// CHAT_DATA / CHAT_ASSETS: hosted deployments keep state on a volume and ship the assets beside the bot.
const DATA = process.env.CHAT_DATA ?? `${import.meta.dir}/data`, PUBLIC_ASSETS = process.env.CHAT_ASSETS ?? `${import.meta.dir}/../demo/video/public`;
const KEYS = `${DATA}/passkeys.json`, SEEN = `${DATA}/seen.json`;
const GREETING = /^(hi|hey|hello|yo|start|contact)\b/i;
const VERB = /\b(?:buy|get|order|grab)\s+(?:me\s+)?/i;
const PRICE = /(?:\s+(?:under|below|for under|for less than|less than|max|up to)\s+|\s*<\s*)\$?(\d+(?:\.\d{1,2})?)\s*[.!?]*$/i;

type Quote = { merchant: string; items: { name: string }[]; subtotal_cents: number; authorization_ceiling_cents: number };
type Order = { name: string; cents: number; id: string };
type Job = { id: string; space: Space; platform: string; token: string; ask: string; max: number; fee: string; quoted: boolean;
  quote?: Quote; order?: Order; stage: Stage | "quote"; tx?: string; approved?: boolean; challenge?: string; card?: any };
type Key = { id: string; publicKey: string; counter: number };
const jobs = new Map<string, Job>();
const load = async <T>(path: string, empty: T): Promise<T> => (await Bun.file(path).exists()) ? Bun.file(path).json() : empty;
const keys = await load<Record<string, Key[]>>(KEYS, {});
const seen = await load<string[]>(SEEN, []);
const AVATAR = await avatar();
const usd = (cents = 0) => `$${(cents / 100).toFixed(2)}`;

// Amazon titles run long: the headline is the part before the first comma or "with", the rest is detail.
function title(full: string) {
  const cut = full.search(/,|\swith\s|\s[-–]\s/i);
  const [head, detail] = cut > 0 ? [full.slice(0, cut).trim(), full.slice(cut).replace(/^[,\s–-]+/, "")] : [full, ""];
  return { head: head.length > 46 ? head.slice(0, head.lastIndexOf(" ", 44)) + "…" : head, detail };
}

const name = (job: Job) => job.order?.name ?? job.quote?.items[0]?.name ?? job.ask.charAt(0).toUpperCase() + job.ask.slice(1);

// Before a quote or order exists (the deployed agent buys without quoting), the card shows the budget instead.
const view = (job: Job, stage = job.stage as Stage) => {
  const { head, detail } = title(name(job));
  const upTo = !job.order && !job.quote;
  const hold: [string, string] = job.order ? ["Amazon order", `${job.order.id.slice(0, 8)}…`]
    : job.quote ? ["Card hold, at most", usd(job.quote.authorization_ceiling_cents)] : ["Item budget", `up to $${job.max}`];
  return { stage, item: head, detail, merchant: job.quote?.merchant ?? "Amazon", upTo, hold, escrow: job.fee, tx: job.tx,
    price: job.order ? usd(job.order.cents) : job.quote ? usd(job.quote.subtotal_cents) : `$${job.max}`,
    og: `${PUBLIC}/og/${job.token}?s=${stage}${job.order ? "o" : ""}` };
};

const buyer = async (path: string, body?: object): Promise<any> => {
  const r = await fetch(BUYER + path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${path} ${r.status} ${await r.text()}`);
  return r.json();
};

// One live card per job, edited in place (a new query string makes iMessage refetch the preview image).
async function showCard(job: Job, stage: Stage) {
  job.stage = stage;
  if (job.platform !== "imessage") return;
  const card = appCard(`${PUBLIC}/a/${job.token}?s=${stage}`);
  if (!job.card) job.card = await job.space.send(card);
  else await job.space.send(edit(card, job.card)).catch(() => {});
}

const contactCard = (space: Space) => contact({
  name: { formatted: "Cardano Card", first: "Cardano", last: "Card" },
  phones: [{ value: (imessage(space as any) as any).phone, type: "work" }],
  org: { name: "Agent on Masumi", title: "Buys anything a card can" },
  note: "Pay in ADA through Masumi escrow on Cardano. Refunded if the purchase fails.",
  photo: { mimeType: "image/png", read: async () => AVATAR },
});

async function introduce(space: Space, platform: string) {
  await space.send(markdown("Hey, I'm **Cardano Card** 👋 Tell me what you want and your budget, and I'll buy it for you.\n\n" +
    "You pay in ADA through escrow on Cardano, so if an order doesn't go through, you get it all back."));
  if (platform === "imessage") await space.send(contactCard(space)).catch((e) => console.log(`contact card failed: ${e.message}`));
}

async function follow(job: Job) {
  const done = new Set<string>();
  const once = async (key: string, fn: () => Promise<unknown>) => { if (!done.has(key)) { done.add(key); await fn(); } };
  let typed = 0, events = 0, node = "", outcome = "";
  for (;;) {
    const v = await buyer(`/jobs/${job.id}`).catch(() => null);
    const p: string = v?.phase ?? "";
    // Verbose mode: the agent's own job timeline, the buyer node's escrow state and Agentcard's raw outcome.
    if (VERBOSE && v) {
      for (const e of (v.events ?? []).slice(events))
        await job.space.send(`🔎 ${clock(e.at)} · ${String(e.phase).replaceAll("_", " ")} — ${e.message}`);
      events = Math.max(events, v.events?.length ?? 0);
      const n = v.node ? `${v.node.state ?? "—"} / ${v.node.action ?? "—"}${v.node.tx ? ` · tx ${v.node.tx.slice(0, 12)}…` : ""}${v.node.error ? ` · ${v.node.error}` : ""}` : "";
      if (n && n !== node) await job.space.send(`⛓️ buyer node: ${(node = n)}`);
      const o = v.purchase ? Object.entries(v.purchase).filter(([, x]) => x !== null && x !== "").map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join(" · ") : "";
      if (o && o !== outcome) await job.space.send(`🧾 Agentcard: ${(outcome = o)}`);
    }
    if (["quote_queued", "preparing_quote", "quote_reconciling"].includes(p) && Date.now() - typed > 15000) {
      typed = Date.now();
      await job.space.startTyping().catch(() => {});
    }
    if (!job.quoted) await once("quote", async () => {
      await job.space.send(markdown(`I can get **${job.ask}** on Amazon for up to **$${job.max}**.\n\n` +
        `To buy it, I'll hold **${job.fee}** in escrow on Cardano. If the order doesn't go through, you get it all back.`));
      job.stage = "approve";
      await job.space.send(richlink(`${PUBLIC}/a/${job.token}`));
    });
    if (p === "awaiting_quote_approval" && !done.has("quote")) {
      job.quote = v.quote;
      const { head } = title(name(job)), price = usd(v.quote.subtotal_cents);
      await job.space.stopTyping().catch(() => {});
      if (v.quote.subtotal_cents > job.max * 100) {
        await job.space.send(markdown(`The closest I found is **${head}** at **${price}**, which is over your $${job.max} limit, so I didn't buy anything.`));
        return;
      }
      await once("quote", async () => {
        await job.space.send(markdown(`Found **${head}** for **${price}** on ${v.quote.merchant}.\n\n` +
          `To buy it, I'll hold **${job.fee}** in escrow on Cardano. If the order doesn't go through, you get it all back.`));
        // A plain link opens Safari, where passkeys (Face ID) work; the in-Messages card is used for status after approval.
        job.stage = "approve";
        await job.space.send(richlink(`${PUBLIC}/a/${job.token}`));
      });
    }
    if (v?.lock_txs?.length && !job.tx) {
      job.tx = v.lock_txs[0];
      await once("locked", async () => {
        await showCard(job, "locked");
        await job.space.send(`Locked in escrow on Cardano. Buying it on ${job.quote?.merchant ?? "Amazon"} now.`);
      });
    }
    if (p === "purchasing") await once("checkout", () => showCard(job, "checkout"));
    if (p === "reconciling") await once("slow", () => job.space.send(`${job.quote?.merchant ?? "Amazon"}'s checkout is taking longer than usual, so I'm confirming the order before doing anything else. Your ${job.fee} stays safe in escrow.`));
    if (v?.purchase?.order_id) {
      const items = v.purchase.items ?? [];
      job.order = { name: items[0]?.name ?? name(job), id: String(v.purchase.order_id),
        cents: Math.round(Number(v.purchase.total_usd ?? (job.quote?.subtotal_cents ?? 0) / 100) * 100) };
      await showCard(job, "ordered");
      await job.space.send(effect(markdown(`Ordered 🎉 **${title(job.order.name).head}** for **${usd(job.order.cents)}** is on its way to ${CITY}.`),
        imessage.effect.message.confetti));
      return;
    }
    if (p === "refund_due" || p === "refunded") await once("declined", async () => {
      await showCard(job, "refunding");
      const reason = v.purchase?.reason;
      await job.space.send((reason === "over_budget" ? `The Amazon cart came to more than your $${job.max} limit, so I didn't buy anything.`
        : reason === "no_cart" ? "I couldn't find a clear match, so I didn't buy anything."
        : SANDBOX && reason === "declined" && job.quoted ? "Amazon didn't accept the card. This is Agentcard's sandbox card, so no real order was placed."
        : "The order didn't go through.") + ` Nothing was charged, and your ${job.fee} is on its way back.`);
    });
    if (p === "refunded") {
      await showCard(job, "refunded");
      await job.space.send(`Your ${job.fee} is back in your wallet ✓`);
      return;
    }
    if (p === "quote_rejected") {
      await job.space.stopTyping().catch(() => {});
      await job.space.send(`I couldn't find a good match for that under $${job.max}. Try adding the brand, or "from Amazon".`);
      return;
    }
    if (p === "manual_review") {
      // An order may exist (over budget, past the deadline, or the card provider never answered): never claim "nothing charged".
      await job.space.send(`I'm checking this order with the card provider before doing anything else. Your ${job.fee} stays protected in escrow until it's settled.`);
      return;
    }
    if (["quote_expired", "payment_creation_unknown", "expired"].includes(p) || v?.pay_error) {
      await job.space.send(!job.approved && p.endsWith("expired") ? "The approval window closed, so I didn't buy anything."
        : "Something went wrong on my end, so I stopped before buying anything. Nothing was charged.");
      return;
    }
    await Bun.sleep(3000);
  }
}

async function approve(job: Job) {
  await buyer(`/jobs/${job.id}/approve`, {});
  job.approved = true;
  await job.space.send(`Approved ✓ Locking your ${job.fee} in escrow…`);
  await showCard(job, "approved");
}

const byToken = (t: string) => [...jobs.values()].find((j) => j.token === t);
const html = (body: string) => new Response(body, { headers: { "content-type": "text/html; charset=utf-8" } });
Bun.serve({
  port: Number(process.env.PORT ?? 8789),
  // Railway's proxy reaches containers over IPv6: HOST="::" there; the default (IPv4) is fine on a laptop.
  hostname: process.env.HOST ?? "0.0.0.0",
  routes: {
    "/webauthn.js": () => new Response(Bun.file(`${import.meta.dir}/node_modules/@simplewebauthn/browser/dist/bundle/index.umd.min.js`)),
    "/assets/masumi.webp": () => new Response(Bun.file(`${PUBLIC_ASSETS}/masumi-wordmark.webp`)),
    "/assets/cardano.svg": () => new Response(Bun.file(`${PUBLIC_ASSETS}/cardano-horizontal-blue.svg`)),
    "/avatar.png": () => new Response(AVATAR, { headers: { "content-type": "image/png" } }),
    "/a/:t": (req) => {
      const job = byToken(req.params.t);
      return job ? html(page(view(job))) : new Response("Not found", { status: 404 });
    },
    "/a/:t/state": (req) => Response.json({ stage: byToken(req.params.t)?.stage }),
    "/og/:t": async (req) => {
      const job = byToken(req.params.t);
      if (!job) return new Response("Not found", { status: 404 });
      const stage = (new URL(req.url).searchParams.get("s") ?? job.stage) as Stage;
      return new Response(await statusCard(view(job, stage)), { headers: { "content-type": "image/png", "cache-control": "public, max-age=31536000" } });
    },
    "/a/:t/options": {
      POST: async (req) => {
        const job = byToken(req.params.t);
        if (!job || job.approved || job.stage !== "approve") return Response.json({ error: "closed" }, { status: 409 });
        const mine = keys[RP] ?? [];
        const options = mine.length
          ? await generateAuthenticationOptions({ rpID: RP, userVerification: "required", allowCredentials: mine.map((k) => ({ id: k.id })) })
          : await generateRegistrationOptions({ rpName: "Cardano Card", rpID: RP, userName: "Cardano Card", attestationType: "none",
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
const channels = (process.env.CHANNELS ?? "imessage").split(",").map((s) => s.trim());
const providers: any[] = !cloud ? [terminal.config()] : [
  ...(channels.includes("imessage") ? [imessage.config()] : []),
  ...(channels.includes("whatsapp") ? [whatsappBusiness.config()] : []),
  ...(channels.includes("telegram") ? [telegram.config()] : []),
];
const spectrum = await Spectrum({ providers });
console.log(`Cardano Card chat on ${cloud ? channels.join(", ") : "terminal"} · approvals at ${PUBLIC}`);

async function handle(space: Space, message: any) {
  if (message.content.type !== "text") return;
  const text: string = message.content.text.trim();
  const sender = message.sender?.id ?? "";
  console.log(`${message.platform} message from ${sender}: ${text.slice(0, 80)}`);
  if (cloud && !owner(message.platform, sender)) return console.log(`ignored ${message.platform} message from ${sender}`);
  const open = [...jobs.values()].find((j) => j.space.id === space.id && j.stage === "approve" && !j.approved);
  if (!cloud && open && /^\/?approve$/i.test(text)) return approve(open);
  const fresh = !seen.includes(space.id), greeting = GREETING.test(text);
  if (fresh) {
    seen.push(space.id);
    await Bun.write(SEEN, JSON.stringify(seen));
  }
  if (fresh || greeting) await introduce(space, message.platform);
  const price = text.match(PRICE);
  if (!VERB.test(text) && !price) {
    if (!greeting) await space.send(`Tell me what you want and your budget, like "Trident gum from Amazon under $20".`);
    return;
  }
  // Keep only what follows "buy", minus the store (the agent shops Amazon) and trailing punctuation.
  const after = VERB.test(text) ? text.slice(text.search(VERB)).replace(VERB, "") : text;
  const ask = after.replace(PRICE, "").replace(/\s+(?:on|from|at)\s+amazon\b/i, "").replace(/[\s,.;!?]+$/, "").trim();
  const max = Number(price?.[1] ?? 20);
  await message.react("👍").catch(() => {});
  await space.send(`Looking for ${ask} under $${max}…`);
  const { job_id, fee, quoted } = await buyer("/jobs", { ask: `${ask} from Amazon`, max_usd: max });
  const job: Job = { id: job_id, space, platform: message.platform, token: crypto.randomUUID().replaceAll("-", ""),
    stage: "quote", ask, max, fee, quoted };
  jobs.set(job_id, job);
  if (VERBOSE) await space.send(`🔎 job ${job_id} · ${quoted ? "local seller (quote first)" : "live CardanoCard /v3"} · escrow ${fee} · budget $${max}`);
  follow(job).catch((e) => console.log(`follow ${job_id} stopped: ${e.message}`));
}

for await (const [space, message] of spectrum.messages)
  handle(space, message).catch((e) => console.log(`handling failed: ${e.message}`));
