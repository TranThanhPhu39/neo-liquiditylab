"""Concurrent, bounded G1 raw collection. Quotes remain unscaled and are not reconstructed."""
import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import ssl
import time

from src.data_pipeline.dnse_realtime_store import RealtimeStore, audit, utcnow
from src.data_providers.dnse_ws_probe import URL, CHANNEL, ProbeError, auth_message, default_connector, quote_observation

MARKETS = {'STO': 'HOSE', 'STX': 'HNX', 'UPX': 'UPCOM'}


def validate_catalog(catalog, today=None, max_age=7):
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    rows = catalog.get('rows', [])
    age = (today-date.fromisoformat(catalog['as_of_date'])).days
    if catalog.get('provider') != 'dnse' or not 0 <= age <= max_age or not rows or len(rows) != catalog.get('count'):
        raise ValueError('WS_CATALOG_MISSING_OR_EXPIRED')
    seen = set()
    for row in rows:
        if (not re.fullmatch('[A-Z0-9]{1,12}', row.get('symbol', '')) or row['symbol'] in seen
                or row.get('market_id') not in MARKETS or row.get('exchange') != MARKETS[row['market_id']]):
            raise ValueError('WS_CATALOG_SCOPE_INVALID')
        seen.add(row['symbol'])
    return age


@dataclass(frozen=True)
class Limits:
    symbols_per_connection: int = 190
    max_connections: int = 9
    reconnect_attempts: int = 5
    queue_size: int = 5000
    batch_size: int = 250
    minimum_free_bytes: int = 5 * 1024**3
    maximum_database_bytes: int = 1536 * 1024**2
    maximum_receipts: int = 5_000_000
    maximum_wire_bytes: int = 8 * 1024**3
    checkpoint_seconds: int = 900
    connect_spacing: float = 1.0

    def validate(self, count):
        if (not 1 <= self.symbols_per_connection <= 190 or not 1 <= self.max_connections <= 9
                or math.ceil(count/self.symbols_per_connection) > self.max_connections
                or not 0 <= self.reconnect_attempts <= 8 or self.queue_size < self.batch_size
                or self.batch_size < 1 or self.checkpoint_seconds < 5 or self.connect_spacing < 0):
            raise ValueError('WS_LIMITS_INVALID_OR_UNIVERSE_TOO_LARGE')


def server_rejection(frame, stage):
    # Do not persist arbitrary server text, which could echo authentication material.
    text = str(frame.get('code', '')) + ' ' + str(frame.get('message', ''))
    for code in ('MAX_CHANNELS_EXCEEDED', 'MAX_CONNECTIONS_EXCEEDED', 'RATE_LIMIT_EXCEEDED', 'INVALID_SIGNATURE'):
        if code in text:
            return 'WS_' + code
    return 'WS_SERVER_REJECTED_' + stage


