"""Offline protocol and isolation checks; never use live credentials or endpoints."""
from io import BytesIO
import json
from urllib.error import HTTPError, URLError

import pytest

from src.data_providers.base import NotConfigured, ProviderError
from src.data_providers.dnse_readonly import (
    API_VERSION, DNSEHTTPError, DNSEReadOnly, NoRedirect, signed_headers,
)


class FixtureOpener:
    def __init__(self, raw=b'{}', error=None):
        self.raw = raw
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return BytesIO(self.raw)


def client(opener):
    return DNSEReadOnly('example-key', 'example-secret', opener=opener)


def test_dnse_signature_matches_fixed_protocol_vector():
    headers = signed_headers(
        'example-key', 'example-secret', '/price/FPT/quotes/latest',
        date_value='Wed, 28 Oct 2026 06:00:00 +0000', nonce='0' * 32,
    )
    assert headers['X-Signature'] == (
        'Signature keyId="example-key",algorithm="hmac-sha256",'
        'headers="(request-target) date",'
        'signature="XC0o8F3i9mvLsVbEW%2FedHTxKnL3iEgHDVe%2Bq3iPhfvc%3D",'
        f'nonce="{"0" * 32}"'
    )
    assert headers['version'] == API_VERSION
    assert headers['x-api-key'] == 'example-key'


@pytest.mark.parametrize('path', [
    '/accounts', '/orders', '/price/FPT/quotes/latest/../orders',
    'https://example.com/price/FPT/quotes/latest', '/price/FPT/quotes/latest?accountId=1',
])
def test_dnse_rejects_non_quote_routes_before_network(path):
    opener = FixtureOpener()
    with pytest.raises(ValueError, match='ROUTE_NOT_ALLOWED'):
        client(opener).get(path)
    assert not opener.calls


def test_dnse_requires_secret_and_rejects_query_outside_allowlist(monkeypatch):
    monkeypatch.delenv('DNSE_API_SECRET', raising=False)
    with pytest.raises(NotConfigured):
        DNSEReadOnly('example-key', opener=FixtureOpener())
    opener = FixtureOpener()
    with pytest.raises(ValueError, match='QUERY_NOT_ALLOWED'):
        client(opener).get('/price/FPT/quotes/latest', {'accountId': 'fixture'})
    assert not opener.calls


def test_dnse_quote_is_get_and_payload_remains_unverified_with_secrets_removed():
    payload = {'symbol': 'FPT', 'bid': [{'price': 54000, 'qtty': 100}],
               'offer': [], 'apiKey': 'example-key', 'accountId': 'fixture',
               'nested': [{'note': 'example-key example-secret', 'accessToken': 'fixture'}]}
    opener = FixtureOpener(json.dumps(payload).encode())
    result = client(opener).get('/price/FPT/quotes/latest', {'boardId': 'G1'})
    request, timeout = opener.calls[0]
    assert request.get_method() == 'GET' and timeout == 30
    assert request.full_url == 'https://openapi.dnse.com.vn/price/FPT/quotes/latest?boardId=G1'
    assert result['units_verified'] is False
    assert result['payload']['bid'] == payload['bid']
    assert result['payload']['nested'] == [{'note': '[REDACTED] [REDACTED]'}]
    assert 'apiKey' not in result['payload'] and 'accountId' not in result['payload']
    assert 'example-secret' not in json.dumps(result)
    assert result['received_at_utc'].endswith('+00:00')


def test_dnse_http_failure_does_not_expose_body_or_headers():
    error = HTTPError('https://openapi.dnse.com.vn', 401, 'example-secret',
                      {'x-api-key': 'example-key'}, BytesIO(b'example-secret'))
    with pytest.raises(DNSEHTTPError) as raised:
        client(FixtureOpener(error=error)).get('/price/FPT/quotes/latest')
    assert str(raised.value) == 'DNSE_HTTP_401'
    assert raised.value.status == 401


@pytest.mark.parametrize('raw', [b'<html>bad gateway</html>', b'\xff'])
def test_dnse_invalid_json_is_not_accepted_as_market_data(raw):
    with pytest.raises(ProviderError, match='DNSE_INVALID_JSON'):
        client(FixtureOpener(raw)).get('/price/FPT/quotes/latest')


def test_dnse_network_failure_is_sanitized_and_redirects_are_disabled():
    with pytest.raises(ProviderError, match='^DNSE_NETWORK_ERROR$'):
        client(FixtureOpener(error=URLError('example-secret'))).get('/price/FPT/quotes/latest')
    assert NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://example.com') is None


def test_dnse_saves_only_numeric_rate_limit_headers():
    class Response(BytesIO):
        headers = {'X-RateLimit-Limit': '100000', 'X-RateLimit-Remaining': '99999',
                   'X-RateLimit-Reset': 'example-secret', 'Authorization': 'example-secret'}
    opener = FixtureOpener()
    opener.open = lambda *args, **kwargs: Response(b'{}')
    observation = client(opener).get('/price/FPT/quotes/latest')
    assert observation['rate_limit_headers'] == {
        'X-RateLimit-Limit': '100000', 'X-RateLimit-Remaining': '99999'}


