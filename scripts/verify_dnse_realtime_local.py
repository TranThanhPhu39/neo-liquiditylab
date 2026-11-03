"""Verify an existing realtime DB through a local encrypted checkpoint roundtrip; no upload."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from config.settings import ROOT
from scripts.github_checkpoint import backup_sqlite, pack_state, unpack_state
from scripts.prepare_dnse_realtime import seed_key
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_realtime_store import audit


def verify(database):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    folder = ROOT/'data/realtime/dnse/local-verification'/stamp
    folder.mkdir(parents=True)
    backup_sqlite(database, folder/'snapshot.db')
    before = audit(folder/'snapshot.db')
    key = seed_key()
    context = 'local-only:dnse-realtime:'+stamp
    manifest = pack_state({'snapshot.db':folder/'snapshot.db'}, folder/'encrypted', key, context)
    unpack_state(folder/'encrypted', folder/'restored', key, context)
    after = audit(folder/'restored/snapshot.db')
    if before != after or (folder/'snapshot.db').read_bytes() != (folder/'restored/snapshot.db').read_bytes():
        raise ValueError('WS_LOCAL_ROUNDTRIP_MISMATCH')
    result = {'state':'VERIFIED_LOCAL_ENCRYPTED_ROUNDTRIP','network_upload':False,
              'source_database':str(Path(database).resolve()),'verification_folder':str(folder),
              'audit':after,'encrypted_parts':len(manifest['parts']),
              'encrypted_bytes':sum(p['bytes'] for p in manifest['parts'])}
    write_json(ROOT/'report/dnse_realtime_local_checkpoint_verification.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.database)))
