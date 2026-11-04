"""Incremental WS structure normalization with complete receipts and raw lineage."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import zlib

from config.settings import ROOT
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_realtime_store import FORMAT, canonical, audit as audit_raw
from src.data_pipeline.dnse_ws_normalization import VERSION, normalize_event
from src.data_pipeline.dnse_ws_units import canonical_top3, load_ws_rules


def output_db(path):
    path=Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size:
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as existing:
                row=existing.execute("SELECT value FROM metadata WHERE key='format'").fetchone()
                if not row or row[0]!=VERSION:
                    raise ValueError('WS_NORMALIZED_TARGET_NOT_RECOGNIZED')
        except sqlite3.Error:
            raise ValueError('WS_NORMALIZED_TARGET_NOT_RECOGNIZED') from None
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript('''
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,settings TEXT NOT NULL,catalog TEXT NOT NULL,
        observed_runs TEXT NOT NULL,known_gaps TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS symbols(source_id TEXT NOT NULL REFERENCES sources(id),symbol TEXT NOT NULL,
        market_id TEXT NOT NULL,exchange TEXT NOT NULL,PRIMARY KEY(source_id,symbol));
      CREATE TABLE IF NOT EXISTS events(source_id TEXT NOT NULL REFERENCES sources(id),event_key TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,symbol TEXT NOT NULL,source_time_utc TEXT,source_day TEXT,
        first_received_at TEXT NOT NULL,state TEXT NOT NULL,error_code TEXT,payload_zlib BLOB,output_sha256 TEXT,
        PRIMARY KEY(source_id,event_key),FOREIGN KEY(source_id,symbol) REFERENCES symbols(source_id,symbol));
      CREATE INDEX IF NOT EXISTS normalized_ws_symbol_time ON events(symbol,source_time_utc);
      CREATE TABLE IF NOT EXISTS receipts(source_id TEXT NOT NULL,receipt_id INTEGER NOT NULL,event_key TEXT NOT NULL,
        connection_id TEXT NOT NULL,received_at TEXT NOT NULL,frame_sha256 TEXT NOT NULL,
        PRIMARY KEY(source_id,receipt_id),FOREIGN KEY(source_id,event_key) REFERENCES events(source_id,event_key),
        FOREIGN KEY(source_id,connection_id) REFERENCES connections(source_id,id));
      CREATE TABLE IF NOT EXISTS connections(source_id TEXT NOT NULL REFERENCES sources(id),id TEXT NOT NULL,
        payload TEXT NOT NULL,PRIMARY KEY(source_id,id));
    ''')
    previous=db.execute("SELECT value FROM metadata WHERE key='format'").fetchone()
    if previous and previous[0]!=VERSION:
        db.close()
        raise ValueError('WS_NORMALIZED_FORMAT_MISMATCH')
    with db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES('format',?)",(VERSION,))
    return db


def audit_normalized(path, *, rules=None):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('WS_NORMALIZED_INTEGRITY_FAILED')
        count=0;canonical_count=0;eligible_count=0
        for row in db.execute("SELECT * FROM events WHERE state='NORMALIZED'"):
            data=zlib.decompress(row['payload_zlib'])
            if hashlib.sha256(data).hexdigest()!=row['output_sha256']:
                raise ValueError('WS_NORMALIZED_PAYLOAD_HASH_FAILED')
            value=json.loads(data)
            if (value['normalization_version']!=VERSION or value['symbol']!=row['symbol']
                    or datetime.fromisoformat(value['collected_at_utc'])!=datetime.fromisoformat(row['first_received_at'])
                    or value['financial_eligible'] or value['price_unit'] is not None or value['volume_unit'] is not None
                    or any(value[f'{s}_{f}_{i}'] is not None for s in ('bid','ask') for f in ('price','vol') for i in (1,2,3))):
                raise ValueError('WS_UNVERIFIED_FINANCE_GUARD_FAILED')
            if 'canonical_snapshot' in value:
                rules=rules or load_ws_rules()
                expected=canonical_top3(value,row['event_key'],rules)
                if expected is None or expected!=value['canonical_snapshot']:
                    raise ValueError('WS_CANONICAL_DERIVATION_FAILED')
                canonical_count+=1
                eligible_count+=int(expected['financial_eligible'])
            count+=1
        return dict(state='VERIFIED',normalized_events=count,
            quarantined_events=db.execute("SELECT count(*) FROM events WHERE state='QUARANTINED'").fetchone()[0],
            receipt_count=db.execute('SELECT count(*) FROM receipts').fetchone()[0],
            source_archives=db.execute('SELECT count(*) FROM sources').fetchone()[0],
            canonical_events=canonical_count,canonical_financial_eligible=eligible_count,
            financial_eligible=False,units_verified=False,message_semantics_verified=False)


def normalize(source, target, *, max_bytes=2*1024**3, min_free_bytes=1024**3, rules=None):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target or (target.exists() and source.samefile(target)):
        raise ValueError('WS_NORMALIZATION_MUST_NOT_OVERWRITE_RAW')
    checked=audit_raw(source)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30)) as incoming, closing(output_db(target)) as output:
        incoming.row_factory=sqlite3.Row
        incoming.execute('BEGIN')
        settings=json.loads(incoming.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])
        if settings.get('format')!=FORMAT:
            raise ValueError('WS_RAW_ARCHIVE_REQUIRED')
        source_id=hashlib.sha256(canonical(settings).encode()).hexdigest()
        catalog=json.loads(incoming.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()[0])
        if hashlib.sha256(canonical(catalog).encode()).hexdigest()!=settings['catalog_hash']:
            raise ValueError('WS_CATALOG_HASH_MISMATCH')
        symbol_rows={r['symbol']:dict(r) for r in incoming.execute('SELECT * FROM symbols')}
        if {r['symbol']:(r['market_id'],r['exchange']) for r in catalog['rows']}!={s:(r['market_id'],r['exchange']) for s,r in symbol_rows.items()}:
            raise ValueError('WS_CATALOG_SYMBOLS_MISMATCH')
        seen=0;reprocessed=0
        rule_hash=rules.rule_hash if rules else ''
        previous_rule=output.execute('SELECT value FROM metadata WHERE key=?',('ws_rules:'+source_id,)).fetchone()
        rules_changed=(previous_rule[0] if previous_rule else '')!=rule_hash
        def budget():
            size=output.execute('PRAGMA page_count').fetchone()[0]*output.execute('PRAGMA page_size').fetchone()[0]
            if size>max_bytes:
                raise ValueError('WS_NORMALIZED_SIZE_LIMIT')
            if shutil.disk_usage(target.parent).free<min_free_bytes:
                raise ValueError('WS_NORMALIZED_LOW_DISK')
        # One transaction makes an incomplete normalization invisible to downstream readers.
        with output:
            output.execute('''INSERT INTO sources VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                observed_runs=excluded.observed_runs,known_gaps=excluded.known_gaps,updated_at=excluded.updated_at''',
                (source_id,canonical(settings),canonical(catalog),canonical([dict(r) for r in incoming.execute('SELECT * FROM runs')]),
                 canonical([dict(r) for r in incoming.execute('SELECT * FROM gaps')]),datetime.now(timezone.utc).isoformat()))
            for row in symbol_rows.values():
                output.execute('INSERT OR IGNORE INTO symbols VALUES(?,?,?,?)',(source_id,row['symbol'],row['market_id'],row['exchange']))
            for row in incoming.execute('SELECT * FROM events ORDER BY event_key'):
                previous=output.execute('SELECT input_sha256,first_received_at FROM events WHERE source_id=? AND event_key=?',(source_id,row['event_key'])).fetchone()
                if previous:
                    if previous[0]!=row['payload_sha256']:
                        raise ValueError('WS_INPUT_EVENT_CHANGED')
                    if not rules_changed and previous['first_received_at']<=row['first_received_at']:
                        continue
                error=None;data=None;digest=None;record=None
                received_at=min(previous['first_received_at'],row['first_received_at']) if previous else row['first_received_at']
                try:
                    record=normalize_event(json.loads(zlib.decompress(row['payload_zlib'])),received_at,
                        row['symbol'],symbol_rows[row['symbol']]['market_id'],settings['day'])
                    if rules:
                        converted=canonical_top3(record,row['event_key'],rules)
                        if converted is not None:
                            record['canonical_snapshot']=converted
                    data=canonical(record).encode();digest=hashlib.sha256(data).hexdigest()
                except (ValueError,TypeError,OverflowError) as exc:
                    import re
                    code=str(exc)
                    error=code if re.fullmatch('WS_[A-Z0-9_]{1,80}',code) else 'WS_EVENT_NORMALIZATION_FAILED'
                output.execute('''INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,event_key) DO UPDATE SET
                    first_received_at=excluded.first_received_at,state=excluded.state,error_code=excluded.error_code,
                    payload_zlib=excluded.payload_zlib,output_sha256=excluded.output_sha256''',
                    (source_id,row['event_key'],row['payload_sha256'],row['symbol'],record['source_timestamp'] if record else row['source_time_utc'],
                     record['source_local_date'] if record else row['source_day'],received_at,
                     'QUARANTINED' if error else 'NORMALIZED',error,zlib.compress(data) if data else None,digest))
                seen+=int(previous is None)
                reprocessed+=int(previous is not None)
                if (seen+reprocessed)%1000==0:
                    budget()
            for row in incoming.execute('SELECT * FROM connections'):
                output.execute('INSERT INTO connections VALUES(?,?,?) ON CONFLICT(source_id,id) DO UPDATE SET payload=excluded.payload',
                               (source_id,row['id'],canonical(dict(row))))
            for ordinal,row in enumerate(incoming.execute('SELECT * FROM receipts ORDER BY id'),1):
                values=(source_id,row['id'],row['event_key'],row['connection_id'],row['received_at'],row['frame_sha256'])
                previous=output.execute('SELECT source_id,receipt_id,event_key,connection_id,received_at,frame_sha256 FROM receipts WHERE source_id=? AND receipt_id=?',values[:2]).fetchone()
                if previous and tuple(previous)!=values:
                    raise ValueError('WS_INPUT_RECEIPT_CHANGED')
                output.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?)',values)
                if ordinal%1000==0:
                    budget()
            total=output.execute('SELECT count(*) FROM events WHERE source_id=?',(source_id,)).fetchone()[0]
            receipts=output.execute('SELECT count(*) FROM receipts WHERE source_id=?',(source_id,)).fetchone()[0]
            expected=incoming.execute('SELECT count(*) FROM events').fetchone()[0]
            expected_receipts=incoming.execute('SELECT count(*) FROM receipts').fetchone()[0]
            if total!=expected or receipts!=expected_receipts:
                raise ValueError('WS_NORMALIZATION_LINEAGE_COUNT_MISMATCH')
            output.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           ('ws_rules:'+source_id,rule_hash))
            budget()
        output.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        incoming.rollback()
    result=audit_normalized(target,rules=rules)
    state='SCOPED_CANONICAL_NORMALIZED' if result['canonical_events'] else 'STRUCTURE_NORMALIZED_UNVERIFIED' if result['normalized_events'] or result['quarantined_events'] else 'NO_QUOTES_TO_NORMALIZE'
    return dict(state=state,normalization_version=VERSION,
        new_events=seen,reprocessed_events=reprocessed,source_id=source_id,raw_audit=checked,audit=result,
        publication_to_pages=False,financial_eligible=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path,help='Raw realtime quotes.db')
    parser.add_argument('--output',type=Path,default=ROOT/'data/processed/dnse_ws/normalized.db')
    args=parser.parse_args()
    with exclusive_job(ROOT/'data/control/dnse_ws_normalization.lock'):
        result=normalize(args.input,args.output,rules=load_ws_rules())
    write_json(ROOT/'report/dnse_ws_normalization_latest.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    main()
