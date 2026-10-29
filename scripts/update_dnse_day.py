"""One command: collect closed session, audit raw, normalize, audit and export."""
import argparse
from contextlib import closing
from datetime import date, datetime, time
import json
from pathlib import Path
import re
import sqlite3

from config.settings import ROOT
from scripts import collect_dnse_history, audit_dnse_history, normalize_dnse_history, audit_dnse_normalized, report_dnse_missing
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_normalization import load_rules
from src.models.orderbook import LOCAL_TZ

STEPS = ['preflight', 'collect', 'audit_raw', 'report_missing', 'normalize', 'audit_normalized', 'finance_checks']


def plan(day, session='day', now=None):
    if session not in ('day','morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    selected = date.fromisoformat(day)
    close = datetime.combine(selected,time(11,30) if session=='morning' else time(15,10),LOCAL_TZ)
    now = now or datetime.now(LOCAL_TZ)
    if now.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return dict(date=day,session=session,ready=now>=close,available_after_local=close.isoformat(),steps=STEPS,
                scope='ALL_LISTED_EQUITIES_HOSE_HNX_UPCOM_G1', schedules_enabled=False)


def complete_archive(path, day, session):
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        metadata=dict(db.execute('SELECT key,value FROM metadata'))
        settings=json.loads(metadata.get('settings','{}'))
        if settings.get('date')!=day or settings.get('session','day')!=session:
            raise ValueError('RAW_ARCHIVE_SETTINGS_MISMATCH')
        return bool(metadata.get('universe_complete') and db.execute('SELECT count(*) FROM symbols').fetchone()[0]
                    and not db.execute("SELECT 1 FROM symbols WHERE status NOT IN ('COMPLETE','NO_DATA_RETURNED') LIMIT 1").fetchone())


def run(day, *, session='day', root=ROOT, export=True, now=None):
    root=Path(root).resolve()
    result=plan(day,session,now)
    if not result['ready']:
        raise ValueError('MORNING_DOWNLOAD_REQUIRES_CLOSED_SESSION' if session=='morning' else 'DAILY_DOWNLOAD_REQUIRES_CLOSED_SESSION')
    folder=root/'data/history/dnse'/day
    if session!='day':
        folder=folder/session
    target=root/'report'/f'dnse_update_{day}_{session}.json'
    result.update(state='RUNNING',completed_steps=[],export=export)
    def save():
        write_json(target,result)
        print(json.dumps({key:result[key] for key in ('date','session','state','completed_steps')},ensure_ascii=True),flush=True)
    with exclusive_job(root/'data/control/dnse_update.lock'),collect_dnse_history.keep_awake():
        try:
            result['current_step']='preflight'
            load_rules(normalize_dnse_history.prepare_rules(root))
            result['completed_steps'].append('preflight')
            save()
            result['current_step']='collect'
            if complete_archive(folder/'history.db',day,session):
                result['collection_state']='ALREADY_COMPLETE_NO_NETWORK'
            else:
                collected=collect_dnse_history.run(day,root=root,session=session)
                result['collection_state']=collected['state']
                if collected['state']!='COMPLETED':
                    raise ValueError('COLLECTION_NOT_COMPLETE')
            result['completed_steps'].append('collect')
            save()
            result['current_step']='audit_raw'
            raw=audit_dnse_history.audit(day,root=root,session=session,export=export)
            if not raw['all_symbols_fully_paginated']:
                raise ValueError('RAW_AUDIT_NOT_COMPLETE')
            result['completed_steps'].append('audit_raw')
            save()
            result['current_step']='report_missing'
            report_dnse_missing.make_report(day,session=session,root=root)
            result['completed_steps'].append('report_missing')
            save()
            result['current_step']='normalize'
            normalized=normalize_dnse_history.run(root=root)
            if normalized['state']!='COMPLETED' or normalized['quarantined_occurrences']:
                raise ValueError('NORMALIZATION_REQUIRES_REVIEW')
            result['completed_steps'].append('normalize')
            save()
            result['current_step']='audit_normalized'
            audited=audit_dnse_normalized.audit(root=root,export=export)
            if audited['state']!='VERIFIED':
                raise ValueError('NORMALIZED_AUDIT_NOT_VERIFIED')
            result['completed_steps'].append('audit_normalized')
            save()
            result['current_step']='finance_checks'
            finance=audit_dnse_normalized.validate_finance(root=root)
            if finance['state']!='PASSED':
                raise ValueError('FINANCE_CHECKS_FAILED')
            result['completed_steps'].append('finance_checks')
            # Replace the current manifest; older optional exports may still exist,
            # but must not be advertised as exports of the newly updated database.
            files={'database':audited['database']}
            if 'export' in audited:
                files['normalized_jsonl_gz']=audited['export']
            write_json(root/'data/processed/dnse/manifest.json',{
                'state':'NORMALIZED_AND_AUDITED',
                'normalization_version':normalized['normalization_version'],
                'rule_hash':normalized['rule_hash'],
                'input_raw_records':normalized['raw_occurrences_processed'],
                'unique_normalized_snapshots':normalized['unique_normalized_snapshots'],
                'exact_duplicates_linked':normalized['duplicate_raw_occurrences'],
                'quarantined':normalized['quarantined_occurrences'],
                'unit_scope_counts':normalized['unit_scope_counts'],
                'files':files,'audit_report':'report/dnse_normalization_audit.json',
                'finance_checks':finance,'exports_refreshed':export})
            result.update(state='COMPLETED',current_step=None,dashboard_database='data/processed/dnse/normalized.db')
            save()
        except (Exception,KeyboardInterrupt) as error:
            result['state']='INTERRUPTED_RESUMABLE' if isinstance(error,KeyboardInterrupt) else 'FAILED_RESUMABLE'
            message=str(error)
            result['error_code']=message if re.fullmatch('[A-Z0-9_]{1,90}',message) else type(error).__name__
            save()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',default=datetime.now(LOCAL_TZ).date().isoformat())
    parser.add_argument('--session',choices=['day','morning'],default='day')
    parser.add_argument('--plan',action='store_true',help='Print steps and close-time gate; no API or file writes')
    parser.add_argument('--no-export',action='store_true',help='Keep all audits; omit large JSONL export files')
    args=parser.parse_args()
    try:
        result=plan(args.date,args.session) if args.plan else run(args.date,session=args.session,export=not args.no_export)
    except (ValueError, OSError, RuntimeError, sqlite3.Error, KeyboardInterrupt) as error:
        message=str(error)
        code=message if re.fullmatch('[A-Z0-9_]{1,90}',message) else type(error).__name__
        print(json.dumps({'state':'NOT_STARTED','error_code':code},ensure_ascii=True))
        return 1
    if args.plan:
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    return 0 if result['state']=='COMPLETED' else 1


if __name__=='__main__':
    raise SystemExit(main())
