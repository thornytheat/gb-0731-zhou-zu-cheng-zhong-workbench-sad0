from collections import Counter
from decimal import Decimal

from app.manifest import clock
from app.modules.m1_intake.service import accept
from app.modules.m8_settlement.passes import shift_passes
from app.modules.m8_settlement.trips import group_trips, orphan_rows, settle, verify_tickets
from app.modules.m8_settlement.weighings import weighing_book
from app.modules.m8_settlement.wheels import build_dossiers

DUPLICATE_CODES = {"REPLAY_ID", "BOUNCE"}


def settle_shift(sources, events=None):
    def log(level, code, message, time=None):
        if events is not None:
            events.append((clock(), level, code, message, time))

    intake = accept(sources["triggers"])
    log("info", "SHIFT_INTAKE", f"{intake['file']} · {intake['raw']} 条轮载触发")
    passes = shift_passes(sources["passes"])
    weighings, weighing_issues = weighing_book(sources["weighings"], passes)
    log("info", "WEIGHINGS", f"过磅事件 {len(weighings)} 条 · 隔离 {len(weighing_issues)} 条")

    dossiers, wheel_issues, catalog = build_dossiers(intake, sources["certificates"], passes, weighings)
    trips, order_issues = group_trips(weighings)
    ticket_checks, conflicts, votes = verify_tickets(weighings, passes)
    rows = settle(trips, dossiers, ticket_checks, passes)

    # 最终计费依据回填：只有可结算趟次的毛重/皮重才允许带选中标记。
    for row in rows:
        chosen = {row["gross_id"], row["tare_id"]} if row["status"] == "settle" else set()
        for entry in row["entries"]:
            entry["chosen"] = entry["weighing_id"] in chosen

    # 轮载台账只对触发文件源行守恒；过磅事件问题独立计数。
    trigger_refs = {event.source.ref for event in intake["accepted"]}
    trigger_issues = [issue for issue in wheel_issues if issue.source.ref in trigger_refs]
    weighing_issue_refs = {
        issue.source.ref for issue in list(weighing_issues) + list(order_issues)
    }
    issues = list(weighing_issues) + list(wheel_issues) + list(order_issues)
    orphans = orphan_rows(order_issues, weighings)
    settled = [row for row in rows if row["status"] == "settle"]
    held = [row for row in rows if row["status"] == "hold"]
    axle_refs = {
        ref for dossier in dossiers.values() for axis in dossier["axes"]
        for ref in (axis["left_ref"], axis["right_ref"])
    }
    duplicate_refs = {issue.source.ref for issue in trigger_issues if issue.code in DUPLICATE_CODES}
    isolated_refs = {issue.source.ref for issue in trigger_issues if issue.code not in DUPLICATE_CODES}
    # 有效 = 全部原始源行减去重复与隔离；轴引用必须是有效集的子集。
    effective_refs = trigger_refs - duplicate_refs - isolated_refs
    effective, duplicates, isolated = len(effective_refs), len(duplicate_refs), len(isolated_refs)
    ledger = {
        "raw": intake["raw"],
        "effective": effective,
        "duplicates": duplicates,
        "isolated": isolated,
        "balanced": intake["raw"] == effective + duplicates + isolated
        and axle_refs <= effective_refs
        and effective_refs.isdisjoint(duplicate_refs | isolated_refs),
        "weighing_issues": len(weighing_issue_refs),
        "weighings": len(weighings),
        "trips": len(rows),
        "settled": len(settled),
        "held": len(held),
        "orphans": len(orphans),
        "net_kg": sum((row["net_kg"] for row in settled), Decimal("0.00")),
        "gross_kg": sum((row["gross_kg"] for row in settled), Decimal("0.00")),
        "tare_kg": sum((row["tare_kg"] for row in settled), Decimal("0.00")),
        "isolation_counts": dict(sorted(Counter(issue.code for issue in issues).items())),
        "ticket_conflicts": len(conflicts),
    }

    for row in settled:
        log("ok", "TRIP_SETTLE", f"{row['trip_id']} · 计费净重 {row['net_kg']} kg",
            next(entry["time"] for entry in row["entries"] if entry["weighing_id"] == row["tare_id"]))
    for row in held:
        log("warn", "TRIP_HOLD", f"{row['trip_id']} · 暂挂 · {'/'.join(row['codes'])}")
    for orphan in orphans:
        log("warn", orphan["codes"][0], f"{orphan['weighing_id']} · 无法归属趟次，证据保留", orphan["time"])

    return {
        "settlement": {
            "rows": rows,
            "orphans": orphans,
            "ticket_votes": [
                {"ticket_id": key[0], "revision": key[1],
                 "refs": [item.source.ref for item in group], "conflict": key in conflicts}
                for key, group in sorted(votes.items())
            ],
            "ledger": ledger,
        },
        "dossiers": list(dossiers.values()),
        "quarantine": sorted(issues, key=lambda issue: issue.source.line),
        "certificates": catalog,
        "passes": passes,
        "weighing_issues": weighing_issues,
    }
