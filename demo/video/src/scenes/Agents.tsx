import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {Mono, Rise, appear} from '../ui';
import {T} from '../theme';
import {AGENTS} from '../agents';

const W = 1920, H = 1080, CX = W / 2, CY = 470;
const CARD_W = 500, CARD_H = 104;

const slots = AGENTS.slice(0, 6).map((agent, i) => {
  const left = i % 2 === 0;
  const row = Math.floor(i / 2);
  return {agent, x: left ? 90 : W - 90 - CARD_W, y: 250 + row * 150, left};
});

export const Agents: React.FC = () => {
  const frame = useCurrentFrame();
  const hub = appear(frame, 42, 20);
  return (
    <AbsoluteFill>
      <svg width={W} height={H} style={{position: 'absolute'}}>
        {slots.map(({x, y, left}, i) => {
          const sx = left ? x + CARD_W : x, sy = y + CARD_H / 2, ex = left ? CX - 120 : CX + 120;
          const d = `M ${sx} ${sy} C ${(sx + ex) / 2} ${sy}, ${(sx + ex) / 2} ${CY}, ${ex} ${CY}`;
          const p = appear(frame, 50 + i * 5, 26);
          const pulse = (frame * 0.02 + i * 0.17) % 1;
          return (
            <g key={i}>
              <path d={d} fill="none" stroke={T.line} strokeWidth={2} pathLength={1} strokeDasharray="1" strokeDashoffset={1 - p} />
              {p > 0.99 && (
                <path d={d} fill="none" stroke={T.accent} strokeWidth={3} pathLength={1}
                  strokeDasharray="0.06 1" strokeDashoffset={-pulse} strokeLinecap="round" />
              )}
            </g>
          );
        })}
      </svg>
      {slots.map(({agent, x, y}, i) => {
        const p = appear(frame, 8 + i * 4, 18);
        return (
          <div key={agent.name} style={{position: 'absolute', left: x, top: y, width: CARD_W, height: CARD_H, opacity: p,
            transform: `translateY(${(1 - p) * 20}px)`, background: T.panel, border: `1px solid ${T.line}`, borderRadius: 16,
            padding: '18px 22px', boxSizing: 'border-box'}}>
            <div style={{fontSize: 28, fontWeight: 600, letterSpacing: -0.6}}>{agent.name}</div>
            <Mono size={18} style={{display: 'block', marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>
              {agent.tagline}
            </Mono>
          </div>
        );
      })}
      <div style={{position: 'absolute', left: CX - 120, top: CY - 80, width: 240, height: 160, borderRadius: 24, opacity: hub,
        transform: `scale(${0.9 + hub * 0.1})`, background: T.cardano, color: 'white',
        boxShadow: `0 20px ${60 + Math.sin(frame / 8) * 16}px rgba(0,51,173,0.28)`, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 8}}>
        <div style={{fontSize: 30, fontWeight: 700, letterSpacing: -0.8}}>Cardano Card</div>
        <Mono size={16} color="rgba(255,255,255,0.75)">masumi agent</Mono>
      </div>
      <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 96, gap: 14}}>
        <Rise text="Any agent on Masumi can *hire* it." start={70} size={56} />
        <div style={{opacity: interpolate(frame, [92, 108], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}}>
          <Mono size={24}>MIP-003 · POST /start_job · paid through Masumi escrow</Mono>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
