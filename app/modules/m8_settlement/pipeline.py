from app.evidence import wire
from app.manifest import clock
from app.modules.m8_settlement.service import settle_shift
from app.reasons import REASONS


def run_shift_pipeline(sources, events=None):
    results = settle_shift(sources, events=events)
    ledger = results["settlement"]["ledger"]
    if not ledger["balanced"]:
        raise RuntimeError("轮载触发流水未守恒")
    for issue in results["quarantine"]:
        if events is not None and issue.code not in {"REPLAY_ID", "BOUNCE"}:
            events.append((clock(), "warn", issue.code,
                           f"{issue.source.ref} · {REASONS[issue.code]}", None))
    if events is not None:
        events.append((clock(), "ok", "SHIFT_LEDGER",
                       f"趟次级核平 · 结算 {ledger['settled']} 趟 · 暂挂 {ledger['held']} 趟", None))
    return wire(results)
