// Cardano Card pitch deck in the Masumi brand (light, Inter, Electric Pink) with Cardano blue for on-chain proof.
// Build: node build.js  -> cardano-card.pptx, uploaded to Google Drive as Google Slides.
const fs = require('fs');
const pptxgen = require('pptxgenjs');
const FACTS = require('./facts.json');
const B = require('./brand.json');

const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = 'Cardano Card';
pres.theme = {headFontFace: B.sans, bodyFontFace: B.sans};

const W = 10, H = 5.625, M = 0.55;
const png = (f) => 'image/png;base64,' + fs.readFileSync(f).toString('base64');

const newSlide = () => pres.addSlide({background: {fill: B.paper}});
const text = (s, value, o) => s.addText(value, {isTextBox: true, margin: 0, fontFace: B.sans, color: B.ink, valign: 'top', ...o});
const mono = (s, value, o) => text(s, value, {fontFace: B.mono, color: B.inkMuted, ...o});
const title = (s, value) => text(s, value, {x: M, y: 0.42, w: W - 2 * M, h: 0.95, fontSize: 28, bold: true});
const card = (s, x, y, w, h) => s.addShape(pres.shapes.ROUNDED_RECTANGLE, {x, y, w, h, rectRadius: 0.12, fill: {color: B.card},
  line: {color: B.cardLine, width: 0.75}});
const dot = (s, x, y, d, color) => s.addShape(pres.shapes.OVAL, {x, y, w: d, h: d, fill: {color}, line: {color, width: 0}});
const cardGlyph = (s, x, y, size) => {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {x, y, w: size * 1.1, h: size * 0.72, rectRadius: size * 0.1,
    fill: {color: B.cardano}, line: {color: B.cardano, width: 0}});
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {x: x + size * 0.14, y: y + size * 0.2, w: size * 0.22, h: size * 0.17,
    rectRadius: size * 0.03, fill: {color: 'FFFFFF', transparency: 15}, line: {color: 'FFFFFF', width: 0}});
};

// 1. Title
{
  const s = newSlide();
  cardGlyph(s, M, 1.45, 0.62);
  text(s, 'Cardano Card', {x: M, y: 2.1, w: 8.5, h: 1.0, fontSize: 54, bold: true});
  text(s, 'A credit card for every Masumi agent', {x: M, y: 3.1, w: 8.5, h: 0.5, fontSize: 22, color: B.inkMuted});
  mono(s, 'built on', {x: M, y: H - 0.88, w: 0.9, h: 0.3, fontSize: 12});
  s.addImage({data: png('assets/masumi-wordmark.png'), x: M + 0.95, y: H - 0.88, w: 1.3, h: 0.2});
  s.addImage({data: png('assets/cardano-logo.png'), x: M + 2.55, y: H - 0.93, w: 1.5, h: 0.3});
  text(s, 'Agentcard', {x: M + 4.35, y: H - 0.9, w: 1.3, h: 0.3, fontSize: 14, bold: true});
  mono(s, 'TOKEN2049 Origins · Mason & Ezra', {x: W - M - 3.6, y: H - 0.88, w: 3.6, h: 0.3, fontSize: 12, align: 'right'});
}

// 2. Problem
{
  const s = newSlide();
  title(s, 'Masumi agents can pay each other, but they can’t buy anything');
  [['Agent economy', 'Agents hire agents on Masumi and settle in ADA or stablecoins on Cardano.', B.accent],
   ['Real economy', 'Amazon and almost every merchant only take a card at checkout.', B.ink]].forEach(([head, body, color], i) => {
    const x = M + i * 4.65;
    card(s, x, 1.65, 4.3, 2.6);
    dot(s, x + 0.35, 1.98, 0.42, color);
    text(s, head, {x: x + 0.35, y: 2.6, w: 3.6, h: 0.45, fontSize: 20, bold: true});
    text(s, body, {x: x + 0.35, y: 3.1, w: 3.6, h: 0.9, fontSize: 15, color: B.inkMuted});
  });
  text(s, 'Nothing connects the two, so an agent with funds still can’t order a pack of gum.',
    {x: M, y: 4.55, w: W - 2 * M, h: 0.5, fontSize: 15, bold: true});
}

