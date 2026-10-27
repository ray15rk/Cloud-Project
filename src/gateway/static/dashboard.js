"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { events: [], examples: [], mode: "signed", filter: "all", busy: false, detail: null, failures: 0 };
const modeNames = { signed: "Signed", tamper: "Tamper", replay: "Replay", capacity: "Capacity", external: "CLI / API" };
const stageNames = { parse: "Message parsing", verify: "Integrity verification", capacity: "Capacity admission", dry_run: "Sandbox dry run", forward: "Endpoint delivery" };
const descriptions = {
  signed: "Sign the original script and run it through the complete pipeline.",
  tamper: "Modify the script after signing. The watchdog should reject it before execution.",
  replay: "Send the same signed request twice. Only the first should be accepted."
};
let toastTimer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", "icon");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").className = error ? "toast error" : "toast";
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 6000);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(path, {
      ...options, signal: controller.signal, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Dashboard-Token": $("meta[name=dashboard-token]").content, ...options.headers }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request is taking too long. Check activity before submitting again; execution may still be running.");
    if (error instanceof TypeError) throw new Error("Cannot reach the gateway. Check that the dashboard server is running.");
    throw error;
  } finally { clearTimeout(timeout); }
}

function setView(view) {
  if (!["overview", "workbench", "activity", "system"].includes(view)) view = "overview";
  document.body.dataset.view = view;
  const names = { overview: "Overview", workbench: "Workbench", activity: "Activity", system: "System status" };
  const titles = { overview: "Deployment overview", workbench: "Your delivery workbench", activity: "Every delivery has a story", system: "A pulse on your pipeline" };
  $("#breadcrumb-current").textContent = names[view];
  $("#page-title").textContent = titles[view];
  $$(".nav-item").forEach((button) => {
    const selected = button.dataset.view === view;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("#sidebar").classList.remove("open");
  $("#menu-button").setAttribute("aria-expanded", "false");
  history.replaceState(null, "", `#${view}`);
  if (view === "system") { refreshHealth(); refreshMetrics(); }
  window.scrollTo({ top: 0, behavior: "auto" });
}

function setMode(mode) {
  state.mode = mode;
  $$("[data-mode]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.mode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  $("#mode-description").textContent = descriptions[mode];
}

function updateEditor() {
  const code = $("#code-editor").value;
  const lines = code.split("\n").length;
  const bytes = new TextEncoder().encode(code).length;
  $("#line-numbers").textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
  $("#code-size").textContent = `${lines} ${lines === 1 ? "line" : "lines"} · ${(bytes / 1024).toFixed(1)} KB · UTF-8`;
  $("#run-button").disabled = state.busy || !code.trim();
  try { localStorage.setItem("relay-draft", JSON.stringify({ code, name: $("#filename").value })); } catch (_) { /* Draft persistence is optional. */ }
}

function loadExample(id, notify = true) {
  const example = state.examples.find((entry) => entry.id === id);
  if (!example) return;
  $("#code-editor").value = example.code;
  $("#filename").value = example.name;
  $("#example-select").value = id;
  updateEditor();
  if (notify) toast(`${example.title} sample loaded.`);
}

async function upload(file) {
  $("#file-input").value = "";
  if (!file) return;
  if (state.busy) return toast("Wait for the current run to finish.", true);
  if (!file.name.toLowerCase().endsWith(".py")) return toast("Choose a Python (.py) file.", true);
  if (file.size > 65536) return toast("Scripts must be 64 KB or smaller.", true);
  try {
    const content = await file.arrayBuffer();
    const code = new TextDecoder("utf-8", { fatal: true }).decode(content);
    $("#code-editor").value = code;
    $("#filename").value = file.name.slice(0, 120);
    $("#example-select").value = "";
    updateEditor();
    toast(`${file.name} is ready to submit.`);
  } catch (_) { toast("This file is not valid UTF-8 text. Save it as UTF-8 and try again.", true); }
  finally { $("#file-input").value = ""; }
}

function duration(milliseconds) {
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)} s` : `${Number(milliseconds).toFixed(1)} ms`;
}

function outcome(event) {
  if (event.status_code === 202) return { label: "Running", style: "running" };
  if (event.response.delivered) return { label: "Delivered", style: "success" };
  if (event.status_code >= 500 && event.response.stage !== "capacity") return { label: "Failed", style: "error" };
  return { label: "Blocked", style: "failure" };
}

function renderTrace(attempts) {
  const last = attempts[attempts.length - 1];
  const response = last.response;
  const status = outcome(last);
  $("#trace-badge").textContent = status.label;
  $("#trace-badge").className = `badge ${status.style}`;
  $("#trace-intro").textContent = `${last.name} · HTTP ${last.status_code}${attempts.length > 1 ? ` · ${attempts.length} attempts` : ""}`;
  const order = ["verify", "capacity", "dry_run", "forward"];
  const failedIndex = last.status_code === 202 ? -1 : response.delivered ? 4 : order.indexOf(response.stage);
  $$(".pipeline-stages li").forEach((item, index) => {
    item.className = last.status_code === 202 ? (index === 0 ? "passed" : "") : index < failedIndex ? "passed" : index === failedIndex ? "failed" : "skipped";
    const timing = response.timings_ms?.[`${item.dataset.stage}_ms`];
    item.querySelector(".stage-time").textContent = timing !== undefined ? duration(timing) : index < failedIndex ? "Passed" : index === failedIndex ? "Stopped" : "Skipped";
  });
  $("#execution-output").textContent = attempts.map((attempt, i) => {
    const result = attempt.response;
    const label = `Attempt ${i + 1} · HTTP ${attempt.status_code} · ${outcome(attempt).label}`;
    const output = result.endpoint_response?.stdout || result.reason || "Execution completed without output.";
    return `${label}\n${output}${attempt.journal_error ? `\n${attempt.journal_error}` : ""}`;
  }).join("\n\n");
  $("#trace-footnote").textContent = last.status_code === 202 ? "Cloud execution is still running. Keep this page open for the final result." : `Completed in ${duration(attempts.reduce((sum, item) => sum + item.duration_ms, 0))} · Original payload ${response.delivered ? "delivered" : "not delivered on this attempt"}`;
}

async function awaitCloud(attempts) {
  const deadline = Date.now() + 360000;
  while (attempts.some((item) => item.status_code === 202) && Date.now() < deadline) {
    renderTrace(attempts);
    await new Promise((resolve) => setTimeout(resolve, 2000));
    for (const item of attempts.filter((entry) => entry.status_code === 202)) {
      const status = await api(item.response.status_url);
      if (status.state === "finished" || status.state === "ambiguous") {
        item.status_code = status.status_code;
        item.response = status.response;
      }
    }
  }
  return attempts;
}

function setBusy(busy) {
  state.busy = busy;
  $("#run-button").disabled = busy || !$("#code-editor").value.trim();
  $("#run-button span").textContent = busy ? "Running…" : "Run pipeline";
  $("#capacity-test").disabled = busy;
  $("#run-button .icon").classList.toggle("spinning", busy);
  $(".composer-card").setAttribute("aria-busy", String(busy));
}

async function runPipeline(capacity = false) {
  if (state.busy) return;
  const code = $("#code-editor").value;
  if (!capacity && !code.trim()) return toast("Add a Python script first.", true);
  if (!capacity && new TextEncoder().encode(code).length > 65536) return toast("Scripts must be 64 KB or smaller.", true);
  if (capacity) setView("workbench");
  setBusy(true);
  $("#trace-badge").textContent = "Running";
  $("#trace-badge").className = "badge running";
  $("#trace-intro").textContent = capacity ? "Submitting concurrent capacity probes…" : "Processing your signed request…";
  $$(".pipeline-stages li").forEach((item) => { item.className = ""; item.querySelector(".stage-time").textContent = "—"; });
  $("#execution-output").textContent = "Waiting for the pipeline result. Resource tests can take up to 10 seconds.";
  $("#trace-footnote").textContent = "The request is in progress. Final stage results will appear when it finishes.";
  try {
    const data = await api(capacity ? "/api/capacity-test" : "/api/run", {
      method: "POST", body: JSON.stringify(capacity ? {} : { code, mode: state.mode, name: $("#filename").value || "untitled.py" })
    });
    await awaitCloud(data.attempts);
    renderTrace(data.attempts);
    if (capacity) {
      const rejected = data.attempts.filter((item) => item.response.stage === "capacity").length;
      toast(`Capacity test: ${data.submitted} submitted, ${rejected} rejected at capacity.`);
    } else toast(data.attempts.length > 1 ? "Replay test completed. Check both attempts below." : "Pipeline run completed. Results are ready.");
    if (data.attempts.some((entry) => entry.journal_error)) toast("Execution finished, but activity could not be saved. Check the gateway terminal.", true);
  } catch (error) {
    $("#trace-badge").textContent = "Unavailable";
    $("#trace-badge").className = "badge error";
    $("#trace-intro").textContent = "The request could not be completed.";
    $("#execution-output").textContent = error.message;
    $("#trace-footnote").textContent = "Check service health and activity before retrying.";
    toast(error.message, true);
  } finally { setBusy(false); await refreshOverview(); refreshHealth(); }
}

function filteredEvents() {
  const query = $("#activity-search").value.trim().toLowerCase();
  return state.events.filter((event) => {
    const accepted = state.filter === "all" || (state.filter === "delivered" ? event.response.delivered : !event.response.delivered);
    return accepted && `${event.name} ${event.scenario} ${event.response.reason || ""} ${event.id}`.toLowerCase().includes(query);
  });
}

function showDetail(event) {
  state.detail = event;
  $("#detail-title").textContent = event.name;
  $("#detail-meta").replaceChildren(element("span", "", `HTTP ${event.status_code}`), element("span", "", duration(event.duration_ms)), element("span", "", modeNames[event.scenario] || event.scenario));
  $("#detail-response").textContent = JSON.stringify(event, null, 2);
  $("#detail-dialog").showModal();
}

function renderActivity() {
  const events = filteredEvents();
  const rows = events.map((event) => {
    const row = element("tr", "table-row");
    const nameCell = element("td", "payload-cell");
    const fileIcon = element("span", "file-icon"); fileIcon.append(icon("code"));
    nameCell.append(fileIcon, element("span", "payload-name", event.name));
    const statusCell = element("td"); const status = outcome(event);
    statusCell.append(element("span", `badge ${status.style}`, status.label));
    const mode = element("td", "", modeNames[event.scenario] || event.scenario);
    const time = element("td", "", new Date(event.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    time.title = new Date(event.created_at * 1000).toLocaleString();
    const actionCell = element("td"); const detail = element("button", "row-detail", "↗");
    detail.setAttribute("aria-label", `View ${event.name} details`); actionCell.append(detail);
    row.append(nameCell, statusCell, mode, element("td", "", duration(event.duration_ms)), time, actionCell);
    row.addEventListener("click", () => showDetail(event));
    return row;
  });
  $("#activity-body").replaceChildren(...rows);
  $("#empty-state").hidden = events.length > 0;
  $("#empty-state h3").textContent = state.events.length ? "No matching submissions." : "A fresh start, a clear record.";
  $("#empty-state p").textContent = state.events.length ? "Try another search or activity filter." : "Run your first payload to see its journey here.";
  $("#history-count").textContent = state.events.length;
  $("#activity-summary").textContent = `Showing ${events.length} of ${state.events.length} recent submissions`;
  $("#export-button").disabled = events.length === 0;
}

function renderNotifications() {
  const failures = state.events.filter((event) => !event.response.delivered);
  $("#notification-dot").hidden = failures.length === 0;
  if (!failures.length) { $("#notification-content").textContent = "No failures recorded. A clear signal."; return; }
  $("#notification-content").replaceChildren(...failures.slice(0, 5).map((event) => {
    const button = element("button", "notification-entry");
    button.append(element("strong", "", event.name), element("small", "", `${stageNames[event.response.stage] || "Pipeline"} · HTTP ${event.status_code}`));
    button.addEventListener("click", () => { $("#notification-panel").hidden = true; $("#notification-button").setAttribute("aria-expanded", "false"); showDetail(event); });
    return button;
  }));
}

async function refreshOverview() {
  try {
    const data = await api("/api/overview");
    state.events = data.events;
    $("#stat-total").textContent = data.stats.total.toLocaleString();
    $("#stat-delivered").textContent = data.stats.delivered.toLocaleString();
    $("#stat-blocked").textContent = data.stats.blocked.toLocaleString();
    $("#stat-average").textContent = data.stats.total ? (data.stats.average_ms >= 1000 ? (data.stats.average_ms / 1000).toFixed(2) : data.stats.average_ms.toFixed(1)) : "—";
    $("#stat-unit").textContent = data.stats.total ? (data.stats.average_ms >= 1000 ? "s" : "ms") : "";
    $("#activity-count").textContent = data.stats.total;
    const percent = data.resources.total_mb ? Math.min(100, Math.round(data.resources.committed_mb / data.resources.total_mb * 100)) : 0;
    $("#memory-percent").textContent = `${percent}%`;
    $("#memory-fill").style.width = `${percent}%`;
    $("#memory-caption").textContent = `${data.resources.committed_mb} / ${data.resources.total_mb} MB · ${data.resources.slots} local slots`;
    $("#connection-banner").hidden = data.configured;
    if (!data.configured) $("#connection-banner").textContent = "The signing secret is not configured. Set SHARED_SECRET in .env and restart the dashboard.";
    renderActivity(); renderNotifications();
  } catch (error) {
    $("#connection-banner").textContent = `${error.message} Displayed activity may be out of date.`;
    $("#connection-banner").hidden = false;
  }
}

async function refreshHealth(force = false) {
  $("#refresh-health .icon").classList.add("spinning");
  try {
    const services = await api(force ? "/api/health?refresh=1" : "/api/health");
    const cloudCard = $(".cloud-card");
    const cloudStatus = services.eks?.status || "unknown";
    cloudCard.querySelector(".badge").textContent = cloudStatus.replaceAll("_", " ");
    cloudCard.querySelector(".badge").className = `badge ${cloudStatus === "online" ? "success" : "amber"}`;
    cloudCard.querySelector("p").textContent = cloudStatus === "online" ? "Cloud overflow is ready. Verified submissions use EKS when local sandbox slots are full." : cloudStatus === "disabled" ? "Cloud overflow is disabled in gateway configuration." : "Cloud overflow is currently unavailable. Check EKS credentials, context, and image settings.";
    cloudCard.querySelector(".cloud-note").textContent = cloudStatus === "online" ? "Overflow submissions receive HTTP 202 and run as EKS Jobs." : "Submissions that cannot use local capacity are rejected with HTTP 503.";
    const labels = { gateway: ["API gateway", "Verification, routing & delivery"], docker: ["Docker engine", "Linux container runtime"], image: ["Sandbox image", services.image?.name || "Runtime image"], endpoint: ["Endpoint device", "Original payload execution"], central: ["Central server", "Failure notification inbox"], eks: ["AWS EKS", "Cloud overflow execution"] };
    $("#service-list").replaceChildren(...Object.entries(labels).map(([key, [name, description]]) => {
      const service = services[key] || { status: "unknown" };
      const row = element("div", "service-row"); const copy = element("div");
      copy.append(element("strong", "", name), element("small", "", description));
      const healthy = ["online", "ready"].includes(service.status);
      row.append(copy, element("span", `badge ${healthy ? "success" : service.status === "not_connected" ? "neutral" : "failure"}`, service.status.replaceAll("_", " ")));
      return row;
    }));
  } catch (error) { $("#service-list").textContent = error.message; }
  finally { $("#refresh-health .icon").classList.remove("spinning"); }
}

function metricRow(label, value, maximum, detail) {
  const row = element("div", "metric-row");
  const heading = element("div", "metric-row-heading");
  heading.append(element("span", "", label), element("strong", "", detail));
  const track = element("div", "metric-track");
  const fill = element("div", "metric-fill");
  fill.style.width = `${maximum ? Math.max(0, Math.min(100, value / maximum * 100)) : 0}%`;
  track.append(fill);
  row.append(heading, track);
  return row;
}

async function refreshMetrics() {
  try {
    const data = await api("/api/metrics");
    const buckets = data.throughput || [];
    const maximum = Math.max(1, ...buckets.map((bucket) => bucket.count));
    $("#throughput-chart").replaceChildren(...buckets.map((bucket) => {
      const bar = element("div", "throughput-bar");
      bar.style.height = `${Math.max(3, bucket.count / maximum * 100)}%`;
      const second = new Date(bucket.start * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      bar.title = `${second}: ${bucket.count} submission${bucket.count === 1 ? "" : "s"}`;
      bar.setAttribute("aria-label", bar.title);
      return bar;
    }));
    $("#throughput-summary").textContent = `${data.submissions_per_second.toFixed(3)} submissions/s average · ${data.peak_per_second} peak in one second`;
    const local = data.locations.local;
    const eks = data.locations.eks;
    const total = local.count + eks.count + data.capacity_rejected;
    $("#capacity-chart").replaceChildren(
      metricRow("Local", local.count, total, `${local.count} / ${data.local_slots} slots`),
      metricRow("EKS", eks.count, total, `${eks.count} / ${data.cloud_limit} concurrent`),
      metricRow("Rejected at capacity", data.capacity_rejected, total, `${data.capacity_rejected}`)
    );
    $("#capacity-caption").textContent = `${total} final submissions in the last hour; bars show the split, while slot labels show configured concurrency.`;
    const stages = [["verify_ms", "Verification"], ["dry_run_ms", "Dry run"], ["forward_ms", "Forwarding"]];
    const allLatencies = stages.flatMap(([key]) => [local[`avg_${key}`], eks[`avg_${key}`]]);
    const maxLatency = Math.max(1, ...allLatencies);
    $("#latency-chart").replaceChildren(...stages.flatMap(([key, name]) => [
      metricRow(`Local ${name}`, local[`avg_${key}`], maxLatency, `${local[`avg_${key}`]} ms`),
      metricRow(`EKS ${name}`, eks[`avg_${key}`], maxLatency, `${eks[`avg_${key}`]} ms`)
    ]));
    const maxFailures = Math.max(1, local.dry_run_failures, eks.dry_run_failures);
    $("#failure-chart").replaceChildren(
      metricRow("Local dry-run blocked", local.dry_run_failures, maxFailures, String(local.dry_run_failures)),
      metricRow("EKS dry-run blocked", eks.dry_run_failures, maxFailures, String(eks.dry_run_failures))
    );
    $("#failure-summary").textContent = `Failure isolation: ${eks.dry_run_failures} EKS dry-run failures, ${local.dry_run_failures} local dry-run failures; ${local.delivered + eks.delivered} successful deliveries.`;
  } catch (error) {
    $("#throughput-chart").textContent = error.message;
    $("#capacity-chart").textContent = "Metrics unavailable.";
    $("#latency-chart").textContent = "Metrics unavailable.";
    $("#failure-chart").textContent = "Metrics unavailable.";
  }
}

function exportCsv() {
  const events = filteredEvents();
  if (!events.length) return;
  const safeCell = (value) => {
    let text = String(value ?? "");
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  const rows = [["ID", "Payload", "Mode", "HTTP status", "Delivered", "Stage", "Duration ms", "Timestamp", "Reason"], ...events.map((event) => [event.id, event.name, event.scenario, event.status_code, event.response.delivered, event.response.stage || "delivered", event.duration_ms, new Date(event.created_at * 1000).toISOString(), event.response.reason || ""])];
  const url = URL.createObjectURL(new Blob(["\uFEFF" + rows.map((row) => row.map(safeCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" }));
  const link = element("a"); link.href = url; link.download = `relay-activity-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`Exported ${events.length} submissions.`);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied to clipboard."); }
  catch (_) { toast("Clipboard access is unavailable. Select and copy the text manually.", true); }
}

