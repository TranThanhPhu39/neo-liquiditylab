"""Browser entry point. Runs the existing Decimal finance core unchanged in Pyodide."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
import math

from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.data_pipeline.validation import validate
from src.finance.execution_simulator import simulate_execution
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.portfolio_liquidation import liquidate_portfolio
from src.finance.stress_testing import apply_scenario, reverse_stress
from src.models.orderbook import LOCAL_TZ
from src.models.portfolio import parse_portfolio
from src.models.scenario import Scenario

_records = []
_catalogs = {}


def encode(value):
    return json.dumps(value, default=lambda x: str(x) if isinstance(x, Decimal) else x.isoformat(), ensure_ascii=False)


def portfolio_symbols(text):
    return encode([position.symbol for position in parse_portfolio(text)])


def ingest(text):
    """Validate an atomic import. Never multiply already-canonical units again."""
    global _records, _catalogs
    payload = json.loads(text)
    records = payload.get('records', [payload]) if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records or len(records) > 30000:
        raise ValueError('Cần từ 1 đến 30.000 snapshot DNSE chuẩn hóa.')
    clean = []
    for record in records:
        if record.get('price_unit') != 'VND' or record.get('volume_unit') != 'shares':
            raise ValueError('Chỉ nhận schema chuẩn hóa VND / shares; không nhận raw API.')
        book = snapshot_from_record(record)
        if book.source_timestamp is None or book.source_timestamp.tzinfo is None:
            raise ValueError('Thiếu múi giờ nguồn.')
        for level in book.bids + book.asks:
            if level.price is not None and (not level.price.is_finite() or level.price <= 0):
                raise ValueError('Giá phải hữu hạn và dương hoặc null.')
            if level.volume is not None and (isinstance(level.volume, bool) or not isinstance(level.volume, int) or not 0 <= level.volume <= 10**15):
                raise ValueError('Khối lượng phải nguyên, không âm hoặc null.')
        # Do not trust a caller-supplied financial_eligible flag.
        eligible = any(level.usable for level in book.bids + book.asks)
        try:
            simulate_execution(book, 1)
        except ValueError:
            eligible = False
        clean.append({**record, 'financial_eligible': eligible,
                      'local_date': book.source_timestamp.astimezone(LOCAL_TZ).date().isoformat()})
    catalogs = {}
    if isinstance(payload, dict) and isinstance(payload.get('catalog'), dict):
        cutoff = datetime.fromisoformat(payload['cutoff']).astimezone(LOCAL_TZ)
        catalogs[cutoff.date().isoformat()] = payload['catalog']
    _records = sorted(clean, key=lambda r: (r['source_timestamp'], r.get('event_id', '')))
    _catalogs = catalogs
    days = sorted({r['local_date'] for r in _records})
    return encode({'days': days, 'count': len(_records),
                   'symbols': {day: sorted({r['symbol'] for r in _records if r['local_date']==day} | set(_catalogs.get(day, {}))) for day in days}})


def view(request):
    q = json.loads(request)
    day = q['day']
    at = datetime.fromisoformat(day + 'T' + q.get('time', '23:59') + ':59.999999+07:00')
    today = [r for r in _records if r['local_date'] == day]
    selected = [r for r in today if datetime.fromisoformat(r['source_timestamp']) <= at]
    latest = {}
    for r in selected:
        if r['symbol'] not in latest or datetime.fromisoformat(r['source_timestamp']) >= datetime.fromisoformat(latest[r['symbol']]['source_timestamp']):
            latest[r['symbol']] = r
    symbol = q['symbol']
    record = latest.get(symbol)
    catalog = _catalogs.get(day, {})
    out = {'symbols': sorted({r['symbol'] for r in today} | set(catalog)), 'watchlist': [], 'record': record,
           'at': at.isoformat(), 'rows': len(selected), 'metrics': None}
    for s in out['symbols']:
        r = latest.get(s, {})
        entry = {k: r.get(k) for k in ('symbol', 'exchange', 'bid_observed_depth_shares',
            'ask_observed_depth_shares', 'unit_verification_status', 'financial_eligible', 'source_local_time')}
        entry.update(symbol=s, exchange=r.get('exchange', catalog.get(s, {}).get('exchange')), has_snapshot=bool(r))
        out['watchlist'].append(entry)
    if record:
        book = snapshot_from_record(record)
        try:
            out['metrics'] = liquidity_metrics(book)
        except ValueError as error:
            out['quality_error'] = str(error)
        # Bounded chart, same-source-day and no future points.
        samples = [r for r in selected if r['symbol'] == symbol]
        stride = max(1, math.ceil(len(samples) / 300))
        sampled = samples[::stride]
        if samples and sampled[-1] is not samples[-1]:
            sampled.append(samples[-1])
        out['history'] = [{k: r.get(k) for k in ('source_local_time', 'bid_observed_depth_shares', 'ask_observed_depth_shares')} for r in sampled]
        if q.get('page') == 'execution':
            try:
                qty = q.get('quantity', 150000)
                args = dict(side=q.get('side', 'sell'), benchmark=q.get('benchmark', 'best'),
                            fee_rate=Decimal(str(q.get('fee_percent', 0))) / 100,
                            sell_tax_rate=Decimal(str(q.get('tax_percent', 0))) / 100)
                execution = simulate_execution(book, qty, **args)
                out['execution'] = asdict(execution)
                out['execution']['fill_values'] = [str(f.price*f.quantity) for f in execution.fills]
                sizes = sorted({1, qty, *[max(1, qty * i // 10) for i in range(1, 21)]})
                out['curve'] = [dict(quantity=n, coverage=str(r.coverage_ratio), cost=str(r.book_walking_cost) if r.book_walking_cost is not None else None)
                                for n in sizes for r in [simulate_execution(book, n, **args)]]
            except (ValueError, ArithmeticError) as error:
                out['execution_error'] = str(error)
    if q.get('page') == 'stress':
        try:
            positions = parse_portfolio(q.get('portfolio', 'symbol,quantity\nACB,150000'))
            books = {s: snapshot_from_record(r) for s, r in latest.items() if s in {p.symbol for p in positions}}
            args = dict(now=at, allow_stale=True, max_skew_seconds=60)
            out['baseline'] = liquidate_portfolio(positions, books, **args)
            shock = Decimal(str(q.get('shock', 50))) / 100
            out['stressed'] = liquidate_portfolio(positions, {s: apply_scenario(b, Scenario(shock)) for s, b in books.items()}, **args)
            if q.get('reverse') and symbol in books:
                out['reverse'] = reverse_stress(books[symbol], q.get('quantity', 150000), Decimal(str(q.get('threshold', 70))) / 100)
        except (ValueError, ArithmeticError) as error:
            out['stress_error'] = str(error)
    return encode(out)
