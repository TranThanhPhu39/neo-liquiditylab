from contextlib import closing
import hashlib
import json
import sqlite3
import zlib

import pytest

from test_dnse_realtime import catalog, frame, observation
from src.data_pipeline.dnse_realtime_store import RealtimeStore
from src.data_pipeline.dnse_ws_normalization import normalize_event, timestamp
from scripts.normalize_dnse_realtime import normalize, audit_normalized
from src.data_pipeline.dnse_normalization import snapshot_from_record


def raw(tmp_path, payload=None):
    path=tmp_path/'raw.db'
    store=RealtimeStore(path,catalog(),'2026-11-03','morning')
    run=store.begin_run('collect',1)
    conn=store.connection(run,0,2)
    obs=observation()
    if payload is not None:
        obs['payload']=payload
    store.append([(conn,obs),(conn,obs)])
    store.finish(run,'WINDOW_ENDED_OBSERVED_ONLY')
    store.close()
    return path


def test_structured_numbers_are_exact_and_never_use_rest_scale():
    f=frame()
    f['bid']=[{'price':'21.100','qtty':'10.25'}]
    r=normalize_event(f,'2026-11-03T02:00:01+00:00','ACB','STO','2026-11-03')
    assert r['bid_levels'][0]['source_price']=='21.100'
    assert r['bid_levels'][0]['source_quantity']=='10.25'
    assert r['ask_source_state']=='NULL_OR_MISSING'
    assert r['ask_levels']==[] and r['bid_vol_1'] is None and r['price_unit'] is None
    assert not r['financial_eligible'] and r['unit_verification_status']=='UNVERIFIED_WEBSOCKET'
    with pytest.raises(ValueError,match='CANONICAL'):
        snapshot_from_record(r)


def test_exact_nanoseconds_and_ambiguous_clock_are_preserved():
    iso,ns=timestamp({'Seconds':1793682000,'Nanos':999999999})
    assert ns=='1793682000999999999' and iso.endswith('00.999999+00:00')
    assert timestamp('2026-11-03 09:00:00')==(None,None)
    assert timestamp({'Seconds':1793682000,'Nanos':-1})==(None,None)
    assert timestamp({'Seconds':True,'Nanos':0})==(None,None)


def test_all_ten_levels_kept_and_empty_is_distinct_from_explicit_zero():
    f=frame()
    f['bid']=[{'price':'21.1','qtty':0} for _ in range(10)]
    f['offer']=[]
    r=normalize_event(f,'2026-11-03T02:00:01+00:00','ACB','STO','2026-11-03')
    assert len(r['bid_levels'])==10 and r['bid_levels'][0]['source_quantity']=='0'
    assert r['ask_source_state']=='EMPTY_ARRAY' and r['ask_vol_1'] is None
    f.pop('offer')
    assert normalize_event(f,'2026-11-03T02:00:01+00:00','ACB','STO','2026-11-03')['ask_source_state']=='NULL_OR_MISSING'


@pytest.mark.parametrize('bad',[True,-1,'NaN','Infinity'])
def test_invalid_numbers_quarantine_without_discarding_receipts(tmp_path,bad):
    f=frame();f['bid'][0]['qtty']=bad
    path=raw(tmp_path,f)
    out=tmp_path/'normalized.db'
    r=normalize(path,out)
    assert r['audit']['quarantined_events']==1 and r['audit']['receipt_count']==2
    with closing(sqlite3.connect(out)) as db:
        assert db.execute('SELECT error_code FROM events').fetchone()[0]=='WS_INVALID_SOURCE_NUMBER'


def test_incremental_lineage_idempotence_and_raw_immutability(tmp_path):
    path=raw(tmp_path);out=tmp_path/'normalized.db'
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    one=normalize(path,out);two=normalize(path,out)
    assert one['new_events']==1 and two['new_events']==0
    assert two['audit']['normalized_events']==1 and two['audit']['receipt_count']==2
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    with pytest.raises(ValueError,match='OVERWRITE_RAW'):
        normalize(path,path)
    with closing(sqlite3.connect(out)) as db:
        value=json.loads(zlib.decompress(db.execute('SELECT payload_zlib FROM events').fetchone()[0]))
        assert value['bid_levels'][0]['source_price']=='21.100'
        assert db.execute('SELECT count(*) FROM connections').fetchone()[0]==1


def test_alias_conflicts_and_source_scope_never_silently_change():
    f=frame();f['bid'][0]['quantity']=20
    with pytest.raises(ValueError,match='ALIAS_CONFLICT'):
        normalize_event(f,'2026-11-03T02:00:01+00:00','ACB','STO','2026-11-03')
    with pytest.raises(ValueError,match='SCOPE'):
        normalize_event(frame(),'2026-11-03T02:00:01+00:00','MSR','UPX','2026-11-03')


def test_output_tampering_is_detected(tmp_path):
    path=raw(tmp_path);out=tmp_path/'normalized.db'
    normalize(path,out)
    with closing(sqlite3.connect(out)) as db:
        db.execute("UPDATE events SET output_sha256='bad'");db.commit()
    with pytest.raises(ValueError,match='HASH_FAILED'):
        audit_normalized(out)


def test_size_guard_rolls_back_derived_rows_and_preserves_raw(tmp_path):
    path=raw(tmp_path);out=tmp_path/'normalized.db'
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='SIZE_LIMIT'):
        normalize(path,out,max_bytes=1)
    with closing(sqlite3.connect(out)) as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0]==0
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest


def test_monitor_accepts_only_sanitized_normalization_summary():
    from scripts.github_monitor import summarize_log
    good={'purpose':'REALTIME_G1_COLLECTOR','state':'SMOKE_FINISHED',
          'normalization':{'state':'SAVED_ENCRYPTED','normalized_events':1,'financial_eligible':False}}
    assert summarize_log(json.dumps(good))['worker_summaries']==[good]
    bad={**good,'normalization':{**good['normalization'],'payload':{'secret':'bad'}}}
    assert not summarize_log(json.dumps(bad))['worker_summaries']


def test_existing_unrelated_database_is_never_modified(tmp_path):
    path=raw(tmp_path);out=tmp_path/'other.db'
    with closing(sqlite3.connect(out)) as db:
        db.execute('CREATE TABLE unrelated(value TEXT)');db.execute("INSERT INTO unrelated VALUES('keep')");db.commit()
    before=hashlib.sha256(out.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='TARGET_NOT_RECOGNIZED'):
        normalize(path,out)
    assert hashlib.sha256(out.read_bytes()).hexdigest()==before
