"""List encrypted SSI cloud captures or import one verified capture for local replay."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import uuid

from config.settings import ROOT
from scripts.github_api import GitHubAPI,repository_path
from scripts.ssi_checkpoint import SSICheckpoints

REPOSITORY='TranThanhPhu39/neo-liquiditylab'


def recovery_key(root=ROOT):
    encoded=os.getenv('LIQUIDITYLAB_STATE_KEY')
    if not encoded:
        # Never silently create/rotate a key while restoring existing cloud data.
        if not (Path(root)/'data/control/github_state_key.dpapi').is_file():
            raise ValueError('SSI_EXISTING_RECOVERY_KEY_REQUIRED')
        from scripts.github_setup import protected_state_key
        encoded=protected_state_key()
    key=base64.b64decode(encoded,validate=True)
    if len(key)!=32:raise ValueError('SSI_STATE_KEY_REQUIRES_32_BYTES')
    return key


def install(files,*,root=ROOT):
    root=Path(root).resolve();m=json.loads(files['run/manifest.json'])
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',m['day']) or not re.fullmatch(r'[a-f0-9]{32}',m['run_id']):
        raise ValueError('SSI_UNSAFE_CAPTURE_ID')
    target=root/'data/realtime/ssi'/m['day']/m['run_id']
    if target.exists():raise ValueError('SSI_CAPTURE_ALREADY_EXISTS_NO_OVERWRITE')
    stage=root/'data/control'/('ssi-import-'+uuid.uuid4().hex);stage.mkdir(parents=True)
    for name,data in files.items():
        if name=='status.json':continue
        if name!='run/manifest.json' and not re.fullmatch(r'run/part-\d{8}\.parquet',name):
            raise ValueError('SSI_UNSAFE_CHECKPOINT_PATH')
        (stage/name.split('/')[1]).write_bytes(data)
    # Imported checkpoints are archives, never a recording journal to recover.
    m['state']='IMPORTED_CLOUD_CHECKPOINT'
    m['imported_source_state']=json.loads(files['run/manifest.json']).get('state')
    (stage/'manifest.json').write_text(json.dumps(m),encoding='utf-8')
    (stage/'cloud-status.json').write_bytes(files['status.json'])
    target.parent.mkdir(parents=True,exist_ok=True);stage.rename(target)
    return str(target)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list',action='store_true');group.add_argument('--tag')
    args=parser.parse_args();api=GitHubAPI()
    if args.list:
        releases=api.request('GET',repository_path(REPOSITORY)+'/releases?per_page=100')
        print(json.dumps([dict(tag=r['tag_name'],url=r['html_url'],name=r['name']) for r in releases
                          if r['tag_name'].startswith('ssi-ws-')],indent=2));return
    files,proof=SSICheckpoints(REPOSITORY,recovery_key(),api=api).restore(args.tag)
    proof['local_folder']=install(files)
    print(json.dumps(proof,indent=2))


if __name__=='__main__':main()
