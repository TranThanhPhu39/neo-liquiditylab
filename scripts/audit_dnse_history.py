"""Verify the saved DNSE daily raw archive and optionally stream an export."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

from config.settings import ROOT
from scripts.collect_dnse_history import token_hash,utcnow
from scripts.scheduled_collection import write_json


def audit(day,*,root=ROOT,export=False,session='day'):
    if session not in ('day','morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    folder=root/'data/history/dnse'/day
    if session!='day':
        folder=folder/session
    db=sqlite3.connect((folder/'history.db').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    integrity=db.execute('PRAGMA quick_check').fetchone()[0]
    if integrity!='ok':
        raise ValueError('SQLITE_INTEGRITY_FAILED')
    symbols={row['symbol']:dict(row) for row in db.execute('SELECT * FROM symbols')}
    universe=json.loads((folder/'universe.json').read_text(encoding='utf-8'))
    if set(symbols)!={row['symbol'] for row in universe['rows']}:
        raise ValueError('UNIVERSE_DATABASE_MISMATCH')
    expected_tokens={symbol:token_hash(None) for symbol in symbols}
    page_counts=Counter()
    quote_counts=Counter()
    first_times={}
    last_times={}
    total_pages=total_quotes=0
    destination=folder/'quotes.raw.jsonl.gz'
    temporary=folder/'quotes.raw.jsonl.gz.tmp'
    output=gzip.open(temporary,'wt',encoding='utf-8',compresslevel=3) if export else None
    try:
        for page in db.execute('SELECT * FROM pages ORDER BY symbol,page_number'):
            symbol=page['symbol']
            if expected_tokens[symbol]!=page['request_token_hash'] or page['page_number']!=page_counts[symbol]+1:
                raise ValueError('PAGE_CHAIN_BROKEN')
            raw=gzip.decompress(page['payload_gzip'])
            if hashlib.sha256(raw).hexdigest()!=page['sha256']:
                raise ValueError('PAGE_CHECKSUM_FAILED')
            payload=json.loads(raw)
            quotes=payload['quotes']
            quote_hash=hashlib.sha256(json.dumps(quotes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if quote_hash!=page['quotes_sha256'] or len(quotes)!=page['quote_count']:
                raise ValueError('QUOTE_CHECKSUM_OR_COUNT_FAILED')
            expected_tokens[symbol]=page['next_token_hash']
            page_counts[symbol]+=1
            quote_counts[symbol]+=len(quotes)
            total_pages+=1
            total_quotes+=len(quotes)
            for quote in quotes:
                if quote['symbol']!=symbol or quote['marketId']!=symbols[symbol]['market_id'] or quote['boardId']!='G1':
                    raise ValueError('QUOTE_SCOPE_MISMATCH')
                stamp=quote['time']
                first_times[symbol]=min(first_times.get(symbol,stamp),stamp)
                last_times[symbol]=max(last_times.get(symbol,stamp),stamp)
                if output:
                    output.write(json.dumps({'provider':'dnse','units_verified':False,'raw_page_id':page['id'],
                        'received_at_utc':page['received_at'],'quote':quote},ensure_ascii=False,separators=(',',':'))+'\n')
        for symbol,row in symbols.items():
            if row['quote_count']!=quote_counts[symbol] or row['page_count']!=page_counts[symbol]:
                raise ValueError('SYMBOL_COUNTS_MISMATCH')
            if row['first_time']!=first_times.get(symbol) or row['last_time']!=last_times.get(symbol):
                raise ValueError('SYMBOL_TIME_RANGE_MISMATCH')
            if row['status'] in ('COMPLETE','NO_DATA_RETURNED') and (row['next_token'] or expected_tokens[symbol] is not None):
                raise ValueError('COMPLETED_SYMBOL_HAS_NEXT_PAGE')
        if output:
            output.close()
            output=None
            temporary.replace(destination)
        counts=Counter(row['status'] for row in symbols.values())
        result={'date':day,'session':session,'checked_at_utc':utcnow(),'provider':'dnse','units_verified':False,
            'sqlite_integrity':integrity,'page_checksums_verified':total_pages,
            'raw_quote_count_verified':total_quotes,'symbols_total':len(symbols),'symbol_status':dict(counts),
            'all_symbols_fully_paginated':sum(counts[key] for key in ('COMPLETE','NO_DATA_RETURNED'))==len(symbols),
            'symbols_with_records':sum(value>0 for value in quote_counts.values()),
            'no_data_symbols':sorted(symbol for symbol,row in symbols.items() if row['status']=='NO_DATA_RETURNED'),
            'first_source_time':min(first_times.values()) if first_times else None,
            'last_source_time':max(last_times.values()) if last_times else None,
            'export':str(destination) if export else None,
            'note':'Provider page chain exhausted per symbol; this does not independently certify upstream data completeness. G1 equity raw only; no trading or normalization.'}
        write_json(root/'report'/f"dnse_history_audit_{day}{'_'+session if session!='day' else ''}.json",result)
        return result
    finally:
        if output:
            output.close()
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--export',action='store_true')
    parser.add_argument('--session',choices=['day','morning'],default='day')
    args=parser.parse_args()
    print(json.dumps(audit(args.date,export=args.export,session=args.session),ensure_ascii=True))


if __name__=='__main__':
    main()
