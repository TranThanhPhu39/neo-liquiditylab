"""Point the existing Free monitor at an approved, remotely verified seed."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from dotenv import dotenv_values
import truststore
from scripts.render_cli_ops import ROOT,call

SERVICE='srv-dbspjs6c6qcc739qhpk0'
WORKSPACE='tea-dbsmkrt6e07s73a9ss90'


def update():
    if call(['workspace','current'])['id']!=WORKSPACE:raise ValueError('CONFIRMED_WORKSPACE_REQUIRED')
    services=call(['services'])
    service=next((v['service'] for v in services if v.get('service',{}).get('id')==SERVICE),None)
    if not service or service['ownerId']!=WORKSPACE or service['name']!='neo-liquiditylab-monitor':
        raise ValueError('CONFIRMED_MONITOR_REQUIRED')
    preparation=json.loads((ROOT/'report/online_monitor_preparation_2026-11-16.json').read_bytes())
    if not preparation.get('uploaded') or not preparation.get('remote_recovery_verified') or preparation['datasets']!=39:
        raise ValueError('APPROVED_REMOTE_SEED_REQUIRED')
    tag=preparation['tag'];commit=json.loads((ROOT/'report/ssi_repair_2026-11-16.json').read_bytes())['source_commit']
    key=dotenv_values(ROOT/'.env',interpolate=False)['RENDER_API_KEY'];truststore.inject_into_ssl()
    base='https://api.render.com/v1/services/'+SERVICE+'/env-vars'
    def api(method,url,value=None):
        data=json.dumps(value).encode() if value is not None else None
        request=Request(url,data=data,method=method,headers={'Authorization':'Bearer '+key,'Accept':'application/json','Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=45) as response:return json.load(response)
        except HTTPError as error:raise RuntimeError('RENDER_HTTP_'+str(error.code)) from None
    before={v['envVar']['key']:v['envVar']['value'] for v in api('GET',base)}
    api('PUT',base+'/MONITOR_SEED_TAG',dict(value=tag))
    after={v['envVar']['key']:v['envVar']['value'] for v in api('GET',base)}
    if after.get('MONITOR_SEED_TAG')!=tag or any(after.get(k)!=v for k,v in before.items() if k!='MONITOR_SEED_TAG'):
        raise ValueError('RENDER_UNRELATED_CONFIGURATION_CHANGED')
    # The application loads the old secret file only for absent env keys.
    # This single service variable intentionally becomes the seed source of truth.
    deploy=call(['deploys','create',SERVICE,'--commit',commit,'--confirm'])
    result=dict(state='APPROVED_SEED_DEPLOY_REQUESTED',service=SERVICE,workspace=WORKSPACE,tag=tag,commit=commit,
        deploy={k:deploy.get(k) for k in ('id','status','createdAt')},environment_keys=sorted(after),
        existing_password_and_database_config_preserved=True,broker_credentials_transferred=False)
    (ROOT/'report/render_seed_update_2026-11-16.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':print(json.dumps(update()))
