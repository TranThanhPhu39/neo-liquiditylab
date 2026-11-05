"""Public REST samples only; never stage unrelated local source or private archives."""
import gzip
import json
import os
from pathlib import Path

from config.settings import ROOT
from scripts.export_web_data import export_data
from scripts.github_api import GitHubAPI
from scripts.github_pages import prepare, publish, public_data_files
from src.data_pipeline.dnse_history import DNSEHistory


def auto_enabled(root=ROOT):
    path=Path(root)/'config/web_publication.json'
    if not path.exists():
        return False
    policy=json.loads(path.read_text(encoding='utf-8'))
    if (policy.get('repository')!='TranThanhPhu39/neo-liquiditylab' or
            policy.get('source')!='AUDITED_DNSE_REST_SAMPLES' or policy.get('raw_or_credentials') is not False):
        raise ValueError('PUBLICATION_POLICY_INVALID')
    return policy.get('enabled') is True


def publish_samples(root=ROOT, *, api=None, export=True):
    root=Path(root)
    source=json.loads((root/'data/processed/dnse/manifest.json').read_text(encoding='utf-8'))
    if (source.get('state')!='NORMALIZED_AND_AUDITED' or source.get('quarantined')!=0 or
            source.get('finance_checks',{}).get('state')!='PASSED'):
        raise ValueError('AUDITED_NORMALIZED_ARCHIVE_REQUIRED')
    from scripts.normalize_dnse_history import file_sha
    database=root/'data/processed/dnse/normalized.db'
    reference=source['files']['database']
    if database.stat().st_size!=reference['bytes'] or file_sha(database)!=reference['sha256']:
        raise ValueError('AUDITED_DATABASE_CHANGED')
    exported=export_data(DNSEHistory(root/'data/processed/dnse/normalized.db'),root/'web/data',
                         source['rule_hash'],preserve_previous=True) if export else None
    files=public_data_files(root)
    if not files:
        raise ValueError('PUBLIC_DATA_MISSING')
    secrets=[os.getenv(k).encode() for k in ('DNSE_API_KEY','DNSE_API_SECRET','VNSTOCK_API_KEY','vnstock_api',
        'GH_TOKEN','GITHUB_TOKEN','NEO_USERNAME','NEO_PASSWORD','NEO_TOKEN','PENPOT_MCP_URL',
        'LIQUIDITYLAB_STATE_KEY') if os.getenv(k)]
    for path,data in files.items():
        decoded=gzip.decompress(data) if path.endswith('.gz') else data
        if any(secret in decoded for secret in secrets):
            raise ValueError('SECRET_DETECTED_IN_PUBLIC_MARKET_DATA')
    api=api or GitHubAPI()
    base,head,tree,changed=prepare(api,files=files)
    result=publish(api,base,head,tree,changed,configure_pages=False)
    # GITHUB_TOKEN pushes do not trigger push workflows. Explicit dispatch is required.
    if os.getenv('GITHUB_ACTIONS')=='true':
        api.request('POST',base+'/actions/workflows/dashboard-pages.yml/dispatches',{'ref':'main'})
        result['pages_deployment_dispatched']=True
    return dict(state='PUBLIC_SAMPLES_PUSHED',export=exported,publication=result)
