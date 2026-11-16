"""Build a separate three-symbol historical revision; never rewrite source data."""
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import uuid
import argparse

from config.settings import ROOT
from src.data_pipeline.parquet_history import ParquetHistory,checked_folder,digest,confined
from src.data_pipeline.dnse_normalization import canonical_json,load_rules
from src.data_pipeline.dnse_unit_revision import revise_record,REVISION_FIELDS
from scripts.build_orderbook_monitor import atomic,build

SYMBOLS=('ACB','FPT','HPG')


def review_evidence(root):
    from src.data_pipeline.ssi_live_normalization import load_contract
    # Validate the newly reviewed independent SSI certificate before using SSI as
    # the numeric reference for DNSE. Historical UNVERIFIED labels remain intact.
    contract=load_contract(root)
    if not all(s in contract['symbols'] and s in contract['trade_symbols'] for s in SYMBOLS):
        raise ValueError('REVIEWED_SSI_REFERENCE_REQUIRED')
    path=root/'data/raw/ssi/acceptance_20261116_061425.json';saved=json.loads(path.read_bytes())
    run=root/'data/realtime/ssi/2026-11-16/1950d86d18b44d1ab480ead87863ac0d'
    import pyarrow.parquet as pq
    trades=[]
    for part in json.loads((run/'manifest.json').read_bytes())['files']:
        source=run/part['path']
        if digest(source)!=part['sha256']:raise ValueError('REFERENCE_CAPTURE_CHANGED')
        for batch in pq.ParquetFile(source).iter_batches(columns=['record_json']):
            for row in batch.to_pylist():
                record=json.loads(row['record_json'])
                if record['kind']=='TRADE':trades.append((record['symbol'],record['trade']))
    reviewed={}
    for symbol in SYMBOLS:
        books=[];pairs=[]
        for pair in saved['quote_matches'][symbol]:
            q=pair['dnse_quote'];f=pair['ssi_frame']
            if q['symbol']!=symbol or q['marketId']!='STO' or q['boardId']!='G1':raise ValueError('QUOTE_REFERENCE_SCOPE')
            for a,b in (('bid','bids'),('offer','asks')):
                actual=[(Decimal(str(v['price']))*1000,Decimal(str(v['quantity']))*10) for v in q[a]]
                expected=[(Decimal(str(p)),Decimal(str(v))) for p,v in f[b][:3]]
                if len(actual)!=3 or actual!=expected:raise ValueError('QUOTE_REFERENCE_SCALE')
            books.append(canonical_json([q['bid'],q['offer']]))
        for ref in saved['references']:
            if ref['symbol']!=symbol or ref['kind']!='trades':continue
            if ref['response']['api_version']!='2026-07-23':raise ValueError('REFERENCE_API_VERSION')
            for q in ref['response']['payload']['trades']:
                if q['symbol']!=symbol or q['marketId']!='STO' or q['boardId']!='G1':raise ValueError('TRADE_REFERENCE_SCOPE')
                candidates=[t for s,t in trades if s==symbol and
                    Decimal(t['price'])==Decimal(str(q['matchPrice']))*1000 and
                    t['qty']==Decimal(str(q['matchQtty']))*10 and
                    t['cumulative']==Decimal(str(q['totalVolumeTraded']))*10]
                if candidates:pairs.append(dict(dnse=q,ssi=candidates[0]))
        distinct=len(set(books));trade_states=len({p['ssi']['cumulative'] for p in pairs})
        if distinct<2 or trade_states<2:raise ValueError('INSUFFICIENT_INDEPENDENT_UNIT_STATES_'+symbol)
        reviewed[symbol]=dict(distinct_books=distinct,distinct_trade_cumulatives=trade_states,
            quote_pairs=saved['quote_matches'][symbol],trade_pairs=pairs)
    evidence=dict(format='reviewed-dnse-ssi-unit-chain-v1',status='EMPIRICALLY_VERIFIED',
        api_version='2026-07-23',market='STO',board='G1',price_multiplier='1000',volume_multiplier='10',
        independent_anchor='Maybank explicit VND/share units -> SSI numeric fields -> DNSE numeric fields',
        evidence=[dict(path=path.relative_to(root).as_posix(),sha256=digest(path))],
        ssi_contract_evidence=contract['evidence'],results=reviewed,
        timing_parity_certified=False,market_completeness_certified=False,
        historical_policy='Apply reviewed fixed API schema factors retrospectively; not independent verification of every historical day.')
    return evidence


