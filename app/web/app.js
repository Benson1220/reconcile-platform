"use strict";

const $ = (id) => document.getElementById(id);
let readOnly = true;
const state = {runId: null, runs: [], runTotal: 0, offset: 0, limit: 10, total: 0, runsToken: 0, runToken: 0, caseToken: 0, detailToken: 0};
const rules = {
  PAID_BUT_ORDER_UNPAID: ["訂單狀態未更新", ""],
  PAYMENT_AMOUNT_MISMATCH: ["付款金額不符", "amount"],
  SETTLEMENT_MISSING: ["結算紀錄缺漏", "missing"],
  SETTLEMENT_ARITHMETIC_MISMATCH: ["結算算式不符", "arithmetic"],
};
const statuses = {OPEN: "待處理", IN_PROGRESS: "調查中", CLOSED: "已結案"};
const labels = {
  order_id: "訂單編號", payment_id: "付款編號", settlement_id: "結算編號",
  amount: "金額", currency: "幣別", payment_status: "付款狀態", status: "付款結果",
  snapshot_at: "訂單快照時間", paid_at: "付款成功時間", expected_settlement_date: "預期結算日",
  gross_amount: "結算總額", fee: "手續費", net_amount: "紀錄中的淨額", settlement_date: "結算日期",
};

