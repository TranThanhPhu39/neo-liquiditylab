"""Generate one bounded, read-only historical breakout research report."""
import argparse
import json
from pathlib import Path

from config.settings import ROOT
from scripts.serve_orderbook_monitor import MonitorServer
from src.monitoring.breakout_store import research


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--reference-price')
    parser.add_argument('--reference-day')
    parser.add_argument('--cutoff', help='Aware ISO source timestamp; default end of selected local day')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    server = MonitorServer(ROOT, port=0)
    try:
        entry = next((e for e in server.index() if e['day'] == args.day and e['symbol'] == args.symbol.upper()
                      and not e.get('archived')), None)
        if not entry:
            raise ValueError('UNKNOWN_RESEARCH_DAY_OR_SYMBOL')
        report = research(server, entry['dataset_id'], args.cutoff or args.day + 'T23:59:59+07:00',
                          args.reference_price, args.reference_day)
        if args.output:
            path = args.output.resolve()
            if not path.is_relative_to(ROOT.resolve()):
                raise ValueError('RESEARCH_OUTPUT_OUTSIDE_WORKSPACE')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(state=report['state'], symbol=report['symbol'], day=report['day'],
                              reference=report['reference'], episodes=len(report['episodes']),
                              groups=report['groups'], quality=report.get('quality')), ensure_ascii=True))
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
