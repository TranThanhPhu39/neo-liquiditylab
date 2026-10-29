from copy import deepcopy
from datetime import datetime
import hashlib
import json

import pytest

from test_dnse_normalization import prepare_fixture, quote, rules
from scripts.normalize_dnse_history import run as normalize
from scripts.dnse_unit_review import capture, save_case
from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.dnse_normalization import canonical_json
from src.data_pipeline.dnse_unit_review import candidates, historical_case, compare_observation, observation_template


def case_for(quote):
    return dict(symbol=quote['symbol'],raw_quote=quote,event_id='dnse:'+hashlib.sha256(canonical_json(quote).encode()).hexdigest())


def observation_for(quote,tmp_path):
    # Synthetic image bytes: comparison checks a supplied transcription, not image authenticity.
    (tmp_path/'fixture.png').write_bytes(b'EXPLICIT_SYNTHETIC_IMAGE_BYTES')
    observed=observation_template(case_for(quote))
    observed.update(independent_source_label='SYNTHETIC TEST DISPLAY',screenshot_path='fixture.png',
        display_timestamp_local='2026-10-28T13:36:43+07:00',display_price_unit='thousand_VND',display_quantity_unit='shares')
    for side in ('bid','offer'):
        observed[side]=[dict(price=str(v['price']),quantity=v['quantity']*10) for v in quote[side]]
    return observed


def test_six_pairs_match_but_never_auto_certify_or_change_rules(quote,tmp_path):
    observed=observation_for(quote,tmp_path)
    observed['bid'].reverse()  # Compare by price and side, not visual row index.
    result=compare_observation(case_for(quote),observed,base=tmp_path)
    assert result['status']=='SIX_PAIRS_MATCH_MANUAL_REVIEW_REQUIRED'
    assert len(result['pairs'])==6 and all(p['matches'] for p in result['pairs'])
    assert not result['rule_changes_applied'] and result['finance_scope_unchanged']


def test_wrong_quantity_or_clock_is_not_approved(quote,tmp_path):
    observed=observation_for(quote,tmp_path)
    observed['offer'][1]['quantity']+=10
    result=compare_observation(case_for(quote),observed,base=tmp_path)
    assert result['status']=='MISMATCH_REVIEW_REQUIRED'
    observed=observation_for(quote,tmp_path)
    observed['display_timestamp_local']='2026-10-29T13:36:43+07:00'
    result=compare_observation(case_for(quote),observed,base=tmp_path)
    assert not result['clock_within_two_seconds'] and result['status']=='MISMATCH_REVIEW_REQUIRED'


def test_blank_observation_or_missing_scope_and_image_cannot_be_evidence(quote,tmp_path):
    template=observation_template(case_for(quote))
    assert all(v['quantity'] is None for v in template['bid'])
    with pytest.raises(ValueError,match='IMAGE_REQUIRED'):
        compare_observation(case_for(quote),template,base=tmp_path)
    observed=observation_for(quote,tmp_path)
    observed['symbol']='FPT'
    with pytest.raises(ValueError,match='SCOPE_MISMATCH'):
        compare_observation(case_for(quote),observed,base=tmp_path)


def test_history_packet_reads_actual_raw_lineage_and_preserves_database(tmp_path,quote):
    prepare_fixture(tmp_path,quote)
    normalize(root=tmp_path)
    store=DNSEHistory(tmp_path/'data/processed/dnse/normalized.db')
    before=hashlib.sha256(store.path.read_bytes()).hexdigest()
    chosen=candidates(store,'2026-10-28',quotas={'HOSE':1})
    assert [row['symbol'] for row in chosen]==['FPT']
    case=historical_case(store,tmp_path,'FPT','2026-10-28',datetime.fromisoformat('2026-10-28T13:36:44+07:00'))
    assert case['raw_quote']['bid'][0]['quantity']==11330
    assert case['raw_quote']['symbol']=='FPT'
    assert case['unit_verification_status']=='ASSUMED_SHARED_SCHEMA'
    assert hashlib.sha256(store.path.read_bytes()).hexdigest()==before


def test_capture_only_uses_one_read_only_quote_and_keeps_scope_unverified(tmp_path,quote):
    class Client:
        calls=[]
        def get(self,path,params):
            self.calls.append((path,params))
            return dict(payload={'data':quote},received_at_utc='2026-10-28T06:36:44+00:00')
    client=Client()
    result=capture('ACB',root=tmp_path,client=client)
    assert client.calls==[('/price/ACB/quotes/latest',{'boardId':'G1'})]
    assert result['state']=='CAPTURED_NOT_VERIFIED'
    case=json.loads(__import__('pathlib').Path(result['case']).read_text(encoding='utf-8'))
    assert case['unit_verification_status']=='NOT_REVIEWED_CAPTURE'
    with pytest.raises(ValueError,match='INVALID_MARKET_SYMBOL'):
        capture('../orders',root=tmp_path,client=client)


def test_case_output_never_overwrites_entered_observations(tmp_path,quote):
    case=case_for(quote)
    save_case(case,tmp_path,'ACB')
    with pytest.raises(ValueError,match='ALREADY_EXISTS'):
        save_case(case,tmp_path,'ACB')
