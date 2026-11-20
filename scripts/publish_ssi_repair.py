"""Publish the SSI repair source only, with bounded credential-redacted API diagnostics."""
import json
from pathlib import Path
import re
import hashlib
from urllib.error import HTTPError
from urllib.parse import urlsplit

from dotenv import dotenv_values
from scripts.github_api import GitHubAPI,repository_path

FILES=['src/data_providers/ssi_stream.py','tests/test_ssi_session.py','src/data_pipeline/ssi_contract_bundle.py',
       'scripts/github_ssi_realtime.py','.github/workflows/ssi-realtime.yml','scripts/ssi_schedule_guard.py',
       '.github/workflows/ssi-schedule-guard.yml','tests/test_ssi_guard_contract.py','docs/ssi_github_actions.md',
       'src/data_pipeline/dnse_unit_revision.py','src/data_pipeline/parquet_history.py','src/data_pipeline/parquet_replay.py',
       'src/monitoring/web_archive.py','scripts/build_orderbook_monitor.py','scripts/serve_monitor_cloud.py',
       'scripts/create_recovery_backup.py','scripts/create_incremental_recovery.py','scripts/prepare_verified_monitor_history.py',
       'scripts/package_monitor_web_seed.py','scripts/serve_orderbook_monitor.py','tests/test_dnse_unit_revision.py','tests/test_parquet_dashboard.py','tests/test_online_monitor.py',
       'README.md','report/data_acceptance_repair_2026-11-16.md',
       '.github/workflows/ssi-unit-contract-check.yml','scripts/publish_monitor_web_seed.py',
       'src/monitoring/web_reviews.py','scripts/update_monitor_seed_render.py',
       'scripts/publish_daily_monitor.py','scripts/scheduled_dnse_day.py','config/monitor_publication.json',
       'scripts/audit_legacy_sqlite.py','scripts/remove_legacy_sqlite.py','scripts/recovery_coverage.py',
       'scripts/preview_monitor_dashboard.py','web/monitor/app.js','web/monitor/index.html','web/monitor/style.css',
       'tests/test_daily_monitor_publication.py','tests/test_scheduled_dnse_day.py','tests/test_legacy_sqlite_removal.py',
       'docs/daily_monitor_automation.md','report/web_completion_audit_2026-11-16.md',
       'scripts/verify_monitor_hosted.py','tests/test_hosted_history_verification.py',
       'docs/parquet_dashboard_storage.md','docs/online_monitor.md',
       'src/monitoring/market.py','scripts/prepare_market_monitor.py','tests/test_market_monitor.py',
       'web/monitor/model.js','web/monitor/landing.html','tests/js/event-replay.test.cjs',
       'docs/market_monitor.md','docs/product_scope.md','report/market_monitor_acceptance_2026-11-16.md',
       'src/monitoring/spread.py','src/monitoring/relative.py','tests/test_relative_monitor.py',
       'src/data_pipeline/market_unit_evidence.py','scripts/audit_market_units.py',
       'scripts/capture_public_unit_reference.py','tests/test_market_unit_evidence.py','docs/market_unit_verification.md',
       'docs/relative_market_analysis.md','scripts/render_cli_ops.py','scripts/cleanup_dashboard_storage.py',
       'scripts/pilot_compact_dnse.py','scripts/compact_dnse_history.py',
       'scripts/migrate_dashboard_parquet.py','scripts/update_dnse_parquet.py',
       'tests/test_event_monitor.py','tests/test_storage_compaction.py','scripts/publish_ssi_repair.py',
       'scripts/normalize_dnse_history.py','scripts/audit_dnse_normalized.py','tests/test_dnse_normalization.py',
       'scripts/github_api.py','scripts/scheduled_cloud_guard.py','scripts/install_cloud_guard_task.py',
       'scripts/install_cloud_guard_task.ps1','scripts/install_dnse_day_task.py','scripts/install_dnse_day_task.ps1',
       '.github/workflows/dnse-realtime.yml','tests/test_scheduled_cloud_guard.py','docs/independent_collection_schedule.md',
       'web/monitor/live.js','tests/test_public_monitor.py','scripts/check_monitor_cloud.py',
       '.github/workflows/monitor-web-checks.yml','web/monitor/capture-model.js','web/monitor/live-model.js',
       'tests/js/capture-model.test.cjs','docs/unified_dashboard.md','scripts/verify_unified_monitor_browser.py',
       'web/monitor/http.js','tests/js/monitor-http.test.cjs','tests/test_cloud_recovery_budget.py',
       'src/monitoring/web_relay.py','src/monitoring/ssi_live.py','src/data_pipeline/ssi_live_store.py',
       'scripts/ssi_checkpoint.py','tests/test_github_ssi.py','tests/test_ssi_live.py',
       'tests/js/ssi-live.test.cjs','tests/js/ssi-live-ui.test.cjs','web/monitor/realtime.html','web/monitor/live.css']


