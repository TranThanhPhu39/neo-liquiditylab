"""Stream immutable DNSE history into a separate resumable canonical archive."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import zlib

from config.settings import ROOT
from scripts.collect_dnse_history import exclusive_job,keep_awake,utcnow
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_normalization import canonical_json,load_rules,normalize_quote,NORMALIZATION_VERSION


def file_sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_rules(root=ROOT):
    path=root/'data/control/dnse_normalization_rules.json'
    evidence_path=root/'report/dnse_orderbook_unit_evidence_2026-10-29_133643.json'
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    if evidence.get('verification_status')!='EMPIRICALLY_VERIFIED_FOR_ACB_G1' or len(evidence.get('matched_level_pairs',[]))!=6:
        raise ValueError('DNSE_ACB_EVIDENCE_NOT_REVIEWED')
    raw_reference=evidence['api_artifact']
    raw_path=root/Path(raw_reference['path'].replace('\\','/'))
    if file_sha(raw_path)!=raw_reference['sha256']:
        raise ValueError('DNSE_RAW_EVIDENCE_CHECKSUM_MISMATCH')
    raw=json.loads(raw_path.read_text(encoding='utf-8'))
    quote=next(q for q in raw['payload']['quotes'] if q['time']==evidence['chosen_source_time'])
    from decimal import Decimal
    for pair in evidence['matched_level_pairs']:
        level=quote[pair['side']][pair['level']-1]
        if Decimal(str(level['price']))*1000!=Decimal(pair['normalized_price_vnd']) or Decimal(str(level['quantity']))*10!=pair['image_quantity_display']:
            raise ValueError('DNSE_EVIDENCE_PAIR_MISMATCH')
    record={'provider':'dnse','api_version':'2026-07-23','board_id':'G1',
        'normalization_version':NORMALIZATION_VERSION,'price_multiplier_to_vnd':'1000',
        'volume_multiplier_to_shares':'10','empirically_verified_symbols':['ACB'],
        'empirically_verified_market_ids':['STO'],'other_symbols_policy':'APPLY_SHARED_SCHEMA_MARK_UNVERIFIED',
        'user_requested_scope':'All collected equities; no invented zero orderbooks',
        'evidence':[{'path':'../../'+evidence_path.relative_to(root).as_posix(),'sha256':file_sha(evidence_path)}]}
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=record:
        raise ValueError('DNSE_EXISTING_RULES_DIFFER_REQUIRE_REVIEW')
    write_json(path,record)
    return path


def connect_output(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript('''
      PRAGMA foreign_keys=ON;
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      CREATE TABLE IF NOT EXISTS normalization_rules (hash TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS archives (id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,
        sha256 TEXT NOT NULL,settings TEXT NOT NULL,raw_records INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS raw_catalog (archive_id TEXT,symbol TEXT,exchange TEXT,status TEXT,
        raw_quote_count INTEGER,PRIMARY KEY(archive_id,symbol));
      CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,
        symbol TEXT NOT NULL,exchange TEXT NOT NULL,source_time TEXT NOT NULL,source_local_date TEXT NOT NULL,
        received_at TEXT NOT NULL,quality TEXT NOT NULL,units_status TEXT NOT NULL,market_session TEXT NOT NULL,
        financial_eligible INTEGER NOT NULL,bid_depth INTEGER,ask_depth INTEGER,
        aggregate_inconsistent INTEGER NOT NULL,payload_zlib BLOB NOT NULL);
      CREATE INDEX IF NOT EXISTS dnse_snapshot_lookup ON snapshots(symbol,source_time,id);
      CREATE INDEX IF NOT EXISTS dnse_snapshot_date ON snapshots(source_local_date);
      CREATE TABLE IF NOT EXISTS lineage (archive_id TEXT,page_id INTEGER,ordinal INTEGER,snapshot_id INTEGER,
        PRIMARY KEY(archive_id,page_id,ordinal),FOREIGN KEY(snapshot_id) REFERENCES snapshots(id));
      CREATE INDEX IF NOT EXISTS dnse_lineage_snapshot ON lineage(snapshot_id);
      CREATE TABLE IF NOT EXISTS processed_pages (archive_id TEXT,page_id INTEGER,sha256 TEXT,
        raw_count INTEGER,converted_count INTEGER,quarantined_count INTEGER,
        PRIMARY KEY(archive_id,page_id));
      CREATE TABLE IF NOT EXISTS quarantine (archive_id TEXT,page_id INTEGER,ordinal INTEGER,reason TEXT,
        PRIMARY KEY(archive_id,page_id,ordinal));
      CREATE TABLE IF NOT EXISTS manual_checks (source_date TEXT,session TEXT,symbol TEXT,evidence TEXT,
        PRIMARY KEY(source_date,session,symbol));
    ''')
    return db


def register_archive(db,path,root,rules):
    digest=file_sha(path)
    manifest_path=path.parent/'manifest.json'
    if manifest_path.exists():
        expected=json.loads(manifest_path.read_text(encoding='utf-8'))['files']['history.db']['sha256']
        if expected!=digest:
            raise ValueError('RAW_ARCHIVE_MANIFEST_CHECKSUM_MISMATCH')
    relative=path.relative_to(root).as_posix()
    existing=db.execute('SELECT sha256 FROM archives WHERE path=?',(relative,)).fetchone()
    if existing and existing[0]!=digest:
        raise ValueError('RAW_ARCHIVE_CHANGED_AFTER_REGISTRATION')
    source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    settings=json.loads(source.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])
    if settings.get('api_version')!=rules.api_version or settings.get('boardId')!='G1':
        source.close()
        raise ValueError('RAW_ARCHIVE_VERSION_BOARD_MISMATCH')
    if source.execute("SELECT 1 FROM symbols WHERE status NOT IN ('COMPLETE','NO_DATA_RETURNED')").fetchone():
        source.close()
        raise ValueError('RAW_ARCHIVE_NOT_FULLY_DOWNLOADED')
    total=source.execute('SELECT COALESCE(sum(quote_count),0) FROM pages').fetchone()[0]
    with db:
        db.execute('INSERT OR IGNORE INTO archives VALUES(?,?,?,?,?)',(digest,relative,digest,canonical_json(settings),total))
        db.executemany('INSERT OR IGNORE INTO raw_catalog VALUES(?,?,?,?,?)',(
            (digest,row['symbol'],row['exchange'],row['status'],row['quote_count']) for row in source.execute('SELECT * FROM symbols')))
        manual=root/'data/control'/f"dnse_manual_checks_{settings['date']}_{settings.get('session','day')}.json"
        if manual.exists():
            annotation=json.loads(manual.read_text(encoding='utf-8'))
            db.executemany('INSERT OR REPLACE INTO manual_checks VALUES(?,?,?,?)',(
                (settings['date'],settings.get('session','day'),row['symbol'],canonical_json(row)) for row in annotation.get('checks',[])))
    return source,digest


def process_page(db,archive_id,page,rules):
    previous=db.execute('SELECT sha256 FROM processed_pages WHERE archive_id=? AND page_id=?',(archive_id,page['id'])).fetchone()
    if previous:
        if previous[0]!=page['sha256']:
            raise ValueError('RAW_PAGE_CHANGED_AFTER_NORMALIZATION')
        return False
    raw=gzip.decompress(page['payload_gzip'])
    if hashlib.sha256(raw).hexdigest()!=page['sha256']:
        raise ValueError('RAW_PAGE_CHECKSUM_FAILED')
    quotes=json.loads(raw)['quotes']
    if len(quotes)!=page['quote_count']:
        raise ValueError('RAW_PAGE_COUNT_MISMATCH')
    settings=json.loads(db.execute('SELECT settings FROM archives WHERE id=?',(archive_id,)).fetchone()[0])
    catalog=db.execute('SELECT exchange FROM raw_catalog WHERE archive_id=? AND symbol=?',(archive_id,page['symbol'])).fetchone()
    if not catalog:
        raise ValueError('RAW_PAGE_SYMBOL_NOT_IN_CATALOG')
    converted=quarantined=0
    with db:
        for ordinal,quote in enumerate(quotes):
            try:
                if quote.get('symbol')!=page['symbol']:
                    raise ValueError('RAW_PAGE_SYMBOL_MISMATCH')
                record=normalize_quote(quote,page['received_at'],rules)
                if record['exchange']!=catalog[0]:
                    raise ValueError('RAW_QUOTE_EXCHANGE_MISMATCH')
                moment=datetime.fromisoformat(record['source_timestamp']).timestamp()
                if not settings['from']<=moment<=settings['to']:
                    raise ValueError('RAW_QUOTE_OUTSIDE_ARCHIVE_WINDOW')
            except (ValueError,TypeError,KeyError) as error:
                reason=str(error) if isinstance(error,ValueError) and str(error).isupper() and len(str(error))<100 else type(error).__name__
                db.execute('INSERT INTO quarantine VALUES(?,?,?,?)',(archive_id,page['id'],ordinal,reason))
                quarantined+=1
                continue
            key=record['event_id']
            compressed=zlib.compress(canonical_json(record).encode(),1)
            cursor=db.execute('''INSERT OR IGNORE INTO snapshots(event_key,symbol,exchange,source_time,
              source_local_date,received_at,quality,units_status,market_session,financial_eligible,
              bid_depth,ask_depth,aggregate_inconsistent,payload_zlib) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (key,record['symbol'],record['exchange'],record['source_timestamp'],record['local_date'],
               record['collected_at_utc'],record['data_quality_status'],record['unit_verification_status'],
               record['market_session'],int(record['financial_eligible']),record['bid_observed_depth_shares'],
               record['ask_observed_depth_shares'],int(bool(record['ignored_source_aggregate_fields'])),
               compressed))
            if cursor.rowcount:
                snapshot_id=cursor.lastrowid
            else:
                existing=db.execute('SELECT id,received_at FROM snapshots WHERE event_key=?',(key,)).fetchone()
                snapshot_id=existing['id']
                if datetime.fromisoformat(record['collected_at_utc'])<datetime.fromisoformat(existing['received_at']):
                    db.execute('UPDATE snapshots SET received_at=?,payload_zlib=? WHERE id=?',
                               (record['collected_at_utc'],compressed,snapshot_id))
            db.execute('INSERT INTO lineage VALUES(?,?,?,?)',(archive_id,page['id'],ordinal,snapshot_id))
            converted+=1
        db.execute('INSERT INTO processed_pages VALUES(?,?,?,?,?,?)',
                   (archive_id,page['id'],page['sha256'],len(quotes),converted,quarantined))
    return True


def summary(db,state):
    return {'state':state,'updated_at_utc':utcnow(),'normalization_version':NORMALIZATION_VERSION,
        'archives':db.execute('SELECT count(*) FROM archives').fetchone()[0],
        'pages_processed':db.execute('SELECT count(*) FROM processed_pages').fetchone()[0],
        'raw_occurrences_processed':db.execute('SELECT COALESCE(sum(raw_count),0) FROM processed_pages').fetchone()[0],
        'normalized_occurrences':db.execute('SELECT count(*) FROM lineage').fetchone()[0],
        'unique_normalized_snapshots':db.execute('SELECT count(*) FROM snapshots').fetchone()[0],
        'quarantined_occurrences':db.execute('SELECT count(*) FROM quarantine').fetchone()[0],
        'symbols_with_normalized_data':db.execute('SELECT count(DISTINCT symbol) FROM snapshots').fetchone()[0]}


def run(root=ROOT,max_pages=None):
    output=root/'data/processed/dnse/normalized.db'
    rules=load_rules(prepare_rules(root))
    report=root/'report/dnse_normalization.json'
    paths=[]
    for folder in sorted((root/'data/history/dnse').iterdir()):
        if folder.is_dir():
            for candidate in (folder/'history.db',folder/'morning/history.db'):
                if candidate.exists():
                    paths.append(candidate.resolve())
    if not paths:
        raise ValueError('NO_RAW_DNSE_ARCHIVE')
    processed=0
    with exclusive_job(root/'data/control/dnse_history.lock'),exclusive_job(root/'data/control/dnse_normalization.lock'),keep_awake(),closing(connect_output(output)) as db:
        current=db.execute('SELECT hash FROM normalization_rules').fetchall()
        if current and {row[0] for row in current}!={rules.rule_hash}:
            raise ValueError('NORMALIZATION_RULES_CHANGED_USE_NEW_OUTPUT_DATABASE')
        with db:
            db.execute('INSERT OR IGNORE INTO normalization_rules VALUES(?,?)',(rules.rule_hash,canonical_json(rules.payload)))
        state='COMPLETED'
        last_progress=0
        for path in paths:
            source,archive_id=register_archive(db,path,root,rules)
            try:
                for page in source.execute('SELECT * FROM pages ORDER BY id'):
                    if max_pages is not None and processed>=max_pages:
                        state='PAUSED_PAGE_LIMIT'
                        break
                    if (root/'data/control/STOP_DNSE_NORMALIZATION').exists():
                        state='STOPPED_BY_USER'
                        break
                    if shutil.disk_usage(output.parent).free<5*1024**3:
                        state='STOPPED_LOW_DISK'
                        break
                    if process_page(db,archive_id,page,rules):
                        processed+=1
                    if time.monotonic()-last_progress>15:
                        result=summary(db,'RUNNING')
                        write_json(report,result)
                        print(json.dumps(result),flush=True)
                        last_progress=time.monotonic()
            finally:
                source.close()
            if file_sha(path)!=archive_id:
                raise ValueError('RAW_ARCHIVE_CHANGED_DURING_NORMALIZATION')
            if state!='COMPLETED':
                break
        result=summary(db,state)
        result['duplicate_raw_occurrences']=result['normalized_occurrences']-result['unique_normalized_snapshots']
        result['rule_hash']=rules.rule_hash
        result['raw_modified']=False
        result['quality_counts']=dict(db.execute('SELECT quality,count(*) FROM snapshots GROUP BY quality').fetchall())
        result['unit_scope_counts']=dict(db.execute('SELECT units_status,count(*) FROM snapshots GROUP BY units_status').fetchall())
        result['market_session_counts']=dict(db.execute('SELECT market_session,count(*) FROM snapshots GROUP BY market_session').fetchall())
        result['financial_eligible_snapshots']=db.execute('SELECT count(*) FROM snapshots WHERE financial_eligible=1').fetchone()[0]
        result['coverage']=[dict(row) for row in db.execute('''SELECT c.archive_id,c.symbol,c.exchange,c.status,
            c.raw_quote_count,COALESCE(n.converted,0) AS normalized_occurrences FROM raw_catalog c LEFT JOIN
            (SELECT l.archive_id,s.symbol,count(*) AS converted FROM lineage l JOIN snapshots s ON s.id=l.snapshot_id
             GROUP BY l.archive_id,s.symbol) n ON n.archive_id=c.archive_id AND n.symbol=c.symbol
            ORDER BY c.archive_id,c.exchange,c.symbol''')]
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        result['integrity']=db.execute('PRAGMA quick_check').fetchone()[0]
        write_json(report,result)
        print(json.dumps({key:value for key,value in result.items() if key!='coverage'}),flush=True)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-pages',type=int)
    args=parser.parse_args()
    try:
        result=run(max_pages=args.max_pages)
    except (Exception,KeyboardInterrupt) as error:
        state='INTERRUPTED_RESUMABLE' if isinstance(error,KeyboardInterrupt) else 'FAILED_RESUMABLE'
        code=str(error) if str(error).isupper() and len(str(error))<100 else type(error).__name__
        output=ROOT/'data/processed/dnse/normalized.db'
        if output.exists():
            with closing(connect_output(output)) as db:
                result=summary(db,state)
        else:
            result={'state':state}
        result['error_code']=code
        write_json(ROOT/'report/dnse_normalization.json',result)
        print(json.dumps(result),flush=True)
        return 1
    return 0 if result['state']=='COMPLETED' else 1


if __name__=='__main__':
    raise SystemExit(main())
