import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {Mono, Rise, appear} from '../ui';
import {T} from '../theme';
import {Wordmark} from './Wordmark';

const Center: React.FC<{children: React.ReactNode; gap?: number}> = ({children, gap = 28}) => (
  <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap}}>{children}</AbsoluteFill>
);

export const Hook: React.FC = () => (
  <Center>
    <Rise text="AI agents can pay each other." start={6} size={92} />
    <Rise text="They still can't *buy* anything." start={42} size={92} color={T.muted} />
  </Center>
);

export const Reveal: React.FC = () => {
  const frame = useCurrentFrame();
  const until = appear(frame, 0, 12) * interpolate(frame, [30, 42], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <Center gap={36}>
      <div style={{position: 'absolute', opacity: until}}><Rise text="Until now." start={0} size={92} /></div>
      <div style={{opacity: appear(frame, 40, 20), transform: `scale(${0.96 + appear(frame, 40, 24) * 0.04})`}}>
        <Wordmark size={132} />
      </div>
      <div style={{opacity: appear(frame, 58, 18), transform: `translateY(${(1 - appear(frame, 58, 18)) * 16}px)`}}>
        <Mono size={34} color={T.muted}>a credit card for every Masumi agent</Mono>
      </div>
    </Center>
  );
};
