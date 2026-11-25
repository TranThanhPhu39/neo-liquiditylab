"""Retry a missed SSI session start without launching duplicate collectors."""
from datetime import datetime,timedelta
import json
import os

from scripts.github_api import GitHubAPI,repository_path
from scripts.github_ssi_realtime import LOCAL,REPOSITORY,policy,plan


ACTIVE = {'queued','in_progress','waiting','pending','requested'}
QUEUED = {'queued','waiting','pending','requested'}


def run_session(run, p):
    title = run.get('display_title','').lower()
    if any(mode in title for mode in ('smoke','network')):
        return 'other'
    for session in p['sessions']:
        if session in title:return session
    for cron, session in p['schedule_utc'].items():
        if cron in title:return session
    return None


def guard(now,api,root=None,flags=None):
    from config.settings import ROOT
    if now.tzinfo is None:raise ValueError('SSI_GUARD_AWARE_CLOCK_REQUIRED')
    now=now.astimezone(LOCAL);p=policy(root or ROOT);base=repository_path(REPOSITORY)
    if now.weekday()>=5 or now.date().isoformat() in p.get('excluded_dates',[]):return dict(state='NON_TRADING_DATE')
    lead=p.get('guard_lead_minutes',35)
    session=next((s for s in p['sessions'] if datetime.fromisoformat(plan(now,s,p)['capture_from'])-timedelta(minutes=lead)<=now<datetime.fromisoformat(plan(now,s,p)['close'])),None)
    if not session:return dict(state='OUTSIDE_COLLECTION_WINDOW')
    for name in ('SSI_REALTIME_ENABLED','SSI_REALTIME_STORAGE_APPROVED'):
        value=flags.get(name) if flags is not None else api.request('GET',base+'/actions/variables/'+name)['value']
        if value!='true':return dict(state='COLLECTION_DISABLED')
    runs=api.request('GET',base+'/actions/workflows/ssi-realtime.yml/runs?per_page=30')['workflow_runs']
    # An unstarted collector from a closed session must not hold the global
    # concurrency group forever. Never cancel a running writer or unknown run.
    cancelled=[]
    session_start=datetime.fromisoformat(plan(now,session,p)['capture_from'])
    same_session_attempts=[r for r in runs if run_session(r,p)==session and r.get('created_at') and
        datetime.fromisoformat(r['created_at']).astimezone(LOCAL).date()==now.date()]
    for r in runs:
        selected=run_session(r,p)
        if r['status'] not in QUEUED or selected in (None,'other') or not r.get('created_at'):continue
        created=datetime.fromisoformat(r['created_at']).astimezone(LOCAL)
        expired=created.date()<now.date() or (created.date()==now.date() and plan(now,selected,p)['closed'])
        stalled=(selected==session and now>=session_start and now-created>=timedelta(minutes=15)
                 and len(same_session_attempts)<2)
        if (expired or stalled) and r.get('head_branch','main')=='main':
            api.request('POST',base+'/actions/runs/'+str(r['id'])+'/cancel')
            cancelled.append(r['id'])
    active=[r for r in runs if r['status'] in ACTIVE and r['id'] not in cancelled and run_session(r,p)!='other']
    if active:
        return dict(state='COLLECTOR_ALREADY_ACTIVE',run_ids=[r['id'] for r in active],session=session,
                    queue_only=all(r['status'] in QUEUED for r in active),
                    worker_start_verified=False,market_coverage_verified=False,cancelled_expired_queued_runs=cancelled,
                    stale_queued_run_ids=[r['id'] for r in active if r['status'] in QUEUED and r.get('created_at') and
                        now-datetime.fromisoformat(r['created_at']).astimezone(LOCAL)>timedelta(minutes=15)])
    start=datetime.fromisoformat(plan(now,session,p)['capture_from'])-timedelta(minutes=lead)
    attempts=[r for r in runs if run_session(r,p) in (None,session) and r.get('created_at') and
              start<=datetime.fromisoformat(r['created_at']).astimezone(LOCAL)<=now]
    if any(r.get('conclusion')=='success' for r in attempts):return dict(state='SESSION_ALREADY_COMPLETED',session=session)
    if len(attempts)>=2:return dict(state='RETRY_LIMIT_REACHED',session=session)
    cooldown_attempts=[r for r in attempts if r['id'] not in cancelled]
    if cooldown_attempts and now-max(datetime.fromisoformat(r.get('updated_at',r['created_at'])).astimezone(LOCAL) for r in cooldown_attempts)<timedelta(minutes=10):
        return dict(state='RETRY_COOLDOWN',session=session)
    api.request('POST',base+'/actions/workflows/ssi-realtime.yml/dispatches',{'ref':'main','inputs':{'mode':'collect','session':session,'seconds':'60','collection_date':now.date().isoformat()}})
    return dict(state='MISSED_SESSION_START_DISPATCHED',session=session,day=now.date().isoformat(),
                late=plan(now,session,p)['late'],realtime_backfill_available=False,
                worker_start_verified=False,market_coverage_verified=False,cancelled_expired_queued_runs=cancelled)


def checked_guard(now,api,*,root=None,flags=None,start_check=None):
    from config.settings import ROOT
    from scripts.check_ssi_start import check
    result=guard(now,api,root=root,flags=flags)
    if result.get('session'):
        proof=(start_check or check)(now,result['session'],root=root or ROOT)
        result['start_check']=proof
        if proof.get('deadline_passed') and (not proof.get('worker_start_verified') or proof.get('late') or
                (proof.get('data_start_deadline_passed') and not proof.get('quotes_observed_for_all_symbols'))):
            result['dispatch_state']=result['state']
            result['state']='ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START'
    return result


if __name__=='__main__':
    if os.getenv('GITHUB_REPOSITORY')!=REPOSITORY or os.getenv('GITHUB_REF')!='refs/heads/main':raise ValueError('SSI_GUARD_REPOSITORY_REQUIRED')
    result=checked_guard(datetime.now(LOCAL),GitHubAPI(),flags={name:os.getenv(name) for name in ('SSI_REALTIME_ENABLED','SSI_REALTIME_STORAGE_APPROVED')})
    print(json.dumps(result))
    raise SystemExit(1 if result['state']=='ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START' else 0)
