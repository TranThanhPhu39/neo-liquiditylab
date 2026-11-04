"""Restore encrypted WS structured data without publishing any plaintext quotes."""
import argparse
from datetime import date
import json
from pathlib import Path
import uuid

from config.settings import ROOT
from scripts.github_checkpoint import ReleaseStore
from scripts.prepare_dnse_realtime import seed_key
from scripts.normalize_dnse_realtime import audit_normalized


def restore(day,partition):
    date.fromisoformat(day)
    if partition not in ('morning','afternoon','smoke'):
        raise ValueError('WS_INVALID_NORMALIZED_PARTITION')
    folder=ROOT/'data/processed/dnse_ws/restored'/day/partition/uuid.uuid4().hex
    database=folder/'normalized.db'
    tag=ReleaseStore('TranThanhPhu39/neo-liquiditylab',seed_key(),namespace='dnse-ws-normalized').restore(
        day+'-'+partition,database,folder/'reports')
    if not tag:
        return dict(state='NO_CHECKPOINT',date=day,partition=partition)
    return dict(state='RESTORED',checkpoint=tag,database=str(database),audit=audit_normalized(database))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--partition',choices=['morning','afternoon','smoke'],required=True)
    args=parser.parse_args()
    print(json.dumps(restore(args.date,args.partition)))
