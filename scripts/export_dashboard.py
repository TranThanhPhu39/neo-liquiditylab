"""Export a bounded, local-only DNSE browser bundle. No credentials or raw API."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from config.settings import Settings
from src.data_pipeline.dnse_history import DNSEHistory
from src.models.orderbook import LOCAL_TZ


def export(store, day, clock='14:20', symbols=None, samples=0):
    at = datetime.fromisoformat(f'{day}T{clock}:59.999999').replace(tzinfo=LOCAL_TZ)
    catalog = store.catalog(day)
    symbols = symbols or sorted(catalog)
    records = list(store.reconstruct_at(symbols, day, at).values())
    if samples:
        for symbol in symbols:
            records.extend(store.sampled_records(symbol, day, at, limit=samples))
    unique = {r['event_id']: r for r in records}
    return {'format': 'liquiditylab-browser-v1', 'source': 'DNSE historical REST',
            'cutoff': at.isoformat(), 'reconstruction': 'ex_post_source_time',
            'catalog': catalog, 'records': list(unique.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', help='YYYY-MM-DD; default latest archived date')
    parser.add_argument('--at', default='14:20', help='Vietnam source clock HH:MM')
    parser.add_argument('--symbols', help='Comma-separated; default all catalog symbols')
    parser.add_argument('--samples', type=int, default=0, help='Optional per-symbol history points (2..1200)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    store = DNSEHistory(Settings().dnse_database_path)
    day = args.date or store.dates()[-1]
    payload = export(store, day, args.at, args.symbols.split(',') if args.symbols else None, args.samples)
    path = args.output or Path(f'data/exports/dashboard/dnse_{day}.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps({'path': str(path.resolve()), 'records': len(payload['records']), 'bytes': path.stat().st_size}))


if __name__ == '__main__':
    main()
