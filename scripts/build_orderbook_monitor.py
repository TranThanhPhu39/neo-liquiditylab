"""Build the offline event workspace from audited history; never call a provider."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zlib

from config.settings import ROOT
from src.data_pipeline.job_lock import exclusive_job
from src.data_pipeline.dnse_history import DNSEHistory, day_bounds, utc_text
from src.data_pipeline.parquet_history import ParquetHistory
from src.data_pipeline.parquet_replay import read_pack, write_pack
from src.monitoring.spread import SpreadConfig, stamp
from src.monitoring.events import OfflineEngine, EventConfig, VERSION, parameters

FORMAT = 'liquiditylab-event-replay-v1'
SYMBOLS = ('ACB','FPT','HPG')


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    temporary.replace(path)


def serial(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serial(v) for v in value]
    return value


def audited_days(root=ROOT):
    audit=json.loads((Path(root)/'report/dnse_normalization_audit.json').read_text(encoding='utf-8'))
    return sorted({a['path'].split('/')[3] for a in audit.get('source_archives_unchanged',[])
                   if a['path'].startswith('data/history/dnse/') and a['path'].endswith('/history.db')
                   and len(a['path'].split('/'))==5})


def build(day, *, root=ROOT, config=None, event_config=None, symbol='ACB'):
    root=Path(root).resolve()
    if symbol not in SYMBOLS:raise ValueError('MONITOR_UNSUPPORTED_SYMBOL')
    with exclusive_job(root/'data/control/orderbook-monitor-build.lock'):
        cfg=config or SpreadConfig()
        return _build(day,root,cfg,event_config or EventConfig(duration_seconds=cfg.min_duration_seconds),symbol)


def _build(day,root,cfg,event_cfg,symbol):
    start,end=day_bounds(day)
    manifest=json.loads((root/'data/processed/dnse/manifest.json').read_text(encoding='utf-8'))
    audit=json.loads((root/'report/dnse_normalization_audit.json').read_text(encoding='utf-8'))
    evidence=next((a for a in audit.get('source_archives_unchanged',[]) if a['path']==f'data/history/dnse/{day}/history.db'),None)
    if manifest.get('state')!='NORMALIZED_AND_AUDITED' or manifest.get('quarantined')!=0:
        raise ValueError('MONITOR_AUDITED_ARCHIVE_REQUIRED')
    if audit.get('state')!='VERIFIED' or not evidence or audit.get('raw_rows_reconciled')!=manifest.get('input_raw_records'):
        raise ValueError('MONITOR_SOURCE_DAY_NOT_IN_AUDIT')
    scope=f'dnse|{symbol}|HOSE|G1|top3|{day}|{manifest["rule_hash"]}'
    engine=OfflineEngine(scope,cfg,event_cfg)
    frames,states,units,reasons=[],Counter(),Counter(),Counter()
    digest=hashlib.sha256();gaps=session_changes=0;previous=None
    store=DNSEHistory(root/'data/processed/dnse/normalized.db')
    with closing(store.connect()) as db:
        if not db.execute('SELECT 1 FROM archives WHERE path=?',(evidence['path'],)).fetchone():
            raise ValueError('MONITOR_SOURCE_DAY_NOT_REGISTERED')
        rows=store.records(symbol,day)
        for r in rows:
            if len(frames)>=50000:raise ValueError('MONITOR_DAY_LIMIT_EXCEEDED_NO_SILENT_SAMPLING')
            if (r.get('symbol')!=symbol or r.get('exchange')!='HOSE' or r.get('board_id')!='G1'
                    or r.get('local_date')!=day or r.get('unit_contract_hash')!=manifest['rule_hash']):
                raise ValueError('MONITOR_SCOPE_MISMATCH')
            f=serial(engine.push(r,len(frames)));states[f['detector_state']]+=1
            units[r.get('unit_verification_status','UNKNOWN')]+=1
            if f.get('reason'):reasons[f['reason']]+=1
            t=stamp(r['source_timestamp']).timestamp()
            if previous:
                if r['market_session']!=previous[1]:session_changes+=1
                elif f['eligible'] and t-previous[0]>cfg.max_observation_gap_seconds:gaps+=1
            previous=(t,r['market_session'])
            frames.append(dict(id=r['event_id'],time=r['source_timestamp'],received_at=r['collected_at_utc'],
                session=r.get('market_session'),quality=r.get('data_quality_status'),units=r.get('unit_verification_status'),
                bids=[[r.get(f'bid_price_{i}'),r.get(f'bid_vol_{i}')] for i in (1,2,3)],
                asks=[[r.get(f'ask_price_{i}'),r.get(f'ask_vol_{i}')] for i in (1,2,3)],features=f))
            digest.update(r['event_id'].encode())
    if not frames:raise ValueError('MONITOR_NO_SOURCE_OBSERVATIONS')
    dataset_id=hashlib.sha256((scope+VERSION+engine.config_hash+digest.hexdigest()).encode()).hexdigest()
    final_events={p['event']['id']:p['event'] for p in engine.patches}
    by_type=dict(Counter(e['type'] for e in final_events.values()))
    quality=dict(unit_counts=dict(units),inhibited_reasons=dict(reasons),observation_gaps=gaps,
                 session_transitions=session_changes,feed_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE',
                 eligible_spread_observations=sum(f['features']['eligible'] for f in frames))
    pack=dict(format=FORMAT,dataset_id=dataset_id,symbol=symbol,day=day,provider='dnse',exchange='HOSE',board='G1',levels=3,
        price_unit='VND',volume_unit='shares',clock_policy='RETROSPECTIVE_SOURCE_ORDER_NOT_LIVE_BACKTEST',
        sampling='ALL_STORED_SYMBOL_OBSERVATIONS_NO_RESAMPLE',unit_rule_hash=manifest['rule_hash'],
        detector_version=VERSION,config=cfg.payload(),detector_configs=dict(spread=cfg.payload(),events=event_cfg.payload()),
        analysis_parameters=parameters(cfg,event_cfg),config_hash=engine.config_hash,source_archive=evidence,
        audit_checked_at=audit['checked_at_utc'],configuration_status='EXPERIMENTAL_UNCALIBRATED',quality_summary=quality,
        continuity_policy='Observation chains only; not continuous-state or provider completeness proof',
        frames=frames,event_patches=engine.patches)
    folder=root/'data/exports/orderbook-monitor';name=f'dnse-{symbol.lower()}-{day}.json';target=folder/name
    index_path=folder/'index.json'
    entries=json.loads(index_path.read_text(encoding='utf-8')).get('datasets',[]) if index_path.exists() else []
    prior=next((e for e in entries if e['file']==name and not e.get('archived')),None)
    old=(read_pack(root,prior['compact_folder']) if prior and prior.get('compact_folder') else
         json.loads(target.read_text(encoding='utf-8')) if target.exists() else None)
    if old:
        if old['dataset_id']!=dataset_id:
            archive_name=f'dnse-{symbol.lower()}-{day}-{old["dataset_id"][:12]}.json'
            archive=folder/archive_name
            if archive.exists() and json.loads(archive.read_text(encoding='utf-8'))['dataset_id']!=old['dataset_id']:
                raise ValueError('MONITOR_ARCHIVE_IDENTITY_CONFLICT')
            if not archive.exists() and not (prior and prior.get('compact_folder')):shutil.copyfile(target,archive)
            for entry in entries:
                if entry['dataset_id']==old['dataset_id']:entry.update(file=archive_name,archived=True)
    compact_folder=write_pack(root,pack) if isinstance(store,ParquetHistory) else None
    if not compact_folder:atomic(target,pack)
    event_name=f'dnse-{symbol.lower()}-{day}-{dataset_id[:12]}.events.json'
    atomic(folder/event_name,dict(dataset_id=dataset_id,symbol=symbol,day=day,events=list(final_events.values()),
                                 quality=quality,config_hash=engine.config_hash))
    entry=dict(file=name,events_file=event_name,dataset_id=dataset_id,day=day,symbol=symbol,provider='dnse',
               observations=len(frames),levels=3,archived=False,event_counts=by_type,quality=quality,
               parameters=pack['analysis_parameters'],detector_version=VERSION)
    if compact_folder:entry.update(compact_folder=compact_folder,storage='PARQUET')
    entries=[e for e in entries if e['dataset_id']!=dataset_id]+[entry]
    atomic(index_path,dict(format=FORMAT,datasets=sorted(entries,key=lambda e:(e['day'],not e.get('archived',False),e['symbol']),reverse=True)))
    report=dict(state='EXPORTED',dataset=entry,events=len(final_events),detector_states=dict(states),
                source_rows_unchanged=True,sampled=False,configuration_status='EXPERIMENTAL_UNCALIBRATED',
                exported_at_utc=datetime.now(timezone.utc).isoformat(),
                bytes=sum(p.stat().st_size for p in (root/compact_folder).iterdir()) if compact_folder else target.stat().st_size,
                path=compact_folder or target.relative_to(root).as_posix())
    atomic(root/'report'/f'orderbook_monitor_{symbol.lower()}_{day}.json',report)
    if symbol=='ACB':atomic(root/'report'/f'orderbook_monitor_{day}.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group();group.add_argument('--date');group.add_argument('--all-days',action='store_true')
    parser.add_argument('--symbols',nargs='+',choices=SYMBOLS,default=['ACB'])
    args=parser.parse_args();days=audited_days() if args.all_days else [args.date or audited_days()[-1]]
    for day in days:
        for symbol in args.symbols:
            r=build(day,symbol=symbol)
            print(json.dumps({k:r[k] for k in ('state','dataset','events','bytes')},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
