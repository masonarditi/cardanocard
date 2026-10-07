"use client";

import { useState } from 'react';
import DemoChat from './demo-chat';
import PhonePreview from './phone-preview';

export default function Landing() {
  const [demo, setDemo] = useState(null);
  function handleClick(event) {
    const trigger = event.target.closest('[data-open-demo], [data-example]');
    if (trigger) setDemo({ example: trigger.dataset.example || null, trigger });
  }
  function closeDemo() {
    const trigger = demo?.trigger;
    setDemo(null);
    requestAnimationFrame(() => trigger?.focus());
  }
  return <><div onClick={handleClick}>
<a className="skip" href="#main">Skip to content</a>
<div className="announcement"><span className="signal"></span> Built for the TOKEN2049 hackathon <span className="announcement-separator">·</span><a href="#how-it-works">Meet your shopping agent <span>↗</span></a></div>
<header className="nav">
<nav className="nav-left" aria-label="Main navigation"><a href="#how-it-works">How it works</a><a href="#under-the-hood">The technology</a></nav>
<a className="brand" href="#" aria-label="Cardano Card home"><span className="brand-symbol" aria-hidden="true">c<span>↗</span></span><span>cardano<span className="brand-light">card</span></span></a>
<div className="nav-right"><a href="#questions">Questions?</a><button className="pill small" data-open-demo>Try the demo <span>↗</span></button></div>
</header>
<main id="main">
<section className="hero" aria-labelledby="hero-heading">
<div className="hero-water" aria-hidden="true"></div>
<div className="hero-copy">
<div className="eyebrow hero-eyebrow"><span className="mini-message">•••</span> A little less checkout. A lot more living.</div>
<h1 id="hero-heading">Your next purchase,<br /><span>one text away.</span></h1>
<p>You ask. Your agent shops.<br className="mobile-break" /> Your funds stay protected along the way.</p>
<button className="text-button" data-open-demo><span className="message-icon" aria-hidden="true">•••</span> Text your agent <span className="button-arrow">↗</span></button>
<span className="cta-note">Try the interactive demo. No card or wallet needed.</span>
</div>
<div className="hero-product">
<div className="floating-note note-left"><span className="note-icon">↗</span><div><strong>One text. Handled.</strong><span>From “I need it” to “it’s ordered.”</span></div></div>
<PhonePreview />
<p className="phone-preview-caption">An example conversation. Your everyday, taken care of.</p>
<div className="floating-note note-right"><span className="note-icon shield">✓</span><div><strong>Your money has a plan.</strong><span>Order confirmed, or refund initiated.</span></div></div>
</div>
<div className="hero-bottom"><span>Built on Cardano. Made for everyday life.</span><a href="#how-it-works" aria-label="See how it works">↓</a></div>
</section>
<section className="partners" aria-label="Underlying technology"><span>A simple conversation.<br />A serious system underneath.</span><div className="partner masumi">✳ <b>masumi</b></div><div className="partner agentcard"><span>▰</span> AgentCard</div><div className="partner cardano"><span>⠿</span> Cardano</div><span className="network-label"><i></i> Preprod demo</span></section>
<section className="intro section-pad" id="how-it-works">
<p className="eyebrow dark-eyebrow">LESS TABS. MORE LIFE.</p>
<h2>What if your shopping list<br />was just a conversation?</h2>
<p className="section-lead">The diapers you forgot. Your next bag of coffee. That charger you keep borrowing.<br className="desktop-break" /> Tell your agent what you need. Stay in the conversation, not the checkout.</p>
<div className="request-chips" aria-label="Try a shopping request"><button data-example="diapers">🧸 Diapers, please <span>↗</span></button><button data-example="coffee">☕ Restock my coffee <span>↗</span></button><button data-example="charger">🔌 Find me a charger <span>↗</span></button></div>
<div className="feature-grid">
<article className="feature feature-conversation"><div className="feature-visual"><span className="mini-date">Today 10:24 AM</span><span className="bubble outgoing">Can you find diapers for under $20?</span><span className="bubble incoming">Absolutely. I’ll find an option and check the total with you first.</span><span className="typing"><i></i><i></i><i></i></span></div><div className="feature-copy"><span>01 / ASK</span><h3>Text it like you mean it.</h3><p>No filters to wrestle with. Just what you need, in your own words.</p></div></article>
<article className="feature feature-approval"><div className="feature-visual"><div className="approval-ticket"><div><span className="ticket-symbol">↗</span><span>Ready when you are<small>Review before checkout</small></span></div><dl><dt>Selected item</dt><dd>Colombian coffee</dd><dt>Spending ceiling</dt><dd>$20.00</dd></dl><span className="approval-button">Looks good. Go ahead. <b>✓</b></span></div></div><div className="feature-copy"><span>02 / APPROVE</span><h3>Your budget. Your call.</h3><p>See the quote and payment terms. Give the green light when you’re ready.</p></div></article>
<article className="feature feature-outcome"><div className="feature-visual"><div className="orbit"><span className="orbit-center">✓</span><span className="orbit-dot one"></span><span className="orbit-dot two"></span></div><div className="outcome-label">A clear outcome.<span>Confirmation or refund status, in the chat.</span></div></div><div className="feature-copy"><span>03 / FOLLOW ALONG</span><h3>Never “where did it go?”</h3><p>Track escrow, checkout, and settlement as separate steps.</p></div></article>
</div>
</section>
<section className="demo-section section-pad" id="conversation">
<div className="demo-section-copy"><p className="eyebrow dark-eyebrow">YOUR VERY CAPABLE +1</p><h2>A shopping agent.<br />With receipts.</h2><p>One place for the request, the approval, and what happened next. Every step has a status you can actually follow.</p><div className="demo-benefits"><span><i>✓</i> Ask in plain language</span><span><i>✓</i> See the cost before approving</span><span><i>✓</i> Follow the order or refund</span></div><button className="black-button" data-open-demo>Take it for a text drive <span>↗</span></button><small>Interactive preview · simulated payments</small></div>
<div className="receipt-preview"><div className="receipt-top"><span className="brand-symbol">c<span>↗</span></span><span>Cardano Card<small>YOUR PURCHASE, AT A GLANCE</small></span><span className="receipt-demo">DEMO</span></div><div className="receipt-request">“Restock my coffee.<br />Keep it under $20.”</div><div className="receipt-line"><span className="receipt-dot done">✓</span><div><strong>Request understood</strong><small>One bag of Colombian ground coffee</small></div><span>01</span></div><div className="receipt-line"><span className="receipt-dot done">✓</span><div><strong>Quote approved</strong><small>You set the spending ceiling</small></div><span>02</span></div><div className="receipt-line"><span className="receipt-dot done">✓</span><div><strong>Escrow funded</strong><small>Held through Masumi on Cardano</small></div><span>03</span></div><div className="receipt-line"><span className="receipt-dot active">↗</span><div><strong>Agent handles checkout</strong><small>Powered by AgentCard</small></div><span>04</span></div><div className="receipt-foot"><span>Made to keep you in the loop.</span><span>◈</span></div></div>
</section>
<section className="system section-pad" id="under-the-hood"><div className="system-heading"><p className="eyebrow">REAL INFRASTRUCTURE. SIMPLE EXPERIENCE.</p><h2>A text on the surface.<br /><span>Trust underneath.</span></h2><p>The conversation is the easy part.<br />Our system coordinates the money and the purchase.</p></div><div className="system-flow"><div><span className="system-step">01</span><strong>You send the intent.</strong><p>An item, a budget, and your approval.</p><span className="system-tag">CONVERSATION</span></div><i>→</i><div><span className="system-step">02</span><strong>Masumi holds escrow.</strong><p>The Cardano payment is tracked against your job.</p><span className="system-tag">CARDANO + MASUMI</span></div><i>→</i><div><span className="system-step">03</span><strong>AgentCard checks out.</strong><p>A linked card fronts the merchant purchase.</p><span className="system-tag">AGENTCARD VAULT</span></div></div><div className="system-outcomes"><span><i>↗</i> Order confirmed <b>→</b> Seller payout</span><span><i>↩</i> Definitive no-purchase failure <b>→</b> Buyer refund</span></div><p className="system-note">Hackathon build on Cardano Preprod. Test assets do not fund real merchant spending. Uncertain checkout outcomes pause for review.</p></section>
<section className="faq section-pad" id="questions"><div><p className="eyebrow dark-eyebrow">A FEW GOOD QUESTIONS</p><h2>Before you<br />hit send.</h2><p>Here’s where the demo is today.</p></div><div className="faq-list"><details open><summary>What can I try right now?<span>＋</span></summary><p>This website is an interactive preview of the text-agent experience. Try a shopping request, approve a sample quote, and follow a simulated purchase or refund. It never moves money or places an order.</p></details><details><summary>Does this already work over SMS or iMessage?<span>＋</span></summary><p>The messaging experience is the interface we’re building. This preview runs in your browser. A live SMS or iMessage connection is not enabled here yet.</p></details><details><summary>What happens behind the conversation?<span>＋</span></summary><p>Our underlying system uses Masumi escrow on Cardano and AgentCard for merchant checkout. Local Preprod payout and refund cases have passed. The hosted V2 flow is being validated separately.</p></details><details><summary>What happens if a purchase fails?<span>＋</span></summary><p>A confirmed no-purchase failure follows the refund path. If a checkout’s outcome is uncertain, the job pauses for review rather than risking a duplicate purchase or a premature refund. On-chain settlement takes time.</p></details><details><summary>How are payments handled in the hackathon?<span>＋</span></summary><p>The current hosted registration uses a fixed 20 tUSDM test payment on Cardano Preprod. AgentCard sandbox confirmations decline by design. A real merchant purchase requires a separately authorized linked card.</p></details></div></section>
<section className="final-cta"><div className="final-water"></div><span className="brand-symbol">c<span>↗</span></span><h2>Life’s busy.<br />Text the shopping part.</h2><button className="text-button" data-open-demo><span className="message-icon">•••</span> Text your agent <span className="button-arrow">↗</span></button><span className="cta-note">One conversation. From intent to outcome.</span></section>
</main>
<footer><a className="brand" href="#"><span className="brand-symbol">c<span>↗</span></span><span>cardano<span className="brand-light">card</span></span></a><p>Built for the everyday. Powered by agents.</p><div><a href="#how-it-works">How it works</a><a href="#questions">Demo details</a><button data-open-demo>Open chat ↗</button></div><span className="footer-note">© 2026 Cardano Card · Hackathon prototype</span></footer>
</div>{demo && <DemoChat example={demo.example} onClose={closeDemo} />}</>;
}
