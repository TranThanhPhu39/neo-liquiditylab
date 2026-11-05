"""Local 15:15 fallback: resume missed closed weekdays and publish audited samples."""
from datetime import date, datetime, time, timedelta
import argparse
import json
from pathlib import Path
import re

from config.settings import ROOT
from scripts import update_dnse_day
from scripts.scheduled_collection import exclusive_job, write_json
from src.models.orderbook import LOCAL_TZ


def policy(root):
    value=json.loads((root/'config/dnse_local_schedule.json').read_text(encoding='utf-8'))
    if not isinstance(value.get('enabled'),bool) or not isinstance(value.get('publish_web'),bool):
        raise ValueError('LOCAL_DNSE_INVALID_POLICY')
    if type(value.get('lookback_days')) is not int or not 1<=value['lookback_days']<=7:
        raise ValueError('LOCAL_DNSE_INVALID_LOOKBACK')
    date.fromisoformat(value['start_date'])
    for day in value.get('excluded_dates',[]):
        date.fromisoformat(day)
    return value


def plan(root=ROOT,now=None):
    root=Path(root);now=now or datetime.now(LOCAL_TZ)
    if now.tzinfo is None:
        raise ValueError('LOCAL_DNSE_TIMEZONE_REQUIRED')
    now=now.astimezone(LOCAL_TZ);config=policy(root)
    first=max(date.fromisoformat(config['start_date']),now.date()-timedelta(days=config['lookback_days']-1))
    days=[];completed=[]
    if config['enabled']:
        day=first
        while day<=now.date():
            close=datetime.combine(day,time(15,15),LOCAL_TZ)
            if day.weekday()<5 and day.isoformat() not in config.get('excluded_dates',[]) and close<=now:
                selected=day.isoformat();report=root/'report'/f'dnse_update_{selected}_day.json'
                previous=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
                raw=root/'data/history/dnse'/selected/'history.db'
                is_complete=(previous.get('state')=='COMPLETED' and
                    all(step in previous.get('completed_steps',[]) for step in update_dnse_day.STEPS) and
                    update_dnse_day.complete_archive(raw,selected,'day'))
                # A completed older run may predate automatic publication. Retry it
                # only if that date is missing from the existing public index.
                if is_complete and config['publish_web']:
                    index_path=root/'web/data/index.json'
                    index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
                    is_complete=any(d.get('date')==selected for d in index.get('days',[]))
                (completed if is_complete else days).append(selected)
            day+=timedelta(days=1)
    return dict(state='READY' if config['enabled'] else 'DISABLED',now_local=now.isoformat(),
                due_dates=days,already_completed=completed,publish_web=config['publish_web'],
                lookback_days=config['lookback_days'],start_date=config['start_date'],
                note='Weekdays only; configure excluded_dates for exchange holidays. Lookback is a local recovery policy, not a provider retention guarantee.')


def run(root=ROOT,now=None):
    root=Path(root)
    with exclusive_job(root/'data/control/dnse_local_schedule.lock'):
        result=plan(root,now);result['runs']=[]
        for day in result['due_dates']:
            if (root/'data/control/STOP_DNSE_HISTORY').exists():
                result.update(state='FAILED_RESUMABLE',error_code='REST_STOP_REQUESTED');break
            outcome=update_dnse_day.run(day,root=root,session='day',export=False,publish_web=result['publish_web'],now=now)
            result['runs'].append(dict(date=day,state=outcome['state'],error_code=outcome.get('error_code')))
            if outcome['state']!='COMPLETED':
                result['state']='FAILED_RESUMABLE';break
        else:
            result['state']='COMPLETED' if result['state']!='DISABLED' else 'DISABLED'
        write_json(root/'report/automation/dnse_local_schedule_result.json',result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',action='store_true');args=parser.parse_args()
    try:
        result=plan() if args.plan else run()
    except (Exception,KeyboardInterrupt) as error:
        code=str(error)
        result=dict(state='FAILED_RESUMABLE',error_code=code if re.fullmatch('[A-Z0-9_]{1,90}',code) else type(error).__name__)
        if not args.plan:
            write_json(ROOT/'report/automation/dnse_local_schedule_result.json',result)
    print(json.dumps(result,ensure_ascii=True))
    return 0 if result['state'] in ('READY','COMPLETED','DISABLED') else 1


if __name__=='__main__':
    raise SystemExit(main())
