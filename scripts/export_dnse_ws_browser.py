"""Export verified canonical WS views locally for the dashboard's file import."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib

from config.settings import ROOT
from scripts.normalize_dnse_realtime import audit_normalized
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_ws_units import load_ws_rules


def export(database,output):
    database=Path(database).resolve();output=Path(output).resolve()
    # This command is deliberately a private local export, separate from cloud publishing.
    if not output.is_relative_to((ROOT/'data').resolve()) or output==database:
        raise ValueError('WS_BROWSER_EXPORT_REQUIRES_PRIVATE_DATA_FOLDER')
    checked=audit_normalized(database,rules=load_ws_rules())
    records=[]
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        for row in db.execute("SELECT payload_zlib FROM events WHERE state='NORMALIZED' ORDER BY source_time_utc,event_key"):
            value=json.loads(zlib.decompress(row[0]))
            if 'canonical_snapshot' in value:
                records.append(value['canonical_snapshot'])
    if not records:
        raise ValueError('WS_BROWSER_EXPORT_NO_VERIFIED_OBSERVATIONS')
    write_json(output,dict(format='liquiditylab-browser-v1',source='DNSE WebSocket observed top3',
        reconstruction='independent_complete_messages_no_carry_forward',
        cutoff=max(r['source_local_time'] for r in records),records=records))
    return dict(state='PRIVATE_BROWSER_EXPORT_READY',records=len(records),
        eligible=checked['canonical_financial_eligible'],path=str(output),publication_to_pages=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'data/processed/dnse_ws/browser-verified.json')
    args=parser.parse_args()
    print(json.dumps(export(args.database,args.output)))
