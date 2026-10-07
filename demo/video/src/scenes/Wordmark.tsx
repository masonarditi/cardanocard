import React from 'react';
import {T} from '../theme';

// Card glyph + name. The chip doubles as a Cardano-style node mark.
export const Wordmark: React.FC<{size?: number}> = ({size = 120}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: size * 0.32}}>
    <div style={{width: size * 1.1, height: size * 0.72, borderRadius: size * 0.1, position: 'relative',
      background: T.cardano, boxShadow: `0 ${size * 0.15}px ${size * 0.5}px rgba(0,51,173,0.25)`}}>
      <div style={{position: 'absolute', left: size * 0.14, top: size * 0.2, width: size * 0.22, height: size * 0.17,
        borderRadius: size * 0.04, background: 'rgba(255,255,255,0.85)'}} />
      <div style={{position: 'absolute', left: size * 0.14, bottom: size * 0.12, width: size * 0.62, height: size * 0.05,
        borderRadius: 4, background: 'rgba(255,255,255,0.45)'}} />
    </div>
    <div style={{fontSize: size, fontWeight: 500, letterSpacing: -size * 0.05, color: T.text}}>Cardano Card</div>
  </div>
);
