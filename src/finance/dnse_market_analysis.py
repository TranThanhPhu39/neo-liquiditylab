"""Bounded, descriptive comparisons for all DNSE equities, without unit approval."""
from collections import Counter
from contextlib import closing
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
from statistics import median
import zlib

from src.data_pipeline.dnse_history import day_bounds, utc_text
from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.finance.liquidity_metrics import liquidity_metrics
from src.models.orderbook import LOCAL_TZ

SOURCE_METRICS = ('bid_depth_source','ask_depth_source','spread_bps','obi')
VERIFIED = ('EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED')


def exchange_grid(day, exchange):
    if exchange not in ('HOSE','HNX','UPCOM'):
        raise ValueError('UNSUPPORTED_EQUITY_EXCHANGE')
    selected = datetime.fromisoformat(day).date()
    begin = time(9,15) if exchange=='HOSE' else time(9)
    finish = time(15) if exchange=='UPCOM' else time(14,30)
    for session, start, end in [('AM',begin,time(11,30)),('PM',time(13),finish)]:
        at = datetime.combine(selected,start,LOCAL_TZ)
        stop = datetime.combine(selected,end,LOCAL_TZ)
        while at < stop:
            yield session, at.replace(second=59,microsecond=999999)
            at += timedelta(minutes=1)


def inventory(db, dates):
    """Archive metadata includes API-empty symbols and missing catalog days."""
    result = {}
    for row in db.execute('''SELECT a.settings,c.symbol,c.exchange,c.status,c.raw_quote_count
            FROM raw_catalog c JOIN archives a ON a.id=c.archive_id'''):
        settings = json.loads(row['settings'])
        day = settings['date']
        if day not in dates:
            continue
        item = result.setdefault(row['symbol'],dict(symbol=row['symbol'],days={}))
        daily = item['days'].setdefault(day,dict(exchange=row['exchange'],raw_records=0,archive_statuses=[],collected_through=0))
        daily['raw_records'] = max(daily['raw_records'],row['raw_quote_count'])
        daily['archive_statuses'].append(row['status'])
        daily['collected_through'] = max(daily['collected_through'],settings['to'])
    for day in dates:
        for row in db.execute('''SELECT symbol,count(*) AS snapshots,min(source_time) AS first_source_time,
                max(source_time) AS last_source_time FROM snapshots WHERE source_local_date=? GROUP BY symbol''',(day,)):
            if row['symbol'] in result and day in result[row['symbol']]['days']:
                result[row['symbol']]['days'][day].update(dict(row))
    for item in result.values():
        for daily in item['days'].values():
            daily.setdefault('snapshots',0)
            daily.setdefault('first_source_time',None)
            daily.setdefault('last_source_time',None)
    return result


def latest_grid_rows(db, symbol, day, grid):
    """One indexed SQL statement per symbol/day; at most 270 payloads in memory."""
    points = list(grid)
    start, _ = day_bounds(day)
    values = ','.join('(?)' for _ in points)
    query = f'''WITH cutoffs(cutoff) AS (VALUES {values})
        SELECT c.cutoff,s.event_key,s.source_time,s.received_at,s.quality,s.units_status,s.market_session,s.payload_zlib
        FROM cutoffs c LEFT JOIN snapshots s ON s.id=(
            SELECT id FROM snapshots WHERE symbol=? AND source_time>=? AND source_time<=c.cutoff
            ORDER BY source_time DESC,id DESC LIMIT 1) ORDER BY c.cutoff'''
    params = [utc_text(at) for _,at in points]+[symbol,utc_text(start)]
    return db.execute(query,params)


