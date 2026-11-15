"""Prepare a private, exact ACB/FPT/HPG subset for the hosted monitor."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from config.settings import ROOT
from src.data_pipeline.parquet_history import checked_folder,confined,digest

SYMBOLS=('ACB','FPT','HPG')


def prepare(root=ROOT):
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
    root=Path(root).resolve();stage=root/'data/control/web-seed'/uuid.uuid4().hex;stage.mkdir(parents=True)
    def copy(relative):
        source=confined(root,relative);target=confined(stage,relative)
        if source.is_symlink():raise ValueError('WEB_SEED_SYMLINK')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    index=json.loads((root/'data/exports/orderbook-monitor/index.json').read_bytes())
    entries=index['datasets'];days={e['day'] for e in entries}
    if not entries or any(e['symbol'] not in SYMBOLS for e in entries):raise ValueError('WEB_SEED_SCOPE')
    copy('data/exports/orderbook-monitor/index.json')
    for e in entries:
        folder,manifest=checked_folder(root,dict(folder=e['compact_folder']))
        for p in folder.iterdir():
            if p.is_file():copy(p.relative_to(root).as_posix())
        if e.get('events_file'):copy('data/exports/orderbook-monitor/'+e['events_file'])
    catalog=json.loads((root/'data/compact/catalog.json').read_bytes());quote_days=[];trade_packages=[]
    selected_rows=0
    for e in catalog['quote_days']:
        if e['day'] not in days:continue
        source=confined(root,e['folder']);target=confined(stage,e['folder']);target.mkdir(parents=True)
        for p in source.glob('quotes-*.parquet'):
            table=ds.dataset(p,format='parquet').to_table(filter=ds.field('symbol').isin(list(SYMBOLS)))
            if not table.num_rows:continue
            pq.write_table(table,target/p.name,compression='zstd',compression_level=6);selected_rows+=table.num_rows
        copy((source/'sqlite_metadata.json').relative_to(root).as_posix())
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(target.iterdir())]
        manifest=json.loads((source/'manifest.json').read_bytes())
        manifest.update(files=files,web_subset_symbols=list(SYMBOLS),derived_from_manifest_sha256=digest(source/'manifest.json'))
        (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8');quote_days.append(e)
    for e in catalog.get('trade_packages',[]):
        if e['symbol'] not in SYMBOLS or e['day'] not in days:continue
        source=confined(root,e['folder'])
        for p in source.iterdir():
            if p.is_file():copy(p.relative_to(root).as_posix())
        trade_packages.append(e)
    catalog.update(quote_days=quote_days,trade_packages=trade_packages,web_subset_symbols=list(SYMBOLS))
    (stage/'data/compact/catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
    for name in ('data/processed/dnse/manifest.json','report/dnse_normalization_audit.json'):copy(name)
    files=[dict(path=p.relative_to(stage).as_posix(),bytes=p.stat().st_size,sha256=digest(p))
           for p in sorted(stage.rglob('*')) if p.is_file()]
    info=dict(format='liquiditylab-private-web-seed-v1',symbols=list(SYMBOLS),days=sorted(days),
              datasets=len(entries),canonical_quote_rows=selected_rows,files=files)
    (stage/'seed-manifest.json').write_text(json.dumps(info),encoding='utf-8')
    return stage,info


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args()
    stage,info=prepare()
    print(json.dumps(dict(stage=str(stage),datasets=info['datasets'],days=len(info['days']),
                         rows=info['canonical_quote_rows'],bytes=sum(f['bytes'] for f in info['files']))))
