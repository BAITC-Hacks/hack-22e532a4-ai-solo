from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import Analyzer
from .config import ROOT, settings
from .db import Store
from .ingestion import IngestionError, MAX_BYTES, parse_file, safe_filename, store_upload
from .model_adapter import ModelAdapter, ModelError
from .reporting import html_report, markdown_report
from .semantic import authority, scope_conflicts, similarity


settings.ensure_dirs()
store = Store(settings.db_path)
model = ModelAdapter(settings.model_mode, settings.model, settings.base_url)
analyzer = Analyzer(store, model)

app = FastAPI(
    title="BaqBaq API",
    version="0.1.0",
    description="Проверяемая сверка организационной структуры и функций",
)


class ComparisonCreate(BaseModel):
    name: str = Field(default="Сверка организационных изменений", min_length=3, max_length=120)
    model_mode: Literal["offline", "live"] | None = None


class ReviewCreate(BaseModel):
    decision: Literal["confirmed", "rejected", "review"]
    note: str = Field(default="", max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    finding_id: str | None = None


class EvaluationProbe(BaseModel):
    before_text: str = Field(min_length=1, max_length=4000)
    after_text: str = Field(min_length=1, max_length=4000)
    before_scope: str = "общая область"
    after_scope: str = "общая область"


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_mode": model.mode,
        "model": model.model,
        "live_ready": model.live_ready,
        "db": str(settings.db_path),
        "supported_formats": ["docx", "pdf (text)", "xlsx"],
    }


@app.post("/api/evaluate/probe")
def evaluation_probe(payload: EvaluationProbe) -> dict:
    """Public, side-effect-free evaluator path used by the bundled verification suite."""
    score = similarity(payload.before_text, payload.after_text)
    return {
        "score": score,
        "related": score >= 0.38,
        "strong_overlap": score >= 0.78 and not scope_conflicts(payload.before_scope, payload.after_scope),
        "scope_conflict": scope_conflicts(payload.before_scope, payload.after_scope),
        "before_authority": authority(payload.before_text),
        "after_authority": authority(payload.after_text),
    }


@app.get("/api/comparisons")
def comparisons() -> list[dict]:
    return store.list_comparisons()


@app.post("/api/comparisons", status_code=201)
def create_comparison(
    payload: ComparisonCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    mode = payload.model_mode or model.mode
    try:
        item, created = store.create_comparison(payload.name, mode, idempotency_key)
        return {**item, "created": created}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/comparisons/{comparison_id}")
def comparison(comparison_id: str) -> dict:
    item = store.get_comparison(comparison_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сравнение не найдено")
    return {**item, "documents": store.list_documents(comparison_id)}


def _require_comparison(comparison_id: str) -> dict:
    item = store.get_comparison(comparison_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сравнение не найдено")
    return item


@app.post("/api/comparisons/{comparison_id}/documents/{side}")
async def upload_documents(
    comparison_id: str,
    side: Literal["before", "after"],
    files: list[UploadFile] = File(...),
) -> dict:
    _require_comparison(comparison_id)
    if not files:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один файл")
    output = []
    for upload in files:
        filename = safe_filename(upload.filename or "document")
        suffix = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = Path(temp.name)
            size = 0
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    temp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"{filename}: файл превышает 25 МБ")
                temp.write(chunk)
        try:
            stored = store_upload(temp_path, settings.upload_dir, comparison_id, side)
            renamed = stored.with_name(f"{stored.name.split('_', 1)[0]}_{filename}")
            if stored != renamed and not renamed.exists():
                stored.rename(renamed)
                stored = renamed
            parsed = parse_file(stored, filename)
            output.append(store.add_document(comparison_id, side, parsed, str(stored)))
        except IngestionError as exc:
            raise HTTPException(status_code=422, detail=f"{filename}: {exc}") from exc
        finally:
            temp_path.unlink(missing_ok=True)
    store.set_comparison_status(comparison_id, "documents_ready")
    return {"documents": output}


@app.post("/api/comparisons/{comparison_id}/demo")
def load_demo(comparison_id: str) -> dict:
    _require_comparison(comparison_id)
    paths = [("before", settings.demo_before), ("after", settings.demo_after)]
    missing = [str(path) for _, path in paths if not path.exists()]
    if missing:
        raise HTTPException(status_code=404, detail="Не найдены demo-файлы: " + "; ".join(missing))
    output = []
    for side, source in paths:
        try:
            stored = store_upload(source, settings.upload_dir, comparison_id, side)
            parsed = parse_file(stored, source.name)
            output.append(store.add_document(comparison_id, side, parsed, str(stored)))
        except IngestionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.set_comparison_status(comparison_id, "documents_ready")
    return {"documents": output}


@app.post("/api/comparisons/{comparison_id}/analyze")
def analyze(comparison_id: str) -> dict:
    item = _require_comparison(comparison_id)
    active_model = model
    if item["model_mode"] != model.mode:
        active_model = ModelAdapter(item["model_mode"], settings.model, settings.base_url)
    try:
        return Analyzer(store, active_model).run(comparison_id)
    except (ValueError, ModelError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Анализ завершился ошибкой: {exc}") from exc


@app.get("/api/comparisons/{comparison_id}/result")
def result(comparison_id: str) -> dict:
    _require_comparison(comparison_id)
    return store.analysis_result(comparison_id)


@app.get("/api/comparisons/{comparison_id}/sources/{span_id}")
def source(comparison_id: str, span_id: str) -> dict:
    _require_comparison(comparison_id)
    item = store.get_span(span_id, comparison_id)
    if not item:
        raise HTTPException(status_code=404, detail="Источник не найден в этом сравнении")
    return item


@app.post("/api/findings/{finding_id}/review")
def review(finding_id: str, payload: ReviewCreate) -> dict:
    try:
        return store.save_review(finding_id, payload.decision, payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Вывод не найден") from exc


@app.post("/api/comparisons/{comparison_id}/ask")
def ask(comparison_id: str, payload: AskRequest) -> dict:
    comparison_item = _require_comparison(comparison_id)
    result_data = store.analysis_result(comparison_id)
    evidence: list[dict] = []
    if payload.finding_id:
        finding = next((item for item in result_data["findings"] if item["id"] == payload.finding_id), None)
        if not finding:
            raise HTTPException(status_code=404, detail="Выбранный вывод не найден")
        evidence = [item for item in (finding.get("before_source"), finding.get("after_source")) if item]
    if not evidence:
        evidence = store.search_spans(comparison_id, payload.question, 8)
    adapter = ModelAdapter(comparison_item["model_mode"], settings.model, settings.base_url)
    try:
        reply = adapter.answer(payload.question, evidence)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    message = store.save_message(
        comparison_id,
        (result_data.get("run") or {}).get("id"),
        payload.finding_id,
        payload.question,
        reply.text,
        reply.source_ids,
        reply.mode,
    )
    return message


@app.get("/api/comparisons/{comparison_id}/report")
def report(comparison_id: str, format: Literal["markdown", "html"] = "markdown") -> Response:
    item = _require_comparison(comparison_id)
    result_data = store.analysis_result(comparison_id)
    if not result_data.get("run") or result_data["run"]["status"] != "completed":
        raise HTTPException(status_code=409, detail="Сначала завершите анализ")
    if format == "html":
        content = html_report(item, result_data)
        return HTMLResponse(
            content,
            headers={"Content-Disposition": f'attachment; filename="baqbaq-{comparison_id}.html"'},
        )
    content = markdown_report(item, result_data)
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="baqbaq-{comparison_id}.md"'},
    )


static_dir = ROOT / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
