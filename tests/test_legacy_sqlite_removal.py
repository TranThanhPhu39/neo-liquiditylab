import json
import sqlite3
import pytest
from scripts import remove_legacy_sqlite as cleanup
from scripts.pilot_compact_dnse import export_day
from scripts.compact_dnse_history import verify_day
from scripts.build_orderbook_monitor import atomic
from src.data_pipeline.parquet_history import digest
from tests.test_storage_compaction import prepare


@pytest.fixture
def legacy(tmp_path,monkeypatch):
    source=prepare(tmp_path);folder,manifest=export_day('2026-11-11',root=tmp_path)
    proof=verify_day(folder,root=tmp_path)
    atomic(tmp_path/'data/compact/catalog.json',dict(quote_days=[proof],trade_packages=[]))
    atomic(tmp_path/'report/legacy_sqlite_removal_eligibility.json',dict(state='LEGACY_SQLITE_ALL_ROWS_AND_RESTORE_COVERAGE_VERIFIED',
        path=source.relative_to(tmp_path).as_posix(),bytes=source.stat().st_size,sha256=digest(source),days=[proof]))
    saved={(folder/x['path']).relative_to(tmp_path).as_posix():x['sha256'] for x in manifest['files']}
    monkeypatch.setattr(cleanup,'verified_recovery_files',lambda root,required:saved)
    return tmp_path,source,folder,saved


def test_removes_only_exact_replaced_derived_database(legacy):
    root,source,folder,_=legacy
    raw=root/'data/history/original.db';raw.parent.mkdir(parents=True);raw.write_bytes(b'unique raw')
    result=cleanup.remove(root)
    assert result['deleted'] and not source.exists()
    assert (folder/'lineage.parquet').exists() and raw.read_bytes()==b'unique raw'


def test_missing_backup_or_changed_source_prevents_removal(legacy):
    root,source,_,saved=legacy
    saved.clear()
    with pytest.raises(ValueError,match='BACKUP_CHANGED'):cleanup.remove(root)
    assert source.exists()
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE unique_notes(note TEXT)')
    with pytest.raises(ValueError,match='CHANGED_AFTER_AUDIT|ACTIVE_WAL_DEFERRED'):cleanup.remove(root)
    assert source.exists()