def analyse_symbol(db, item, dates, max_age_seconds=60):
    if not 0 < max_age_seconds <= 600:
        raise ValueError('INVALID_SOURCE_AGE_LIMIT')
    symbol = item['symbol']
    rows, units = [], set()
    for day in dates:
        daily = item['days'].get(day)
        if daily is None:
            continue
        points = list(exchange_grid(day,daily['exchange']))
        if not daily['snapshots']:
            source_rows = [None]*len(points)
        else:
            source_rows = latest_grid_rows(db,symbol,day,points)
        for (session,at),source in zip(points,source_rows):
            row = dict(date=day,session=session,clock=at.strftime('%H:%M'),exchange=daily['exchange'],
                cutoff_local=at.isoformat(),eligible=False,matched_across_days=False,reason='NO_EVENT_BEFORE_CUTOFF',
                source_time_local=None,received_at_utc=None,age_seconds=None,event_id=None,
                unit_verification_status=None,**{key:None for key in SOURCE_METRICS})
            if at.timestamp()>daily['collected_through']:
                row['reason']='NOT_COLLECTED_THROUGH_CUTOFF'
            elif not daily['snapshots']:
                row['reason']='API_EMPTY_RESPONSE' if 'NO_DATA_RETURNED' in daily['archive_statuses'] else 'NO_NORMALIZED_RECORDS'
            elif source is not None and source['event_key'] is not None:
                observed = datetime.fromisoformat(source['source_time'])
                age = (at-observed).total_seconds()
                units.add(source['units_status'])
                row.update(event_id=source['event_key'],source_time_local=observed.astimezone(LOCAL_TZ).isoformat(),
                           received_at_utc=source['received_at'],age_seconds=age,unit_verification_status=source['units_status'])
                if source['market_session']!='DNSE_CONTINUOUS_'+session:
                    row['reason']='OUTSIDE_SELECTED_CONTINUOUS_SESSION'
                elif not 0<=age<=max_age_seconds:
                    row['reason']='STALE_OR_FUTURE'
                elif source['quality']=='INVALID':
                    row['reason']='INVALID_BOOK'
                else:
                    record = json.loads(zlib.decompress(source['payload_zlib']))
                    book = snapshot_from_record(record)
                    if book.exchange!=daily['exchange']:
                        row['reason']='SOURCE_EXCHANGE_MISMATCH'
                    elif not all(level.usable for level in book.bids+book.asks):
                        row['reason']='INCOMPLETE_TOP3'
                    else:
                        try:
                            metrics = liquidity_metrics(book)
                            row.update(eligible=True,reason='ELIGIBLE_DESCRIPTIVE',
                                bid_depth_source=Decimal(metrics['bid_depth'])/10,
                                ask_depth_source=Decimal(metrics['ask_depth'])/10,
                                spread_bps=metrics['relative_spread']*10000 if metrics['relative_spread'] is not None else None,
                                obi=metrics['obi'])
                        except ValueError:
                            row['reason']='INVALID_BOOK'
            rows.append(row)
    shared = set.intersection(*[
        {(row['session'],row['clock']) for row in rows if row['date']==day and row['eligible']} for day in dates])
    for row in rows:
        row['matched_across_days'] = row['eligible'] and (row['session'],row['clock']) in shared
    summaries=[]
    for day in dates:
        for session in ('AM','PM'):
            selected = [r for r in rows if r['date']==day and r['session']==session]
            matched = [r for r in selected if r['matched_across_days']]
            all_fresh = [r for r in selected if r['eligible']]
            def med(values,key):
                numbers=[r[key] for r in values if r[key] is not None]
                return median(numbers) if numbers else None
            summaries.append(dict(date=day,session=session,grid_points=len(selected),
                eligible_points=len(all_fresh),matched_points=len(matched),
                catalog_present=day in item['days'],
                exclusion_reasons=dict(Counter(r['reason'] for r in selected if not r['eligible'])),
                matched_medians={key:med(matched,key) for key in SOURCE_METRICS},
                daily_fresh_medians={key:med(all_fresh,key) for key in SOURCE_METRICS}))
    records=sum(day['snapshots'] for day in item['days'].values())
    if not records:
        state='NO_QUOTES_IN_SELECTED_ARCHIVES'
    elif not shared:
        state='NO_COMMON_FRESH_COMPLETE_MINUTES'
    else:
        state='DESCRIPTIVE_COMPARISON_READY'
    # This module deliberately contains no execution simulator or unit override.
    financial = 'SEE_EXISTING_VERIFIED_ACB_REPORT' if symbol=='ACB' and units and units.issubset(VERIFIED) else 'BLOCKED_UNVERIFIED_UNITS'
    return dict(symbol=symbol,dates=dates,state=state,financial_status=financial,
        observed_unit_statuses=sorted(units),source_quantity_unit='DNSE raw quantity; not certified as shares',
        common_minutes_each_day=len(shared),raw_inventory=item['days'],summaries=summaries,grid=rows,
        method='Minute-end latest source event, max age 60s, complete top3 both sides; common eligible clocks across dates',
        max_age_seconds=max_age_seconds,rule_changes_applied=False,financial_simulation_performed=False)
