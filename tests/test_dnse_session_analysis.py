from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json

import pytest

from test_dnse_normalization import quote, rules
from src.data_pipeline.dnse_normalization import normalize_quote
from src.finance.dnse_session_analysis import analyse_sessions, minute_grid
from scripts.report_dnse_sessions import write_report


class GridStore:
    def __init__(self, records):
        self.records=records

    def dates(self):
        return ['2026-10-28','2026-10-29']

    def reconstruct_at(self,symbols,day,at):
        record=self.records.get((day,at.strftime('%H:%M')))
        return {'ACB':record} if record else {}

    def window(self,symbol,day):
        return dict(snapshots=sum(key[0]==day for key in self.records))


@pytest.fixture
def grid_store(quote,rules):
    records={}
    for day in ('2026-10-28','2026-10-29'):
        for clock in ('09:15','14:20'):
            raw={**deepcopy(quote),'time':day+' '+clock+':50.000'}
            records[(day,clock)]=normalize_quote(raw,day+'T22:00:00+00:00',rules)
    outlier=deepcopy(quote)
    outlier['time']='2026-10-28 09:16:50.000'
    for level in outlier['bid']:
        level['quantity']*=100
    records[('2026-10-28','09:16')]=normalize_quote(outlier,'2026-10-28T22:00:00+00:00',rules)
    stale={**deepcopy(quote),'time':'2026-10-29 09:15:00.000'}
    records[('2026-10-29','09:16')]=normalize_quote(stale,'2026-10-29T22:00:00+00:00',rules)
    return GridStore(records)


def test_grid_excludes_auctions_and_lunch_and_never_uses_next_minute():
    grid=list(minute_grid('2026-10-29'))
    assert len(grid)==225
    assert sum(s=='AM' for s,_ in grid)==135
    assert grid[0][1].isoformat()=='2026-10-29T09:15:59.999999+07:00'
    assert grid[134][1].strftime('%H:%M')=='11:29'
    assert grid[-1][1].strftime('%H:%M')=='14:29'


def test_equal_clock_sample_excludes_stale_and_unmatched_outlier(grid_store):
    report=analyse_sessions(grid_store,grid_store.dates())
    first=report['summaries'][0]
    assert first['eligible_points']==2 and first['matched_points']==1
    assert first['medians']['bid_depth']==301100  # The unmatched 100x outlier has no weight.
    second=report['summaries'][2]
    assert second['exclusion_reasons']['STALE_OR_FUTURE']==1
    assert len(report['grid'])==450
    assert sum(r['matched_across_days'] for r in report['grid'])==4
    row=next(r for r in report['execution'] if r['date']=='2026-10-29' and r['clock']=='09:15' and r['quantity']==150000 and r['side']=='sell')
    assert row['gross_vnd']==Decimal('3177430000') and row['cost_vnd']==Decimal('2570000')
    stress=next(r for r in report['stress'] if r['date']=='2026-10-29' and r['clock']=='09:15' and r['quantity']==150000 and r['depth_reduction']==Decimal('.75'))
    assert stress['executed']==75275


def test_newest_invalid_partial_and_future_points_are_excluded(grid_store,quote,rules):
    for clock,kind in [('09:17','invalid'),('09:18','partial'),('09:19','future')]:
        raw={**deepcopy(quote),'time':'2026-10-29 '+clock+':50.000'}
        if kind=='invalid':
            raw['bid'][0]['price']=22
        elif kind=='partial':
            raw['bid']=raw['bid'][:2]
        else:
            raw['time']='2026-10-29 09:20:00.000'
        grid_store.records[('2026-10-29',clock)]=normalize_quote(raw,'2026-10-29T22:00:00+00:00',rules)
    report=analyse_sessions(grid_store,grid_store.dates())
    group=report['summaries'][2]
    assert group['exclusion_reasons']['INVALID_BOOK']==1
    assert group['exclusion_reasons']['INCOMPLETE_TOP3']==1
    assert group['exclusion_reasons']['STALE_OR_FUTURE']==2
    assert all(r['clock'] not in ('09:17','09:18','09:19') for r in report['execution'] if r['date']=='2026-10-29')


def test_unit_guard_is_not_relaxed_by_report(grid_store):
    for record in grid_store.records.values():
        record['unit_verification_status']='ASSUMED_SHARED_SCHEMA'
    with pytest.raises(ValueError,match='NO_COMMON_VERIFIED_MINUTES'):
        analyse_sessions(grid_store,grid_store.dates())


def test_stress_tail_is_reported_even_when_median_coverage_is_full(grid_store,quote,rules):
    for day in grid_store.dates():
        for clock in ('09:20','09:21'):
            raw={**deepcopy(quote),'time':day+' '+clock+':50.000'}
            if clock=='09:20':
                for level in raw['bid']:
                    level['quantity']//=10
            grid_store.records[(day,clock)]=normalize_quote(raw,day+'T22:00:00+00:00',rules)
    group=analyse_sessions(grid_store,grid_store.dates())['summaries'][0]
    assert group['medians']['stress50_coverage_150000']==1
    assert group['stress50_min_coverage_150000']==Decimal(15055)/150000
    assert group['stress50_full_coverage_share_150000']==Decimal(2)/3


def test_standalone_report_has_exact_money_and_no_external_script(grid_store,tmp_path):
    report=analyse_sessions(grid_store,grid_store.dates())
    stem=tmp_path/'analysis'
    result=write_report(report,stem)
    assert result['common_minutes_per_day']==2
    payload=json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'))
    assert payload['execution'][0]['gross_vnd']==str(report['execution'][0]['gross_vnd'])
    content=stem.with_suffix('.html').read_text(encoding='utf-8')
    assert '<html lang=' in content and 'Plotly.newPlot' in content
    assert '<script src=' not in content
    assert 'STALE_OR_FUTURE' in content
    assert stem.with_suffix('.grid.csv').exists() and stem.with_suffix('.stress.csv').exists()
