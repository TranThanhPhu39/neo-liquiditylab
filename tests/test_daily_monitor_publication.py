import json
from pathlib import Path
import pytest
from scripts import publish_daily_monitor as p


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/monitor_publication.json').write_text(json.dumps(dict(enabled=True,symbols=p.SYMBOLS,verify_timeout_seconds=0)))
    monkeypatch.setattr(p,'fingerprint',lambda root:('a'*64,'2026-11-16'))
    calls=[]
    def prepare(root):
        calls.append('prepare');stage=root/'data/control/verified-monitor-history'/('b'*32);stage.mkdir(parents=True)
        return dict(stage=stage.relative_to(root).as_posix())
    def package(stage,report):
        calls.append('package');report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(dict(latest_day='2026-11-16',folder=str(stage)+'-sealed',tag='fixture',uploaded=False)))
    def publish(report):
        calls.append('publish');v=json.loads(report.read_bytes());v['remote_recovery_verified']=True;report.write_text(json.dumps(v))
    monkeypatch.setattr(p,'prepare',prepare);monkeypatch.setattr(p,'package',package);monkeypatch.setattr(p,'publish',publish)
    monkeypatch.setattr(p,'deploy',lambda report:calls.append('deploy') or dict(deploy={'id':'fixture'}))
    monkeypatch.setattr(p,'verify',lambda day:True)
    return tmp_path,calls,publish


def test_publication_is_idempotent_and_resumes_after_upload_failure(prepared,monkeypatch):
    root,calls,upload=prepared
    monkeypatch.setattr(p,'publish',lambda report:(_ for _ in ()).throw(ValueError('UPLOAD_RETRY')))
    first=p.run(root)
    assert first['state']=='FAILED_RESUMABLE' and calls==['prepare','package']
    monkeypatch.setattr(p,'publish',upload)
    second=p.run(root)
    assert second['state']=='COMPLETED' and 'error_code' not in second
    assert calls==['prepare','package','publish','deploy']
    assert p.run(root)['already_current'] and calls==['prepare','package','publish','deploy']


def test_changed_source_blocks_upload_and_wrong_scope_blocks_everything(prepared,monkeypatch):
    root,calls,_=prepared;count=0
    def fingerprint(root):
        nonlocal count
        count+=1;return (('a' if count==1 else 'c')*64,'2026-11-16')
    monkeypatch.setattr(p,'fingerprint',fingerprint)
    assert p.run(root)['error_code']=='MONITOR_SOURCE_CHANGED_RETRY'
    assert calls==['prepare','package']
    cfg=root/'config/monitor_publication.json';cfg.write_text(json.dumps(dict(enabled=True,symbols=['OTHER'])))
    with pytest.raises(ValueError,match='SCOPE'):p.run(root)


def test_prune_only_records_owned_stages_and_preserves_raw(tmp_path):
    base=tmp_path/'data/control/verified-monitor-history';base.mkdir(parents=True)
    artifacts=[]
    for name in ('a','b','c'):
        stage=base/(name*32);sealed=base/(name*32+'-sealed');stage.mkdir();sealed.mkdir()
        artifacts.append(dict(stage=str(stage),sealed=str(sealed)))
    raw=tmp_path/'data/history';raw.mkdir();(raw/'original.db').write_bytes(b'keep')
    keep,pending=p.prune(tmp_path,artifacts,2)
    assert len(keep)==2 and not pending and not Path(artifacts[0]['stage']).exists()
    assert (raw/'original.db').read_bytes()==b'keep'


def test_market_publication_requires_a_usable_restored_day_not_only_a_catalog(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts import verify_monitor_hosted
    from src.data_pipeline import parquet_history
    (tmp_path/'config').mkdir();(tmp_path/'config/monitor_publication.json').write_text(json.dumps(dict(enabled=True,symbols=p.SYMBOLS,market_enabled=True)))
    monkeypatch.setattr(p,'ROOT',tmp_path)
    monkeypatch.setattr(parquet_history,'ParquetHistory',lambda path:SimpleNamespace(catalog=lambda day:{s:{} for s in p.SYMBOLS+['VNM']}))
    entries=[dict(day='2026-11-16',symbol=s,observations=1,quality=dict(unit_counts={'EMPIRICALLY_VERIFIED':1})) for s in p.SYMBOLS]
    entries.append(dict(day='2026-11-16',symbol='VNM',observations=1,market_observation=True,dataset_id='a'*64))
    pack=dict(dataset_id='a'*64,detector_scope='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION',frames=[])
    def request(path):
        if path=='/api/hosting':return dict(seed_ready=True,recovery_state='READY')
        if path=='/api/datasets':return dict(datasets=entries)
        return pack
    monkeypatch.setattr(verify_monitor_hosted,'request',request)
    assert not p.verify('2026-11-16')
    pack['frames']=[dict(features=dict(eligible=False))]
    assert p.verify('2026-11-16')
    pack['frames'][0]['features']['eligible']=True
    assert not p.verify('2026-11-16')
