import gzip
import hashlib
import json

import pytest

from scripts.collect_dnse_history import connect,initialize,run,store_page

DAY='2026-10-28'
UNIVERSE=[{'symbol':'FPT','marketId':'STO','securityGroupId':'ST'},
          {'symbol':'SHS','marketId':'STX','securityGroupId':'ST'},
          {'symbol':'ACV','marketId':'UPX','securityGroupId':'ST'}]


def observation(symbol='FPT',market='STO',second=1,next_token=None):
    return {'received_at_utc':'2026-10-28T15:00:00+00:00','rate_limit_headers':{},'payload':{
        'quotes':[{'symbol':symbol,'marketId':market,'boardId':'G1','bid':[],'offer':[],
                   'time':f'2026-10-28 09:15:{second:02d}.001'}], 'nextPageToken':next_token}}


class FixtureClient:
    def __init__(self):
        self.calls=[]
    def get(self,path,params):
        self.calls.append((path,dict(params)))
        if path=='/instruments':
            return {'payload':{'data':UNIVERSE,'total':3},'rate_limit_headers':{}}
        symbol=path.split('/')[2]
        market=next(row['marketId'] for row in UNIVERSE if row['symbol']==symbol)
        page_two='nextPageToken' in params
        return observation(symbol,market,second=2 if page_two else 1,next_token=None if page_two else 'fixture-next')


def test_round_robin_download_resume_continues_token_and_does_not_redownload_pages(tmp_path):
    first=FixtureClient()
    paused=run(DAY,page_size=1,root=tmp_path,max_requests=2,client=first)
    assert paused['state']=='PAUSED_REQUEST_LIMIT' and paused['pages_saved']==2
    second=FixtureClient()
    complete=run(DAY,page_size=1,root=tmp_path,client=second)
    assert complete['state']=='COMPLETED' and complete['symbols_fully_paginated']==3
    assert complete['raw_quote_count']==6 and complete['pages_saved']==6
    assert complete['exchange_counts']=={'HNX':1,'HOSE':1,'UPCOM':1}
    assert len(second.calls)==4 and all(call[0]!='/instruments' for call in second.calls)
    assert sum('nextPageToken' in params for _,params in second.calls)==3
    assert complete['sqlite_integrity']=='ok' and complete['finance_enabled'] is False
    third=FixtureClient()
    repeat=run(DAY,page_size=1,root=tmp_path,client=third)
    assert not third.calls and repeat['raw_quote_count']==6


def test_raw_pages_are_compressed_checksummed_and_atomic(tmp_path):
    db=connect(tmp_path/'history.db')
    settings=initialize(db,DAY,1000)
    with db:
        db.execute("INSERT INTO symbols(symbol,exchange,market_id) VALUES('FPT','HOSE','STO')")
    row=db.execute('SELECT * FROM symbols').fetchone()
    store_page(db,row,observation(next_token='next'),settings)
    page=db.execute('SELECT * FROM pages').fetchone()
    raw=gzip.decompress(page['payload_gzip'])
    assert hashlib.sha256(raw).hexdigest()==page['sha256']
    assert json.loads(raw)['quotes'][0]['symbol']=='FPT'
    updated=db.execute('SELECT * FROM symbols').fetchone()
    assert updated['next_token']=='next' and updated['page_count']==1 and updated['status']=='PARTIAL'
    with pytest.raises(ValueError,match='TOKEN_REPEATED'):
        store_page(db,updated,observation(second=2,next_token='next'),settings)
    assert db.execute('SELECT count(*) FROM pages').fetchone()[0]==1
    bad=observation(symbol='HPG',second=3)
    with pytest.raises(ValueError,match='SCOPE_MISMATCH'):
        store_page(db,updated,bad,settings)
    bad=observation(second=3)
    bad['payload']['quotes'][0]['time']='2026-10-29 09:00:00.000'
    with pytest.raises(ValueError,match='OUTSIDE_REQUEST'):
        store_page(db,updated,bad,settings)
    db.close()


def test_empty_results_are_reported_separately_from_observed_quotes(tmp_path):
    class Empty(FixtureClient):
        def get(self,path,params):
            result=super().get(path,params)
            if path!='/instruments':
                result['payload']={'quotes':[]}
            return result
    report=run(DAY,root=tmp_path,client=Empty())
    assert report['state']=='COMPLETED' and report['raw_quote_count']==0
    assert report['symbol_status']=={'NO_DATA_RETURNED':3}
    assert report['no_data_symbols']==['ACV','FPT','SHS']


