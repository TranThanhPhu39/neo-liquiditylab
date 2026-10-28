from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.data_pipeline.raw_collection import collect_raw_once
from src.data_pipeline.raw_migration import migrate_raw
from src.data_pipeline.storage import Storage
from src.data_pipeline.unit_contracts import load_unit_contract, UnitsNotVerified
from src.finance.execution_simulator import simulate_execution
from src.finance.stress_testing import compare_scenario
from src.models.scenario import Scenario
from src.finance.data_verification import verify_raw_prices
from scripts.finalize_collection import close_collection
from scripts.validate_normalized_sample import validate_sample


def contract_file(tmp_path, **changes):
    evidence = tmp_path/"synthetic_unit_evidence.txt"
    evidence.write_text("SYNTHETIC TEST EVIDENCE ONLY: not a vendor unit declaration.",encoding="utf-8")
    record = {"provider":"vnstock","upstream_source":"kbs","provider_version":"5.2.1",
        "verification_status":"VERIFIED","price_multiplier_to_vnd":"1","volume_multiplier_to_shares":"1",
        "verified_at":"2026-10-20T10:00:00+07:00","evidence_type":"official_versioned_contract",
        "review_note":"Synthetic contract fixture for offline tests only.",
        "evidence":{"source_url":"https://example.com/synthetic-test","local_path":evidence.name,
                    "sha256":hashlib.sha256(evidence.read_bytes()).hexdigest()},**changes}
    path = tmp_path/"contract.json"
    path.write_text(json.dumps(record),encoding="utf-8")
    return path


def seed(db,book,row=None):
    raw = row or dict(book.to_record(),close_price=str(book.last_price))
    response = {"version":"5.2.1","account":{"authenticated":True},"rows":[raw],
                "received_at_utc":book.collected_at_utc.isoformat(),"missing_columns":[]}
    collect_raw_once(db,["HPG"],transport=lambda _:response)


def test_migration_preserves_raw_time_and_financial_example(tmp_path,book):
    contract = load_unit_contract(contract_file(tmp_path))
    with Storage(tmp_path/"data.db") as db:
        seed(db,book)
        before = json.dumps(db.raw_batches(),sort_keys=True)
        result = migrate_raw(db,contract)
        assert result["inserted"]==1 and result["quality_counts"]["VALID"]==1
        assert json.dumps(db.raw_batches(),sort_keys=True)==before
        snapshot = db.query(provider="vnstock")[0]
        assert snapshot.collected_at_utc==book.collected_at_utc and snapshot.source_timestamp is None
        assert snapshot.capture_method=="raw_migration"
        r = simulate_execution(snapshot,3000)
        assert r.gross_value==89875000 and r.book_walking_cost==125000
        stress = compare_scenario(snapshot,4000,Scenario(".5"))
        assert stress["stressed"].executed_quantity==1750
        assert migrate_raw(db,contract)["skipped_existing"]==1
        assert len(db.query(provider="vnstock"))==1
        assert db.connection.execute("SELECT COUNT(*) FROM snapshot_lineage").fetchone()[0]==1
        checked = validate_sample(db)
        assert checked["execution_cases_checked"]==2 and checked["stress_cases_checked"]==1
        assert checked["portfolio_positions"].startswith("SIMULATED")


def test_explicit_price_and_volume_factors(tmp_path,book):
    contract = load_unit_contract(contract_file(tmp_path,price_multiplier_to_vnd="1000",volume_multiplier_to_shares="10"))
    row = dict(book.to_record(),close_price="30")
    for key in list(row):
        if "_price_" in key or key=="reference_price":
            row[key] = str(Decimal(row[key])/1000)
        elif "_vol_" in key:
            row[key] = str(Decimal(row[key])/10)
    with Storage(tmp_path/"scaled.db") as db:
        seed(db,book,row)
        migrate_raw(db,contract)
        s = db.query()[0]
        assert s.bids==book.bids and s.last_price==book.last_price


