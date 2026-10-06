// Offline verification only: synthetic inputs, no signing, submission or networking.
const assert = require('node:assert/strict');
const req = require('node:module').createRequire('/usr/src/app/package.json');
const { MeshTxBuilder, resolveScriptHash } = req('@meshsdk/core');
const original = req('@meshsdk/core-cst');
const patched = req('/usr/src/app/node_modules/@meshsdk/core-cst/dist/index.preprod-test.cjs');
const { Serialization } = req('@cardano-sdk/core');
const { blake2b } = req('ethereum-cryptography/blake2b');
let raw = '';
process.stdin.on('data', d => raw += d);
process.stdin.on('end', () => {
  try {
    const params = JSON.parse(raw);
    const address = 'addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj';
    const script = { code: '4e4d01000033222220051200120011', version: 'V3' };
    const policy = resolveScriptHash(script.code, script.version);
    function build(serializer, network) {
      const b = new MeshTxBuilder({serializer});
      return b.txIn('a'.repeat(64), 0, [{unit:'lovelace',quantity:'20000000'}], address)
        .txInCollateral('b'.repeat(64), 0, [{unit:'lovelace',quantity:'5000000'}], address)
        .mintPlutusScript('V3').mint('1', policy, '01').mintingScript(script.code)
        .mintRedeemerValue({alternative:0,fields:[]}, 'Mesh', {mem:7000000,steps:3000000000})
        .txOut(address, [{unit:'lovelace',quantity:'5000000'},{unit:policy+'01',quantity:'1'}])
        .changeAddress(address).setNetwork(network).completeSync();
    }
    const old = Serialization.Transaction.fromCbor(build(new original.CardanoSDKSerializer(), 'preprod'));
    const next = Serialization.Transaction.fromCbor(build(new patched.CardanoSDKSerializer(), 'preprod'));
    const costs = new Serialization.Costmdls();
    costs.insert(Serialization.CostModel.newPlutusV3(params.cost_models_raw.PlutusV3));
    const witness = next.witnessSet();
    const datums = witness.plutusData();
    const hashInput = Buffer.concat([
      Buffer.from(witness.redeemers().toCbor(), 'hex'),
      ...(datums && datums.size() ? [Buffer.from(datums.toCbor(),'hex')] : []),
      Buffer.from(costs.languageViewsEncoding(),'hex')
    ]);
    const expected = Buffer.from(blake2b(hashInput, 32)).toString('hex');
    assert.equal(next.body().scriptDataHash(), expected);
    assert.notEqual(old.body().scriptDataHash(), expected);
    for (const network of ['mainnet','preview','testnet']) {
      assert.equal(build(new patched.CardanoSDKSerializer(),network),build(new original.CardanoSDKSerializer(),network));
    }
    console.log(JSON.stringify({offline_hash_matches_current_preprod:true,old_hash_mismatches:true,other_networks_unchanged:true,protocol_major_ver:params.protocol_major_ver}));
  } catch (e) { console.error('Offline patch verification failed: '+e.message); process.exitCode=2; }
});