def test_null_book_side_is_preserved_as_source_data(tmp_path):
    class OneSided(FixtureClient):
        def get(self,path,params):
            result=super().get(path,params)
            if path!='/instruments':
                result['payload']['quotes'][0]['offer']=None
            return result
    result=run(DAY,page_size=1,root=tmp_path,client=OneSided())
    assert result['state']=='COMPLETED' and result['raw_quote_count']==6
    db=connect(tmp_path/'data/history/dnse'/DAY/'history.db')
    assert json.loads(gzip.decompress(db.execute('SELECT payload_gzip FROM pages LIMIT 1').fetchone()[0]))['quotes'][0]['offer'] is None
    db.close()


def test_network_failure_preserves_progress_and_resume(tmp_path,monkeypatch):
    monkeypatch.setattr('scripts.collect_dnse_history.time.sleep',lambda seconds:None)
    from src.data_providers.base import ProviderError
    class Failing(FixtureClient):
        def get(self,path,params):
            if path.startswith('/price/FPT'):
                raise ProviderError('DNSE_TIMEOUT')
            return super().get(path,params)
    report=run(DAY,page_size=1,root=tmp_path,client=Failing())
    assert report['state']=='FAILED_RESUMABLE' and report['error_code']=='DNSE_TIMEOUT'
    assert report['pages_saved']==1
    assert run(DAY,page_size=1,root=tmp_path,client=FixtureClient())['state']=='COMPLETED'


def test_transient_timeout_retries_same_page_without_losing_records(tmp_path,monkeypatch):
    from src.data_providers.base import ProviderError
    monkeypatch.setattr('scripts.collect_dnse_history.time.sleep',lambda seconds:None)
    class Transient(FixtureClient):
        failed=False
        def get(self,path,params):
            if path.startswith('/price/') and not self.failed:
                self.failed=True
                raise ProviderError('DNSE_TIMEOUT')
            return super().get(path,params)
    report=run(DAY,page_size=1,root=tmp_path,client=Transient())
    assert report['state']=='COMPLETED' and report['raw_quote_count']==6
    assert report['requests_attempted']==8


def test_symbol_http_failure_is_not_reported_as_full_completion(tmp_path):
    from src.data_providers.dnse_readonly import DNSEHTTPError
    class NotFound(FixtureClient):
        def get(self,path,params):
            if path.startswith('/price/FPT'):
                raise DNSEHTTPError(404)
            return super().get(path,params)
    report=run(DAY,page_size=1,root=tmp_path,client=NotFound())
    assert report['state']=='COMPLETED_WITH_ERRORS'
    assert report['symbols_fully_paginated']==2
    assert report['incomplete_symbols'][0]['symbol']=='FPT'
    assert report['incomplete_symbols'][0]['error']=='HTTP_404'


def test_resume_rejects_changed_settings_and_stop_file_prevents_quote_calls(tmp_path):
    run(DAY,page_size=1,root=tmp_path,max_requests=1,client=FixtureClient())
    changed=run(DAY,page_size=1000,root=tmp_path,client=FixtureClient())
    assert changed['state']=='FAILED_RESUMABLE' and changed['error_code']=='RESUME_SETTINGS_MISMATCH'
    control=tmp_path/'data/control/STOP_DNSE_HISTORY'
    control.write_text('stop')
    fixture=FixtureClient()
    stopped=run(DAY,page_size=1,root=tmp_path,client=fixture)
    assert stopped['state']=='STOPPED_BY_USER' and not fixture.calls


def test_audit_verifies_page_chains_and_exports_raw_records(tmp_path):
    from scripts.audit_dnse_history import audit
    run(DAY,page_size=1,root=tmp_path,client=FixtureClient())
    report=audit(DAY,root=tmp_path,export=True)
    assert report['all_symbols_fully_paginated'] and report['page_checksums_verified']==6
    assert report['raw_quote_count_verified']==6 and report['symbols_with_records']==3
    with gzip.open(report['export'],'rt',encoding='utf-8') as stream:
        records=[json.loads(line) for line in stream]
    assert len(records)==6 and all(record['units_verified'] is False for record in records)
    db=connect(tmp_path/'data/history/dnse'/DAY/'history.db')
    with db:
        db.execute("UPDATE pages SET sha256='tampered' WHERE id=1")
    db.close()
    with pytest.raises(ValueError,match='CHECKSUM_FAILED'):
        audit(DAY,root=tmp_path)


