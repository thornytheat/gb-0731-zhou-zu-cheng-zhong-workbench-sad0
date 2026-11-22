export const byId = (id) => document.getElementById(id);

export function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hourCycle: "h23", hour: "2-digit", minute: "2-digit", second: "2-digit",
  fractionalSecondDigits: 3,
});
const dateFormat = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", dateStyle: "short"});

export function clock(value) {
  return timeFormat.format(new Date(value.replace(/(\.\d{3})\d+Z$/, "$1Z")));
}

export function dateTime(value) {
  return `${dateFormat.format(new Date(value.replace(/(\.\d{3})\d+Z$/, "$1Z")))} ${clock(value)}`;
}

const col = (label, read, numeric = false) => ({label, read, numeric});
const source = (obj) => obj.source.ref;
const joined = (list) => list?.join(" / ");
const labels = {
  pass: "正常", warn: "偏载", severe: "严重", matched: "已核平", review: "待复秤",
  settle: "可结算", hold: "暂挂",
};
const styles = {
  pass: "ok", warn: "warn", severe: "bad", matched: "ok", review: "warn",
  settle: "ok", hold: "bad",
};
const displayStatus = (level) => ({text: labels[level], level: styles[level]});

const issueColumns = [
  col("车次", (r) => r.pass_id), col("原因码", (r) => r.code), col("说明", (r) => r.message),
  col("源文件行", source), col("关联证据", (r) => joined(r.related)), col("保留记录", (r) => r.kept_id),
  col("原始字段", (r) => joined(r.source.cells)),
];
const wheelColumns = [
  col("记录号", (r) => r.trigger.id), col("车次", (r) => r.trigger.pass_id),
  col("轮道", (r) => r.trigger.side), col("记录时刻", (r) => clock(r.trigger.time)),
  col("原轮载 kg", (r) => r.trigger.raw_kg, true), col("修正轮载 kg", (r) => r.kg, true),
  col("标定证书", (r) => r.cert), col("证书行", (r) => r.cert_ref), col("触发行", (r) => source(r.trigger)),
];

