"""Staged SSI FastConnect V3 market-data access; never trading or account APIs.

Contract checked against developers.ssi.com.vn on 2026-11-10.
This client returns raw securities metadata, NOT normalized order books.
"""
from datetime import datetime, timezone
import json
import math
import os
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener

from src.data_providers.base import NotConfigured, ProviderError

BASE_URL = 'https://api.ssi.com.vn'
TOKEN_PATH = '/api/v3/auth/token'
SECURITIES_PATH = '/api/v3/data/securitiesByBoard'
MAX_RESPONSE = 4 * 1024 * 1024


def reject_constant(value):
    raise ValueError('Non-finite JSON number')


class SSIHTTPError(ProviderError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'SSI_HTTP_{status}')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def error_code(error):
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'SSI_TLS_VERIFICATION_FAILED'
    if isinstance(reason, socket.gaierror):
        return 'SSI_DNS_ERROR'
    if isinstance(reason, TimeoutError):
        return 'SSI_TIMEOUT'
    if isinstance(reason, ssl.SSLError):
        return 'SSI_TLS_ERROR'
    if isinstance(reason, ConnectionError):
        return 'SSI_CONNECTION_ERROR'
    return 'SSI_NETWORK_ERROR'


class SSIReadOnly:
    def __init__(self, api_key=None, api_secret=None, *, opener=None, clock=time.time):
        self._key = api_key if api_key is not None else os.getenv('SSI_API_KEY')
        self._secret = api_secret if api_secret is not None else os.getenv('SSI_API_SECRET')
        if not all(isinstance(v, str) and v.strip() and len(v) <= 4096 and '\r' not in v and '\n' not in v
                   for v in (self._key, self._secret)):
            raise NotConfigured('SSI_REQUIRED_CREDENTIALS_MISSING_OR_INVALID')
        if opener is None:
            context = ssl.create_default_context()
            if os.name == 'nt':
                try:
                    import truststore
                    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                except ImportError:
                    pass
            opener = build_opener(HTTPSHandler(context=context), NoRedirect())
        self._opener = opener
        self._clock = clock
        self._token = None
        self._expires_at = 0
        self.rate_limit_headers = {}

    def _request(self, method, path, *, params=None, body=None):
        if (method, path) not in (('POST', TOKEN_PATH), ('GET', SECURITIES_PATH)):
            raise ValueError('SSI_ROUTE_NOT_ALLOWED')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'User-Agent': 'LiquidityLab-SSI-ReadOnly/1'}
        if method == 'GET':
            if not self._token:
                raise ProviderError('SSI_ACCESS_TOKEN_REQUIRED')
            headers['Authorization'] = 'Bearer ' + self._token
        url = BASE_URL + path + ('?' + urlencode(params) if params else '')
        request = Request(url, method=method, headers=headers,
                          data=json.dumps(body).encode('utf-8') if body is not None else None)
        try:
            with self._opener.open(request, timeout=15) as response:
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise ProviderError('SSI_RESPONSE_TOO_LARGE')
                self.rate_limit_headers = {
                    name: response.headers.get(name) for name in
                    ('X-RATELIMIT-LIMIT', 'X-RATELIMIT-REMAINING', 'X-RATELIMIT-RESET')
                    if re.fullmatch(r'[0-9]{1,20}', str(response.headers.get(name, '')))}
        except HTTPError as error:
            raise SSIHTTPError(error.code) from None
        except (OSError, URLError) as error:
            raise ProviderError(error_code(error)) from None
        try:
            return json.loads(data, parse_constant=reject_constant)
        except (ValueError, UnicodeError):
            raise ProviderError('SSI_INVALID_JSON') from None

    def authenticate(self):
        self._token = None
        self._expires_at = 0
        result = self._request('POST', TOKEN_PATH, body={'apiKey': self._key, 'apiSecret': self._secret})
        if not isinstance(result, dict):
            raise ProviderError('SSI_AUTH_RESPONSE_INVALID')
        token, expires = result.get('accessToken'), result.get('expiresAt')
        if (not isinstance(token, str) or not token or len(token) > 32768 or
                any(c.isspace() for c in token) or result.get('tokenType', 'Bearer') != 'Bearer' or
                type(expires) not in (int, float) or not math.isfinite(expires)):
            raise ProviderError('SSI_AUTH_RESPONSE_INVALID')
        # Official SSI examples show both epoch seconds and milliseconds.
        expires = expires / 1000 if expires > 100_000_000_000 else expires
        if not self._clock() + 30 < expires <= 253402300799:
            raise ProviderError('SSI_TOKEN_EXPIRED_OR_TOO_SHORT')
        self._token, self._expires_at = token, expires
        # Tokens remain in memory; callers/loggers get no token material.
        return {'provider': 'ssi', 'api_version': 'v3', 'authenticated': True,
                'expires_at_utc': datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                'trading_requested': False}

    def securities(self, *, symbol=None, board=None, index=None):
        params = {k: v for k, v in dict(symbol=symbol, board=board, index=index).items() if v is not None}
        if len(params) != 1:
            raise ValueError('SSI_EXACTLY_ONE_SECURITY_FILTER_REQUIRED')
        name, value = next(iter(params.items()))
        if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9_]{1,20}', value):
            raise ValueError('SSI_SECURITY_FILTER_INVALID')
        if name == 'board' and value not in ('HOSE', 'HNX', 'UPCOM'):
            raise ValueError('SSI_BOARD_NOT_ALLOWED')
        if not self._token or self._expires_at <= self._clock() + 30:
            self.authenticate()
        try:
            result = self._request('GET', SECURITIES_PATH, params=params)
        except SSIHTTPError as error:
            if error.status == 401:
                self._token, self._expires_at = None, 0
            raise
        if not isinstance(result, list) or len(result) > 30000 or any(
            not isinstance(row, dict) or not isinstance(row.get('symbol'), str) or
            not re.fullmatch(r'[A-Z0-9]{1,12}', row['symbol']) or
            row.get('board') not in ('HOSE', 'HNX', 'UPCOM') or
            (symbol is not None and row['symbol'] != symbol) or
            (board is not None and row['board'] != board) for row in result):
            raise ProviderError('SSI_SECURITIES_RESPONSE_INVALID')
        if len({row['symbol'] for row in result}) != len(result):
            raise ProviderError('SSI_DUPLICATE_SECURITY')
        return result
