"""Offline transcripts cover durable receipts, resume, scope limits and encryption isolation."""
import asyncio
import base64
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.collect_dnse_realtime import session_plan
from scripts.github_checkpoint import backup_sqlite, pack_state, unpack_state, ReleaseStore
from src.data_pipeline.dnse_realtime import collect, Limits, validate_catalog
from src.data_pipeline.dnse_realtime_store import RealtimeStore, audit
from src.data_providers.dnse_ws_probe import quote_observation
from src.models.orderbook import LOCAL_TZ


def catalog(symbols=('ACB', 'MSR')):
    return {'provider': 'dnse', 'as_of_date': '2026-11-03', 'count': len(symbols),
            'rows': [{'symbol': s, 'market_id': 'UPX' if s == 'MSR' else 'STO',
                      'exchange': 'UPCOM' if s == 'MSR' else 'HOSE'} for s in symbols]}


def frame(symbol='ACB'):
    return {'T': 'q', 'symbol': symbol, 'marketId': 'UPX' if symbol == 'MSR' else 'STO', 'boardId': 'G1',
            'bid': [{'price': '21.100', 'qtty': 10}], 'offer': None,
            'time': {'Seconds': 1793682000, 'Nanos': 1000000}}


def observation(symbol='ACB'):
    return quote_observation(frame(symbol), ('ACB','MSR'), '2026-11-03T02:00:01+00:00', (), 'a'*64)


class Socket:
    def __init__(self, values, sent):
        self.values = list(values)
        self.sent = sent
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def recv(self):
        if not self.values:
            await asyncio.sleep(.01)
            raise TimeoutError
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)

    async def send(self, data):
        self.sent.append(json.loads(data))


def test_receipts_survive_dedup_restart_and_preserve_exact_unscaled_text(tmp_path):
    path = tmp_path/'quotes.db'
    db = RealtimeStore(path, catalog(), '2026-11-03', 'morning')
    run_id = db.begin_run('collect', 1)
    connection = db.connection(run_id, 0, 2)
    db.transition(connection, 'authenticated_at')
    db.transition(connection, 'subscribed_at')
    db.append([(connection, observation()), (connection, observation())])
    assert db.status(run_id)['unique_events'] == 1 and db.status(run_id)['receipt_count'] == 2
    db.close()
    db = RealtimeStore(path, catalog(), '2026-11-03', 'morning')
    next_id = db.begin_run('collect', 1)
    assert db.db.execute('SELECT state FROM runs WHERE id=?', (run_id,)).fetchone()[0] == 'INTERRUPTED_CHECKPOINT'
    assert db.db.execute('SELECT error_code FROM connections WHERE id=?', (connection,)).fetchone()[0] == 'RESTORED_FROM_LIVE_CHECKPOINT'
    assert next_id != run_id
    db.finish(next_id, 'WINDOW_ENDED_OBSERVED_ONLY')
    db.close()
    assert audit(path)['receipt_count'] == 2
    import zlib
    with closing(sqlite3.connect(path)) as raw:
        payload = json.loads(zlib.decompress(raw.execute('SELECT payload_zlib FROM events').fetchone()[0]))
    assert payload['bid'][0]['price'] == '21.100' and payload['bid'][0]['qtty'] == 10
    assert payload['offer'] is None


def test_catalog_expiry_scope_change_and_connection_budget(tmp_path):
    assert validate_catalog(catalog(), datetime(2026,11,10).date()) == 7
    with pytest.raises(ValueError, match='EXPIRED'):
        validate_catalog(catalog(), datetime(2026,11,11).date())
    with pytest.raises(ValueError, match='LIMITS'):
        Limits().validate(1711)
    db = RealtimeStore(tmp_path/'q.db', catalog(), '2026-11-03', 'morning')
    db.close()
    with pytest.raises(ValueError, match='SETTINGS_CHANGED'):
        RealtimeStore(tmp_path/'q.db', catalog(('ACB',)), '2026-11-03', 'morning')


