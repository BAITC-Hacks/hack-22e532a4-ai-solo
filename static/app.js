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
  structure_removed: 'Структура · не найдено',
  structure_transformed: 'Структура · преобразование',
  preserved: "Сохранено", changed: "Изменено", transferred: "Перенос", split: "Разделение",
  merged: "Объединение", new: "Новое", potential_loss: "Потенциальная потеря",
  possible_duplicate: "Возможное дублирование", potential_conflict: "Потенциальный конфликт",
  insufficient_data: "Недостаточно данных",
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(response.status===401 ? 'Требуется вход жюри. Откройте /workspace и введите выданный доступ.' : response.status===429 ? 'Слишком много запросов. Подождите минуту и повторите.' : (body?.detail || (typeof body==='string' && body.length<300 ? body : `Ошибка ${response.status}. Повторите запрос.`)));
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
    body: JSON.stringify({ name: "Проверка документов", model_mode:'live' }),
  });
  state.comparisonId = created.id;
  localStorage.setItem("baqbaq-comparison", created.id);
  state.comparison = { ...created, documents: [] };
}

async function refreshComparison() {
  state.comparison = await api(`/api/comparisons/${state.comparisonId}`);
  if (state.health) {
    const actualMode = state.comparison.model_mode;
    const text = actualMode === 'live' ? 'Live · подключён' : 'Локальный расчёт';
    $('#mode-badge').innerHTML = `<span class="status-dot"></span><span class="mode-long">${escapeHtml(text)}</span><span class="mode-short">${actualMode === 'live' ? 'Live' : 'Offline'}</span>`;
  }
  renderDocuments();
  await refreshComparisons();
}

