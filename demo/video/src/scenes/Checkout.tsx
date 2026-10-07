import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {Mono, Panel, Pill, Pop, Rise, appear} from '../ui';
import {T} from '../theme';
import {FACTS, short} from '../facts';

const Row: React.FC<{k: string; v: string; start: number; color?: string}> = ({k, v, start, color = T.text}) => {
  const frame = useCurrentFrame();
  const p = appear(frame, start, 14);
  return (
    <div style={{display: 'flex', justifyContent: 'space-between', padding: '14px 0', borderBottom: `1px solid ${T.line}`,
      opacity: p, transform: `translateX(${(1 - p) * 16}px)`}}>
      <Mono size={22}>{k}</Mono>
      <span style={{fontSize: 26, fontWeight: 550, color}}>{v}</span>
    </div>
  );
};

export const Checkout: React.FC = () => (
  <AbsoluteFill>
    <AbsoluteFill style={{alignItems: 'center', paddingTop: 140}}>
      <Rise text="Then it *buys* the real thing." start={2} size={60} />
    </AbsoluteFill>
    <AbsoluteFill style={{flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 56, paddingTop: 90}}>
      <Pop start={14}>
        <div style={{width: 520, height: 320, borderRadius: 28, padding: 38, boxSizing: 'border-box', position: 'relative',
          background: 'linear-gradient(145deg, #1C1C1E, #0A0A0A)', color: 'white',
          boxShadow: '0 30px 80px rgba(0,0,0,0.18)'}}>
          <Mono size={20} color="rgba(255,255,255,0.6)">agentcard vault</Mono>
          <div style={{width: 74, height: 54, borderRadius: 10, marginTop: 34, background: 'linear-gradient(135deg, #E9D6A1, #B8995A)'}} />
          <div style={{fontFamily: T.mono, fontSize: 30, letterSpacing: 1, marginTop: 34, whiteSpace: 'nowrap'}}>•••• •••• •••• ••••</div>
          <div style={{position: 'absolute', right: 38, bottom: 34}}><Pill color="#5BE3A8">auto-approved</Pill></div>
        </div>
      </Pop>
      <Pop start={46}>
        <Panel title="amazon order · via agentcard purchase api" width={720} badge={<Pill color={T.ok}>settled</Pill>}>
          <Row k="item" v={FACTS.item} start={56} />
          <Row k="subtotal" v={FACTS.subtotal} start={66} />
          <Row k="ships to" v="Berkeley, CA 94704" start={76} />
          <Row k="order" v={short(FACTS.orderId, 8, 4)} start={86} />
          <Row k="placed" v={`${FACTS.orderTime} SGT`} start={96} />
        </Panel>
      </Pop>
    </AbsoluteFill>
  </AbsoluteFill>
);