def test_schema_neutral_backup_and_encrypted_roundtrip(tmp_path):
    db = RealtimeStore(tmp_path/'q.db', catalog(), '2026-11-03', 'smoke')
    run_id = db.begin_run('smoke', 1)
    con = db.connection(run_id, 0, 2)
    db.append([(con, observation())])
    db.close()
    backup_sqlite(tmp_path/'q.db', tmp_path/'copy.db')
    with closing(sqlite3.connect(tmp_path/'copy.db')) as raw:
        assert raw.execute("SELECT count(*) FROM sqlite_master WHERE name='orderbook_snapshots'").fetchone()[0] == 0
    encrypted = tmp_path/'encrypted'
    pack_state({'snapshot.db':tmp_path/'copy.db'}, encrypted, b'x'*32, 'repo:dnse-ws-state-tag')
    unpack_state(encrypted, tmp_path/'restore', b'x'*32, 'repo:dnse-ws-state-tag')
    assert audit(tmp_path/'restore/snapshot.db')['unique_events'] == 1
    with pytest.raises(FileExistsError):
        backup_sqlite(tmp_path/'q.db', tmp_path/'copy.db')


def test_collector_writes_quotes_pongs_and_all_shard_acks(tmp_path):
    sockets, messages = [], []
    def connector(url, **options):
        assert options['ssl'].check_hostname and options['proxy'] is None
        sock = Socket([{'action':'welcome'}, {'action':'auth_success'}, {'action':'subscribed'},
                       {'action':'ping'}, frame(), frame(), frame('MSR')], messages)
        sockets.append(sock)
        return sock
    r = asyncio.run(collect(tmp_path/'q.db', catalog(), '2026-11-03', 'smoke', 'fixture-key','fixture-secret', 1,
                            limits=Limits(minimum_free_bytes=0, connect_spacing=0), connector=connector))
    assert r['state'] == 'SMOKE_FINISHED' and r['shards_subscribed'] == 1
    assert r['unique_events'] == 2 and r['receipt_count'] == 3 and r['duplicate_receipts'] == 1
    assert r['unpersisted_receipts_this_run'] == 0 and all(s.closed for s in sockets)
    assert [m['action'] for m in messages] == ['auth','subscribe','pong']
    assert messages[1]['channels'] == [{'name':'top_price.G1.json','symbols':['ACB','MSR']}]
    assert 'fixture-secret' not in json.dumps(r) and not r['finance_enabled']


def test_rejection_is_fatal_and_never_retries_private_or_limit_errors(tmp_path):
    calls=[]
    def connector(*args, **kwargs):
        calls.append(1)
        return Socket([{'action':'welcome'}, {'action':'auth_error','message':'fixture-secret'}], [])
    r = asyncio.run(collect(tmp_path/'q.db', catalog(), '2026-11-03', 'smoke','fixture-key','fixture-secret',1,
                            limits=Limits(minimum_free_bytes=0, connect_spacing=0), connector=connector))
    assert r['state'] == 'FAILED_RESUMABLE' and len(calls) == 1 and r['receipt_count'] == 0
    assert 'fixture-secret' not in json.dumps(r)


def test_stop_file_drains_queue_and_checkpoints_real_database(tmp_path):
    stop = tmp_path/'STOP'
    saved=[]
    def connector(*args, **kwargs):
        return Socket([{'action':'welcome'}, {'action':'auth_success'}, {'action':'subscribed'}, frame()], [])
    def status(value):
        if value['state']=='RUNNING': stop.write_text('stop')
    def checkpoint(path, value):
        saved.append(audit(path)['receipt_count'])
    r = asyncio.run(collect(tmp_path/'q.db', catalog(), '2026-11-03', 'smoke','fixture-key','fixture-secret',1,
                            limits=Limits(minimum_free_bytes=0, connect_spacing=0), connector=connector,
                            stop_file=stop, status_hook=status, checkpoint=checkpoint))
    assert r['state'] == 'STOPPED_BY_USER' and saved and r['audit']['sqlite_integrity'] == 'ok'


