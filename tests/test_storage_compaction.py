from contextlib import closing
from datetime import datetime
import json
import sqlite3
import zlib

import pytest
pytest.importorskip('pyarrow')

from scripts.normalize_dnse_history import connect_output
from scripts.pilot_compact_dnse import export_day,restore_day,SQL_COLUMNS


def prepare(root):
    path=root/'data/processed/dnse/normalized.db'
    with closing(connect_output(path)) as db:
        db.execute('INSERT INTO normalization_rules VALUES(?,?)',('rule','{}'))
        db.execute('INSERT INTO archives VALUES(?,?,?,?,?)',('archive','data/history/dnse/2026-11-11/history.db','hash',json.dumps({'date':'2026-11-11'}),3))
        db.execute('INSERT INTO raw_catalog VALUES(?,?,?,?,?)',('archive','ACB','HOSE','COMPLETE',3))
        db.execute('INSERT INTO processed_pages VALUES(?,?,?,?,?,?)',('archive',1,'hash',3,3,0))
        for rid,symbol in [(3,'ACB'),(9,'FPT')]:
            record=dict(event_id='event-'+symbol,local_date='2026-11-11',bid_price_1='21250.00',bid_vol_1=0,bid_price_2=None,bid_vol_2=None,
                        unit_verification_status='EMPIRICALLY_VERIFIED' if symbol=='ACB' else 'ASSUMED_SHARED_SCHEMA')
            raw=json.dumps(record,separators=(',',':')).encode()
            values=[rid,'event-'+symbol,symbol,'HOSE','2026-11-11T02:15:00.123000+00:00','2026-11-11','2026-11-12T10:00:00+00:00','VALID',record['unit_verification_status'],'DNSE_CONTINUOUS_AM',int(symbol=='ACB'),0,None,0,zlib.compress(raw,1)]
            db.execute('INSERT INTO snapshots('+','.join(SQL_COLUMNS)+',payload_zlib) VALUES('+','.join('?' for _ in values)+')',values)
        db.executemany('INSERT INTO lineage VALUES(?,?,?,?)',[('archive',1,0,3),('archive',1,1,3),('archive',1,2,9)])
        db.commit()
    return path


def test_parquet_roundtrip_keeps_exact_json_ids_null_zero_scope_and_duplicate_lineage(tmp_path):
    source=prepare(tmp_path);folder,manifest=export_day('2026-11-11',root=tmp_path)
    output=tmp_path/'drill/result.db';result=restore_day(folder,output)
    assert result['snapshots']==2 and result['lineage_rows']==3
    assert manifest['original_database_changed'] is False
    with closing(sqlite3.connect(source)) as a,closing(sqlite3.connect(output)) as b:
        for table in ('snapshots','lineage','normalization_rules','archives','raw_catalog','processed_pages','quarantine','manual_checks'):
            assert a.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall()==b.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall()
        raw=json.loads(zlib.decompress(b.execute('SELECT payload_zlib FROM snapshots WHERE id=3').fetchone()[0]))
        assert raw['bid_price_1']=='21250.00' and raw['bid_vol_1']==0 and raw['bid_vol_2'] is None
    with pytest.raises(ValueError,match='DESTINATION_EXISTS'):restore_day(folder,output)


def test_parquet_corruption_is_detected_before_restore(tmp_path):
    prepare(tmp_path);folder,_=export_day('2026-11-11',root=tmp_path)
    next(folder.glob('quotes-*.parquet')).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='CHECKSUM'):restore_day(folder,tmp_path/'bad/result.db')
