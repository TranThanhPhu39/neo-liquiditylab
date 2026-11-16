"""Revalidate unit metadata while preserving every market observation field."""
from dataclasses import replace
from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.data_pipeline.validation import validate

REVISION_FIELDS={'issues','unit_verification_status','unit_contract_hash','financial_eligible','data_quality_status'}


def revise_record(record,rules,prior_hash):
    if (record.get('normalization_version')!='dnse-canonical-v1' or
        record.get('unit_contract_hash')!=prior_hash or record.get('market_id')!='STO' or
        record.get('board_id')!='G1' or record.get('symbol') not in rules.verified_symbols or
        record.get('market_id') not in rules.verified_markets):
        raise ValueError('UNIT_REVISION_SCOPE_MISMATCH')
    issues=tuple(i for i in record['issues'] if i!='UNVERIFIED_DNSE_UNIT_SCOPE')
    snap=validate(replace(snapshot_from_record(record),issues=issues,
        unit_verification_status='EMPIRICALLY_VERIFIED',unit_contract_hash=rules.rule_hash))
    # A unit certificate must never clear an independently invalid observation.
    if record['data_quality_status']=='INVALID':snap=replace(snap,data_quality_status='INVALID')
    revised=dict(record,issues=list(snap.issues),unit_verification_status=snap.unit_verification_status,
        unit_contract_hash=rules.rule_hash,data_quality_status=snap.data_quality_status,
        financial_eligible=snap.data_quality_status!='INVALID' and
            snap.market_session in ('DNSE_CONTINUOUS_AM','DNSE_CONTINUOUS_PM') and
            any(level.usable for level in snap.bids+snap.asks))
    if any(revised[k]!=v for k,v in record.items() if k not in REVISION_FIELDS):
        raise ValueError('UNIT_REVISION_OBSERVATION_CHANGED')
    return revised