def test_session_windows_skip_early_late_and_weekends():
    assert not session_plan(datetime(2026,11,3,8,0,tzinfo=LOCAL_TZ),'morning')['ready']
    assert session_plan(datetime(2026,11,3,8,52,tzinfo=LOCAL_TZ),'morning')['start_delay_seconds'] == 300
    assert not session_plan(datetime(2026,11,3,11,32,tzinfo=LOCAL_TZ),'morning')['ready']
    assert session_plan(datetime(2026,11,3,12,52,tzinfo=LOCAL_TZ),'afternoon')['ready']
    assert not session_plan(datetime(2026,11,7,9,0,tzinfo=LOCAL_TZ),'morning')['ready']


def test_realtime_workflow_is_gated_and_never_uploads_clear_data():
    import yaml
    path = Path(__file__).resolve().parents[1]/'.github/workflows/dnse-realtime.yml'
    text = path.read_text(encoding='utf-8-sig')
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'schedule','workflow_dispatch'}
    assert 'DNSE_REALTIME_ENABLED' in workflow['jobs']['collector']['if']
    assert 'DNSE_REALTIME_STORAGE_APPROVED' in workflow['jobs']['collector']['if']
    assert workflow['concurrency']['cancel-in-progress'] == 'false'
    assert 'upload-artifact' not in text and 'LIQUIDITYLAB_STATE_KEY' in text


def test_transient_disconnect_resubscribes_and_retains_gap_and_receipts(tmp_path):
    sockets=[]
    def connector(*args, **kwargs):
        frames=[{'action':'welcome'},{'action':'auth_success'},{'action':'subscribed'},frame()]
        frames += [OSError('fixture-secret')] if not sockets else [frame('MSR')]
        sock=Socket(frames,[])
        sockets.append(sock)
        return sock
    r=asyncio.run(collect(tmp_path/'q.db',catalog(),'2026-11-03','smoke','fixture-key','fixture-secret',3,
                          limits=Limits(minimum_free_bytes=0,connect_spacing=0,batch_size=1),connector=connector))
    assert r['state']=='SMOKE_FINISHED' and len(sockets)==2 and r['connection_attempts']==2
    assert r['receipt_count']==3 and r['unique_events']==2 and r['known_gaps']==2
    with closing(sqlite3.connect(tmp_path/'q.db')) as db:
        assert db.execute('SELECT count(*) FROM gaps WHERE finished_at IS NULL').fetchone()[0]==0
    assert all(s.closed for s in sockets)


def test_atomic_receipt_batch_rolls_back_on_scope_fk_error(tmp_path):
    db=RealtimeStore(tmp_path/'q.db',catalog(),'2026-11-03','smoke')
    run_id=db.begin_run('smoke',1); con=db.connection(run_id,0,2)
    broken=observation(); broken['payload']['symbol']='UNKNOWN'
    with pytest.raises(sqlite3.IntegrityError):
        db.append([(con,observation()),(con,broken)])
    assert db.status(run_id)['receipt_count']==db.status(run_id)['unique_events']==0
    db.close()


def test_stop_before_start_makes_no_socket_or_database(tmp_path):
    stop=tmp_path/'STOP'; stop.write_text('stop')
    def forbidden(*args,**kwargs): raise AssertionError('Must not connect')
    r=asyncio.run(collect(tmp_path/'q.db',catalog(),'2026-11-03','smoke','fixture-key','fixture-secret',1,
                          limits=Limits(minimum_free_bytes=0),connector=forbidden,stop_file=stop))
    assert r['state']=='STOPPED_BY_USER' and not (tmp_path/'q.db').exists()


