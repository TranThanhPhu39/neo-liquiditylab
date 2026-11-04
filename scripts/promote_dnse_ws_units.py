"""Recompute private evidence before writing a metadata-only ACB WS contract."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from config.settings import ROOT
from scripts.normalize_dnse_history import prepare_rules
from scripts.scheduled_collection import write_json
from src.data_pipeline.dnse_normalization import load_rules
from src.data_pipeline.dnse_ws_review import compare


def promote(comparison,*,root=ROOT):
    comparison=Path(comparison).resolve();root=Path(root)
    saved=json.loads(comparison.read_text(encoding='utf-8'))
    rules=load_rules(prepare_rules(root))
    if (saved.get('symbol'),saved.get('market_id'),saved.get('board_id'))!=('ACB','STO','G1') or saved.get('rest_unit_rule_hash')!=rules.rule_hash:
        raise ValueError('WS_PROMOTION_VERIFIED_REFERENCE_SCOPE_REQUIRED')
    if 'ACB' not in rules.verified_symbols or 'STO' not in rules.verified_markets:
        raise ValueError('WS_PROMOTION_REFERENCE_NOT_VERIFIED')
    def artifact(item):
        path=(comparison.parent/item['path']).resolve()
        if path.parent!=comparison.parent or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('WS_PROMOTION_EVIDENCE_CHECKSUM_FAILED')
        return gzip.decompress(path.read_bytes())
    ws=[json.loads(line) for line in artifact(saved['ws_artifact']).splitlines()]
    quotes=[]
    for item in saved['rest_artifacts']:
        observation=json.loads(artifact(item))
        if observation.get('path')!='/price/ACB/quotes' or observation.get('method')!='GET':
            raise ValueError('WS_PROMOTION_READONLY_REFERENCE_REQUIRED')
        quotes.extend(observation['payload']['quotes'])
    checked=compare(ws,quotes,symbol='ACB',market='STO',price_multiplier=rules.price_multiplier,
        volume_multiplier=rules.volume_multiplier,rest_unit_verified=True)
    factors=checked['factor_candidates']
    if (checked['status']!='EMPIRICAL_TOP3_CROSSCHECK_PASSED' or len(factors)!=1
            or factors[0]['price_multiplier_to_vnd']!='1E+3' or factors[0]['volume_multiplier_to_shares']!='1E+1'
            or checked['counts'].get('six_pairs_match')!=checked['counts']['ws_events']):
        raise ValueError('WS_PROMOTION_ALL_OBSERVATIONS_MUST_MATCH')
    contract=dict(format='dnse-ws-units-v1',provider='dnse',scope=dict(symbol='ACB',market_id='STO',board_id='G1'),
        price_multiplier_to_vnd='1000',volume_multiplier_to_shares='10',
        verification_status='EMPIRICALLY_VERIFIED_VIA_VERIFIED_REST',
        policy='COMPLETE_TOP3_EACH_MESSAGE_NO_CARRY_FORWARD',full_ten_level_semantics_verified=False,
        evidence=dict(source_day=ws[0]['source_local_date'],events=checked['counts']['six_pairs_match'],
            distinct_matched_clocks=checked['distinct_matched_clocks'],
            rest_unit_rule_hash=rules.rule_hash,ws_sha256=saved['ws_artifact']['sha256'],
            rest_sha256=[p['sha256'] for p in saved['rest_artifacts']],
            comparison_sha256=hashlib.sha256(comparison.read_bytes()).hexdigest()))
    destination=root/'config/unit_contract.dnse-ws-v1.json'
    write_json(destination,contract)
    return dict(state='SCOPED_CONTRACT_PROMOTED',symbol='ACB',events=checked['counts']['six_pairs_match'],
        distinct_matched_clocks=checked['distinct_matched_clocks'],contract=str(destination),public_quote_payload=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(promote(args.comparison)))
