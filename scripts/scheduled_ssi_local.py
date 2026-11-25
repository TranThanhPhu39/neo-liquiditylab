"""Windows fallback: local Parquet capture, encrypted checkpoints, no relay writer."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta
import json
import re
import threading
import time
from types import SimpleNamespace

from config.settings import ROOT
from scripts.github_ssi_realtime import LOCAL, REPOSITORY, policy, plan, outcome
from scripts.check_ssi_start import check
from scripts.collect_dnse_history import keep_awake
from scripts.scheduled_cloud_guard import error_code
from scripts.scheduled_collection import write_json
from src.data_pipeline.job_lock import exclusive_job
from src.monitoring.ssi_live import SSILive


def session_at(now,p):
    if now.weekday()>=5 or now.date().isoformat() in p.get('excluded_dates',[]):return None
    for session in p['sessions']:
        w=plan(now,session,p)
        if datetime.fromisoformat(w['capture_from'])+timedelta(minutes=2)<=now<datetime.fromisoformat(w['close']):return session
    return None


def cloud_approval():
    from scripts.github_api import GitHubAPI,repository_path
    api=GitHubAPI();base=repository_path(REPOSITORY)
    return all(api.request('GET',base+'/actions/variables/'+name)['value']=='true'
               for name in ('SSI_REALTIME_ENABLED','SSI_REALTIME_STORAGE_APPROVED'))


def checkpoint_store(root):
    from scripts.ssi_checkpoint import SSICheckpoints
    from scripts.restore_ssi_checkpoint import recovery_key
    return SSICheckpoints(REPOSITORY,recovery_key(root))


def retry_pending(root,store_factory):
    for target in sorted((root/'report/automation/ssi-local-archives').glob('*.json')):
        saved=json.loads(target.read_text())
        if saved.get('state')!='LOCAL_CAPTURE_SAVED_UPLOAD_PENDING':continue
        run_id=saved['capture_run_id']
        if not re.fullmatch('[a-f0-9]{32}',run_id):raise ValueError('LOCAL_CAPTURE_ID_INVALID')
        folder=(root/'data/realtime/ssi'/saved['date']/run_id).resolve();folder.relative_to(root/'data/realtime/ssi')
        manifest=json.loads((folder/'manifest.json').read_bytes())
        if manifest['run_id']!=run_id or manifest['state']=='RECORDING':raise ValueError('LOCAL_CLOSED_CAPTURE_REQUIRED')
        # Closed immutable parts are checked by pack(); do not reopen a journal
        # or change old Parquet while retrying an upload.
        journal=SimpleNamespace(folder=folder,manifest=manifest,flush=lambda:None)
        service=SimpleNamespace(lock=threading.RLock(),journal=journal,status=lambda:dict(saved['capture'],active=False))
        checkpoint=store_factory(root).publish(service,saved['session']+'-local',saved['worker_id'],'1',final=True)
        saved.update(state='LOCAL_CAPTURE_PROCESSED',final_checkpoint=checkpoint)
        write_json(target,saved)
        current=root/'report/automation/ssi-local'/f"{saved['date']}-{saved['session']}.json"
        if current.exists() and json.loads(current.read_text()).get('capture_run_id')==run_id:write_json(current,saved)
        return dict(state='PENDING_LOCAL_UPLOAD_RECOVERED',report=str(target.relative_to(root)))
    return None


def run(*,root=ROOT,clock=None,probe=check,service_factory=SSILive,store_factory=checkpoint_store,
        approval=cloud_approval):
    root=root.resolve();clock=clock or (lambda:datetime.now(LOCAL));now=clock().astimezone(LOCAL)
    p=policy(root);session=session_at(now,p)
    if not p.get('local_fallback_enabled'):return dict(state='LOCAL_FALLBACK_DISABLED')
    stop=root/'data/control/STOP_SSI_LOCAL'
    if stop.exists():return dict(state='STOPPED_BY_LOCAL_FLAG')
    if not session:
        with exclusive_job(root/'data/control/ssi-local-fallback.lock'):
            recovered=retry_pending(root,store_factory)
        return recovered or dict(state='OUTSIDE_LOCAL_FALLBACK_WINDOW')
    target=root/'report/automation/ssi-local'/f'{now.date().isoformat()}-{session}.json'
    with exclusive_job(root/'data/control/ssi-local-fallback.lock'),keep_awake():
        previous=json.loads(target.read_text()) if target.exists() else {}
        if previous.get('processing_state')=='COMPLETED':
            return retry_pending(root,store_factory) or dict(state='LOCAL_SESSION_ALREADY_RECORDED',report=str(target.relative_to(root)))
        proof=probe(now,session,root=root)
        # No local relay: a recovery archive must never compete with GitHub's
        # active web writer. Checkpoints appear as a separate replay capture.
        if proof.get('worker_start_verified') and (not proof.get('deadline_passed') or proof.get('quotes_observed_for_all_symbols')):
            result=dict(state='REMOTE_WORKER_HEALTHY',start_check=proof,attempts=previous.get('attempts',0))
            write_json(target,result);return result
        cloud_checked=True
        try:
            if not approval():return dict(state='COLLECTION_DISABLED_BY_REPOSITORY')
        except Exception:
            # Explicit local enablement remains valid during a GitHub outage.
            # Raw persistence must not depend on accessing GitHub.
            cloud_checked=False
        attempts=previous.get('attempts',0)
        if attempts>=2:return dict(state='LOCAL_RETRY_LIMIT_REACHED',attempts=attempts)
        w=plan(clock().astimezone(LOCAL),session,p)
        seconds=int((datetime.fromisoformat(w['close'])-clock()).total_seconds())
        if seconds<10:return dict(state='OUTSIDE_LOCAL_FALLBACK_WINDOW')
        service=service_factory(root);store=None;storage_errors=[];started=time.monotonic()
        # A numeric local session identifier fits the existing encrypted archive
        # format; the session suffix and report identify its Windows origin.
        worker_id=str(int(datetime.now(timezone.utc).timestamp()*1_000_000))
        result=dict(state='LOCAL_RECORDING',processing_state='RUNNING',date=w['day'],session=session,
                    execution_origin='WINDOWS_LOCAL_FALLBACK',attempts=attempts+1,start_check=proof,
                    cloud_approval_checked=cloud_checked,relay_enabled=False,market_coverage_verified=False,
                    checkpoints=[],storage_errors=storage_errors,worker_id=worker_id)
        def save():
            write_json(target,result)
            if result.get('capture_run_id'):
                write_json(root/'report/automation/ssi-local-archives'/(result['capture_run_id']+'.json'),result)
        def publish(final=False):
            nonlocal store
            try:
                if store is None:store=store_factory(root)
                saved=store.publish(service,session+'-local',worker_id,'1',final=final)
                result['checkpoints'].append(saved)
                return saved
            except Exception as error:
                storage_errors.append(error_code(error));store=None
                return None
        interrupted=False;save()
        try:
            service.start(p['symbols'],seconds)
            result['capture_run_id']=service.journal.run_id;save()
            next_checkpoint=time.monotonic()+p['checkpoint_seconds'];last_heartbeat=0
            while service.thread.is_alive():
                service.thread.join(timeout=1)
                if stop.exists():interrupted=True;service.stop()
                if time.monotonic()-last_heartbeat>=30:
                    result.update(capture=service.status(),heartbeat_at_local=clock().isoformat());save()
                    last_heartbeat=time.monotonic()
                if time.monotonic()>=next_checkpoint:
                    publish();save();next_checkpoint=time.monotonic()+p['checkpoint_seconds']
        except KeyboardInterrupt:
            interrupted=True;service.stop()
        finally:
            service.close()
        capture=service.status()
        state=outcome(capture,w,interrupted)
        final=publish(final=True)
        result.update(state='LOCAL_CAPTURE_PROCESSED' if final else 'LOCAL_CAPTURE_SAVED_UPLOAD_PENDING',
                      processing_state='COMPLETED' if capture.get('reason')=='DURATION_LIMIT' else 'RETRYABLE',
                      collection_state=state,capture=capture,final_checkpoint=final,
                      elapsed_seconds=round(time.monotonic()-started,3),finished_at_local=clock().isoformat())
        save();return result


def main():
    folder=ROOT/'report/automation/logs';folder.mkdir(parents=True,exist_ok=True)
    log=folder/('ssi_local_'+datetime.now(LOCAL).date().isoformat()+'.log')
    with log.open('a',encoding='utf-8',buffering=1) as stream,redirect_stdout(stream),redirect_stderr(stream):
        print(json.dumps(dict(state='LOCAL_FALLBACK_TASK_STARTED',at=datetime.now(LOCAL).isoformat())),flush=True)
        try:result=run()
        except Exception as error:result=dict(state='FAILED_LOCAL_FALLBACK',error_code=error_code(error))
        write_json(ROOT/'report/automation/ssi_local_task_last.json',result)
        print(json.dumps(result),flush=True)
    if result.get('collection_state') and result['collection_state']!='COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED':return 1
    return 1 if result['state'] in ('FAILED_LOCAL_FALLBACK','LOCAL_CAPTURE_SAVED_UPLOAD_PENDING','LOCAL_RETRY_LIMIT_REACHED') else 0


if __name__=='__main__':raise SystemExit(main())
