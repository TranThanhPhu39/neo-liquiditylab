"""Prepare auditable unit comparisons; never approve units or alter finance gates."""
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

from src.data_pipeline.dnse_history import day_bounds, utc_text
from src.data_pipeline.dnse_normalization import canonical_json, source_time


def candidates(store, day, quotas=None):
    quotas = quotas or {'HOSE':4,'HNX':3,'UPCOM':3}
    start, end = day_bounds(day)
    with closing(store.connect()) as db:
        rows = [dict(row) for row in db.execute('''SELECT symbol,exchange,count(*) AS snapshots
            FROM snapshots WHERE source_time>=? AND source_time<? AND source_local_date=?
            AND symbol!='ACB' GROUP BY symbol,exchange ORDER BY snapshots DESC,symbol''',
            (utc_text(start),utc_text(end),day))]
    selected = []
    for exchange, count in quotas.items():
        selected.extend([row for row in rows if row['exchange']==exchange][:count])
    return selected


def historical_case(store, root, symbol, day, at):
    record = store.reconstruct_at([symbol],day,at).get(symbol)
    if record is None:
        raise ValueError('NO_SOURCE_SNAPSHOT_AT_CUTOFF')
    root = Path(root).resolve()
    with closing(store.connect()) as db:
        origin = db.execute('''SELECT a.path,l.page_id,l.ordinal FROM snapshots s
            JOIN lineage l ON l.snapshot_id=s.id JOIN archives a ON a.id=l.archive_id
            WHERE s.event_key=? ORDER BY a.path,l.page_id,l.ordinal LIMIT 1''',(record['event_id'],)).fetchone()
    if origin is None:
        raise ValueError('RAW_LINEAGE_REQUIRED')
    path = (root/origin['path']).resolve()
    if not path.is_relative_to(root/'data/history/dnse'):
        raise ValueError('RAW_LINEAGE_OUTSIDE_ARCHIVE')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        encoded = db.execute('SELECT payload_gzip FROM pages WHERE id=?',(origin['page_id'],)).fetchone()[0]
    payload = json.loads(gzip.decompress(encoded))
    quote = payload['quotes'][origin['ordinal']]
    identity = 'dnse:'+hashlib.sha256(canonical_json(quote).encode()).hexdigest()
    if identity!=record['event_id']:
        raise ValueError('RAW_CANONICAL_IDENTITY_MISMATCH')
    return dict(schema='dnse-unit-review-v1',case_kind='HISTORICAL_REFERENCE',
                symbol=symbol,exchange=record['exchange'],raw_quote=quote,
                source_time_local=source_time(quote['time']).isoformat(),
                received_at_local=record['received_at_local'],cutoff_local=at.isoformat(),
                source_age_at_cutoff_seconds=(at-source_time(quote['time'])).total_seconds(),
                origin=dict(origin),event_id=identity,price_multiplier_to_vnd='1000',
                quantity_multiplier_to_shares='10',unit_verification_status=record['unit_verification_status'],
                rule_changes_applied=False)


def observation_template(case):
    return dict(symbol=case['symbol'],market_id=case['raw_quote']['marketId'],board_id='G1',
        independent_source_label=None,screenshot_path=None,display_timestamp_local=None,
        display_price_unit=None,display_quantity_unit=None,
        bid=[dict(price=None,quantity=None) for _ in range(3)],
        offer=[dict(price=None,quantity=None) for _ in range(3)])


def finite(value):
    if isinstance(value,bool) or value is None:
        raise ValueError('NUMERIC_OBSERVATION_REQUIRED')
    try:
        result = Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError('NUMERIC_OBSERVATION_REQUIRED') from None
    if not result.is_finite() or result<0:
        raise ValueError('NONNEGATIVE_FINITE_OBSERVATION_REQUIRED')
    return result


def compare_observation(case, observation, *, base=Path('.')):
    """Compare an image transcription, binding its image hash and both clocks.

    Six numerical matches are a *review candidate*, not automatic proof that a
    transcription is faithful or the image is independent. No rule is written.
    """
    quote = case['raw_quote']
    identity = 'dnse:'+hashlib.sha256(canonical_json(quote).encode()).hexdigest()
    if identity!=case['event_id']:
        raise ValueError('RAW_CASE_IDENTITY_MISMATCH')
    if (observation.get('symbol'),observation.get('market_id'),observation.get('board_id'))!=(quote['symbol'],quote['marketId'],'G1'):
        raise ValueError('OBSERVATION_SCOPE_MISMATCH')
    if not observation.get('independent_source_label') or not observation.get('screenshot_path'):
        raise ValueError('INDEPENDENT_SOURCE_AND_IMAGE_REQUIRED')
    image = (Path(base)/observation['screenshot_path']).resolve()
    if image.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not image.is_file():
        raise ValueError('EVIDENCE_IMAGE_REQUIRED')
    if not isinstance(observation.get('display_timestamp_local'),str):
        raise ValueError('DISPLAY_TIMESTAMP_REQUIRED')
    observed = datetime.fromisoformat(observation['display_timestamp_local'])
    if observed.tzinfo is None:
        raise ValueError('OBSERVATION_TIMEZONE_REQUIRED')
    delta = (source_time(quote['time'])-observed).total_seconds()
    if observation.get('display_price_unit') not in ('VND','thousand_VND') or observation.get('display_quantity_unit')!='shares':
        raise ValueError('EXPLICIT_DISPLAY_UNITS_REQUIRED')
    price_factor = Decimal(1000) if observation['display_price_unit']=='thousand_VND' else Decimal(1)
    pairs=[]
    for side in ('bid','offer'):
        raw_levels, display = quote.get(side), observation.get(side)
        if not isinstance(raw_levels,list) or len(raw_levels)<3 or not isinstance(display,list) or len(display)!=3:
            raise ValueError('THREE_LEVELS_EACH_SIDE_REQUIRED')
        seen=set()
        for index,level in enumerate(raw_levels[:3],1):
            price,quantity = finite(level.get('price'))*1000,finite(level.get('quantity'))*10
            if price<=0 or quantity!=quantity.to_integral_value() or price in seen:
                raise ValueError('RAW_LEVEL_NOT_COMPARABLE')
            seen.add(price)
            matches=[v for v in display if finite(v.get('price'))*price_factor==price]
            found = matches[0] if len(matches)==1 else None
            displayed = finite(found['quantity']) if found else None
            if displayed is not None and displayed!=displayed.to_integral_value():
                raise ValueError('DISPLAY_QUANTITY_MUST_BE_WHOLE_SHARES')
            pairs.append(dict(side=side,raw_level=index,price_vnd=str(price),
                              expected_quantity_shares=int(quantity),observed_quantity_shares=int(displayed) if displayed is not None else None,
                              matches=displayed==quantity if displayed is not None else False))
    aligned = abs(delta)<=2
    all_match = aligned and all(pair['matches'] for pair in pairs)
    return dict(status='SIX_PAIRS_MATCH_MANUAL_REVIEW_REQUIRED' if all_match else 'MISMATCH_REVIEW_REQUIRED',
                symbol=quote['symbol'],market_id=quote['marketId'],board_id='G1',event_id=identity,
                source_time_local=source_time(quote['time']).isoformat(),display_time=observed.isoformat(),
                source_minus_display_seconds=delta,clock_within_two_seconds=aligned,pairs=pairs,
                source_label=observation['independent_source_label'],image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                rule_changes_applied=False,finance_scope_unchanged=True,
                note='Numerical comparison of a supplied transcription only; image/source authenticity and scope require review. No global unit certification.')