def test_morning_history_uses_fixed_cutoff_and_is_separate_from_daily_archive(tmp_path):
    from scripts.audit_dnse_history import audit
    daily=run(DAY,page_size=1,root=tmp_path,client=FixtureClient())
    daily_path=tmp_path/'data/history/dnse'/DAY/'history.db'
    daily_hash=hashlib.sha256(daily_path.read_bytes()).hexdigest()
    morning=run(DAY,page_size=1,root=tmp_path,client=FixtureClient(),session='morning')
    assert daily['state']==morning['state']=='COMPLETED'
    assert morning['settings']['session']=='morning'
    assert morning['settings']['to']==1793161800
    assert daily['settings']['to']==1793175000
    assert hashlib.sha256(daily_path.read_bytes()).hexdigest()==daily_hash
    assert (tmp_path/'data/history/dnse'/DAY/'morning/history.db').exists()
    assert (tmp_path/'report'/f'dnse_history_{DAY}_morning.json').exists()
    assert audit(DAY,root=tmp_path,session='morning',export=True)['all_symbols_fully_paginated']


def test_morning_archive_rejects_quotes_after_1130(tmp_path):
    db=connect(tmp_path/'history.db')
    settings=initialize(db,DAY,1000,session='morning')
    with db:
        db.execute("INSERT INTO symbols(symbol,exchange,market_id) VALUES('FPT','HOSE','STO')")
    row=db.execute('SELECT * FROM symbols').fetchone()
    afternoon=observation()
    afternoon['payload']['quotes'][0]['time']='2026-10-28 13:00:00.001'
    with pytest.raises(ValueError,match='OUTSIDE_REQUEST'):
        store_page(db,row,afternoon,settings)
    assert db.execute('SELECT count(*) FROM pages').fetchone()[0]==0
    db.close()


def test_missing_report_does_not_treat_pending_errors_or_null_sides_as_empty(tmp_path):
    from scripts.report_dnse_missing import make_report
    db_path=tmp_path/'data/history/dnse'/DAY/'morning/history.db'
    db_path.parent.mkdir(parents=True)
    db=connect(db_path)
    settings=initialize(db,DAY,1000,session='morning')
    with db:
        db.executemany('INSERT INTO symbols(symbol,exchange,market_id,status,error) VALUES(?,?,?,?,?)',[
            ('FPT','HOSE','STO','PENDING',None),('SHS','HNX','STX','ERROR','HTTP_404'),
            ('ACV','UPCOM','UPX','PENDING',None),('HPG','HOSE','STO','PENDING',None)])
    empty=observation(symbol='ACV',market='UPX')
    empty['payload']={'quotes':[]}
    store_page(db,db.execute("SELECT * FROM symbols WHERE symbol='ACV'").fetchone(),empty,settings)
    one_sided=observation(symbol='HPG')
    one_sided['payload']['quotes'][0]['offer']=None
    store_page(db,db.execute("SELECT * FROM symbols WHERE symbol='HPG'").fetchone(),one_sided,settings)
    db.close()
    report=make_report(DAY,root=tmp_path)
    assert report['empty_count']==1 and report['empty_by_exchange']['UPCOM']==['ACV']
    assert report['error_count']==1 and report['errors'][0]['symbol']=='SHS'
    assert report['symbols_pending_first_page']==1 and not report['first_pass_finished']
    assert report['symbols_with_records']==1
    assert (tmp_path/'report'/f'dnse_missing_{DAY}_morning.txt').exists()
    control=tmp_path/'data/control'
    control.mkdir(parents=True,exist_ok=True)
    (control/f'dnse_manual_checks_{DAY}_morning.json').write_text(json.dumps({
        'date':DAY,'session':'morning','checks':[{'symbol':'ACV','matched_volume':0,
        'displayed_bid_volume':0,'displayed_ask_volume':0,'sources':['User fixture'],
        'evidence_type':'USER_REPORTED_ZERO_ACTIVITY_AND_DISPLAYED_DEPTH'}]}))
    checked=make_report(DAY,root=tmp_path)
    assert checked['empty_count']==1 and checked['manual_zero_display_count']==1
    assert checked['empty_responses_pending_manual_review']==0
    assert checked['pending_manual_review_by_exchange']['UPCOM']==[]
    db=connect(db_path)
    assert db.execute("SELECT status FROM symbols WHERE symbol='ACV'").fetchone()[0]=='NO_DATA_RETURNED'
    db.close()