// 所有 API 與 CSV 文字都透過 textContent 顯示，不插入 HTML。
function node(tag, className = "", text = "") {
  const item = document.createElement(tag);
  if (className) item.className = className;
  item.textContent = text;
  return item;
}
function message(id, text = "", success = false) {
  const item = $(id);
  item.textContent = text;
  item.className = `message${success ? " success" : ""}`;
  item.hidden = !text;
}
function displayTime(value) {
  try {
    return new Intl.DateTimeFormat("zh-TW", {timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(value));
  } catch { return String(value ?? "—"); }
}
function amount(value) {
  if (value === null || value === undefined || value === "") return "—";
  const [integer, fraction] = String(value).split(".");
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (fraction === undefined ? "" : `.${fraction}`);
}
async function api(path, options) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("無法連線到服務，請確認後端仍在執行，再重新整理。"); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`服務未回傳有效結果（HTTP ${response.status}），請稍後再試。`); }
  if (!response.ok) {
    let detail = data.detail;
    if (Array.isArray(detail)) detail = detail.map((entry) => `${entry.loc?.slice(1).join(" / ") || "欄位"}：${entry.msg}`).join("；");
    const error = new Error(typeof detail === "string" ? detail : `操作失敗（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}
function connection(ok) {
  $("connection").className = `connection ${ok ? "ready" : "failed"}`;
  $("connection").textContent = ok ? "資料已載入" : "資料讀取失敗";
}
function emptyTable(text) {
  const row = node("tr");
  const cell = node("td", "empty", text);
  cell.colSpan = 5;
  row.append(cell);
  $("case-rows").replaceChildren(row);
  $("prev-page").disabled = true;
  $("next-page").disabled = true;
}
function resetMetrics() {
  for (const id of ["metric-run", "metric-count", "metric-coverage"]) $(id).textContent = "—";
  $("filtered-count").textContent = "0";
  $("run-info").textContent = "載入批次後，即可查看比對條件與證據。";
}
function renderCases(data) {
  state.total = data.total;
  $("filtered-count").textContent = String(data.total);
  if (!data.items.length) {
    emptyTable(data.total ? "此頁沒有資料，請返回上一頁。" : "目前條件下沒有異常。可選擇其他異常類型查看。");
  } else {
    const rows = data.items.map((item) => {
      const row = node("tr");
      const ids = node("td");
      ids.append(node("div", "record-id", item.order_id), node("div", "record-secondary", item.payment_id));
      const type = node("td");
      const [label, color] = rules[item.rule] || [item.rule, ""];
      type.append(node("span", `badge ${color}`, label));
      const difference = node("td", "amount-value", amount(item.difference));
      const action = node("td");
      const button = node("button", "row-button", "查看證據 ↗");
      button.type = "button";
      button.setAttribute("aria-label", `查看 ${item.order_id} 的證據`);
      button.addEventListener("click", () => openCase(item.case_id, item.order_id));
      action.append(button);
      const progress = node("td");
      progress.append(node("span", `case-status ${item.status.toLowerCase()}`, statuses[item.status] || item.status));
      row.append(ids, type, progress, difference, action);
      return row;
    });
    $("case-rows").replaceChildren(...rows);
  }
  $("page-info").textContent = data.total ? `第 ${data.offset + 1}–${data.offset + data.items.length} 筆，共 ${data.total} 筆` : "共 0 筆異常";
  $("prev-page").disabled = state.offset === 0;
  $("next-page").disabled = state.offset + state.limit >= data.total;
}
async function loadCases() {
  if (!state.runId) return;
  const token = ++state.caseToken;
  const runId = state.runId;
  const params = new URLSearchParams({limit: String(state.limit), offset: String(state.offset)});
  if ($("rule-filter").value) params.set("rule", $("rule-filter").value);
  if ($("status-filter").value) params.set("status", $("status-filter").value);
  emptyTable("正在讀取異常清單…");
  $("filtered-count").textContent = "…";
  $("page-info").textContent = "載入中";
  message("query-message");
  try {
    const data = await api(`/api/runs/${runId}/cases?${params}`);
    if (token !== state.caseToken || runId !== state.runId) return;
    renderCases(data);
    connection(true);
  } catch (error) {
    if (token !== state.caseToken || runId !== state.runId) return;
    emptyTable("異常清單讀取失敗。請點選「重新整理」再試一次。");
    $("filtered-count").textContent = "—";
    $("page-info").textContent = "未取得資料";
    message("query-message", error.message);
    connection(false);
  }
}
async function selectRun(runId) {
  const token = ++state.runToken;
  ++state.caseToken;
  state.runId = Number(runId);
  state.offset = 0;
  $("rule-filter").value = "";
  $("rule-filter").disabled = true;
  $("status-filter").value = "";
  $("status-filter").disabled = true;
  resetMetrics();
  emptyTable("正在讀取比對批次…");
  message("query-message");
  try {
    const run = await api(`/api/runs/${state.runId}`);
    if (token !== state.runToken) return;
    $("metric-run").textContent = `#${run.run_id}`;
    $("metric-count").textContent = String(run.case_count);
    $("metric-coverage").textContent = run.config.settlement_complete_through || "—";
    $("run-info").textContent = `基準時間 ${displayTime(run.as_of)} · 同步等待 ${run.config.order_sync_grace_minutes} 分鐘 · 規則版本 ${run.rules_version}`;
    $("rule-filter").disabled = false;
    $("status-filter").disabled = false;
    await loadCases();
  } catch (error) {
    if (token !== state.runToken) return;
    emptyTable("無法取得這個批次，請重新整理或選擇其他批次。");
    $("page-info").textContent = "未取得資料";
    message("query-message", error.message);
    connection(false);
  }
}
async function loadRuns(preferred = null, append = false) {
  const token = ++state.runsToken;
  $("refresh-runs").disabled = true;
  $("more-runs").disabled = true;
  try {
    const page = await api(`/api/runs?limit=50&offset=${append ? state.runs.length : 0}`);
    if (token !== state.runsToken) return;
    state.runs = append ? [...state.runs, ...page.items] : page.items;
    state.runTotal = page.total;
    const target = Number(preferred || state.runId);
    const select = $("run-select");
    select.replaceChildren(...state.runs.map((run) => {
      const option = node("option", "", `批次 #${run.run_id} · ${displayTime(run.as_of)} · ${run.case_count} 筆異常`);
      option.value = String(run.run_id);
      return option;
    }));
    select.disabled = !state.runs.length;
    $("more-runs").hidden = state.runs.length >= state.runTotal;
    connection(true);
    if (!state.runs.length) {
      state.runId = null;
      ++state.runToken;
      ++state.caseToken;
      select.append(node("option", "", "尚無比對批次"));
      resetMetrics();
      $("rule-filter").disabled = true;
      $("status-filter").disabled = true;
      $("page-info").textContent = "尚未匯入資料";
      emptyTable("尚無比對結果。請先上傳三份 CSV，開始第一次比對。");
      message("query-message");
      return;
    }
    const initial = readOnly ? state.runs.at(-1) : state.runs[0];
    const selected = state.runs.find((run) => run.run_id === target) || initial;
    select.value = String(selected.run_id);
    if (!append || selected.run_id !== state.runId) await selectRun(selected.run_id);
  } catch (error) {
    if (token !== state.runsToken) return;
    connection(false);
    message("query-message", error.message);
    if (!state.runId) {
      $("run-select").replaceChildren(node("option", "", "批次載入失敗"));
      emptyTable("資料讀取失敗，請確認資料庫與後端已啟動，再點選「重新整理」。");
    }
  } finally {
    if (token === state.runsToken) {
      $("refresh-runs").disabled = false;
      $("more-runs").disabled = false;
    }
  }
}
function pair(label, value) {
  const entry = node("div", "evidence-pair");
  entry.append(node("dt", "", label), node("dd", "", value === "" ? "（未提供）" : String(value ?? "—")));
  return entry;
}

