// Images for the chat (contact photo, link-preview cards), rendered with satori + resvg in the demo video's style.
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";

export const T = {
  bg: "#F5F5F5", panel: "#FFFFFF", line: "#E3E3E3", text: "#0A0A0A", muted: "#6E6E6E",
  accent: "#FA008C", cardano: "#0033AD", ok: "#0E8F63", todo: "#D4D4D4",
};
const fonts = await Promise.all([400, 500, 600, 700].map(async (weight) => ({
  name: "Inter", weight: weight as 400, style: "normal" as const,
  data: await Bun.file(`${import.meta.dir}/assets/fonts/Inter-${weight}.ttf`).arrayBuffer(),
})));

type Node = { type: string; props: Record<string, any> };
const h = (style: Record<string, any>, ...children: any[]): Node =>
  ({ type: "div", props: { style: { display: "flex", ...style }, children: children.flat().filter((c) => c !== null && c !== false) } });

async function png(node: Node, width: number, height: number) {
  const svg = await satori(node as any, { width, height, fonts });
  return Buffer.from(new Resvg(svg, { fitTo: { mode: "width", value: width * 2 } }).render().asPng());
}

// The card glyph from the video's wordmark: Cardano-blue card, white chip and stripe.
const glyph = (size: number, card = T.cardano, chip = "rgba(255,255,255,0.88)", shadow = 0.28) =>
  h({ width: size * 1.1, height: size * 0.72, borderRadius: size * 0.1, background: card, position: "relative",
      boxShadow: `0 ${size * 0.12}px ${size * 0.3}px rgba(0,51,173,${shadow})` },
    h({ position: "absolute", left: size * 0.14, top: size * 0.2, width: size * 0.22, height: size * 0.17, borderRadius: size * 0.04, background: chip }),
    h({ position: "absolute", left: size * 0.14, bottom: size * 0.12, width: size * 0.62, height: size * 0.05, borderRadius: 4, background: "rgba(255,255,255,0.45)" }));

export const avatar = () => png(
  h({ width: 512, height: 512, alignItems: "center", justifyContent: "center",
      backgroundImage: `radial-gradient(circle at 50% 42%, #FFFFFF 0%, ${T.bg} 62%, #ECECEC 100%)` },
    h({ transform: "rotate(-8deg)" }, glyph(250, T.cardano, "rgba(255,255,255,0.9)", 0.16))), 512, 512);

export type Stage = "approve" | "approved" | "locked" | "checkout" | "ordered" | "refunding" | "refunded";
const STEPS = ["Quote", "Face ID", "Escrow", "Checkout"];
const at: Record<Stage, number> = { approve: 1, approved: 2, locked: 3, checkout: 3, ordered: 5, refunding: 4, refunded: 5 };
const badge: Record<Stage, [string, string]> = {
  approve: ["Approve with Face ID", T.accent], approved: ["Approved", T.ok], locked: ["Escrow locked", T.cardano],
  checkout: ["Checking out", T.text], ordered: ["Ordered", T.ok], refunding: ["Refunding", T.muted], refunded: ["Refunded", T.ok],
};

export type CardView = { stage: Stage; item: string; merchant: string; price: string; upTo?: boolean; escrow: string; tx?: string };

export function statusCard(v: CardView) {
  const refund = v.stage === "refunding" || v.stage === "refunded";
  const steps = [...STEPS, refund ? "Refunded" : "Ordered"];
  const [label, color] = badge[v.stage];
  const note = v.stage === "approve" ? `${v.escrow} held in Masumi escrow on Cardano. Refunded if the purchase fails.`
    : v.tx ? `Escrow tx ${v.tx.slice(0, 8)}…${v.tx.slice(-6)} · Cardano Preprod`
    : `${v.escrow} in Masumi escrow on Cardano · refunded if the purchase fails`;
  return png(
    h({ width: 1200, height: 630, flexDirection: "column", padding: "56px 64px", background: T.bg, fontFamily: "Inter", color: T.text,
        backgroundImage: "radial-gradient(ellipse 60% 55% at 70% 40%, rgba(250,0,140,0.07), transparent 70%)" },
      h({ alignItems: "center", justifyContent: "space-between" },
        h({ alignItems: "center", gap: 18 }, glyph(44), h({ fontSize: 34, fontWeight: 500, letterSpacing: -1.4 }, "Cardano Card")),
        h({ fontSize: 26, fontWeight: 500, color, border: `2px solid ${color}55`, background: `${color}14`, borderRadius: 999, padding: "10px 24px" }, label)),
      h({ marginTop: 52, alignItems: "flex-end", justifyContent: "space-between", gap: 40 },
        h({ flexDirection: "column", flex: 1 },
          h({ fontSize: 26, color: T.muted, fontWeight: 500 }, v.merchant),
          h({ fontSize: 58, fontWeight: 600, letterSpacing: -2.4, lineHeight: 1.08, marginTop: 10 }, v.item)),
        h({ flexDirection: "column", alignItems: "flex-end" },
          v.upTo ? h({ fontSize: 26, fontWeight: 500, color: T.muted, marginBottom: 8 }, "up to") : null,
          h({ fontSize: 96, fontWeight: 600, letterSpacing: -4.5, lineHeight: 1 }, v.price))),
      h({ marginTop: "auto", flexDirection: "column", gap: 22 },
        h({ alignItems: "center" }, ...steps.flatMap((name, i) => {
          const done = i < at[v.stage], now = i === at[v.stage];
          const dot = refund && i === 3 ? T.muted : done ? (i === 4 ? T.ok : T.text) : now ? color : T.todo;
          return [
            i > 0 ? h({ flex: 1, height: 3, background: i <= at[v.stage] ? T.text : T.todo, margin: "0 14px" }) : null,
            h({ alignItems: "center", gap: 12 },
              h({ width: 22, height: 22, borderRadius: 11, background: dot, boxShadow: now ? `0 0 0 7px ${color}26` : "none" }),
              h({ fontSize: 24, fontWeight: now ? 600 : 500, color: done || now ? T.text : T.muted }, name)),
          ];
        })),
        h({ fontSize: 24, color: T.muted }, note))),
    1200, 630);
}
