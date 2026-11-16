"""Install the reviewed, bounded unit certificate from private runner configuration."""
import json
from pathlib import Path
import re
import tempfile

from src.data_pipeline.ssi_live_normalization import load_contract
from src.data_pipeline.ssi_live_store import atomic


def install_contract_bundle(root, encoded):
    if not encoded:return None
    if len(encoded.encode())>60000:raise ValueError('SSI_CONTRACT_BUNDLE_TOO_LARGE')
    bundle=json.loads(encoded)
    if bundle.get('format')!='liquiditylab-ssi-unit-contract-bundle-v1':raise ValueError('SSI_CONTRACT_BUNDLE_FORMAT')
    contract=bundle['contract'];files=bundle['evidence_files']
    if not isinstance(files,dict) or not 1<=len(files)<=4:raise ValueError('SSI_CONTRACT_BUNDLE_FILE_LIMIT')
    if any(not re.fullmatch(r'data/raw/ssi/unit_certificate_[a-z0-9_]+\.json',name) for name in files):
        raise ValueError('SSI_CONTRACT_BUNDLE_UNSAFE_PATH')
    if set(files)!={e['path'] for e in contract['evidence']}:raise ValueError('SSI_CONTRACT_BUNDLE_EVIDENCE_SET')
    root=Path(root).resolve();control=root/'data/control';control.mkdir(parents=True,exist_ok=True)
    # Validate every evidence hash and scope before writing the live contract.
    with tempfile.TemporaryDirectory(prefix='contract-review-',dir=control) as temporary:
        stage=Path(temporary)
        for name,value in files.items():
            path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);atomic(path,value)
        (stage/'data/control').mkdir(parents=True);atomic(stage/'data/control/ssi_live_contract.json',contract)
        checked=load_contract(stage)
        for name in files:
            target=(root/name).resolve()
            if not target.is_relative_to(root/'data/raw/ssi'):raise ValueError('SSI_CONTRACT_BUNDLE_UNSAFE_DESTINATION')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((stage/name).read_bytes())
        atomic(control/'ssi_live_contract.json',contract)
    return {k:checked.get(k) for k in ('status','symbols','trade_status','trade_symbols','hash')}