async function refreshComparisons() {
  const items=await api('/api/comparisons');
  $('#comparison-history').innerHTML=items.map(c=>`<option value="${escapeHtml(c.id)}" ${c.id===state.comparisonId?'selected':''}>${escapeHtml(c.name)} · ${escapeHtml(c.model_mode)} · ${escapeHtml(new Date(c.created_at).toLocaleTimeString('ru-RU'))}</option>`).join('');
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
        <span class="doc-status ${doc.status === "ready" && !doc.warnings?.length ? "" : "attention"}" title="${escapeHtml((doc.warnings || []).join('; '))}">${doc.status === "ready" && !doc.warnings?.length ? "Готов" : "Проверить"}</span>
      </div>${doc.warnings?.length ? `<p class="document-warning">${escapeHtml(doc.warnings.join('; '))}</p>` : ''}`).join("");
  }
  const beforeReady = state.comparison?.documents?.some((doc) => doc.side === "before" && doc.status === "ready");
  const afterReady = state.comparison?.documents?.some((doc) => doc.side === "after" && doc.status === "ready");
  const ready = beforeReady && afterReady;
  $("#analyze-button").disabled = !ready || state.busy;
  const readiness = $("#readiness");
  readiness.classList.toggle("ready", ready);
  readiness.querySelector(".readiness-icon").textContent = `${Number(beforeReady) + Number(afterReady)}/2`;
  const partial=(state.comparison?.documents||[]).some(d=>d.status!=='ready'||d.warnings?.length);
  readiness.querySelector("strong").textContent = ready ? (partial ? 'Комплект неполный — результат будет предварительным' : 'Комплекты готовы к анализу') : "Нужны оба комплекта";
  readiness.querySelector("small").textContent = ready ? "Будут проверены функции, ответственность и изменения структуры" : "Добавьте хотя бы по одному документу каждой редакции";
  if(state.health && !state.health.live_ready) {
    $('#analyze-button').disabled=true;
    readiness.querySelector('small').textContent='Для Live-анализа настройте OPENAI_API_KEY в .env и перезапустите сервер.';
  }
}

async function upload(side, files) {
  if (!files?.length || state.busy) return;
  clearError("#upload-error");
  setBusy(true, "Читаем документы и сохраняем ссылки на пункты…");
  const data = new FormData();
  [...files].forEach((file) => data.append("files", file));
  try {
    await api(`/api/comparisons/${state.comparisonId}/documents/${side}`, { method: "POST", body: data });
    await refreshComparison();
    toast("Документы загружены и разобраны");
  } catch (error) { await refreshComparison(); showError("#upload-error", error.message+' Непрочитанный файл учтён в комплекте. Для проверки без него создайте новое сравнение.'); }
  finally { setBusy(false); }
}

function setBusy(busy, text = "") {
  state.busy = busy;
  $('#new-comparison').disabled = busy;
  $('#run-history').disabled = busy;
  $('#comparison-history').disabled = busy;
  $('#model-mode').disabled = busy;
  for(const selector of ['#report-md','#report-html']) {$(selector).classList.toggle('disabled',busy || !state.result?.run);$(selector).setAttribute('aria-disabled',String(busy || !state.result?.run));}
  $$('.upload-card input').forEach(input => { input.disabled = busy; });
  $("#progress").classList.toggle("hidden", !busy);
  if (text) $("#progress-text").textContent = text;
  $("#analyze-button").disabled = busy || !state.comparison?.documents?.some((d) => d.side === "before" && d.status === "ready") || !state.comparison?.documents?.some((d) => d.side === "after" && d.status === "ready");
  $("#analyze-button").setAttribute("aria-busy", String(busy));
  if(state.health && !state.health.live_ready) $('#analyze-button').disabled=true;
}

async function analyze() {
  clearError("#result-error");
  setBusy(true, "Строим реестр структуры и атомарных функций…");
  $("#empty-state").classList.add("hidden");
  $("#results-content").classList.add("hidden");
  const stageNames = {extract_registry:'Извлечён реестр', prepare_search_index:'Подготовлен поиск', compare_functions:'Найдены соответствия', check_coverage:'Проверено покрытие', check_overlap_and_independence:'Проверены пересечения', resolve_sources:'Проверены ссылки', model_extract_functions:'Модель извлекла функции', model_match_functions:'Модель сопоставила функции', model_review:'Завершена проверка модели'};
  const timer = window.setInterval(async () => {
    try {
      const progress = await api(`/api/comparisons/${state.comparisonId}/progress`);
      if (state.busy && progress.last_completed_tool) $("#progress-text").textContent = `${stageNames[progress.last_completed_tool] || progress.last_completed_tool}. Анализ продолжается…`;
    } catch { /* Progress failure does not replace the analysis response. */ }
  }, 1800);
  try {
    state.result = await api(`/api/comparisons/${state.comparisonId}/analyze`, { method: "POST" });
    await refreshRuns();
    renderResult();
    $("#results-section").scrollIntoView({ behavior: "smooth", block: "start" });
    toast(state.result.run.status === 'partial' ? 'Анализ неполный: проверьте источники и предупреждения' : 'Анализ завершён, источники проверены');
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
  $('#partial-warning').classList.toggle('hidden', state.result?.run?.status !== 'partial');
  const warnings=state.result?.metadata?.extraction_warnings || [];
  $('#partial-warning').textContent='Неполный анализ. '+(warnings.length ? `Предупреждений проверки модели: ${warnings.length}. `+[...new Set(warnings.map(w=>w.reason))].join('; ')+'. ' : 'Проверьте предупреждения документов. ')+'Отсутствие вывода не означает отсутствие риска.';
  $("#empty-state").classList.toggle("hidden", findings.length > 0);
  $("#results-content").classList.toggle("hidden", findings.length === 0);
  if (!findings.length) {
    $('#evidence-panel').classList.add('hidden');
    for (const id of ['#report-md','#report-html']) { $(id).classList.add('disabled'); $(id).setAttribute('aria-disabled','true'); $(id).href='#'; }
    return;
  }
  const counts = state.result.summary || {};
  $('#evidence-panel').classList.remove('hidden');
  const risks = (counts.potential_loss || 0) + (counts.possible_duplicate || 0) + (counts.potential_conflict || 0);
  const matched = new Set(findings.filter(f=>['preserved','transferred','changed','split','merged'].includes(f.finding_type)).map(f=>`${f.before_span_id}|${f.before_function}`)).size;
  const sourced = findings.filter(item=>item.before_source || item.after_source).length;
  const pending = findings.filter(item=>!item.review || item.review.decision==='review').length;
  $("#summary-cards").innerHTML = `
    <article class="summary-card teal"><span>Выводов с источниками</span><strong>${sourced}</strong></article>
    <article class="summary-card"><span>Исходных функций сопоставлено</span><strong>${matched}</strong></article>
    <article class="summary-card ochre"><span>Ожидают решения человека</span><strong>${pending}</strong></article>
    <article class="summary-card rust"><span>Индикаторов риска</span><strong>${risks}</strong></article>`;
  $("#report-md").classList.remove("disabled"); $("#report-md").setAttribute("aria-disabled", "false");
  $("#report-html").classList.remove("disabled"); $("#report-html").setAttribute("aria-disabled", "false");
  $("#report-md").href = `/api/comparisons/${state.comparisonId}/report?format=markdown&run_id=${state.result.run.id}`;
  $("#report-html").href = `/api/comparisons/${state.comparisonId}/report?format=html&run_id=${state.result.run.id}`;
  renderFindings();
  const params = new URLSearchParams(location.search);
  const selected = params.get("finding");
  const visibleIds = $$(".row-select").map((button) => button.dataset.id);
  selectFinding(visibleIds.includes(selected) ? selected : visibleIds[0] || findings[0].id, false);
}

function typeFilterMatch(item, filter) {
  const type = item.finding_type;
  if (!filter) return true;
  if (filter === "focus") return !['preserved','structure_preserved'].includes(type);
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
      <td><button class="row-select" type="button" data-id="${item.id}" aria-label="${escapeHtml(item.title+' · '+(item.before_owner||item.after_owner||''))}"><span class="finding-badge ${badgeClass(item.finding_type)}">${escapeHtml(typeLabels[item.finding_type] || item.finding_type)}</span></button></td>
      <td>${item.before_owner ? `<span class="owner">${escapeHtml(item.before_owner)}</span><span class="function-text">${escapeHtml(item.before_function || "")}</span>` : '<span class="dash">Нет источника на стороне «До»</span>'}</td>
      <td>${item.after_owner ? `<span class="owner">${escapeHtml(item.after_owner)}</span><span class="function-text">${escapeHtml(item.after_function || "")}</span>` : '<span class="dash">Эквивалент не найден</span>'}</td>
      <td><span class="evidence-status ${item.evidence_status.toLowerCase()}">${item.evidence_status === "MATCH" ? "Точное основание" : "Гипотеза алгоритма"}</span><span class="function-text">${escapeHtml(item.review ? 'Сотрудник: '+decisionLabel(item.review.decision) : 'Решение сотрудника не принято')}</span></td>
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
  $("#detail-confidence").textContent = item.evidence_status==='MATCH' ? 'Точное основание в тексте' : 'Интерпретация требует проверки';
  $("#detail-title").textContent = item.title;
  $("#detail-summary").textContent = item.summary;
  $("#before-meta").textContent = sourceMeta(item.before_source);
  $("#after-meta").textContent = sourceMeta(item.after_source);
  $('#before-source-side').textContent = item.before_source?.side === 'after' ? 'После · первый источник' : 'До';
  $('#after-source-side').textContent = item.after_source?.side === 'before' ? 'До · второй источник' : 'После';
  $("#before-quote").textContent = item.before_source?.original_text || "Для новой функции источник на стороне «До» отсутствует.";
  $("#after-quote").textContent = item.after_source?.original_text || "Эквивалентный фрагмент в загруженном комплекте «После» не найден.";
  if ($('#show-diff')?.checked && item.before_source && item.after_source) {
    const before=item.before_source.original_text, after=item.after_source.original_text;
    $('#before-quote').innerHTML=markedDifference(before,after);
    $('#after-quote').innerHTML=markedDifference(after,before);
  }
  $("#detail-limit").textContent = item.limitations;
  const context=[...(item.before_context||[]),...(item.after_context||[])].filter(Boolean);
  $('#source-context').classList.toggle('hidden',!context.length);
  $('#source-context-text').innerHTML=context.map(s=>`<p class="source-meta">${escapeHtml(sourceMeta(s))}</p><blockquote>${escapeHtml(s.original_text)}</blockquote>`).join('');
  $("#review-status").textContent = item.review ? `Последнее решение: ${decisionLabel(item.review.decision)}` : "Решение ещё не зафиксировано";
  $('#review-note').value = item.review?.note || '';
  $('#review-history').innerHTML = (item.review_history || []).map(review => `<li>${escapeHtml(new Date(review.created_at).toLocaleString('ru-RU'))} · ${escapeHtml(decisionLabel(review.decision))} · ${escapeHtml(review.note)}</li>`).join('');
  $$("#review-buttons button").forEach((button) => button.classList.toggle("active", item.review?.decision === button.dataset.decision));
  $("#trace-count").textContent = `(${state.result.trace.length})`;
  $("#trace-list").innerHTML = state.result.trace.map((event) => `<li><strong>${escapeHtml(event.tool)}</strong> · ${event.duration_ms} мс<code>${escapeHtml(event.result)}</code></li>`).join("");
  $("#agent-answer").classList.add("hidden");
  renderFindings();
  if (updateUrl) { const params = new URLSearchParams(location.search); params.set("finding", id); history.replaceState(null, "", `${location.pathname}?${params}`); }
}

function decisionLabel(value) { return ({ confirmed: "подтверждено", rejected: "отклонено", review: "проверить" })[value] || value; }

function markedDifference(text, other) {
  const words=new Set(other.toLowerCase().split(/\s+/));
  return text.split(/(\s+)/).map(word=>!word.trim() || words.has(word.toLowerCase()) ? escapeHtml(word) : `<mark>${escapeHtml(word)}</mark>`).join('');
}

async function saveReview(decision) {
  if (!state.selectedId) return;
  try {
    const review = await api(`/api/findings/${state.selectedId}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, note: $('#review-note').value }) });
    const item = state.result.findings.find((f) => f.id === state.selectedId); item.review = review;
    item.review_history = [...(item.review_history || []), review];
    const selected=state.selectedId; renderResult(); selectFinding(selected, false); toast("Решение сохранено отдельно от машинного вывода");
  } catch (error) { $("#review-status").textContent = error.message; }
}

