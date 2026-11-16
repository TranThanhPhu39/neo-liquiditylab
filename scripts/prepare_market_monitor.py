"""Export audited whole-market display columns, preserving every observation."""
from collections import Counter
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from src.data_pipeline.parquet_history import ParquetHistory,checked_folder,digest
from src.monitoring.market import FORMAT,TEXT_FIELDS,BOOK_FIELDS,SYMBOL

VERSION='market-projection-v1'


def package_day(root,cache,saved):
    """Independent immutable day releases avoid uploading old market days again."""
    from scripts.restore_ssi_checkpoint import recovery_key
    from scripts.package_monitor_web_seed import LocalAssets
    from src.monitoring.web_archive import encrypt_seed,restore_seed
    signature=hashlib.sha256(json.dumps(saved['files'],sort_keys=True).encode()).hexdigest()[:16]
    tag=f'monitor-web-seed-market-{saved["day"]}-{signature}'
    report=root/'report'/f'online_monitor_preparation_market_{saved["day"]}_{signature}.json'
    if report.exists():
        old=json.loads(report.read_bytes())
        if old.get('state')=='ENCRYPTED_AND_RESTORE_VERIFIED' and all((Path(old['folder'])/n).exists() for n in old['assets']):return tag,report
    key=recovery_key(root);output=cache/'sealed'
    with tempfile.TemporaryDirectory(prefix='mp-',dir=root/'data/control') as tmp:
        payload=Path(tmp)/'payload';payload.mkdir()
        files=[]
        for f in saved['files']:
            name=f'data/compact/market/{saved["day"]}/{f["path"]}';p=payload/name;p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(cache/f['path'],p);files.append(dict(path=name,bytes=f['bytes'],sha256=f['sha256']))
        atomic(payload/'seed-manifest.json',dict(format='liquiditylab-private-web-seed-v1',files=files,
            datasets=len(saved['datasets']),canonical_quote_rows=saved['rows'],days=[saved['day']],market_scope='ALL_CATALOG_SYMBOLS'))
        names=encrypt_seed(payload,key,tag,output)
        proof=restore_seed(Path(tmp)/'r',key,tag,api=LocalAssets(output,names))
    tar=output/'private-seed.tar.gz'
    if tar.resolve().parent==output.resolve() and tar.is_file():tar.unlink()
    result=dict(state='ENCRYPTED_AND_RESTORE_VERIFIED',tag=tag,folder=str(output),datasets=len(saved['datasets']),
        days=1,latest_day=saved['day'],canonical_rows=saved['rows'],symbols=sorted(e['symbol'] for e in saved['datasets']),
        assets=names,encrypted_bytes=sum((output/n).stat().st_size for n in names),proof=proof,uploaded=False,market_day=True)
    atomic(report,result)
    return tag,report


