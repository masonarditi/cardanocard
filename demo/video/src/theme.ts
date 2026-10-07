import {loadFont as loadSans} from '@remotion/google-fonts/Inter';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';

const sans = loadSans('normal', {weights: ['300', '400', '500', '600', '700'], subsets: ['latin']});
const mono = loadMono('normal', {weights: ['400', '500'], subsets: ['latin']});

// Masumi brand (2503 brand guidelines, masumi.network): Neutral 100 background, black type, Electric Pink accent, Inter.
// Cardano brand (cardano.org/brand-assets): Blue #0033AD. One meaning per color:
// pink = masumi (agents, escrow), blue = cardano (on-chain, the card), green = success.
export const T = {
  sans: sans.fontFamily,
  mono: mono.fontFamily,
  bg: '#F5F5F5',
  panel: '#FFFFFF',
  line: '#E3E3E3',
  grid: 'rgba(0,0,0,0.045)',
  glow: 'rgba(250,0,140,0.06)',
  text: '#0A0A0A',
  muted: '#6E6E6E',
  accent: '#FA008C',
  cardano: '#0033AD',
  ok: '#0E8F63',
  ink: '#111111',
};
