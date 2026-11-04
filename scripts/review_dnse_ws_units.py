"""Capture bounded REST evidence and compare it with already collected WS events."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
import zlib

from config.settings import ROOT
from scripts.normalize_dnse_history import prepare_rules
from scripts.normalize_dnse_realtime import audit_normalized
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_normalization import load_rules
from src.data_pipeline.dnse_ws_review import compare,rest_clock
from src.data_providers.dnse_readonly import DNSEReadOnly


def load_ws(database,symbols=None):
    database=Path(database).resolve()
    audit_normalized(database)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if symbols is None:
            rows=db.execute('''SELECT e.symbol,s.exchange,count(*) n FROM events e JOIN symbols s
                ON s.source_id=e.source_id AND s.symbol=e.symbol WHERE e.state='NORMALIZED'
                GROUP BY e.symbol,s.exchange ORDER BY n DESC,e.symbol''').fetchall()
            symbols=['ACB']
            for exchange in ('HNX','UPCOM'):
                chosen=next((r['symbol'] for r in rows if r['exchange']==exchange),None)
                if chosen:
                    symbols.append(chosen)
        if not 1<=len(symbols)<=12 or len(set(symbols))!=len(symbols) or any(not re.fullmatch('[A-Z0-9]{1,12}',s) for s in symbols):
            raise ValueError('REVIEW_SYMBOL_SCOPE_INVALID')
        result={}
        for symbol in symbols:
            records=[json.loads(zlib.decompress(r[0])) for r in db.execute(
                "SELECT payload_zlib FROM events WHERE symbol=? AND state='NORMALIZED' ORDER BY source_time_utc",(symbol,))]
            if not records or not any(r['source_unix_nanoseconds'] for r in records):
                raise ValueError('REVIEW_NO_WS_QUOTES_WITH_CLOCK')
            if len({(r['source_local_date'],r['market_id']) for r in records})!=1:
                raise ValueError('REVIEW_ONE_DAY_MARKET_PER_SYMBOL_REQUIRED')
            result[symbol]=records
    return result


def fetch_rest(symbol,ws,folder,client,max_pages=10):
    clocks=[int(r['source_unix_nanoseconds']) for r in ws if r['source_unix_nanoseconds']]
    start,end=min(clocks)//10**9,max(clocks)//10**9+1
    if end-start>3600:
        raise ValueError('REVIEW_WINDOW_EXCEEDS_ONE_HOUR')
    params={'boardId':'G1','from':start,'to':end,'order':'ASC','limit':1000}
    quotes=[];pages=[];tokens=set();previous=None
    for page in range(1,max_pages+1):
        observation=client.get(f'/price/{symbol}/quotes',params)
        payload=observation['payload']
        batch=payload.get('quotes') if isinstance(payload,dict) else None
        if not isinstance(batch,list) or len(batch)>1000:
            raise ValueError('REVIEW_REST_QUOTE_SCHEMA')
        for quote in batch:
            if (quote.get('symbol'),quote.get('marketId'),quote.get('boardId'))!=(symbol,ws[0]['market_id'],'G1'):
                raise ValueError('REVIEW_REST_SCOPE_MISMATCH')
            ns,_=rest_clock(quote['time'])
            if not start*10**9<=ns<end*10**9 or previous is not None and ns<previous:
                raise ValueError('REVIEW_REST_CLOCK_OUTSIDE_WINDOW_OR_REGRESSED')
            previous=ns
        encoded=json.dumps(observation,ensure_ascii=False,separators=(',',':')).encode()
        name=f'{symbol}.rest-page-{page:03d}.json.gz'
        packed=gzip.compress(encoded,mtime=0)
        (folder/name).write_bytes(packed)
        pages.append(dict(path=name,sha256=hashlib.sha256(packed).hexdigest(),quotes=len(batch)))
        quotes.extend(batch)
        token=payload.get('nextPageToken') or None
        if not token:
            return quotes,pages
        if not isinstance(token,str) or token in tokens or not batch:
            raise ValueError('REVIEW_REST_PAGINATION_INVALID')
        tokens.add(token);params['nextPageToken']=token
    raise ValueError('REVIEW_REST_PAGE_BUDGET_EXCEEDED')


def run(database,*,symbols=None,root=ROOT,client=None):
    root=Path(root)
    source=load_ws(database,symbols)
    rules=load_rules(prepare_rules(root))
    folder=root/'data/control/dnse_ws_rest_review'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    summaries=[]
    client=client or DNSEReadOnly()
    for symbol,ws in source.items():
        quotes,pages=fetch_rest(symbol,ws,folder,client)
        packed=gzip.compress(('\n'.join(json.dumps(r,ensure_ascii=False,separators=(',',':')) for r in ws)+'\n').encode(),mtime=0)
        ws_name=symbol+'.ws.jsonl.gz';(folder/ws_name).write_bytes(packed)
        result=compare(ws,quotes,symbol=symbol,market=ws[0]['market_id'],price_multiplier=rules.price_multiplier,
            volume_multiplier=rules.volume_multiplier,rest_unit_verified=symbol in rules.verified_symbols and ws[0]['market_id'] in rules.verified_markets)
        result.update(rest_unit_rule_hash=rules.rule_hash,ws_artifact=dict(path=ws_name,sha256=hashlib.sha256(packed).hexdigest()),rest_artifacts=pages)
        write_json(folder/(symbol+'.comparison.json'),result)
        summaries.append({k:v for k,v in result.items() if k not in ('matched','mismatches')})
        print(json.dumps({'symbol':symbol,'status':result['status'],'counts':result['counts'],'factors':result['factor_candidates']}),flush=True)
    report=dict(state='COMPARED_NOT_PROMOTED',created_at_utc=datetime.now(timezone.utc).isoformat(),
        private_evidence_folder=str(folder),database=str(Path(database).resolve()),symbols=summaries,
        rule_changes_applied=False,financial_scope_unchanged=True)
    days={r['source_local_date'] for records in source.values() for r in records}
    day=next(iter(days)) if len(days)==1 else 'multiple-days'
    write_json(root/'report'/f'dnse_ws_rest_review_{day}.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--symbols',nargs='+')
    args=parser.parse_args()
    report=run(args.database,symbols=args.symbols)
    print(json.dumps({'state':report['state'],'symbols':len(report['symbols']),'private_evidence_folder':report['private_evidence_folder']}))
