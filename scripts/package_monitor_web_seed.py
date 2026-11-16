"""Encrypt and fully restore-check an existing private monitor staging directory."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from datetime import datetime,timezone

from config.settings import ROOT
from scripts.restore_ssi_checkpoint import recovery_key
from src.monitoring.web_archive import encrypt_seed,restore_seed,REPOSITORY


class LocalAssets:
    def __init__(self,folder,names):self.folder=folder;self.names=names
    def request(self,method,path,**kwargs):
        if '/releases/tags/' in path:
            return dict(assets=[dict(name=n,id=i,size=(self.folder/n).stat().st_size) for i,n in enumerate(self.names)])
        return (self.folder/self.names[int(path.rsplit('/',1)[-1])]).read_bytes()


def package(stage,report_path=None):
    stage=Path(stage).resolve();allowed=(ROOT/'data/control/web-seed').resolve()
    permitted=[allowed,(ROOT/'data/control/verified-monitor-history').resolve()]
    if stage.parent not in permitted or stage.is_symlink():raise ValueError('WEB_SEED_UNEXPECTED_STAGE')
    manifest=json.loads((stage/'seed-manifest.json').read_bytes())
    tag='monitor-web-seed-'+datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+hashlib.sha256((stage/'seed-manifest.json').read_bytes()).hexdigest()[:12]
    output=stage.parent/(stage.name+'-sealed');key=recovery_key()
    names=encrypt_seed(stage,key,tag,output)
    with tempfile.TemporaryDirectory(prefix='web-seed-verify-',dir=allowed) as tmp:
        restored=Path(tmp).resolve()
        if not restored.is_relative_to(allowed):raise ValueError('WEB_SEED_UNSAFE_VERIFY_ROOT')
        proof=restore_seed(restored,key,tag,api=LocalAssets(output,names))
        from scripts.serve_orderbook_monitor import MonitorServer
        server=MonitorServer(restored,port=0,assets_root=ROOT)
        try:
            entries=server.index();day=max(e['day'] for e in entries)
            watch=server.watchlist(day,day+'T04:29:59+00:00')
            trade_counts={}
            for symbol in ('ACB','FPT','HPG'):
                e=next(e for e in entries if e['day']==day and e['symbol']==symbol and not e.get('archived'))
                m=json.loads((restored/e['compact_folder']/'metadata.json').read_bytes())
                if m['dataset_id']!=e['dataset_id']:raise ValueError('WEB_SEED_DATASET_ID')
                trade_counts[symbol]=len(server.trades(e['dataset_id'])['trades'])
            # Exercise the existing audited reanalysis against the hosted subset.
            from scripts.build_orderbook_monitor import build
            rebuilt=build(day,root=restored,symbol='ACB')
            proof.update(watch_symbols=[r['symbol'] for r in watch],trade_counts=trade_counts,
                         reanalysis_state=rebuilt['state'],reanalysis_observations=rebuilt['dataset']['observations'])
        finally:server.server_close()
    # Remove only our redundant plaintext tar after successful encrypted restore.
    tar=output/'private-seed.tar.gz'
    if any(tar.resolve().is_relative_to(base) for base in permitted) and tar.is_file():tar.unlink()
    result=dict(state='ENCRYPTED_AND_RESTORE_VERIFIED',tag=tag,folder=str(output),
        datasets=manifest['datasets'],days=len(manifest['days']),canonical_rows=manifest['canonical_quote_rows'],
        latest_day=max(manifest['days']),symbols=manifest['symbols'],
        encrypted_bytes=sum((output/n).stat().st_size for n in names),assets=names,proof=proof,
        uploaded=False,hosting_deployed=False)
    (Path(report_path) if report_path else ROOT/'report/online_monitor_preparation_2026-11-15.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',required=True);a=p.parse_args()
    print(json.dumps(package(a.stage),indent=2))
