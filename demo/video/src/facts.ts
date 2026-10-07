// Real values from Mason's live run on Cardano Preprod (6 Oct 2026). Nothing here is mocked.
export const FACTS = {
  agentId: '7e8bdaf2b2b919a3a4b94002cafb50086c0c845fe535d07a77ab7f778526aa4d9605b0499f001a3990fd8905ed575bd53507329c8ebd08912bccdedd',
  registrationTx: 'a17f14560d3d7e64eaf09e9810744978cb0b3d83f4424bae2d78f0b509f483cf',
  lockTx: '7fd13a227f65232b03870a34b83f023b463fe699a28e48d772c7683bff225370',
  lockBlock: '5,260,604',
  lockTime: '20:37:59',
  orderTime: '20:39:18',
  resultTx: 'b30b139b045568c8847a2f43f8d45f7a4f3ee3edbf1e4bb9933c00a846d82bed',
  resultBlock: '5,260,609',
  resultTime: '20:39:36',
  resultHash: '49dafabe68804d46de21607152956708c680d6fadd08745b64eecff76e00feee',
  orderId: '9a193914-c97a-45d7-b997-90ae81507fb9',
  item: 'Trident Original Sugar Free Gum, 14 pc',
  subtotal: '$1.32',
  ceiling: '$12.06',
  escrow: '10 tADA',
  seconds: 97,
};

export const short = (hash: string, head = 8, tail = 6) => `${hash.slice(0, head)}…${hash.slice(-tail)}`;
