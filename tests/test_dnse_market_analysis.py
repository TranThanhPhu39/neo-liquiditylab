from contextlib import closing
from datetime import datetime
from decimal import Decimal
import gzip
import hashlib
import json

import pytest

from test_dnse_normalization import prepare_fixture, quote, rules
from scripts.normalize_dnse_history import run as normalize, connect_output, process_page
from scripts.report_dnse_market import run as report_market
from src.data_pipeline.dnse_history import DNSEHistory
from src.finance.dnse_market_analysis import exchange_grid, inventory, analyse_symbol


@pytest.fixture
def archive(tmp_path,quote):
    prepare_fixture(tmp_path,quote)
    normalize(root=tmp_path)
    return DNSEHistory(tmp_path/'data/processed/dnse/normalized.db')


def test_exchange_specific_minutes_and_no_lunch_or_auction():
    for exchange,count,last in [('HOSE',225,'14:29'),('HNX',240,'14:29'),('UPCOM',270,'14:59')]:
        grid=list(exchange_grid('2026-10-29',exchange))
        assert len(grid)==count and grid[-1][1].strftime('%H:%M')==last
        assert all(not '11:30'<=point.strftime('%H:%M')<'13:00' for _,point in grid)
        assert grid[0][1].strftime('%H:%M')==('09:15' if exchange=='HOSE' else '09:00')


def test_unverified_book_is_descriptive_only_and_stale_not_forward_filled(archive):
    with closing(archive.connect()) as db:
        items=inventory(db,['2026-10-28'])
        result=analyse_symbol(db,items['FPT'],['2026-10-28'])
    assert result['state']=='DESCRIPTIVE_COMPARISON_READY'
    assert result['financial_status']=='BLOCKED_UNVERIFIED_UNITS'
    assert not result['financial_simulation_performed'] and not result['rule_changes_applied']
    first=next(r for r in result['grid'] if r['clock']=='13:36')
    assert first['eligible'] and first['bid_depth_source']==Decimal(30110)
    assert first['ask_depth_source']==Decimal(34980)
    assert first['obi']==Decimal(301100-349800)/Decimal(301100+349800)
    following=next(r for r in result['grid'] if r['clock']=='13:37')
    assert not following['eligible'] and following['reason']=='STALE_OR_FUTURE'
    assert following['bid_depth_source'] is None


def test_empty_api_and_absent_catalog_day_are_distinct_without_zero_books(archive):
    with closing(archive.connect()) as db:
        items=inventory(db,['2026-10-28','2026-10-29'])
        result=analyse_symbol(db,items['EMG'],['2026-10-28','2026-10-29'])
    assert result['state']=='NO_QUOTES_IN_SELECTED_ARCHIVES'
    assert result['common_minutes_each_day']==0
    assert all(row['bid_depth_source'] is None and row['obi'] is None for row in result['grid'])
    assert all(row['reason']=='API_EMPTY_RESPONSE' for row in result['grid'])
    missing=[g for g in result['summaries'] if g['date']=='2026-10-29']
    assert all(not g['catalog_present'] and g['grid_points']==0 for g in missing)


def test_upcom_afternoon_uses_its_own_calendar_and_actual_zero_can_remain_zero(tmp_path,quote,rules):
    path=tmp_path/'market.db'
    db=connect_output(path)
    settings={'date':'2026-10-28',
              'from':int(datetime.fromisoformat('2026-10-28T00:00:00+07:00').timestamp()),
              'to':int(datetime.fromisoformat('2026-10-28T15:10:00+07:00').timestamp())}
    with db:
        db.execute('INSERT INTO archives VALUES(?,?,?,?,?)',('a','fixture','h',json.dumps(settings),1))
        db.execute("INSERT INTO raw_catalog VALUES('a','TEST','UPCOM','COMPLETE',1)")
    raw={**quote,'symbol':'TEST','marketId':'UPX','time':'2026-10-28 14:40:50.000',
         'bid':[dict(price=v['price'],quantity=0) for v in quote['bid']]}
    payload=json.dumps({'quotes':[raw]}).encode()
    page=dict(id=1,symbol='TEST',received_at='2026-10-28T22:00:00+00:00',quote_count=1,
              sha256=hashlib.sha256(payload).hexdigest(),payload_gzip=gzip.compress(payload))
    process_page(db,'a',page,rules)
    assert db.execute('SELECT count(*) FROM quarantine').fetchone()[0]==0
    db.close()
    store=DNSEHistory(path)
    with closing(store.connect()) as db:
        item=inventory(db,['2026-10-28'])['TEST']
        result=analyse_symbol(db,item,['2026-10-28'])
    point=next(r for r in result['grid'] if r['clock']=='14:40')
    assert point['eligible'] and point['bid_depth_source']==0 and point['obi']==-1, (point['reason'],point['age_seconds'],point['source_time_local'])
    assert len(result['grid'])==270
    assert result['financial_status']=='BLOCKED_UNVERIFIED_UNITS'


def test_common_minutes_require_evidence_on_both_dates(archive):
    with closing(archive.connect()) as db:
        item=inventory(db,['2026-10-28','2026-10-29'])['FPT']
        result=analyse_symbol(db,item,['2026-10-28','2026-10-29'])
    assert result['state']=='NO_COMMON_FRESH_COMPLETE_MINUTES'
    assert any(row['eligible'] for row in result['grid'])
    assert not any(row['matched_across_days'] for row in result['grid'])


def test_market_report_includes_empty_symbols_resumes_and_preserves_input(archive,tmp_path):
    before=hashlib.sha256(archive.path.read_bytes()).hexdigest()
    result=report_market(root=tmp_path,dates=['2026-10-28'])
    assert result['symbols_total']==3
    assert result['financial_states']=={'SEE_EXISTING_VERIFIED_ACB_REPORT':1,'BLOCKED_UNVERIFIED_UNITS':2}
    folder=tmp_path/'report/dnse_market_analysis_2026-10-28'
    assert (folder/'symbols/EMG.html').exists() and (folder/'assets/plotly.min.js').exists()
    assert 'API trả rỗng' in (folder/'symbols/EMG.html').read_text(encoding='utf-8')
    assert 'Độ sâu' in (folder/'symbols/FPT.html').read_text(encoding='utf-8')
    assert '<script src=\'../assets/plotly.min.js\'>' in (folder/'symbols/FPT.html').read_text(encoding='utf-8')
    again=report_market(root=tmp_path,dates=['2026-10-28'],refresh_pages=True)
    assert again['symbol_states']==result['symbol_states']
    assert hashlib.sha256(archive.path.read_bytes()).hexdigest()==before
    (tmp_path/'data/processed/dnse/manifest.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='CACHE_INPUT_CHANGED'):
        report_market(root=tmp_path,dates=['2026-10-28'])
