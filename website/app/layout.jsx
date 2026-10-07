import './globals.css';

export const metadata = {
  title: 'Cardano Card — Your next purchase, one text away.',
  description: 'A shopping agent you can text. A conversational checkout experience powered by Masumi escrow and AgentCard.',
};
export const viewport = { themeColor: '#260d1c' };

export default function RootLayout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}