async def _collect(store, catalog, day, partition, key, secret, seconds, *, mode,
                   limits, connector, checkpoint, status_hook, stop_file):
    path = store.path
    rows = sorted(catalog['rows'], key=lambda r: r['symbol'])
    shards = [rows[i:i+limits.symbols_per_connection] for i in range(0, len(rows), limits.symbols_per_connection)]
    run_id = store.begin_run(mode, len(shards))
    queue = asyncio.Queue(limits.queue_size)
    stop = asyncio.Event()
    deadline = time.monotonic() + seconds
    connect_lock = asyncio.Lock()
    next_connect = [0.0]
    metrics = {'wire_frames': 0, 'wire_bytes': 0, 'quotes_queued': 0, 'checkpoints_saved': 0,
               'checkpoint_failures': 0, 'fatal_error': None}

    def status(state='RUNNING'):
        return {'provider': 'dnse', 'purpose': 'REALTIME_G1_COLLECTOR', 'state': state,
                'date_local': day, 'partition': partition, 'mode': mode, 'run_id': run_id,
                'catalog_as_of_date': catalog['as_of_date'], 'catalog_age_days': (date.fromisoformat(day)-date.fromisoformat(catalog['as_of_date'])).days,
                'updated_at_utc': utcnow(), **metrics, **store.status(run_id)}

    async def emit(state='RUNNING'):
        result = status(state)
        if status_hook:
            status_hook(result)
        return result

    def fail(code):
        metrics['fatal_error'] = metrics['fatal_error'] or code
        stop.set()

    async def read(ws, timeout):
        raw = await asyncio.wait_for(ws.recv(), timeout)
        encoded = raw.encode() if isinstance(raw, str) else raw
        metrics['wire_frames'] += 1
        metrics['wire_bytes'] += len(encoded)
        if metrics['wire_bytes'] > limits.maximum_wire_bytes:
            raise ProbeError('WS_WIRE_BUDGET_EXCEEDED')
        def invalid_constant(_):
            raise ProbeError('WS_NONFINITE_JSON')
        frame = json.loads(encoded, parse_float=str, parse_constant=invalid_constant)
        if not isinstance(frame, dict):
            raise ProbeError('WS_FRAME_NOT_OBJECT')
        action = frame.get('action', frame.get('a'))
        if action == 'ping':
            await ws.send(b'{"action":"pong"}')
        if action in ('error', 'auth_error'):
            raise ProbeError(server_rejection(frame, 'AUTH_OR_SUBSCRIBE'))
        if frame.get('T') in ('do', 'eo', 'dp', 'ep', 'a'):
            raise ProbeError('WS_UNREQUESTED_PRIVATE_MESSAGE')
        return frame, action, hashlib.sha256(encoded).hexdigest()

    async def accept(frame, digest, connection_id, selected):
        if frame.get('T') != 'q':
            return
        observation = quote_observation(frame, selected, utcnow(), (key, secret), digest)
        if frame['marketId'] != selected[frame['symbol']]['market_id']:
            raise ProbeError('WS_CATALOG_MARKET_MISMATCH')
        observation['capture_method'] = 'websocket_realtime'
        if metrics['quotes_queued'] >= limits.maximum_receipts:
            raise ProbeError('WS_RECEIPT_BUDGET_EXCEEDED')
        try:
            await asyncio.wait_for(queue.put((connection_id, observation)), 5)
        except TimeoutError:
            raise ProbeError('WS_WRITER_BACKPRESSURE') from None
        metrics['quotes_queued'] += 1

    async def worker(shard_id, group):
        selected = {r['symbol']: r for r in group}
        failures = 0
        gap_id = store.gap(run_id, shard_id, utcnow(), None, 'STARTUP_NOT_OBSERVED')
        while not stop.is_set() and time.monotonic() < deadline:
            connection_id = store.connection(run_id, shard_id, len(group))
            reason = 'WINDOW_END'
            disconnected = False
            try:
                async with connect_lock:
                    await asyncio.sleep(max(0, next_connect[0]-time.monotonic()))
                    next_connect[0] = time.monotonic()+limits.connect_spacing
                async with (connector or default_connector)(URL, ssl=ssl.create_default_context(), proxy=None,
                        open_timeout=15, close_timeout=5, ping_interval=20, ping_timeout=20,
                        max_size=256*1024, max_queue=64) as ws:
                    async def wait_action(expected, phase, seconds_limit=15):
                        phase_end = min(deadline, time.monotonic()+seconds_limit)
                        while not stop.is_set() and time.monotonic() < phase_end:
                            frame, action, digest = await read(ws, max(.01, phase_end-time.monotonic()))
                            if phase == 'SUBSCRIBE':
                                await accept(frame, digest, connection_id, selected)
                            if action == expected or (phase == 'WELCOME' and ('session_id' in frame or 'sid' in frame)):
                                return
                        raise ProbeError('WS_' + phase + '_TIMEOUT')
                    await wait_action('welcome', 'WELCOME')
                    await ws.send(json.dumps(auth_message(key, secret)).encode())
                    await wait_action('auth_success', 'AUTH')
                    store.transition(connection_id, 'authenticated_at')
                    await ws.send(json.dumps({'action': 'subscribe', 'channels': [{'name': CHANNEL, 'symbols': list(selected)}]}).encode())
                    await wait_action('subscribed', 'SUBSCRIBE')
                    store.transition(connection_id, 'subscribed_at')
                    store.finish_gap(gap_id)
                    gap_id = None
                    last_frame = time.monotonic()
                    next_ping = last_frame+25
                    while not stop.is_set() and time.monotonic() < deadline:
                        now = time.monotonic()
                        if now >= next_ping:
                            await ws.send(b'{"action":"ping"}')
                            next_ping = now+25
                        try:
                            frame, _, digest = await read(ws, max(.01, min(25, deadline-now, next_ping-now)))
                            last_frame = time.monotonic()
                            await accept(frame, digest, connection_id, selected)
                        except TimeoutError:
                            if time.monotonic()-last_frame > 75:
                                raise ProbeError('WS_HEARTBEAT_TIMEOUT')
            except asyncio.CancelledError:
                reason = 'STOP_OR_DEADLINE'
                raise
            except Exception as error:
                reason = str(error) if isinstance(error, ProbeError) else 'WS_CONNECTION_INTERRUPTED'
                failures += 1
                if gap_id is None:
                    gap_id = store.gap(run_id, shard_id, utcnow(), None, reason)
                store.disconnect(connection_id, reason)
                disconnected = True
                permanent = isinstance(error, (ProbeError, ValueError)) and reason != 'WS_HEARTBEAT_TIMEOUT'
                if permanent or failures > limits.reconnect_attempts:
                    fail(reason if permanent else 'WS_RECONNECT_LIMIT_REACHED')
                else:
                    await asyncio.sleep(min(30, 2**failures))
            finally:
                if not disconnected:
                    store.disconnect(connection_id, reason)

    async def writer():
        batch = []
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), .5)
                except TimeoutError:
                    item = 'flush'
                if item is None:
                    if batch:
                        store.append(batch)
                    return
                if item != 'flush':
                    batch.append(item)
                if len(batch) >= limits.batch_size or item == 'flush':
                    if batch:
                        store.append(batch)
                        batch.clear()
        except Exception:
            fail('WS_WRITER_FAILED')
            raise

    async def checkpoint_loop():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), limits.checkpoint_seconds)
                return
            except TimeoutError:
                await emit()
                try:
                    if checkpoint:
                        await asyncio.to_thread(checkpoint, Path(path), status())
                        metrics['checkpoints_saved'] += 1
                except Exception:
                    metrics['checkpoint_failures'] += 1
                    if metrics['checkpoint_failures'] >= 2:
                        fail('WS_CHECKPOINT_REPEATED_FAILURE')

    workers = [asyncio.create_task(worker(i, group)) for i, group in enumerate(shards)]
    writing = asyncio.create_task(writer())
    saving = asyncio.create_task(checkpoint_loop())
    last_status = 0
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            if writing.done():
                fail('WS_WRITER_FAILED')
                break
            if any(task.done() and not task.cancelled() and task.exception() for task in workers):
                fail('WS_WORKER_FAILED')
            if shutil.disk_usage(Path(path).parent).free < limits.minimum_free_bytes:
                fail('WS_LOW_DISK')
            if sum(p.stat().st_size for p in (Path(path), Path(str(path)+'-wal')) if p.exists()) > limits.maximum_database_bytes:
                fail('WS_DATABASE_SIZE_LIMIT')
            if stop_file and Path(stop_file).exists():
                metrics['stop_requested'] = True
                stop.set()
            if time.monotonic()-last_status > 15:
                await emit()
                last_status = time.monotonic()
            await asyncio.sleep(.25)
    finally:
        stop.set()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not writing.done():
            try:
                await asyncio.wait_for(queue.put(None), 2)
            except TimeoutError:
                fail('WS_WRITER_DRAIN_TIMEOUT')
                writing.cancel()
        await asyncio.gather(writing, return_exceptions=True)
        await saving
    if not metrics.get('stop_requested') and store.status(run_id)['shards_subscribed'] < len(shards):
        metrics['fatal_error'] = metrics['fatal_error'] or 'WS_SUBSCRIPTION_INCOMPLETE'
    state = 'FAILED_RESUMABLE' if metrics['fatal_error'] else 'STOPPED_BY_USER' if metrics.get('stop_requested') else 'SMOKE_FINISHED' if mode == 'smoke' else 'WINDOW_ENDED_OBSERVED_ONLY'
    store.finish(run_id, state)
    result = await emit(state)
    result['unpersisted_receipts_this_run'] = max(0, metrics['quotes_queued']-store.db.execute(
        'SELECT count(*) FROM receipts r JOIN connections c ON c.id=r.connection_id WHERE c.run_id=?', (run_id,)).fetchone()[0])
    result['audit'] = await asyncio.to_thread(audit, path)
    if checkpoint:
        try:
            await asyncio.to_thread(checkpoint, Path(path), result)
            metrics['checkpoints_saved'] += 1
            result['checkpoints_saved'] = metrics['checkpoints_saved']
        except Exception:
            result.update(state='FAILED_RESUMABLE', fatal_error='WS_FINAL_CHECKPOINT_FAILED')
            store.finish(run_id, 'FAILED_RESUMABLE')
    if status_hook:
        status_hook(result)
    return result


async def collect(path, catalog, day, partition, key, secret, seconds, *, mode='smoke',
                  limits=None, connector=None, checkpoint=None, status_hook=None, stop_file=None):
    limits = limits or Limits()
    validate_catalog(catalog, date.fromisoformat(day))
    limits.validate(len(catalog['rows']))
    if isinstance(seconds, bool) or not 1 <= seconds <= 4*3600 or mode not in ('smoke', 'collect'):
        raise ValueError('WS_DURATION_OR_MODE_INVALID')
    if stop_file and Path(stop_file).exists():
        result = {'provider':'dnse','purpose':'REALTIME_G1_COLLECTOR','state':'STOPPED_BY_USER',
                  'date_local':day,'partition':partition,'mode':mode,'started_network':False}
        if status_hook:
            status_hook(result)
        return result
    auth_message(key, secret)
    with closing(RealtimeStore(path, catalog, day, partition)) as store:
        return await _collect(store, catalog, day, partition, key, secret, seconds, mode=mode,
                              limits=limits, connector=connector, checkpoint=checkpoint,
                              status_hook=status_hook, stop_file=stop_file)
