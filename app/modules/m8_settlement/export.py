import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.modules.m7_batches.export import csv_bytes
from app.reasons import REASONS


def _cell(value):
    if value is None:
        return ""
    return value


def trip_rows(results):
    for row in results["settlement"]["rows"]:
        yield (
            row["trip_id"], row["pass_id"], row["plate"],
            "结算" if row["status"] == "settle" else "暂挂",
            _cell(row["gross_id"]), _cell(row["gross_kg"]),
            _cell(row["tare_id"]), _cell(row["tare_kg"]),
            _cell(row["net_kg"]),
            " / ".join(row["codes"]),
            " / ".join(REASONS[code] for code in row["codes"]),
            row["pass_ref"],
        )


def weighing_rows(results):
    for row in results["settlement"]["rows"]:
        for entry in row["entries"]:
            yield (
                row["trip_id"], entry["weighing_id"],
                {"gross": "进磅", "regross": "复磅", "tare": "回磅"}[entry["kind"]],
                entry["time"], entry["ticket_id"], entry["revision"],
                entry["ticket_kg"], entry["wheel_kg"], entry["difference_kg"],
                "是" if entry["eligible"] else "否",
                "是" if entry["chosen"] else "否",
                "晚到" if entry["late"] else "",
                " / ".join(entry["codes"]),
                entry["source_ref"],
                " / ".join(entry["wheel_refs"]),
                " / ".join(entry["issue_refs"]),
                " / ".join(entry["ticket_refs"]),
            )


def orphan_rows(results):
    for orphan in results["settlement"]["orphans"]:
        yield (
            orphan["weighing_id"], orphan["pass_id"],
            {"gross": "进磅", "regross": "复磅", "tare": "回磅"}[orphan["kind"]],
            orphan["time"], orphan["ticket_id"], orphan["revision"], orphan["ticket_kg"],
            "晚到" if orphan["late"] else "",
            " / ".join(orphan["codes"]),
            " / ".join(REASONS[code] for code in orphan["codes"]),
            orphan["source_ref"],
        )


def export_shift(manifest, results):
    ledger = results["settlement"]["ledger"]
    files = {
        "班结清单.json": json.dumps(
            {"manifest": manifest, "ledger": ledger}, indent=2, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8"),
        "趟次结算.csv": csv_bytes(
            ("趟次", "车次", "车牌", "状态", "计费进磅", "计费毛重kg", "计费回磅", "皮重kg",
             "计费净重kg", "原因码", "说明", "车次档案"),
            trip_rows(results),
        ),
        "过磅归属.csv": csv_bytes(
            ("趟次", "过磅号", "类型", "过磅时刻UTC", "磅单号", "版次", "磅单总重kg", "轮载合计kg",
             "差额kg", "可采信", "计费选中", "晚到标记", "原因码", "过磅源行", "轮载证据",
             "缺证依据", "磅单冲突证据"),
            weighing_rows(results),
        ),
        "无主过磅.csv": csv_bytes(
            ("过磅号", "车次", "类型", "过磅时刻UTC", "磅单号", "版次", "总重kg", "晚到标记",
             "原因码", "说明", "源行"),
            orphan_rows(results),
        ),
    }
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
    return output.getvalue()
