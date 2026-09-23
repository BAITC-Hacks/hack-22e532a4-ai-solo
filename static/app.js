const state = {
  comparisonId: localStorage.getItem("baqbaq-comparison"),
  comparison: null,
  result: null,
  selectedId: null,
  busy: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const typeLabels = {
  structure_preserved: "Структура · сохранено", structure_added: "Структура · добавлено",
  preserved: "Сохранено", changed: "Изменено", transferred: "Перенос", split: "Разделение",
  merged: "Объединение", new: "Новое", potential_loss: "Потенциальная потеря",
  possible_duplicate: "Возможное дублирование", potential_conflict: "Потенциальный конфликт",
  insufficient_data: "Недостаточно данных",
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || body || `Ошибка ${response.status}`);
  return body;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  window.setTimeout(() => node.classList.add("hidden"), 3200);
}

function showError(selector, message) {
  const node = $(selector);
  node.textContent = message;
  node.classList.remove("hidden");
}

function clearError(selector) { $(selector).classList.add("hidden"); }

async function ensureComparison() {
  if (state.comparisonId) {
    try { state.comparison = await api(`/api/comparisons/${state.comparisonId}`); return; }
    catch { localStorage.removeItem("baqbaq-comparison"); state.comparisonId = null; }
  }
  const created = await api("/api/comparisons", {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ name: "Сверка положений внутреннего аудита" }),
  });
  state.comparisonId = created.id;
  localStorage.setItem("baqbaq-comparison", created.id);
  state.comparison = { ...created, documents: [] };
}

async function refreshComparison() {
  state.comparison = await api(`/api/comparisons/${state.comparisonId}`);
  renderDocuments();
}

function renderDocuments() {
  for (const side of ["before", "after"]) {
    const docs = (state.comparison?.documents || []).filter((doc) => doc.side === side);
    const target = $(`#${side}-docs`);
    if (!docs.length) { target.innerHTML = '<p class="empty-mini">Файлы ещё не добавлены</p>'; continue; }
    target.innerHTML = docs.map((doc) => `
      <div class="document-item">
        <span class="file-type">${escapeHtml(doc.file_type)}</span>
        <span class="document-name"><strong title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</strong><small>${escapeHtml(doc.revision)} · ${doc.span_count} фрагм.</small></span>
        <span class="doc-status ${doc.status === "ready" ? "" : "attention"}">${doc.status === "ready" ? "Готов" : "Проверить"}</span>
      </div>`).join("");
  }
  const beforeReady = state.comparison?.documents?.some((doc) => doc.side === "before" && doc.status === "ready");
  const afterReady = state.comparison?.documents?.some((doc) => doc.side === "after" && doc.status === "ready");
  const ready = beforeReady && afterReady;
  $("#analyze-button").disabled = !ready || state.busy;
  const readiness = $("#readiness");
  readiness.classList.toggle("ready", ready);
  readiness.querySelector(".readiness-icon").textContent = `${Number(beforeReady) + Number(afterReady)}/2`;
  readiness.querySelector("strong").textContent = ready ? "Комплекты готовы к анализу" : "Нужны оба комплекта";
  readiness.querySelector("small").textContent = ready ? "Источники разобраны и закреплены за версиями" : "Добавьте хотя бы по одному читаемому документу";
}

async function upload(side, files) {
  if (!files?.length || state.busy) return;
  clearError("#upload-error");
  setBusy(true, "Извлекаем текст и устойчивые локаторы…");
  const data = new FormData();
  [...files].forEach((file) => data.append("files", file));
  try {
    await api(`/api/comparisons/${state.comparisonId}/documents/${side}`, { method: "POST", body: data });
    await refreshComparison();
    toast("Документы загружены и разобраны");
  } catch (error) { showError("#upload-error", error.message); }
  finally { setBusy(false); }
}

function setBusy(busy, text = "") {
  state.busy = busy;
  $("#progress").classList.toggle("hidden", !busy);
  if (text) $("#progress-text").textContent = text;
  $("#demo-button").disabled = busy;
  $("#analyze-button").disabled = busy || !state.comparison?.documents?.some((d) => d.side === "before" && d.status === "ready") || !state.comparison?.documents?.some((d) => d.side === "after" && d.status === "ready");
  $("#analyze-button").setAttribute("aria-busy", String(busy));
}

async function loadDemo() {
  clearError("#upload-error");
  setBusy(true, "Читаем контрольные редакции 8 и 9…");
  try {
    await api(`/api/comparisons/${state.comparisonId}/demo`, { method: "POST" });
    await refreshComparison();
    toast("Контрольная пара загружена через обычный parser pipeline");
  } catch (error) { showError("#upload-error", error.message); }
  finally { setBusy(false); }
}

