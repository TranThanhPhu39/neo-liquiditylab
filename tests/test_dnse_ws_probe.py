"""Synthetic WebSocket transcripts exercise scope, authentication, budgets and redaction."""
import asyncio
from copy import deepcopy
import json
import ssl

import pytest

from src.data_providers.dnse_ws_probe import (
    CHANNEL, ProbeError, auth_message, run_probe, source_time, subscription,
)


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def recv(self):
        if not self.frames:
            raise TimeoutError
        value = self.frames.pop(0)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value).encode()

    async def send(self, data):
        self.sent.append(json.loads(data))


def quote(symbol='ACB', **changes):
    return {'T': 'q', 'marketId': 'STO', 'boardId': 'G1', 'symbol': symbol,
            'time': {'Seconds': 1704067200, 'Nanos': 123000000},
            'bid': [{'price': 21.1, 'qtty': 100} for _ in range(3)],
            'offer': [{'price': 21.2, 'qtty': 200} for _ in range(3)], **changes}


def run(frames, symbols=('ACB',), **kwargs):
    socket = Socket(frames)
    def connector(url, **options):
        assert url == 'wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json'
        assert options['proxy'] is None and options['ssl'].check_hostname
        assert options['ssl'].verify_mode == ssl.CERT_REQUIRED
        return socket
    result, observations = asyncio.run(run_probe('fixture-key', 'fixture-secret', symbols, 1,
                                                  connector=connector, **kwargs))
    return socket, result, observations


def test_auth_and_subscription_have_fixed_market_scope():
    message = auth_message('fixture-key', 'fixture-secret', 1234567, '890')
    assert message['action'] == 'auth' and message['timestamp'] == 1234567 and message['nonce'] == '890'
    assert len(message['signature']) == 64 and 'fixture-secret' not in json.dumps(message)
    assert subscription(('ACB', 'MSR')) == {'action': 'subscribe', 'channels': [
        {'name': 'top_price.G1.json', 'symbols': ['ACB', 'MSR']}]}
    for symbols in ((), ('ACB', 'ACB'), ('ACB', 'FPT', 'PVS', 'MSR'), ('orders',), ('ACB/../orders',)):
        with pytest.raises(ProbeError):
            subscription(symbols)


def test_quotes_keep_qtty_clocks_and_unverified_units_without_private_fields():
    frame = quote(api_key='fixture-key', accountId='fixture-account', extra='fixture-secret')
    original = deepcopy(frame)
    socket, result, rows = run([{'action': 'welcome', 'session_id': 'private-session'},
                               {'action': 'auth_success'}, {'action': 'subscribed'}, frame, frame, frame])
    assert result['status'] == 'QUOTES_RECEIVED_ALL_SYMBOLS' and result['authenticated']
    assert result['subscription_acknowledged'] and socket.closed
    assert len(rows) == 3 and rows[0]['payload']['bid'][0]['qtty'] == 100
    assert rows[0]['source_time_utc'] == '2024-01-01T00:00:00.123000+00:00'
    assert rows[0]['unit_verification_status'] == 'UNVERIFIED_WEBSOCKET'
    assert result['samples'][0]['bid_numeric_three_levels'] == 3
    assert result['samples'][0]['bid_quantity_fields'] == ['qtty']
    serialized = json.dumps((result, rows))
    assert all(secret not in serialized for secret in ('fixture-key', 'fixture-secret', 'private-session', 'fixture-account'))
    assert frame == original and [s['action'] for s in socket.sent] == ['auth', 'subscribe']


def test_rejected_auth_does_not_subscribe_or_expose_server_echo():
    socket, result, rows = run([{'sid': 'secret-session'},
                               {'action': 'auth_error', 'message': 'fixture-key fixture-secret'}])
    assert result['error_code'] == 'WS_SERVER_REJECTED_AUTH'
    assert not rows and not result['authenticated'] and socket.closed
    assert [s['action'] for s in socket.sent] == ['auth']
    assert 'fixture-secret' not in json.dumps(result)


@pytest.mark.parametrize('frame', [quote('FPT'), quote(boardId='G2'), {'T': 'a', 'accountId': 'fixture'}])
def test_unrequested_symbol_board_or_private_message_is_rejected(frame):
    _, result, rows = run([{'action': 'welcome'}, {'action': 'auth_success'}, frame])
    assert result['status'] == 'FAILED' and not rows


def test_ping_and_authenticated_idle_are_distinct_from_transport_failure():
    socket, result, rows = run([{'action': 'welcome'}, {'action': 'ping'},
                               {'action': 'auth_success'}, {'action': 'subscribed'}])
    assert result['status'] == 'AUTHENTICATED_NO_QUOTES' and result['authenticated'] and not rows
    assert any(s['action'] == 'pong' for s in socket.sent)
    _, failed, _ = run([{'action': 'welcome'}, OSError('fixture-secret')])
    assert failed['status'] == 'FAILED' and 'fixture-secret' not in json.dumps(failed)


def test_unknown_time_partial_book_and_frame_budget(monkeypatch):
    assert source_time('2026-11-03 14:30:00') is None
    assert source_time('2026-11-03T14:30:00+07:00') == '2026-11-03T07:30:00+00:00'
    assert source_time(True) is None and source_time({'Seconds': 1, 'Nanos': -1}) is None
    _, result, rows = run([{'action': 'welcome'}, {'action': 'auth_success'},
                           quote(bid=None, offer=[], time=None)])
    assert result['samples'][0]['bid_levels_max'] == 0 and rows[0]['source_time_utc'] is None
    monkeypatch.setattr('src.data_providers.dnse_ws_probe.MAX_FRAMES', 3)
    _, result, _ = run([{'action': 'welcome'}, {'action': 'auth_success'},
                        {'action': 'pong'}, {'action': 'pong'}])
    assert result['error_code'] == 'WS_FRAME_BUDGET_EXCEEDED'


def test_invalid_duration_stops_before_network():
    def forbidden(*args, **kwargs):
        raise AssertionError('Network must not be called')
    with pytest.raises(ProbeError, match='DURATION'):
        asyncio.run(run_probe('fixture-key', 'fixture-secret', seconds=91, connector=forbidden))


def test_monitor_accepts_safe_ws_summary_and_rejects_payload_extension():
    from scripts.github_monitor import summarize_log
    _, report, _ = run([{'action': 'welcome'}, {'action': 'auth_success'}, quote()])
    assert summarize_log(json.dumps(report))['worker_summaries'] == [report]
    assert not summarize_log(json.dumps({**report, 'raw_payload': quote()}))['worker_summaries']
