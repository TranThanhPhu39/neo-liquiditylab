from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import io
import json
import tarfile
import hashlib

import pytest
from src.data_pipeline.dnse_normalization import DnseRules,normalize_quote
from src.data_pipeline.dnse_unit_revision import revise_record,REVISION_FIELDS
from scripts.create_incremental_recovery import verify,FORMAT


def fixture():
    rules=DnseRules('2026-07-23',Decimal(1000),Decimal(10),('ACB',),('STO',),'old',{})
    quote=dict(symbol='FPT',marketId='STO',boardId='G1',time='2026-11-13 13:30:00.001',
        bid=[dict(price=p,quantity=100) for p in (50,49.95,49.9)],
        offer=[dict(price=p,quantity=200) for p in (50.05,50.1,50.15)])
    record=normalize_quote(quote,'2026-11-13T09:00:00+00:00',rules)
    return record,replace(rules,verified_symbols=('ACB','FPT','HPG'),rule_hash='reviewed')


def test_unit_revision_revalidates_but_preserves_all_market_fields_and_input():
    old,rules=fixture();original=deepcopy(old)
    assert old['data_quality_status']=='PARTIAL' and not old['financial_eligible']
    new=revise_record(old,rules,'old')
    assert old==original and new['financial_eligible'] and new['data_quality_status']=='VALID'
    assert new['unit_verification_status']=='EMPIRICALLY_VERIFIED'
    assert {k:v for k,v in old.items() if k not in REVISION_FIELDS}=={k:v for k,v in new.items() if k not in REVISION_FIELDS}


def test_revision_cannot_clear_invalid_or_auction_gate_or_change_unreviewed_scope():
    old,rules=fixture()
    invalid=dict(old,data_quality_status='INVALID')
    assert not revise_record(invalid,rules,'old')['financial_eligible']
    outside=dict(old,market_session='DNSE_OUTSIDE_CONTINUOUS')
    assert not revise_record(outside,rules,'old')['financial_eligible']
    for wrong in (dict(old,symbol='OTHER'),dict(old,market_id='STX'),dict(old,unit_contract_hash='wrong')):
        with pytest.raises(ValueError,match='SCOPE'):revise_record(wrong,rules,'old')


def test_incremental_archive_detects_corrupt_and_missing_payload(tmp_path):
    expected=b'correct';entry=dict(path='data/value.json',bytes=len(expected),sha256=hashlib.sha256(expected).hexdigest())
    def bundle(content):
        path=tmp_path/('missing.tar.gz' if content is None else 'bad.tar.gz')
        manifest=json.dumps(dict(format=FORMAT,changed_files=[entry])).encode()
        with tarfile.open(path,'w:gz') as archive:
            info=tarfile.TarInfo('INCREMENTAL_MANIFEST.json');info.size=len(manifest);archive.addfile(info,io.BytesIO(manifest))
            if content is not None:
                info=tarfile.TarInfo(entry['path']);info.size=len(content);archive.addfile(info,io.BytesIO(content))
        return path
    with pytest.raises(ValueError,match='HASH_MISMATCH'):verify(bundle(b'corrupt'))
    with pytest.raises(ValueError,match='MISSING_MEMBER'):verify(bundle(None))
