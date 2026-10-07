import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {Background, appear, exitAt} from './ui';
import {Hook, Reveal} from './scenes/Intro';
import {Agents} from './scenes/Agents';
import {Call} from './scenes/Call';
import {Escrow} from './scenes/Escrow';
import {Checkout} from './scenes/Checkout';
import {Proof} from './scenes/Proof';
import {Close} from './scenes/Close';

const SCENES: [React.FC, number][] = [
  [Hook, 105], [Reveal, 105], [Agents, 165], [Call, 180], [Escrow, 150], [Checkout, 195], [Proof, 180], [Close, 210],
];
export const LAUNCH_FRAMES = SCENES.reduce((sum, [, length]) => sum + length, 0);

// Each scene eases in and blurs out so cuts feel continuous.
const Scene: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const p = Math.min(appear(frame, 0, 10), exitAt(frame, durationInFrames - 10, 10));
  return <AbsoluteFill style={{opacity: p, filter: `blur(${(1 - p) * 10}px)`}}>{children}</AbsoluteFill>;
};

export const Launch: React.FC = () => {
  let from = 0;
  return (
    <Background>
      {SCENES.map(([Component, length], i) => {
        const start = from;
        from += length;
        return (
          <Sequence key={i} from={start} durationInFrames={length}>
            <Scene><Component /></Scene>
          </Sequence>
        );
      })}
    </Background>
  );
};
