"""Browser bridge keeps the repository finance and reconstruction contracts."""
from datetime import datetime
import json

import pytest

from test_dnse_history import history
from test_dnse_normalization import quote
from src.app.browser_bridge import ingest, view
from scripts.export_dashboard import export


def bundle(history):
    return export(history, '2026-10-28', '14:00')


def query(**kwargs):
    return json.loads(view(json.dumps(dict(day='2026-10-28', time='14:00', symbol='ACB', page='execution', **kwargs))))


def test_exact_finance_unverified_guard_and_atomic_import(history):
    payload=bundle(history)
    info=json.loads(ingest(json.dumps(payload)))
    assert info['symbols']['2026-10-28']==['ACB','EMG','FPT']
    r=query(quantity=150000)
    assert r['execution']['book_walking_cost']=='2570000.00'
    assert r['execution']['coverage_ratio']=='1'
    fpt=next(r for r in payload['records'] if r['symbol']=='FPT')
    fpt['financial_eligible']=True
    ingest(json.dumps(payload))
    r=json.loads(view(json.dumps(dict(day='2026-10-28',time='14:00',symbol='FPT',page='execution'))))
    assert not r['record']['financial_eligible'] and 'execution_error' in r
    bad={**payload['records'][0], 'volume_unit':'raw'}
    with pytest.raises(ValueError,match='schema'):
        ingest(json.dumps(bad))
    assert query()['record']['symbol']=='ACB'  # Failed import did not replace state.


def test_source_cutoff_and_independent_portfolio_exclusions(history):
    ingest(json.dumps(bundle(history)))
    early=json.loads(view(json.dumps(dict(day='2026-10-28',time='09:00',symbol='ACB'))))
    assert early['record'] is None and all(not r['has_snapshot'] for r in early['watchlist'])
    r=json.loads(view(json.dumps(dict(day='2026-10-28',time='14:00',symbol='FPT',page='stress',
                                     portfolio='symbol,quantity\nACB,150000\nFPT,100\nEMG,200',shock=50))))
    assert r['baseline']['coverage']=='1'
    assert r['baseline']['analyzed_quantity']==150000
    assert r['baseline']['excluded_quantity']==300
    assert r['baseline']['exclusions']['EMG']['reason']=='NO DATA'
    assert 'chưa được xác minh' in r['baseline']['exclusions']['FPT']['reason']


def test_import_never_accepts_raw_units_or_invalid_share_type(history):
    record=bundle(history)['records'][0]
    for value in (True,1.5,-1,'100'):
        with pytest.raises(ValueError,match='Khối lượng'):
            ingest(json.dumps({**record,'bid_vol_1':value}))
    for value in ('NaN','Infinity','-1'):
        with pytest.raises(ValueError,match='Giá'):
            ingest(json.dumps({**record,'bid_price_1':value}))
