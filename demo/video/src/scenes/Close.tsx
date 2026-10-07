import React from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame} from 'remotion';
import {Mono, Rise, appear} from '../ui';
import {T} from '../theme';
import {Wordmark} from './Wordmark';

// Merchants from Agentcard's live Purchase API catalogue (GET /buy/merchants, Oct 2026).
const VENDORS = ['Amazon', 'Walmart', 'Target', 'Best Buy', 'Flights', 'Hotels', 'DoorDash', 'OpenTable', 'StubHub', 'Mindbody'];

// Official marks: masumi.network wordmark, cardano.org horizontal blue logo (shown at least as large as partners').
export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  const row = appear(frame, 88, 16);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 40}}>
      <div style={{opacity: appear(frame, 0, 18)}}><Wordmark size={96} /></div>
      <Rise text="Any agent on Masumi can now buy *anything* a card can." start={14} size={52} />
      <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 14, width: 1300}}>
        {VENDORS.map((v, i) => {
          const p = appear(frame, 34 + i * 4, 14);
          return (
            <span key={v} style={{opacity: p, transform: `translateY(${(1 - p) * 12}px)`, fontSize: 30, fontWeight: 500,
              letterSpacing: -0.6, padding: '12px 26px', borderRadius: 999, background: T.panel, border: `1px solid ${T.line}`}}>
              {v}
            </span>
          );
        })}
      </div>
      <div style={{opacity: row, transform: `translateY(${(1 - row) * 12}px)`, display: 'flex', alignItems: 'center', gap: 44, marginTop: 20}}>
        <Mono size={22}>built on</Mono>
        <Img src={staticFile('masumi-wordmark.webp')} style={{height: 34}} />
        <Img src={staticFile('cardano-horizontal-blue.svg')} style={{height: 44}} />
        <span style={{fontSize: 30, fontWeight: 600, letterSpacing: -0.6, color: T.text}}>Agentcard</span>
        <Mono size={22}>· TOKEN2049 Origins</Mono>
      </div>
    </AbsoluteFill>
  );
};
