"""Remove only the audited, byte-pinned, fully replaced derived normalized DB."""
from contextlib import ExitStack
from datetime import datetime,timezone
import json
from pathlib import Path

from config.settings import ROOT
from scripts.audit_legacy_sqlite import verify_metadata
from scripts.recovery_coverage import verified_recovery_files
from scripts.scheduled_collection import exclusive_job
from scripts.build_orderbook_monitor import atomic
from src.data_pipeline.parquet_history import checked_folder,digest


def remove(root=ROOT):
    root=Path(root).resolve();candidate=root/'data/processed/dnse/normalized.db';source=candidate.resolve()
    if source!=candidate or candidate.is_symlink():raise ValueError('LEGACY_REMOVAL_UNSAFE_PATH')
    proof_path=root/'report/legacy_sqlite_removal_eligibility.json';proof=json.loads(proof_path.read_bytes())
    if proof.get('state')!='LEGACY_SQLITE_ALL_ROWS_AND_RESTORE_COVERAGE_VERIFIED':raise ValueError('LEGACY_FULL_REPLACEMENT_PROOF_REQUIRED')
    if not source.exists():return dict(state='LEGACY_DATABASE_ALREADY_ABSENT')
    if proof['path']!='data/processed/dnse/normalized.db':raise ValueError('LEGACY_REMOVAL_WRONG_TARGET')
    with ExitStack() as locks:
        for name in ('dnse_update','dnse_history','dnse_normalization','dnse-compaction'):
            locks.enter_context(exclusive_job(root/'data/control'/(name+'.lock')))
        if source.stat().st_size!=proof['bytes'] or digest(source)!=proof['sha256']:raise ValueError('LEGACY_CHANGED_AFTER_AUDIT')
        wal=Path(str(source)+'-wal')
        if wal.exists() and wal.stat().st_size:raise ValueError('LEGACY_ACTIVE_WAL_DEFERRED')
        catalog=json.loads((root/'data/compact/catalog.json').read_bytes())
        mapping={e['day']:e for e in catalog['quote_days']}
        required={}
        for day in proof['days']:
            folder,manifest=checked_folder(root,mapping[day['day']])
            required.update({(folder/f['path']).relative_to(root).as_posix():f['sha256'] for f in manifest['files']})
        saved=verified_recovery_files(root,required)
        for day in proof['days']:
            folder,manifest=checked_folder(root,mapping[day['day']])
            if manifest['canonical_snapshot_digest']!=day['canonical_snapshot_digest'] or manifest['snapshots']!=day['snapshots']:
                raise ValueError('LEGACY_PARQUET_CHANGED_AFTER_AUDIT')
            for f in manifest['files']:
                if saved.get((folder/f['path']).relative_to(root).as_posix())!=f['sha256']:
                    raise ValueError('LEGACY_REPLACEMENT_BACKUP_CHANGED')
        metadata=verify_metadata(root,source,[d['day'] for d in proof['days']])
        source.unlink()
        pending=[]
        for p in (Path(str(source)+'-wal'),Path(str(source)+'-shm')):
            if p.exists():
                try:p.unlink()
                except OSError:pending.append(p.relative_to(root).as_posix())
        proof.update(state='LEGACY_DERIVED_SQLITE_REMOVED',deleted=True,freed_bytes=proof['bytes'],
            metadata_tables_verified=metadata,removed_at_utc=datetime.now(timezone.utc).isoformat(),
            original_raw_archives_deleted=False,parquet_or_backups_deleted=False,cleanup_pending=pending)
        atomic(proof_path,proof)
        return {k:v for k,v in proof.items() if k!='days'}


if __name__=='__main__':print(json.dumps(remove()))