async function askAgent(event) {
  event.preventDefault();
  const question = $("#agent-question").value.trim();
  if (question.length < 3) return;
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true; button.setAttribute("aria-busy", "true"); button.textContent = "Ищем основания…";
  try {
    const message = await api(`/api/comparisons/${state.comparisonId}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, finding_id: state.selectedId, run_id: state.result?.run?.id }) });
    const answer = $("#agent-answer"); answer.textContent = message.answer + '\n\nЖурнал: ' + (message.trace || []).map(x => `${x.tool} · ${x.status} · ${x.duration_ms} мс`).join('; '); answer.classList.remove("hidden");
  } catch (error) { const answer = $("#agent-answer"); answer.textContent = error.message; answer.classList.remove("hidden"); }
  finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = "Задать вопрос"; }
}

function wireEvents() {
  $('#show-diff')?.addEventListener('change',()=>selectFinding(state.selectedId,false));
  $('#comparison-history').addEventListener('change',async event=>{
    if(state.busy)return;
    state.comparisonId=event.target.value;state.selectedId=null;state.result=null;
    localStorage.setItem('baqbaq-comparison',state.comparisonId);
    history.replaceState(null,'',location.pathname);
    await refreshComparison();await refreshRuns();
    state.result=await api(`/api/comparisons/${state.comparisonId}/result`);renderResult();
  });
  $('#new-comparison').addEventListener('click', async () => {
    if (state.busy) return;
    localStorage.removeItem('baqbaq-comparison');
    state.comparisonId = null; state.result = null; state.selectedId = null;
    await ensureComparison(); await refreshComparison(); await refreshRuns(); renderResult();
    $('#evidence-content').classList.add('hidden'); $('#evidence-placeholder').classList.remove('hidden');
  });
  $('#run-history').addEventListener('change', async (event) => {
    if (!event.target.value || state.busy) return;
    state.result = await api(`/api/comparisons/${state.comparisonId}/result?run_id=${encodeURIComponent(event.target.value)}`);
    renderResult();
  });
  $("#before-files").addEventListener("change", (event) => upload("before", event.target.files));
  $("#after-files").addEventListener("change", (event) => upload("after", event.target.files));
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
    state.health = health;
    $('#model-mode').value='live';
    const modeLong = health.live_ready ? 'Live · подключён' : 'API не настроен';
    const modeShort = 'Live';
    $("#mode-badge").innerHTML = `<span class="status-dot"></span><span class="mode-long">${modeLong}</span><span class="mode-short">${modeShort}</span>`;
    await ensureComparison();
    await refreshComparison();
    await refreshRuns();
    const current = await api(`/api/comparisons/${state.comparisonId}/result`);
    if (['completed','partial'].includes(current?.run?.status)) { state.result = current; renderResult(); }
  } catch (error) { showError("#upload-error", `Не удалось запустить рабочее место: ${error.message}`); }
}

async function refreshRuns() {
  const runs = await api(`/api/comparisons/${state.comparisonId}/runs`);
  const labels={completed:'Завершён',partial:'Неполный результат',failed:'Ошибка',running:'Выполняется'};
  $('#run-history').innerHTML = runs.length ? runs.map(run => `<option value="${escapeHtml(run.id)}">${escapeHtml(new Date(run.started_at).toLocaleString('ru-RU'))} · ${escapeHtml(labels[run.status]||run.status)} · ${escapeHtml(run.model_mode==='live'?'Live':'Локальный расчёт')}</option>`).join('') : '<option value="">Анализ ещё не запускался</option>';
}

init();
