from argparse import Namespace

import httpx
import pytest

from cardano_card.terminal import demo, live, print_status, safe, watch


def job(phase="paid", simulated=True):
    return {"id": "job", "phase": phase, "status": "running", "escrow_state": "Withdrawn",
            "simulated_escrow": simulated, "simulated_purchase": True,
            "events": [{"at": 1, "phase": phase, "message": "Saved transition"}]}


def test_watch_is_read_only_and_does_not_print_private_purchase_fields():
    calls, lines = [], []
    def handle(req):
        calls.append((req.method, req.url.path))
        data = job()
        data["purchase"] = {"approval_url": "https://private.invalid/token", "address": "private address"}
        if req.url.path == "/evidence":
            data = {"order_id": "SIM-order", "total_usd": "7.50", "chain_transactions": []}
        return httpx.Response(200, json=data)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        client.base_url = "http://localhost"
        assert watch(client, "job", emit=lines.append) == "paid"
    assert calls == [("GET", "/status"), ("GET", "/evidence")]
    assert not any("private" in line for line in lines)
    assert any("SIMULATED" in line for line in lines)


def test_demo_refuses_preprod_before_any_write():
    calls = []
    def handle(req):
        calls.append(req.method)
        return httpx.Response(200, json={"simulated_escrow": False, "simulated_purchase": True})
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="requires simulated"):
            demo(client, Namespace())
    assert calls == ["GET"]


def test_simulation_watch_refuses_provider_mode_change():
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=job(simulated=False)))) as client:
        with pytest.raises(ValueError, match="refuses"):
            watch(client, "job", simulate=True, emit=lambda _: None)


def test_unknown_job_stays_pending_and_deduplicates_events():
    lines, calls, now = [], [], [0]
    def handle(req):
        calls.append(req.method)
        return httpx.Response(200, json=job("reconciling"))
    def sleep(seconds):
        now[0] += seconds
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)) as client:
        assert watch(client, "job", timeout=3, emit=lines.append, sleep=sleep, clock=lambda:now[0]) == "pending"
    assert calls == ["GET"] * 3
    assert sum("Saved transition" in line for line in lines) == 1
    assert lines[-1].startswith("PENDING")


def test_failed_simulation_write_is_not_retried():
    calls = []
    def handle(req):
        calls.append(req.method)
        if req.method == "POST":
            raise httpx.ReadTimeout("lost")
        return httpx.Response(200, json=job("awaiting_payment"))
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            watch(client, "job", simulate=True, emit=lambda _: None)
    assert calls == ["GET", "POST"]


def test_terminal_controls_are_removed():
    assert "\x1b" not in safe("hello\x1b[2J\r\nworld")


def test_live_follows_new_jobs_after_completion_and_reconnects_without_duplicates():
    lines, requests, now = [], [], [0]
    def event(sequence, job_id, phase):
        return {"sequence": sequence, "job_id": job_id, "phase": phase, "at": 1,
                "message": "Saved step", "simulated_escrow": True, "simulated_purchase": True}
    def handle(req):
        requests.append(req)
        if len(requests) == 1:
            return httpx.Response(200, json={"events": [event(1, "first-job", "paid")], "cursor": 1, "latest": 1})
        if len(requests) == 2:
            raise httpx.ReadTimeout("private provider detail")
        return httpx.Response(200, json={"events": [event(2, "second-job", "creating_payment"), event(3, "second-job", "awaiting_payment")], "cursor": 3, "latest": 3})
    def sleep(seconds):
        now[0] += seconds
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)) as client:
        assert live(client, timeout=2, emit=lines.append, sleep=sleep, clock=lambda:now[0]) == 3
    assert all(req.method == "GET" and req.url.path == "/events" for req in requests)
    assert requests[2].url.params["after"] == "1"
    assert sum("Saved step" in line for line in lines) == 3
    assert any("RECONNECTED" in line for line in lines)
    assert not any("private" in line for line in lines)


@pytest.mark.parametrize("status", [401, 404, 409])
def test_live_stops_on_auth_route_or_database_mismatch(status):
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(ValueError):
            live(client, emit=lambda _: None)
