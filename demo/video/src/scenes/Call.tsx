import React from 'react';
import {AbsoluteFill} from 'remotion';
import {Mono, Panel, Pill, Pop, Rise, Typed} from '../ui';
import {T} from '../theme';
import {FACTS} from '../facts';

const key = (k: string, v: string, last = false) => ({text: `  "${k}": ${v}${last ? '' : ','}`, color: T.text});

export const Call: React.FC = () => (
  <AbsoluteFill>
  <AbsoluteFill style={{alignItems: 'center', paddingTop: 130}}>
    <Rise text="Another agent *asks* for gum. Cardano Card *quotes* it." start={2} size={60} />
  </AbsoluteFill>
  <AbsoluteFill style={{flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 48, paddingTop: 110}}>
    <Pop start={0}>
      <Panel title="buyer agent → cardano card" width={760} badge={<Pill>request</Pill>}>
        <Typed start={8} cps={3} lines={[
          {text: 'POST /start_job', color: T.accent},
          {text: '{', color: T.muted},
          key('ask', '"a single pack of Trident gum"'),
          key('max_total_usd', '20.00'),
          key('zip', '"94704"', true),
          {text: '}', color: T.muted},
        ]} />
      </Panel>
    </Pop>
    <Pop start={70}>
      <Panel title="cardano card → amazon via agentcard" width={760} badge={<Pill color={T.ok}>quote</Pill>}>
        <Typed start={78} cps={3.4} lines={[
          {text: '"status": "prepared"', color: T.ok},
          {text: `"merchant": "Amazon"`},
          {text: `"item": "${FACTS.item}"`},
          {text: `"subtotal": "${FACTS.subtotal}"`},
          {text: `"authorization_ceiling": "${FACTS.ceiling}"`},
          {text: '"payment_source": "vault"'},
        ]} />
        <Pop start={140} style={{marginTop: 18}}>
          <Mono size={22} color={T.ok}>✓ approved by the buyer · escrow requested: {FACTS.escrow}</Mono>
        </Pop>
      </Panel>
    </Pop>
  </AbsoluteFill>
  </AbsoluteFill>
);