@pytest.mark.parametrize("change",[
    {"verification_status":"UNVERIFIED"}, {"volume_multiplier_to_shares":"NaN"},
    {"volume_multiplier_to_shares":"0"}, {"price_multiplier_to_vnd":"-1"},
    {"verified_at":"2026-10-20T10:00:00"}, {"provider_version":"5.0.1"},
    {"evidence_type":"guessed_from_plausible_numbers"}, {"review_note":""}])
def test_contract_blocks_incomplete_or_wrong_evidence(tmp_path,change):
    with pytest.raises(UnitsNotVerified):
        load_unit_contract(contract_file(tmp_path,**change))


def test_evidence_checksum_and_changed_contract_are_rejected(tmp_path,book):
    path = contract_file(tmp_path)
    contract = load_unit_contract(path)
    (tmp_path/"synthetic_unit_evidence.txt").write_text("tampered",encoding="utf-8")
    with pytest.raises(UnitsNotVerified,match="checksum"):
        load_unit_contract(path)
    with Storage(tmp_path/"change.db") as db:
        seed(db,book)
        migrate_raw(db,contract)
        changed = load_unit_contract(contract_file(tmp_path,volume_multiplier_to_shares="10"))
        with pytest.raises(ValueError,match="hồ sơ đơn vị khác"):
            migrate_raw(db,changed)


def test_migration_rolls_back_snapshot_if_lineage_write_fails(tmp_path,book):
    contract = load_unit_contract(contract_file(tmp_path))
    with Storage(tmp_path/"rollback.db") as db:
        seed(db,book)
        db.connection.execute("CREATE TRIGGER reject_lineage BEFORE INSERT ON snapshot_lineage BEGIN SELECT RAISE(ABORT,'test'); END")
        with pytest.raises(sqlite3.IntegrityError):
            migrate_raw(db,contract)
        assert db.query()==[]
        assert db.connection.execute("SELECT COUNT(*) FROM unit_contracts").fetchone()[0]==0
        assert len(db.raw_batches())==1


def test_missing_level_is_not_filled_in_during_migration(tmp_path,book):
    contract = load_unit_contract(contract_file(tmp_path))
    with Storage(tmp_path/"partial.db") as db:
        row = dict(book.to_record(),close_price="30000",bid_price_1="ATO")
        seed(db,book,row)
        result = migrate_raw(db,contract)
        assert result["quality_counts"]["PARTIAL"]==1
        snapshot = db.query()[0]
        assert snapshot.bids[0].price is None
        assert simulate_execution(snapshot,100).book_walking_cost is None


def test_price_only_actual_schema_does_not_require_volume_assumption(tmp_path,book):
    with Storage(tmp_path/"prices.db") as db:
        row = dict(book.to_record(),close_price="30000",bid_vol_1="unknown-unit-value")
        seed(db,book,row)
        report = verify_raw_prices(db)
        assert report["price_checks_passed"]==1
        check = report["checks"][0]
        assert check["spread_source_price_units"]=="50"
        assert Decimal(check["relative_spread"])==Decimal(50)/Decimal(30025)
        assert report["execution_stress_portfolio_on_raw"]=="BLOCKED_UNITS_UNVERIFIED"
        assert db.query()==[]


def test_bad_raw_prices_do_not_create_diagnostic_results(tmp_path,book):
    with Storage(tmp_path/"bad_prices.db") as db:
        seed(db,book,dict(book.to_record(),ask_price_1="ATO"))
        report = verify_raw_prices(db)
        assert report["price_checks_failed"]==1
        assert report["checks"][0]["relative_spread"] is None


def test_post_collection_close_uses_real_backup_and_keeps_units_blocked(tmp_path,book):
    path = tmp_path/"close.db"
    with Storage(path) as db:
        seed(db,book)
    status = tmp_path/"status.json"
    status.write_text(json.dumps({"collector_state":"COMPLETED"}),encoding="utf-8")
    output = tmp_path/"close.json"
    report = close_collection(path,status,output,artifact_dir=tmp_path/"artifacts")
    assert report["status"]=="COMPLETED_RAW_ONLY" and report["symbol_observations"]==1
    assert report["integrity_check"]=="ok" and Path(report["backup_path"]).is_file()
    assert report["raw_to_normalized_status"]=="BLOCKED_UNITS_UNVERIFIED"
    assert report["price_checks_passed"]==1
