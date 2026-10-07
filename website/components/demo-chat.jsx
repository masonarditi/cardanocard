"use client";

import { useEffect, useRef, useState } from 'react';

const EXAMPLES = {
  coffee: { ask: 'Find me a bag of Colombian ground coffee under $20.', name: 'Colombian ground coffee', detail: '16 oz · one bag', estimate: 12.41, emoji: '☕' },
  diapers: { ask: 'Find a small pack of size 3 diapers for under $20.', name: 'Size 3 diapers', detail: 'One small pack · everyday essentials', estimate: 14.99, emoji: '🧸' },
  charger: { ask: 'Find a USB-C charging cable under $20.', name: 'USB-C charging cable', detail: '6 ft cable · one pack', estimate: 9.99, emoji: '🔌' },
};
const STEPS = ['Request received', 'Quote approved', 'Escrow funded', 'Merchant checkout', 'Settlement'];
const money = (amount) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);

function Brand() {
  return <span className="brand"><span className="brand-symbol">c<span>↗</span></span><span>cardano<span className="brand-light">card</span></span></span>;
}

export default function DemoChat({ example, onClose }) {
  const dialog = useRef(null);
  const feed = useRef(null);
  const input = useRef(null);
  const sequence = useRef(0);
  const initialized = useRef(false);
  const [messages, setMessages] = useState([]);
  const [request, setRequest] = useState(null);
  const [phase, setPhase] = useState('idle');
  const [scenario, setScenario] = useState('success');
  const [draft, setDraft] = useState('');
  const [toast, setToast] = useState('');
  const started = useRef(false);
  const stage = {idle:-1, finding:0, quote:1, approved:2, funded:3, checkout:3, settling:4, paid:5, refunded:5, over_budget:0}[phase];
  const finished = ['paid','refunded','over_budget'].includes(phase);
  const locked = phase !== 'idle';
  const complete = phase === 'paid' || phase === 'refunded';

  useEffect(() => {
    dialog.current.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, []);

  useEffect(() => {
    if (example && !initialized.current) { initialized.current = true; send(EXAMPLES[example].ask, example); }
  }, [example]);

  useEffect(() => {
    feed.current?.scrollTo({top: feed.current.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }, [messages, phase]);

  function append(message) { setMessages(previous => [...previous, {...message, id: ++sequence.current}]); }
  function say(text, outgoing=false) { append({type:'text', text, outgoing}); }
  function event(text, refund=false) { append({type:'event',text,refund}); }

  // Only browser-local state changes occur here. Live funding, messages and checkout
  // must be added behind separately authenticated server endpoints.
  useEffect(() => {
    let timer;
    if (phase === 'finding') timer = setTimeout(() => {
      if (request.budget <= 0 || request.budget < request.product.estimate) {
        say(`The sample item is ${money(request.product.estimate)}, above your ${money(request.budget)} limit. I won’t start checkout or request funding.`);
        event('Budget respected · no payment', true);
        setPhase('over_budget');
      } else {
        say(request.matched ? `Found a sample option: ${request.product.name.toLowerCase()} for ${money(request.product.estimate)}. Let’s review it first.` : 'I can show you the flow with an illustrative quote. This preview doesn’t search live merchant inventory.');
        append({type:'quote'}); setPhase('quote');
      }
    }, 850);
    if (phase === 'approved') timer = setTimeout(() => {
      say('Ready. In the connected flow, you’d fund Masumi escrow before I check out. Here, you can simulate that step.');
      append({type:'fund'});
    }, 650);
    if (phase === 'funded') timer = setTimeout(() => {
      say('Escrow is ready. Now I’m stepping through AgentCard checkout.'); setPhase('checkout');
    }, 600);
    if (phase === 'checkout') timer = setTimeout(() => {
      if (scenario === 'refund') {
        say('The sandbox checkout was declined. No merchant purchase was made, so this demo follows the refund path.'); event('↩ Demo refund requested',true);
      } else {
        say('Your sample order is confirmed. I’ve saved the order result so the escrow can follow the payout path.'); event('✓ Demo order confirmed');
      }
      setPhase('settling');
    },1600);
    if (phase === 'settling') timer = setTimeout(() => {
      const outcome = scenario === 'refund' ? 'refunded' : 'paid';
      say(scenario === 'refund' ? 'Demo refund complete. Your 20 tUSDM test payment is shown as returned. On the real network, fees and confirmation times affect settlement.' : 'All set. The demo seller payout is complete. In a live run, payout follows the contract’s release window.');
      event(scenario === 'refund' ? '✓ Refunded · simulated settlement' : '✓ Seller paid · simulated settlement',scenario === 'refund');
      setRequest(previous => ({...previous,completedAt:new Date().toISOString(),outcome}));
      setPhase(outcome);
    },1400);
    return () => clearTimeout(timer);
  },[phase]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''),2600);
    return () => clearTimeout(timer);
  },[toast]);

  function send(raw, key) {
    const text = raw.trim();
    if (!text || started.current) return;
    started.current = true;
    const matched = key || (/coffee/i.test(text) ? 'coffee' : /diaper/i.test(text) ? 'diapers' : /charg|cable/i.test(text) ? 'charger' : null);
    const product = matched ? {...EXAMPLES[matched]} : {name:'Your requested item',detail:'Illustrative item for this demo',estimate:12.41,emoji:'🛍️'};
    const match = text.match(/\$\s*(\d+(?:\.\d{1,2})?)|(?:under|budget(?: of)?|up to)\s+(\d+(?:\.\d{1,2})?)/i);
    const budget = match ? Number(match[1] || match[2]) : 20;
    setRequest({text,product,budget,matched,escrow:'20 tUSDM',startedAt:new Date().toISOString(),demo:true});
    setDraft('');say(text,true);setPhase('finding');
  }
  function reset() { started.current=false;setMessages([]);setRequest(null);setPhase('idle');setDraft('');setTimeout(()=>input.current?.focus(),0); }
  function approve() { if(phase !== 'quote')return;say('Looks good. Go ahead.',true);setPhase('approved'); }
  function fund() { if(phase !== 'approved')return;event('✓ Demo escrow funded · 20 tUSDM');setPhase('funded'); }
  function receipt() {
    if (!complete) return;
    const blob=new Blob([JSON.stringify({service:'Cardano Card',mode:'browser_demo',real_payment:false,real_purchase:false,...request},null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='cardano-card-demo-receipt.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);setToast('Demo receipt downloaded');
  }
  function closeOnBackdrop(event) {
    if(event.target !== dialog.current)return;
    const r=dialog.current.getBoundingClientRect();
    if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)onClose();
  }

  return <><dialog ref={dialog} id="demo-dialog" aria-labelledby="demo-title" onClick={closeOnBackdrop} onCancel={event=>{event.preventDefault();onClose();}}>
    <div className="demo-window">
      <aside className="chat-sidebar"><Brand /><p className="sidebar-label">YOUR SHOPPING AGENT</p><button className="new-chat" onClick={reset}>＋ New conversation</button><div className="sidebar-conversation"><span>{request?.product.emoji || '☕'}</span><div>{request?.product.name || 'A little everyday help'}<small>Interactive demo</small></div></div><div className="sidebar-bottom"><span className="signal" /> Browser demo<p>No messages sent.<br />No wallet or card charged.</p></div></aside>
      <section className="chat-main">
        <header className="chat-header"><div><h2 id="demo-title">Your shopping agent</h2><span><i /> Here to help</span></div><div><span className="demo-badge">INTERACTIVE DEMO</span><button className="icon-button" aria-label="Close demo" onClick={onClose}>×</button></div></header>
        <div className="chat-feed" ref={feed} role="log" aria-live="polite">
          {phase === 'idle' ? <div className="chat-welcome"><span className="brand-symbol">c<span>↗</span></span><h3>A little everyday help.</h3><p>Tell me what you need and your budget. I’ll walk you through a sample shopping journey.</p><div className="chat-suggestions">{Object.entries(EXAMPLES).map(([key,item])=><button key={key} onClick={()=>send(item.ask,key)}>{item.emoji} {item.ask}</button>)}</div></div> : <span className="timestamp">Today · interactive preview</span>}
          {messages.map(message => message.type === 'text' ? <div key={message.id} className={`bubble ${message.outgoing?'outgoing':'incoming'}`}>{message.text}</div> : message.type === 'event' ? <div key={message.id} className={`feed-event${message.refund?' refund':''}`}>{message.text}</div> : message.type === 'quote' ? <div key={message.id} className="chat-quote"><div className="quote-tag">SAMPLE QUOTE · NOT A LIVE OFFER</div><h4>{request.product.name}</h4><p>{request.product.detail}</p><dl><dt>Item estimate</dt><dd>{money(request.product.estimate)}</dd><dt>Merchant ceiling</dt><dd>{money(request.budget)}</dd><dt>Test escrow</dt><dd>20 tUSDM</dd></dl><button className="chat-action" disabled={phase!=='quote'} onClick={approve}>{phase==='quote'?'Approve this sample quote →':'Quote approved ✓'}</button><small>The merchant ceiling covers the illustrative checkout. Test escrow is a separate demo payment.</small></div> : <div key={message.id} className="chat-quote"><div className="quote-tag">CARDANO PREPROD · DEMO</div><h4>20 tUSDM in escrow</h4><p>Funds stay associated with this purchase until its outcome is resolved.</p><button className="chat-action" disabled={phase!=='approved'} onClick={fund}>{phase==='approved'?'Simulate escrow funding →':'Demo funded ✓'}</button><small>No wallet opens and no transaction is sent.</small></div>)}
          {['finding','funded','checkout','settling'].includes(phase) && <div className="chat-typing" aria-label="Agent is composing a demo reply"><i /><i /><i /></div>}
          {finished && <button className="feed-reset" onClick={reset}>Try another request ↗</button>}
          {complete && <button className="feed-reset mobile-receipt" onClick={receipt}>Download demo receipt ↓</button>}
        </div>
        <div className="chat-bottom"><div className="scenario-control"><span>Preview outcome</span><button disabled={locked} aria-pressed={scenario==='success'} onClick={()=>setScenario('success')}>Order confirmed</button><button disabled={locked} aria-pressed={scenario==='refund'} onClick={()=>setScenario('refund')}>Checkout declined</button></div><form onSubmit={event=>{event.preventDefault();send(draft);}}><label className="sr-only" htmlFor="chat-input">Your shopping request</label><input ref={input} id="chat-input" autoComplete="off" maxLength={500} placeholder="What can I take off your list?" value={draft} disabled={locked} onChange={event=>setDraft(event.target.value)} /><button type="submit" disabled={locked} aria-label="Send shopping request">↑</button></form><p>Demo conversations stay in this browser session.</p></div>
      </section>
      <aside className="journey"><span className="sidebar-label">YOUR PURCHASE</span><h3>{request?.product.name || 'From text to taken care of.'}</h3><p>{phase==='paid'?'Sample order confirmed. Demo seller paid.':phase==='refunded'?'Sample checkout declined. Demo buyer refunded.':request?'One request. Every step visible.':'Your request starts the story.'}</p><ol>{STEPS.map((step,index)=><li key={step} className={index<stage?'done':index===stage?'current':''}><i>{index<stage?'✓':index+1}</i><span>{step}</span></li>)}</ol><div className="journey-total"><span>Merchant ceiling</span><strong>{request?money(request.budget):'—'}</strong><span>Demo escrow</span><strong>20 tUSDM</strong><small>Test payment shown for illustration.<br />Separate from merchant spending.</small></div><button disabled={!complete} onClick={receipt}>Download demo receipt ↓</button></aside>
    </div>
  </dialog><div id="toast" className={toast?'show':''} role="status">{toast}</div></>;
}
