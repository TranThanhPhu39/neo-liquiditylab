"""Only a completed full-day REST backfill may enter the automatic public pipeline."""
import json
from pathlib import Path

from config.settings import ROOT
from scripts.publish_dnse_web import auto_enabled
from scripts.prepare_dnse_web_rules import restore
from scripts.update_dnse_day import run,complete_archive
from scripts.scheduled_collection import write_json


def publish_completed(root=ROOT):
    root=Path(root)
    if not auto_enabled(root):
        return dict(state='SKIPPED_PUBLICATION_DISABLED')
    if json.loads((root/'config/web_publication.json').read_text(encoding='utf-8')).get('github_enabled') is not True:
        return dict(state='SKIPPED_CLOUD_PUBLICATION_NOT_PROVISIONED')
    status=json.loads((root/'report/github/dnse_realtime_result.json').read_text(encoding='utf-8'))
    backfill=status.get('backfill',status if status.get('purpose')=='REST_HISTORY_BACKFILL' else {})
    if status.get('state','').startswith('FAILED') or backfill.get('state')!='REST_BACKFILL_COMPLETED':
        return dict(state='SKIPPED_NO_COMPLETED_REST_HISTORY')
    if backfill.get('session')!='day':
        return dict(state='SKIPPED_WAITING_FOR_FULL_DAY')
    day=backfill['date_local']
    if not complete_archive(root/f'data/history/dnse/{day}/history.db',day,'day'):
        raise ValueError('PUBLICATION_REQUIRES_COMPLETE_DAY_ARCHIVE')
    restore(root)
    result=run(day,root=root,export=False,publish_web=True)
    if result['state']!='COMPLETED':
        raise ValueError(result.get('error_code','WEB_PIPELINE_FAILED'))
    return dict(state='PUBLIC_SAMPLES_PUSHED',date_local=day,publication=result['web_publication'])


def main():
    try:
        result=publish_completed()
    except Exception as error:
        from scripts.github_dnse_backfill import safe_error
        result=dict(state='FAILED_WEB_PUBLICATION',error_code=safe_error(error))
    write_json(ROOT/'report/github/dnse_web_publication.json',result)
    print(json.dumps(result),flush=True)
    return 1 if result['state'].startswith('FAILED') else 0


if __name__=='__main__':
    raise SystemExit(main())
