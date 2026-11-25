"""Windowless task entry: durable start/finish and safe daily operational logs."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import json

from config.settings import ROOT
from scripts.github_ssi_realtime import LOCAL
from scripts.scheduled_cloud_guard import run, error_code
from scripts.scheduled_collection import write_json


def main(root=ROOT, runner=run):
    folder=root/'report/automation/logs'
    folder.mkdir(parents=True,exist_ok=True)
    now=datetime.now(LOCAL)
    log=folder/('cloud_guard_'+now.date().isoformat()+'.log')
    target=root/'report/automation/cloud_dispatch_entry.json'
    entry=dict(state='RUNNING',started_at_local=now.isoformat(),log=str(log.relative_to(root)))
    write_json(target,entry)
    with log.open('a',encoding='utf-8',buffering=1) as stream, redirect_stdout(stream), redirect_stderr(stream):
        print(json.dumps(entry),flush=True)
        try:result=runner(root=root)
        except Exception as error:result=dict(state='FAILED_RESUMABLE',error_code=error_code(error))
        entry.update(state=result['state'],finished_at_local=datetime.now(LOCAL).isoformat(),result=result)
        write_json(target,entry)
        print(json.dumps(entry),flush=True)
    return 1 if result['state'] in ('FAILED_RESUMABLE','ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START') else 0


if __name__=='__main__':raise SystemExit(main())
