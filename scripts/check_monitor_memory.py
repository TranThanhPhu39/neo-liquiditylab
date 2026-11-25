"""Exercise real public read paths; CI runs the server with a hard 512 MiB limit."""
import argparse
import gzip
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def check(origin, day, timeout=600, repeats=3):
    started=time.monotonic();checks=[]
    def get(path):
        while True:
            try:
                with urlopen(origin+path,timeout=60) as response:
                    raw=response.read()
                    if response.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
                    return json.loads(raw)
            except HTTPError as error:
                if error.code not in (502,503,504):raise
            except (URLError,TimeoutError,ConnectionError):pass
            if time.monotonic()-started>timeout:raise RuntimeError('MEMORY_SMOKE_HTTP_TIMEOUT')
            time.sleep(2)
    health=get('/healthz')
    while True:
        status=get('/api/hosting')
        if status.get('seed_ready') and status.get('recovery_state')=='READY':break
        if time.monotonic()-started>timeout:raise RuntimeError('MEMORY_SMOKE_RECOVERY_TIMEOUT')
        time.sleep(2)
    entries=get('/api/datasets')['datasets']
    latest=max(e['day'] for e in entries)
    active=[e for e in entries if e['day']==day and not e.get('archived')]
    if not active:raise ValueError('MEMORY_SMOKE_DAY_MISSING')
    symbols=[e['symbol'] for e in active if e.get('market_observation') and e.get('observations')][:47]
    for attempt in range(repeats):
        for names in (['ACB','FPT','HPG'], ['ACB','FPT','HPG']+symbols):
            query=urlencode(dict(day=day,cutoff=day+'T14:00:00+07:00',symbols=','.join(names)))
            rows=get('/api/watchlist?'+query)
            assert [r['symbol'] for r in rows]==names
            checks.append(dict(operation='watchlist',iteration=attempt,symbols=len(rows)))
        count=len(get('/api/library'));checks.append(dict(operation='library',events=count))
        entry=next(e for e in active if e['symbol']=='ACB')
        replay=get('/api/dataset?dataset_id='+entry['dataset_id'])
        assert len(replay['frames'])==entry['observations']
        checks.append(dict(operation='primary_replay',observations=len(replay['frames'])))
        del replay
        assert get('/healthz')==health
        print(json.dumps(dict(state='MEMORY_SMOKE_ITERATION_PASSED',iteration=attempt+1)),flush=True)
    runs=get('/api/live/runs')
    if runs:
        run=runs[0]
        for symbol in run['symbols']:
            packet=get('/api/live/view?'+urlencode(dict(run_id=run['run_id'],symbol=symbol,start=0)))
            assert packet['total']==run['quote_counts'][symbol]
            checks.append(dict(operation='ssi_view',symbol=symbol,observations=len(packet['frames'])))
            del packet
    result=dict(state='REAL_HISTORY_HTTP_MEMORY_SMOKE_PASSED',application=health['application'],
                day=day,latest_history_day=latest,catalog_entries=len(entries),checks=checks,
                elapsed_seconds=round(time.monotonic()-started,2))
    print(json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',default='http://127.0.0.1:10000')
    parser.add_argument('--day',default='2026-11-23')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=check(args.origin.rstrip('/'),args.day,repeats=args.repeats)
    if args.output:args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
