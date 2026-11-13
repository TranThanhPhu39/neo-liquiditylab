"""GitHub SSI session worker: bounded windows, truthful outcomes, encrypted Parquet."""
import argparse
import base64
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import time
from zoneinfo import ZoneInfo

from config.settings import ROOT
from src.data_pipeline.ssi_live_store import atomic
from src.monitoring.ssi_live import SSILive
from scripts.ssi_checkpoint import SSICheckpoints

REPOSITORY='TranThanhPhu39/neo-liquiditylab'
LOCAL=ZoneInfo('Asia/Ho_Chi_Minh')


def policy(root=ROOT):
    p=json.loads((Path(root)/'config/ssi_github_schedule.json').read_bytes())
    if p['repository']!=REPOSITORY or p['symbols']!=['ACB','FPT','HPG'] or p['checkpoint_seconds']!=900:
        raise ValueError('SSI_INVALID_CLOUD_POLICY')
    return p


def plan(now,session,p):
    if now.tzinfo is None:raise ValueError('SSI_AWARE_CLOCK_REQUIRED')
    now=now.astimezone(LOCAL);day=now.date().isoformat()
    if session not in p['sessions']:raise ValueError('SSI_UNKNOWN_SESSION')
    windows=p['sessions'][session]
    at=lambda key:datetime.fromisoformat(day+'T'+windows[key]+':00').replace(tzinfo=LOCAL)
    return dict(day=day,session=session,capture_from=at('capture_from').isoformat(),coverage_from=at('coverage_from').isoformat(),
        close=at('close').isoformat(),closed=now>=at('close'),late=now>at('coverage_from'),
        non_trading_date=now.weekday()>=5 or day in p.get('excluded_dates',[]))


def network_probe():
    rows=[]
    for host in ('api.ssi.com.vn','stream.ssi.com.vn'):
        try:
            socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
            with socket.create_connection((host,443),timeout=10) as tcp:
                with ssl.create_default_context().wrap_socket(tcp,server_hostname=host) as tls:
                    rows.append(dict(host=host,dns=True,tcp=True,tls=True,tls_version=tls.version()))
        except Exception as error:rows.append(dict(host=host,state='FAILED',error_code=type(error).__name__))
    return dict(state='NETWORK_PREFLIGHT_PASSED' if all(r.get('tls') for r in rows) else 'FAILED_NETWORK_PREFLIGHT',hosts=rows,
                credentials_used=False,market_data_access_proven=False)


def outcome(status,window,interrupted=False):
    if interrupted:return 'FAILED_INTERRUPTED'
    if status['state']=='FAILED':return 'FAILED_SSI_COLLECTION'
    if not status['quotes'] or not status['trades']:return 'FAILED_NO_COMPLETE_MARKET_DATA_PROOF'
    if any(status['quote_counts'].get(s,0)==0 for s in ('ACB','FPT','HPG')):return 'FAILED_INCOMPLETE_SYMBOLS'
    if status['rejected']:return 'FAILED_DATA_QUARANTINE'
    late=window and (window['late'] or not status.get('first_connected_at') or
        datetime.fromisoformat(status['first_connected_at'])>datetime.fromisoformat(window['coverage_from']))
    if late:return 'PARTIAL_LATE_START'
    if status['gaps']:return 'PARTIAL_CONNECTION_GAPS'
    return 'COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED' if window else 'SMOKE_ENCRYPTED_AND_RESTORE_VERIFIED'


