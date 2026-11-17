"""Private Render CLI transport; credentials never enter arguments or output."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/'data/control/render-cli/bin/render.exe'
CONFIG=ROOT/'data/control/render-cli/config'


def call(arguments,*,timeout=120,raw_text=False):
    env={k:v for k,v in os.environ.items() if not re.search(r'KEY|SECRET|TOKEN|PASSWORD|PENPOT|NEO_USERNAME',k,re.I)}
    # Pass only the explicitly configured Render credential to the official CLI.
    # Broker credentials in .env must never be inherited by deployment commands.
    api_key=os.environ.get('RENDER_API_KEY') or dotenv_values(ROOT/'.env',interpolate=False).get('RENDER_API_KEY')
    if api_key:
        env['RENDER_API_KEY']=api_key
    env.update(RENDER_CLI_CONFIG_DIR=str(CONFIG),RENDER_CLI_DISABLE_ANALYTICS='1')
    result=subprocess.run([str(CLI),*arguments,'--output','json'],cwd=ROOT,env=env,
        stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        code='RENDER_CLI_FAILED'
        message=result.stderr.lower()
        if 'login' in message or 'logged in' in message:code='RENDER_LOGIN_REQUIRED'
        elif 'workspace' in message:code='RENDER_WORKSPACE_REQUIRED'
        elif '403' in message:code='RENDER_HTTP_403'
        elif '401' in message:code='RENDER_HTTP_401'
        raise RuntimeError(code)
    if raw_text:return result.stdout
    try:return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Log queries emit one JSON object per line rather than a JSON array.
        try:return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        except json.JSONDecodeError:raise RuntimeError('RENDER_EXPECTED_JSON_RESPONSE') from None


def safe_metadata(value):
    """Read-only discovery summaries, including shape but excluding secret values."""
    if isinstance(value,list):return [safe_metadata(x) for x in value]
    if not isinstance(value,dict):return {'kind':type(value).__name__}
    allowed=('id','name','email','status','type','plan','region','role','dashboardUrl','url','ownerId','workspaceId')
    result={k:value[k] for k in allowed if k in value and isinstance(value[k],(str,int,bool,type(None)))}
    result['response_fields']=list(value)
    for name in ('service','workspace','owner','postgres','user','deploy','data'):
        if isinstance(value.get(name),(dict,list)):result[name]=safe_metadata(value[name])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resource',choices=['identity','workspaces','selected','services','databases'])
    args=parser.parse_args()
    commands={'identity':['whoami'],'workspaces':['workspaces'],'selected':['workspace','current'],
              'services':['services'],'databases':['postgres','list']}
    print(json.dumps(safe_metadata(call(commands[args.resource]))))
