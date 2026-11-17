from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import http.client
import json
from pathlib import Path
import sqlite3
import threading
import zlib
import shutil
import subprocess

import pytest

from src.monitoring.spread import SpreadConfig, SpreadDetector, events_at, features
from scripts.build_orderbook_monitor import build
from scripts.serve_orderbook_monitor import MonitorServer, ReviewStore


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for web replay checks')
def test_monitor_browser_model_and_interactions():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([shutil.which('node'),'--test',str(root/'tests/js/event-replay.test.cjs')],
                          cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def record(n, ask='10002', **changes):
    base = dict(provider='dnse', symbol='ACB', exchange='HOSE', board_id='G1', local_date='2026-11-11',
                source_timestamp=(datetime(2026,11,11,2,15,tzinfo=timezone.utc)+timedelta(seconds=n)).isoformat(),
                collected_at_utc='2026-11-11T10:00:00+00:00', event_id='dnse:'+str(n).zfill(64),
                unit_contract_hash='rule', unit_verification_status='EMPIRICALLY_VERIFIED',
                price_unit='VND', volume_unit='shares', data_quality_status='VALID', market_session='DNSE_CONTINUOUS_AM')
    for i in (1,2,3):
        base.update({f'bid_price_{i}':str(10001-i), f'bid_vol_{i}':i*100,
                     f'ask_price_{i}':str(Decimal(ask)+i-1), f'ask_vol_{i}':i*200})
    return {**base, **changes}


def config():
    return SpreadConfig(baseline_seconds=60, min_samples=3, min_span_seconds=2,
                        min_coverage=Decimal('0'), multiplier=Decimal('2'), min_increase_bps=Decimal('1'),
                        min_duration_seconds=2, min_observations=3, max_observation_gap_seconds=5,
                        end_duration_seconds=1, cooldown_seconds=2)


def episode():
    return [record(i) for i in range(4)] + [record(i,'10010') for i in range(4,7)] + [record(i) for i in (7,8)]


def run(rows):
    d=SpreadDetector('fixture-only',config()); states=[d.push(r,i) for i,r in enumerate(rows)]
    return d,states


def test_hand_calculated_features_missing_zero_and_bad_units():
    f=features(record(0))
    assert f['bid_depth']==600 and f['ask_depth']==1200 and f['imbalance']==Decimal(-1)/3
    assert f['spread_bps']==Decimal(2)/Decimal(10001)*10000
    assert features(record(0,bid_vol_2=None))['bid_depth'] is None
    assert features(record(0,bid_vol_2=0))['bid_depth']==400
    assert features(record(0,bid_price_1=None))['spread_bps'] is None
    assert features(record(0,ask_price_1='9999'))['spread_bps'] is None
    assert not features(record(0,unit_verification_status='ASSUMED_SHARED_SCHEMA'))['eligible']
    assert not features(record(0,market_session='DNSE_OUTSIDE_CONTINUOUS'))['eligible']


def test_episode_persistence_recovery_and_no_future_leak():
    rows=episode();d,states=run(rows)
    assert states[4]['detector_state']=='CANDIDATE'
    assert events_at(d.patches,5)==[]
    at_detection=events_at(d.patches,6)
    assert len(at_detection)==1 and at_detection[0]['state']=='ACTIVE'
    assert at_detection[0]['started_at']==rows[4]['source_timestamp']
    assert at_detection[0]['ended_at'] is None
    assert at_detection[0]['baseline']['last_source_time']<rows[4]['source_timestamp']
    assert events_at(d.patches,7)[0]['state']=='RECOVERING'
    assert len(events_at(d.patches,8))==1 and events_at(d.patches,8)[0]['state']=='ENDED'
    # A full run projected to a prefix must equal actually processing that prefix.
    prefix,_=run(rows[:7]);assert prefix.patches==[p for p in d.patches if p['at_index']<=6]


@pytest.mark.parametrize('change', [dict(unit_verification_status='ASSUMED_SHARED_SCHEMA'),
                                  dict(market_session='DNSE_CONTINUOUS_PM'), dict(ask_price_1=None)])
def test_quality_or_session_interrupts_episode(change):
    d,_=run(episode()[:7]);d.push(record(7,**change),7)
    assert events_at(d.patches,7)[0]['state']=='INTERRUPTED'
    assert d.candidate is None and d.active is None


def test_observation_gap_is_not_mislabelled_as_liquidity_event():
    d,_=run(episode()[:7]);state=d.push(record(30),7)
    assert events_at(d.patches,7)[0]['end_reason']=='OBSERVATION_GAP'
    assert state['detector_state']=='WARMING_UP' and state['quality_reason']=='OBSERVATION_GAP'


def test_same_books_at_new_times_are_evidence_not_silently_deduplicated():
    d,_=run(episode()[:7])
    e=events_at(d.patches,6)[0]
    assert e['observations_at_detection']==3 and e['start_index']==4 and e['detected_index']==6


def test_out_of_order_and_naive_clock_fail_or_inhibit():
    d,_=run(episode()[:7]);s=d.push(record(1),7)
    assert s['quality_reason']=='OUT_OF_ORDER_SOURCE_TIME' and s['detector_state']=='INHIBITED'
    with pytest.raises(ValueError,match='TIMEZONE'):
        d.push(record(8,source_timestamp='2026-11-11T09:16:00'),8)


@pytest.mark.parametrize('changes',[dict(min_samples=True),dict(multiplier=Decimal('NaN')),
                                   dict(baseline_seconds=0),dict(release_fraction=Decimal('1'))])
def test_configuration_rejects_ambiguous_or_invalid_values(changes):
    with pytest.raises(ValueError):SpreadConfig(**changes)


def prepare_archive(root):
    p=root/'data/processed/dnse';p.mkdir(parents=True)
    manifest=dict(state='NORMALIZED_AND_AUDITED',quarantined=0,rule_hash='rule',input_raw_records=9)
    (p/'manifest.json').write_text(json.dumps(manifest))
    (root/'report').mkdir()
    (root/'report/dnse_normalization_audit.json').write_text(json.dumps(dict(state='VERIFIED',raw_rows_reconciled=9,
        checked_at_utc='2026-11-12T00:00:00+00:00',source_archives_unchanged=[dict(path='data/history/dnse/2026-11-11/history.db',sha256='fixture')])))
    with closing(sqlite3.connect(p/'normalized.db')) as db:
        db.executescript('CREATE TABLE archives(path TEXT);CREATE TABLE snapshots(id INTEGER PRIMARY KEY,symbol TEXT,source_time TEXT,payload_zlib BLOB);')
        db.execute('INSERT INTO archives VALUES(?)',('data/history/dnse/2026-11-11/history.db',))
        for i,r in enumerate(episode()):db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(i,'ACB',r['source_timestamp'],zlib.compress(json.dumps(r).encode())))
        db.commit()