async function init() {
  $("#today").textContent = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date());
  $$("[data-view]").filter((node) => node.tagName === "BUTTON").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $(".brand").addEventListener("click", () => setView("overview"));
  $("#new-submission").addEventListener("click", () => { setView("workbench"); $("#code-editor").focus(); });
  $("#menu-button").addEventListener("click", () => { const open = $("#sidebar").classList.toggle("open"); $("#menu-button").setAttribute("aria-expanded", String(open)); });
  $$("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $$("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    $$("[data-filter]").forEach((node) => { node.classList.toggle("selected", node === button); node.setAttribute("aria-pressed", String(node === button)); });
    renderActivity();
  }));
  $("#activity-search").addEventListener("input", renderActivity);
  $("#code-editor").addEventListener("input", updateEditor);
  $("#filename").addEventListener("input", updateEditor);
  $("#code-editor").addEventListener("scroll", () => { $("#line-numbers").scrollTop = $("#code-editor").scrollTop; });
  $("#code-editor").addEventListener("keydown", (event) => {
    if (event.key === "Tab") { event.preventDefault(); const editor = event.target; editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end"); updateEditor(); }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); runPipeline(); }
  });
  $("#file-input").addEventListener("change", (event) => upload(event.target.files[0]));
  ["dragenter", "dragover"].forEach((type) => $("#drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("#drop-zone").classList.add("drag-over"); }));
  ["dragleave", "drop"].forEach((type) => $("#drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("#drop-zone").classList.remove("drag-over"); }));
  $("#drop-zone").addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));
  $("#example-select").addEventListener("change", (event) => loadExample(event.target.value));
  $("#run-button").addEventListener("click", () => runPipeline());
  $("#capacity-test").addEventListener("click", () => runPipeline(true));
  $("#integrity-test").addEventListener("click", () => { setView("workbench"); loadExample("good", false); setMode("tamper"); });
  $("#resource-test").addEventListener("click", () => { setView("workbench"); loadExample("infinite_loop", false); setMode("signed"); });
  $("#refresh-health").addEventListener("click", () => refreshHealth(true));
  $("#export-button").addEventListener("click", exportCsv);
  $("#copy-output").addEventListener("click", () => copyText($("#execution-output").textContent));
  $("#copy-detail").addEventListener("click", () => copyText($("#detail-response").textContent));
  ["#close-detail", "#done-detail"].forEach((id) => $(id).addEventListener("click", () => $("#detail-dialog").close()));
  $("#detail-dialog").addEventListener("click", (event) => { if (event.target === $("#detail-dialog")) { const box = event.target.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close(); } });
  $("#notification-button").addEventListener("click", () => { const panel = $("#notification-panel"); panel.hidden = !panel.hidden; $("#notification-button").setAttribute("aria-expanded", String(!panel.hidden)); });
  document.addEventListener("click", (event) => { if (!event.target.closest("#notification-panel, #notification-button")) { $("#notification-panel").hidden = true; $("#notification-button").setAttribute("aria-expanded", "false"); } });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { $("#notification-panel").hidden = true; $("#notification-button").setAttribute("aria-expanded", "false"); $("#sidebar").classList.remove("open"); $("#menu-button").setAttribute("aria-expanded", "false"); } });
  try { const draft = JSON.parse(localStorage.getItem("relay-draft")); if (draft && typeof draft.code === "string" && draft.code.length <= 65536) { $("#code-editor").value = draft.code; $("#filename").value = typeof draft.name === "string" ? draft.name.slice(0, 120) : "untitled.py"; } } catch (_) { /* Keep default script if storage is unavailable. */ }
  updateEditor();
  setView(location.hash.slice(1));
  try {
    const data = await api("/api/examples"); state.examples = data.examples;
    for (const example of data.examples) { const option = element("option", "", example.title); option.value = example.id; $("#example-select").append(option); }
  } catch (error) { toast(error.message, true); }
  await refreshOverview(); refreshHealth();
  setInterval(() => { if (!document.hidden) refreshOverview(); }, 4000);
  setInterval(() => { if (!document.hidden && document.body.dataset.view === "system") { refreshHealth(); refreshMetrics(); } }, 15000);
}

init().catch((error) => toast(`Dashboard could not initialize: ${error.message}`, true));
