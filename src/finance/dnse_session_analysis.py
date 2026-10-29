"""Equal-clock, retrospective A1/B5 comparisons from immutable DNSE history."""
from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.finance.execution_simulator import simulate_execution
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.stress_testing import apply_scenario
from src.models.orderbook import LOCAL_TZ
from src.models.scenario import Scenario

QUANTITIES = (10000, 50000, 150000, 500000)
SHOCKS = (Decimal('0'), Decimal('.25'), Decimal('.50'), Decimal('.75'))
METRICS = ('bid_depth', 'ask_depth', 'spread_bps', 'obi', 'sell_coverage_150000',
           'buy_coverage_150000', 'sell_cost_bps_150000', 'sell_cost_vnd_150000',
           'stress50_coverage_150000')


def minute_grid(day):
    """225 minute-end cutoffs: HOSE continuous AM and PM, no closing auction."""
    selected = datetime.fromisoformat(day).date()
    for name, start, end in [('AM', time(9,15), time(11,30)), ('PM', time(13), time(14,30))]:
        point = datetime.combine(selected, start, LOCAL_TZ)
        stop = datetime.combine(selected, end, LOCAL_TZ)
        while point < stop:
            yield name, point.replace(second=59, microsecond=999999)
            point += timedelta(minutes=1)


def execution_row(result, base):
    return dict(base, side=result.side, quantity=result.requested_quantity,
                executed=result.executed_quantity, uncovered=result.uncovered_quantity,
                coverage=result.coverage_ratio, vwap_vnd=result.vwap,
                gross_vnd=result.gross_value, cost_vnd=result.book_walking_cost,
                cost_bps=result.cost_bps, benchmark_value_vnd=result.benchmark_value)


def decimal_median(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return median(values) if values else None


def analyse_sessions(store, dates, symbol='ACB', max_age_seconds=60):
    """Use the last event, not the last *valid* event, at each minute cutoff.

    This is explicitly ex-post event-time reconstruction. Receipt clocks remain
    real. Reject stale/partial/invalid/unknown-unit books rather than forward-fill
    them indefinitely. Compare days only on the intersection of eligible clocks.
    """
    dates = sorted(set(dates))
    if not dates or not 0 < max_age_seconds <= 600:
        raise ValueError('INVALID_SESSION_ANALYSIS_SETTINGS')
    if symbol != 'ACB':
        raise ValueError('REPORT_SCOPE_ACB_HOSE_ONLY')
    if any(day not in store.dates() for day in dates):
        raise ValueError('REQUESTED_SOURCE_DAY_NOT_AVAILABLE')
    grid, execution, stress = [], [], []
    for day in dates:
        for session, cutoff in minute_grid(day):
            row = dict(date=day, session=session, clock=cutoff.strftime('%H:%M'),
                       cutoff_local=cutoff.isoformat(), symbol=symbol, eligible=False,
                       reason='NO_DATA', matched_across_days=False, event_id=None,
                       source_time_local=None, received_at_local=None, age_seconds=None,
                       **{key: None for key in METRICS})
            records = store.reconstruct_at([symbol], day, cutoff)
            record = records.get(symbol)
            if record is None:
                grid.append(row)
                continue
            book = snapshot_from_record(record)
            row.update(event_id=book.event_id, source_time_local=book.source_timestamp.astimezone(LOCAL_TZ).isoformat(),
                       received_at_local=book.received_at_local.isoformat(),
                       age_seconds=(cutoff-book.source_timestamp).total_seconds())
            if book.exchange != 'HOSE' or book.unit_verification_status not in ('EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED'):
                row['reason'] = 'UNITS_OR_EXCHANGE_NOT_VERIFIED'
            elif book.market_session != 'DNSE_CONTINUOUS_' + session:
                row['reason'] = 'OUTSIDE_SELECTED_CONTINUOUS_SESSION'
            elif not 0 <= row['age_seconds'] <= max_age_seconds:
                row['reason'] = 'STALE_OR_FUTURE'
            elif book.data_quality_status == 'INVALID':
                row['reason'] = 'INVALID_BOOK'
            elif not all(level.usable for level in book.bids+book.asks):
                row['reason'] = 'INCOMPLETE_TOP3'
            else:
                try:
                    metrics = liquidity_metrics(book)
                    base = dict(date=day, session=session, clock=row['clock'], event_id=book.event_id)
                    point_execution, point_stress = [], []
                    for quantity in QUANTITIES:
                        for side in ('sell','buy'):
                            result = simulate_execution(book, quantity, side=side)
                            point_execution.append(execution_row(result, base))
                            if quantity == 150000:
                                row[side+'_coverage_150000'] = result.coverage_ratio
                                if side == 'sell':
                                    row['sell_cost_bps_150000'] = result.cost_bps
                                    row['sell_cost_vnd_150000'] = result.book_walking_cost
                        for shock in SHOCKS:
                            result = simulate_execution(apply_scenario(book, Scenario(shock)), quantity)
                            point_stress.append(dict(execution_row(result, base), depth_reduction=shock))
                            if quantity == 150000 and shock == Decimal('.50'):
                                row['stress50_coverage_150000'] = result.coverage_ratio
                    row.update(eligible=True, reason='ELIGIBLE', bid_depth=metrics['bid_depth'],
                               ask_depth=metrics['ask_depth'], spread_bps=metrics['relative_spread']*10000
                               if metrics['relative_spread'] is not None else None, obi=metrics['obi'])
                    execution.extend(point_execution)
                    stress.extend(point_stress)
                except ValueError:
                    for key in METRICS:
                        row[key] = None
                    row['reason'] = 'FINANCIAL_GUARD_REJECTED'
            grid.append(row)
    clocks_by_date = [{(r['session'], r['clock']) for r in grid if r['date']==day and r['eligible']} for day in dates]
    shared = set.intersection(*clocks_by_date)
    if not shared:
        raise ValueError('NO_COMMON_VERIFIED_MINUTES')
    for row in grid:
        row['matched_across_days'] = row['eligible'] and (row['session'], row['clock']) in shared
    for row in execution+stress:
        row['matched_across_days'] = (row['session'], row['clock']) in shared
    summaries = []
    for day in dates:
        for session in ('AM','PM'):
            all_rows = [r for r in grid if r['date']==day and r['session']==session]
            matched = [r for r in all_rows if r['matched_across_days']]
            summaries.append(dict(date=day, session=session, grid_points=len(all_rows),
                eligible_points=sum(r['eligible'] for r in all_rows), matched_points=len(matched),
                exclusion_reasons=dict(Counter(r['reason'] for r in all_rows if not r['eligible'])),
                medians={key: decimal_median(matched,key) for key in METRICS},
                full_sell_coverage_share_150000=Decimal(sum(r['sell_coverage_150000']==1 for r in matched))/len(matched) if matched else None,
                stress50_min_coverage_150000=min((r['stress50_coverage_150000'] for r in matched),default=None),
                stress50_full_coverage_share_150000=Decimal(sum(r['stress50_coverage_150000']==1 for r in matched))/len(matched) if matched else None))
    return dict(symbol=symbol,dates=dates,max_age_seconds=max_age_seconds,
                sampling='minute_end_latest_event_common_eligible_clocks',benchmark='best',
                fee_rate='0',sell_tax_rate='0',quantities=QUANTITIES,depth_reductions=SHOCKS,
                windows={day:store.window(symbol,day) for day in dates},summaries=summaries,
                grid=grid,execution=execution,stress=stress)
