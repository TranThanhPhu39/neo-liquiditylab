from contextlib import closing
import base64
import hashlib
import json
import shutil
import sqlite3
import zlib

import pytest
from scripts.build_orderbook_monitor import atomic
from scripts.prepare_market_monitor import project_day,package_day
from scripts.pilot_compact_dnse import export_day
from scripts.compact_dnse_history import verify_day
from scripts.serve_orderbook_monitor import MonitorServer
from src.data_pipeline.parquet_history import ParquetHistory
from src.monitoring.market import FORMAT,MarketHistory
from tests.test_storage_compaction import prepare as storage
from tests.test_event_monitor import record


def test_watch_latest_selection_preserves_ties_null_zero_without_materializing_table(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    import pyarrow as pa
    store=MarketHistory(tmp_path);when=datetime(2026,11,23,3,tzinfo=timezone.utc)
    rows=[dict(symbol='ACB',source_time=when,source_order=i,bid_vol_1=q,ask_vol_1=None)
          for i,q in ((9,0),(2,100),(7,200))]
    source=pa.Table.from_pylist(rows)
    from types import SimpleNamespace
    monkeypatch.setattr(store,'scan',lambda *a:SimpleNamespace(to_batches=lambda:source.to_batches(max_chunksize=1)))
    monkeypatch.setattr(store,'table',lambda *a:(_ for _ in ()).throw(AssertionError('Full table read')))
    found=store.at([dict(symbol='ACB',observations=3,market_file='fixture')],when.isoformat())
    assert found['ACB']['source_order']==9 and found['ACB']['bid_vol_1']==0
    assert found['ACB']['ask_vol_1'] is None


@pytest.fixture
def market(tmp_path):
    source=storage(tmp_path)
    with closing(sqlite3.connect(source)) as db:
        for rid,symbol,exchange,t in ((3,'ACB','HOSE',0),(9,'SHS','HNX',2)):
            r=record(t,symbol=symbol,exchange=exchange,bid_vol_1=0,bid_vol_2=None,
                event_id=hashlib.sha256(symbol.encode()).hexdigest(),unit_verification_status='ASSUMED_SHARED_SCHEMA')
            db.execute('UPDATE snapshots SET symbol=?,exchange=?,event_key=?,units_status=?,financial_eligible=0,payload_zlib=?,source_time=? WHERE id=?',
                (symbol,exchange,r['event_id'],r['unit_verification_status'],zlib.compress(json.dumps(r).encode()),r['source_timestamp'],rid))
        db.execute('DELETE FROM raw_catalog')
        db.executemany('INSERT INTO raw_catalog VALUES(?,?,?,?,?)',[
            ('archive','ACB','HOSE','COMPLETE',1),('archive','SHS','HNX','COMPLETE',1),('archive','EMG','UPCOM','NO_DATA_RETURNED',0)])
        db.commit()
    folder,_=export_day('2026-11-11',root=tmp_path);verified=verify_day(folder,root=tmp_path)
    atomic(tmp_path/'data/compact/catalog.json',dict(quote_days=[verified],trade_packages=[]))
    cache,saved=project_day(tmp_path,'2026-11-11',ParquetHistory(tmp_path/'data/compact/catalog.json'))
    for f in saved['files']:
        target=tmp_path/'data/compact/market/2026-11-11'/f['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(cache/f['path'],target)
    atomic(tmp_path/'data/compact/market/catalog.json',dict(format=FORMAT,datasets=saved['datasets']))
    atomic(tmp_path/'data/exports/orderbook-monitor/index.json',dict(datasets=[]))
    return tmp_path,cache,saved,source


def test_whole_market_keeps_missing_zero_null_exchange_and_blocks_detectors(market):
    root,cache,saved,source=market;source.unlink()
    server=MonitorServer(root,port=0)
    try:
        entries=server.index();assert {e['symbol'] for e in entries}=={'ACB','SHS','EMG'}
        shs=next(e for e in entries if e['symbol']=='SHS');empty=next(e for e in entries if e['symbol']=='EMG')
        p=json.loads(server.dataset(shs['dataset_id']).read_bytes())
        assert p['exchange']=='HNX' and p['frames'][0]['bids'][0][1]==0 and p['frames'][0]['bids'][1][1] is None
        assert not p['frames'][0]['features']['eligible'] and p['event_patches']==[]
        assert empty['availability']=='NO_DATA_RETURNED' and empty['observations']==0
        with pytest.raises(ValueError,match='NO_SOURCE'):server.dataset(empty['dataset_id']).read_bytes()
        assert server.trades(shs['dataset_id'])['state']=='NOT_COLLECTED'
        with pytest.raises(ValueError,match='NO_DETECTOR_PERMISSION'):server.analyze(p['day'],'SHS',None,None)
        before={r['symbol']:r for r in server.watchlist(p['day'],'2026-11-11T02:15:00+00:00',['ACB','SHS','EMG'])}
        assert before['SHS']['status']=='NO_DATA' and before['EMG']['status']=='NO_DATA_RETURNED'
        after=server.watchlist(p['day'],'2026-11-11T02:15:02+00:00',['SHS'])[0]
        assert after['time']=='2026-11-11T02:15:02+00:00' and after['status']=='UNVERIFIED_UNITS'
        with pytest.raises(ValueError):server.watchlist(p['day'],'2026-11-11T02:15:02Z',['../.env'])
    finally:server.server_close()
    assert not source.exists()


def test_market_file_tampering_and_traversal_are_rejected(market):
    root,cache,saved,_=market;e=next(e for e in saved['datasets'] if e['symbol']=='SHS')
    (root/e['market_file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='CHECKSUM'):MarketHistory(root).replay(e)
    bad=dict(e,market_file='data/compact/market/../../../../.env')
    atomic(root/'data/compact/market/catalog.json',dict(format=FORMAT,datasets=[bad]))
    with pytest.raises(ValueError):MarketHistory(root).index()


def test_encrypted_day_restores_only_on_demand_and_keeps_no_data_label(market,monkeypatch):
    from scripts.package_monitor_web_seed import LocalAssets
    from src.monitoring.web_archive import restore_seed
    root,cache,saved,_=market;key=b'x'*32
    monkeypatch.setenv('LIQUIDITYLAB_STATE_KEY',base64.b64encode(key).decode())
    tag,report=package_day(root,cache,saved);packed=json.loads(report.read_bytes())
    cloud=root/'cloud';cloud.mkdir()
    atomic(cloud/'data/compact/market/catalog.json',dict(format=FORMAT,datasets=saved['datasets'],day_releases={saved['day']:tag}))
    history=MarketHistory(cloud);entries=history.index();calls=[]
    def recover(destination,release):
        calls.append(release);return restore_seed(destination,key,release,api=LocalAssets(cache/'sealed',packed['assets']))
    history.recover_day=recover
    assert not calls
    e=next(e for e in entries if e['symbol']=='SHS')
    assert history.replay(e)['frames'] and calls==[tag]
    assert history.replay(e)['frames'] and calls==[tag]
    assert next(e for e in history.index() if e['symbol']=='EMG')['availability']=='NO_DATA_RETURNED'


def test_concurrent_catalog_requests_parse_once_and_reuse_file_path_checks(market,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import threading,time
    import src.monitoring.market as module
    root,_,_,_=market;history=MarketHistory(root);read=Path.read_bytes;reads=[];paths=[]
    original=module.confined
    def counted_read(path):
        if path==history.path:reads.append(path);time.sleep(.03)
        return read(path)
    def counted_path(root,name):paths.append(name);return original(root,name)
    monkeypatch.setattr(Path,'read_bytes',counted_read);monkeypatch.setattr(module,'confined',counted_path)
    barrier=threading.Barrier(6)
    def load():barrier.wait();return history.index()
    with ThreadPoolExecutor(max_workers=6) as workers:results=list(workers.map(lambda _:load(),range(6)))
    assert len(reads)==1 and all(v is results[0] for v in results)
    assert len(paths)==len({e['market_file'] for e in results[0]})