def run(mode='smoke',session='morning',seconds=60,*,root=ROOT):
    root=Path(root).resolve();report=root/'report/github/ssi_realtime_result.json';report.parent.mkdir(parents=True,exist_ok=True)
    if os.getenv('GITHUB_REPOSITORY')!=REPOSITORY or os.getenv('GITHUB_REF')!='refs/heads/main':
        raise ValueError('SSI_UNEXPECTED_REPOSITORY_OR_REF')
    if mode=='network':
        result=network_probe();atomic(report,result);print(json.dumps(result),flush=True);return result
    if mode not in ('smoke','collect'):raise ValueError('SSI_UNKNOWN_MODE')
    if os.getenv('SSI_REALTIME_STORAGE_APPROVED')!='true':raise ValueError('SSI_ENCRYPTED_STORAGE_APPROVAL_REQUIRED')
    if mode=='collect' and os.getenv('SSI_REALTIME_ENABLED')!='true':raise ValueError('SSI_SCHEDULE_NOT_ENABLED')
    if not all(os.getenv(k) for k in ('SSI_API_KEY','SSI_API_SECRET','LIQUIDITYLAB_STATE_KEY')):
        raise ValueError('SSI_REQUIRED_SECRETS_MISSING')
    key=base64.b64decode(os.environ['LIQUIDITYLAB_STATE_KEY'],validate=True)
    if len(key)!=32:raise ValueError('SSI_STATE_KEY_REQUIRES_32_BYTES')
    p=policy(root);window=None
    if mode=='collect':
        if os.getenv('GITHUB_EVENT_NAME')=='schedule':
            session=p['schedule_utc'].get(os.getenv('SSI_SCHEDULE_CRON'))
        window=plan(datetime.now(LOCAL),session,p)
        if window['non_trading_date']:
            result=dict(state='NOT_SCHEDULED_NON_TRADING_DATE',plan=window);atomic(report,result);return result
        if window['closed']:
            result=dict(state='FAILED_SESSION_WINDOW_ALREADY_CLOSED',plan=window,realtime_recovered=False);atomic(report,result);return result
        start=datetime.fromisoformat(window['capture_from'])
        while datetime.now(LOCAL)<start:time.sleep(min(5,(start-datetime.now(LOCAL)).total_seconds()))
        window=plan(datetime.now(LOCAL),session,p)
        seconds=max(0,int((datetime.fromisoformat(window['close'])-datetime.now(LOCAL)).total_seconds()))
        if seconds<10:
            result=dict(state='FAILED_SESSION_WINDOW_ALREADY_CLOSED',plan=window);atomic(report,result);return result
    elif type(seconds) is not int or not 10<=seconds<=120:raise ValueError('SSI_INVALID_SMOKE_DURATION')
    service=SSILive(root);store=SSICheckpoints(REPOSITORY,key)
    run_id=os.environ['GITHUB_RUN_ID'];attempt=os.getenv('GITHUB_RUN_ATTEMPT','1');stopped=False;checkpoints=[];storage_errors=[]
    def terminate(*args):
        nonlocal stopped
        stopped=True;service.stop()
    if __import__('threading').current_thread() is __import__('threading').main_thread():
        signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    service.start(p['symbols'],seconds)
    result=dict(state='COLLECTING',mode=mode,session=session,plan=window,realtime_backfill_available=False,
        capture_run_id=service.journal.run_id,checkpoint_seconds=p['checkpoint_seconds'])
    next_checkpoint=time.monotonic()+p['checkpoint_seconds'];last_log=0
    atomic(report,result)
    while service.thread.is_alive():
        service.thread.join(timeout=1)
        if stopped:service.stop()
        if time.monotonic()-last_log>=60:
            s=service.status();print(json.dumps({k:s[k] for k in ('state','quotes','trades','gaps','rejected','persisted_records')}),flush=True);last_log=time.monotonic()
        if time.monotonic()>=next_checkpoint:
            try:checkpoints.append(store.publish(service,session,run_id,attempt))
            except Exception as error:storage_errors.append(type(error).__name__)
            next_checkpoint=time.monotonic()+p['checkpoint_seconds']
    status=service.status();result.update(state=outcome(status,window,stopped),capture=status,checkpoint_errors=storage_errors)
    try:
        final=store.publish(service,session,run_id,attempt,final=True)
        result.update(final_checkpoint=final,temporary_checkpoints_superseded=len(checkpoints))
    except Exception as error:result.update(state='FAILED_ENCRYPTED_STORAGE',error_code=type(error).__name__)
    atomic(report,result)
    print(json.dumps(result),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['network','smoke','collect'],default='smoke')
    parser.add_argument('--session',choices=['morning','afternoon'],default='morning')
    parser.add_argument('--seconds',type=int,default=60);a=parser.parse_args()
    try:r=run(a.mode,a.session,a.seconds)
    except Exception as error:
        message=str(error)
        import re
        r=dict(state='FAILED_SSI_CLOUD_WORKER',error_code=message if re.fullmatch('[A-Z0-9_]{1,90}',message) else type(error).__name__)
        path=ROOT/'report/github/ssi_realtime_result.json';path.parent.mkdir(parents=True,exist_ok=True);atomic(path,r)
        print(json.dumps(r),flush=True)
    return 0 if r['state'] in ('NETWORK_PREFLIGHT_PASSED','NOT_SCHEDULED_NON_TRADING_DATE','COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED','SMOKE_ENCRYPTED_AND_RESTORE_VERIFIED') else 1


if __name__=='__main__':raise SystemExit(main())
