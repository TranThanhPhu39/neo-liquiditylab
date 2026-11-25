from contextlib import closing
from datetime import datetime
import json

import pytest
pytest.importorskip('pyarrow')

from scripts.build_orderbook_monitor import atomic
from scripts.compact_dnse_history import verify_day, compact_trades
from scripts.pilot_compact_dnse import export_day
from scripts.serve_orderbook_monitor import MonitorServer
from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.parquet_history import ParquetHistory, confined
from src.data_pipeline.parquet_replay import write_pack, read_pack
from tests.test_storage_compaction import prepare


def compact(root):
    source=prepare(root)
    folder,_=export_day('2026-11-11',root=root)
    verified=verify_day(folder,root=root)
    atomic(root/'data/compact/catalog.json',dict(quote_days=[verified],trade_packages=[]))
    return source,folder


def test_history_queries_keep_ties_cutoffs_zero_null_and_lineage_without_sqlite(tmp_path):
    source,_=compact(tmp_path)
    expected=DNSEHistory(source,prefer_parquet=False)
    store=DNSEHistory(source)
    assert isinstance(store,ParquetHistory)
    at=datetime.fromisoformat('2026-11-11T11:29:59+07:00')
    assert store.catalog('2026-11-11')==expected.catalog('2026-11-11')
    assert store.window('ACB','2026-11-11')==expected.window('ACB','2026-11-11')
    assert store.reconstruct_at(['ACB','FPT','MISSING'],'2026-11-11',at)==expected.reconstruct_at(['ACB','FPT','MISSING'],'2026-11-11',at)
    assert store.sampled_records('ACB','2026-11-11',at)==expected.sampled_records('ACB','2026-11-11',at)
    source.unlink()
    assert store.records('ACB','2026-11-11')[0]['bid_vol_1']==0
    assert store.records('ACB','2026-11-11')[0]['bid_vol_2'] is None
    assert store.origin('event-ACB','ACB','2026-11-11')['ordinal']==0
    with closing(store.connect()) as db:
        assert db.execute('SELECT count(*) FROM archives').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='snapshots'").fetchone()[0]==0
    assert not source.exists()
    with pytest.raises(ValueError):store.reconstruct_at(['ACB'],'2026-11-11',datetime(2026,11,12))


def test_parquet_replay_and_trade_api_keep_identity_quality_and_reviews_without_old_json(tmp_path):
    source,_=compact(tmp_path)
    package=dict(dataset_id='a'*64,symbol='ACB',day='2026-11-11',frames=[dict(time='one',bids=[['21250.00',0],[None,None]])],
                 event_patches=[],quality_summary={'units':'ASSUMED_SHARED_SCHEMA'})
    folder=write_pack(tmp_path,package)
    assert read_pack(tmp_path,folder)==package
    path=tmp_path/'data/exports/orderbook-trades/dnse-acb-2026-11-11.json'
    trade=dict(format='liquiditylab-market-trades-v1',symbol='ACB',day='2026-11-11',trade_dataset_id='b'*64,
               unit_status='ASSUMED_SHARED_SCHEMA',trades=[dict(id='1',time='2026-11-11T02:00:00+00:00',price='21250.00',qty=100,side='UNSPECIFIED')])
    atomic(path,trade);entry=compact_trades(path,root=tmp_path)
    catalog=tmp_path/'data/compact/catalog.json';index=json.loads(catalog.read_bytes());index['trade_packages']=[entry];atomic(catalog,index)
    atomic(tmp_path/'data/exports/orderbook-monitor/index.json',dict(datasets=[dict(file='dnse-acb-2026-11-11.json',
        day=package['day'],symbol='ACB',dataset_id=package['dataset_id'],compact_folder=folder)]))
    source.unlink();path.unlink()
    server=MonitorServer(tmp_path,port=0)
    try:
        assert json.loads(server.dataset(package['dataset_id']).read_bytes())==package
        assert server.trades(package['dataset_id'])==trade
        server.reviews.append(dict(dataset_id='a'*64,event_id='c'*64,verdict='uncertain',note='keep me'))
        assert server.reviews.read('a'*64)[0]['note']=='keep me'
        bad=json.loads((tmp_path/folder/'metadata.json').read_bytes());bad['dataset_id']='d'*64;atomic(tmp_path/folder/'metadata.json',bad)
        with pytest.raises(ValueError,match='IDENTITY'):server.dataset('a'*64).read_bytes()
    finally:server.server_close()


