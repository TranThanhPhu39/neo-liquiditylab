"""Resumable audited local history -> encrypted release -> existing private web."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.scheduled_collection import exclusive_job

SYMBOLS=['ACB','FPT','HPG']


def policy(root):
    path=Path(root)/'config/monitor_publication.json'
    value=json.loads(path.read_bytes()) if path.exists() else dict(enabled=False,symbols=SYMBOLS)
    if value.get('symbols')!=SYMBOLS or not isinstance(value.get('enabled'),bool):raise ValueError('MONITOR_PUBLICATION_SCOPE_INVALID')
    return value


def fingerprint(root):
    root=Path(root);catalog=json.loads((root/'data/compact/catalog.json').read_bytes())
    entries=catalog['quote_days']
    if not entries or any(e['state']!='SOURCE_AND_PARQUET_ALL_ROWS_VERIFIED' for e in entries):
        raise ValueError('MONITOR_AUDITED_PARQUET_REQUIRED')
    contract=root/'data/control/ssi_live_contract.json'
    source=dict(quotes=entries,trades=catalog.get('trade_packages',[]),contract_sha256=hashlib.sha256(contract.read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest(),max(e['day'] for e in entries)


def prepare(root):
    from scripts.prepare_verified_monitor_history import prepare as build
    return build(root)


def package(stage,report):
    from scripts.package_monitor_web_seed import package as seal
    return seal(stage,report_path=report)


def publish(report):
    from scripts.publish_monitor_web_seed import publish as upload
    return upload(report)


def deploy(report):
    from scripts.update_monitor_seed_render import update
    return update(report)


def verify(day):
    from scripts.verify_monitor_hosted import request
    hosting=request('/api/hosting')
    if not hosting.get('seed_ready') or hosting.get('recovery_state')!='READY':return False
    entries=request('/api/datasets')['datasets']
    for symbol in SYMBOLS:
        e=next((e for e in entries if e['day']==day and e['symbol']==symbol and not e.get('archived')),None)
        if not e or e.get('observations',0)<=0 or set(e.get('quality',{}).get('unit_counts',{}))!={'EMPIRICALLY_VERIFIED'}:return False
    return True


def prune(root,artifacts,retain):
    """Only auto-generated cache paths recorded by this publisher are eligible."""
    root=Path(root).resolve();base=root/'data/control/verified-monitor-history';pending=[]
    keep=artifacts[-max(2,retain):]
    for artifact in artifacts[:-max(2,retain)]:
        for name in ('stage','sealed'):
            p=Path(artifact[name]).resolve()
            if p.parent!=base or not re.fullmatch('[a-f0-9]{32}(-sealed)?',p.name) or p.is_symlink():
                raise ValueError('MONITOR_CACHE_CLEANUP_UNSAFE_PATH')
            if p.exists():
                try:shutil.rmtree(p)
                except OSError:pending.append(str(p))
    return keep,pending


def run(root=ROOT,*,sleep=time.sleep):
    root=Path(root).resolve();cfg=policy(root)
    if not cfg['enabled']:return dict(state='DISABLED')
    output=root/'report/automation/monitor_daily_publication.json'
    with exclusive_job(root/'data/control/monitor-daily-publication.lock'):
        signature,day=fingerprint(root)
        old=json.loads(output.read_bytes()) if output.exists() else {}
        if old.get('source_fingerprint')==signature and old.get('state')=='COMPLETED':
            return dict(old,already_current=True)
        result=(old if old.get('source_fingerprint')==signature else
            dict(source_fingerprint=signature,day=day,artifacts=old.get('artifacts',[])))
        result.update(state='RUNNING',started_at=datetime.now(timezone.utc).isoformat())
        result.pop('error_code',None)
        def save():atomic(output,result)
        save()
        try:
            if not result.get('stage'):
                result['step']='prepare';save()
                built=prepare(root);result['stage']=str(root/built['stage']);save()
            stage=Path(result['stage'])
            report=root/'report'/('online_monitor_preparation_auto_'+signature[:16]+'.json')
            result['preparation_report']=str(report)
            if not report.exists():
                result['step']='encrypt_and_restore_verify';save();package(stage,report)
            if fingerprint(root)[0]!=signature:raise ValueError('MONITOR_SOURCE_CHANGED_RETRY')
            preparation=json.loads(report.read_bytes())
            if not preparation.get('remote_recovery_verified'):
                result['step']='upload_and_remote_verify';save();publish(report)
            preparation=json.loads(report.read_bytes())
            if preparation.get('latest_day')!=day:raise ValueError('MONITOR_SEED_DAY_MISMATCH')
            if fingerprint(root)[0]!=signature:raise ValueError('MONITOR_SOURCE_CHANGED_RETRY')
            if not result.get('deployment'):
                result['step']='deploy';save();result['deployment']=deploy(report);save()
            result['step']='verify_hosted';save()
            deadline=time.monotonic()+cfg.get('verify_timeout_seconds',360)
            while True:
                try:ready=verify(day)
                except (OSError,ValueError):ready=False
                if ready:break
                if time.monotonic()>=deadline:raise ValueError('MONITOR_HOSTED_VERIFICATION_PENDING')
                sleep(10)
            artifacts=result.get('artifacts',[])
            if not any(x['stage']==str(stage) for x in artifacts):
                artifacts.append(dict(stage=str(stage),sealed=preparation['folder']))
            kept,pending=prune(root,artifacts,cfg.get('retain_auto_stages',2))
            result.update(state='COMPLETED',step=None,tag=preparation['tag'],artifacts=kept,cleanup_pending=pending,
                finished_at=datetime.now(timezone.utc).isoformat(),private_market_payload=True,
                original_raw_or_canonical_deleted=False)
        except Exception as error:
            code=str(error)
            result.update(state='FAILED_RESUMABLE',error_code=code if re.fullmatch('[A-Z0-9_]{1,100}',code) else type(error).__name__)
        save();return result


if __name__=='__main__':
    result=run();print(json.dumps({k:v for k,v in result.items() if k!='artifacts'}),flush=True)
    raise SystemExit(0 if result['state'] in ('COMPLETED','DISABLED') else 1)
