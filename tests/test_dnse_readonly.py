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