def test_catalog_cannot_escape_storage_root(tmp_path):
    with pytest.raises(ValueError,match='UNSAFE'):confined(tmp_path,'../private/.env')


def test_cutoff_lookup_keeps_physical_order_ties_and_payload_without_loading_history(tmp_path,monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import timedelta
    _,folder=compact(tmp_path)
    store=ParquetHistory(tmp_path/'data/compact/catalog.json')
    path=next(p for p in folder.glob('quotes-*.parquet') if 'ACB' in pq.read_table(p,columns=['symbol']).column('symbol').to_pylist())
    table=pq.read_table(path);template=next(r for r in table.to_pylist() if r['symbol']=='ACB')
    point=datetime.fromisoformat('2026-11-11T11:29:59+07:00')
    rows=[]
    # Deliberately nonchronological physical rows, tied source timestamps,
    # future row first and a selected row in a later Parquet row group.
    for rid,delta in ((20,1),(11,0),(19,0),(12,-1)):
        row=dict(template,id=rid,source_timestamp_utc=point+timedelta(seconds=delta))
        record=json.loads(row['record_json']);record.update(test_marker=rid,bid_vol_1=0,bid_vol_2=None)
        row['record_json']=json.dumps(record).encode();rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows,schema=table.schema),path,row_group_size=2)
    monkeypatch.setattr(store,'rows',lambda *a:(_ for _ in ()).throw(AssertionError('Full-history read')))
    selected=store.at_row('ACB','2026-11-11',point)
    assert selected['id']==19 and json.loads(selected['record_json'])['test_marker']==19
    actual=store.reconstruct_at(['ACB','MISSING','ACB'],'2026-11-11',point)
    assert set(actual)=={'ACB'} and actual['ACB']['bid_vol_1']==0 and actual['ACB']['bid_vol_2'] is None
    assert store.at_row('ACB','2026-11-11',point-timedelta(seconds=2)) is None
    assert not store._cache


def test_large_event_history_is_losslessly_parquet_backed_and_legacy_replay_still_reads(tmp_path):
    from src.data_pipeline.parquet_replay import encode,identity
    import pyarrow as pa
    import pyarrow.parquet as pq
    patches=[dict(at_index=i,event=dict(evidence='x'*12000)) for i in range(1800)]
    package=dict(dataset_id='e'*64,frames=[dict(time='one')],event_patches=patches)
    assert len(encode(patches))>20_000_000
    relative=write_pack(tmp_path,package);folder=tmp_path/relative
    assert (folder/'metadata.json').stat().st_size<1000
    assert read_pack(tmp_path,relative)==package
    legacy=tmp_path/'data/compact/monitor/legacy';legacy.mkdir()
    small=dict(dataset_id='f'*64,frames=[dict(time='legacy')],event_patches=[patches[0]])
    (legacy/'metadata.json').write_bytes(encode({k:v for k,v in small.items() if k!='frames'}))
    pq.write_table(pa.table({'frame_json':[encode(f) for f in small['frames']]}),legacy/'frames.parquet')
    (legacy/'manifest.json').write_bytes(encode(dict(format='liquiditylab-parquet-replay-v1',dataset_id=small['dataset_id'],
        frames=1,semantic_sha256=identity(small))))
    assert read_pack(tmp_path,legacy.relative_to(tmp_path))==small


def test_legacy_normalization_command_cannot_recreate_full_sqlite_in_parquet_mode(tmp_path):
    from scripts.normalize_dnse_history import run
    source,_=compact(tmp_path)
    source.unlink()
    with pytest.raises(ValueError,match='PARQUET_MODE_USE_UPDATE_DNSE_DAY'):run(root=tmp_path)
    assert not source.exists()


