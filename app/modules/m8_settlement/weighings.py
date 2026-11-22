from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.errors import DomainError
from app.evidence import Issue, SourceRow
from app.modules.m1_intake.reader import read_table
from app.values import instant, mass, rounded, token

HEADER = ("过磅号", "车次", "过磅类型", "磅单号", "版次", "过磅时间", "总重", "单位", "备注")
KINDS = {"in": "gross", "reweigh": "regross", "out": "tare"}
KIND_ORDER = {"gross": 1, "regross": 2, "tare": 3}


@dataclass(frozen=True)
class Weighing:
    id: str
    pass_id: str
    kind: str
    ticket_id: str
    revision: int
    time: datetime
    kg: Decimal
    source: SourceRow
    late: bool


def weighing_book(source, passes):
    table = read_table(source, HEADER)
    book, issues = [], []
    highwater = {}
    for row in table.rows:
        pass_id = table.field(row, "车次")
        try:
            if len(row.cells) != len(HEADER):
                raise DomainError("BAD_COLUMNS")
            data = table.columns(row)
            kind_label = token(data["过磅类型"]).casefold()
            kind = KINDS.get(kind_label)
            if kind is None:
                raise DomainError("WEIGHING_BAD_KIND")
            revision_text = token(data["版次"])
            if not revision_text.isascii() or not revision_text.isdigit() or int(revision_text) == 0:
                raise DomainError("BAD_VALUE")
            time = instant(data["过磅时间"])
            kg = rounded(mass(data["总重"], data["单位"]))
            if kg <= 0:
                raise DomainError("BAD_VALUE")
            # 晚到：源文件里后到、但时刻早于该车次此前任一记录（物理乱序）。
            mark = highwater.get(pass_id)
            late = mark is not None and time < mark
            highwater[pass_id] = time if mark is None else max(mark, time)
            book.append(Weighing(
                token(data["过磅号"]), pass_id, kind, token(data["磅单号"]), int(revision_text),
                time, kg, row, late,
            ))
        except DomainError as exc:
            issues.append(Issue(exc.code, row, pass_id))
    known = [item for item in book if item.pass_id in passes]
    for item in book:
        if item.pass_id not in passes:
            issues.append(Issue("PASS_UNKNOWN", item.source, item.pass_id))
    return known, issues
