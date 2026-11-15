"""Loopback-only event replay app. Serves an asset allowlist, never the workspace."""
import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from config.settings import ROOT
from src.monitoring.spread import features, stamp
from src.data_pipeline.dnse_history import DNSEHistory, day_bounds, utc_text
from src.data_pipeline.parquet_history import ParquetHistory, confined
from src.data_pipeline.parquet_replay import ReplayDocument
from src.monitoring.ssi_live import SSILive
from scripts.build_orderbook_monitor import build, serial
from src.monitoring.events import configs_from_parameters, VERSION
import zlib

ASSETS = {'/': ('index.html', 'text/html'), '/dashboard': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/style.css': ('style.css', 'text/css'), '/app.js': ('app.js', 'text/javascript'),
          '/model.js': ('model.js', 'text/javascript'), '/heatmap.js': ('heatmap.js', 'text/javascript')}
ASSETS.update({'/realtime':('realtime.html','text/html'),'/realtime/':('realtime.html','text/html'),'/live.js':('live.js','text/javascript'),'/live.css':('live.css','text/css'),'/live-model.js':('live-model.js','text/javascript')})
VERDICTS = {'useful', 'not_useful', 'data_issue', 'uncertain'}
APPLICATION_VERSION='offline-monitor-v4-parquet'


class ReviewStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS reviews(
                id INTEGER PRIMARY KEY,dataset_id TEXT NOT NULL,event_id TEXT NOT NULL,
                verdict TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)''')
            db.commit()

    def read(self, dataset_id):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM reviews WHERE dataset_id=? ORDER BY id', (dataset_id,))]

    def all(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM reviews ORDER BY id')]

    def append(self, value):
        if (not isinstance(value, dict) or set(value) != {'dataset_id', 'event_id', 'verdict', 'note'}
                or any(not isinstance(value[k], str) or not re.fullmatch('[0-9a-f]{64}', value[k])
                       for k in ('dataset_id', 'event_id'))
                or not isinstance(value['verdict'], str) or value['verdict'] not in VERDICTS or not isinstance(value['note'], str)
                or len(value['note']) > 2000):
            raise ValueError('INVALID_REVIEW')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('INSERT INTO reviews(dataset_id,event_id,verdict,note,created_at) VALUES(?,?,?,?,?)',
                       (value['dataset_id'], value['event_id'], value['verdict'], value['note'],
                        datetime.now(timezone.utc).isoformat()))
            db.commit()


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root=ROOT, port=8877, *, bind_host='127.0.0.1', handler_class=None, assets_root=None, reviews=None):
        self.root = Path(root).resolve()
        self.data = self.root / 'data/exports/orderbook-monitor'
        self.assets = Path(assets_root or self.root) / 'web/monitor'
        self.reviews = reviews if reviews is not None else ReviewStore(self.root / 'data/control/orderbook-monitor-reviews.db')
        self.csrf = secrets.token_urlsafe(32)
        self.live = SSILive(self.root)
        super().__init__((bind_host, port), handler_class or Handler)

    def server_close(self):
        self.live.close()
        super().server_close()

    def index(self):
        data = json.loads((self.data / 'index.json').read_text(encoding='utf-8'))
        entries = data.get('datasets', [])
        if not isinstance(entries, list) or len(entries) > 500:
            raise ValueError('INVALID_DATASET_INDEX')
        for e in entries:
            if (e['symbol'] not in ('ACB','FPT','HPG') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',e['day'])
                    or not re.fullmatch(r'dnse-(acb|fpt|hpg)-\d{4}-\d{2}-\d{2}(?:-[0-9a-f]{12})?\.json', e['file'])):
                raise ValueError('INVALID_DATASET_FILENAME')
        return entries

    def dataset(self, dataset_id):
        entry = next((e for e in self.index() if e['dataset_id'] == dataset_id), None)
        if not entry:
            raise ValueError('UNKNOWN_DATASET')
        if entry.get('compact_folder'):
            folder=confined(self.root,entry['compact_folder'])
            if not folder.is_relative_to(self.root/'data/compact/monitor'):
                raise ValueError('INVALID_DATASET_FILE')
            return ReplayDocument(self.root,entry['compact_folder'])
        path = self.data / entry['file']
        if path.is_symlink() or path.stat().st_size > 80_000_000:
            raise ValueError('INVALID_DATASET_FILE')
        return path

    def summary(self,dataset_id):
        entry=next((e for e in self.index() if e['dataset_id']==dataset_id),None)
        if not entry:raise ValueError('UNKNOWN_DATASET')
        if entry.get('events_file'):
            name=entry['events_file']
            if not re.fullmatch(r'dnse-(acb|fpt|hpg)-\d{4}-\d{2}-\d{2}-[0-9a-f]{12}\.events\.json',name):
                raise ValueError('INVALID_EVENTS_FILE')
            path=self.data/name
            if path.is_symlink() or path.stat().st_size>20_000_000:raise ValueError('INVALID_EVENTS_FILE')
            result=json.loads(path.read_text(encoding='utf-8'))
            if result['dataset_id']!=dataset_id:raise ValueError('EVENTS_IDENTITY_MISMATCH')
            return result
        old=json.loads(self.dataset(dataset_id).read_text(encoding='utf-8'))
        if old['dataset_id']!=dataset_id:raise ValueError('DATASET_IDENTITY_MISMATCH')
        events={p['event']['id']:p['event'] for p in old['event_patches']}
        return dict(dataset_id=dataset_id,symbol=old['symbol'],day=old['day'],events=list(events.values()),quality={})

    def trades(self,dataset_id):
        entry=next((e for e in self.index() if e['dataset_id']==dataset_id),None)
        if not entry:raise ValueError('UNKNOWN_DATASET')
        store=DNSEHistory(self.root/'data/processed/dnse/normalized.db')
        if isinstance(store,ParquetHistory):
            result=store.trades(entry['symbol'],entry['day'])
            if result is not None:return result
        path=self.root/'data/exports/orderbook-trades'/f'dnse-{entry["symbol"].lower()}-{entry["day"]}.json'
        if not path.exists():return dict(state='NOT_COLLECTED',symbol=entry['symbol'],day=entry['day'],trades=[])
        if path.is_symlink() or path.stat().st_size>80_000_000:raise ValueError('INVALID_TRADE_FILE')
        result=json.loads(path.read_text(encoding='utf-8'))
        if result.get('symbol')!=entry['symbol'] or result.get('day')!=entry['day'] or result.get('format')!='liquiditylab-market-trades-v1':
            raise ValueError('TRADE_IDENTITY_MISMATCH')
        return result

    def library(self):
        notes=self.reviews.all();latest={(r['dataset_id'],r['event_id']):r for r in notes}
        rows=[]
        for entry in self.index():
            for e in self.summary(entry['dataset_id'])['events']:
                review=latest.get((entry['dataset_id'],e['id']))
                rows.append(dict(dataset_id=entry['dataset_id'],day=entry['day'],symbol=entry['symbol'],
                    archived=entry.get('archived',False),event=e,review=review,
                    reviews=sum(r['dataset_id']==entry['dataset_id'] and r['event_id']==e['id'] for r in notes)))
        return sorted(rows,key=lambda r:r['event']['detected_at'],reverse=True)

    def watchlist(self,day,cutoff):
        entries=[e for e in self.index() if e['day']==day and not e.get('archived')]
        if not entries:raise ValueError('UNKNOWN_DAY')
        start,end=day_bounds(day);point=stamp(cutoff)
        if not start<=point<end:raise ValueError('INVALID_CUTOFF')
        result=[]
        store=DNSEHistory(self.root/'data/processed/dnse/normalized.db')
        records=store.reconstruct_at(('ACB','FPT','HPG'),day,point)
        for symbol in ('ACB','FPT','HPG'):
            entry=next((e for e in entries if e['symbol']==symbol),None)
            r=records.get(symbol)
            if not r:
                result.append(dict(symbol=symbol,status='NO_DATA',dataset_id=entry['dataset_id'] if entry else None));continue
            f=serial(features(r));age=(point-stamp(r['source_timestamp'])).total_seconds()
            result.append(dict(symbol=symbol,dataset_id=entry['dataset_id'] if entry else None,
                status='STALE_AT_CUTOFF' if age>60 else ('VERIFIED' if f['eligible'] else f['reason']),
                time=r['source_timestamp'],age_seconds=age,units=r['unit_verification_status'],
                bid=r.get('bid_price_1'),ask=r.get('ask_price_1'),features=f))
        return result

    def analyze(self,day,symbol,config,event_config):
        return build(day,symbol=symbol,root=self.root,config=config,event_config=event_config)


def report_payload(server,dataset_id,event_id=None):
    summary=server.summary(dataset_id)
    events=summary['events']
    if event_id:
        events=[e for e in events if e['id']==event_id]
        if not events:raise ValueError('UNKNOWN_EVENT')
    ids={e['id'] for e in events}
    pack=json.loads(server.dataset(dataset_id).read_text(encoding='utf-8'))
    if pack['dataset_id']!=dataset_id:raise ValueError('DATASET_IDENTITY_MISMATCH')
    provenance={k:pack.get(k) for k in ('provider','exchange','board','levels','price_unit','volume_unit',
        'clock_policy','sampling','unit_rule_hash','detector_version','config_hash','detector_configs',
        'config','analysis_parameters','source_archive','audit_checked_at','continuity_policy')}
    return dict(scope='FULL_SESSION_RETROSPECTIVE_REVIEW',configuration_status='EXPERIMENTAL_UNCALIBRATED',
                dataset_id=dataset_id,symbol=summary['symbol'],day=summary['day'],quality=summary['quality'],
                provenance=provenance,events=events,reviews=[r for r in server.reviews.read(dataset_id) if r['event_id'] in ids])


def report_csv(report):
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['symbol','day','event_id','type','side','detected_at','state','verdict','note'])
    notes={r['event_id']:r for r in report['reviews']}
    def safe(value):
        value=str(value or '')
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for e in report['events']:
        r=notes.get(e['id'],{})
        writer.writerow([safe(v) for v in [report['symbol'],report['day'],e['id'],e['type'],e.get('side'),
            e['detected_at'],e['state'],r.get('verdict'),r.get('note')]])
    return ('\ufeff'+out.getvalue()).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log query strings, submitted notes, or request headers.

    def send_bytes(self, value, content_type='application/json', status=200, filename=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(value)

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode('utf-8'), status=status)

    def valid_host(self):
        return self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port)

    def expected_origin(self):
        return 'http://127.0.0.1:' + str(self.server.server_port)

    def do_GET(self):
        if not self.valid_host():
            return self.send_json({'error': 'INVALID_HOST'}, 403)
        route = urlsplit(self.path)
        try:
            if route.path in ASSETS:
                name, content_type = ASSETS[route.path]
                return self.send_bytes((self.server.assets / name).read_bytes(), content_type)
            if route.path == '/api/datasets':
                return self.send_json(dict(datasets=self.server.index(), csrf=self.server.csrf,application_version=APPLICATION_VERSION))
            params = parse_qs(route.query)
            if route.path=='/api/live/status':return self.send_json(dict(self.server.live.status(),csrf=self.server.csrf))
            if route.path=='/api/live/runs':return self.send_json(self.server.live.runs())
            if route.path=='/api/live/view':
                return self.send_json(self.server.live.view(params.get('run_id',[''])[0],params.get('symbol',['ACB'])[0],
                    int(params.get('after',['-1'])[0]),int(params['start'][0]) if 'start' in params else None,int(params.get('after_trade',['-1'])[0])))
            if route.path=='/api/live/events':
                return self.send_json(self.server.live.events(params.get('run_id',[''])[0],params.get('symbol',['ACB'])[0],params.get('at',[''])[0]))
            if route.path in ('/api/live/reviews','/api/live/export'):
                run=params.get('run_id',[''])[0];symbol=params.get('symbol',['ACB'])[0]
                cutoff=params.get('at',[datetime.now(timezone.utc).isoformat()])[0]
                events=self.server.live.events(run,symbol,cutoff)
                notes=self.server.reviews.read(self.server.live.dataset_id(run,symbol))
                if route.path.endswith('/reviews'):return self.send_json(notes)
                return self.send_bytes(json.dumps(dict(provider='ssi',run_id=run,symbol=symbol,events=events,reviews=notes,
                    cutoff=cutoff,scope='UNTIL_RECEIVE_CUTOFF',clock_policy='RECEIVE_ORDER_WITH_ORIGINAL_SOURCE_CLOCK',configuration_status='EXPERIMENTAL_UNCALIBRATED'),ensure_ascii=False).encode(),filename='ssi-events.json')
            dataset_id = params.get('dataset_id', [''])[0]
            if route.path == '/api/dataset':
                raw=self.server.dataset(dataset_id).read_bytes()
                if json.loads(raw)['dataset_id']!=dataset_id:raise ValueError('DATASET_IDENTITY_MISMATCH')
                return self.send_bytes(raw)
            if route.path == '/api/reviews':
                self.server.dataset(dataset_id)
                return self.send_json(self.server.reviews.read(dataset_id))
            if route.path=='/api/trades':return self.send_json(self.server.trades(dataset_id))
            if route.path=='/api/library':return self.send_json(self.server.library())
            if route.path=='/api/watchlist':
                return self.send_json(self.server.watchlist(params.get('day',[''])[0],params.get('cutoff',[''])[0]))
            if route.path=='/api/export':
                report=report_payload(self.server,dataset_id,params.get('event_id',[None])[0])
                if params.get('format',['json'])[0]=='csv':
                    return self.send_bytes(report_csv(report),'text/csv',filename='liquidity-events.csv')
                return self.send_bytes(json.dumps(report,ensure_ascii=False).encode('utf-8'),filename='liquidity-events.json')
            self.send_json({'error': 'NOT_FOUND'}, 404)
        except (ValueError, KeyError, TypeError, OSError,sqlite3.Error):
            self.send_json({'error': 'DATA_NOT_READY_OR_INVALID'}, 404)

    def do_POST(self):
        origin = self.expected_origin()
        if (not self.valid_host() or self.headers.get('Origin') != origin
                or not secrets.compare_digest(self.headers.get('X-Monitor-CSRF', ''), self.server.csrf)):
            return self.send_json({'error': 'INVALID_LOCAL_ORIGIN'}, 403)
        if self.path not in ('/api/reviews','/api/analyze','/api/live/start','/api/live/stop','/api/live/reviews'):
            return self.send_json({'error': 'NOT_FOUND'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16000 or self.headers.get_content_type() != 'application/json':
                raise ValueError('INVALID_BODY')
            value = json.loads(self.rfile.read(length))
            if self.path=='/api/live/start':
                if not isinstance(value,dict) or set(value)!={'symbols','seconds'}:raise ValueError('INVALID_LIVE_START')
                return self.send_json(self.server.live.start(value['symbols'],value['seconds']))
            if self.path=='/api/live/stop':
                if value!={}:raise ValueError('INVALID_LIVE_STOP')
                return self.send_json(self.server.live.stop())
            if self.path=='/api/live/reviews':
                if not isinstance(value,dict) or set(value)!={'run_id','symbol','event_id','verdict','note'}:raise ValueError('INVALID_SSI_REVIEW')
                events=self.server.live.events(value['run_id'],value['symbol'],datetime.now(timezone.utc).isoformat())
                if value['event_id'] not in {e['id'] for e in events}:raise ValueError('UNKNOWN_SSI_EVENT')
                self.server.reviews.append(dict(dataset_id=self.server.live.dataset_id(value['run_id'],value['symbol']),
                    event_id=value['event_id'],verdict=value['verdict'],note=value['note']))
                return self.send_json(dict(state='SAVED'))
            if self.path=='/api/analyze':
                if not isinstance(value,dict) or set(value)!={'day','symbol','parameters'}:raise ValueError('INVALID_ANALYSIS')
                if not any(e['day']==value['day'] and e['symbol']==value['symbol'] for e in self.server.index()):raise ValueError('UNKNOWN_DATASET')
                cfg,events=configs_from_parameters(value['parameters'])
                result=self.server.analyze(value['day'],value['symbol'],cfg,events)
                return self.send_json(result['dataset'])
            summary=self.server.summary(value['dataset_id'])
            if value['event_id'] not in {e['id'] for e in summary['events']}:
                raise ValueError('UNKNOWN_EVENT')
            self.server.reviews.append(value)
            self.send_json({'state': 'SAVED'})
        except (ValueError, KeyError, TypeError, OSError,RuntimeError,sqlite3.Error):
            self.send_json({'error': 'INVALID_ANALYSIS' if self.path=='/api/analyze' else 'INVALID_REVIEW'}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    args = parser.parse_args()
    server = MonitorServer(port=args.port)
    print(f'Local event replay: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
