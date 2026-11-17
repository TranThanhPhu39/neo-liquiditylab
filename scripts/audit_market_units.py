"""Audit any catalog symbols against saved independent board references; no promotion."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from src.data_pipeline.market_unit_evidence import compare, read_maybank
from src.data_pipeline.parquet_history import ParquetHistory, checked_folder


def audit(root, day, references, *, symbols=None, max_seconds=5):
    root = Path(root).resolve()
    source = ParquetHistory(root / 'data/compact/catalog.json')
    if day not in source.dates():
        raise ValueError('NORMALIZED_PARQUET_DAY_REQUIRED')
    catalog = source.catalog(day)
    selected = sorted(set(symbols or catalog))
    if not selected or any(s not in catalog for s in selected):
        raise ValueError('UNIT_AUDIT_CATALOG_SYMBOL_REQUIRED')
    observations, rejected, artifacts = [], [], []
    for reference in references:
        path = Path(reference).resolve()
        if not path.is_relative_to(root / 'data/raw'):
            raise ValueError('UNIT_REFERENCE_RAW_PATH_REQUIRED')
        encoded = path.read_bytes()
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError('UNIT_REFERENCE_TOO_LARGE')
        refs, errors = read_maybank(json.loads(encoded))
        observations.extend(refs)
        rejected.extend(dict(artifact=path.relative_to(root).as_posix(), **error) for error in errors)
        artifacts.append(dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(encoded).hexdigest()))
    # Verify the saved normalized day before comparing its records; never use SQLite.
    checked_folder(root, source.days[day])
    candidates = {(r['symbol'], r['day'], r['exchange']) for r in observations}
    results = []
    for symbol in selected:
        exchange = catalog[symbol]['exchange']
        records = source.records(symbol, day) if (symbol, day, exchange) in candidates else []
        results.append(compare(symbol, day, exchange, records, observations, max_seconds=max_seconds))
    return dict(format='liquiditylab-market-unit-audit-v1', day=day, checked_at_utc=datetime.now(timezone.utc).isoformat(),
        symbols=len(selected), status_counts=dict(Counter(r['status'] for r in results)), references=artifacts,
        max_seconds=max_seconds, results=results, rejected_reference_rows=rejected,
        contracts_changed=False, detector_permissions_changed=False, raw_or_parquet_changed=False,
        limitations=['Numerical candidates require source/layout review before unit certification.',
                    'No verification of trade bubbles, historical coverage, feed completeness, or manipulation.',
                    'A quote certificate is scoped by symbol, exchange, G1 board, API version and unit rule.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True)
    parser.add_argument('--reference', action='append', required=True)
    parser.add_argument('--symbols', help='Comma-separated catalog symbols; omitted means the whole day catalog.')
    parser.add_argument('--window-seconds', type=int, choices=(5, 20), default=5)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = audit(ROOT, args.day, args.reference, symbols=args.symbols.split(',') if args.symbols else None,
                   max_seconds=args.window_seconds)
    path = Path(args.output) if args.output else ROOT / 'report' / f'market_unit_audit_{args.day}_{args.window_seconds}s.json'
    if not path.resolve().is_relative_to(ROOT / 'report'):
        raise ValueError('UNIT_AUDIT_REPORT_PATH_REQUIRED')
    atomic(path, result)
    print(json.dumps({k: result[k] for k in ('day', 'symbols', 'status_counts', 'contracts_changed', 'detector_permissions_changed')}))
