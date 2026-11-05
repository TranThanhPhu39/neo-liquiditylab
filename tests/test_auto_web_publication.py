from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from test_dnse_normalization import prepare_fixture,quote
from test_github_dnse_backfill import Store
from scripts import publish_dnse_web as web, publish_dnse_backfill_web as cloud
from scripts import prepare_dnse_web_rules as rules
from scripts.export_web_data import export_data
from scripts.normalize_dnse_history import prepare_rules
from src.data_pipeline.dnse_normalization import load_rules


def policy(root):
    (root/'config').mkdir(exist_ok=True)
    (root/'config/web_publication.json').write_text(json.dumps(dict(enabled=True,github_enabled=True,
        repository='TranThanhPhu39/neo-liquiditylab',source='AUDITED_DNSE_REST_SAMPLES',raw_or_credentials=False)))


class API:
    def __init__(self):
        self.calls=[]
        self.paths=[]
    def request(self,method,path,payload=None):
        self.calls.append((method,path,payload))
        if path=='/repos/TranThanhPhu39/neo-liquiditylab':
            return dict(private=False,default_branch='main',permissions={'admin':False})
        if method=='GET' and '/git/ref/' in path:return {'object':{'sha':'old-head'}}
        if method=='GET' and '/git/commits/' in path:return {'tree':{'sha':'old-tree'}}
        if method=='GET' and '/git/trees/' in path:return {'tree':[]}
        if method=='POST' and path.endswith('/git/blobs'):return {'sha':'fixture-blob'}
        if method=='POST' and path.endswith('/git/trees'):
            self.paths=[x['path'] for x in payload['tree']]
            return {'sha':'new-tree'}
        if method=='POST' and path.endswith('/git/commits'):return {'sha':'new-head'}
        if method=='PATCH' and '/git/refs/' in path:return {}
        if method=='POST' and path.endswith('/dispatches'):return None
        pytest.fail('Unexpected API operation: '+method+' '+path)


def fixture_day(root,quote):
    path=prepare_fixture(root,quote)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO metadata VALUES('universe_complete','true')")
        rows=[dict(symbol=s,exchange=e,market_id=m) for s,e,m in db.execute('SELECT symbol,exchange,market_id FROM symbols')]
    db.close()  # Close WAL before the immutable-archive checksum is taken.
    (path.parent/'universe.json').write_text(json.dumps({'rows':rows}))
    policy(root)
    (root/'report/github').mkdir()
    (root/'report/github/dnse_realtime_result.json').write_text(json.dumps(dict(
        purpose='REST_HISTORY_BACKFILL',state='REST_BACKFILL_COMPLETED',session='day',date_local='2026-10-28')))
    return path


def test_completed_cloud_day_runs_real_normalize_audit_and_data_only_publication(tmp_path,quote,monkeypatch):
    fixture_day(tmp_path,quote)
    api=API()
    monkeypatch.setattr(web,'GitHubAPI',lambda:api)
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setattr(cloud,'restore',lambda root:load_rules(prepare_rules(root)))
    # Unrelated source and .env must never be staged by automatic data publication.
    (tmp_path/'.env').write_text('PRIVATE_FIXTURE_SECRET')
    result=cloud.publish_completed(tmp_path)
    assert result['state']=='PUBLIC_SAMPLES_PUSHED'
    assert api.paths and all(p.startswith('web/data/') for p in api.paths)
    assert any(path.endswith('/dashboard-pages.yml/dispatches') for _,path,_ in api.calls)
    assert not any(path.endswith('/pages') for _,path,_ in api.calls)
    manifest=json.loads((tmp_path/'data/processed/dnse/manifest.json').read_text())
    assert manifest['state']=='NORMALIZED_AND_AUDITED' and manifest['quarantined']==0
    assert manifest['finance_checks']['state']=='PASSED'
    market_index=json.loads((tmp_path/'web/data/index.json').read_text())
    data=json.loads(gzip.decompress((tmp_path/'web/data'/market_index['days'][0]['market']['path']).read_bytes()))
    assert {r['symbol'] for r in data['records']}=={'ACB','FPT'}
    assert next(r for r in data['records'] if r['symbol']=='FPT')['financial_eligible'] is False
    assert 'EMG' in data['catalog']
    # A stale success manifest cannot authorize publishing a subsequently changed DB.
    database=tmp_path/'data/processed/dnse/normalized.db'
    with database.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='AUDITED_DATABASE_CHANGED'):
        web.publish_samples(tmp_path,api=api)


