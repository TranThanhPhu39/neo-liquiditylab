"""Publish the reviewed dashboard to the existing public repository and enable Pages."""
import argparse
import base64
import hashlib
import gzip
import json
import os
from pathlib import Path

from config.settings import ROOT, get_vnstock_api_key
from scripts.github_api import GitHubAPI, GitHubError, repository_path
from scripts.github_setup import public_source, REPOSITORY

WEB_FILES = {
    '.nojekyll', 'README.md', 'app.js', 'cloud-data.js', 'market-view.js', 'depth-surface.js', 'engine-client.js', 'engine-worker.js', 'engine.zip', 'index.html',
    'vendor/plotly-gl3d-3.7.0.min.js','vendor/PLOTLY-LICENSE','vendor/plotly-manifest.json',
    'mark.svg', 'style.css', 'THIRD_PARTY.md', 'fonts/OFL.txt', 'fonts/rubik-0.woff2',
    'fonts/rubik-1.woff2', 'fonts/rubik-2.woff2', 'runtime/PYODIDE-LICENSE',
    'runtime/pyodide-lock.json', 'runtime/pyodide.asm.js', 'runtime/pyodide.asm.wasm',
    'runtime/pyodide.js', 'runtime/python_stdlib.zip',
}
BINARY = {'.zip', '.woff2', '.wasm', '.gz'}


def public_data_files(root):
    from scripts.export_web_data import FORMAT, FIELDS, referenced_files
    folder = root/'web/data'
    if not (folder/'index.json').exists():
        return {}
    index = json.loads((folder/'index.json').read_text(encoding='utf-8'))
    if index.get('format') != FORMAT:
        raise ValueError('EXPECTED_PUBLIC_DATA_INDEX')
    result = {'web/data/index.json': (folder/'index.json').read_bytes()}
    for item in referenced_files(index):
        path = folder/item['path']
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent!=root):
            raise ValueError('PUBLIC_DATA_SYMLINK_REJECTED')
        content = path.read_bytes()
        if len(content)!=item['bytes'] or hashlib.sha256(content).hexdigest()!=item['sha256']:
            raise ValueError('PUBLIC_DATA_CHECKSUM_MISMATCH')
        payload = json.loads(gzip.decompress(content))
        if len(payload['records'])!=item['records'] or any(set(r)-FIELDS for r in payload['records']):
            raise ValueError('UNREVIEWED_FIELDS_IN_PUBLIC_DATA')
        result['web/data/'+item['path']] = content
    if sum(map(len, result.values())) > 600*1024*1024:
        raise ValueError('PUBLIC_DATA_SIZE_REQUIRES_REVIEW')
    return result


def reviewed_files():
    files = {name: content.encode('utf-8') for name, content in public_source().items()}
    values = [get_vnstock_api_key(), *(os.getenv(name) for name in (
        'DNSE_API_KEY', 'DNSE_API_SECRET', 'GH_TOKEN', 'GITHUB_TOKEN',
        'PENPOT_MCP_URL', 'LIQUIDITYLAB_STATE_KEY'))]
    secrets = [v.encode() for v in values if v]
    for name in sorted(WEB_FILES):
        path = ROOT/'web'/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('PUBLIC_WEB_FILE_MISSING_OR_SYMLINK')
        content = path.read_bytes()
        if any(secret in content for secret in secrets):
            raise ValueError('SECRET_DETECTED_IN_PUBLIC_WEB')
        if len(content) > 20*1024*1024:
            raise ValueError('UNEXPECTED_LARGE_WEB_ASSET')
        files['web/'+name] = content
    for path, content in public_data_files(ROOT).items():
        decoded = gzip.decompress(content) if path.endswith('.gz') else content
        if any(secret in decoded for secret in secrets):
            raise ValueError('SECRET_DETECTED_IN_PUBLIC_MARKET_DATA')
        files[path] = content
    return files


def blob_sha(content):
    return hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()


def prepare(api, files=None):
    base = repository_path(REPOSITORY)
    repo = api.request('GET', base)
    if repo['private'] or repo['default_branch'] != 'main' or (files is None and not repo['permissions']['admin']):
        raise ValueError('EXPECTED_PUBLIC_MAIN_ADMIN_REPOSITORY')
    head = api.request('GET', base+'/git/ref/heads/main')['object']['sha']
    commit = api.request('GET', base+'/git/commits/'+head)
    tree = api.request('GET', base+'/git/trees/'+commit['tree']['sha']+'?recursive=1')
    existing = {row['path']: row['sha'] for row in tree['tree'] if row['type']=='blob'}
    selected=reviewed_files() if files is None else files
    changed = {path: data for path, data in selected.items() if existing.get(path) != blob_sha(data)}
    return base, head, commit['tree']['sha'], changed


def publish(api, base, head, base_tree, files, *, configure_pages=True):
    # Enable Pages before the push can start its deployment workflow.
    pages={'html_url':'https://tranthanhphu39.github.io/neo-liquiditylab/','build_type':'workflow'}
    if configure_pages:
        try:
            pages = api.request('GET', base+'/pages')
        except GitHubError as error:
            if error.status != 404:
                raise
            pages = api.request('POST', base+'/pages', {'build_type': 'workflow'})
        if pages.get('build_type') != 'workflow':
            api.request('PUT', base+'/pages', {'build_type': 'workflow'})
    entries = []
    for path, content in files.items():
        entry = {'path': path, 'mode': '100644', 'type': 'blob'}
        if Path(path).suffix in BINARY:
            entry['sha'] = api.request('POST', base+'/git/blobs',
                {'content': base64.b64encode(content).decode(), 'encoding': 'base64'})['sha']
        else:
            entry['content'] = content.decode('utf-8')
        entries.append(entry)
    if entries:
        tree = api.request('POST', base+'/git/trees', {'base_tree': base_tree, 'tree': entries})
        new = api.request('POST', base+'/git/commits', {
            'message': 'Deploy monochrome DNSE dashboard to GitHub Pages',
            'tree': tree['sha'], 'parents': [head]})
        api.request('PATCH', base+'/git/refs/heads/main', {'sha': new['sha'], 'force': False})
        head = new['sha']
    return {'repository': REPOSITORY, 'commit': head, 'changed_files': len(files),
            'pages_url': pages.get('html_url'), 'build_type': 'workflow',
            'secrets_changed': False, 'collector_settings_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    api = GitHubAPI()
    base, head, tree, changed = prepare(api)
    plan = {'repository': REPOSITORY, 'base_commit': head,
            'changed_paths': sorted(changed), 'total_changed_bytes': sum(map(len, changed.values()))}
    if args.publish:
        result = publish(api, base, head, tree, changed)
        path = ROOT/'data/control/github_pages_publish.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
    else:
        print(json.dumps(plan))


if __name__ == '__main__':
    main()
