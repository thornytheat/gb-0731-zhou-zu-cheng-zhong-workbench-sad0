import {byId, dateTime, element, moduleViews, renderChart, renderTable} from "./views.js";
import {startTelemetry} from "./telemetry.js";

const state = {snapshot: null, history: [], module: "axles", view: 0, busy: false, passId: ""};
const nav = Array.from(document.querySelectorAll("[data-module]"));
const sample = byId("sample");
const vehicle = byId("vehicle");
const filter = byId("filter");
const exportButton = byId("export");

function isShift(snapshot = state.snapshot) {
  return snapshot?.manifest?.sample === "shift"
    || (!snapshot?.manifest && sample.value === "shift")
    || Boolean(snapshot?.data?.settlement);
}

function ledgerOf(snapshot = state.snapshot) {
  return isShift(snapshot) ? snapshot?.data.settlement.ledger : snapshot?.data.reconcile.ledger;
}

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message ?? `请求失败 ${response.status}`);
  return payload;
}

function showError(text = "") {
  const error = byId("module-error");
  error.textContent = text;
  error.hidden = !text;
}

function showNote(text = "") {
  const note = byId("action-note");
  note.textContent = text;
  note.hidden = !text;
}

function controls() {
  const id = state.snapshot?.manifest?.run_id;
  const shift = isShift();
  sample.disabled = state.busy;
  byId("run").disabled = state.busy;
  byId("rerun").disabled = state.busy || !id;
  exportButton.textContent = shift ? "导出本班结果" : "导出复核包";
  if (id && !state.busy) exportButton.href = `/api/batches/${id}/export`;
  else exportButton.removeAttribute("href");
  exportButton.setAttribute("aria-disabled", String(!id || state.busy));
}

function defaultModule() {
  return isShift() ? "settlement" : "axles";
}

function selectModule(name) {
  state.module = name;
  state.view = 0;
  filter.value = "";
  nav.forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.module === name)));
  paintEvidence();
}

function paintMetrics() {
  const manifest = state.snapshot?.manifest;
  const shift = isShift();
  const ledger = ledgerOf();
  const stateText = state.busy ? "处理中" : manifest ? "已归档" : ledger ? "未归档预览" : "暂无结果";
  byId("snapshot-state").replaceChildren(element("i", "", `lamp ${manifest && !state.busy ? "ok" : ""}`), stateText);
  byId("batch-id").textContent = manifest?.run_id.slice(0, 12) ?? "—";
  byId("batch-id").title = manifest?.run_id ?? "";
  const fileKey = shift ? "triggers" : "triggers";
  byId("data-version").textContent = manifest?.files[fileKey].sha256.slice(0, 12) ?? "—";
  byId("data-version").title = manifest?.files[fileKey].sha256 ?? "";
  byId("created-at").textContent = manifest ? dateTime(manifest.created_at) : "—";
  byId("kpi-trips-box").hidden = !shift;
  byId("kpi-net-box").hidden = !shift;
  byId("kpi-wheel-box").hidden = shift;
  byId("kpi-review-box").hidden = shift;
  for (const name of ["raw", "effective", "duplicates", "isolated"]) {
    byId(`kpi-${name}`).textContent = ledger?.[name] ?? "—";
  }
  if (shift) {
    byId("kpi-trips").textContent = ledger ? `${ledger.settled} / ${ledger.held}` : "—";
    byId("kpi-net_kg").textContent = ledger?.net_kg ?? "—";
  } else {
    byId("kpi-wheel_kg").textContent = ledger?.wheel_kg ?? "—";
    byId("kpi-review").textContent = ledger?.review ?? "—";
  }
}