def prepare(root=ROOT):
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
    root=Path(root).resolve();evidence=review_evidence(root)
    stage=root/'data/control/verified-monitor-history'/uuid.uuid4().hex;stage.mkdir(parents=True)
    atomic(stage/'report/dnse_reviewed_unit_chain.json',evidence)
    original=json.loads((root/'data/control/dnse_normalization_rules.json').read_bytes())
    # Keep the original rules/canonical datasets unchanged. The new scope has its
    # own hash and dataset IDs; this is a derived data product.
    rules_value=dict(original,empirically_verified_symbols=list(SYMBOLS),
        evidence=[dict(path='../../report/dnse_reviewed_unit_chain.json',sha256=digest(stage/'report/dnse_reviewed_unit_chain.json'))])
    atomic(stage/'data/control/dnse_normalization_rules.json',rules_value)
    rules=load_rules(stage/'data/control/dnse_normalization_rules.json')
    prior_hash=hashlib.sha256(canonical_json(original).encode()).hexdigest()
    source=ParquetHistory(root/'data/compact/catalog.json');entries=[];proof=[];total=0
    for day in source.dates():
        folder,manifest=checked_folder(root,source.days[day]);target=stage/'data/compact/dnse'/day;target.mkdir(parents=True)
        ids=[];before=hashlib.sha256();after=hashlib.sha256();count=eligible=0
        for path in sorted(folder.glob('quotes-*.parquet')):
            table=ds.dataset(path,format='parquet').to_table(filter=ds.field('symbol').isin(list(SYMBOLS)))
            if not table.num_rows:continue
            values=table.to_pylist()
            for row in values:
                old=json.loads(row['record_json']);new=revise_record(old,rules,prior_hash)
                immutable={k:v for k,v in old.items() if k not in REVISION_FIELDS}
                before.update(canonical_json(immutable).encode()+b'\n')
                after.update(canonical_json({k:v for k,v in new.items() if k not in REVISION_FIELDS}).encode()+b'\n')
                row.update(record_json=canonical_json(new).encode(),units_status=new['unit_verification_status'],
                    quality=new['data_quality_status'],financial_eligible=int(new['financial_eligible']))
                ids.append(row['id']);count+=1;eligible+=int(new['financial_eligible'])
            pq.write_table(pa.Table.from_pylist(values,schema=table.schema),target/path.name,compression='zstd',compression_level=6)
        if not count or before.hexdigest()!=after.hexdigest():raise ValueError('REVISION_OBSERVATION_IDENTITY_CHANGED')
        lineage=ds.dataset(folder/'lineage.parquet',format='parquet').to_table(filter=ds.field('snapshot_id').isin(ids))
        pq.write_table(lineage,target/'lineage.parquet',compression='zstd',compression_level=6)
        metadata=json.loads((folder/'sqlite_metadata.json').read_bytes())
        metadata['normalization_rules']=[dict(hash=rules.rule_hash,payload=canonical_json(rules_value))]
        atomic(target/'sqlite_metadata.json',metadata)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(target.iterdir())]
        revised=dict(format='liquiditylab-unit-revised-parquet-day-v1',day=day,snapshots=count,
            lineage_rows=lineage.num_rows,files=files,unchanged_observation_digest=before.hexdigest(),
            upstream_manifest_sha256=digest(folder/'manifest.json'),unit_rule_hash=rules.rule_hash,
            upstream_raw_audit_retained=True,raw_rows_reconciled_in_this_revision=False)
        atomic(target/'manifest.json',revised)
        entry=dict(day=day,state='UNIT_REVISION_ALL_ROWS_VERIFIED',folder=target.relative_to(stage).as_posix(),snapshots=count)
        # Read the written file back and compare all JSON/projection fields.
        read_count=0;read_digest=hashlib.sha256()
        for path in sorted(target.glob('quotes-*.parquet')):
            for batch in pq.ParquetFile(path).iter_batches():
                for row in batch.to_pylist():
                    record=json.loads(row['record_json'])
                    if (row['event_key']!=record['event_id'] or row['symbol']!=record['symbol'] or
                        row['units_status']!=record['unit_verification_status'] or row['quality']!=record['data_quality_status'] or
                        bool(row['financial_eligible'])!=record['financial_eligible'] or record['unit_contract_hash']!=rules.rule_hash):
                        raise ValueError('REVISION_PARQUET_PROJECTION_MISMATCH')
                    read_digest.update(canonical_json({k:v for k,v in record.items() if k not in REVISION_FIELDS}).encode()+b'\n');read_count+=1
        if read_count!=count or read_digest.hexdigest()!=before.hexdigest():raise ValueError('REVISION_PARQUET_ROUNDTRIP_MISMATCH')
        entries.append(entry);total+=count;proof.append(dict(day=day,rows=count,eligible=eligible,unchanged_digest=before.hexdigest()))
        print(json.dumps(dict(state='REVISION_DAY_VERIFIED',day=day,rows=count,eligible=eligible)),flush=True)
    trade_entries=[]
    for entry in source.index.get('trade_packages',[]):
        if entry.get('symbol') not in SYMBOLS:continue
        trade_folder,_=checked_folder(root,entry)
        destination=stage/entry['folder'];destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(trade_folder,destination)
        trade_entries.append(entry)
    atomic(stage/'data/compact/catalog.json',dict(quote_days=entries,trade_packages=trade_entries))
    revision=dict(state='ALL_REVISED_CANONICAL_ROWS_VERIFIED',rows=total,rule_hash=rules.rule_hash,
        observation_fields_unchanged=True,days=proof,source_root_unchanged=True,
        raw_reconciliation='Original raw audits retained; this run independently checks every revised canonical observation.')
    atomic(stage/'report/unit_revision_proof.json',revision)
    archives={a['path']:a for day in source.dates() for a in source.metadata(day)['archives']}
    checked_archives=[]
    for path,a in sorted(archives.items()):
        if digest(confined(root,path))!=a['sha256']:raise ValueError('REVISION_RAW_ARCHIVE_CHANGED')
        checked_archives.append(dict(path=path,sha256=a['sha256']))
    audit=dict(state='UNIT_REVISION_VERIFIED',checked_at_utc=datetime.now(timezone.utc).isoformat(),revised_rows=total,
        source_archives_unchanged=checked_archives,
        source_archive_claim_basis='Current raw file hashes plus prior per-day raw audits; no new raw row reconciliation is claimed',
        revision_proof=dict(path='report/unit_revision_proof.json',sha256=digest(stage/'report/unit_revision_proof.json')))
    atomic(stage/'report/dnse_normalization_audit.json',audit)
    atomic(stage/'data/processed/dnse/manifest.json',dict(state='UNIT_REVISION_AUDITED',quarantined=0,rule_hash=rules.rule_hash,revised_rows=total))
    return finalize(stage,root)