def project_day(root,day,source):
    import pyarrow as pa
    import pyarrow.parquet as pq
    folder,manifest=checked_folder(root,source.days[day]);upstream=digest(folder/'manifest.json')
    cache=root/'data/control/market-projection-cache'/(day+'-'+upstream[:16])
    if (cache/'manifest.json').exists():
        saved=json.loads((cache/'manifest.json').read_bytes())
        if saved.get('version')==VERSION and all(digest(cache/f['path'])==f['sha256'] for f in saved['files']):return cache,saved
    cache.mkdir(parents=True,exist_ok=True)
    schema=pa.schema([(k,pa.string()) for k in TEXT_FIELDS]+[(k,pa.int64() if '_vol_' in k else pa.string()) for k in BOOK_FIELDS]+
        [('source_order',pa.int64()),('source_time',pa.timestamp('us',tz='UTC'))])
    counts=Counter();units={};stats={};files=[];total=0
    for path in sorted(folder.glob('quotes-*.parquet')):
        target=cache/path.name;expected=hashlib.sha256();actual=hashlib.sha256();n=0
        with pq.ParquetWriter(target,schema,compression='zstd',compression_level=6) as writer:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2048,columns=['id','source_timestamp_utc','record_json']):
                projected=[]
                for row in batch.to_pylist():
                    r=json.loads(row['record_json']);s=r['symbol']
                    if not SYMBOL.fullmatch(s) or r['local_date']!=day or r['exchange'] not in ('HOSE','HNX','UPCOM'):raise ValueError('MARKET_SOURCE_SCOPE_INVALID')
                    v={k:r.get(k) for k in TEXT_FIELDS+BOOK_FIELDS}
                    v.update(source_order=row['id'],source_time=row['source_timestamp_utc'])
                    projected.append(v);counts[s]+=1;units.setdefault(s,Counter())[r['unit_verification_status']]+=1
                    if counts[s]>100000:raise ValueError('MARKET_DAY_LIMIT_NO_SILENT_SAMPLING')
                    stats[s]=dict(exchange=r['exchange'],unit_rule_hash=r['unit_contract_hash'])
                    expected.update(json.dumps(v,sort_keys=True,default=str,separators=(',',':')).encode()+b'\n')
                writer.write_table(pa.Table.from_pylist(projected,schema=schema));n+=len(projected)
        read_count=0
        for batch in pq.ParquetFile(target).iter_batches(batch_size=2048):
            for row in batch.to_pylist():
                actual.update(json.dumps(row,sort_keys=True,default=str,separators=(',',':')).encode()+b'\n');read_count+=1
        if n!=read_count or expected.hexdigest()!=actual.hexdigest():raise ValueError('MARKET_PROJECTION_ROUNDTRIP_FAILED')
        if target.stat().st_size>32*1024*1024:raise ValueError('MARKET_PROJECTION_SHARD_TOO_LARGE')
        files.append(dict(path=target.name,bytes=target.stat().st_size,sha256=digest(target)));total+=n
    if total!=manifest['snapshots']:raise ValueError('MARKET_SOURCE_ROW_COUNT_MISMATCH')
    registry=source.catalog(day)
    if any(s not in registry for s in counts):raise ValueError('MARKET_SYMBOL_NOT_IN_SOURCE_CATALOG')
    archives=source.metadata(day)['archives'];archive=next(a for a in archives if a['path']==f'data/history/dnse/{day}/history.db')
    entries=[]
    for symbol,r in sorted(registry.items()):
        if not SYMBOL.fullmatch(symbol):raise ValueError('MARKET_CATALOG_SYMBOL_INVALID')
        count=counts[symbol];shard=hashlib.sha256(symbol.encode()).digest()[0]%4
        file=next((f for f in files if f['path']==f'quotes-{shard}.parquet'),None)
        if file is None and count:raise ValueError('MARKET_OBSERVED_SYMBOL_SHARD_MISSING')
        file=file or files[0]  # An absent symbol needs no data shard; never read it as zero.
        scope=f'{VERSION}|{upstream}|{day}|{symbol}'
        entries.append(dict(dataset_id=hashlib.sha256(scope.encode()).hexdigest(),symbol=symbol,day=day,exchange=r['exchange'],
            provider='dnse',observations=count,levels=3,archived=False,market_observation=True,availability='AVAILABLE' if count else r['status'],
            market_file=f'data/compact/market/{day}/{file["path"]}',market_sha256=file['sha256'],
            event_counts={},detector_version='market-observation-only-v1',source_archive={k:archive[k] for k in ('path','sha256')},
            unit_rule_hash=stats.get(symbol,{}).get('unit_rule_hash'),quality=dict(unit_counts=dict(units.get(symbol,{})),
                eligible_spread_observations=0,feed_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE',observation_gaps=None,
                detector_scope='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION')))
    saved=dict(version=VERSION,day=day,upstream_manifest_sha256=upstream,rows=total,files=files,datasets=entries,
        verification='EVERY_PROJECTED_FIELD_ROUNDTRIP_VERIFIED',no_sampling=True)
    atomic(cache/'manifest.json',saved)
    print(json.dumps(dict(state='MARKET_DAY_ALL_ROWS_VERIFIED',day=day,rows=total,symbols=len(entries),bytes=sum(f['bytes'] for f in files))),flush=True)
    return cache,saved


