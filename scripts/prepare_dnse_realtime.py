"""Refresh DNSE equity catalog locally and optionally publish only an encrypted seed."""
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3

from config.settings import ROOT
from scripts.collect_dnse_history import connect, load_universe, utcnow
from scripts.github_checkpoint import ReleaseStore, state_key
from scripts.github_setup import protected_state_key
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_realtime import validate_catalog
from src.data_providers.dnse_readonly import DNSEReadOnly
from src.models.orderbook import LOCAL_TZ

REPOSITORY = 'TranThanhPhu39/neo-liquiditylab'


def seed_key():
    encoded = os.getenv('LIQUIDITYLAB_STATE_KEY')
    if not encoded:
        if not (ROOT/'data/control/github_state_key.dpapi').exists():
            raise ValueError('STATE_KEY_BACKUP_MISSING_DO_NOT_REGENERATE')
        encoded = protected_state_key()
    return state_key(encoded)


def prepare(publish=False):
    if publish and os.getenv('DNSE_REALTIME_STORAGE_APPROVED') != 'true':
        raise ValueError('WS_ENCRYPTED_STORAGE_NOT_APPROVED')
    now = datetime.now(LOCAL_TZ)
    folder = ROOT/'data/realtime/dnse/catalog'/now.strftime('%Y%m%dT%H%M%S%f')
    folder.mkdir(parents=True)
    with closing(connect(folder/'reference.db')) as db:
        load_universe(db, DNSEReadOnly(min_interval=4), folder)
    catalog = json.loads((folder/'universe.json').read_text(encoding='utf-8'))
    catalog.update(as_of_date=now.date().isoformat(), scope='DNSE_LISTED_EQUITIES_ST_G1')
    validate_catalog(catalog, now.date())
    write_json(ROOT/'data/control/dnse_ws_catalog.json', catalog)
    write_json(folder/'universe.json', catalog)
    with closing(sqlite3.connect(folder/'catalog.db')) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT INTO metadata VALUES(?,?)', ('catalog', json.dumps(catalog, sort_keys=True)))
        db.commit()
    result = {'state': 'CATALOG_PREPARED', 'as_of_date': catalog['as_of_date'], 'symbols': catalog['count'],
              'source': 'DNSE_REST_INSTRUMENTS', 'encrypted_release': None}
    write_json(folder/'status.json', result)
    if publish:
        store = ReleaseStore(REPOSITORY, seed_key(), namespace='dnse-ws-catalog')
        result['encrypted_release'] = store.publish('latest', folder/'catalog.db', folder, 'local-catalog', '1')
        result['state'] = 'CATALOG_PREPARED_AND_ENCRYPTED'
    write_json(ROOT/'report/dnse_ws_catalog_preparation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.publish)), flush=True)
        return 0
    except Exception as error:
        message = str(error)
        code = message if __import__('re').fullmatch('[A-Z0-9_]{1,90}', message) else type(error).__name__
        print(json.dumps({'state': 'FAILED', 'error_code': code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
