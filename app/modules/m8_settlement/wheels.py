from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from app.config import MAX_WHEEL_KG
from app.evidence import Issue
from app.modules.m2_calibration.certificates import certificates
from app.modules.m3_debounce.service import debounce
from app.modules.m4_axles.pair import match
from app.modules.m5_balance.thresholds import grade
from app.modules.m8_settlement.passes import ShiftPass
from app.values import rounded


@dataclass(frozen=True)
class ShiftWheel:
    trigger: object
    kg: Decimal
    cert: str
    cert_ref: str


def calibrate_wheels(events, catalog, passes):
    by_side = {side: [item for item in catalog if item.side == side] for side in ("L", "R")}
    wheels, issues = [], []
    for event in events:
        crossing = passes.get(event.pass_id)
        if crossing is None:
            issues.append(Issue("PASS_UNKNOWN", event.source, event.pass_id))
            continue
        if not crossing.start <= event.time <= crossing.end:
            issues.append(Issue("OUTSIDE_PASS", event.source, event.pass_id, (crossing.source.ref,)))
            continue
        eligible = [item for item in by_side[event.side] if item.start <= event.time < item.end]
        if len(eligible) != 1:
            code = "CAL_OVERLAP" if eligible else "CAL_MISSING"
            issues.append(Issue(code, event.source, event.pass_id,
                                tuple(item.source.ref for item in eligible)))
            continue
        chosen = eligible[0]
        kg = rounded(event.raw_kg * chosen.gain + chosen.bias_kg)
        if not 0 < kg <= MAX_WHEEL_KG:
            issues.append(Issue("WHEEL_RANGE", event.source, event.pass_id, (chosen.source.ref,)))
            continue
        wheels.append(ShiftWheel(event, kg, chosen.id, chosen.source.ref))
    return wheels, issues


def assign_wheels(kept, weighings):
    """按过磅时刻切分：轮载归入其时刻之前最近一次过磅，不使用时间窗硬合。"""
    by_pass = defaultdict(list)
    for wheel in kept:
        by_pass[wheel.trigger.pass_id].append(wheel)
    segments, stray = {}, []
    for pass_id, wheels in by_pass.items():
        queue = sorted(wheels, key=lambda item: (item.trigger.time, item.trigger.source.line))
        marks = sorted(
            (item for item in weighings if item.pass_id == pass_id),
            key=lambda item: (item.time, item.source.line),
        )
        cursor = 0
        for weighing in marks:
            begin = cursor
            while cursor < len(queue) and queue[cursor].trigger.time <= weighing.time:
                cursor += 1
            segments[weighing.id] = queue[begin:cursor]
        stray.extend(queue[cursor:])
    return segments, stray


def build_dossiers(intake, cert_source, passes, weighings):
    catalog = certificates(cert_source)
    wheels, cal_issues = calibrate_wheels(intake["accepted"], catalog, passes)
    pulse = debounce(intake["accepted"], wheels)
    excluded = {issue.source.ref for issue in pulse["issues"]}
    kept = [wheel for wheel in pulse["kept"] if wheel.trigger.source.ref not in excluded]
    segments, stray = assign_wheels(kept, weighings)
    issues = (list(intake["issues"]) + list(cal_issues)
              + list(pulse["issues"]) + list(pulse["duplicates"]))
    for wheel in stray:
        issues.append(Issue("WHEELS_INCOMPLETE", wheel.trigger.source, wheel.trigger.pass_id))

    dossiers = {}
    for weighing in weighings:
        crossing = ShiftPass(weighing.id, weighing.pass_id, weighing.time, weighing.time, None, weighing.source)
        pairs, pair_issues, _ = match(segments.get(weighing.id, []), crossing)
        issues.extend(pair_issues)
        axes, severe, wheel_refs, cert_refs = [], False, [], []
        for index, pair in enumerate(pairs, 1):
            level, percent = grade(pair.left.kg, pair.right.kg)
            severe = severe or level == "severe"
            axis_id = f"{weighing.id}/A{index:02d}"
            axes.append({
                "id": axis_id,
                "time": pair.time,
                "skew_ms": pair.skew_ms,
                "left_kg": pair.left.kg,
                "right_kg": pair.right.kg,
                "total_kg": pair.left.kg + pair.right.kg,
                "level": level,
                "percent": percent,
                "left_ref": pair.left.trigger.source.ref,
                "right_ref": pair.right.trigger.source.ref,
                "left_cert_ref": pair.left.cert_ref,
                "right_cert_ref": pair.right.cert_ref,
            })
            wheel_refs.extend((pair.left.trigger.source.ref, pair.right.trigger.source.ref))
            cert_refs.extend((pair.left.cert_ref, pair.right.cert_ref))
        wheel_kg = sum((axis["total_kg"] for axis in axes), Decimal("0.00"))
        segment = segments.get(weighing.id, [])
        dossiers[weighing.id] = {
            "weighing_id": weighing.id,
            "axes": axes,
            "wheel_kg": wheel_kg,
            "severe": severe,
            "incomplete": bool(pair_issues) or not segment or len(segment) != 2 * len(pairs),
            "wheel_refs": wheel_refs,
            "cert_refs": cert_refs,
            "issue_refs": [item.source.ref for item in pair_issues],
        }
    return dossiers, issues, catalog