def prepare(root=ROOT,base_stage=None,days=None):
    root=Path(root).resolve()
    source_signature=hashlib.sha256((root/'data/compact/catalog.json').read_bytes()+(root/'data/control/ssi_live_contract.json').read_bytes()+VERSION.encode()).hexdigest()
    selected_days=days or ParquetHistory(root/'data/compact/catalog.json').dates()
    latest=root/'report/market_monitor_preparation_latest.json'
    if latest.exists():
        prior=json.loads(latest.read_bytes());prior_stage=root/prior['stage']
        if (prior.get('state')=='LOCAL_WHOLE_MARKET_PREPARED' and prior.get('source_signature')==source_signature and prior.get('selected_days')==selected_days and
            prior_stage.resolve().parent==(root/'data/control/verified-monitor-history').resolve() and not prior_stage.is_symlink() and
            (prior_stage/'seed-manifest.json').is_file() and digest(prior_stage/'seed-manifest.json')==prior.get('seed_manifest_sha256')):return prior
    if base_stage is None:
        from scripts.prepare_verified_monitor_history import prepare as verified
        base_stage=root/verified(root)['stage']
    base_stage=Path(base_stage).resolve()
    if base_stage.parent!=(root/'data/control/verified-monitor-history').resolve():raise ValueError('MARKET_BASE_STAGE_INVALID')
    stage=base_stage.parent/uuid.uuid4().hex
    shutil.copytree(base_stage,stage)
    source=ParquetHistory(root/'data/compact/catalog.json');entries=[];files=[];proof=[];releases={};reports=[]
    for day in selected_days:
        cache,saved=project_day(root,day,source)
        for f in saved['files']:
            relative=f'data/compact/market/{day}/{f["path"]}';p=stage/relative;p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(cache/f['path'],p)
        entries.extend(saved['datasets']);proof.append({k:saved[k] for k in ('day','rows','verification','upstream_manifest_sha256')})
        tag,report=package_day(root,cache,saved);releases[day]=tag;reports.append(str(report))
    catalog=stage/'data/compact/market/catalog.json';atomic(catalog,dict(format=FORMAT,datasets=entries,days=proof,day_releases=releases))
    files.append(dict(path=catalog.relative_to(stage).as_posix(),bytes=catalog.stat().st_size,sha256=digest(catalog)))
    manifest=json.loads((stage/'seed-manifest.json').read_bytes());base=json.loads((stage/'data/exports/orderbook-monitor/index.json').read_bytes())['datasets']
    occupied={(e['day'],e['symbol']) for e in base if not e.get('archived')}
    manifest.update(market_scope='ALL_CATALOG_SYMBOLS',market_days=proof,market_symbols=sorted({e['symbol'] for e in entries}),
        market_rows=sum(e['observations'] for e in entries),market_catalog_entries=len(entries),
        datasets=len(base)+sum((e['day'],e['symbol']) not in occupied for e in entries),
        verified_replay_symbols=['ACB','FPT','HPG'])
    manifest['files']=[f for f in manifest['files'] if not f['path'].startswith('data/compact/market/')]+files
    atomic(stage/'seed-manifest.json',manifest)
    atomic(stage/'market-publication.json',dict(day_reports=reports))
    if hashlib.sha256((root/'data/compact/catalog.json').read_bytes()+(root/'data/control/ssi_live_contract.json').read_bytes()+VERSION.encode()).hexdigest()!=source_signature:
        raise ValueError('MARKET_SOURCE_CHANGED_RETRY')
    result=dict(state='LOCAL_WHOLE_MARKET_PREPARED',stage=stage.relative_to(root).as_posix(),symbols=len(manifest['market_symbols']),
        source_signature=source_signature,selected_days=selected_days,
        seed_manifest_sha256=digest(stage/'seed-manifest.json'),
        days=len(proof),market_rows=manifest['market_rows'],market_catalog_entries=len(entries),datasets=manifest['datasets'],
        market_index_bytes=sum(f['bytes'] for f in files),total_bytes=sum(f['bytes'] for f in manifest['files']),
        market_encrypted_bytes=sum(json.loads(Path(r).read_bytes())['encrypted_bytes'] for r in reports),market_day_reports=reports,
        original_data_changed=False,web_deployed=False,detectors_expanded=False)
    atomic(root/'report/market_monitor_preparation_latest.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-stage');p.add_argument('--date');a=p.parse_args()
    print(json.dumps(prepare(base_stage=a.base_stage,days=[a.date] if a.date else None)))
