"""Bounded DNSE market-quotes probe. No account channels, orders, or normalization."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import ssl
import time
import uuid

from src.data_providers.dnse_readonly import redact_payload

URL = 'wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json'
CHANNEL = 'top_price.G1.json'
MAX_FRAMES = 1000
MAX_BYTES = 8 * 1024 * 1024
QUOTE_FIELDS = {'T', 'marketId', 'boardId', 'symbol', 'isin', 'bid', 'offer',
                'totalOfferQtty', 'totalBidQtty', 'time'}


class ProbeError(Exception):
    pass


def auth_message(key, secret, timestamp=None, nonce=None):
    if not key or not secret or any(c in key + secret for c in '\r\n'):
        raise ProbeError('WS_CREDENTIALS_MISSING_OR_INVALID')
    timestamp = int(time.time()) if timestamp is None else timestamp
    nonce = str(time.time_ns() // 1000) if nonce is None else nonce
    message = ':'.join((key, str(timestamp), nonce)).encode()
    return {'action': 'auth', 'api_key': key, 'timestamp': timestamp, 'nonce': nonce,
            'signature': hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()}


def subscription(symbols):
    if not 1 <= len(symbols) <= 3 or len(set(symbols)) != len(symbols) or any(
            not re.fullmatch('[A-Z0-9]{1,12}', s) for s in symbols):
        raise ProbeError('WS_INVALID_SYMBOL_SCOPE')
    return {'action': 'subscribe', 'channels': [{'name': CHANNEL, 'symbols': list(symbols)}]}


def source_time(value):
    """Preserve ambiguous strings; Unix/protobuf clocks are UTC, never host-local."""
    try:
        if isinstance(value, dict):
            seconds = Decimal(str(value.get('Seconds', value.get('seconds'))))
            nanos = Decimal(str(value.get('Nanos', value.get('nanos', 0))))
            if not 0 <= nanos < 1_000_000_000:
                return None
            at = datetime.fromtimestamp(float(seconds + nanos / 1_000_000_000), timezone.utc)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = Decimal(str(value))
            at = datetime.fromtimestamp(float(seconds / 1000 if seconds > 1e12 else seconds), timezone.utc)
        elif isinstance(value, str):
            at = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if at.tzinfo is None:
                return None
        else:
            return None
        return at.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError, InvalidOperation):
        return None


def numeric(value, integral=False):
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = Decimal(str(value))
        return number.is_finite() and number >= 0 and (not integral or number == number.to_integral_value())
    except InvalidOperation:
        return False


def quote_observation(frame, symbols, received_at, credentials, frame_hash):
    if frame.get('symbol') not in symbols or frame.get('boardId') != 'G1' or frame.get('marketId') not in ('STO', 'STX', 'UPX'):
        raise ProbeError('WS_QUOTE_SCOPE_MISMATCH')
    payload = {k: frame[k] for k in QUOTE_FIELDS if k in frame}
    for side in ('bid', 'offer'):
        levels = payload.get(side)
        if levels is not None and (not isinstance(levels, list) or len(levels) > 100):
            raise ProbeError('WS_QUOTE_LEVEL_SCHEMA_INVALID')
        if levels is not None:
            if any(not isinstance(level, dict) for level in levels):
                raise ProbeError('WS_QUOTE_LEVEL_SCHEMA_INVALID')
            payload[side] = [{k: level[k] for k in ('price', 'qtty', 'quantity') if k in level} for level in levels]
    payload = redact_payload(payload, credentials)
    return {'provider': 'dnse', 'capture_method': 'websocket_bounded_probe',
            'received_at_utc': received_at, 'source_time_utc': source_time(frame.get('time')),
            'unit_verification_status': 'UNVERIFIED_WEBSOCKET', 'original_frame_sha256': frame_hash,
            'payload': payload}


def summarize(observations, symbols):
    result = []
    for symbol in symbols:
        rows = [r for r in observations if r['payload']['symbol'] == symbol]
        clocks = [r['source_time_utc'] for r in rows if r['source_time_utc']]
        item = {'symbol': symbol, 'quotes': len(rows), 'source_times_parsed': len(clocks),
                'source_time_min_utc': min(clocks) if clocks else None,
                'source_time_max_utc': max(clocks) if clocks else None}
        for side in ('bid', 'offer'):
            levels = [r['payload'].get(side) or [] for r in rows]
            item[side + '_levels_min'] = min(map(len, levels)) if levels else None
            item[side + '_levels_max'] = max(map(len, levels)) if levels else None
            item[side + '_numeric_three_levels'] = sum(len(book) >= 3 and all(
                numeric(level.get('price')) and numeric(level.get('qtty', level.get('quantity')), True)
                for level in book[:3]) for book in levels)
            item[side + '_quantity_fields'] = sorted({key for book in levels for level in book
                                                    for key in ('qtty', 'quantity') if key in level})
        result.append(item)
    return result


def default_connector(*args, **kwargs):
    from websockets.asyncio.client import connect
    return connect(*args, **kwargs)


async def run_probe(key, secret, symbols=('ACB', 'PVS', 'MSR'), seconds=60, *, connector=None):
    # Validate scope and all budgets before credentials or network I/O.
    request = subscription(symbols)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 90:
        raise ProbeError('WS_INVALID_DURATION')
    auth_message(key, secret)
    report = {'provider': 'dnse', 'purpose': 'READ_ONLY_WEBSOCKET_DIAGNOSTIC',
              'probe_id': uuid.uuid4().hex, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'STARTING', 'stage': 'UPGRADE', 'transport_upgraded': False,
              'authenticated': False, 'subscription_acknowledged': False,
              'symbols_requested': list(symbols), 'channel': CHANNEL, 'observe_seconds_limit': seconds,
              'units_verified': False, 'finance_enabled': False, 'frames_received': 0, 'bytes_received': 0}
    observations = []
    counts = Counter()
    started = time.monotonic()

    async def receive(ws, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        raw = await asyncio.wait_for(ws.recv(), remaining)
        encoded = raw.encode('utf-8') if isinstance(raw, str) else raw
        report['frames_received'] += 1
        report['bytes_received'] += len(encoded)
        if report['frames_received'] > MAX_FRAMES or report['bytes_received'] > MAX_BYTES:
            raise ProbeError('WS_FRAME_BUDGET_EXCEEDED')
        frame = json.loads(encoded)
        if not isinstance(frame, dict):
            raise ProbeError('WS_FRAME_NOT_OBJECT')
        action = frame.get('action', frame.get('a'))
        if action == 'ping':
            await ws.send(json.dumps({'action': 'pong'}).encode())
        if action in ('auth_error', 'error'):
            raise ProbeError('WS_SERVER_REJECTED_' + report['stage'])
        return frame, action, hashlib.sha256(encoded).hexdigest()

    try:
        async with asyncio.timeout(seconds + 65):
            async with (connector or default_connector)(URL, ssl=ssl.create_default_context(),
                    proxy=None, open_timeout=15, close_timeout=5, ping_interval=20,
                    ping_timeout=20, max_size=256 * 1024, max_queue=8) as ws:
                report['transport_upgraded'] = True
                report['stage'] = 'WELCOME'
                deadline = time.monotonic() + 15
                while True:
                    frame, action, _ = await receive(ws, deadline)
                    if action in ('welcome', 'connected') or 'session_id' in frame or 'sid' in frame:
                        break
                report['stage'] = 'AUTH'
                await ws.send(json.dumps(auth_message(key, secret)).encode())
                deadline = time.monotonic() + 15
                while True:
                    _, action, _ = await receive(ws, deadline)
                    if action == 'auth_success':
                        report['authenticated'] = True
                        break
                report['stage'] = 'QUOTES'
                await ws.send(json.dumps(request).encode())
                deadline = time.monotonic() + seconds
                while True:
                    try:
                        frame, action, digest = await receive(ws, deadline)
                    except TimeoutError:
                        report['stop_reason'] = 'OBSERVATION_WINDOW_ENDED'
                        break
                    if action == 'subscribed':
                        report['subscription_acknowledged'] = True
                    if frame.get('T') == 'q':
                        observation = quote_observation(frame, symbols, datetime.now(timezone.utc).isoformat(), (key, secret), digest)
                        symbol = observation['payload']['symbol']
                        if counts[symbol] < 10:
                            observations.append(observation)
                            counts[symbol] += 1
                        if all(counts[s] >= 3 for s in symbols):
                            report['stop_reason'] = 'THREE_QUOTES_PER_SYMBOL'
                            break
                    elif frame.get('T') in ('do', 'eo', 'dp', 'ep', 'a'):
                        raise ProbeError('WS_UNREQUESTED_PRIVATE_MESSAGE')
                report['status'] = ('QUOTES_RECEIVED_ALL_SYMBOLS' if all(counts[s] for s in symbols)
                                    else 'QUOTES_RECEIVED_PARTIAL_SCOPE' if observations else 'AUTHENTICATED_NO_QUOTES')
    except Exception as error:
        report['status'] = 'FAILED'
        report['error_code'] = str(error) if isinstance(error, ProbeError) else 'WS_' + report['stage'] + '_' + (
            'TIMEOUT' if isinstance(error, TimeoutError) else 'TRANSPORT_OR_FRAME_ERROR')
        # Never include exception text, close reasons, auth replies, session IDs, or headers.
    report['samples'] = summarize(observations, symbols)
    report['quotes_retained'] = len(observations)
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report, observations