function workflow(data, findingId = data.case_id) {
  const section = node("section", "workflow");
  const heading = node("div", "workflow-heading");
  heading.append(node("h3", "", "案件處理"), node("span", `case-status ${data.status.toLowerCase()}`, statuses[data.status]));
  section.append(heading);
  const form = node("form");
  const fields = node("fieldset");
  fields.disabled = readOnly;
  const statusLabel = node("label", "field-label", "更新處理狀態");
  statusLabel.htmlFor = "case-status";
  const select = node("select");
  select.id = "case-status";
  for (const [value, label] of Object.entries(statuses)) {
    const option = node("option", "", label);
    option.value = value;
    select.append(option);
  }
  select.value = data.status;
  const noteLabel = node("label", "field-label", "新增處理備註（必填）");
  noteLabel.htmlFor = "case-note";
  const note = node("textarea");
  note.id = "case-note";
  note.rows = 3;
  note.maxLength = 2000;
  note.required = true;
  note.placeholder = "記錄查核內容、下一步或結案結果…";
  const help = node("p", "input-help", "每次儲存都會新增一筆處理紀錄；結案時請說明確認結果與處理方式。");
  const save = node("button", "button primary", "儲存處理紀錄");
  save.type = "submit";
  fields.append(statusLabel, select, noteLabel, note, help, save);
  const feedback = node("p", "message");
  feedback.id = "workflow-message";
  feedback.setAttribute("role", "status");
  feedback.hidden = true;
  const reload = node("button", "button secondary", "重新載入案件，保留草稿");
  reload.type = "button";
  reload.hidden = true;
  form.append(fields, feedback, reload);
  section.append(form);
  section.append(node("p", "input-help", `版本 ${data.version} · 更新於 ${displayTime(data.updated_at)}${data.closed_at ? ` · 結案於 ${displayTime(data.closed_at)}` : ""}`));

  const history = node("details", "workflow-history");
  history.open = data.history.length > 0;
  history.append(node("summary", "", `處理紀錄（${data.history.length}）`));
  if (!data.history.length) history.append(node("p", "muted small", "尚無處理備註。新增第一筆，開始追蹤進度。"));
  for (const event of data.history) {
    const entry = node("article", "history-entry");
    entry.append(node("p", "history-meta", `${displayTime(event.created_at)} · ${event.from_status === event.to_status ? `${statuses[event.to_status]} · 新增備註` : `${statuses[event.from_status]} → ${statuses[event.to_status]}`}`), node("p", "history-note", event.note));
    history.append(entry);
  }
  section.append(history);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (fields.disabled) return;
    const noteText = note.value.trim();
    if (!noteText) {feedback.textContent = "請填寫處理備註；結案時請說明處理結果。"; feedback.hidden = false; return;}
    const token = state.detailToken;
    const body = {status: select.value, note: noteText, expected_version: data.version};
    fields.disabled = true;
    save.textContent = "正在儲存…";
    feedback.hidden = true;
    reload.hidden = true;
    try {
      const updated = await api(`/api/cases/${findingId}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (state.runId) await loadCases();
      if (token !== state.detailToken || !$("case-dialog").open) return;
      renderDetail(updated);
      message("workflow-message", "處理紀錄已儲存，狀態與備註已更新。", true);
    } catch (error) {
      if (token !== state.detailToken || !$("case-dialog").open) return;
      feedback.textContent = error.message;
      feedback.hidden = false;
      reload.hidden = error.status !== 409;
    } finally {fields.disabled = false; save.textContent = "儲存處理紀錄";}
  });
  reload.addEventListener("click", async () => {
    const draft = note.value;
    const desiredStatus = select.value;
    const token = state.detailToken;
    reload.disabled = true;
    try {
      const updated = await api(`/api/cases/${findingId}`);
      if (token !== state.detailToken || !$("case-dialog").open) return;
      renderDetail(updated);
      $("case-note").value = draft;
      $("case-status").value = desiredStatus;
      message("workflow-message", "已載入最新版本並保留草稿。請核對最新處理紀錄後，再儲存。", true);
    } catch (error) {feedback.textContent = error.message; feedback.hidden = false;}
    finally {reload.disabled = false;}
  });
  return section;
}

function ruleSummaryPanel(data) {
  const section = node("section", "ai-panel");
  const heading = node("div", "workflow-heading");
  heading.append(node("h3", "", "規則式查核摘要"), node("span", "ai-tag", "規則整理"));
  const notice = node("p", "input-help", "依固定規則整理本案件的異常、原始證據與查核步驟，不呼叫外部模型，無需 API 金鑰或額度。");
  const button = node("button", "button secondary", "產生規則式摘要");
  button.type = "button";
  button.disabled = true;
  const feedback = node("p", "input-help", "正在讀取規則式摘要…");
  feedback.setAttribute("role", "status");
  const result = node("div", "ai-content");
  section.append(heading, notice, button, feedback, result);
  const token = state.detailToken;
  const alive = () => token === state.detailToken && section.isConnected;
  let configured = false;

  function citations(ids, catalog) {
    const refs = node("div", "ai-citations");
    for (const id of ids) {
      const source = catalog[id];
      if (!source) continue;
      const ref = node("button", "citation", `${id} · ${source.file}${source.line ? ` 第 ${source.line} 列` : " 查無紀錄"}`);
      ref.type = "button";
      ref.addEventListener("click", () => document.getElementById(`case-evidence-${id}`)?.scrollIntoView({behavior:"smooth", block:"start"}));
      refs.append(ref);
    }
    return refs;
  }
  function showSummary(response) {
    result.replaceChildren();
    const summary = response.summary;
    if (!summary) return;
    if (response.stale) result.append(node("p", "message", `此摘要依據案件版本 ${summary.case_version}，目前為版本 ${response.current_case_version}，請重新產生後再參考。`));
    result.append(node("p", "ai-overview", summary.content.overview));
    const catalog = summary.input_snapshot.evidence;
    for (const [title, items, ordered] of [
      ["證據重點", summary.content.confirmed_facts, false],
      ["待驗證推測", summary.content.possible_causes, false],
      ["建議查核步驟", summary.content.recommended_steps, true],
    ]) {
      if (!items.length) continue;
      result.append(node("h4", "", title));
      const list = node(ordered ? "ol" : "ul");
      for (const item of items) {
        const entry = node("li");
        entry.append(node("p", "", item.text));
        if (item.how_to_verify) entry.append(node("p", "muted small", `驗證方式：${item.how_to_verify}`));
        entry.append(citations(item.evidence_ids, catalog));
        list.append(entry);
      }
      result.append(list);
    }
    if (summary.content.missing_information.length) {
      result.append(node("h4", "", "尚缺資料"));
      const list = node("ul");
      for (const item of summary.content.missing_information) list.append(node("li", "", item));
      result.append(list);
    }
    result.append(node("p", "input-help", `摘要規則版本 ${summary.rule_version} · 案件版本 ${summary.case_version} · ${displayTime(summary.created_at)}`));
    button.textContent = response.stale ? "依最新案件產生摘要" : "讀取目前版本摘要";
  }
  async function load() {
    try {
      const saved = await api(`/api/cases/${data.case_id}/rule-summary`);
      if (!alive()) return;
      configured = true;
      button.disabled = readOnly;
      button.hidden = readOnly;
      feedback.textContent = readOnly ? "展示版提供預先整理的摘要。可點選引用查看原始證據。" : "可直接產生摘要。建議步驟來自固定查核清單，實際原因仍需人工確認。";
      showSummary(saved);
    } catch (error) {if (alive()) feedback.textContent = error.message;}
  }
  button.addEventListener("click", async () => {
    if (!configured || button.disabled) return;
    button.disabled = true;
    button.textContent = "正在整理摘要…";
    feedback.textContent = "正在讀取或產生摘要，請稍候。";
    try {
      const response = await api(`/api/cases/${data.case_id}/rule-summary`, {method:"POST"});
      if (!alive()) return;
      showSummary(response);
      feedback.textContent = response.reused ? "已讀取目前版本的規則式摘要。" : "規則式摘要已儲存。可點選引用查看原始證據。";
    } catch (error) {
      if (!alive()) return;
      feedback.textContent = error.message;
      button.textContent = "重試產生規則式摘要";
    } finally {if (alive()) button.disabled = !configured;}
  });
  load();
  return section;
}
function trackingPanel(data) {
  const section = node("section", "workflow");
  const tracking = data.tracking;
  const status = {
    MISSING: "最新觀察仍缺少結算",
    PENDING_REVIEW: "已找到結算 · 待人工確認",
    REVIEWED: "已找到結算 · 已人工結案",
  };
  section.append(node("h3", "", "跨批次追蹤"), node("p", "message", status[tracking.status]));
  section.append(node("p", "input-help", `付款 ${data.payment_id} 的調查狀態與備註會沿用案件 #${tracking.workflow.case_id}。`));
  const list = node("ol", "tracking-observations");
  let missingCount = 0;
  for (const entry of tracking.observations) {
    const item = node("li");
    const label = entry.missing_case_id ? (++missingCount === 1 ? "首次發現缺漏" : "再次發現缺漏") : "找到結算紀錄";
    item.append(node("p", "", `批次 #${entry.run_id} · ${displayTime(entry.as_of)} · ${label}`));
    if (entry.missing_case_id && entry.missing_case_id !== data.case_id) {
      const link = node("button", "row-button", "查看該批次證據");
      link.type = "button";
      link.addEventListener("click", () => openCase(entry.missing_case_id, data.order_id));
      item.append(link);
    }
    for (const evidence of entry.settlements) {
      const details = node("details", "raw-data");
      details.append(node("summary", "", `${evidence.record.settlement_id} · ${evidence.file} 第 ${evidence.line} 列`));
      details.append(node("pre", "", JSON.stringify(evidence.record, null, 2)));
      item.append(details);
    }
    list.append(item);
  }
  section.append(list);
  return section;
}

function renderDetail(data) {
  const container = $("case-detail");
  const [label, color] = rules[data.rule] || [data.rule, ""];
  const badge = node("span", `badge ${color}`, label);
  const summary = node("p", "detail-summary", data.summary);
  const grid = node("dl", "detail-grid");
  const fields = [["案件編號", `#${data.case_id}`], ["比對批次", `#${data.run_id}`], ["訂單編號", data.order_id], ["付款編號", data.payment_id]];
  if (data.settlement_id) fields.push(["結算編號", data.settlement_id]);
  for (const [key, title] of [["order_amount", "訂單金額"], ["payment_amount", "付款金額"], ["gross_amount", "結算總額"], ["fee", "手續費"], ["net_amount", "紀錄淨額"], ["expected_net_amount", "依算式應有淨額"], ["difference", "差額"]]) {
    if (data[key] !== undefined) fields.push([title, `${amount(data[key])} ${data.currency || ""}`.trim()]);
  }
  if (data.expected_settlement_date) fields.push(["預期結算日", data.expected_settlement_date]);
  for (const [title, value] of fields) {
    const entry = node("div");
    entry.append(node("dt", "", title), node("dd", "", value));
    grid.append(entry);
  }
  const rootCause = node("p", "root-cause", data.root_cause || "實際原因待調查。");
  const report = node("a", "button secondary", "列印／另存 PDF ↗");
  report.href = `/cases/${data.case_id}/report`;
  report.target = "_blank";
  report.rel = "noopener";
  container.replaceChildren(badge, summary, report);
  if (data.tracking) container.append(trackingPanel(data));
  container.append(workflow(data.tracking?.workflow || data, data.case_id), ruleSummaryPanel(data), grid);
  if (data.difference_definition) container.append(node("p", "muted small", data.difference_definition));
  container.append(rootCause, node("h3", "detail-section-title", "原始證據"));
  for (const [index, evidence] of (data.evidence || []).entries()) {
    const card = node("section", "evidence-card");
    card.id = `case-evidence-E${index + 1}`;
    const absent = evidence.type === "absence_check";
    const heading = node("div", "evidence-title", absent ? "查無對應結算" : evidence.file);
    heading.append(node("span", "", absent ? "查詢條件與涵蓋範圍" : `原始檔案第 ${evidence.line} 列`));
    const body = node("div", "evidence-body");
    const description = node("dl");
    if (absent) {
      for (const [title, value] of [["查詢檔案", evidence.file], ["搜尋欄位", evidence.lookup_field], ["搜尋付款編號", evidence.lookup_value], ["符合筆數", evidence.matching_rows], ["掃描筆數", evidence.rows_scanned], ["資料完整涵蓋至", evidence.complete_through], ["涵蓋宣告來源", evidence.coverage_source]]) description.append(pair(title, value));
      body.append(description, node("p", "muted small", evidence.coverage_note));
    } else {
      for (const [key, value] of Object.entries(evidence.record || {})) description.append(pair(labels[key] || key, value));
      body.append(description);
    }
    const raw = node("details", "raw-data");
    raw.append(node("summary", "", `查看第 ${index + 1} 份證據的完整 JSON`), node("pre", "", JSON.stringify(evidence, null, 2)));
    body.append(raw);
    card.append(heading, body);
    container.append(card);
  }
}
async function openCase(caseId, orderId) {
  const token = ++state.detailToken;
  $("detail-title").textContent = `${orderId} · 案件 #${caseId}`;
  $("case-detail").replaceChildren(node("p", "detail-loading", "正在讀取案件證據…"));
  if (!$("case-dialog").open) $("case-dialog").showModal();
  $("case-dialog").scrollTop = 0;
  try {
    const data = await api(`/api/cases/${caseId}`);
    if (token !== state.detailToken || !$("case-dialog").open) return;
    renderDetail(data);
  } catch (error) {
    if (token !== state.detailToken || !$("case-dialog").open) return;
    $("case-detail").replaceChildren(node("p", "message", error.message));
  }
}
$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (readOnly) return;
  message("upload-message");
  const form = event.currentTarget;
  const payload = new FormData(form);
  for (const source of ["orders", "payments", "settlements"]) {
    const file = $(source).files[0];
    if (!file || !/\.csv$/i.test(file.name) || file.size === 0 || file.size > 2 * 1024 * 1024) {
      message("upload-message", "請為每個來源選擇非空白的 CSV，每份檔案上限 2 MiB。");
      return;
    }
  }
  let localTime = $("as-of").value;
  if (localTime.length === 16) localTime += ":00";
  payload.set("as_of", `${localTime}+08:00`);
  $("upload-fields").disabled = true;
  $("upload-button").textContent = "正在上傳與比對…";
  try {
    const result = await api("/api/reconciliations", {method: "POST", body: payload});
    message("upload-message", `${result.reused ? "已沿用" : "已建立"}批次 #${result.run_id}，共 ${result.case_count} 筆異常。`, true);
    await loadRuns(result.run_id);
  } catch (error) { message("upload-message", error.message); }
  finally {
    $("upload-fields").disabled = false;
    $("upload-button").replaceChildren(document.createTextNode("上傳並比對 "), node("span", "", "→"));
  }
});
$("run-select").addEventListener("change", (event) => selectRun(event.target.value));
$("rule-filter").addEventListener("change", () => {state.offset = 0; loadCases();});
$("status-filter").addEventListener("change", () => {state.offset = 0; loadCases();});
$("refresh-runs").addEventListener("click", () => loadRuns());
$("more-runs").addEventListener("click", () => loadRuns(null, true));
$("prev-page").addEventListener("click", () => {state.offset = Math.max(0, state.offset - state.limit); loadCases();});
$("next-page").addEventListener("click", () => {state.offset += state.limit; loadCases();});
$("close-dialog").addEventListener("click", () => $("case-dialog").close());
$("case-dialog").addEventListener("close", () => {++state.detailToken;});
$("case-dialog").addEventListener("click", (event) => {
  const rect = event.currentTarget.getBoundingClientRect();
  if (event.target === event.currentTarget && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) event.currentTarget.close();
});
async function startWorkbench() {
  $("upload-fields").disabled = true;
  try {
    const config = await api("/api/deployment");
    readOnly = config.read_only !== false;
    $("upload-fields").disabled = readOnly;
    if (readOnly) {
      $("demo-notice").hidden = false;
      document.querySelector(".upload-panel").hidden = true;
      $("workbench-layout").classList.add("demo-layout");
    }
    await loadRuns();
  } catch (error) {
    message("query-message", error.message);
    connection(false);
  }
}
startWorkbench();
