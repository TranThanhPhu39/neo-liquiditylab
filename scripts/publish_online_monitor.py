"""Publish only reviewed web-backend source, never bootstrap plaintext or keys."""
import argparse
import ast
import base64
import json
import os
from pathlib import Path
import re

from config.settings import ROOT
from scripts.github_api import GitHubAPI,repository_path

REPOSITORY='TranThanhPhu39/neo-liquiditylab'


def sources(root=ROOT):
    root=Path(root);seen=set();todo=['scripts/serve_monitor_cloud.py','scripts/github_ssi_realtime.py',
        'scripts/prepare_monitor_web_seed.py','scripts/package_monitor_web_seed.py']
    while todo:
        name=todo.pop()
        if name in seen:continue
        seen.add(name)
        parts=Path(name).parts
        for n in range(1,len(parts)):
            init=Path(*parts[:n])/'__init__.py'
            if (root/init).is_file() and init.as_posix() not in seen:todo.append(init.as_posix())
        tree=ast.parse((root/name).read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            modules=([node.module] if isinstance(node,ast.ImportFrom) and node.module else
                     [a.name for a in node.names] if isinstance(node,ast.Import) else [])
            for module in modules:
                if module.split('.')[0] not in ('src','config','scripts'):continue
                path=module.replace('.','/')+'.py'
                if not (root/path).is_file():path=module.replace('.','/')+'/__init__.py'
                if not (root/path).is_file():raise ValueError('MISSING_PUBLIC_SOURCE_DEPENDENCY')
                todo.append(path)
    seen.update(('render.yaml','.dockerignore','deploy/render/Dockerfile','deploy/render/requirements.txt',
        '.github/workflows/ssi-realtime.yml','.github/workflows/monitor-web-checks.yml','docs/online_monitor.md',
        'scripts/check_monitor_cloud.py','scripts/publish_online_monitor.py','tests/test_online_monitor.py'))
    seen.update(p.relative_to(root).as_posix() for p in (root/'web/monitor').iterdir() if p.is_file() and p.suffix in ('.html','.css','.js'))
    protected=[v for k,v in os.environ.items() if len(v)>=8 and re.search('KEY|SECRET|TOKEN|PASSWORD|USERNAME|PENPOT',k,re.I)]
    files={}
    for name in sorted(seen):
        p=root/name
        if p.is_symlink() or not p.is_file():raise ValueError('PUBLIC_SOURCE_MISSING_OR_SYMLINK')
        value=p.read_text(encoding='utf-8-sig')
        if len(value.encode())>300_000 or any(secret in value for secret in protected):raise ValueError('UNSAFE_PUBLIC_SOURCE')
        files[name]=value
    return files


def publish():
    api=GitHubAPI();base=repository_path(REPOSITORY);files=sources()
    repo=api.request('GET',base)
    if repo['private'] or repo['default_branch']!='main' or not repo['permissions'].get('push'):raise ValueError('UNEXPECTED_REPOSITORY')
    head=api.request('GET',base+'/git/ref/heads/main')['object']['sha'];old=api.request('GET',base+'/git/commits/'+head)
    tree=api.request('POST',base+'/git/trees',dict(base_tree=old['tree']['sha'],
        tree=[dict(path=n,mode='100644',type='blob',content=v) for n,v in files.items()]))
    commit=api.request('POST',base+'/git/commits',dict(message='Prepare private hosted monitor with encrypted recovery and SSI web relay',tree=tree['sha'],parents=[head]))
    api.request('PATCH',base+'/git/refs/heads/main',dict(sha=commit['sha'],force=False))
    return dict(commit=commit['sha'],files=len(files),render_deployed=False,data_uploaded=False,
                blueprint_url='https://github.com/'+REPOSITORY+'/blob/main/render.yaml')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--publish',action='store_true');a=p.parse_args()
    print(json.dumps(publish() if a.publish else dict(files=len(sources()),source_review='PASSED')))
