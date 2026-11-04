"""Exact-source-clock WS/REST comparisons; never approve a unit rule by name alone."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from src.models.orderbook import LOCAL_TZ


def decimal(value):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError()
        number=Decimal(str(value))
        if not number.is_finite() or number<0:
            raise ValueError()
        return number
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError('REVIEW_FINITE_NONNEGATIVE_NUMBER_REQUIRED') from None


def rest_clock(value):
    """Return a timestamp precision interval, not a loose matching tolerance."""
    if not isinstance(value,str):
        raise ValueError('REVIEW_REST_CLOCK_REQUIRED')
    match=re.fullmatch(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{1,9})',value)
    if not match:
        raise ValueError('REVIEW_REST_CLOCK_FORMAT')
    at=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    delta=at-datetime(1970,1,1,tzinfo=timezone.utc)
    ns=(delta.days*86400+delta.seconds)*10**9+int(match[2].ljust(9,'0'))
    return ns,10**(9-len(match[2]))


def six_levels(record,kind):
    result=[]
    for side in ('bid','ask'):
        levels=record.get(side+'_levels') if kind=='ws' else record.get('bid' if side=='bid' else 'offer')
        if not isinstance(levels,list) or len(levels)<3:
            raise ValueError('REVIEW_SIX_COMPLETE_LEVELS_REQUIRED')
        for index,level in enumerate(levels[:3],1):
            price=decimal(level.get('source_price' if kind=='ws' else 'price'))
            qty=decimal(level.get('source_quantity' if kind=='ws' else 'quantity'))
            if price<=0:
                raise ValueError('REVIEW_POSITIVE_PRICE_REQUIRED')
            result.append((side,index,price,qty))
    return result


def pair_factors(ws,rest,price_multiplier,volume_multiplier):
    price_ratios=set();qty_ratios=set()
    pairs=[]
    for (side,index,wp,wq),(_,_,rp,rq) in zip(six_levels(ws,'ws'),six_levels(rest,'rest')):
        price_ratios.add(rp*price_multiplier/wp)
        if wq:
            qty_ratios.add(rq*volume_multiplier/wq)
        elif rq:
            raise ValueError('REVIEW_ZERO_QUANTITY_MISMATCH')
        pairs.append(dict(side=side,level=index,ws_price=str(wp),ws_quantity=str(wq),
                          rest_price=str(rp),rest_quantity=str(rq)))
    if len(price_ratios)!=1 or len(qty_ratios)!=1:
        raise ValueError('REVIEW_INCONSISTENT_LEVEL_FACTORS')
    p,q=next(iter(price_ratios)),next(iter(qty_ratios))
    if p<=0 or q<=0:
        raise ValueError('REVIEW_POSITIVE_MULTIPLIERS_REQUIRED')
    if any(decimal(pair['ws_quantity'])*q!=(decimal(pair['ws_quantity'])*q).to_integral_value() for pair in pairs):
        raise ValueError('REVIEW_FRACTIONAL_NORMALIZED_SHARES')
    return (str(p.normalize()),str(q.normalize())),pairs


def compare(ws_records,rest_quotes,*,symbol,market,price_multiplier=Decimal(1000),
            volume_multiplier=Decimal(10),rest_unit_verified=False,min_matches=20):
    if isinstance(min_matches,bool) or not isinstance(min_matches,int) or min_matches<20:
        raise ValueError('REVIEW_MINIMUM_TWENTY_DISTINCT_CLOCKS_REQUIRED')
    buckets=defaultdict(list)
    rest_valid=0
    seen=set()
    for rest in rest_quotes:
        if (rest.get('symbol'),rest.get('marketId'),rest.get('boardId'))!=(symbol,market,'G1'):
            raise ValueError('REVIEW_REST_SCOPE_MISMATCH')
        ns,width=rest_clock(rest['time'])
        try:
            values=six_levels(rest,'rest')
        except ValueError:
            continue
        identity=(ns,width,tuple(values))
        if identity in seen:
            continue
        seen.add(identity);rest_valid+=1
        buckets[ns//10**9].append((ns,width,rest))
    counts=Counter();factor_counts=Counter();matched=[];mismatches=[];complete_clocks=set()
    for ws in ws_records:
        if (ws.get('symbol'),ws.get('market_id'),ws.get('board_id'))!=(symbol,market,'G1'):
            raise ValueError('REVIEW_WS_SCOPE_MISMATCH')
        counts['ws_events']+=1
        if ws.get('source_unix_nanoseconds') is None:
            counts['unknown_clock']+=1;continue
        try:
            six_levels(ws,'ws')
        except ValueError:
            counts['incomplete_or_auction_ws']+=1;continue
        tick=int(ws['source_unix_nanoseconds'])
        complete_clocks.add(tick)
        candidates=[r for ns,width,r in buckets[tick//10**9] if ns<=tick<ns+width]
        if not candidates:
            counts['no_same_source_clock']+=1;continue
        outcomes=[]
        for rest in candidates:
            try:
                factors,pairs=pair_factors(ws,rest,price_multiplier,volume_multiplier)
                outcomes.append((factors,pairs,rest['time']))
            except ValueError:
                pass
        distinct={o[0] for o in outcomes}
        if len(distinct)!=1:
            key='same_clock_mismatch' if not distinct else 'ambiguous_factors'
            counts[key]+=1
            mismatches.append(dict(ws_source_timestamp=ws['source_timestamp'],reason=key,
                                   rest_candidate_count=len(candidates)))
            continue
        factors,pairs,rest_time=outcomes[0]
        counts['six_pairs_match']+=1;factor_counts[factors]+=1
        matched.append(dict(ws_source_timestamp=ws['source_timestamp'],ws_unix_nanoseconds=str(tick),
            rest_source_time=rest_time,price_multiplier_to_vnd=factors[0],volume_multiplier_to_shares=factors[1],pairs=pairs))
    distinct_matched=len({r['ws_unix_nanoseconds'] for r in matched})
    consistent=len(factor_counts)==1 and not counts['same_clock_mismatch'] and not counts['ambiguous_factors']
    enough=consistent and distinct_matched>=min_matches
    status='EMPIRICAL_TOP3_CROSSCHECK_PASSED' if enough and rest_unit_verified else 'RELATIVE_SCALE_MATCH_ONLY' if enough else 'INSUFFICIENT_OR_CONFLICTING_EVIDENCE'
    return dict(status=status,symbol=symbol,market_id=market,board_id='G1',counts=dict(counts),
        rest_complete_quotes=rest_valid,distinct_complete_ws_clocks=len(complete_clocks),
        distinct_matched_clocks=distinct_matched,minimum_matches=min_matches,
        factor_candidates=[dict(price_multiplier_to_vnd=p,volume_multiplier_to_shares=q,matches=n) for (p,q),n in factor_counts.items()],
        rest_unit_verified=rest_unit_verified,top3_independent_message_evidence=enough,
        full_ten_level_semantics_verified=False,rule_changes_applied=False,
        matched=matched,mismatches=mismatches)
