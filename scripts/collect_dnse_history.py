"""Resumable daily raw bid/ask download for every listed equity, board G1 only."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date,datetime,timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

from config.settings import ROOT
from scripts.scheduled_collection import exclusive_job,write_json
from src.data_providers.dnse_readonly import DNSEReadOnly,DNSEHTTPError
from src.data_providers.base import ProviderError

VN=ZoneInfo('Asia/Ho_Chi_Minh')
MARKETS={'STO':'HOSE','STX':'HNX','UPX':'UPCOM'}
SAFE_ERRORS={'DNSE_TIMEOUT','DNSE_NETWORK_ERROR','DNSE_DNS_ERROR','DNSE_CONNECTION_ERROR',
             'DNSE_TLS_ERROR','DNSE_TLS_VERIFICATION_FAILED','DNSE_RESPONSE_TOO_LARGE','DNSE_INVALID_JSON'}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def keep_awake():
    """Temporarily keep Windows awake while the user-requested download runs."""
    previous=None
    if os.name=='nt':
        import ctypes
        previous=ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        yield
    finally:
        if os.name=='nt' and previous:
            ctypes.windll.kernel32.SetThreadExecutionState(previous)


def connect(path):
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY, exchange TEXT NOT NULL, market_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', next_token TEXT, page_count INTEGER NOT NULL DEFAULT 0,
            quote_count INTEGER NOT NULL DEFAULT 0, first_time TEXT, last_time TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL, symbol TEXT, requested_at TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'STARTED');
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, page_number INTEGER NOT NULL,
            request_token_hash TEXT NOT NULL, next_token_hash TEXT,
            received_at TEXT NOT NULL, quote_count INTEGER NOT NULL,
            first_time TEXT, last_time TEXT, sha256 TEXT NOT NULL, quotes_sha256 TEXT NOT NULL,
            payload_gzip BLOB NOT NULL,
            UNIQUE(symbol,page_number), UNIQUE(symbol,request_token_hash), UNIQUE(symbol,quotes_sha256));
        CREATE INDEX IF NOT EXISTS symbol_status ON symbols(status,page_count,symbol);
    ''')
    return db


def initialize(db,day,page_size,session='day'):
    if session not in ('day','morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    if date.fromisoformat(day)>datetime.now(VN).date():
        raise ValueError('FUTURE_DATE_NOT_ALLOWED')
    # Includes pre-open book messages, all morning/afternoon phases, and closing messages.
    start=datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=VN)
    end=datetime.fromisoformat(day+('T11:30:00' if session=='morning' else 'T15:10:00')).replace(tzinfo=VN)
    if end>datetime.now(VN):
        raise ValueError('MORNING_DOWNLOAD_REQUIRES_CLOSED_SESSION' if session=='morning' else 'DAILY_DOWNLOAD_REQUIRES_CLOSED_SESSION')
    settings={'date':day,'from':int(start.timestamp()),'to':int(end.timestamp()),
              'boardId':'G1','order':'ASC','page_size':page_size,'units_verified':False,
              'scope':'ALL_LISTED_EQUITIES_HOSE_HNX_UPCOM','api_version':'2026-07-23'}
    if session!='day':
        settings['session']=session
    previous=db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()
    if previous and json.loads(previous[0])!=settings:
        raise ValueError('RESUME_SETTINGS_MISMATCH')
    with db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('settings',?)",(json.dumps(settings),))
    return settings


def request(db,client,path,params,symbol=None):
    # Persist attempted calls before I/O so crash/restart cannot reset the local budget.
    day=datetime.now(timezone.utc).date().isoformat()
    used=db.execute('SELECT count(*) FROM requests WHERE requested_at>=?',(day,)).fetchone()[0]
    if used>=90000:
        raise RuntimeError('LOCAL_REQUEST_BUDGET_REACHED')
    with db:
        row=db.execute('INSERT INTO requests(endpoint,symbol,requested_at) VALUES(?,?,?)',
                       (path,symbol,utcnow()))
        request_id=row.lastrowid
    try:
        result=client.get(path,params)
    except Exception as error:
        code=f'HTTP_{error.status}' if isinstance(error,DNSEHTTPError) else (
             str(error) if isinstance(error,ProviderError) and str(error) in SAFE_ERRORS else type(error).__name__)
        with db:
            db.execute('UPDATE requests SET outcome=? WHERE id=?',(code,request_id))
        raise
    with db:
        db.execute("UPDATE requests SET outcome='OK' WHERE id=?",(request_id,))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('rate_limit_headers',?)",
                   (json.dumps(result.get('rate_limit_headers',{})),))
    return result


def request_with_retries(db,client,path,params,symbol=None):
    for attempt in range(3):
        try:
            return request(db,client,path,params,symbol)
        except ProviderError as error:
            retryable=(isinstance(error,DNSEHTTPError) and error.status in (500,502,503,504)) or (
                not isinstance(error,DNSEHTTPError) and str(error) in {
                    'DNSE_TIMEOUT','DNSE_NETWORK_ERROR','DNSE_DNS_ERROR','DNSE_CONNECTION_ERROR'})
            if not retryable or attempt==2:
                raise
            code=f'HTTP_{error.status}' if isinstance(error,DNSEHTTPError) else str(error)
            print(json.dumps({'state':'RETRYING_TRANSIENT_ERROR','symbol':symbol,'error_code':code,
                              'next_attempt':attempt+2}),flush=True)
            time.sleep((2,5)[attempt])


def load_universe(db,client,folder):
    if db.execute("SELECT 1 FROM metadata WHERE key='universe_complete'").fetchone():
        return
    rows=[]
    expected=None
    for page in range(1,31):
        observation=request_with_retries(db,client,'/instruments',{'securityGroupId':'ST','limit':1000,'page':page})
        payload=observation['payload']
        data=payload.get('data') if isinstance(payload,dict) else None
        if not isinstance(data,list) or not isinstance(payload.get('total'),int):
            raise ValueError('DNSE_UNIVERSE_SCHEMA_INVALID')
        if expected is not None and expected!=payload['total']:
            raise ValueError('DNSE_UNIVERSE_CHANGED_DURING_PAGINATION')
        expected=payload['total']
        rows.extend(data)
        write_json(folder/f'universe_page_{page}.json',observation)
        if len(rows)>=expected:
            break
        if not data:
            raise ValueError('DNSE_UNIVERSE_TRUNCATED')
    if len(rows)!=expected:
        raise ValueError('DNSE_UNIVERSE_INCOMPLETE')
    universe={}
    for row in rows:
        symbol=row.get('symbol','')
        market=row.get('marketId')
        if market not in MARKETS or row.get('securityGroupId')!='ST' or not re.fullmatch('[A-Z0-9]{1,12}',symbol):
            raise ValueError('UNEXPECTED_SECURITY_IN_EQUITY_UNIVERSE')
        if symbol in universe:
            raise ValueError('DUPLICATE_UNIVERSE_SYMBOL')
        universe[symbol]={'symbol':symbol,'exchange':MARKETS[market],'market_id':market}
    if set(row['market_id'] for row in universe.values())!=set(MARKETS):
        raise ValueError('UNIVERSE_MISSING_EXCHANGE')
    write_json(folder/'universe.json',{'provider':'dnse','received_at_utc':utcnow(),
               'count':len(universe),'rows':list(universe.values())})
    with db:
        db.executemany('INSERT OR IGNORE INTO symbols(symbol,exchange,market_id) VALUES(:symbol,:exchange,:market_id)',universe.values())
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('universe_complete','true')")


def token_hash(token):
    return hashlib.sha256((token or '').encode()).hexdigest()


def store_page(db,row,observation,settings):
    payload=observation['payload']
    quotes=payload.get('quotes') if isinstance(payload,dict) else None
    if not isinstance(quotes,list) or len(quotes)>settings['page_size']:
        raise ValueError('DNSE_QUOTES_SCHEMA_OR_LIMIT_INVALID')
    times=[]
    for quote in quotes:
        if not isinstance(quote,dict) or quote.get('symbol')!=row['symbol'] or quote.get('marketId')!=row['market_id'] or quote.get('boardId')!='G1':
            raise ValueError('DNSE_QUOTE_SCOPE_MISMATCH')
        stamp=quote.get('time')
        if not isinstance(stamp,str) or any(side not in quote or quote[side] is not None and not isinstance(quote[side],list)
                                            for side in ('bid','offer')):
            raise ValueError('DNSE_QUOTE_SCHEMA_INVALID')
        moment=datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=VN).timestamp()
        if not settings['from']<=moment<=settings['to']:
            raise ValueError('DNSE_TIME_OUTSIDE_REQUEST')
        times.append(stamp)
    if times!=sorted(times) or times and row['last_time'] and times[0]<row['last_time']:
        raise ValueError('DNSE_PAGE_TIME_REGRESSED')
    next_token=payload.get('nextPageToken') or None
    if next_token and (not isinstance(next_token,str) or len(next_token)>4096 or not quotes):
        raise ValueError('DNSE_PAGE_TOKEN_INVALID')
    current_hash=token_hash(row['next_token'])
    next_hash=token_hash(next_token) if next_token else None
    if next_hash and (next_hash==current_hash or db.execute(
            'SELECT 1 FROM pages WHERE symbol=? AND request_token_hash=?',(row['symbol'],next_hash)).fetchone()):
        raise ValueError('DNSE_PAGE_TOKEN_REPEATED')
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    quote_digest=hashlib.sha256(json.dumps(quotes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if db.execute('SELECT 1 FROM pages WHERE symbol=? AND quotes_sha256=?',(row['symbol'],quote_digest)).fetchone():
        raise ValueError('DNSE_PAGE_REPEATED')
    complete=not next_token
    status=('COMPLETE' if quotes or row['quote_count'] else 'NO_DATA_RETURNED') if complete else 'PARTIAL'
    with db:
        db.execute('''INSERT INTO pages(symbol,page_number,request_token_hash,next_token_hash,
            received_at,quote_count,first_time,last_time,sha256,quotes_sha256,payload_gzip) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (row['symbol'],row['page_count']+1,current_hash,next_hash,observation['received_at_utc'],len(quotes),
             min(times) if times else None,max(times) if times else None,digest,quote_digest,gzip.compress(encoded,compresslevel=3)))
        db.execute('''UPDATE symbols SET status=?,next_token=?,page_count=page_count+1,
            quote_count=quote_count+?,first_time=COALESCE(first_time,?),last_time=COALESCE(?,last_time),error=NULL
            WHERE symbol=?''',(status,next_token,len(quotes),min(times) if times else None,
                              max(times) if times else None,row['symbol']))
    return status


def summary(db,state,error=None):
    statuses=dict(db.execute('SELECT status,count(*) FROM symbols GROUP BY status').fetchall())
    pages,quotes=db.execute('SELECT count(*),COALESCE(sum(quote_count),0) FROM pages').fetchone()
    exchanges={row[0]:row[1] for row in db.execute('SELECT exchange,count(*) FROM symbols GROUP BY exchange')}
    complete=statuses.get('COMPLETE',0)+statuses.get('NO_DATA_RETURNED',0)
    stored_settings=db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()
    result={'provider':'dnse','state':state,'updated_at_utc':utcnow(),'settings':json.loads(stored_settings[0]) if stored_settings else None,'units_verified':False,
        'finance_enabled':False,'symbols_total':sum(statuses.values()),'exchange_counts':exchanges,
        'symbol_status':statuses,'symbols_fully_paginated':complete,'pages_saved':pages,'raw_quote_count':quotes,
        'requests_attempted':db.execute('SELECT count(*) FROM requests').fetchone()[0],
        'note':'G1 listed equities only. Raw records; not normalized. Empty API results do not prove no trading.'}
    if error:
        result['error_code']=error
    return result


def run(day,*,page_size=1000,root=ROOT,max_requests=None,client=None,session='day'):
    if not isinstance(page_size,int) or not 1<=page_size<=1000:
        raise ValueError('PAGE_SIZE_MUST_BE_1_TO_1000')
    date.fromisoformat(day)
    if session not in ('day','morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    folder=root/'data/history/dnse'/day
    if session!='day':
        folder=folder/session
    folder.mkdir(parents=True,exist_ok=True)
    report=root/'report'/f"dnse_history_{day}{'_'+session if session!='day' else ''}.json"
    with exclusive_job(root/'data/control/dnse_history.lock'),keep_awake():
        db=connect(folder/'history.db')
        state='RUNNING'
        error_code=None
        try:
            settings=initialize(db,day,page_size,session)
            client=client or DNSEReadOnly(min_interval=0.5)
            load_universe(db,client,folder)
            initial_requests=db.execute('SELECT count(*) FROM requests').fetchone()[0]
            last_progress=0
            consecutive_failures=0
            # Round-robin pages give every symbol coverage before deeply paginating busy symbols.
            while True:
                if (root/'data/control/STOP_DNSE_HISTORY').exists():
                    state='STOPPED_BY_USER'
                    break
                if shutil.disk_usage(folder).free<5*1024**3:
                    state='STOPPED_LOW_DISK'
                    break
                if max_requests is not None and db.execute('SELECT count(*) FROM requests').fetchone()[0]-initial_requests>=max_requests:
                    state='PAUSED_REQUEST_LIMIT'
                    break
                row=db.execute("SELECT * FROM symbols WHERE status IN ('PENDING','PARTIAL') ORDER BY page_count,symbol LIMIT 1").fetchone()
                if row is None:
                    state='COMPLETED_WITH_ERRORS' if db.execute("SELECT 1 FROM symbols WHERE status='ERROR'").fetchone() else 'COMPLETED'
                    break
                params={'boardId':'G1','from':settings['from'],'to':settings['to'],
                        'limit':page_size,'order':'ASC'}
                if row['next_token']:
                    params['nextPageToken']=row['next_token']
                observation=None
                try:
                    observation=request_with_retries(db,client,f"/price/{row['symbol']}/quotes",params,row['symbol'])
                    try:
                        store_page(db,row,observation,settings)
                    except ValueError:
                        quarantine=folder/f"rejected_{row['symbol']}_{row['page_count']+1}_{int(time.time())}.json.gz"
                        quarantine.write_bytes(gzip.compress(json.dumps(observation,ensure_ascii=False).encode()))
                        raise
                    consecutive_failures=0
                except DNSEHTTPError as error:
                    if error.status not in (400,404):
                        raise
                    with db:
                        db.execute("UPDATE symbols SET status='ERROR',error=? WHERE symbol=?",(f'HTTP_{error.status}',row['symbol']))
                    consecutive_failures+=1
                    if consecutive_failures>=5:
                        raise RuntimeError('REPEATED_SYMBOL_HTTP_FAILURES')
                remaining=observation.get('rate_limit_headers',{}).get('X-RateLimit-Remaining') if observation else None
                if remaining is not None and int(remaining)<100:
                    state='STOPPED_QUOTA_RESERVE'
                    break
                if time.monotonic()-last_progress>=15:
                    result=summary(db,'RUNNING')
                    write_json(report,result)
                    print(json.dumps({key:result[key] for key in ('state','symbols_total','symbols_fully_paginated','pages_saved','raw_quote_count')}),flush=True)
                    last_progress=time.monotonic()
        except KeyboardInterrupt:
            state='INTERRUPTED_RESUMABLE'
        except Exception as error:
            state='FAILED_RESUMABLE'
            error_code=f'HTTP_{error.status}' if isinstance(error,DNSEHTTPError) else (
                str(error) if re.fullmatch('[A-Z0-9_]{1,90}',str(error)) else type(error).__name__)
        finally:
            result=summary(db,state,error_code)
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            result['sqlite_integrity']=db.execute('PRAGMA quick_check').fetchone()[0]
            result['database_bytes']=(folder/'history.db').stat().st_size
            result['incomplete_symbols']=[dict(row) for row in db.execute(
                "SELECT symbol,exchange,status,page_count,quote_count,error FROM symbols WHERE status NOT IN ('COMPLETE','NO_DATA_RETURNED') ORDER BY symbol")]
            result['no_data_symbols']=[row[0] for row in db.execute("SELECT symbol FROM symbols WHERE status='NO_DATA_RETURNED' ORDER BY symbol")]
            write_json(report,result)
            db.close()
        print(json.dumps({key:result[key] for key in ('state','symbols_total','symbols_fully_paginated','pages_saved','raw_quote_count','sqlite_integrity')}),flush=True)
        return result


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',default=datetime.now(VN).date().isoformat())
    parser.add_argument('--page-size',type=int,default=1000)
    parser.add_argument('--session',choices=['day','morning'],default='day')
    parser.add_argument('--max-requests',type=int,help='Optional explicit bounded smoke run; resume without this option')
    args=parser.parse_args()
    result=run(args.date,page_size=args.page_size,max_requests=args.max_requests,session=args.session)
    return 0 if result['state']=='COMPLETED' else 1


if __name__=='__main__':
    raise SystemExit(main())