async function analyze() {
  clearError("#result-error");
  setBusy(true, "Строим реестр структуры и атомарных функций…");
  $("#empty-state").classList.add("hidden");
  $("#results-content").classList.add("hidden");
  const stages = ["Сопоставляем функции по смыслу…", "Проверяем покрытие и возможные пересечения…", "Разрешаем ссылки на точные источники…"];
  let stage = 0;
  const timer = window.setInterval(() => { $("#progress-text").textContent = stages[Math.min(stage++, stages.length - 1)]; }, 1800);
  try {
    state.result = await api(`/api/comparisons/${state.comparisonId}/analyze`, { method: "POST" });
    renderResult();
    $("#results-section").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Анализ завершён, источники проверены");
  } catch (error) {
    const box = $("#result-error");
    box.querySelector("span").textContent = error.message;
    box.classList.remove("hidden");
    $("#empty-state").classList.remove("hidden");
  } finally { window.clearInterval(timer); setBusy(false); }
}

function badgeClass(type) {
  if (["potential_loss", "possible_duplicate", "potential_conflict"].includes(type)) return "risk";
  if (["preserved", "structure_preserved"].includes(type)) return "verified";
  return "change";
}

function renderResult() {
  const findings = state.result?.findings || [];
  $("#empty-state").classList.toggle("hidden", findings.length > 0);
  $("#results-content").classList.toggle("hidden", findings.length === 0);
  if (!findings.length) return;
  const counts = state.result.summary || {};
  const risks = (counts.potential_loss || 0) + (counts.possible_duplicate || 0) + (counts.potential_conflict || 0);
  const matched = (counts.preserved || 0) + (counts.transferred || 0) + (counts.changed || 0) + (counts.split || 0);
  const verified = findings.filter((item) => item.evidence_status === "MATCH").length;
  $("#summary-cards").innerHTML = `
    <article class="summary-card teal"><span>Выводов с основаниями</span><strong>${verified}</strong></article>
    <article class="summary-card"><span>Сопоставлено функций</span><strong>${matched}</strong></article>
    <article class="summary-card ochre"><span>Требуют проверки</span><strong>${findings.length - verified}</strong></article>
    <article class="summary-card rust"><span>Индикаторов риска</span><strong>${risks}</strong></article>`;
  $("#report-md").classList.remove("disabled"); $("#report-md").setAttribute("aria-disabled", "false");
  $("#report-html").classList.remove("disabled"); $("#report-html").setAttribute("aria-disabled", "false");
  $("#report-md").href = `/api/comparisons/${state.comparisonId}/report?format=markdown`;
  $("#report-html").href = `/api/comparisons/${state.comparisonId}/report?format=html`;
  renderFindings();
  const params = new URLSearchParams(location.search);
  const selected = params.get("finding");
  const visibleIds = $$(".row-select").map((button) => button.dataset.id);
  selectFinding(visibleIds.includes(selected) ? selected : visibleIds[0] || findings[0].id, false);
}

function typeFilterMatch(item, filter) {
  const type = item.finding_type;
  if (!filter) return true;
  if (filter === "focus") return ["structure_added", "changed", "split", "merged", "potential_loss", "possible_duplicate", "potential_conflict", "new"].includes(type) || (type === "transferred" && item.confidence < .75);
  if (filter === "attention") return !["preserved", "structure_preserved"].includes(type);
  if (filter === "structure") return type.startsWith("structure_");
  if (filter === "changed") return ["changed", "split", "merged"].includes(type);
  return type === filter;
}

function renderFindings() {
  const query = $("#result-search").value.trim().toLowerCase();
  const type = $("#type-filter").value;
  const evidence = $("#evidence-filter").value;
  const filtered = (state.result?.findings || []).filter((item) => {
    const haystack = [item.title, item.summary, item.before_owner, item.after_owner, item.before_function, item.after_function, item.before_source?.clause_label, item.after_source?.clause_label].join(" ").toLowerCase();
    return (!query || haystack.includes(query)) && typeFilterMatch(item, type) && (!evidence || item.evidence_status === evidence);
  });
  $("#no-filter-results").classList.toggle("hidden", filtered.length > 0);
  $("#findings-body").innerHTML = filtered.map((item) => `
    <tr data-id="${item.id}" class="${item.id === state.selectedId ? "selected" : ""}">
      <td><button class="row-select" type="button" data-id="${item.id}"><span class="finding-badge ${badgeClass(item.finding_type)}">${escapeHtml(typeLabels[item.finding_type] || item.finding_type)}</span></button></td>
      <td>${item.before_owner ? `<span class="owner">${escapeHtml(item.before_owner)}</span><span class="function-text">${escapeHtml(item.before_function || "")}</span>` : '<span class="dash">Нет источника на стороне «До»</span>'}</td>
      <td>${item.after_owner ? `<span class="owner">${escapeHtml(item.after_owner)}</span><span class="function-text">${escapeHtml(item.after_function || "")}</span>` : '<span class="dash">Эквивалент не найден</span>'}</td>
      <td><span class="evidence-status ${item.evidence_status.toLowerCase()}">${item.evidence_status === "MATCH" ? "Подтверждено" : "Проверить"}</span><span class="function-text">${escapeHtml(item.summary)}</span></td>
    </tr>`).join("");
  $$(".row-select").forEach((button) => button.addEventListener("click", () => selectFinding(button.dataset.id)));
}

function sourceMeta(source) {
  if (!source) return "Источник отсутствует для этой стороны вывода";
  return `${source.filename} · ${source.revision} · ${source.clause_label ? `пункт ${source.clause_label}` : source.locator}`;
}

