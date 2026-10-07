import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {Mono, Pop, Rise, appear, ease} from '../ui';
import {T} from '../theme';
import {FACTS, short} from '../facts';

const STEPS = [
  {t: FACTS.lockTime, label: 'escrow locked', sub: `block ${FACTS.lockBlock}`},
  {t: FACTS.orderTime, label: 'amazon order placed', sub: short(FACTS.orderId, 8, 4)},
  {t: FACTS.resultTime, label: 'result proven on-chain', sub: `block ${FACTS.resultBlock}`},
];

export const Proof: React.FC = () => {
  const frame = useCurrentFrame();
  const line = interpolate(frame, [20, 90], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});
  const count = Math.round(interpolate(frame, [96, 136], [0, FACTS.seconds], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease}));
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{alignItems: 'center', paddingTop: 130}}>
        <Rise text="Every step is *provable.*" start={2} size={60} />
      </AbsoluteFill>
      <div style={{position: 'absolute', left: 260, right: 260, top: 470, height: 2, background: T.line}}>
        <div style={{width: `${line * 100}%`, height: 2, background: T.cardano}} />
      </div>
      {STEPS.map((s, i) => {
        const p = appear(frame, 24 + i * 28, 16);
        const left = 260 + i * ((1920 - 520) / 2);
        return (
          <div key={s.label} style={{position: 'absolute', left: left - 170, top: 400, width: 340, textAlign: 'center', opacity: p,
            transform: `translateY(${(1 - p) * 14}px)`}}>
            <Mono size={24} color={T.text}>{s.t}</Mono>
            <div style={{width: 18, height: 18, borderRadius: 9, background: T.cardano, margin: '22px auto',
              boxShadow: `0 0 0 6px rgba(0,51,173,0.12)`}} />
            <div style={{fontSize: 30, fontWeight: 600, letterSpacing: -0.6}}>{s.label}</div>
            <Mono size={20} style={{display: 'block', marginTop: 8}}>{s.sub}</Mono>
          </div>
        );
      })}
      <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 110, gap: 18}}>
        <div style={{opacity: appear(frame, 94, 14), display: 'flex', alignItems: 'baseline', gap: 24}}>
          <span style={{fontSize: 120, fontWeight: 500, letterSpacing: -6, color: T.text}}>{count}s</span>
          <span style={{fontSize: 34, color: T.muted}}>from escrow lock to on-chain proof of a real order</span>
        </div>
        <Pop start={140}>
          <Mono size={22}>result hash {short(FACTS.resultHash, 10, 6)} · no purchase? the buyer is refunded from escrow</Mono>
        </Pop>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