def main():
    protected=[v for k,v in dotenv_values('.env',interpolate=False).items() if v and len(v)>=8 and re.search('KEY|SECRET|TOKEN|PASSWORD|USERNAME|PENPOT',k,re.I)]
    files={n:Path(n).read_text(encoding='utf-8') for n in FILES}
    assert not any(v in text for v in protected for text in files.values())
    api=GitHubAPI();original=api._opener.open
    def open_request(request,*args,**kwargs):
        try:return original(request,*args,**kwargs)
        except HTTPError as error:
            try:body=json.loads(error.read(16384))
            except (ValueError,OSError):body={}
            message=str(body.get('message',''))[:1500]
            for value in protected:message=message.replace(value,'[REDACTED]')
            print(json.dumps(dict(endpoint=urlsplit(request.full_url).path,status=error.code,message=message,
                errors=[{k:e.get(k) for k in ('resource','field','code')} for e in body.get('errors',[]) if isinstance(e,dict)])),flush=True)
            raise
    api._opener.open=open_request
    b=repository_path('TranThanhPhu39/neo-liquiditylab')
    h=api.request('GET',b+'/git/ref/heads/main')['object']['sha'];c=api.request('GET',b+'/git/commits/'+h)
    existing=api.request('GET',b+'/git/trees/'+c['tree']['sha']+'?recursive=1')
    if existing.get('truncated'):raise ValueError('SOURCE_TREE_TRUNCATED')
    known={e['path']:e['sha'] for e in existing['tree'] if e['type']=='blob'}
    changes=[]
    for name,content in files.items():
        raw=content.encode('utf-8');git_sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if known.get(name)==git_sha:continue
        blob=api.request('POST',b+'/git/blobs',dict(content=content,encoding='utf-8'))
        if blob['sha']!=git_sha:raise ValueError('SOURCE_BLOB_IDENTITY_MISMATCH')
        changes.append(dict(path=name,type='blob',mode='100644',sha=blob['sha']))
    if not changes:
        print(json.dumps(dict(source_commit=h,state='SOURCE_ALREADY_CURRENT')));return
    t=api.request('POST',b+'/git/trees',{'base_tree':c['tree']['sha'],'tree':changes})
    new=api.request('POST',b+'/git/commits',{'message':'Bound catalog, checkpoint and replay memory while preserving full Parquet history','tree':t['sha'],'parents':[h]})
    api.request('PATCH',b+'/git/refs/heads/main',{'sha':new['sha'],'force':False})
    report=dict(source_commit=new['sha'],contract_bundle_upload='INSTALLED' if Path('report/ssi_contract_secret_2026-11-16.json').exists() else 'WAITING_FOR_EXPLICIT_APPROVAL',units_locally_reviewed=['ACB','FPT','HPG'])
    Path('report/ssi_repair_2026-11-16.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