function selectFinding(id, updateUrl = true) {
  const item = state.result?.findings?.find((finding) => finding.id === id);
  if (!item) return;
  state.selectedId = id;
  $("#evidence-placeholder").classList.add("hidden");
  $("#evidence-content").classList.remove("hidden");
  $("#detail-type").textContent = typeLabels[item.finding_type] || item.finding_type;
  $("#detail-type").className = `finding-badge ${badgeClass(item.finding_type)}`;
  $("#detail-confidence").textContent = `${Math.round(item.confidence * 100)}% уверенности`;
  $("#detail-title").textContent = item.title;
  $("#detail-summary").textContent = item.summary;
  $("#before-meta").textContent = sourceMeta(item.before_source);
  $("#after-meta").textContent = sourceMeta(item.after_source);
  $("#before-quote").textContent = item.before_source?.original_text || "Для новой функции источник на стороне «До» отсутствует.";
  $("#after-quote").textContent = item.after_source?.original_text || "Эквивалентный фрагмент в загруженном комплекте «После» не найден.";
  $("#detail-limit").textContent = item.limitations;
  $("#review-status").textContent = item.review ? `Последнее решение: ${decisionLabel(item.review.decision)}` : "Решение ещё не зафиксировано";
  $$("#review-buttons button").forEach((button) => button.classList.toggle("active", item.review?.decision === button.dataset.decision));
  $("#trace-count").textContent = `(${state.result.trace.length})`;
  $("#trace-list").innerHTML = state.result.trace.map((event) => `<li><strong>${escapeHtml(event.tool)}</strong> · ${event.duration_ms} мс<code>${escapeHtml(event.result)}</code></li>`).join("");
  $("#agent-answer").classList.add("hidden");
  renderFindings();
  if (updateUrl) { const params = new URLSearchParams(location.search); params.set("finding", id); history.replaceState(null, "", `${location.pathname}?${params}`); }
}

function decisionLabel(value) { return ({ confirmed: "подтверждено", rejected: "отклонено", review: "проверить" })[value] || value; }

async function saveReview(decision) {
  if (!state.selectedId) return;
  try {
    const review = await api(`/api/findings/${state.selectedId}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, note: "" }) });
    const item = state.result.findings.find((f) => f.id === state.selectedId); item.review = review;
    selectFinding(state.selectedId, false); toast("Решение сохранено отдельно от машинного вывода");
  } catch (error) { $("#review-status").textContent = error.message; }
}

async function askAgent(event) {
  event.preventDefault();
  const question = $("#agent-question").value.trim();
  if (question.length < 3) return;
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true; button.setAttribute("aria-busy", "true"); button.textContent = "Ищем основания…";
  try {
    const message = await api(`/api/comparisons/${state.comparisonId}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, finding_id: state.selectedId }) });
    const answer = $("#agent-answer"); answer.textContent = message.answer; answer.classList.remove("hidden");
  } catch (error) { const answer = $("#agent-answer"); answer.textContent = error.message; answer.classList.remove("hidden"); }
  finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = "Задать вопрос"; }
}

function wireEvents() {
  $("#before-files").addEventListener("change", (event) => upload("before", event.target.files));
  $("#after-files").addEventListener("change", (event) => upload("after", event.target.files));
  $("#demo-button").addEventListener("click", loadDemo);
  $("#analyze-button").addEventListener("click", analyze);
  $("#retry-analysis").addEventListener("click", analyze);
  $("#result-search").addEventListener("input", renderFindings);
  $("#type-filter").addEventListener("change", renderFindings);
  $("#evidence-filter").addEventListener("change", renderFindings);
  $("#review-buttons").addEventListener("click", (event) => { const button = event.target.closest("button[data-decision]"); if (button) saveReview(button.dataset.decision); });
  $("#ask-form").addEventListener("submit", askAgent);
  $("#theme-toggle").addEventListener("click", () => { const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; localStorage.setItem("baqbaq-theme", next); });
  for (const card of $$(".upload-card")) {
    const side = card.dataset.side; const zone = card.querySelector(".drop-zone");
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("dragging"); }));
    zone.addEventListener("drop", (event) => upload(side, event.dataTransfer.files));
  }
}

async function init() {
  document.documentElement.dataset.theme = localStorage.getItem("baqbaq-theme") || "light";
  wireEvents();
  try {
    const health = await api("/api/health");
    const modeLong = health.model_mode === "live" ? `Live · ${escapeHtml(health.model)}` : "Offline · локальный анализ";
    const modeShort = health.model_mode === "live" ? "Live" : "Offline";
    $("#mode-badge").innerHTML = `<span class="status-dot"></span><span class="mode-long">${modeLong}</span><span class="mode-short">${modeShort}</span>`;
    await ensureComparison();
    await refreshComparison();
    const current = await api(`/api/comparisons/${state.comparisonId}/result`);
    if (current?.run?.status === "completed") { state.result = current; renderResult(); }
  } catch (error) { showError("#upload-error", `Не удалось запустить рабочее место: ${error.message}`); }
}

init();
