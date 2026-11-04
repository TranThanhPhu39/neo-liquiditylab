"""Export audited DNSE history into bounded, public, read-only web partitions."""
from contextlib import closing
from datetime import datetime
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re

from config.settings import ROOT, Settings
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_history import DNSEHistory
from src.models.orderbook import LOCAL_TZ

FORMAT = 'liquiditylab-cloud-v1'
SAMPLE_LIMIT = 240  # Last source event per six-minute bucket, at most 240/day.
ANCHORS = ('11:29', '14:20', '15:10')
FIELDS = {
    'symbol', 'exchange', 'provider', 'upstream_source', 'capture_method', 'provider_version',
    'market_session', 'data_quality_status', 'schema_version', 'event_id', 'unit_verification_status',
    'unit_contract_hash', 'collected_at_utc', 'source_timestamp', 'received_at_local', 'issues',
    'normalization_version', 'price_unit', 'volume_unit', 'source_local_time', 'local_date',
    'bid_observed_depth_shares', 'ask_observed_depth_shares', 'financial_eligible',
    *[f'{side}_{field}_{i}' for side in ('bid', 'ask') for field in ('price', 'vol') for i in (1, 2, 3)],
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def public_record(record):
    if record.get('provider') != 'dnse' or record.get('normalization_version') != 'dnse-canonical-v1':
        raise ValueError('CANONICAL_DNSE_REST_REQUIRED')
    return {k: v for k, v in record.items() if k in FIELDS}


def write_partition(out, day, label, payload):
    content = gzip.compress(canonical(payload), compresslevel=9, mtime=0)
    digest = hashlib.sha256(content).hexdigest()
    name = f'{day}/{label}-{digest[:16]}.json.gz'
    path = out/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return dict(path=name, sha256=digest, bytes=len(content), records=len(payload['records']))


def referenced_files(index):
    result = []
    for day in index['days']:
        for item in [day['market'], *day['groups'].values()]:
            name = item['path']
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}/(?:market|history-[0-9a-f])-[0-9a-f]{16}\.json\.gz', name):
                raise ValueError('UNSAFE_CLOUD_PARTITION_PATH')
            if not re.fullmatch('[0-9a-f]{64}', item['sha256']):
                raise ValueError('INVALID_CLOUD_PARTITION_DIGEST')
            result.append(item)
    return result


def export_data(store, out, rule_hash, days=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    before = (store.path.stat().st_size, store.path.stat().st_mtime_ns)
    previous = {}
    if (out/'index.json').exists():
        old = json.loads((out/'index.json').read_text(encoding='utf-8'))
        if old.get('format') == FORMAT:
            previous = {d['date']: d for d in old['days']}
    entries = []
    for day in days or store.dates():
        catalog = store.catalog(day)
        symbols = sorted(catalog)
        with closing(store.connect()) as db:
            archives = sorted(row['id'] for row in db.execute('SELECT id,settings FROM archives')
                              if json.loads(row['settings'])['date'] == day)
        signature = hashlib.sha256(canonical(dict(format=FORMAT, day=day, archives=archives,
            rule_hash=rule_hash, samples=SAMPLE_LIMIT, anchors=ANCHORS))).hexdigest()
        old = previous.get(day)
        if old and old.get('fingerprint') == signature:
            refs = referenced_files({'days': [old]})
            if all((out/r['path']).exists() and hashlib.sha256((out/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in refs):
                entries.append(old)
                continue
        market = {}
        for clock in ANCHORS:
            at = datetime.fromisoformat(f'{day}T{clock}:59.999999').replace(tzinfo=LOCAL_TZ)
            for r in store.reconstruct_at(symbols, day, at).values():
                market[r['event_id']] = public_record(r)
        if not market:
            continue
        cutoff = f'{day}T15:10:59.999999+07:00'
        payload = dict(format='liquiditylab-browser-v1', cutoff=cutoff,
            source='DNSE historical REST', reconstruction='ex_post_source_time',
            catalog={s: {'symbol': s, 'exchange': catalog[s]['exchange']} for s in symbols},
            records=sorted(market.values(), key=lambda r: (r['source_timestamp'], r['event_id'])))
        entry = dict(date=day, fingerprint=signature, catalog_symbols=len(symbols),
            preferred_time='14:20' if any(r['source_local_time'][11:16]>='13:00' for r in market.values()) else '11:29',
            market=write_partition(out, day, 'market', payload), groups={})
        groups = {format(i, 'x'): [] for i in range(16)}
        at = datetime.fromisoformat(cutoff)
        for symbol in symbols:
            group = format(sum(map(ord, symbol)) % 16, 'x')
            groups[group].extend(public_record(r) for r in store.sampled_records(symbol, day, at, limit=SAMPLE_LIMIT))
        for group, records in groups.items():
            entry['groups'][group] = write_partition(out, day, 'history-'+group,
                dict(records=sorted(records, key=lambda r: (r['symbol'], r['source_timestamp'], r['event_id']))))
        entries.append(entry)
        print(json.dumps({'exported_day': day, 'catalog_symbols': len(symbols),
                          'sampled_records': sum(r['records'] for r in entry['groups'].values())}), flush=True)
    if not entries:
        raise ValueError('NO_AUDITED_DATA_TO_PUBLISH')
    if before != (store.path.stat().st_size, store.path.stat().st_mtime_ns):
        raise ValueError('SOURCE_DATABASE_CHANGED_DURING_EXPORT')
    index = dict(format=FORMAT, latest_day=max(e['date'] for e in entries),
        generated_at=datetime.now(LOCAL_TZ).isoformat(), provider='dnse',
        normalization_version='dnse-canonical-v1', rule_hash=rule_hash,
        sampling=dict(bucket_seconds=360, market_cutoffs=list(ANCHORS),
                      note='Source-time historical samples; not realtime or complete tick history'),
        days=sorted(entries, key=lambda e: e['date']))
    write_json(out/'index.json', index)
    refs = referenced_files(index)
    return dict(state='EXPORTED', latest_day=index['latest_day'], days=len(entries),
                partitions=len(refs), compressed_bytes=sum(r['bytes'] for r in refs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Publish these market-data partitions publicly on the existing Pages site')
    args = parser.parse_args()
    source = json.loads((ROOT/'data/processed/dnse/manifest.json').read_text(encoding='utf-8'))
    if source['state']!='NORMALIZED_AND_AUDITED' or source['quarantined'] or source['finance_checks']['state']!='PASSED':
        raise ValueError('AUDITED_NORMALIZED_ARCHIVE_REQUIRED')
    with exclusive_job(ROOT/'data/control/dnse_update.lock'):
        result = export_data(DNSEHistory(Settings().dnse_database_path), ROOT/'web/data', source['rule_hash'])
    if args.publish:
        from scripts.github_pages import prepare, publish
        from scripts.github_api import GitHubAPI
        api = GitHubAPI()
        base, head, tree, changed = prepare(api)
        result['publication'] = publish(api, base, head, tree, changed)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