def test_export_preserves_every_observation_and_requires_audited_day(tmp_path):
    prepare_archive(tmp_path)
    report=build('2026-11-11',root=tmp_path,config=config())
    assert report['dataset']['observations']==9 and report['events']==1 and not report['sampled']
    pack=json.loads((tmp_path/report['path']).read_text(encoding='utf-8'))
    assert pack['clock_policy']=='RETROSPECTIVE_SOURCE_ORDER_NOT_LIVE_BACKTEST'
    assert pack['frames'][0]['bids'][0]==['10000',100]
    with pytest.raises(ValueError,match='NOT_IN_AUDIT'):build('2026-11-12',root=tmp_path)


def test_loopback_server_asset_allowlist_csrf_and_review_history(tmp_path):
    prepare_archive(tmp_path);report=build('2026-11-11',root=tmp_path,config=config())
    assets=tmp_path/'web/monitor';assets.mkdir(parents=True);(assets/'index.html').write_text('<title>fixture</title>')
    server=MonitorServer(tmp_path,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def request(method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            c.request(method,path,json.dumps(body) if body else None,headers or {})
            r=c.getresponse();return r.status,r.read()
        finally:c.close()
    try:
        assert request('GET','/')[0]==200
        assert request('GET','/.env')[0]==404
        assert request('GET','/../.env')[0]==404
        assert request('GET','/api/dataset?dataset_id=../../.env')[0]==404
        assert request('GET','/api/datasets',headers={'Host':'attacker.invalid'})[0]==403
        metadata=json.loads(request('GET','/api/datasets')[1]);dataset_id=metadata['datasets'][0]['dataset_id']
        pack=json.loads(request('GET','/api/dataset?dataset_id='+dataset_id)[1]);eid=pack['event_patches'][0]['event']['id']
        body=dict(dataset_id=dataset_id,event_id=eid,verdict='useful',note='<script>literal note</script>')
        assert request('POST','/api/reviews',body,{'Content-Type':'application/json','Origin':'http://attacker.invalid'})[0]==403
        headers={'Content-Type':'application/json','Origin':origin,'X-Monitor-CSRF':metadata['csrf']}
        assert request('POST','/api/reviews',body,headers)[0]==200
        body['note']='updated';assert request('POST','/api/reviews',body,headers)[0]==200
        history=json.loads(request('GET','/api/reviews?dataset_id='+dataset_id)[1])
        assert len(history)==2 and history[0]['note']=='<script>literal note</script>' and history[1]['note']=='updated'
        body['event_id']='0'*64;assert request('POST','/api/reviews',body,headers)[0]==400
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