export function moduleViews(name, snapshot, history) {
  const data = snapshot?.data;
  const stage = data?.[name];
  const empty = {summary: "当前数据源不含该阶段", views: [["暂无数据", [], [col("提示", () => "请切换数据源")]]]};
  if (!stage && !["batches", "settlement"].includes(name)) return empty;
  switch (name) {
    case "intake": return {summary: stage ? `${stage.encoding} · ${stage.segments} 段 · ${stage.blank} 空行 · ${stage.late} 乱序` : "", views: [
      ["采样记录", stage?.accepted, [
        col("记录号", (r) => r.id), col("车次", (r) => r.pass_id), col("轮道", (r) => r.side),
        col("采样时间", (r) => dateTime(r.time)), col("原轮载 kg", (r) => r.raw_kg, true),
        col("顺序", (r) => r.late ? "乱序" : "顺序"), col("源文件行", source),
        col("原始字段", (r) => joined(r.source.cells)),
      ]], ["采样隔离", stage?.issues, issueColumns],
    ]};
    case "calibration": return {summary: stage ? `${stage.wheels.length} 条修正 · ${stage.issues.length} 条隔离` : "", views: [
      ["修正轮载", stage?.wheels, wheelColumns],
      ["标定证书", stage?.certificates, [
        col("证书", (r) => r.id), col("轮道", (r) => r.side), col("生效", (r) => dateTime(r.start)),
        col("失效", (r) => dateTime(r.end)), col("增益", (r) => r.gain, true),
        col("偏置 kg", (r) => r.bias_kg, true), col("源文件行", source),
      ]], ["标定隔离", stage?.issues, issueColumns],
    ]};
    case "debounce": return {summary: stage ? `${stage.kept.length} 条保留 · ${stage.duplicates.length} 条重复` : "", views: [
      ["保留触发", stage?.kept, wheelColumns], ["重复触发", stage?.duplicates, issueColumns],
      ["记录冲突", stage?.issues, issueColumns],
    ]};
    case "axles": return {summary: stage ? `${stage.axes.length} 轴 · ${stage.groups} 轴组` : "", views: [
      ["确认车轴", stage?.axes, [
        col("轴号", (r) => r.id), col("轴组", (r) => r.group), col("轴时刻", (r) => clock(r.time)),
        col("轴距 m", (r) => r.gap_m, true), col("右减左 ms", (r) => r.skew_ms, true),
        col("左轮 kg", (r) => r.left.kg, true), col("右轮 kg", (r) => r.right.kg, true),
        col("轴重 kg", (r) => r.total_kg, true), col("左轮证据", (r) => source(r.left.trigger)),
        col("右轮证据", (r) => source(r.right.trigger)),
      ]], ["轴组隔离", stage?.issues, issueColumns],
    ]};
    case "balance": return {summary: stage ? `${stage.counts.pass} 正常 · ${stage.counts.warn} 偏载 · ${stage.counts.severe} 严重` : "", views: [
      ["偏载结果", stage?.rows, [
        col("轴号", (r) => r.id), col("判级", (r) => displayStatus(r.level)),
        col("左轮 kg", (r) => r.left_kg, true), col("右轮 kg", (r) => r.right_kg, true),
        col("偏载率 %", (r) => r.percent, true), col("重侧", (r) => ({L: "左", R: "右"})[r.heavy]),
        col("左轮证据", (r) => r.left_ref), col("右轮证据", (r) => r.right_ref),
      ]],
    ]};
    case "reconcile": return {summary: stage ? `${stage.ledger.matched} 核平 · ${stage.ledger.review} 待复秤` : "", views: [
      ["勾稽结果", stage?.rows, [
        col("车次", (r) => r.pass_id), col("车牌", (r) => r.plate), col("状态", (r) => displayStatus(r.status)),
        col("磅单", (r) => r.ticket?.id), col("版次", (r) => r.ticket?.revision, true),
        col("静态总重 kg", (r) => r.ticket?.kg, true), col("有效轮载 kg", (r) => r.wheel_kg, true),
        col("差额 kg", (r) => r.difference_kg, true), col("原因码", (r) => joined(r.codes)),
        col("磅单证据", (r) => joined(r.ticket_refs)), col("轮载证据", (r) => joined(r.wheel_refs)),
        col("复秤证据", (r) => joined(r.review_refs)),
      ]], ["磅单隔离", stage?.ticket_issues, issueColumns], ["触发隔离汇总", data?.quarantine, issueColumns],
    ]};
    case "settlement": return {
      summary: stage ? `${stage.ledger.settled} 趟结算 · ${stage.ledger.held} 趟暂挂 · ${stage.ledger.orphans} 条无主过磅` : "",
      views: [
        ["趟次结果", stage?.rows, [
          col("趟次", (r) => r.trip_id), col("车次", (r) => r.pass_id), col("车牌", (r) => r.plate),
          col("状态", (r) => displayStatus(r.status)),
          col("计费进磅", (r) => r.gross_id), col("毛重 kg", (r) => r.gross_kg, true),
          col("计费回磅", (r) => r.tare_id), col("皮重 kg", (r) => r.tare_kg, true),
          col("计费净重 kg", (r) => r.net_kg, true), col("暂挂原因", (r) => joined(r.codes)),
          col("车次档案", (r) => r.pass_ref),
        ]],
        ["过磅归属", stage?.rows.flatMap((trip) => trip.entries.map((entry) => ({
          ...entry, trip_id: trip.trip_id, pass_id: trip.pass_id,
        }))), [
          col("趟次", (r) => r.trip_id), col("过磅号", (r) => r.weighing_id),
          col("类型", (r) => ({gross: "进磅", regross: "复磅", tare: "回磅"})[r.kind]),
          col("过磅时刻", (r) => clock(r.time)), col("磅单", (r) => r.ticket_id),
          col("版次", (r) => r.revision, true), col("磅单 kg", (r) => r.ticket_kg, true),
          col("轮载 kg", (r) => r.wheel_kg, true), col("差额 kg", (r) => r.difference_kg, true),
          col("可采信", (r) => r.eligible ? "是" : "否"), col("计费选中", (r) => r.chosen ? "是" : "否"),
          col("晚到", (r) => r.late ? "晚到" : ""), col("原因码", (r) => joined(r.codes)),
          col("过磅源行", (r) => r.source_ref), col("轮载证据", (r) => joined(r.wheel_refs)),
          col("缺证依据", (r) => joined(r.issue_refs)), col("磅单冲突", (r) => joined(r.ticket_refs)),
        ]],
        ["无主过磅", stage?.orphans, [
          col("过磅号", (r) => r.weighing_id), col("车次", (r) => r.pass_id),
          col("类型", (r) => ({gross: "进磅", regross: "复磅", tare: "回磅"})[r.kind]),
          col("过磅时刻", (r) => clock(r.time)), col("磅单", (r) => r.ticket_id),
          col("版次", (r) => r.revision, true), col("总重 kg", (r) => r.ticket_kg, true),
          col("晚到", (r) => r.late ? "晚到" : ""), col("原因码", (r) => joined(r.codes)),
          col("源行", (r) => r.source_ref),
        ]],
        ["磅单版次投票", stage?.ticket_votes, [
          col("磅单号", (r) => r.ticket_id), col("版次", (r) => r.revision, true),
          col("状态", (r) => r.conflict ? displayStatus("hold") : displayStatus("settle")),
          col("投票源行", (r) => joined(r.refs)),
        ]],
        ["过磅轮载档案", data?.dossiers?.flatMap((dossier) => dossier.axes.map((axis) => ({
          ...axis, weighing_id: dossier.weighing_id,
        }))) ?? [], [
          col("过磅号", (r) => r.weighing_id), col("轴号", (r) => r.id),
          col("轴时刻", (r) => clock(r.time)),
          col("左轮 kg", (r) => r.left_kg, true), col("右轮 kg", (r) => r.right_kg, true),
          col("轴重 kg", (r) => r.total_kg, true),
          col("偏载率 %", (r) => r.percent, true), col("判级", (r) => displayStatus(r.level)),
          col("左轮证据", (r) => r.left_ref), col("右轮证据", (r) => r.right_ref),
        ]],
        ["触发隔离", data?.quarantine, issueColumns],
      ],
    };
    case "batches": return {summary: `${history.length} 条归档`, views: [
      ["归档批次", history, [
        col("批次号", (r) => ({text: r.run_id.slice(0, 12), run: r.run_id})),
        col("数据源", (r) => r.files.triggers.file), col("规则版本", (r) => r.rules),
        col("归档时间", (r) => dateTime(r.created_at)), col("用时 ms", (r) => r.elapsed_ms, true),
        col("触发数", (r) => r.ledger.raw, true), col("待复秤", (r) => r.ledger.review, true),
      ]], ["输入文件", Object.values(snapshot?.manifest?.files ?? {}), [
        col("源文件", (r) => r.file), col("字节数", (r) => r.bytes, true), col("SHA-256", (r) => r.sha256),
      ]],
    ]};
    default: throw new Error("模块不存在");
  }
}

