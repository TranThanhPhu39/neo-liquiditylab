"""Independently reconcile canonical DNSE records against every raw row and export."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import gzip
import hashlib
import json
import sqlite3
import time
import zlib

from config.settings import ROOT
from scripts.collect_dnse_history import keep_awake,utcnow
from scripts.normalize_dnse_history import file_sha
from scripts.scheduled_collection import write_json
from src.models.orderbook import LOCAL_TZ
from src.data_pipeline.dnse_normalization import snapshot_from_record


def expected_number(value,factor,volume):
    try:
        if value is None or isinstance(value,bool):
            return None
        number=Decimal(str(value))*factor
        if not number.is_finite() or number<0 or not volume and number==0:
            return None
        if volume and number!=number.to_integral_value():
            return None
        return int(number) if volume else number
    except (InvalidOperation,ValueError,TypeError):
        return None


def check_record(record,quote,page_received,rule_hash):
    if record['symbol']!=quote['symbol'] or record['market_id']!=quote['marketId'] or record['board_id']!=quote['boardId']:
        raise ValueError('NORMALIZED_SCOPE_MISMATCH')
    expected_time=datetime.strptime(quote['time'],'%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=LOCAL_TZ)
    if datetime.fromisoformat(record['source_timestamp'])!=expected_time.astimezone(timezone.utc):
        raise ValueError('NORMALIZED_SOURCE_CLOCK_CHANGED')
    if record['local_date']!=expected_time.date().isoformat():
        raise ValueError('NORMALIZED_LOCAL_DATE_CHANGED')
    if datetime.fromisoformat(record['collected_at_utc'])>datetime.fromisoformat(page_received):
        raise ValueError('NORMALIZED_RECEIPT_NOT_EARLIEST')
    if record['unit_contract_hash']!=rule_hash or record['price_unit']!='VND' or record['volume_unit']!='shares':
        raise ValueError('NORMALIZED_UNIT_METADATA_MISMATCH')
    for raw_side,side in (('bid','bid'),('offer','ask')):
        levels=quote.get(raw_side) or []
        known=[]
        for index in range(3):
            raw=levels[index] if index<len(levels) else {}
            expected_price=expected_number(raw.get('price'),Decimal(1000),False)
            expected_volume=expected_number(raw.get('quantity'),Decimal(10),True)
            value=record[f'{side}_price_{index+1}']
            actual_price=Decimal(value) if value is not None else None
            if actual_price!=expected_price or record[f'{side}_vol_{index+1}']!=expected_volume:
                raise ValueError('NORMALIZED_LEVEL_SCALE_MISMATCH')
            if expected_price is not None and expected_volume is not None:
                known.append(expected_volume)
        if record[side+'_observed_depth_shares']!=(sum(known) if known else None):
            raise ValueError('NORMALIZED_DEPTH_NOT_FROM_LEVELS')
    if record['unit_verification_status']=='ASSUMED_SHARED_SCHEMA' and record['financial_eligible']:
        raise ValueError('UNVERIFIED_SCOPE_FINANCIAL_ELIGIBILITY')


def audit(root=ROOT,export=False):
    folder=root/'data/processed/dnse'
    path=folder/'normalized.db'
    checked=quarantined=pages_checked=0
    last_progress=0
    with keep_awake(),closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        rules=db.execute('SELECT hash,payload FROM normalization_rules').fetchone()
        actual_rule_hash=hashlib.sha256(json.dumps(json.loads(rules['payload']),sort_keys=True,
            ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        if actual_rule_hash!=rules['hash']:
            raise ValueError('NORMALIZATION_RULE_CHECKSUM_FAILED')
        source_files=[]
        for archive in db.execute('SELECT * FROM archives ORDER BY path').fetchall():
            source_path=root/archive['path']
            before=file_sha(source_path)
            if before!=archive['sha256']:
                raise ValueError('RAW_ARCHIVE_CHECKSUM_CHANGED')
            with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as source:
                source.row_factory=sqlite3.Row
                expected_pages=source.execute('SELECT count(*) FROM pages').fetchone()[0]
                if db.execute('SELECT count(*) FROM processed_pages WHERE archive_id=?',(archive['id'],)).fetchone()[0]!=expected_pages:
                    raise ValueError('NORMALIZATION_PAGES_INCOMPLETE')
                for page in source.execute('SELECT * FROM pages ORDER BY id'):
                    raw=gzip.decompress(page['payload_gzip'])
                    if hashlib.sha256(raw).hexdigest()!=page['sha256']:
                        raise ValueError('RAW_PAGE_CHECKSUM_FAILED')
                    ledger=db.execute('SELECT * FROM processed_pages WHERE archive_id=? AND page_id=?',(archive['id'],page['id'])).fetchone()
                    quotes=json.loads(raw)['quotes']
                    if ledger['sha256']!=page['sha256'] or ledger['raw_count']!=len(quotes):
                        raise ValueError('NORMALIZED_PAGE_LEDGER_MISMATCH')
                    mapped={row['ordinal']:row for row in db.execute('''SELECT l.ordinal,s.* FROM lineage l
                        JOIN snapshots s ON s.id=l.snapshot_id WHERE l.archive_id=? AND l.page_id=?''',(archive['id'],page['id']))}
                    rejected={row[0] for row in db.execute('SELECT ordinal FROM quarantine WHERE archive_id=? AND page_id=?',(archive['id'],page['id']))}
                    if set(mapped)&rejected or set(mapped)|rejected!=set(range(len(quotes))):
                        raise ValueError('RAW_ROW_LINEAGE_COVERAGE_INCOMPLETE')
                    for index,row in mapped.items():
                        record=json.loads(zlib.decompress(row['payload_zlib']))
                        check_record(record,quotes[index],page['received_at'],rules['hash'])
                        expected_identity='dnse:'+hashlib.sha256(json.dumps(quotes[index],sort_keys=True,
                            ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                        if row['event_key']!=expected_identity or record['event_id']!=expected_identity:
                            raise ValueError('RAW_EVENT_IDENTITY_MISMATCH')
                        if row['quality']!=record['data_quality_status'] or row['units_status']!=record['unit_verification_status']:
                            raise ValueError('NORMALIZED_INDEX_PAYLOAD_MISMATCH')
                        checked+=1
                    quarantined+=len(rejected)
                    pages_checked+=1
                    if time.monotonic()-last_progress>15:
                        print(json.dumps({'state':'AUDITING','pages_checked':pages_checked,'raw_rows_reconciled':checked,
                                          'quarantined_rows':quarantined}),flush=True)
                        last_progress=time.monotonic()
            if file_sha(source_path)!=before:
                raise ValueError('RAW_ARCHIVE_CHANGED_DURING_AUDIT')
            source_files.append({'path':archive['path'],'sha256':before})
        total=db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
        result={'state':'VERIFIED','checked_at_utc':utcnow(),'pages_verified':pages_checked,
            'raw_rows_reconciled':checked,'unique_normalized_snapshots':total,'quarantined_rows':quarantined,
            'duplicates_preserved_as_lineage':checked-total,'source_archives_unchanged':source_files,
            'integrity':db.execute('PRAGMA quick_check').fetchone()[0],
            'foreign_key_errors':len(db.execute('PRAGMA foreign_key_check').fetchall()),
            'unit_scope_counts':dict(db.execute('SELECT units_status,count(*) FROM snapshots GROUP BY units_status').fetchall()),
            'quality_counts':dict(db.execute('SELECT quality,count(*) FROM snapshots GROUP BY quality').fetchall()),
            'financial_eligible_snapshots':db.execute('SELECT count(*) FROM snapshots WHERE financial_eligible=1').fetchone()[0]}
        if result['integrity']!='ok' or result['foreign_key_errors']:
            raise ValueError('NORMALIZED_DATABASE_INTEGRITY_FAILED')
        if checked+quarantined!=db.execute('SELECT sum(raw_records) FROM archives').fetchone()[0]:
            raise ValueError('RAW_ARCHIVE_RECORD_TOTAL_MISMATCH')
        if export:
            target=folder/'quotes.normalized.jsonl.gz'
            temporary=target.with_suffix(target.suffix+'.tmp')
            exported=0
            with gzip.open(temporary,'wb',compresslevel=1) as stream:
                for row in db.execute('SELECT payload_zlib FROM snapshots ORDER BY id'):
                    stream.write(zlib.decompress(row[0])+b'\n')
                    exported+=1
                    if exported%100000==0:
                        print(json.dumps({'state':'EXPORTING','records_written':exported}),flush=True)
            if exported!=total:
                raise ValueError('NORMALIZED_EXPORT_COUNT_MISMATCH')
            temporary.replace(target)
            result['export']={'path':str(target.relative_to(root)),'records':exported,'sha256':file_sha(target),'bytes':target.stat().st_size}
        result['database']={'path':str(path.relative_to(root)),'sha256':file_sha(path),'bytes':path.stat().st_size}
        write_json(root/'report/dnse_normalization_audit.json',result)
        return result


def validate_finance(root=ROOT):
    from src.finance.execution_simulator import simulate_execution
    from src.finance.stress_testing import compare_scenario
    from src.models.scenario import Scenario
    path=root/'data/processed/dnse/normalized.db'
    checked=0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        samples=[]
        for ordering in ('ASC','DESC'):
            samples.extend(row[0] for row in db.execute('SELECT payload_zlib FROM snapshots WHERE financial_eligible=1 ORDER BY source_time '+ordering+' LIMIT 20'))
        for encoded in samples:
            record=json.loads(zlib.decompress(encoded))
            book=snapshot_from_record(record)
            for side,depth_key in (('sell','bid_observed_depth_shares'),('buy','ask_observed_depth_shares')):
                depth=record[depth_key]
                if not depth:
                    continue
                requested=max(100,depth//2)
                result=simulate_execution(book,requested,side=side)
                if result.gross_value!=sum((fill.price*fill.quantity for fill in result.fills),Decimal(0)) or not 0<=result.coverage_ratio<=1:
                    raise ValueError('FINANCIAL_SAMPLE_INVARIANT_FAILED')
                if side=='sell':
                    compared=compare_scenario(book,requested,Scenario(Decimal('.5')))
                    if compared['stressed'].executed_quantity>compared['baseline'].executed_quantity:
                        raise ValueError('FINANCIAL_STRESS_MONOTONICITY_FAILED')
                checked+=1
        assumed=db.execute("SELECT payload_zlib FROM snapshots WHERE units_status='ASSUMED_SHARED_SCHEMA' LIMIT 1").fetchone()
        blocked=False
        if assumed:
            try:
                simulate_execution(snapshot_from_record(json.loads(zlib.decompress(assumed[0]))),100)
            except ValueError:
                blocked=True
            if not blocked:
                raise ValueError('UNVERIFIED_UNIT_GUARD_FAILED')
    result={'state':'PASSED','eligible_snapshots_sampled':len(samples),'execution_checks':checked,
            'assumed_scope_default_financial_calculation_blocked':blocked}
    write_json(root/'report/dnse_normalization_finance_checks.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export',action='store_true')
    args=parser.parse_args()
    result=audit(export=args.export)
    result['finance_checks']=validate_finance()
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
