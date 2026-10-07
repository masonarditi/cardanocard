import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {T} from './theme';

export const ease = Easing.bezier(0.16, 1, 0.3, 1);

export const appear = (frame: number, start: number, length = 18) =>
  interpolate(frame, [start, start + length], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});

export const exitAt = (frame: number, start: number, length = 12) =>
  interpolate(frame, [start, start + length], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease});

export const Background: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: T.bg, fontFamily: T.sans, color: T.text, overflow: 'hidden'}}>
      <AbsoluteFill style={{
        backgroundImage: `linear-gradient(${T.grid} 1px, transparent 1px), linear-gradient(90deg, ${T.grid} 1px, transparent 1px)`,
        backgroundSize: '96px 96px', backgroundPosition: `0 ${-frame * 0.4}px`,
        maskImage: 'radial-gradient(ellipse 70% 60% at 50% 50%, black 30%, transparent 100%)',
      }} />
      <AbsoluteFill style={{background: `radial-gradient(ellipse 50% 40% at 50% 55%, ${T.glow}, transparent 70%)`}} />
      {children}
    </AbsoluteFill>
  );
};

// Words rise and unblur one by one.
export const Rise: React.FC<{text: string; start: number; size?: number; color?: string; weight?: number; stagger?: number}> =
  ({text, start, size = 96, color = T.text, weight = 400, stagger = 3}) => {
    const frame = useCurrentFrame();
    return (
      <div style={{fontSize: size, fontWeight: weight, letterSpacing: -size * 0.04, lineHeight: 1.05, color, textAlign: 'center'}}>
        {text.split(' ').map((word, i) => {
          const p = appear(frame, start + i * stagger, 16);
          const hot = word.startsWith('*');
          return (
            <span key={i} style={{display: 'inline-block', opacity: p, transform: `translateY(${(1 - p) * 28}px)`,
              filter: `blur(${(1 - p) * 8}px)`, marginRight: size * 0.24, color: hot ? T.accent : undefined,
              fontWeight: hot ? weight + 100 : undefined}}>{word.replace(/\*/g, '')}</span>
          );
        })}
      </div>
    );
  };

export const Mono: React.FC<{children: React.ReactNode; size?: number; color?: string; style?: React.CSSProperties}> =
  ({children, size = 22, color = T.muted, style}) => (
    <span style={{fontFamily: T.mono, fontSize: size, color, letterSpacing: 0, ...style}}>{children}</span>
  );

export const Pill: React.FC<{children: React.ReactNode; color?: string; upper?: boolean}> = ({children, color = T.accent, upper = false}) => (
  <span style={{fontFamily: T.mono, fontSize: 18, color, border: `1px solid ${color}55`, background: `${color}14`,
    borderRadius: 999, padding: '6px 14px', letterSpacing: 0.5, textTransform: upper ? 'uppercase' : 'none'}}>{children}</span>
);

export const Panel: React.FC<{title: string; children: React.ReactNode; width?: number; style?: React.CSSProperties; badge?: React.ReactNode}> =
  ({title, children, width = 820, style, badge}) => (
    <div style={{width, background: T.panel, border: `1px solid ${T.line}`, borderRadius: 18, overflow: 'hidden',
      boxShadow: '0 30px 80px rgba(0,0,0,0.07)', ...style}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 10, padding: '16px 22px', borderBottom: `1px solid ${T.line}`}}>
        {[0, 1, 2].map((i) => <div key={i} style={{width: 12, height: 12, borderRadius: 6, background: T.line}} />)}
        <Mono size={18} style={{marginLeft: 12, flex: 1}}>{title}</Mono>
        {badge}
      </div>
      <div style={{padding: '24px 28px'}}>{children}</div>
    </div>
  );

// Reveals code/terminal lines character by character.
export const Typed: React.FC<{lines: {text: string; color?: string}[]; start: number; cps?: number; size?: number}> =
  ({lines, start, cps = 2.2, size = 24}) => {
    const frame = useCurrentFrame();
    let budget = Math.max(0, (frame - start) * cps);
    return (
      <div style={{fontFamily: T.code, fontSize: size, lineHeight: 1.6, whiteSpace: 'pre'}}>
        {lines.map((line, i) => {
          const shown = line.text.slice(0, Math.max(0, Math.floor(budget)));
          budget -= line.text.length;
          return <div key={i} style={{color: line.color ?? T.text, minHeight: size * 1.6}}>{shown}</div>;
        })}
      </div>
    );
  };

export const Pop: React.FC<{start: number; children: React.ReactNode; style?: React.CSSProperties}> = ({start, children, style}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - start, fps, config: {damping: 18, stiffness: 140}});
  return <div style={{opacity: Math.min(1, s * 1.4), transform: `scale(${0.94 + s * 0.06}) translateY(${(1 - s) * 18}px)`, ...style}}>{children}</div>;
};