def test_cleanup_defers_locked_sqlite_then_removes_only_fully_verified_replacements(tmp_path,monkeypatch):
    from scripts.migrate_dashboard_parquet import migrate
    from scripts.cleanup_dashboard_storage import run as clean
    from scripts.scheduled_collection import exclusive_job
    source,_=compact(tmp_path)
    pack=dict(dataset_id='a'*64,symbol='ACB',day='2026-11-11',frames=[],event_patches=[])
    path=tmp_path/'data/exports/orderbook-monitor/dnse-acb-2026-11-11.json'
    atomic(path,pack)
    atomic(path.parent/'index.json',dict(datasets=[dict(file=path.name,day=pack['day'],symbol='ACB',dataset_id=pack['dataset_id'])]))
    atomic(tmp_path/'data/processed/dnse/manifest.json',dict(state='NORMALIZED_AND_AUDITED',files={}))
    raw=tmp_path/'data/raw/keep.json';atomic(raw,dict(evidence='retain'))
    migrate(tmp_path)
    # Backup/restore is exercised independently and by the real migration drill.
    # Keep this lock/deletion test independent of Windows nested-path limits.
    from src.data_pipeline.parquet_history import digest
    saved={p.relative_to(tmp_path).as_posix():digest(p) for p in (tmp_path/'data/compact').rglob('*') if p.is_file()}
    monkeypatch.setattr('scripts.cleanup_dashboard_storage.backup_verified',lambda root:saved)
    with exclusive_job(tmp_path/'data/control/dnse_update.lock'):
        first=clean(tmp_path)
        assert first['state']=='JSON_CLEANED_SQLITE_DEFERRED'
        assert source.exists() and not path.exists() and raw.exists()
    atomic(tmp_path/'report/dnse_normalization_audit.json',dict(state='VERIFIED',raw_rows_reconciled=3,unique_normalized_snapshots=2))
    final=clean(tmp_path)
    assert final['state']=='CLEANED' and not source.exists() and raw.exists()
    assert DNSEHistory(source).records('ACB','2026-11-11')[0]['bid_vol_1']==0


def test_incremental_day_update_does_not_recreate_global_sqlite_and_keeps_audits(tmp_path):
    from tests.test_dnse_normalization import prepare_fixture
    from scripts.update_dnse_parquet import run
    quote={'symbol':'ACB','marketId':'STO','boardId':'G1','time':'2026-10-28 13:36:43.792',
           'bid':[{'price':21.2-i*.05,'quantity':100} for i in range(3)],
           'offer':[{'price':21.25+i*.05,'quantity':200} for i in range(3)]}
    raw=prepare_fixture(tmp_path,quote)
    before=raw.read_bytes()
    atomic(tmp_path/'data/compact/catalog.json',dict(quote_days=[],trade_packages=[]))
    atomic(tmp_path/'report/dnse_normalization_audit.json',dict(state='VERIFIED',source_archives_unchanged=[]))
    steps=[]
    normalized,audit,finance=run('2026-10-28',root=tmp_path,progress=steps.append)
    assert steps==['normalize','audit_normalized','finance_checks']
    assert audit['storage']=='PARQUET' and audit['raw_rows_reconciled']==2
    assert normalized['unique_normalized_snapshots']==2 and finance['state']=='PASSED'
    assert not (tmp_path/'data/processed/dnse/normalized.db').exists()
    assert not (tmp_path/'data/processed/dnse/work/2026-10-28/normalized.db').exists()
    store=DNSEHistory(tmp_path/'data/processed/dnse/normalized.db')
    assert store.catalog('2026-10-28')['EMG']['archive_statuses']==['NO_DATA_RETURNED']
    assert store.records('FPT','2026-10-28')[0]['unit_verification_status']=='ASSUMED_SHARED_SCHEMA'
    # A repeated completed day remains lossless and retains the raw lineage.
    run('2026-10-28',root=tmp_path)
    assert raw.read_bytes()==before and store.origin(store.records('ACB','2026-10-28')[0]['event_id'],'ACB','2026-10-28')
    from scripts.build_orderbook_monitor import build
    from src.monitoring.spread import SpreadConfig
    from dataclasses import replace
    from decimal import Decimal
    atomic(tmp_path/'data/processed/dnse/manifest.json',dict(state='NORMALIZED_AND_AUDITED',quarantined=0,
        input_raw_records=2,rule_hash=normalized['rule_hash']))
    first=build('2026-10-28',root=tmp_path)['dataset']
    server=MonitorServer(tmp_path,port=0)
    try:
        original=server.dataset(first['dataset_id']).read_bytes()
        second=build('2026-10-28',root=tmp_path,config=replace(SpreadConfig(),multiplier=Decimal(4)))['dataset']
        assert first['dataset_id']!=second['dataset_id']
        assert server.dataset(first['dataset_id']).read_bytes()==original
        assert next(e for e in server.index() if e['dataset_id']==first['dataset_id'])['archived']
        assert all(e['storage']=='PARQUET' for e in server.index())
        assert not (tmp_path/'data/exports/orderbook-monitor/dnse-acb-2026-10-28.json').exists()
    finally:server.server_close()
