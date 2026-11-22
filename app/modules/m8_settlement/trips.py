from collections import defaultdict
from datetime import timedelta

from app.config import GROSS_KINDS, SHIFT_TICKET_GRACE_MIN, TARE_KINDS, TICKET_TOLERANCE
from app.evidence import Issue
from app.modules.m8_settlement.weighings import KIND_ORDER


def group_trips(weighings):
    """以过磅语义定边界：进磅开趟、回磅闭趟；不按时间间隔合并相邻两趟。

    按源文件物理顺序处理，使晚到复磅保留其晚到标记；同一趟仍开放时到达的
    复磅归入本趟，闭趟之后又没有新进磅的记录判为无主过磅。
    """
    grouped = defaultdict(list)
    for item in sorted(weighings, key=lambda row: row.source.line):
        grouped[item.pass_id].append(item)

    trips, order_issues = [], []
    for pass_id, queue in grouped.items():
        current, serial, stage = None, 0, 0
        for weighing in queue:
            rank = KIND_ORDER[weighing.kind]
            if current is None:
                if rank != 1:
                    order_issues.append(Issue("WEIGHING_ORPHAN", weighing.source, pass_id))
                    continue
                serial += 1
                current = {"id": f"{pass_id}/T{serial:02d}", "pass_id": pass_id,
                           "weighings": [weighing], "max_stage": 1}
                stage = 1
                continue
            if rank <= stage:
                # 同类型复磅且版次更新，视为晚到的纠错复磅，归入开放趟次保留全部依据。
                newer_regross = (
                    weighing.kind == "regross"
                    and all(item.kind != "regross" or weighing.revision > item.revision
                            for item in current["weighings"])
                )
                if not newer_regross:
                    order_issues.append(Issue("WEIGHING_BAD_ORDER", weighing.source, pass_id,
                                              (current["weighings"][-1].source.ref,)))
                    continue
            current["weighings"].append(weighing)
            current["max_stage"] = max(current["max_stage"], rank)
            stage = current["max_stage"]
            if weighing.kind in TARE_KINDS:
                trips.append(current)
                current, stage = None, 0
        if current is not None:
            # 趟次内按时刻重排，晚到记录仍带 late 标记且依据完整保留。
            current["weighings"].sort(key=lambda item: (item.time, item.source.line))
            trips.append(current)
    return trips, order_issues


def verify_tickets(weighings, passes):
    """同磅单号同版次冲突即挂账；车牌、签发时间窗和轮载差额逐一互证。"""
    votes = defaultdict(list)
    for weighing in weighings:
        votes[(weighing.ticket_id, weighing.revision)].append(weighing)
    conflicts = {key for key, rows in votes.items()
                 if len({(row.kg, row.time) for row in rows}) > 1}

    checks = {}
    for weighing in weighings:
        codes, related = [], []
        crossing = passes[weighing.pass_id]
        key = (weighing.ticket_id, weighing.revision)
        if key in conflicts:
            codes.append("TRIP_TICKET_CONFLICT")
            related.extend(row.source.ref for row in votes[key] if row is not weighing)
        if weighing.time < crossing.start or weighing.time - crossing.end >= timedelta(
                minutes=SHIFT_TICKET_GRACE_MIN):
            codes.append("TICKET_TIME")
        checks[weighing.id] = {"codes": codes, "refs": tuple(dict.fromkeys(related))}
    return checks, conflicts, votes


def settle(trips, dossiers, ticket_checks, passes):
    rows = []
    for trip in sorted(trips, key=lambda item: item["id"]):
        codes = set()
        entries, gross_choice, tare_choice = [], None, None
        for weighing in trip["weighings"]:
            dossier = dossiers[weighing.id]
            ticket = ticket_checks[weighing.id]
            entry_codes = list(ticket["codes"])
            if dossier["incomplete"]:
                entry_codes.append("TRIP_WHEEL_GAP")
            if dossier["severe"]:
                entry_codes.append("TRIP_SEVERE_BALANCE")
            difference = weighing.kg - dossier["wheel_kg"]
            if dossier["wheel_kg"] > 0 and abs(difference) > TICKET_TOLERANCE:
                entry_codes.append("TRIP_TICKET_UNVERIFIED")
            eligible = not entry_codes
            role = "gross" if weighing.kind in GROSS_KINDS else "tare"
            if eligible:
                if role == "gross" and (gross_choice is None or weighing.time > gross_choice[0].time):
                    gross_choice = (weighing, dossier)
                if role == "tare" and tare_choice is None:
                    tare_choice = (weighing, dossier)
            entries.append({
                "weighing_id": weighing.id,
                "kind": weighing.kind,
                "time": weighing.time,
                "ticket_id": weighing.ticket_id,
                "revision": weighing.revision,
                "ticket_kg": weighing.kg,
                "wheel_kg": dossier["wheel_kg"],
                "difference_kg": difference,
                "late": weighing.late,
                "eligible": eligible,
                "chosen": False,
                "codes": entry_codes,
                "ticket_refs": ticket["refs"],
                "source_ref": weighing.source.ref,
                "axis_ids": [axis["id"] for axis in dossier["axes"]],
                "wheel_refs": dossier["wheel_refs"],
                "issue_refs": dossier["issue_refs"],
            })
            codes.update(entry_codes)

        if gross_choice is None:
            codes.add("TRIP_NO_GROSS")
        if tare_choice is None:
            codes.add("TRIP_NO_TARE")
        if not any(item["kind"] in TARE_KINDS for item in entries):
            codes.add("TRIP_OPEN")

        net_kg = None
        if gross_choice is not None and tare_choice is not None:
            net_kg = gross_choice[0].kg - tare_choice[0].kg
        status = "settle" if not codes and net_kg is not None and net_kg > 0 else "hold"
        rows.append({
            "trip_id": trip["id"],
            "pass_id": trip["pass_id"],
            "plate": passes[trip["pass_id"]].plate,
            "status": status,
            "codes": sorted(codes),
            "gross_id": gross_choice[0].id if gross_choice else None,
            "tare_id": tare_choice[0].id if tare_choice else None,
            "gross_kg": gross_choice[0].kg if gross_choice else None,
            "tare_kg": tare_choice[0].kg if tare_choice else None,
            "net_kg": net_kg,
            "entries": entries,
            "pass_ref": passes[trip["pass_id"]].source.ref,
        })
    return rows


def orphan_rows(orphan_issues, weighings):
    by_ref = defaultdict(list)
    for issue in orphan_issues:
        by_ref[issue.source.ref].append(issue.code)
    rows = []
    for issue in orphan_issues:
        weighing = next(item for item in weighings if item.source.ref == issue.source.ref)
        rows.append({
            "weighing_id": weighing.id,
            "pass_id": weighing.pass_id,
            "kind": weighing.kind,
            "time": weighing.time,
            "ticket_id": weighing.ticket_id,
            "revision": weighing.revision,
            "ticket_kg": weighing.kg,
            "late": weighing.late,
            "codes": by_ref[issue.source.ref],
            "source_ref": issue.source.ref,
        })
    dedup = {row["weighing_id"]: row for row in rows}
    return [dedup[key] for key in sorted(dedup)]