function valueText(value) {
  return value && typeof value === "object" ? value.text : value ?? "";
}

export function renderTable(view, query) {
  const [, rows = [], columns] = view;
  const matches = [];
  for (const row of rows) {
    const values = columns.map((column) => column.read(row));
    if (!query || values.some((value) => String(valueText(value)).toLowerCase().includes(query))) matches.push(values);
  }
  const host = byId("evidence-table");
  byId("table-count").textContent = `${matches.length} / ${rows.length} 条`;
  if (!matches.length) {
    host.replaceChildren(element("p", query ? "没有匹配记录。" : "暂无记录。", "empty"));
    return;
  }
  const table = element("table", "", "ledger");
  const header = table.createTHead().insertRow();
  for (const column of columns) {
    const th = element("th", column.label, column.numeric ? "num" : "");
    th.scope = "col";
    header.append(th);
  }
  const body = table.createTBody();
  for (const values of matches) {
    const row = body.insertRow();
    for (const [index, value] of values.entries()) {
      const td = row.insertCell();
      const text = String(valueText(value));
      td.title = text;
      if (columns[index].numeric) td.className = "num";
      if (value?.level) {
        td.append(element("i", "", `lamp ${value.level}`), text);
      } else if (value?.run) {
        const button = element("button", text);
        button.type = "button";
        button.dataset.run = value.run;
        button.title = value.run;
        td.append(button);
      } else {
        td.textContent = text || "—";
      }
    }
  }
  host.replaceChildren(table);
}

function track(side, value, maximum) {
  const wrap = element("div", "", `load-track ${side}`);
  const label = element("span", value, "num");
  const bed = element("div", "", "bar-bed");
  const bar = element("i", "", "load-bar");
  bar.style.setProperty("--amount", `${Number(value) / maximum * 100}%`);
  bed.append(bar);
  wrap.append(label, bed);
  return wrap;
}

export function renderChart(data, passId) {
  const axes = data?.axles.axes.filter((axis) => axis.pass_id === passId) ?? [];
  const host = byId("load-chart");
  const match = data?.reconcile.rows.find((row) => row.pass_id === passId);
  byId("load-summary").replaceChildren(element("span", `${axes.length} 根确认车轴`),
    element("span", match ? `有效合计 ${match.wheel_kg} kg` : "—"));
  if (!axes.length) {
    host.replaceChildren(element("p", "暂无确认车轴。", "empty"));
    return;
  }
  const maximum = Math.ceil(Math.max(...axes.flatMap((axis) => [Number(axis.left.kg), Number(axis.right.kg)])) / 1000) * 1000;
  const axisScale = element("div", "", "load-scale");
  axisScale.append(...["轴号", `左轮 ${maximum}`, "│", `${maximum} 右轮`, "kg"].map((item) => element("span", item)));
  const frag = document.createDocumentFragment();
  frag.append(axisScale);
  const grades = new Map(data.balance.rows.map((row) => [row.id, row]));
  for (const axis of axes) {
    const grade = grades.get(axis.id);
    const state = displayStatus(grade.level);
    const row = element("div", "", "load-row");
    row.append(element("span", axis.id.split("/").at(-1), "axis-tag"), track("left", axis.left.kg, maximum),
      element("i", "", "load-axis-line"), track("right", axis.right.kg, maximum),
      element("span", state.text, `load-grade level-${state.level}`));
    row.title = `${axis.id} · 左 ${source(axis.left.trigger)} · 右 ${source(axis.right.trigger)}`;
    frag.append(row);
  }
  host.replaceChildren(frag);
}
