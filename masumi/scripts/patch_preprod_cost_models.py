"""Local Masumi 0.22.0 / Mesh beta.65 Preprod compatibility repair.

Generate patched ESM/CJS serializers from docker-copied originals. Does not install,
restart, sign, submit, or retry. Caller verifies the current public protocol snapshot.
Only transactions explicitly marked preprod use snapshot models; all others retain
upstream defaults. Rebuild/recreate containers loses this patch; revalidate on epoch
protocol changes. Keep backups and record SHA256 in the generated manifest.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

MARKER = 'CARDANO_CARD_PREPROD_COST_MODELS'


def patch(source, parameters):
    if MARKER in source:
        raise ValueError('Already patched; start from the preserved original')
    models = parameters['cost_models_raw']
    for language in ('PlutusV1', 'PlutusV2', 'PlutusV3'):
        values = models.get(language)
        if not isinstance(values, list) or len(values) < 160 or any(type(n) is not int for n in values):
            raise ValueError('Invalid raw cost-model vector')
    point = '  coreSerializeTxBody = (txBuilderBody) => {\n'
    if source.count(point) != 1:
        raise ValueError('Unexpected serializer implementation')
    result = source.replace(point, point + '    this.cardanoCardNetwork = txBuilderBody.network;\n', 1)
    for version in (1, 2, 3):
        pattern = rf'(let costModelV{version} = [\w.]+\.CostModel\.newPlutusV{version}\(\s*)([\w.]*DEFAULT_V{version}_COST_MODEL_LIST)(\s*\);)'
        result, count = re.subn(pattern, lambda m: m[1] + '(this.cardanoCardNetwork === "preprod" ? '
                               + MARKER + f'.PlutusV{version} : ' + m[2] + ')' + m[3], result)
        if count != 1:
            raise ValueError('Unexpected cost-model implementation')
    return 'const ' + MARKER + ' = ' + json.dumps(models, separators=(',', ':')) + ';\n' + result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', required=True)
    parser.add_argument('--original-directory', required=True)
    parser.add_argument('--output-directory', required=True)
    args = parser.parse_args()
    parameters = json.loads(Path(args.parameters).read_text())
    output = Path(args.output_directory)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'scope': 'preprod only', 'epoch': parameters['epoch'],
                'protocol_major_ver': parameters['protocol_major_ver'], 'files': {}}
    for name in ('index.js', 'index.cjs'):
        source = (Path(args.original_directory) / name).read_text()
        result = patch(source, parameters)
        (output / name).write_text(result)
        manifest['files'][name] = {'original_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                  'patched_sha256': hashlib.sha256(result.encode()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
