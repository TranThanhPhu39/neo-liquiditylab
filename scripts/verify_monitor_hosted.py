"""Verify real hosted history using private credentials without logging payloads."""
import base64
import gzip
import io
import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from dotenv import dotenv_values
import truststore

from scripts.render_cli_ops import ROOT

ORIGIN = 'https://neo-liquiditylab-monitor.onrender.com'


def request(path, value=None, csrf=None):
    truststore.inject_into_ssl()
    password = dotenv_values(ROOT / '.env', interpolate=False)['MONITOR_WEB_PASSWORD']
    headers = {'Authorization': 'Basic ' + base64.b64encode(('liquiditylab:' + password).encode()).decode(),
               'Accept-Encoding': 'gzip'}
    body = None
    if value is not None:
        headers.update({'Content-Type': 'application/json', 'Origin': ORIGIN, 'X-Monitor-CSRF': csrf or ''})
        body = json.dumps(value).encode()
    with urlopen(Request(ORIGIN + path, data=body, headers=headers), timeout=60) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('VERIFICATION_RESPONSE_TOO_LARGE')
        if response.headers.get('Content-Encoding') == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(128 * 1024 * 1024 + 1)
            if len(raw) > 128 * 1024 * 1024:
                raise ValueError('VERIFICATION_JSON_TOO_LARGE')
        return json.loads(raw)


def verify():
    hosting = request('/api/hosting')
    if not hosting['seed_ready']:
        return dict(state='WAITING_FOR_RECOVERY', hosting=hosting)
    index = request('/api/datasets')
    entries = index['datasets']
    assert len(entries) >= 34  # Saved analyses can add versioned replays.
    active = [e for e in entries if not e.get('archived')]
    selected = max((e for e in active if e['symbol'] == 'ACB'), key=lambda e: e['day'])
    package = request('/api/dataset?dataset_id=' + selected['dataset_id'])
    assert package['dataset_id'] == selected['dataset_id'] and package['frames']
    assert any(f['bids'] and f['asks'] for f in package['frames'])
    trades = request('/api/trades?dataset_id=' + selected['dataset_id'])
    assert trades['symbol'] == 'ACB'
    # Quote collection does not imply that a matched-trade feed was collected.
    # Verify available bubbles separately without inventing today's trades.
    latest_trade_count = len(trades['trades'])
    bubble_day = selected['day'] if latest_trade_count else None
    for older in sorted((e for e in active if e['symbol']=='ACB' and e['day']<selected['day']),key=lambda e:e['day'],reverse=True):
        if bubble_day:break
        candidate=request('/api/trades?dataset_id='+older['dataset_id'])
        assert candidate['symbol']=='ACB'
        if candidate['trades']:trades=candidate;bubble_day=older['day']
    library = request('/api/library')
    history = request('/api/review-history')
    result = dict(state='HOSTED_HISTORY_VERIFIED', datasets=len(entries), active_datasets=len(active),
                  days=len({e['day'] for e in active}), replay_day=selected['day'], replay_symbol='ACB',
                  replay_frames=len(package['frames']), latest_day_trade_count=latest_trade_count,
                  bubbles_day=bubble_day, real_trades_for_bubbles=len(trades['trades']),
                  library_events=len(library), review_history_accessible=isinstance(history['reviews'], list),
                  hosting=hosting)
    result['checked_at_utc']=datetime.now(timezone.utc).isoformat()
    path = ROOT / 'report/monitor_hosted_verification.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(verify()))