@pytest.mark.parametrize('state,session', [('FAILED_REST_BACKFILL','day'),('REST_BACKFILL_COMPLETED','morning')])
def test_failed_or_morning_collection_never_publishes(tmp_path,monkeypatch,state,session):
    policy(tmp_path)
    p=tmp_path/'report/github';p.mkdir(parents=True)
    (p/'dnse_realtime_result.json').write_text(json.dumps(dict(purpose='REST_HISTORY_BACKFILL',state=state,session=session)))
    monkeypatch.setattr(cloud,'restore',lambda *a:pytest.fail('Must not restore or publish'))
    assert cloud.publish_completed(tmp_path)['state'].startswith('SKIPPED')


def test_policy_off_by_default_and_wrong_destination_rejected(tmp_path):
    assert not web.auto_enabled(tmp_path)
    policy(tmp_path)
    assert web.auto_enabled(tmp_path)
    p=tmp_path/'config/web_publication.json'
    value=json.loads(p.read_text());value['repository']='someone/else'
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='POLICY_INVALID'):web.auto_enabled(tmp_path)


def test_cloud_unprovisioned_does_not_read_private_state_or_publish(tmp_path,monkeypatch):
    policy(tmp_path)
    p=tmp_path/'config/web_publication.json'
    value=json.loads(p.read_text());value['github_enabled']=False
    p.write_text(json.dumps(value))
    monkeypatch.setattr(cloud,'restore',lambda *a:pytest.fail('Not authorized/provisioned'))
    assert cloud.publish_completed(tmp_path)['state']=='SKIPPED_CLOUD_PUBLICATION_NOT_PROVISIONED'


def test_preserve_public_history_when_runner_has_no_older_archives(tmp_path,quote):
    from scripts.normalize_dnse_history import run
    from src.data_pipeline.dnse_history import DNSEHistory
    prepare_fixture(tmp_path,quote);normalized=run(root=tmp_path)
    store=DNSEHistory(tmp_path/'data/processed/dnse/normalized.db')
    out=tmp_path/'web/data'
    export_data(store,out,normalized['rule_hash'])
    before=json.loads((out/'index.json').read_text())['days']
    store.dates=lambda:[]
    export_data(store,out,normalized['rule_hash'],preserve_previous=True)
    assert json.loads((out/'index.json').read_text())['days']==before
    with pytest.raises(ValueError,match='PUBLIC_RULE_CHANGE'):
        export_data(store,out,'unreviewed-new-rule',preserve_previous=True)


def test_encrypted_rules_bootstrap_roundtrip_and_path_scope(tmp_path,quote):
    source=tmp_path/'local';source.mkdir();prepare_fixture(source,quote)
    store=Store(tmp_path/'releases')
    prepared=rules.prepare(source,store=store)
    restored=rules.restore(tmp_path/'runner',store=store)
    assert prepared['rule_hash']==restored['rule_hash']
    assert load_rules(prepare_rules(tmp_path/'runner')).verified_symbols==('ACB',)
    with pytest.raises(ValueError,match='PATH_INVALID'):
        rules.raw_path(source,{'api_artifact':{'path':'../../.env'}})


def test_secret_in_evidence_never_enters_release(tmp_path,quote,monkeypatch):
    prepare_fixture(tmp_path,quote)
    monkeypatch.setenv('DNSE_API_KEY','EMPIRICALLY_VERIFIED_FOR_ACB_G1')
    with pytest.raises(ValueError,match='CONTAIN_SECRET'):
        rules.prepare(tmp_path,store=Store(tmp_path/'releases'))


def test_local_update_defaults_follow_policy_without_requiring_flag(tmp_path,quote,monkeypatch):
    from scripts import update_dnse_day
    fixture_day(tmp_path,quote)
    api=API();monkeypatch.setattr(web,'GitHubAPI',lambda:api)
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    result=update_dnse_day.run('2026-10-28',root=tmp_path,export=False)
    assert result['state']=='COMPLETED' and 'publish_web' in result['completed_steps']
    assert api.paths and not any(path.endswith('/dispatches') for _,path,_ in api.calls)


def test_on_time_window_also_prepares_rest_for_daily_publication(tmp_path,monkeypatch):
    from test_github_dnse_backfill import configure
    from scripts import github_dnse_realtime as runner
    configure(monkeypatch,tmp_path,8,57)
    store=Store(tmp_path/'releases')
    monkeypatch.setattr(runner,'ReleaseStore',lambda *a,**kw:store)
    monkeypatch.setattr(runner,'load_seed',lambda _:({'count':3,'as_of_date':'2026-11-04'},'fixture'))
    async def collected(*args,**kw):return dict(state='WINDOW_ENDED_OBSERVED_ONLY',receipt_count=2)
    monkeypatch.setattr(runner,'collect',collected)
    monkeypatch.setattr(runner,'recover_session',lambda *a,**kw:dict(state='REST_BACKFILL_COMPLETED'))
    result=runner.run('collect')
    assert result['state']=='COLLECTED_AND_REST_BACKFILLED' and runner.exit_code(result)==0
    assert not result['realtime_window_missed'] and result['realtime_result']['receipt_count']==2