function paintPasses() {
  const passes = state.snapshot?.data.calibration?.passes
    ?? (shiftPasses(state.snapshot) ? state.snapshot?.data.passes : null);
  const ids = passes ? Object.keys(passes) : [];
  if (!ids.includes(state.passId)) state.passId = ids[0] ?? "";
  vehicle.disabled = !ids.length;
  if (ids.length) {
    vehicle.replaceChildren(...ids.map((id) => new Option(id, id, false, id === state.passId)));
  } else vehicle.replaceChildren(new Option("—", ""));
  if (state.snapshot?.data?.axles) renderChart(state.snapshot?.data, state.passId);
  else byId("load-chart").replaceChildren(element("p", "趟次班次按过磅档案查看轮载。", "empty"));
}

function shiftPasses(snapshot) {
  return snapshot?.data?.settlement && !snapshot?.data?.calibration;
}

function paintEvidence(resetScroll = true) {
  const info = moduleViews(state.module, state.snapshot, state.history);
  byId("module-title").textContent = nav.find((button) => button.dataset.module === state.module).textContent.trim();
  byId("module-summary").textContent = info.summary;
  const bar = byId("module-views");
  const activeView = bar.contains(document.activeElement);
  bar.replaceChildren(...info.views.map(([label], index) => {
    const button = element("button", label);
    button.type = "button";
    button.dataset.view = index;
    button.setAttribute("aria-pressed", String(index === state.view));
    return button;
  }));
  if (activeView) bar.querySelector(`[data-view="${state.view}"]`).focus();
  renderTable(info.views[state.view], filter.value.trim().toLowerCase());
  if (resetScroll) byId("evidence-table").scrollTo(0, 0);
}

function paint() {
  controls();
  paintMetrics();
  paintPasses();
  paintEvidence();
}

async function refresh(runId) {
  const query = runId ? `run_id=${encodeURIComponent(runId)}` : `sample=${encodeURIComponent(sample.value)}`;
  state.snapshot = await request(`/api/workbench?${query}`);
  if (state.snapshot.manifest) sample.value = state.snapshot.manifest.sample;
}

async function history() {
  state.history = (await request("/api/batches")).batches;
}

async function act(action) {
  if (state.busy) return;
  state.busy = true;
  showError();
  showNote();
  controls();
  paintMetrics();
  try {
    await action();
    byId("service-lamp").className = "lamp ok";
    byId("service-state").textContent = "服务在线";
  } catch (error) {
    showError(`${error.message}，请重试。`);
    if (!state.snapshot) {
      byId("service-lamp").className = "lamp warn";
      byId("service-state").textContent = "读取失败";
    }
  } finally {
    state.busy = false;
    paint();
  }
}

byId("run-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    const result = await request("/api/batches", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({sample: sample.value}),
    });
    await refresh(result.manifest.run_id);
    await history();
    showNote(result.reused ? "沿用原批次" : "批次已归档");
  });
});
byId("rerun").addEventListener("click", () => act(async () => {
  const id = state.snapshot?.manifest?.run_id;
  if (!id) return;
  await request(`/api/batches/${id}/rerun`, {method: "POST"});
  await refresh(id);
  await history();
  showNote("重算核验一致");
}));
sample.addEventListener("change", () => act(async () => {
  state.snapshot = null;
  await refresh();
  selectModule(defaultModule());
}));
vehicle.addEventListener("change", () => {
  state.passId = vehicle.value;
  if (state.snapshot?.data?.axles) renderChart(state.snapshot?.data, state.passId);
});
for (const button of nav) {
  button.addEventListener("click", () => selectModule(button.dataset.module));
}
byId("module-views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) {
    state.view = Number(button.dataset.view);
    filter.value = "";
    paintEvidence();
  }
});
byId("evidence-table").addEventListener("click", (event) => {
  const button = event.target.closest("[data-run]");
  if (button) act(() => refresh(button.dataset.run));
});
filter.addEventListener("input", () => paintEvidence());

startTelemetry(request);
act(async () => {
  await history();
  const firstSample = state.history[0]?.sample;
  if (firstSample) sample.value = firstSample;
  await refresh(state.history[0]?.run_id);
  selectModule(defaultModule());
});
