"""Read only G1 quotes over DNSE WebSocket, then disconnect within a fixed budget."""
import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os

from config.settings import ROOT
from scripts.scheduled_collection import write_json
from src.data_providers.dnse_ws_probe import ProbeError, run_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', nargs='+', default=['ACB', 'PVS', 'MSR'])
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--report', default='report/dnse_ws_probe.json')
    parser.add_argument('--discard-raw', action='store_true', help='Print/store summary only; no market payload artifact')
    args = parser.parse_args()
    try:
        report, observations = asyncio.run(run_probe(os.getenv('DNSE_API_KEY'), os.getenv('DNSE_API_SECRET'), args.symbols, args.seconds))
    except ProbeError as error:
        report, observations = {'provider': 'dnse', 'purpose': 'READ_ONLY_WEBSOCKET_DIAGNOSTIC',
                                'status': 'FAILED', 'error_code': str(error)}, []
    if observations and not args.discard_raw:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        path = ROOT/'data/raw'/f'dnse_ws_probe_{stamp}.jsonl.gz'
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, 'wt', encoding='utf-8', compresslevel=1) as stream:
            for observation in observations:
                stream.write(json.dumps(observation, ensure_ascii=False) + '\n')
        # Local-only sidecar. Never include raw values or artifacts in public job summaries.
        write_json(path.with_suffix(path.suffix+'.manifest.json'),
                   {'path': str(path.relative_to(ROOT)), 'records': len(observations),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'probe_id': report['probe_id']})
    write_json(ROOT/args.report, report)
    print(json.dumps(report, ensure_ascii=True))
    return 1 if report['status'] == 'FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
