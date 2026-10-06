import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cost_patch', Path(__file__).parents[1] / 'scripts/patch_preprod_cost_models.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_source(prefix=''):
    return '  coreSerializeTxBody = (txBuilderBody) => {\n' + ''.join(
        f'let costModelV{i} = S.CostModel.newPlutusV{i}(\n  {prefix}DEFAULT_V{i}_COST_MODEL_LIST\n);\n' for i in (1,2,3))


@pytest.mark.parametrize('prefix',['','import_common10.'])
def test_patch_is_preprod_scoped_and_keeps_other_network_defaults(prefix):
    params={'cost_models_raw':{f'PlutusV{i}':[i]*350 for i in (1,2,3)}}
    result=module.patch(fixture_source(prefix),params)
    assert result.count('this.cardanoCardNetwork === "preprod"') == 3
    for i in (1,2,3):
        assert f': {prefix}DEFAULT_V{i}_COST_MODEL_LIST)' in result
    assert 'this.cardanoCardNetwork = txBuilderBody.network' in result
    with pytest.raises(ValueError,match='Already patched'):
        module.patch(result,params)


def test_patch_refuses_changed_library_and_invalid_parameters():
    params={'cost_models_raw':{f'PlutusV{i}':[i]*350 for i in (1,2,3)}}
    with pytest.raises(ValueError,match='Unexpected serializer'):
        module.patch('different source',params)
    params['cost_models_raw']['PlutusV3'][0]=True
    with pytest.raises(ValueError,match='Invalid raw'):
        module.patch(fixture_source(),params)
