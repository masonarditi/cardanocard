"""Secret-free diagnostics. Never reads/refreshes a user token or purchases anything."""
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE = 'https://api.agentcard.sh'


def check(directory, *, authenticate=False, client=None):
    directory = Path(directory)
    values = dotenv_values(directory / '.env', interpolate=False)
    names = ('AGENTCARD_CLIENT_ID', 'AGENTCARD_CLIENT_SECRET')
    missing = [name for name in names if not values.get(name)]
    report = {
        'status': 'missing_credentials' if missing else 'configuration_present',
        'missing_credentials': missing,
        'linked_user_file_present': (directory / '.agentcard_tokens.json').is_file(),
        'refresh_needs_reconciliation': (directory / '.agentcard_refresh_pending').exists(),
        'organization_authenticated': False,
        'sandbox_verified': False,
        'user_access': 'not_checked',
        'checkout': 'not_tested',
        'issuing': 'not_tested',
    }
    if missing or not authenticate:
        return report
    if client is None:
        with httpx.Client(timeout=30, follow_redirects=False) as owned:
            return _authenticate(report, values, owned)
    return _authenticate(report, values, client)


def _authenticate(report, values, client):
    try:
        response = client.post(BASE + '/api/v2/oauth/token', data={
            'grant_type': 'client_credentials',
            'client_id': values['AGENTCARD_CLIENT_ID'],
            'client_secret': values['AGENTCARD_CLIENT_SECRET']})
        report['oauth_http_status'] = response.status_code
        response.raise_for_status()
        token = response.json().get('access_token')
        if not isinstance(token, str) or not token:
            report['status'] = 'invalid_auth_response'
            return report
        report['organization_authenticated'] = True
        response = client.get(BASE + '/api/v2', headers={'Authorization': 'Bearer ' + token})
        report['introspection_http_status'] = response.status_code
        response.raise_for_status()
        verified = response.json().get('test_mode') is True
        report['sandbox_verified'] = verified
        report['status'] = 'organization_sandbox_verified' if verified else 'sandbox_not_verified'
    except Exception:
        # Provider payloads and exception strings can contain tokens or account data.
        report['status'] = 'authentication_check_failed'
    return report
