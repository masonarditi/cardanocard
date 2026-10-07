import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {Mono, Panel, Pill, Pop, Rise, appear, ease} from '../ui';
import {T} from '../theme';
import {FACTS, short} from '../facts';

// The real Amazon listing for the item Cardano Card bought (B071GV16XD). Agentcard's Purchase API places the
// order server-side, so the page is shown as the product, with the real Agentcard order alongside it.
const Row: React.FC<{k: string; v: string; start: number}> = ({k, v, start}) => {
  const frame = useCurrentFrame();
  const p = appear(frame, start, 14);
  return (
    <div style={{display: 'flex', justifyContent: 'space-between', padding: '11px 0', borderBottom: `1px solid ${T.line}`,
      opacity: p, transform: `translateX(${(1 - p) * 16}px)`}}>
      <Mono size={20}>{k}</Mono>
      <span style={{fontSize: 22, fontWeight: 500}}>{v}</span>
    </div>
  );
};

const Browser: React.FC = () => {
  const frame = useCurrentFrame();
  const zoom = interpolate(frame, [20, 150], [1, 1.45], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});
  const ring = appear(frame, 110, 14);
  return (
    <div style={{width: 1040, height: 660, borderRadius: 16, overflow: 'hidden', background: 'white', border: `1px solid ${T.line}`,
      boxShadow: '0 30px 80px rgba(0,0,0,0.10)', display: 'flex', flexDirection: 'column'}}>
      <div style={{height: 50, display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px', background: '#F0F0F0',
        borderBottom: `1px solid ${T.line}`}}>
        {['#FF5F57', '#FEBC2E', '#28C840'].map((c) => <div key={c} style={{width: 12, height: 12, borderRadius: 6, background: c}} />)}
        <div style={{marginLeft: 18, flex: 1, height: 30, borderRadius: 8, background: 'white', display: 'flex', alignItems: 'center',
          padding: '0 14px'}}>
          <Mono size={17} color={T.text}>amazon.com/dp/B071GV16XD</Mono>
        </div>
      </div>
      <div style={{flex: 1, overflow: 'hidden'}}>
        {/* zoom toward the price and buy box; the ring marks the $1.32 price inside the zoomed page */}
        <div style={{position: 'relative', transform: `scale(${zoom})`, transformOrigin: '80% 62%'}}>
          <Img src={staticFile('amazon-product.png')} style={{width: '100%', display: 'block'}} />
          <div style={{position: 'absolute', left: 790, top: 272, width: 66, height: 42, borderRadius: 10, opacity: ring,
            border: `3px solid ${T.accent}`, boxShadow: `0 0 0 ${(1 - ring) * 14}px rgba(250,0,140,0.15)`}} />
        </div>
      </div>
    </div>
  );
};

export const Checkout: React.FC = () => (
  <AbsoluteFill>
    <AbsoluteFill style={{alignItems: 'center', paddingTop: 70}}>
      <Rise text="Then it *buys* it on Amazon." start={2} size={60} />
    </AbsoluteFill>
    <AbsoluteFill style={{flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 40, paddingTop: 120}}>
      <Pop start={8}><Browser /></Pop>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26, width: 560}}>
        <Pop start={40}>
          <div style={{height: 210, borderRadius: 22, padding: 30, boxSizing: 'border-box', position: 'relative', color: 'white',
            background: 'linear-gradient(145deg, #1C1C1E, #0A0A0A)', boxShadow: '0 30px 80px rgba(0,0,0,0.18)'}}>
            <Mono size={18} color="rgba(255,255,255,0.6)">agentcard vault</Mono>
            <div style={{width: 60, height: 44, borderRadius: 8, marginTop: 24, background: 'linear-gradient(135deg, #E9D6A1, #B8995A)'}} />
            <div style={{fontSize: 26, letterSpacing: 2, marginTop: 22, whiteSpace: 'nowrap'}}>•••• •••• •••• ••••</div>
            <div style={{position: 'absolute', right: 30, bottom: 28}}><Pill color="#5BE3A8">auto-approved</Pill></div>
          </div>
        </Pop>
        <Pop start={70}>
          <Panel title="order placed via agentcard" width={560} badge={<Pill color={T.ok}>settled</Pill>}>
            <Row k="item" v="Trident Original gum, 14 pc" start={80} />
            <Row k="subtotal" v={FACTS.subtotal} start={88} />
            <Row k="ships to" v="Berkeley, CA 94704" start={96} />
            <Row k="order" v={short(FACTS.orderId, 8, 4)} start={104} />
            <Row k="placed" v={`${FACTS.orderTime} SGT`} start={112} />
          </Panel>
        </Pop>
      </div>
    </AbsoluteFill>
  </AbsoluteFill>
);
