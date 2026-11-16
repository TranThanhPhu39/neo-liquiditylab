"""Password-protected hosted monitor; SSI secrets remain on the GitHub collector."""
import base64
from datetime import datetime,timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit,parse_qs
import zlib

from config.settings import ROOT
from scripts.serve_orderbook_monitor import MonitorServer,Handler,APPLICATION_VERSION,ASSETS
from src.data_pipeline.parquet_history import confined
from src.monitoring.web_relay import RelayLive,MAX_BODY
from src.monitoring.web_archive import CloudRecovery
from src.monitoring.web_reviews import PostgresReviews,UnconfiguredReviews


class CloudServer(MonitorServer):
    def __init__(self,root,*,port=10000,origin,read_password,ingest_token,database_url=None,bind_host='0.0.0.0'):
        parts=urlsplit(origin)
        if parts.scheme not in ('https','http') or not parts.netloc or parts.path not in ('','/') or parts.query or parts.fragment or parts.username:
            raise ValueError('MONITOR_PUBLIC_ORIGIN_INVALID')
        if parts.scheme=='http' and parts.hostname!='127.0.0.1':raise ValueError('MONITOR_HTTPS_REQUIRED')
        if len(read_password)<24 or len(ingest_token)<32:raise ValueError('MONITOR_STRONG_CREDENTIALS_REQUIRED')
        self.public_origin=origin.rstrip('/');self.read_password=read_password;self.ingest_token=ingest_token
        self.heavy=threading.BoundedSemaphore(1);self.recovery=None
        reviews=PostgresReviews(database_url) if database_url else UnconfiguredReviews()
        super().__init__(root,port,bind_host=bind_host,handler_class=CloudHandler,assets_root=ROOT,reviews=reviews)
        self.live=RelayLive(self.root)

    def index(self):
        if not (self.data/'index.json').exists():return []
        return super().index()

    def metadata(self,dataset_id):
        entry=next((e for e in self.index() if e['dataset_id']==dataset_id),None)
        if not entry:raise ValueError('UNKNOWN_DATASET')
        if entry.get('market_observation'):
            return self.market.metadata(entry)
        if entry.get('compact_folder'):
            self.dataset(dataset_id)  # Apply the existing confined-path checks.
            result=json.loads((confined(self.root,entry['compact_folder'])/'metadata.json').read_bytes())
        else:result=json.loads(self.dataset(dataset_id).read_bytes())
        if result['dataset_id']!=dataset_id:raise ValueError('DATASET_IDENTITY_MISMATCH')
        return result

    def summary(self,dataset_id):
        e=next((e for e in self.index() if e['dataset_id']==dataset_id),None)
        if e and e.get('market_observation'):return super().summary(dataset_id)
        if e and e.get('events_file'):return super().summary(dataset_id)
        m=self.metadata(dataset_id)
        if 'event_patches' in m:patches=m['event_patches']
        else:
            import pyarrow.parquet as pq
            folder=confined(self.root,e['compact_folder']);path=folder/'event-patches.parquet'
            source=pq.ParquetFile(path)
            if source.metadata.num_rows>200000 or path.stat().st_size>80_000_000:raise ValueError('INVALID_REPLAY_ROWS')
            patches=(json.loads(r['patch_json']) for batch in source.iter_batches(batch_size=256,columns=['patch_json']) for r in batch.to_pylist())
        events={p['event']['id']:p['event'] for p in patches}
        return dict(dataset_id=dataset_id,symbol=m['symbol'],day=m['day'],events=list(events.values()),quality=m.get('quality_summary',{}))

    def analyze(self,day,symbol,config,event_config):
        if isinstance(self.reviews,UnconfiguredReviews):raise RuntimeError('PERSISTENT_ANALYSES_NOT_CONFIGURED')
        result=super().analyze(day,symbol,config,event_config);self.reviews.record_analysis(result['dataset']);return result

    def restore_analyses(self):
        from src.monitoring.events import configs_from_parameters
        revisions={r['old_dataset_id']:r['new_dataset_id'] for r in self.reviews.analysis_revisions()}
        manifest_path=self.root/'data/processed/dnse/manifest.json'
        unit_revision=manifest_path.is_file() and json.loads(manifest_path.read_bytes()).get('state')=='UNIT_REVISION_AUDITED'
        for row in self.reviews.analyses():
            known={e['dataset_id'] for e in self.index()}
            if row['dataset_id'] in known or revisions.get(row['dataset_id']) in known:continue
            cfg,events=configs_from_parameters(row['parameters'])
            result=super().analyze(row['day'],row['symbol'],cfg,events)
            if result['dataset']['dataset_id']!=row['dataset_id']:
                if not unit_revision:raise ValueError('CLOUD_ANALYSIS_IDENTITY_CHANGED')
                self.reviews.record_analysis(result['dataset'])
                self.reviews.record_analysis_revision(row['dataset_id'],result['dataset']['dataset_id'])
                print(json.dumps(dict(state='ANALYSIS_RECALCULATED_AFTER_VERIFIED_UNIT_REVISION',
                    old_dataset_id=row['dataset_id'],new_dataset_id=result['dataset']['dataset_id'],reviews_remapped=False)),flush=True)

    def server_close(self):
        if self.recovery:self.recovery.close()
        super().server_close()


