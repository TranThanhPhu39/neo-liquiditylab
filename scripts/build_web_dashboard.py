"""Build the deterministic browser bundle from the repository's finance core."""
import hashlib
import json
from pathlib import Path
import zipfile

import tzdata

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'src/__init__.py', 'src/app/__init__.py', 'src/app/browser_bridge.py',
    'src/models/__init__.py', 'src/models/orderbook.py', 'src/models/execution.py',
    'src/models/portfolio.py', 'src/models/scenario.py',
    'src/data_pipeline/__init__.py', 'src/data_pipeline/validation.py',
    'src/data_pipeline/dnse_normalization.py', 'src/finance/__init__.py',
    'src/data_pipeline/dnse_ws_units.py', 'src/data_pipeline/dnse_ws_review.py',
    'src/data_pipeline/dnse_ws_normalization.py', 'config/unit_contract.dnse-ws-v1.json',
    'src/finance/execution_simulator.py', 'src/finance/liquidity_metrics.py',
    'src/finance/portfolio_liquidation.py', 'src/finance/stress_testing.py',
)


def build(root=ROOT):
    destination = root / 'web/engine.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = {name: (root/name).read_bytes() for name in MODULES}
    files['zones/Asia/Ho_Chi_Minh'] = (Path(tzdata.__file__).parent/'zoneinfo/Asia/Ho_Chi_Minh').read_bytes()
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return {'path': 'web/engine.zip', 'files': len(files),
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'bytes': destination.stat().st_size}


if __name__ == '__main__':
    print(json.dumps(build()))
