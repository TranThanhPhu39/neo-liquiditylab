"""Publish the already restore-verified ciphertext, then verify public recovery."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import quote

from config.settings import ROOT
from scripts.github_api import GitHubAPI,GitHubError,repository_path,release_asset_bytes
from scripts.restore_ssi_checkpoint import recovery_key
from src.monitoring.web_archive import REPOSITORY,restore_seed


def publish(report_path=None):
    report_path=Path(report_path).resolve() if report_path else ROOT/'report/online_monitor_preparation_2026-11-15.json'
    if report_path.parent!=(ROOT/'report').resolve() or not report_path.name.startswith('online_monitor_preparation_'):
        raise ValueError('UNEXPECTED_SEED_REPORT')
    report=json.loads(report_path.read_bytes())
    if report['state']!='ENCRYPTED_AND_RESTORE_VERIFIED':raise ValueError('VERIFIED_SEED_REQUIRED')
    folder=Path(report['folder']).resolve();allowed=(ROOT/'data/control/web-seed').resolve()
    if folder.parent not in (allowed,(ROOT/'data/control/verified-monitor-history').resolve()) or folder.is_symlink():
        raise ValueError('UNEXPECTED_SEED_LOCATION')
    tag=report['tag'];outer=json.loads((folder/'manifest.json').read_bytes())
    if outer['context']!=REPOSITORY+':'+tag:raise ValueError('SEED_CONTEXT_CHANGED')
    names=['manifest.json']+[p['name'] for p in outer['parts']]
    if names!=report['assets']:raise ValueError('SEED_ASSET_SET_CHANGED')
    for part in outer['parts']:
        p=folder/part['name']
        if p.is_symlink() or p.stat().st_size!=part['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=part['sha256']:
            raise ValueError('SEED_CIPHERTEXT_CHANGED')
    api=GitHubAPI();base=repository_path(REPOSITORY)
    try:release=api.request('GET',base+'/releases/tags/'+tag)
    except GitHubError as error:
        if error.status!=404:raise
        release=api.request('POST',base+'/releases',dict(tag_name=tag,target_commitish='main',name='Private encrypted monitor bootstrap',
            draft=True,prerelease=True,body='AES-GCM ciphertext for the owner-protected dashboard. No credentials or plaintext market data.'))
    existing={a['name']:a for a in release['assets']}
    for name in names:
        data=(folder/name).read_bytes();expected=hashlib.sha256(data).hexdigest()
        if name in existing:
            a=existing[name]
            if a.get('digest')=='sha256:'+expected:continue
            if hashlib.sha256(release_asset_bytes(api,REPOSITORY,a,limit=17*1024*1024)).hexdigest()==expected:continue
            raise ValueError('EXISTING_RELEASE_ASSET_DIFFERS')
        api.request('POST',release['upload_url'].split('{')[0]+'?name='+quote(name,safe=''),data=data,content_type='application/octet-stream')
    api.request('PATCH',base+'/releases/'+str(release['id']),dict(draft=False))
    with tempfile.TemporaryDirectory(prefix='remote-web-seed-',dir=allowed) as tmp:
        if not Path(tmp).resolve().is_relative_to(allowed):raise ValueError('UNSAFE_VERIFICATION_ROOT')
        proof=restore_seed(tmp,recovery_key(),tag,api=GitHubAPI(token=''))
    report.update(uploaded=True,remote_recovery_verified=True,public_download_requires_github_token=False,
                  remote_proof=proof,seed_release_id=release['id'])
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return dict(tag=tag,uploaded=True,remote_recovery_verified=True,datasets=proof['datasets'],credentials_logged=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report');args=parser.parse_args()
    print(json.dumps(publish(args.report)))