class CloudHandler(Handler):
    def valid_host(self):return self.headers.get('Host')==urlsplit(self.server.public_origin).netloc
    def expected_origin(self):return self.server.public_origin

    def authenticated(self):
        expected='Basic '+base64.b64encode(('liquiditylab:'+self.server.read_password).encode()).decode()
        actual=self.headers.get('Authorization','')
        if len(actual)<=1024 and actual.isascii() and secrets.compare_digest(actual,expected):return True
        self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="LiquidityLab", charset="UTF-8"')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.end_headers();return False

    def do_GET(self):
        route=urlsplit(self.path)
        if route.path=='/healthz':return self.send_json(dict(status='ok',application=APPLICATION_VERSION))
        if not self.valid_host():return self.send_json({'error':'INVALID_HOST'},403)
        if route.path in ('/','/about'):
            return self.send_bytes((self.server.assets/'landing.html').read_bytes(),'text/html')
        if route.path=='/intro.css':
            return self.send_bytes((self.server.assets/'intro.css').read_bytes(),'text/css')
        if route.path=='/robots.txt':
            return self.send_bytes(b'User-agent: *\nAllow: /$\nDisallow: /dashboard\nDisallow: /index.html\nDisallow: /realtime\nDisallow: /api/\n','text/plain')
        if not self.authenticated():return
        if route.path=='/api/hosting':
            r=self.server.recovery
            entries=self.server.index();latest=max((e['day'] for e in entries),default=None)
            current=[e for e in entries if e['day']==latest and not e.get('archived')]
            return self.send_json(dict(hosted=True,collection_control='SSI_GITHUB_AND_WINDOWS_DNSE_DAILY',
                latest_history_day=latest,market_scope='ALL_CATALOG_SYMBOLS' if any(e.get('market_observation') for e in entries) else 'THREE_VERIFIED_SYMBOLS',
                latest_catalog_symbols=len(current),latest_symbols_with_observations=sum(bool(e.get('observations')) for e in current),
                recovery_state=r.state if r else 'NOT_CONFIGURED',seed_ready=r.seed_ready if r else bool(self.server.index()),
                persistent_reviews=not isinstance(self.server.reviews,UnconfiguredReviews)))
        if route.path=='/api/review-history':
            try:return self.send_json(dict(reviews=self.server.reviews.all(),analyses=self.server.reviews.analyses(),analysis_revisions=self.server.reviews.analysis_revisions()))
            except RuntimeError:return self.send_json({'error':'PERSISTENT_STORAGE_UNAVAILABLE'},503)
        if route.path=='/api/dataset':
            if not self.server.heavy.acquire(blocking=False):return self.send_json({'error':'SERVER_BUSY_RETRY'},503)
            try:return self.stream_dataset(parse_qs(route.query).get('dataset_id',[''])[0])
            except (ValueError,KeyError,OSError):return self.send_json({'error':'DATA_NOT_READY_OR_INVALID'},404)
            finally:self.server.heavy.release()
        # Serialize expensive history/analysis requests on small free instances.
        heavy=route.path in ('/api/watchlist','/api/library','/api/export','/api/trades')
        if heavy and not self.server.heavy.acquire(blocking=False):return self.send_json({'error':'SERVER_BUSY_RETRY'},503)
        try:return super().do_GET()
        except RuntimeError:return self.send_json({'error':'PERSISTENT_STORAGE_UNAVAILABLE'},503)
        finally:
            if heavy:self.server.heavy.release()

    def stream_dataset(self,dataset_id):
        import pyarrow.parquet as pq
        entry=next((e for e in self.server.index() if e['dataset_id']==dataset_id),None)
        if not entry:raise ValueError('UNKNOWN_DATASET')
        self.server.dataset(dataset_id)
        if entry.get('market_observation'):
            if not entry['observations']:raise ValueError('MARKET_NO_SOURCE_OBSERVATIONS')
            frames=iter(self.server.market.frames(entry));first=next(frames)
            meta=self.server.market.metadata(entry)
            self.send_response(200)
            for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Encoding':'gzip','Connection':'close','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}.items():self.send_header(k,v)
            self.end_headers();self.close_connection=True
            with gzip.GzipFile(fileobj=self.wfile,mode='wb',compresslevel=1) as output:
                output.write(json.dumps(meta,ensure_ascii=False,separators=(',',':')).encode()[:-1]+b',"frames":[')
                output.write(json.dumps(first,ensure_ascii=False,separators=(',',':')).encode())
                for frame in frames:output.write(b','+json.dumps(frame,ensure_ascii=False,separators=(',',':')).encode())
                output.write(b']}')
            return
        if not entry.get('compact_folder'):
            raw=self.server.dataset(dataset_id).read_bytes()
            value=gzip.compress(raw,compresslevel=1)
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Encoding','gzip')
            self.send_header('Content-Length',str(len(value)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(value)
            return
        folder=confined(self.server.root,entry['compact_folder']);meta=self.server.metadata(dataset_id)
        parquet=pq.ParquetFile(folder/'frames.parquet')
        manifest=json.loads((folder/'manifest.json').read_bytes())
        if parquet.metadata.num_rows!=manifest['frames'] or parquet.metadata.num_rows>50000:raise ValueError('INVALID_REPLAY_ROWS')
        patches=None
        if manifest.get('event_patches_in_parquet'):
            path=folder/'event-patches.parquet';patches=pq.ParquetFile(path)
            if (patches.metadata.num_rows!=manifest['event_patch_rows'] or patches.metadata.num_rows>200000 or
                path.stat().st_size>80_000_000):raise ValueError('INVALID_REPLAY_ROWS')
        self.send_response(200)
        for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Encoding':'gzip','Connection':'close',
                    'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}.items():self.send_header(k,v)
        self.end_headers();self.close_connection=True
        with gzip.GzipFile(fileobj=self.wfile,mode='wb',compresslevel=1) as output:
            output.write(json.dumps(meta,ensure_ascii=False,separators=(',',':')).encode()[:-1]+b',"frames":[')
            first=True
            for batch in parquet.iter_batches(batch_size=256,columns=['frame_json']):
                for row in batch.to_pylist():
                    if not first:output.write(b',')
                    output.write(row['frame_json']);first=False
            output.write(b']')
            if patches is not None:
                output.write(b',"event_patches":[');first=True
                for batch in patches.iter_batches(batch_size=256,columns=['patch_json']):
                    for row in batch.to_pylist():
                        if not first:output.write(b',')
                        output.write(row['patch_json']);first=False
                output.write(b']')
            output.write(b'}')

    def do_POST(self):
        if not self.valid_host():return self.send_json({'error':'INVALID_HOST'},403)
        if self.path=='/api/relay':
            expected='Bearer '+self.server.ingest_token;actual=self.headers.get('Authorization','')
            if len(actual)>1024 or not actual.isascii() or not secrets.compare_digest(actual,expected):return self.send_json({'error':'UNAUTHORIZED'},401)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=MAX_BODY or self.headers.get_content_type()!='application/json':raise ValueError('INVALID_BODY')
                raw=self.rfile.read(size)
                if self.headers.get('Content-Encoding')=='gzip':
                    decompressor=zlib.decompressobj(16+zlib.MAX_WBITS);raw=decompressor.decompress(raw,MAX_BODY+1)
                    if not decompressor.eof or decompressor.unused_data or len(raw)>MAX_BODY:raise ValueError('INVALID_GZIP')
                elif self.headers.get('Content-Encoding'):raise ValueError('INVALID_ENCODING')
                result=self.server.live.apply(json.loads(raw));return self.send_json(result)
            except (ValueError,KeyError,TypeError,OSError,EOFError,zlib.error):return self.send_json({'error':'RELAY_PACKET_REJECTED'},400)
        if not self.authenticated():return
        if self.path in ('/api/live/start','/api/live/stop'):
            return self.send_json({'error':'COLLECTION_MANAGED_BY_GITHUB_SCHEDULE'},403)
        if self.path in ('/api/reviews','/api/live/reviews','/api/analyze') and isinstance(self.server.reviews,UnconfiguredReviews):
            return self.send_json({'error':'PERSISTENT_REVIEWS_NOT_CONFIGURED'},503)
        if self.path=='/api/analyze' and not self.server.heavy.acquire(blocking=False):return self.send_json({'error':'SERVER_BUSY_RETRY'},503)
        try:return super().do_POST()
        finally:
            if self.path=='/api/analyze':self.server.heavy.release()


def load_secret_file(secret_file):
    from dotenv import dotenv_values
    path=Path(secret_file)
    if not path.is_file() or path.stat().st_size>16384:raise ValueError('MONITOR_CONFIG_FILE_INVALID')
    values=dotenv_values(path,interpolate=False);loaded=[]
    for name in ('MONITOR_WEB_PASSWORD','MONITOR_INGEST_TOKEN','MONITOR_DATABASE_URL','LIQUIDITYLAB_STATE_KEY','MONITOR_SEED_TAG'):
        if not os.getenv(name) and values.get(name):os.environ[name]=values[name];loaded.append(name)
    return loaded


def main():
    if os.getenv('MONITOR_CONFIG_FILE'):load_secret_file(os.environ['MONITOR_CONFIG_FILE'])
    root=Path(os.getenv('MONITOR_DATA_ROOT','/tmp/liquiditylab')).resolve();root.mkdir(parents=True,exist_ok=True)
    origin=os.getenv('MONITOR_PUBLIC_ORIGIN') or os.getenv('RENDER_EXTERNAL_URL','')
    server=CloudServer(root,port=int(os.getenv('PORT','10000')),origin=origin,
        read_password=os.environ['MONITOR_WEB_PASSWORD'],ingest_token=os.environ['MONITOR_INGEST_TOKEN'],
        database_url=os.getenv('MONITOR_DATABASE_URL'))
    encoded=os.getenv('LIQUIDITYLAB_STATE_KEY')
    if encoded:
        key=base64.b64decode(encoded,validate=True)
        if len(key)!=32:raise ValueError('INVALID_RECOVERY_KEY')
        server.recovery=CloudRecovery(root,key,os.getenv('MONITOR_SEED_TAG'),live=server.live,on_seed=server.restore_analyses,
            preferred_tags=json.loads(os.getenv('SSI_RECOVERY_TAGS_JSON','[]')));server.recovery.start()
    print(json.dumps(dict(state='CLOUD_MONITOR_READY',origin=origin,ssi_keys_on_host=False)),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
