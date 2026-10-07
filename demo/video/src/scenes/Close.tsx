import React from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame} from 'remotion';
import {Mono, Rise, appear} from '../ui';
import {T} from '../theme';
import {Wordmark} from './Wordmark';

// Official marks: masumi.network wordmark, cardano.org horizontal blue logo (shown at least as large as partners').
export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  const row = appear(frame, 50, 16);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 44}}>
      <div style={{opacity: appear(frame, 0, 18)}}><Wordmark size={110} /></div>
      <Rise text="Any agent on Masumi can now *buy* from Amazon." start={16} size={52} />
      <div style={{opacity: row, transform: `translateY(${(1 - row) * 12}px)`, display: 'flex', alignItems: 'center', gap: 44, marginTop: 24}}>
        <Mono size={22}>built on</Mono>
        <Img src={staticFile('masumi-wordmark.webp')} style={{height: 34}} />
        <Img src={staticFile('cardano-horizontal-blue.svg')} style={{height: 44}} />
        <span style={{fontSize: 30, fontWeight: 600, letterSpacing: -0.6, color: T.text}}>Agentcard</span>
        <Mono size={22}>· TOKEN2049 Origins</Mono>
      </div>
    </AbsoluteFill>
  );
};