def finalize(stage,root=ROOT):
    root=Path(root).resolve();stage=Path(stage).resolve()
    if stage.parent!=(root/'data/control/verified-monitor-history').resolve():raise ValueError('REVISION_STAGE_INVALID')
    source=ParquetHistory(root/'data/compact/catalog.json')
    catalog=json.loads((stage/'data/compact/catalog.json').read_bytes())
    if not catalog['trade_packages']:
        for entry in source.index.get('trade_packages',[]):
            if entry.get('symbol') not in SYMBOLS:continue
            folder,_=checked_folder(root,entry)
            target=stage/entry['folder'];target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copytree(folder,target)
            checked_folder(stage,entry);catalog['trade_packages'].append(entry)
        atomic(stage/'data/compact/catalog.json',catalog)
    for day in source.dates():
        for symbol in SYMBOLS:
            result=build(day,root=stage,symbol=symbol)
            print(json.dumps(dict(state='REVISED_REPLAY_BUILT',day=day,symbol=symbol,observations=result['dataset']['observations'])),flush=True)
    index=json.loads((stage/'data/exports/orderbook-monitor/index.json').read_bytes())
    names={'data/compact/catalog.json','data/exports/orderbook-monitor/index.json','data/processed/dnse/manifest.json',
        'report/dnse_normalization_audit.json','report/unit_revision_proof.json','report/dnse_reviewed_unit_chain.json',
        'data/control/dnse_normalization_rules.json'}
    for entry in catalog['quote_days']+catalog['trade_packages']:
        folder,manifest=checked_folder(stage,entry)
        names.add((folder/'manifest.json').relative_to(stage).as_posix())
        names.update((folder/p['path']).relative_to(stage).as_posix() for p in manifest['files'])
    for entry in index['datasets']:
        folder,manifest=checked_folder(stage,dict(folder=entry['compact_folder']))
        names.add((folder/'manifest.json').relative_to(stage).as_posix())
        names.update((folder/p['path']).relative_to(stage).as_posix() for p in manifest['files'])
        if entry.get('events_file'):names.add('data/exports/orderbook-monitor/'+entry['events_file'])
    files=[dict(path=n,bytes=(stage/n).stat().st_size,sha256=digest(stage/n)) for n in sorted(names)]
    proof=json.loads((stage/'report/unit_revision_proof.json').read_bytes())
    atomic(stage/'seed-manifest.json',dict(format='liquiditylab-private-web-seed-v1',symbols=list(SYMBOLS),days=source.dates(),
        datasets=len(index['datasets']),canonical_quote_rows=proof['rows'],files=files))
    result=dict(state='LOCAL_VERIFIED_HISTORY_PREPARED',stage=stage.relative_to(root).as_posix(),rows=proof['rows'],days=len(catalog['quote_days']),datasets=len(index['datasets']),
        original_data_changed=False,web_deployed=False,trade_packages_revised=False,rule_hash=proof['rule_hash'])
    atomic(root/'report/verified_monitor_history_latest.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--resume');args=parser.parse_args()
    print(json.dumps(finalize(args.resume) if args.resume else prepare()))