def test_history_probe_uses_vietnam_window_and_rejects_broad_intervals():
    from scripts.probe_dnse import history_window, observation_summary
    window = history_window('2026-10-28')
    assert window == {'from': 1793161740, 'to': 1793161800}
    with pytest.raises(ValueError, match='five minutes'):
        history_window('2026-10-28', '09:15', '11:30')
    with pytest.raises(ValueError, match='five minutes'):
        history_window('2026-10-28', '11:30', '11:29')
    with pytest.raises(ValueError, match='future'):
        history_window('2099-10-28')
    observation = {'payload': {'quotes': [{'symbol': 'FPT', 'boardId': 'G1',
        'bid': [], 'offer': [], 'time': '2026-10-28 11:29:59.001'}]}}
    assert observation_summary(observation, 'FPT', window)['quote_count'] == 1
    with pytest.raises(ValueError, match='SCOPE_OR_SCHEMA'):
        observation_summary(observation, 'HPG', window)
    observation['payload']['quotes'][0]['time'] = '2026-10-28 14:45:00.001'
    with pytest.raises(ValueError, match='OUTSIDE_REQUEST'):
        observation_summary(observation, 'FPT', window)


def test_history_probe_follows_one_page_token_and_marks_incomplete_history(tmp_path, monkeypatch):
    from scripts import probe_dnse
    calls = []
    class FixtureClient:
        def get(self, path, params):
            calls.append((path, dict(params)))
            return {'received_at_utc': '2026-10-28T14:00:00+00:00', 'payload': {
                'quotes': [{'symbol': 'FPT', 'boardId': 'G1', 'bid': [], 'offer': [],
                    'time': f'2026-10-28 11:29:{59-len(calls):02d}.001'}],
                'nextPageToken': f'fixture-page-{len(calls)}'}}
    monkeypatch.setattr(probe_dnse, 'DNSEReadOnly', FixtureClient)
    monkeypatch.setattr(probe_dnse, 'ROOT', tmp_path)
    report_path = tmp_path/'report.json'
    monkeypatch.setattr('sys.argv', ['probe', '--symbols', 'FPT', '--history-date', '2026-10-28',
                                    '--report', str(report_path)])
    assert probe_dnse.main() == 0
    assert len(calls) == 2 and calls[1][1]['nextPageToken'] == 'fixture-page-1'
    assert calls[0][1]['limit'] == 2 and calls[0][0] == '/price/FPT/quotes'
    report = json.loads(report_path.read_text())
    assert report['units_verified'] is False and report['finance_enabled'] is False
    assert report['samples'][-1]['has_next_page'] is True
    assert 'fixture-page' not in json.dumps(report)


def test_dnse_workflow_is_manual_readonly_and_has_no_market_data_upload():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[1]/'.github/workflows/dnse-preflight.yml'
    raw = path.read_text()
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'secrets.DNSE_API_KEY' in raw and 'secrets.DNSE_API_SECRET' in raw
    assert 'upload-artifact' not in raw and 'secrets.VNSTOCK_API_KEY' not in raw


def test_dnse_secret_setup_requires_both_values_before_network(monkeypatch):
    from scripts import github_setup
    monkeypatch.setenv('DNSE_API_KEY', 'synthetic-dnse-key')
    monkeypatch.delenv('DNSE_API_SECRET', raising=False)
    monkeypatch.setattr(github_setup, 'GitHubAPI', lambda: pytest.fail('network must not start'))
    with pytest.raises(RuntimeError, match='DNSE_CREDENTIALS_MISSING'):
        github_setup.set_secrets(dnse_only=True)


def test_dnse_secret_setup_encrypts_only_approved_pair(monkeypatch):
    import base64
    from nacl.public import PrivateKey, SealedBox
    from scripts import github_setup
    private = PrivateKey.generate()
    captured = {}
    monkeypatch.setenv('DNSE_API_KEY', 'synthetic-dnse-key')
    monkeypatch.setenv('DNSE_API_SECRET', 'synthetic-dnse-secret')
    monkeypatch.setattr(github_setup, 'protected_state_key', lambda: pytest.fail('do not change state key'))
    class FixtureAPI:
        def request(self, method, path, payload=None):
            if method == 'GET':
                return {'key': base64.b64encode(bytes(private.public_key)).decode(), 'key_id': 'fixture'}
            captured[path.rsplit('/', 1)[1]] = SealedBox(private).decrypt(
                base64.b64decode(payload['encrypted_value'])).decode()
    monkeypatch.setattr(github_setup, 'GitHubAPI', FixtureAPI)
    result = github_setup.set_secrets(dnse_only=True)
    assert captured == {'DNSE_API_KEY': 'synthetic-dnse-key', 'DNSE_API_SECRET': 'synthetic-dnse-secret'}
    assert result['values_logged'] is False and 'synthetic-dnse' not in json.dumps(result)
