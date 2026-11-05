"""Encrypted bootstrap of existing verified REST evidence; no plaintext publication."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from config.settings import ROOT
from scripts.github_checkpoint import ReleaseStore
from scripts.normalize_dnse_history import prepare_rules
from scripts.prepare_dnse_realtime import seed_key
from src.data_pipeline.dnse_normalization import load_rules
from scripts.scheduled_collection import write_json

REPOSITORY='TranThanhPhu39/neo-liquiditylab'
NAMESPACE='dnse-rest-web-rules'
EVIDENCE='report/dnse_orderbook_unit_evidence_2026-10-29_133643.json'


def raw_path(root,evidence):
    path=Path(root)/evidence['api_artifact']['path'].replace('\\','/')
    if not path.resolve().is_relative_to((Path(root)/'data/raw').resolve()) or path.suffix!='.json' or path.is_symlink():
        raise ValueError('WEB_RULES_RAW_PATH_INVALID')
    return path


def prepare(root=ROOT, *, store=None):
    root=Path(root)
    rules=load_rules(prepare_rules(root))
    evidence=(root/EVIDENCE).read_bytes()
    raw=raw_path(root,json.loads(evidence)).read_bytes()
    secrets=[os.getenv(k).encode() for k in ('DNSE_API_KEY','DNSE_API_SECRET','NEO_USERNAME','NEO_PASSWORD',
        'GH_TOKEN','GITHUB_TOKEN','PENPOT_MCP_URL','LIQUIDITYLAB_STATE_KEY') if os.getenv(k)]
    if any(value in content for content in (evidence,raw) for value in secrets):
        raise ValueError('WEB_RULES_CONTAIN_SECRET')
    store=store or ReleaseStore(REPOSITORY,seed_key(),namespace=NAMESPACE)
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        database=folder/'rules.db'
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE bootstrap(name TEXT PRIMARY KEY,payload BLOB NOT NULL)')
            db.executemany('INSERT INTO bootstrap VALUES(?,?)',[('evidence',evidence),('raw',raw)])
            db.commit()
        write_json(folder/'status.json',dict(state='REVIEWED_REST_RULES',rule_hash=rules.rule_hash))
        tag=store.publish('latest',database,folder,'local-rest-rules','1')
    return dict(state='ENCRYPTED_WEB_RULES_PREPARED',checkpoint=tag,rule_hash=rules.rule_hash)


def restore(root=ROOT, *, store=None):
    root=Path(root)
    store=store or ReleaseStore(REPOSITORY,seed_key(),namespace=NAMESPACE)
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        tag=store.restore('latest',folder/'rules.db',folder/'reports')
        if not tag:
            raise ValueError('WEB_RULES_CHECKPOINT_MISSING')
        with closing(sqlite3.connect((folder/'rules.db').as_uri()+'?mode=ro',uri=True)) as db:
            contents=dict(db.execute('SELECT name,payload FROM bootstrap'))
        if set(contents)!={'evidence','raw'} or any(len(x)>2*1024*1024 for x in contents.values()):
            raise ValueError('WEB_RULES_BOOTSTRAP_INVALID')
        evidence=json.loads(contents['evidence'])
        for target,content in ((root/EVIDENCE,contents['evidence']),(raw_path(root,evidence),contents['raw'])):
            if target.exists() and target.read_bytes()!=content:
                raise ValueError('WEB_RULES_RESTORE_WOULD_OVERWRITE')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content)
    rules=load_rules(prepare_rules(root))
    return dict(checkpoint=tag,rule_hash=rules.rule_hash)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish',action='store_true',required=True)
    parser.parse_args()
    print(json.dumps(prepare()))
