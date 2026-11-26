from datetime import datetime
import json

import pytest

from config.settings import ROOT
from scripts import audit_daily_delivery as audit
from src.data_pipeline.ssi_live_store import LiveJournal


DAY = '2026-11-25'


def test_dnse_rejects_wrong_counts_and_missing_data_relabeling():
    expected = dict(ACB=dict(observations=1,availability='AVAILABLE'),
                    EMG=dict(observations=0,availability='NO_DATA_RETURNED'))
    rows = [dict(item,symbol=symbol,day=DAY,dataset_id=symbol) for symbol,item in expected.items()]

    def request(path):
        if path == '/api/hosting':
            return dict(seed_ready=True)
        if path == '/api/datasets':
            return dict(datasets=rows)
        return dict(dataset_id='ACB',frames=[{}])

    assert audit.verify_dnse_web(DAY,expected,request)['state']=='VERIFIED'
    rows[1]['availability']='AVAILABLE'
    assert audit.verify_dnse_web(DAY,expected,request)['state']=='WEB_OBSERVATIONS_MISMATCH'
    rows[1]['availability']='NO_DATA_RETURNED'
    rows[0]['observations']=0
    assert audit.verify_dnse_web(DAY,expected,request)['state']=='WEB_OBSERVATIONS_MISMATCH'


def test_ssi_delivery_can_succeed_with_partial_market_coverage(tmp_path):
    folder=tmp_path/DAY/('a'*32)
    journal=LiveJournal(tmp_path,list(audit.SYMBOLS),folder=folder)
    for symbol in audit.SYMBOLS:
        journal.append(dict(kind='QUOTE',symbol=symbol,received_at=DAY+'T13:00:00+07:00',
                            quote_index=0,frame=dict(index=0,bids=[],asks=[],units='EMPIRICALLY_VERIFIED')))
        journal.append(dict(kind='TRADE',symbol=symbol,received_at=DAY+'T13:00:00+07:00',
                            trade=dict(qty=100,source_time=DAY+'T13:00:00+07:00')))
    journal.close()
    manifest=journal.manifest
    status=dict(state='STOPPED',active=False,reason='DURATION_LIMIT',quotes=3,trades=3,
                quote_counts=manifest['quote_counts'],rejected=0,gaps=1,first_connected_at=DAY+'T12:45:00+07:00')
    files={f'run/{p["path"]}':(folder/p['path']).read_bytes() for p in manifest['files']}
    files.update({'status.json':json.dumps(status),'run/manifest.json':json.dumps(manifest)})
    proof=dict(records=6,state='DOWNLOADED_DECRYPTED_PARQUET_VERIFIED',run_id=journal.run_id,
               quote_counts=manifest['quote_counts'],trade_counts=manifest['trade_counts'])
    runs=[dict(proof,day=DAY)]
    release=dict(tag_name='ssi-ws-fixture',name='Encrypted SSI session fixture')

    def request(path):
        from urllib.parse import parse_qs,urlsplit
        symbol=parse_qs(urlsplit(path).query)['symbol'][0]
        return dict(day=DAY,run_id=journal.run_id,symbol=symbol,total=1,trade_total=1,frames=[dict(index=0)])

    result=audit.ssi_capture(DAY,'afternoon',release,lambda tag:(files,proof),runs,request,audit.policy(ROOT))
    assert result['processing_state']=='VERIFIED'
    assert result['market_coverage']=='PARTIAL_CONNECTION_GAPS'
    assert result['provider_completeness']=='UNKNOWN_NO_PROVIDER_SEQUENCE'

    runs[0]['quote_counts']=dict(ACB=1,FPT=1,HPG=0)
    result=audit.ssi_capture(DAY,'afternoon',release,lambda tag:(files,proof),runs,request,audit.policy(ROOT))
    assert result['web']['state']=='WEB_CAPTURE_COUNTS_MISMATCH'
    assert result['processing_state']=='PENDING_OR_NO_USABLE_REPLAY'


def test_closed_morning_without_checkpoint_is_not_success():
    class API:
        def request(self,*args):
            return []
    def unused(*args):
        pytest.fail('No checkpoint to open')
    result=audit.ssi_session(DAY,'morning',API(),unused,[],unused,audit.policy(ROOT),
                             {'morning':[dict(created_after_close=True,conclusion='failure')]})
    assert result['state']=='NO_CAPTURE_GITHUB_RUN_CREATED_AFTER_CLOSE'
    assert result['captures']==[]
