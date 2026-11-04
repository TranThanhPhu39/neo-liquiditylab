from datetime import datetime
import gzip
import hashlib
import json

import pytest

from test_dnse_history import history
from test_dnse_normalization import quote
from scripts.export_web_data import export_data, public_record, referenced_files
from scripts.github_pages import public_data_files


def test_public_export_is_bounded_and_keeps_unknown_distinct_from_zero(history,tmp_path):
    root=tmp_path/'publication'
    out=root/'web/data'
    before=hashlib.sha256(history.path.read_bytes()).hexdigest()
    result=export_data(history,out,'fixture-rule')
    assert result['latest_day']=='2026-10-28' and result['partitions']==17
    index=json.loads((out/'index.json').read_text())
    market=json.loads(gzip.decompress((out/index['days'][0]['market']['path']).read_bytes()))
    assert set(market['catalog'])=={'ACB','FPT','EMG'}
    assert {r['symbol'] for r in market['records']}=={'ACB','FPT'}
    assert all(datetime.fromisoformat(r['source_timestamp'])<=datetime.fromisoformat(market['cutoff']) for r in market['records'])
    assert next(r for r in market['records'] if r['symbol']=='FPT')['unit_verification_status']=='ASSUMED_SHARED_SCHEMA'
    clean=public_record({**market['records'][0],'account':'private','raw':{'token':'private'},'api_key':'private'})
    assert 'account' not in clean and 'raw' not in clean and 'api_key' not in clean
    assert hashlib.sha256(history.path.read_bytes()).hexdigest()==before
    assert len(public_data_files(root))==18


def test_incremental_export_reuses_immutable_partitions_and_rejects_tampering(history,tmp_path):
    root=tmp_path/'publication'
    out=root/'web/data'
    export_data(history,out,'fixture-rule')
    index=json.loads((out/'index.json').read_text())
    hashes={r['path']:r['sha256'] for r in referenced_files(index)}
    export_data(history,out,'fixture-rule')
    again=json.loads((out/'index.json').read_text())
    assert hashes=={r['path']:r['sha256'] for r in referenced_files(again)}
    target=out/index['days'][0]['market']['path']
    target.write_bytes(target.read_bytes()+b'bad')
    with pytest.raises(ValueError,match='CHECKSUM'):
        public_data_files(root)


def test_public_manifest_cannot_publish_arbitrary_files(history,tmp_path):
    out=tmp_path/'web/data'
    export_data(history,out,'fixture-rule')
    index=json.loads((out/'index.json').read_text())
    index['days'][0]['market']['path']='../../.env'
    with pytest.raises(ValueError,match='UNSAFE'):
        referenced_files(index)
