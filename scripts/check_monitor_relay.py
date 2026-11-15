"""Verify runner-to-web transport with real archived SSI records, never live quotes."""
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

from scripts.restore_ssi_checkpoint import install, recovery_key
from scripts.ssi_checkpoint import SSICheckpoints
from src.monitoring.public_releases import PublicReleases
from src.monitoring.web_relay import RelayPublisher


def check():
    api = PublicReleases('TranThanhPhu39/neo-liquiditylab')
    releases = api.request('GET', api.base + '/releases?per_page=100')
    if not releases:
        raise ValueError('NO_ARCHIVED_CAPTURE_AVAILABLE')
    files, proof = SSICheckpoints(api.repository, recovery_key(), api=api).restore(
        releases[0]['tag_name'], max_bytes=64 * 1024 * 1024)
    with tempfile.TemporaryDirectory(prefix='monitor-relay-check-') as temporary:
        folder = Path(install(files, root=temporary))
        manifest = json.loads((folder / 'manifest.json').read_bytes())
        status = json.loads(files['status.json'])
        status.update(active=False, state='ARCHIVED_RELAY_CHECK')
        journal = SimpleNamespace(manifest=manifest, folder=folder, buffer=[], ordinal=manifest['last_ordinal'])
        service = SimpleNamespace(lock=threading.RLock(), journal=journal, status=lambda: status)
        publisher = RelayPublisher(service, os.environ['SSI_MONITOR_URL'], os.environ['SSI_MONITOR_INGEST_TOKEN'])
        for _ in range(20):
            publisher.once()
            if publisher.cursor == journal.ordinal + 1:
                break
        else:
            raise ValueError('RELAY_CHECK_DID_NOT_FINISH')
        publisher.once()  # Duplicate delivery must remain idempotent.
        result = dict(state='ARCHIVED_RUNNER_TO_WEB_RELAY_VERIFIED', run_id=manifest['run_id'],
                      day=manifest['day'], records_acknowledged=publisher.cursor,
                      quote_counts=proof['quote_counts'], trade_counts=proof['trade_counts'],
                      live_market_test=False, broker_api_called=False)
        return result


if __name__ == '__main__':
    print(json.dumps(check()))
