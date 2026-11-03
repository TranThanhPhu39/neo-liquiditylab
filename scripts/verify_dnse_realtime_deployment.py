"""Verify two actual smoke runners and decrypted continuity before enabling the schedule."""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid

from config.settings import ROOT
from scripts.github_api import GitHubAPI, repository_path
from scripts.github_checkpoint import ReleaseStore
from scripts.github_dnse_realtime import REPOSITORY
from scripts.prepare_dnse_realtime import seed_key
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_realtime_store import audit
from src.models.orderbook import LOCAL_TZ


def verify(first_id, second_id, enable=False):
    reports=[json.loads((ROOT/f'report/github/run_{n}.json').read_text(encoding='utf-8'))
             for n in (first_id,second_id)]
    results=[]
    for report in reports:
        if report['status']!='completed' or report['conclusion']!='success':
            raise ValueError('WS_RUN_NOT_SUCCESSFUL')
        jobs=[j for j in report['jobs'] if j['name']=='collector' and j['conclusion']=='success']
        if len(jobs)!=1:
            raise ValueError('WS_COLLECTOR_JOB_NOT_FOUND')
        value=jobs[0]['worker_summaries'][-1]
        if (value.get('state')!='SMOKE_FINISHED' or value.get('shards_subscribed')!=value.get('expected_connections')
                or value.get('expected_connections',0)<1 or value.get('fatal_error')
                or value.get('checkpoints_saved',0)<2 or value.get('unpersisted_receipts_this_run')!=0
                or value.get('audit',{}).get('state')!='VERIFIED'):
            raise ValueError('WS_SMOKE_CHECKS_FAILED')
        results.append({'github_run_id':report['id'],'runner_id':jobs[0]['runner_id'],**value})
    first,second=results
    if first['runner_id']==second['runner_id'] or first['run_id']==second['run_id']:
        raise ValueError('WS_DISTINCT_RUNNERS_REQUIRED')
    if first['date_local']!=second['date_local'] or first['seed_checkpoint']!=second['seed_checkpoint']:
        raise ValueError('WS_SMOKE_SCOPE_MISMATCH')
    restored=second.get('restored_checkpoint') or ''
    if f'-{first_id}-' not in restored:
        raise ValueError('WS_FIRST_RUN_CHECKPOINT_NOT_RESTORED')
    folder=ROOT/'data/realtime/dnse/deployment-verification'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    day=second['date_local']
    tag=ReleaseStore(REPOSITORY,seed_key(),namespace='dnse-ws-state').restore(
        day+'-smoke',folder/'snapshot.db',folder/'reports')
    if not tag or f'-{second_id}-' not in tag:
        raise ValueError('WS_SECOND_RUN_CHECKPOINT_NOT_FOUND')
    checked=audit(folder/'snapshot.db')
    with closing(sqlite3.connect((folder/'snapshot.db').as_uri()+'?mode=ro',uri=True)) as db:
        for value in results:
            row=db.execute('SELECT state FROM runs WHERE id=?',(value['run_id'],)).fetchone()
            count=db.execute('SELECT count(DISTINCT shard) FROM connections WHERE run_id=? AND subscribed_at IS NOT NULL',
                             (value['run_id'],)).fetchone()[0]
            if not row or row[0]!='SMOKE_FINISHED' or count!=value['expected_connections']:
                raise ValueError('WS_CHECKPOINT_RUN_CONTINUITY_FAILED')
        symbols=db.execute('SELECT count(*) FROM symbols').fetchone()[0]
    api=GitHubAPI(); base=repository_path(REPOSITORY)
    flags={v['name']:v['value'] for v in api.request('GET',base+'/actions/variables')['variables']}
    if flags.get('DNSE_REALTIME_STORAGE_APPROVED')!='true':
        raise ValueError('WS_ENCRYPTED_STORAGE_NOT_APPROVED')
    if enable:
        name='DNSE_REALTIME_ENABLED'
        api.request('PATCH' if name in flags else 'POST',
                    base+'/actions/variables/'+name if name in flags else base+'/actions/variables',
                    {'name':name,'value':'true'})
    flags={v['name']:v['value'] for v in api.request('GET',base+'/actions/variables')['variables']}
    result={'state':'VERIFIED_TWO_RUNNERS','checked_at_local':datetime.now(LOCAL_TZ).isoformat(),
            'runners':results,'restored_final_tag':tag,'audit':checked,'catalog_symbols':symbols,
            'decrypted_snapshot':str(folder/'snapshot.db'),
            'schedule_enabled':flags.get('DNSE_REALTIME_ENABLED')=='true',
            'storage_approved':flags.get('DNSE_REALTIME_STORAGE_APPROVED')=='true',
            'legacy_vnstock_enabled':flags.get('LIQUIDITYLAB_WORKER_ENABLED')=='true',
            'in_session_full_market_validation':'PENDING_FIRST_SCHEDULED_SESSION',
            'websocket_units_verified':False}
    write_json(ROOT/'report/dnse_realtime_github_activation_2026-11-03.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('first_run',type=int)
    parser.add_argument('second_run',type=int)
    parser.add_argument('--enable',action='store_true')
    args=parser.parse_args()
    value=verify(args.first_run,args.second_run,args.enable)
    print(json.dumps({k:v for k,v in value.items() if k not in ('runners','decrypted_snapshot')}))
