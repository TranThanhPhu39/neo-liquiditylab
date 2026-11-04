"""Offline evidence, scope and canonical finance regression tests."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import gzip
import hashlib
import json
import sqlite3
import zlib

import pytest

from test_dnse_normalization import rules
from test_dnse_ws_normalization import raw
from src.data_pipeline.dnse_normalization import normalize_quote,snapshot_from_record
from src.data_pipeline.dnse_ws_normalization import normalize_event
from src.data_pipeline.dnse_ws_review import compare,rest_clock
from src.data_pipeline.dnse_ws_units import canonical_top3,load_ws_rules
from src.finance.execution_simulator import simulate_execution
from src.app.browser_bridge import ingest,view
from scripts.normalize_dnse_realtime import normalize,audit_normalized
from scripts.review_dnse_ws_units import fetch_rest


def sample(second=0):
    clock=f'2026-11-03 09:30:{second:02}.123'
    ns,_=rest_clock(clock)
    quote=dict(symbol='ACB',marketId='STO',boardId='G1',time=clock,
        bid=[dict(price=str(Decimal('21.20')-i*Decimal('.05')),quantity=100+i) for i in range(3)],
        offer=[dict(price=str(Decimal('21.25')+i*Decimal('.05')),quantity=200+i) for i in range(3)])
    payload=dict(T='q',symbol='ACB',marketId='STO',boardId='G1',
        time=dict(Seconds=ns//10**9,Nanos=ns%10**9),
        bid=[dict(price=l['price'],qtty=l['quantity']) for l in quote['bid']],
        offer=[dict(price=l['price'],qtty=l['quantity']) for l in quote['offer']])
    received=datetime.fromtimestamp(ns//10**9+1,timezone.utc).isoformat()
    return normalize_event(payload,received,'ACB','STO','2026-11-03'),quote,payload


def evidence():
    rows=[sample(i) for i in range(20)]
    return [r[0] for r in rows],[r[1] for r in rows]


def test_exact_clock_six_pairs_and_reference_scope():
    ws,rest=evidence()
    checked=compare(ws,rest,symbol='ACB',market='STO',rest_unit_verified=True)
    assert checked['status']=='EMPIRICAL_TOP3_CROSSCHECK_PASSED'
    assert checked['distinct_matched_clocks']==20 and checked['counts']['six_pairs_match']==20
    assert checked['factor_candidates']==[dict(price_multiplier_to_vnd='1E+3',volume_multiplier_to_shares='1E+1',matches=20)]
    assert not checked['full_ten_level_semantics_verified'] and not checked['rule_changes_applied']
    assert compare(ws,rest,symbol='ACB',market='STO')['status']=='RELATIVE_SCALE_MATCH_ONLY'
    # Repeating one event cannot manufacture twenty distinct source clocks.
    assert compare(ws[:1]*20,rest,symbol='ACB',market='STO',rest_unit_verified=True)['status']=='INSUFFICIENT_OR_CONFLICTING_EVIDENCE'
    with pytest.raises(ValueError,match='TWENTY'):
        compare(ws,rest,symbol='ACB',market='STO',min_matches=1)


def test_precision_interval_is_not_nearest_time_tolerance():
    ws,rest=evidence()
    rest[0]['time']=rest[0]['time'][:-2]  # 0.1-second precision contains WS .123
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['six_pairs_match']==20
    rest[0]['time']='2026-11-03 09:30:00.124'
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['no_same_source_clock']==1


def test_mismatch_ambiguity_and_wrong_scope_never_pass():
    ws,rest=evidence();rest[0]['bid'][0]['quantity']+=1
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['same_clock_mismatch']==1
    ws,rest=evidence();other=deepcopy(rest[0])
    for side in ('bid','offer'):
        for level in other[side]:
            level['price']=str(Decimal(level['price'])*2)
    assert compare(ws,rest+[other],symbol='ACB',market='STO')['counts']['ambiguous_factors']==1
    rest[0]['marketId']='STX'
    with pytest.raises(ValueError,match='SCOPE_MISMATCH'):
        compare(ws,rest,symbol='ACB',market='STO')


def test_partial_and_zero_quantity_are_not_fabricated():
    ws,rest=evidence();ws[0]['ask_levels']=[]
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['incomplete_or_auction_ws']==1
    ws,rest=evidence();ws[0]['bid_levels'][0]['source_quantity']='0'
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['same_clock_mismatch']==1
    rest[0]['bid'][0]['quantity']=0
    assert compare(ws,rest,symbol='ACB',market='STO')['counts']['six_pairs_match']==20


@pytest.mark.parametrize('side',['sell','buy'])
@pytest.mark.parametrize('qty',[1,1500,150000])
def test_canonical_conversion_has_identical_finance_to_rest(rules,side,qty):
    ws,rest,_=sample();converted=canonical_top3(ws,'fixture',load_ws_rules())
    expected=normalize_quote(rest,ws['collected_at_utc'],rules)
    got=simulate_execution(snapshot_from_record(converted),qty,side=side)
    reference=simulate_execution(snapshot_from_record(expected),qty,side=side)
    assert got==reference
    assert converted['financial_eligible'] and converted['bid_vol_1']==1000
    assert ws['bid_vol_1'] is None  # Source view is unchanged.


@pytest.mark.parametrize('change',['symbol','market','partial','fraction','clock','date'])
def test_conversion_rejects_unreviewed_or_incomplete_scope(change):
    ws,_,_=sample()
    if change=='symbol':ws['symbol']='FPT'
    if change=='market':ws['market_id']='STX'
    if change=='partial':ws['ask_levels'][0]['source_quantity']=None
    if change=='fraction':ws['bid_levels'][0]['source_quantity']='1.05'
    if change=='clock':ws['source_unix_nanoseconds']=str(int(ws['source_unix_nanoseconds'])+1000000)
    if change=='date':ws['issues'].append('OUT_OF_PARTITION_SOURCE_DAY')
    assert canonical_top3(ws,'fixture',load_ws_rules()) is None


def test_crossed_and_future_books_cannot_enter_finance():
    ws,_,_=sample();ws['bid_levels'][0]['source_price']='22'
    r=canonical_top3(ws,'fixture',load_ws_rules())
    assert not r['financial_eligible']
    with pytest.raises(ValueError):simulate_execution(snapshot_from_record(r),1)
    ws,_,_=sample();ws['collected_at_utc']='2026-11-03T02:29:00+00:00'
    assert not canonical_top3(ws,'fixture',load_ws_rules())['financial_eligible']


def test_rule_change_reprocesses_existing_events_and_audits_nested_provenance(tmp_path):
    _,_,payload=sample();path=raw(tmp_path,payload);out=tmp_path/'normalized.db'
    assert normalize(path,out)['audit']['canonical_events']==0
    converted=normalize(path,out,rules=load_ws_rules())
    assert converted['new_events']==0 and converted['reprocessed_events']==1
    assert converted['audit']['canonical_events']==1
    # raw() deliberately supplies a receipt before this source clock: never finance-eligible.
    assert converted['audit']['canonical_financial_eligible']==0
    assert normalize(path,out,rules=load_ws_rules())['reprocessed_events']==0
    with closing(sqlite3.connect(out)) as db:
        record=json.loads(zlib.decompress(db.execute('SELECT payload_zlib FROM events').fetchone()[0]))
        record['canonical_snapshot']['bid_vol_1']+=1
        packed=json.dumps(record).encode()
        db.execute('UPDATE events SET payload_zlib=?,output_sha256=?',(zlib.compress(packed),hashlib.sha256(packed).hexdigest()))
        db.commit()
    with pytest.raises(ValueError,match='DERIVATION_FAILED'):audit_normalized(out)


def test_ws_browser_import_a1_b5_and_forged_contract_rejection():
    ws,_,_=sample();r=canonical_top3(ws,'fixture',load_ws_rules())
    ingest(json.dumps(dict(records=[r])))
    request=dict(day='2026-11-03',time='09:31',symbol='ACB',quantity=1500,page='execution')
    assert json.loads(view(json.dumps(request)))['execution']['executed_quantity']==1500
    request.update(page='stress',portfolio='symbol,quantity\nACB,1500\nFPT,100',shock=50)
    got=json.loads(view(json.dumps(request)))
    assert got['baseline']['analyzed_quantity']==1500 and got['baseline']['excluded_quantity']==100
    r['unit_contract_hash']='forged'
    with pytest.raises(ValueError,match='CONTRACT_SCOPE'):ingest(json.dumps(dict(records=[r])))


def test_rest_pagination_repeated_token_and_window_bounds(tmp_path):
    ws,rest,_=sample()
    class Client:
        def get(self,*args):
            return dict(payload=dict(quotes=[rest],nextPageToken='repeat'))
    with pytest.raises(ValueError,match='PAGINATION_INVALID'):
        fetch_rest('ACB',[ws],tmp_path,Client())
    rest['time']='2026-11-03 09:30:01.000'
    with pytest.raises(ValueError,match='OUTSIDE_WINDOW'):
        fetch_rest('ACB',[ws],tmp_path,Client())


def test_promotion_rechecks_checksums_and_relative_only_reference(tmp_path,monkeypatch,rules):
    from scripts import promote_dnse_ws_units as promotion
    ws,quotes=evidence()
    def save(name,content):
        packed=gzip.compress(content)
        (tmp_path/name).write_bytes(packed)
        return dict(path=name,sha256=hashlib.sha256(packed).hexdigest())
    w=save('ws.gz','\n'.join(json.dumps(r) for r in ws).encode())
    r=save('rest.gz',json.dumps(dict(path='/price/ACB/quotes',method='GET',payload=dict(quotes=quotes))).encode())
    record=dict(symbol='ACB',market_id='STO',board_id='G1',rest_unit_rule_hash=rules.rule_hash,ws_artifact=w,rest_artifacts=[r])
    path=tmp_path/'comparison.json';path.write_text(json.dumps(record))
    monkeypatch.setattr(promotion,'prepare_rules',lambda root:None)
    monkeypatch.setattr(promotion,'load_rules',lambda path:rules)
    assert promotion.promote(path,root=tmp_path)['events']==20
    (tmp_path/'ws.gz').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='CHECKSUM_FAILED'):promotion.promote(path,root=tmp_path)
