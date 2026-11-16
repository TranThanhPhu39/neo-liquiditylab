"""Retry a missed SSI session start without launching duplicate collectors."""
from datetime import datetime,timedelta
import json
import os

from scripts.github_api import GitHubAPI,repository_path
from scripts.github_ssi_realtime import LOCAL,REPOSITORY,policy,plan


def guard(now,api,root=None):
    from config.settings import ROOT
    if now.tzinfo is None:raise ValueError('SSI_GUARD_AWARE_CLOCK_REQUIRED')
    now=now.astimezone(LOCAL);p=policy(root or ROOT);base=repository_path(REPOSITORY)
    if now.weekday()>=5 or now.date().isoformat() in p.get('excluded_dates',[]):return dict(state='NON_TRADING_DATE')
    session=next((s for s in p['sessions'] if datetime.fromisoformat(plan(now,s,p)['capture_from'])-timedelta(minutes=15)<=now<datetime.fromisoformat(plan(now,s,p)['close'])),None)
    if not session:return dict(state='OUTSIDE_COLLECTION_WINDOW')
    for name in ('SSI_REALTIME_ENABLED','SSI_REALTIME_STORAGE_APPROVED'):
        if api.request('GET',base+'/actions/variables/'+name)['value']!='true':return dict(state='COLLECTION_DISABLED')
    runs=api.request('GET',base+'/actions/workflows/ssi-realtime.yml/runs?per_page=30')['workflow_runs']
    # Treat a queued run as active too; repeated guard ticks must not queue copies.
    active=[r for r in runs if r['status'] in ('queued','in_progress','waiting','pending','requested')]
    if active:return dict(state='COLLECTOR_ALREADY_ACTIVE',run_ids=[r['id'] for r in active],session=session)
    start=datetime.fromisoformat(plan(now,session,p)['capture_from'])-timedelta(minutes=15)
    attempts=[r for r in runs if start<=datetime.fromisoformat(r['created_at']).astimezone(LOCAL)<=now and 'smoke' not in r.get('display_title','').lower() and 'network' not in r.get('display_title','').lower()]
    if any(r.get('conclusion')=='success' for r in attempts):return dict(state='SESSION_ALREADY_COMPLETED',session=session)
    if len(attempts)>=2:return dict(state='RETRY_LIMIT_REACHED',session=session)
    if attempts and now-max(datetime.fromisoformat(r.get('updated_at',r['created_at'])).astimezone(LOCAL) for r in attempts)<timedelta(minutes=10):
        return dict(state='RETRY_COOLDOWN',session=session)
    api.request('POST',base+'/actions/workflows/ssi-realtime.yml/dispatches',{'ref':'main','inputs':{'mode':'collect','session':session,'seconds':'60'}})
    return dict(state='MISSED_SESSION_START_DISPATCHED',session=session,day=now.date().isoformat(),
                late=plan(now,session,p)['late'],realtime_backfill_available=False)


if __name__=='__main__':
    if os.getenv('GITHUB_REPOSITORY')!=REPOSITORY or os.getenv('GITHUB_REF')!='refs/heads/main':raise ValueError('SSI_GUARD_REPOSITORY_REQUIRED')
    print(json.dumps(guard(datetime.now(LOCAL),GitHubAPI())))