// 3. How it works
{
  const s = newSlide();
  title(s, 'Cardano Card turns an escrowed agent payment into a real card purchase');
  const steps = [
    ['1', 'Request', 'Any agent calls start_job over MIP-003'],
    ['2', 'Quote', 'Agentcard builds the Amazon cart and its full price ceiling'],
    ['3', 'Escrow', 'The buyer approves and locks payment in Masumi escrow'],
    ['4', 'Purchase', 'Agentcard checks out with the vaulted card'],
    ['5', 'Proof', 'The order hash goes on-chain; escrow pays out or refunds'],
  ];
  const cw = 1.6, gap = (W - 2 * M - 5 * cw) / 4;
  steps.forEach(([n, head, body], i) => {
    const x = M + i * (cw + gap);
    card(s, x, 1.75, cw, 2.75);
    dot(s, x + 0.2, 1.98, 0.42, B.accent);
    text(s, n, {x: x + 0.2, y: 1.98, w: 0.42, h: 0.42, fontSize: 15, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle'});
    text(s, head, {x: x + 0.2, y: 2.6, w: cw - 0.35, h: 0.4, fontSize: 17, bold: true});
    text(s, body, {x: x + 0.2, y: 3.05, w: cw - 0.35, h: 1.35, fontSize: 13, color: B.inkMuted});
  });
  mono(s, 'If no purchase happens, the buyer is refunded from escrow. Nothing is ever bought twice.',
    {x: M, y: 4.75, w: W - 2 * M, h: 0.3, fontSize: 12});
}

// 4. Proof
{
  const s = newSlide();
  title(s, `A real Amazon order, proven on-chain ${FACTS.seconds} seconds after escrow locked`);
  const pts = [
    [FACTS.lockTime, 'Escrow locked', `block ${FACTS.lockBlock}`],
    [FACTS.orderTime, 'Amazon order placed', `${FACTS.item} · ${FACTS.subtotal}`],
    [FACTS.resultTime, 'Result proven on-chain', `block ${FACTS.resultBlock}`],
  ];
  const x0 = M + 0.25, x1 = W - M - 0.25, y = 2.55;
  s.addShape(pres.shapes.LINE, {x: x0, y, w: x1 - x0, h: 0, line: {color: B.cardano, width: 2}});
  pts.forEach(([t, head, sub], i) => {
    const cx = x0 + i * (x1 - x0) / 2;
    dot(s, cx - 0.09, y - 0.09, 0.18, B.cardano);
    const align = i === 0 ? 'left' : i === 2 ? 'right' : 'center';
    const bx = align === 'left' ? cx - 0.09 : align === 'right' ? cx - 2.7 + 0.09 : cx - 1.35;
    mono(s, `${t} SGT`, {x: bx, y: y - 0.55, w: 2.7, h: 0.3, fontSize: 13, color: B.ink, align});
    text(s, head, {x: bx, y: y + 0.28, w: 2.7, h: 0.35, fontSize: 16, bold: true, align});
    mono(s, sub, {x: bx, y: y + 0.66, w: 2.7, h: 0.5, fontSize: 11, align});
  });
  text(s, `${FACTS.seconds}s`, {x: M, y: 3.85, w: 1.9, h: 0.9, fontSize: 54, bold: true});
  text(s, 'from escrow lock to on-chain proof of a real order', {x: M + 1.95, y: 4.12, w: 5, h: 0.4, fontSize: 16, color: B.inkMuted});
  mono(s, `Cardano Preprod · Agentcard order ${FACTS.orderId.slice(0, 8)}… · tx ${FACTS.lockTx.slice(0, 10)}… and ${FACTS.resultTx.slice(0, 10)}…`,
    {x: M, y: H - 0.55, w: W - 2 * M, h: 0.25, fontSize: 10});
}

// 5. Safety
{
  const s = newSlide();
  title(s, 'The buyer’s money only moves after the order is certain');
  [['Escrow before checkout', 'The card is charged only after the buyer’s payment is locked on Cardano.'],
   ['Full price ceiling', 'Agentcard’s authorization ceiling, not just the subtotal, must fit the approved budget.'],
   ['Never bought twice', 'Every confirm is saved before it is sent; an uncertain one is inspected, not repeated.'],
   ['Refund only on proof', 'A definite no-purchase refunds the buyer. An uncertain outcome waits for reconciliation.'],
  ].forEach(([head, body], i) => {
    const x = M + (i % 2) * 4.55, y = 1.55 + Math.floor(i / 2) * 1.75;
    card(s, x, y, 4.35, 1.55);
    dot(s, x + 0.3, y + 0.32, 0.26, B.accent);
    text(s, head, {x: x + 0.8, y: y + 0.27, w: 3.35, h: 0.38, fontSize: 17, bold: true});
    text(s, body, {x: x + 0.8, y: y + 0.68, w: 3.35, h: 0.75, fontSize: 13, color: B.inkMuted});
  });
}

// 6. Architecture
{
  const s = newSlide();
  title(s, 'Built on Masumi, Cardano and Agentcard');
  const lane = (y, label, boxes) => {
    mono(s, label, {x: M, y: y + 0.28, w: 1.6, h: 0.3, fontSize: 11});
    const bw = (W - 2 * M - 1.75 - (boxes.length - 1) * 0.25) / boxes.length;
    boxes.forEach(([head, body, fill], i) => {
      const x = M + 1.75 + i * (bw + 0.25);
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {x, y, w: bw, h: 0.85, rectRadius: 0.1, fill: {color: fill || B.card},
        line: {color: fill || B.cardLine, width: 0.75}});
      text(s, head, {x: x + 0.2, y: y + 0.13, w: bw - 0.4, h: 0.32, fontSize: 15, bold: true, color: fill ? 'FFFFFF' : B.ink});
      text(s, body, {x: x + 0.2, y: y + 0.47, w: bw - 0.4, h: 0.3, fontSize: 12, color: fill ? 'E8EEFF' : B.inkMuted});
    });
  };
  lane(1.5, 'Buyers', [['Any Masumi agent', 'calls the MIP-003 API'], ['Escrow payment', 'locked before any purchase']]);
  lane(2.55, 'Cardano Card', [['Agent + coordinator', 'quote, approval, escrow checks', B.cardano],
    ['Purchase adapter', 'prepare · confirm · inspect', B.cardano]]);
  lane(3.6, 'Rails', [['Masumi node', 'escrow on Cardano'], ['Agentcard', 'Purchase API, vault'], ['Amazon', 'real order and delivery']]);
}

// 7. Next
{
  const s = newSlide();
  title(s, 'Next: open Cardano Card to every agent on the marketplace');
  [['Public endpoint', 'Registered on Masumi Preprod; hosted escrow backend next'],
   ['Caller limits', 'Per-agent caps before strangers can spend on a real card'],
   ['Mainnet', 'Real ADA and USDCx escrow'],
   ['Issued cards', 'Crypto-funded single-use cards from Agentcard instead of one vault card'],
  ].forEach(([head, body], i) => {
    const y = 1.6 + i * 0.85;
    dot(s, M, y + 0.09, 0.2, B.accent);
    text(s, head, {x: M + 0.45, y, w: 2.6, h: 0.4, fontSize: 17, bold: true});
    text(s, body, {x: M + 3.1, y: y + 0.02, w: 5.7, h: 0.4, fontSize: 15, color: B.inkMuted});
  });
}

pres.writeFile({fileName: 'cardano-card.pptx'}).then((f) => console.log('wrote', f));
