from decimal import Decimal

import pytest

from app.evidence import wire
from app.modules.m8_settlement.pipeline import run_shift_pipeline
from app.modules.m8_settlement.service import settle_shift
from app.sources import read_sources
from app.config import ROOT


@pytest.fixture(scope="module")
def shift_sources():
    return read_sources(ROOT / "sample_data", "shift")


@pytest.fixture(scope="module")
def shift_result(shift_sources):
    return run_shift_pipeline(shift_sources)


def trip(result, trip_id):
    return next(row for row in result["settlement"]["rows"] if row["trip_id"] == trip_id)


def entry(result, weighing_id):
    for row in result["settlement"]["rows"]:
        for item in row["entries"]:
            if item["weighing_id"] == weighing_id:
                return item
    raise KeyError(weighing_id)


def test_trigger_ledger_conserves_source_lines(shift_result):
    ledger = shift_result["settlement"]["ledger"]
    assert ledger["balanced"] is True
    assert ledger["raw"] == ledger["effective"] + ledger["duplicates"] + ledger["isolated"]
    assert (ledger["raw"], ledger["effective"], ledger["duplicates"], ledger["isolated"]) == (59, 50, 1, 8)


def test_regross_corrects_gross_and_keeps_all_three_weighings(shift_result):
    row = trip(shift_result, "T101/T01")
    assert row["status"] == "settle"
    assert (row["gross_id"], row["tare_id"], row["net_kg"]) == ("SW-002", "SW-003", "5000.00")
    ids = [item["weighing_id"] for item in row["entries"]]
    assert ids == ["SW-001", "SW-002", "SW-003"]
    original = entry(shift_result, "SW-001")
    correction = entry(shift_result, "SW-002")
    assert original["chosen"] is False and correction["chosen"] is True
    assert original["eligible"] is True and correction["eligible"] is True
    # 被纠错的进磅及其轮载依据仍然完整保留。
    assert len(original["wheel_refs"]) == 4 and original["wheel_kg"] == "8100.00"


def test_adjacent_trips_never_merged_by_time_window(shift_result):
    first = trip(shift_result, "T102/T01")
    second = trip(shift_result, "T102/T02")
    assert (first["gross_id"], first["tare_id"], first["net_kg"]) == ("SW-004", "SW-005", "5000.00")
    assert (second["gross_id"], second["tare_id"], second["net_kg"]) == ("SW-006", "SW-007", "5100.00")


def test_settlement_requires_ticket_and_wheel_corroboration(shift_result):
    row = trip(shift_result, "T103/T01")
    assert row["status"] == "settle"
    for item in row["entries"]:
        assert item["difference_kg"] == "0.00"
        assert item["wheel_refs"]


def test_severe_balance_trip_is_held_without_billing_weight(shift_result):
    row = trip(shift_result, "T104/T01")
    assert row["status"] == "hold"
    assert "TRIP_SEVERE_BALANCE" in row["codes"]
    assert row["gross_id"] is None and row["tare_id"] is None and row["net_kg"] is None


def test_missing_wheel_and_revision_conflict_hold_trip(shift_result):
    row = trip(shift_result, "T105/T01")
    assert row["status"] == "hold"
    assert {"TRIP_WHEEL_GAP", "TRIP_TICKET_CONFLICT"} <= set(row["codes"])
    conflict_votes = [v for v in shift_result["settlement"]["ticket_votes"]
                      if v["ticket_id"] == "WT-105" and v["conflict"]]
    assert conflict_votes and len(conflict_votes[0]["refs"]) == 2


def test_open_trip_and_late_regross_retain_evidence(shift_result):
    row = trip(shift_result, "T106/T01")
    assert row["status"] == "hold"
    assert {"TRIP_OPEN", "TRIP_NO_TARE"} <= set(row["codes"])
    late = entry(shift_result, "SW-018")
    assert late["late"] is True and late["kind"] == "regross"
    # 晚到复磅无轮载依据时不能计费，但原始过磅与缺证依据都保留。
    assert late["chosen"] is False and "TRIP_WHEEL_GAP" in late["codes"]
    assert late["source_ref"] == "shift-weighings.csv:19"


def test_orphan_weighings_are_kept_outside_trips(shift_result):
    orphans = {item["weighing_id"]: item for item in shift_result["settlement"]["orphans"]}
    assert set(orphans) == {"SW-014", "SW-017"}
    assert orphans["SW-014"]["codes"] == ["WEIGHING_ORPHAN"]
    assert all(item["status"] in ("settle", "hold") for item in shift_result["settlement"]["rows"])


def test_bounce_is_duplicate_not_isolated(shift_result):
    ledger = shift_result["settlement"]["ledger"]
    assert ledger["isolation_counts"]["BOUNCE"] == 1
    bounce = next(item for item in shift_result["quarantine"] if item["code"] == "BOUNCE")
    assert bounce["kept_id"] == "ST103-2L"


def test_settlement_totals_match_settled_trips(shift_result):
    ledger = shift_result["settlement"]["ledger"]
    settled = [row for row in shift_result["settlement"]["rows"] if row["status"] == "settle"]
    total_net = sum((Decimal(row["net_kg"]) for row in settled), Decimal("0.00"))
    assert Decimal(ledger["net_kg"]) == total_net == Decimal("19100.00")
    assert (ledger["settled"], ledger["held"], ledger["trips"]) == (4, 3, 7)


def test_held_trips_never_carry_billing_choice(shift_result):
    for row in shift_result["settlement"]["rows"]:
        if row["status"] == "hold":
            assert all(item["chosen"] is False for item in row["entries"])
            assert row["net_kg"] is None
    chosen = [item for row in shift_result["settlement"]["rows"]
              for item in row["entries"] if item["chosen"]]
    # 4 个结算趟次各选一条毛重与一条皮重。
    assert len(chosen) == 8


def test_wire_output_is_json_safe(shift_sources):
    import json
    json.dumps(wire(settle_shift(shift_sources)), ensure_ascii=False)
