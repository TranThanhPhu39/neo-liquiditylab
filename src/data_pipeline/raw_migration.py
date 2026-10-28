"""Idempotent raw-to-canonical migration; raw data remains immutable."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from src.data_pipeline.normalization import normalize
from src.data_pipeline.unit_contracts import UnitsNotVerified


def _scaled(value, multiplier):
    if value is None:
        return None
    try:
        result = Decimal(str(value))*multiplier
        return str(result) if result.is_finite() else value
    except InvalidOperation:
        # Keep ATO/ATC and other non-numeric values visible to validation.
        return value


def migrate_raw(storage, contract, batch_ids=None, *, symbols=None):
    batches = storage.raw_batches(provider=contract.provider,batch_ids=batch_ids)
    if batch_ids is not None:
        requested = set(batch_ids)
        batches = [b for b in batches if b["id"] in requested]
        if requested!={b["id"] for b in batches}:
            raise ValueError("Không tìm thấy đầy đủ batch yêu cầu")
    plan,skipped = [],0
    for batch in batches:
        if (batch["provider"],batch["upstream_source"],batch["provider_version"]) != (
                contract.provider,contract.upstream_source,contract.provider_version):
            raise UnitsNotVerified("Batch khác source/version đã thẩm định")
        for raw in batch["payload"]["rows"]:
            symbol = raw["symbol"]
            if symbols is not None and symbol not in symbols:
                continue
            existing = storage.connection.execute("SELECT contract_hash FROM snapshot_lineage WHERE raw_batch_id=? AND symbol=?",
                (batch["id"],symbol)).fetchone()
            if existing:
                if existing["contract_hash"]!=contract.contract_hash:
                    raise ValueError("Batch đã dùng hồ sơ đơn vị khác; cần quy trình hiệu chỉnh riêng, không ghi đè")
                skipped+=1
                continue
            row = dict(raw)
            for key,value in raw.items():
                if key.endswith("price") or "_price_" in key:
                    row[key] = _scaled(value,contract.price_multiplier)
                elif "_vol_" in key:
                    row[key] = _scaled(value,contract.volume_multiplier)
            row.update(last_price=_scaled(raw.get("close_price"),contract.price_multiplier),
                       event_id=f"raw:{batch['id']}:{symbol}",source_timestamp=batch["payload"].get("source_timestamp"),
                       market_session=batch["payload"].get("market_session","UNKNOWN"))
            snapshot = normalize(row,provider=contract.provider,upstream_source=contract.upstream_source,
                provider_version=contract.provider_version,capture_method="raw_migration",
                collected_at=datetime.fromisoformat(batch["received_at_utc"]))
            plan.append((batch,symbol,replace(snapshot,raw=dict(raw))))
    inserted = 0
    qualities = {"VALID":0,"PARTIAL":0,"INVALID":0}
    with storage.transaction():
        storage.connection.execute("BEGIN IMMEDIATE")
        storage.connection.execute("INSERT OR IGNORE INTO unit_contracts VALUES(?,?,?)",(
            contract.contract_hash,json.dumps(contract.payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        for batch,symbol,snapshot in plan:
            # One transaction for snapshot/levels/raw/lineage and contract registration.
            if not storage.insert(snapshot,run_id=batch["run_id"]):
                raise ValueError("Snapshot đã tồn tại nhưng thiếu lineage; cần đối chiếu thủ công")
            identity = [snapshot.provider,snapshot.upstream_source,snapshot.symbol,snapshot.event_id]
            event_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            snapshot_id = storage.connection.execute("SELECT id FROM orderbook_snapshots WHERE event_key=?",(event_key,)).fetchone()[0]
            storage.connection.execute("INSERT INTO snapshot_lineage VALUES(?,?,?,?,?)",(
                batch["id"],symbol,contract.contract_hash,snapshot_id,datetime.now(timezone.utc).isoformat()))
            inserted+=1
            qualities[snapshot.data_quality_status]+=1
    return {"status":"NORMALIZED","inserted":inserted,"skipped_existing":skipped,"quality_counts":qualities,
            "contract_hash":contract.contract_hash,"raw_modified":False,
            "note":"Giữ timestamp nhận gốc. Source timestamp thiếu không được tạo từ thời điểm migration."}
