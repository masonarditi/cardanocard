import json

import httpx
import pytest

from cardano_card.agentcard_health import check


def test_missing_credentials_never_calls_provider(tmp_path):
    class NoCalls:
        def post(self, *a, **kw):
            pytest.fail('No provider calls without credentials')
    report = check(tmp_path, authenticate=True, client=NoCalls())
    assert report['status'] == 'missing_credentials'
    assert report['user_access'] == 'not_checked'


@pytest.mark.parametrize('mode,expected', [(True, 'organization_sandbox_verified'),
    (False, 'sandbox_not_verified'), (None, 'sandbox_not_verified'), ('true', 'sandbox_not_verified')])
def test_org_check_requires_boolean_sandbox_and_never_uses_user_token(tmp_path, mode, expected):
    (tmp_path / '.env').write_text('AGENTCARD_CLIENT_ID=fixture-id\nAGENTCARD_CLIENT_SECRET=fixture-secret\n')
    (tmp_path / '.agentcard_tokens.json').write_text('invalid file deliberately never read')
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/api/v2/oauth/token':
            return httpx.Response(200, json={'access_token': 'fixture-token'})
        assert request.url.path == '/api/v2'
        return httpx.Response(200, json={'test_mode': mode})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert check(tmp_path, client=client)['status'] == 'configuration_present'
        assert calls == []
        report = check(tmp_path, authenticate=True, client=client)
    assert report['status'] == expected
    assert len(calls) == 2
    assert all(secret not in json.dumps(report) for secret in ('fixture-secret', 'fixture-token'))
    assert report['checkout'] == report['issuing'] == 'not_tested'


def test_provider_error_is_redacted(tmp_path):
    (tmp_path / '.env').write_text('AGENTCARD_CLIENT_ID=id\nAGENTCARD_CLIENT_SECRET=secret\n')
    def handler(request):
        return httpx.Response(401, json={'error': 'sensitive provider details'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = check(tmp_path, authenticate=True, client=client)
    assert report['status'] == 'authentication_check_failed'
    assert report['oauth_http_status'] == 401
    assert 'sensitive' not in json.dumps(report)
