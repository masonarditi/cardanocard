import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {Mono, Pill, Pop, Rise, appear, ease} from '../ui';
import {T} from '../theme';
import {FACTS, short} from '../facts';

const Node: React.FC<{title: string; sub: string; x: number; accent?: boolean; start: number}> = ({title, sub, x, accent, start}) => {
  const frame = useCurrentFrame();
  const p = appear(frame, start, 18);
  return (
    <div style={{position: 'absolute', left: x, top: 380, width: 460, height: 190, borderRadius: 22, opacity: p,
      transform: `translateY(${(1 - p) * 20}px)`, boxSizing: 'border-box', padding: 30,
      background: accent ? T.cardano : T.panel, color: accent ? 'white' : T.text,
      border: `1px solid ${accent ? T.cardano : T.line}`, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10}}>
      <div style={{fontSize: 36, fontWeight: 650, letterSpacing: -0.8}}>{title}</div>
      <Mono size={20} color={accent ? 'rgba(255,255,255,0.75)' : T.muted}>{sub}</Mono>
    </div>
  );
};

export const Escrow: React.FC = () => {
  const frame = useCurrentFrame();
  const travel = interpolate(frame, [40, 85], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});
  const x = 800 + travel * (1130 - 800 - 140);
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{alignItems: 'center', paddingTop: 150}}>
        <Rise text="The buyer's payment locks in *escrow* on Cardano." start={2} size={60} />
      </AbsoluteFill>
      <Node title="Buyer agent" sub="Masumi purchasing wallet" x={330} start={10} />
      <Node title="Masumi escrow" sub="smart contract · Cardano Preprod" x={1130} accent start={18} />
      <div style={{position: 'absolute', left: 790, top: 474, width: 340, height: 2, background: T.line}} />
      <div style={{position: 'absolute', left: x, top: 448, opacity: appear(frame, 34, 8) * (1 - appear(frame, 82, 10)),
        fontFamily: T.mono, fontSize: 22, padding: '12px 18px', borderRadius: 999, background: T.cardano, color: 'white'}}>
        {FACTS.escrow}
      </div>
      <Pop start={88} style={{position: 'absolute', left: 1130, top: 600}}>
        <Pill color={T.ok} upper={false}>funds locked · {FACTS.escrow}</Pill>
      </Pop>
      <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 150}}>
        <Pop start={100}>
          <Mono size={26} color={T.text}>
            tx {short(FACTS.lockTx, 10, 8)}  ·  block {FACTS.lockBlock}  ·  {FACTS.lockTime} SGT
          </Mono>
        </Pop>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
