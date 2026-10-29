"""Offline normalization fixtures; never request market data or credentials."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import gzip
import hashlib
import json
import zlib

import pytest

from src.data_pipeline.dnse_normalization import DnseRules,normalize_quote,snapshot_from_record,load_rules
from src.finance.execution_simulator import simulate_execution
from src.finance.stress_testing import compare_scenario
from src.models.scenario import Scenario
from scripts.normalize_dnse_history import run,connect_output,process_page,canonical_json

RECEIVED='2026-10-28T15:00:00+00:00'


@pytest.fixture
def rules():
    return DnseRules('2026-07-23',Decimal(1000),Decimal(10),('ACB',),('STO',),'fixture-rule-hash',{})


@pytest.fixture
def quote():
    return {'symbol':'ACB','marketId':'STO','boardId':'G1','time':'2026-10-28 13:36:43.792',
        'bid':[{'price':21.2,'quantity':11330},{'price':21.15,'quantity':2200},{'price':21.1,'quantity':16580}],
        'offer':[{'price':21.25,'quantity':5620},{'price':21.3,'quantity':17310},{'price':21.35,'quantity':12050}],
        'totalBidQtty':0,'totalOfferQtty':0}


def test_scale_depth_clock_and_exact_execution_math(quote,rules):
    original=deepcopy(quote)
    record=normalize_quote(quote,RECEIVED,rules)
    assert quote==original
    assert Decimal(record['bid_price_1'])==21200 and record['bid_vol_1']==113300
    assert record['bid_observed_depth_shares']==301100
    assert record['ask_observed_depth_shares']==349800
    assert record['ignored_source_aggregate_fields']==['totalBidQtty','totalOfferQtty']
    assert record['source_timestamp']=='2026-10-28T06:36:43.792000+00:00'
    assert record['collected_at_utc']==RECEIVED and record['local_date']=='2026-10-28'
    assert record['financial_eligible'] and record['data_quality_status']=='VALID'
    snapshot=snapshot_from_record(record)
    result=simulate_execution(snapshot,150000)
    assert result.gross_value==Decimal(3177430000)
    assert result.book_walking_cost==Decimal(2570000)
    compared=compare_scenario(snapshot,150000,Scenario(Decimal('.75')))
    assert compared['stressed'].executed_quantity==75275
    assert compared['stressed'].coverage_ratio<compared['baseline'].coverage_ratio


def test_shared_schema_is_converted_but_scope_remains_unverified(quote,rules):
    quote['symbol']='FPT'
    record=normalize_quote(quote,RECEIVED,rules)
    assert record['bid_vol_1']==113300
    assert record['unit_verification_status']=='ASSUMED_SHARED_SCHEMA'
    assert not record['financial_eligible']
    with pytest.raises(ValueError,match='chưa được xác minh'):
        simulate_execution(snapshot_from_record(record),100)


def test_unknown_side_is_null_not_zero_and_invalid_fraction_is_not_rounded(quote,rules):
    quote['bid']=None
    record=normalize_quote(quote,RECEIVED,rules)
    assert record['bid_observed_depth_shares'] is None and record['bid_vol_1'] is None
    assert record['bid_source_state']=='NULL_OR_MISSING'
    quote['offer'][0]['quantity']='1.05'
    record=normalize_quote(quote,RECEIVED,rules)
    assert record['ask_vol_1'] is None and record['data_quality_status']=='INVALID'
    with pytest.raises(ValueError,match='không hợp lệ'):
        simulate_execution(snapshot_from_record(record),100,side='buy')


def test_auction_is_retained_but_not_used_as_continuous_execution(quote,rules):
    quote['time']='2026-10-28 09:00:00.001'
    record=normalize_quote(quote,RECEIVED,rules)
    assert record['market_session']=='DNSE_OUTSIDE_CONTINUOUS' and not record['financial_eligible']
    with pytest.raises(ValueError,match='ngoài cửa sổ'):
        simulate_execution(snapshot_from_record(record),100)
    quote['marketId']='UPX'
    quote['time']='2026-10-28 14:45:00.001'
    assert normalize_quote(quote,RECEIVED,rules)['market_session']=='DNSE_CONTINUOUS_PM'


def test_duplicate_source_records_keep_both_lineages_without_double_scaling(tmp_path,quote,rules):
    db=connect_output(tmp_path/'out.db')
    settings={'from':datetime.fromisoformat('2026-10-28T00:00:00+07:00').timestamp(),
              'to':datetime.fromisoformat('2026-10-28T15:10:00+07:00').timestamp()}
    with db:
        db.execute('INSERT INTO archives VALUES(?,?,?,?,?)',('archive','fixture','hash',json.dumps(settings),2))
        db.execute("INSERT INTO raw_catalog VALUES('archive','ACB','HOSE','COMPLETE',2)")
    payload=json.dumps({'quotes':[quote,quote]}).encode()
    page={'id':1,'symbol':'ACB','received_at':RECEIVED,'quote_count':2,
          'sha256':hashlib.sha256(payload).hexdigest(),'payload_gzip':gzip.compress(payload)}
    assert process_page(db,'archive',page,rules)
    assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM lineage').fetchone()[0]==2
    saved=json.loads(zlib.decompress(db.execute('SELECT payload_zlib FROM snapshots').fetchone()[0]))
    assert saved['bid_vol_1']==113300
    assert not process_page(db,'archive',page,rules)
    earlier_payload=json.dumps({'quotes':[quote]}).encode()
    earlier={**page,'id':2,'quote_count':1,'received_at':'2026-10-28T14:00:00+00:00',
             'sha256':hashlib.sha256(earlier_payload).hexdigest(),'payload_gzip':gzip.compress(earlier_payload)}
    assert process_page(db,'archive',earlier,rules)
    earliest=json.loads(zlib.decompress(db.execute('SELECT payload_zlib FROM snapshots').fetchone()[0]))
    assert earliest['collected_at_utc']=='2026-10-28T14:00:00+00:00'
    assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==1
    with pytest.raises(ValueError,match='RAW_PAGE_CHANGED'):
        process_page(db,'archive',{**page,'sha256':'tampered'},rules)
    db.close()


def prepare_fixture(root,quote):
    from scripts.collect_dnse_history import connect,initialize,store_page
    evidence_dir=root/'report'; evidence_dir.mkdir()
    raw_dir=root/'data/raw'; raw_dir.mkdir(parents=True)
    evidence_raw=raw_dir/'unit-fixture.json'
    evidence_raw.write_text(json.dumps({'payload':{'quotes':[quote]}}))
    pairs=[]
    for side in ('bid','offer'):
        for index,level in enumerate(quote[side]):
            pairs.append({'side':side,'level':index+1,
                'normalized_price_vnd':str(Decimal(str(level['price']))*1000),
                'image_quantity_display':level['quantity']*10})
    evidence={'verification_status':'EMPIRICALLY_VERIFIED_FOR_ACB_G1','matched_level_pairs':pairs,
        'chosen_source_time':quote['time'],'api_artifact':{'path':'data/raw/unit-fixture.json',
        'sha256':hashlib.sha256(evidence_raw.read_bytes()).hexdigest()}}
    (evidence_dir/'dnse_orderbook_unit_evidence_2026-10-29_133643.json').write_text(json.dumps(evidence))
    folder=root/'data/history/dnse/2026-10-28'; folder.mkdir(parents=True)
    source=connect(folder/'history.db')
    settings=initialize(source,'2026-10-28',1000)
    with source:
        source.executemany('INSERT INTO symbols(symbol,exchange,market_id) VALUES(?,?,?)',[
            ('ACB','HOSE','STO'),('FPT','HOSE','STO'),('EMG','UPCOM','UPX')])
    for symbol in ('ACB','FPT','EMG'):
        row=source.execute('SELECT * FROM symbols WHERE symbol=?',(symbol,)).fetchone()
        observation={'received_at_utc':RECEIVED,'payload':{'quotes':[] if symbol=='EMG' else [{**quote,'symbol':symbol}]}}
        store_page(source,row,observation,settings)
    source.close()
    return folder/'history.db'


def test_complete_migration_resumes_preserves_raw_and_does_not_invent_empty_books(tmp_path,quote):
    source=prepare_fixture(tmp_path,quote)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    paused=run(root=tmp_path,max_pages=1)
    assert paused['state']=='PAUSED_PAGE_LIMIT'
    result=run(root=tmp_path)
    assert result['state']=='COMPLETED' and result['raw_occurrences_processed']==2
    assert result['unique_normalized_snapshots']==2 and result['normalized_occurrences']==2
    assert result['unit_scope_counts']=={'ASSUMED_SHARED_SCHEMA':1,'EMPIRICALLY_VERIFIED':1}
    assert result['financial_eligible_snapshots']==1
    assert next(row for row in result['coverage'] if row['symbol']=='EMG')['normalized_occurrences']==0
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    repeat=run(root=tmp_path)
    assert repeat['unique_normalized_snapshots']==2 and repeat['pages_processed']==3
    assert repeat['integrity']=='ok'


def test_independent_audit_checks_values_against_raw_and_exports(tmp_path,quote):
    from scripts.audit_dnse_normalized import audit,validate_finance
    prepare_fixture(tmp_path,quote)
    run(root=tmp_path)
    result=audit(root=tmp_path,export=True)
    assert result['raw_rows_reconciled']==2 and result['unique_normalized_snapshots']==2
    assert result['pages_verified']==3 and result['export']['records']==2
    with gzip.open(tmp_path/result['export']['path'],'rt',encoding='utf-8') as stream:
        assert len([json.loads(line) for line in stream])==2
    assert validate_finance(tmp_path)['assumed_scope_default_financial_calculation_blocked']
    db=connect_output(tmp_path/'data/processed/dnse/normalized.db')
    row=db.execute('SELECT id,payload_zlib FROM snapshots LIMIT 1').fetchone()
    record=json.loads(zlib.decompress(row['payload_zlib']))
    record['bid_vol_1']=1
    with db:
        db.execute('UPDATE snapshots SET payload_zlib=? WHERE id=?',
                   (zlib.compress(canonical_json(record).encode()),row['id']))
    db.close()
    with pytest.raises(ValueError,match='SCALE_MISMATCH'):
        audit(root=tmp_path)


def test_audit_refuses_to_run_while_normalizer_has_exclusive_lock(tmp_path,quote):
    from scripts.audit_dnse_normalized import audit
    from scripts.collect_dnse_history import exclusive_job
    prepare_fixture(tmp_path,quote)
    run(root=tmp_path)
    with exclusive_job(tmp_path/'data/control/dnse_normalization.lock'):
        with pytest.raises(RuntimeError,match='ANOTHER_COLLECTION_JOB'):
            audit(root=tmp_path)
