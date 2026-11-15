"""Persistent review history for the hosted monitor (no local SQLite fallback)."""
from datetime import datetime,timezone
import json
import re


class PostgresReviews:
    def __init__(self,url):
        import psycopg
        from psycopg.rows import dict_row
        self.connect=lambda:psycopg.connect(url,connect_timeout=10,row_factory=dict_row)
        try:
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS monitor_reviews (id BIGSERIAL PRIMARY KEY,dataset_id TEXT NOT NULL,event_id TEXT NOT NULL,verdict TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS monitor_review_dataset ON monitor_reviews(dataset_id,id)')
                db.execute('CREATE TABLE IF NOT EXISTS monitor_analyses (dataset_id TEXT PRIMARY KEY,day TEXT NOT NULL,symbol TEXT NOT NULL,parameters JSONB NOT NULL,created_at TEXT NOT NULL)')
        except Exception:raise RuntimeError('PERSISTENT_REVIEWS_UNAVAILABLE') from None

    def query(self,sql,args=()):
        try:
            with self.connect() as db:return db.execute(sql,args).fetchall()
        except Exception:raise RuntimeError('PERSISTENT_REVIEWS_UNAVAILABLE') from None

    def all(self):return self.query('SELECT * FROM monitor_reviews ORDER BY id')
    def read(self,dataset_id):return self.query('SELECT * FROM monitor_reviews WHERE dataset_id=%s ORDER BY id',(dataset_id,))

    def analyses(self):return self.query('SELECT * FROM monitor_analyses ORDER BY created_at')

    def record_analysis(self,entry):
        try:
            with self.connect() as db:
                db.execute('INSERT INTO monitor_analyses(dataset_id,day,symbol,parameters,created_at) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT(dataset_id) DO NOTHING',
                    (entry['dataset_id'],entry['day'],entry['symbol'],json.dumps(entry['parameters']),datetime.now(timezone.utc).isoformat()))
        except Exception:raise RuntimeError('PERSISTENT_ANALYSES_UNAVAILABLE') from None

    def append(self,value):
        if (not isinstance(value,dict) or set(value)!={'dataset_id','event_id','verdict','note'} or
            any(not isinstance(value[k],str) or not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('dataset_id','event_id')) or
            value['verdict'] not in ('useful','not_useful','data_issue','uncertain') or not isinstance(value['note'],str) or len(value['note'])>2000):
            raise ValueError('INVALID_REVIEW')
        try:
            with self.connect() as db:
                db.execute('INSERT INTO monitor_reviews(dataset_id,event_id,verdict,note,created_at) VALUES(%s,%s,%s,%s,%s)',
                    tuple(value[k] for k in ('dataset_id','event_id','verdict','note'))+(datetime.now(timezone.utc).isoformat(),))
        except Exception:raise RuntimeError('PERSISTENT_REVIEWS_UNAVAILABLE') from None


class UnconfiguredReviews:
    def all(self):return []
    def read(self,dataset_id):return []
    def append(self,value):raise RuntimeError('PERSISTENT_REVIEWS_NOT_CONFIGURED')
    def analyses(self):return []
