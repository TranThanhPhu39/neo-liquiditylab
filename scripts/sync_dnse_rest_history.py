"""Restore encrypted REST backfills into the local history pipeline without overwriting."""
import argparse
from contextlib import closing
import json
import sqlite3
import tempfile
from pathlib import Path

from config.settings import ROOT
from scripts.github_checkpoint import ReleaseStore, backup_sqlite
from scripts.github_dnse_backfill import NAMESPACE, REPOSITORY, closed_session, history_folder, restore_universe
from scripts.prepare_dnse_realtime import seed_key
from scripts.scheduled_collection import exclusive_job, write_json


def restore(day, session, *, root=ROOT, store=None):
    closed_session(day,session)
    root = Path(root)
    destination = history_folder(root,day,session)
    with exclusive_job(root/'data/control/dnse_history.lock'):
        if (destination/'history.db').exists() or (destination/'universe.json').exists():
            raise FileExistsError('REST_RESTORE_WOULD_OVERWRITE_LOCAL_HISTORY')
        store = store or ReleaseStore(REPOSITORY,seed_key(),namespace=NAMESPACE)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            tag = store.restore(day+'-'+session,folder/'history.db',folder/'reports')
            if not tag:
                return dict(state='NO_CHECKPOINT',date_local=day,session=session)
            with closing(sqlite3.connect((folder/'history.db').as_uri()+'?mode=ro',uri=True)) as db:
                settings = json.loads(db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])
                if settings['date'] != day or settings.get('session','day') != session or settings['boardId'] != 'G1':
                    raise ValueError('REST_RESTORED_SCOPE_MISMATCH')
                complete = bool(db.execute('SELECT 1 FROM symbols').fetchone()) and not db.execute(
                    "SELECT 1 FROM symbols WHERE status NOT IN ('COMPLETE','NO_DATA_RETURNED')").fetchone()
            restore_universe(folder/'history.db',folder)
            backup_sqlite(folder/'history.db',destination/'history.db')
            if (folder/'universe.json').exists():
                write_json(destination/'universe.json',json.loads((folder/'universe.json').read_text(encoding='utf-8')))
    return dict(state='RESTORED' if complete else 'RESTORED_PARTIAL_RUN_UPDATE_TO_RESUME',
                date_local=day,session=session,checkpoint=tag,database=str(destination/'history.db'),
                source='REST_HISTORY',units_verified=False,financial_eligible=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--session',choices=['morning','day'],default='day')
    args=parser.parse_args()
    result=restore(args.date,args.session)
    print(json.dumps(result))
    return 1 if result['state']=='NO_CHECKPOINT' else 0


if __name__=='__main__':
    raise SystemExit(main())
