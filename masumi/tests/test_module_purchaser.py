import copy
import sys
import types

import pytest

from cardano_card.engine import Engine
from cardano_card.models import StartRequest
from cardano_card.providers import FakeEscrow, ModulePurchaser
from cardano_card.store import Store
from test_lifecycle import PAYLOAD

SUCCESS = {"status": "success", "order_id": "ord_1", "total_usd": 1.32, "merchant": "Amazon",
           "items": [{"name": "Gum", "qty": 1, "price_usd": 1.32}]}


@pytest.fixture
def mason(tmp_path):
    module = types.ModuleType("fake_mason_purchase")
    module.calls, module.results = [], []
    module.purchase = lambda ask, cap, address, request_id: module.calls.append(request_id) or module.results.pop(0)
    module.inspect_purchase = lambda request_id: module.results.pop(0)
    sys.modules[module.__name__] = module
    store = Store(str(tmp_path / "jobs.db"))
    yield module, Engine(store, FakeEscrow(store), ModulePurchaser(module.__name__))
    store.close()


async def funded(engine):
    job = await engine.start(StartRequest.model_validate(copy.deepcopy(PAYLOAD)))
    await engine.simulate(job["id"], "fund")
    await engine.tick()
    return engine.get(job["id"])


async def test_success_submits_result_once(mason):
    module, engine = mason
    module.results.append(SUCCESS)
    job = await funded(engine)
    assert job["phase"] == "submitting_result"
    assert job["outcome"]["total_usd"] == "1.32" and module.calls == [job["id"]]


@pytest.mark.parametrize("reason,mapped", [("sandbox_mode", "declined"), ("needs_input", "no_cart"),
                                           ("approval_required", "declined"), ("over_budget", "over_budget")])
async def test_failures_refund(mason, reason, mapped):
    module, engine = mason
    module.results.append({"status": "failed", "reason": reason, "conversation_id": "c1"})
    job = await funded(engine)
    assert job["outcome"] == {"status": "failed", "reason": mapped}
    await engine.simulate(job["id"], "request_refund")
    await engine.tick()
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "refunded"


async def test_unknown_inspects_without_repurchasing(mason):
    module, engine = mason
    module.results += [{"status": "pending", "reason": "unknown", "conversation_id": "c1", "detail": "in progress"}, SUCCESS]
    job = await funded(engine)
    assert job["phase"] == "reconciling"
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "submitting_result" and len(module.calls) == 1
