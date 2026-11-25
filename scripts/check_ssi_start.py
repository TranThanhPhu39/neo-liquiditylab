"""Read public collector telemetry; a dispatch or queued job is never data proof."""
from datetime import datetime
import json
from urllib.request import urlopen

from scripts.github_ssi_realtime import LOCAL, plan, policy

ORIGIN='https://neo-liquiditylab-monitor.onrender.com'


def evaluate(status, now, session, p):
    window=plan(now,session,p)
    result=dict(state='WORKER_START_NOT_VERIFIED',session=session,day=window['day'],
                deadline_passed=now>=datetime.fromisoformat(window['coverage_from']),
                worker_start_verified=False,market_coverage_verified=False,realtime_backfill_available=False)
    stamp=status.get('first_connected_at')
    if not stamp or not status.get('active'):return result
    first=datetime.fromisoformat(stamp)
    if first.tzinfo is None:return result
    first=first.astimezone(LOCAL)
    if not datetime.fromisoformat(window['capture_from'])<=first<datetime.fromisoformat(window['close']) or first>now:return result
    age=status.get('delivery_age_seconds')
    if not isinstance(age,(int,float)) or not 0<=age<=30:return result
    if status.get('state') not in ('RECEIVING','CONNECTED_WAITING_DATA'):return result
    observed=all(status.get('quote_counts',{}).get(s,0)>0 for s in ('ACB','FPT','HPG'))
    result.update(state='RECEIVING_MARKET_DATA' if observed else 'CONNECTED_WAITING_MARKET_DATA',
                  worker_start_verified=True,first_connected_at=stamp,quotes_observed_for_all_symbols=observed,
                  late=first>datetime.fromisoformat(window['coverage_from']),run_id=status.get('run_id'))
    return result


def check(now,session,*,root,get=None):
    def fetch():
        # Fixed owner-approved public endpoint. No GitHub/SSI credentials sent.
        with urlopen(ORIGIN+'/api/live/status',timeout=10) as response:
            raw=response.read(256*1024+1)
        if len(raw)>256*1024:raise ValueError('SSI_START_TELEMETRY_TOO_LARGE')
        return json.loads(raw)
    try:return evaluate((get or fetch)(),now,session,policy(root))
    except (OSError,ValueError,TypeError,KeyError):
        return dict(state='START_TELEMETRY_UNAVAILABLE',worker_start_verified=False,
                    market_coverage_verified=False,session=session,
                    deadline_passed=now>=datetime.fromisoformat(plan(now,session,policy(root))['coverage_from']))
