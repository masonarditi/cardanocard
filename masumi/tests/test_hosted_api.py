import httpx

from cardano_card.hosted_api import app
from cardano_card.models import INPUT_SCHEMA


async def test_public_registration_does_not_advertise_or_execute_jobs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Even inherited live configuration must not activate an execution provider.
    monkeypatch.setenv("CARDANO_CARD_MODE", "preprod")
    monkeypatch.setenv("PURCHASE_BACKEND", "staged_module")
    monkeypatch.setenv("ALLOW_EXTERNAL_VAULT_CHECKOUT", "true")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/healthz")).status_code == 200
        availability = (await client.get("/availability")).json()
        assert availability["status"] == "unavailable"
        assert availability["accepting_jobs"] is False
        assert (await client.get("/input_schema")).json() == INPUT_SCHEMA
        for path in ["/start_job", "/provide_input"]:
            response = await client.post(path, content="private checkout data")
            assert response.status_code == 503
            assert "private checkout data" not in response.text
        assert (await client.get("/status", params={"job_id": "anything"})).status_code == 503
        for path in ["/jobs", "/events", "/evidence", "/.env", "/openapi.json", "/docs"]:
            assert (await client.get(path)).status_code == 404
    assert list(tmp_path.iterdir()) == []