def test_checkpoint_failure_preserves_data_and_reports_no_false_success(tmp_path):
    def connector(*args,**kwargs):
        return Socket([{'action':'welcome'},{'action':'auth_success'},{'action':'subscribed'},frame()],[])
    def bad_checkpoint(*args): raise RuntimeError('fixture-secret')
    r=asyncio.run(collect(tmp_path/'q.db',catalog(),'2026-11-03','smoke','fixture-key','fixture-secret',1,
                          limits=Limits(minimum_free_bytes=0,connect_spacing=0),connector=connector,checkpoint=bad_checkpoint))
    assert r['state']=='FAILED_RESUMABLE' and r['fatal_error']=='WS_FINAL_CHECKPOINT_FAILED'
    assert audit(tmp_path/'q.db')['receipt_count']==1 and 'fixture-secret' not in json.dumps(r)


def test_fraction_tokens_keep_source_precision_in_archive(tmp_path):
    payload=json.dumps(frame()).replace('"21.100"','21.100')
    def connector(*args,**kwargs):
        return Socket([{'action':'welcome'},{'action':'auth_success'},{'action':'subscribed'},payload],[])
    r=asyncio.run(collect(tmp_path/'q.db',catalog(),'2026-11-03','smoke','fixture-key','fixture-secret',1,
                          limits=Limits(minimum_free_bytes=0,connect_spacing=0),connector=connector))
    import zlib
    with closing(sqlite3.connect(tmp_path/'q.db')) as db:
        saved=json.loads(zlib.decompress(db.execute('SELECT payload_zlib FROM events').fetchone()[0]))
    assert saved['bid'][0]['price']=='21.100' and r['receipt_count']==1


def test_worker_requires_explicit_storage_approval(monkeypatch):
    from scripts.github_dnse_realtime import require_environment
    monkeypatch.setenv('GITHUB_REPOSITORY','TranThanhPhu39/neo-liquiditylab')
    monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    for name in ('DNSE_API_KEY','DNSE_API_SECRET','LIQUIDITYLAB_STATE_KEY'):
        monkeypatch.setenv(name,'fixture')
    monkeypatch.delenv('DNSE_REALTIME_STORAGE_APPROVED',raising=False)
    with pytest.raises(ValueError,match='STORAGE_NOT_APPROVED'): require_environment()
    monkeypatch.setenv('DNSE_REALTIME_STORAGE_APPROVED','true')
    require_environment()


def test_catalog_publish_guard_precedes_any_data_or_network_operation(monkeypatch):
    from scripts.prepare_dnse_realtime import prepare
    monkeypatch.delenv('DNSE_REALTIME_STORAGE_APPROVED', raising=False)
    with pytest.raises(ValueError, match='STORAGE_NOT_APPROVED'):
        prepare(publish=True)


def test_audit_detects_payload_symbol_mismatch(tmp_path):
    db=RealtimeStore(tmp_path/'q.db',catalog(),'2026-11-03','smoke')
    run_id=db.begin_run('smoke',1); con=db.connection(run_id,0,2)
    db.append([(con,observation())])
    with db.db:
        db.db.execute("UPDATE events SET symbol='MSR'")
    db.close()
    with pytest.raises(ValueError,match='PAYLOAD_SCOPE_FAILED'): audit(tmp_path/'q.db')


def test_monitor_accepts_realtime_counters_and_rejects_raw_payload():
    from scripts.github_monitor import summarize_log
    value={'purpose':'REALTIME_G1_COLLECTOR','state':'SMOKE_FINISHED','shards_subscribed':8,
           'audit':{'state':'VERIFIED','receipt_count':0}}
    assert summarize_log(json.dumps(value))['worker_summaries']==[value]
    assert not summarize_log(json.dumps({**value,'raw_payload':frame()}))['worker_summaries']
    assert not summarize_log(json.dumps({**value,'audit':{'api_key':'fixture'}}))['worker_summaries']
