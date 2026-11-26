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
    days=[];completed=[];pages_pending=False
    prior_path=root/'report/automation/dnse_local_schedule_result.json'
    if prior_path.exists():pages_pending=bool(json.loads(prior_path.read_bytes()).get('pages_publication_pending'))
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
                    pages_pending=pages_pending or not any(d.get('date')==selected for d in index.get('days',[]))
                (completed if is_complete else days).append(selected)
            day+=timedelta(days=1)
    return dict(state='READY' if config['enabled'] else 'DISABLED',now_local=now.isoformat(),
                due_dates=days,already_completed=completed,publish_web=config['publish_web'],
                pages_publication_pending=pages_pending,
                lookback_days=config['lookback_days'],start_date=config['start_date'],
                note='Weekdays only; configure excluded_dates for exchange holidays. Lookback is a local recovery policy, not a provider retention guarantee.')


def run(root=ROOT,now=None):
    root=Path(root)
    with exclusive_job(root/'data/control/dnse_local_schedule.lock'):
        result=plan(root,now);result['runs']=[]
        for day in result['due_dates']:
            if (root/'data/control/STOP_DNSE_HISTORY').exists():
                result.update(state='FAILED_RESUMABLE',error_code='REST_STOP_REQUESTED');break
            # Collection must not wait behind a failed upload for an older date.
            outcome=update_dnse_day.run(day,root=root,session='day',export=False,publish_web=False,now=now)
            result['runs'].append(dict(date=day,state=outcome['state'],error_code=outcome.get('error_code')))
            if outcome['state']!='COMPLETED':
                result['state']='FAILED_RESUMABLE'
        else:
            if result['state']!='FAILED_RESUMABLE':result['state']='COMPLETED' if result['state']!='DISABLED' else 'DISABLED'
        newly_audited = any(item['state']=='COMPLETED' for item in result['runs'])
        has_audited_days = result['already_completed'] or newly_audited
        can_publish = (result['state'] != 'DISABLED'
                       and result.get('error_code') != 'REST_STOP_REQUESTED'
                       and bool(has_audited_days))
        if can_publish:
            def publication_failed(error):
                code=str(error)
                result.update(state='FAILED_RESUMABLE',error_code=code if re.fullmatch('[A-Z0-9_]{1,90}',code) else type(error).__name__)
            try:
                if result['publish_web'] and (newly_audited or result['pages_publication_pending']):
                    result['pages_publication_pending']=True
                    write_json(root/'report/automation/dnse_local_schedule_result.json',result)
                    from scripts.publish_dnse_web import publish_samples
                    result['pages_publication']=publish_samples(root)
                    result['pages_publication_pending']=False
            except Exception as error:
                publication_failed(error)
            # Pages and the private monitor are independent destinations. A
            # failed public sample upload must not hold back audited replay.
            try:
                from scripts.publish_daily_monitor import run as publish_monitor
                result['monitor_publication']=publish_monitor(root)
                if result['monitor_publication']['state'] not in ('COMPLETED','DISABLED'):
                    result.update(state='FAILED_RESUMABLE',error_code='MONITOR_PUBLICATION_PENDING')
            except Exception as error:
                publication_failed(error)
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
