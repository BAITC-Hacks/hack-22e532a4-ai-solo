from __future__ import annotations

import shutil
import tempfile
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import Analyzer
from .config import ROOT, settings
from .db import Store, AnalysisBusy
from .domain import ParsedDocument
from .ingestion import IngestionError, MAX_BYTES, parse_file, safe_filename, store_upload, sha256_file
from .model_adapter import ModelAdapter, ModelError
from .gateway import Gateway
from .agent import answer_with_tools
from .reporting import html_report, markdown_report
from .semantic import authority, scope_conflicts, similarity
from . import auth


settings.ensure_dirs()
store = Store(settings.db_path)
store.recover_interrupted_runs()
model = ModelAdapter(settings.model_mode, settings.model, settings.base_url)
analyzer = Analyzer(store, model)

app = FastAPI(
    title="BaqBaq API",
    version="0.3.0",
    description="Проверяемая сверка организационной структуры и функций",
)
app.middleware('http')(auth.gate)
app.state.store = store


class LoginRequest(BaseModel):
    password: str = Field(min_length=1,max_length=256)


@app.get('/api/session')
def session(request: Request):
    return {'authenticated':auth.authorized(request), 'protected':auth.configured()}


@app.post('/api/session')
def login(payload: LoginRequest,request:Request):
    from fastapi.responses import JSONResponse
    if not auth.configured(): return {'authenticated':True,'protected':False}
    if not auth.login_allowed(request):
        raise HTTPException(status_code=429,detail='Слишком много попыток. Подождите минуту.')
    if not auth.check_password(payload.password):
        raise HTTPException(status_code=401,detail='Неверный код доступа жюри')
    response=JSONResponse({'authenticated':True,'protected':True})
    response.set_cookie(auth.COOKIE,auth.token(),max_age=8*3600,httponly=True,secure=True,samesite='lax',path='/')
    response.headers['Cache-Control']='no-store'
    return response


@app.delete('/api/session')
def logout():
    from fastapi.responses import JSONResponse
    response=JSONResponse({'authenticated':False})
    response.delete_cookie(auth.COOKIE,path='/',secure=True,httponly=True,samesite='lax')
    response.headers['Cache-Control']='no-store'
    return response


class ComparisonCreate(BaseModel):
    name: str = Field(default="Сверка организационных изменений", min_length=3, max_length=120)
    model_mode: Literal["offline", "live"] | None = None


class ReviewCreate(BaseModel):
    decision: Literal["confirmed", "rejected", "review"]
    note: str = Field(default="", max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    finding_id: str | None = None
    run_id: str | None = None


class EvaluationProbe(BaseModel):
    before_text: str = Field(min_length=1, max_length=4000)
    after_text: str = Field(min_length=1, max_length=4000)
    before_scope: str = "общая область"
    after_scope: str = "общая область"


@app.get("/api/health")
def health() -> dict:
    from .provenance import runtime_manifest
    return {
        "status": "ok",
        "model_mode": 'live' if auth.public_live() else model.mode,
        "model": model.model,
        "live_ready": model.live_ready,
        "release": os.getenv('BAQBAQ_RELEASE', 'working-tree'),
        'source_sha256': runtime_manifest()['source_sha256'],
        'protected': auth.configured(),
        'public_live': auth.public_live(),
        "supported_formats": ["docx", "pdf (text)", "xlsx"],
        'search_mode': os.getenv('BAQBAQ_SEARCH_MODE', 'fastembed'),
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
def comparisons(request: Request) -> list[dict]:
    return store.list_comparisons(getattr(request.state,'owner_key',None))


@app.post("/api/comparisons", status_code=201)
def create_comparison(
    payload: ComparisonCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    mode = 'live' if auth.public_live() else payload.model_mode or model.mode
    owner=getattr(request.state,'owner_key',None)
    if owner and idempotency_key:
        import hashlib
        idempotency_key=hashlib.sha256((owner+':'+idempotency_key).encode()).hexdigest()
    try:
        item, created = store.create_comparison(payload.name, mode, idempotency_key)
        if owner:
            if created: store.bind_comparison(item['id'],owner)
            elif not store.owns_comparison(item['id'],owner):
                raise HTTPException(status_code=409,detail='Создайте новое сравнение')
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
    if len(files)>20:
        raise HTTPException(status_code=413, detail='За один запрос можно добавить до 20 файлов')
    if (store.latest_run(comparison_id) or {}).get('status') == 'running':
        raise HTTPException(status_code=409, detail='Дождитесь окончания анализа перед изменением комплекта')
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
                    break
                temp.write(chunk)
        if size > MAX_BYTES:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail=f"{filename}: файл превышает 25 МБ")
        try:
            stored = store_upload(temp_path, settings.upload_dir, comparison_id, side)
            renamed = stored.with_name(f"{stored.name.split('_', 1)[0]}_{filename}")
            if stored != renamed and not renamed.exists():
                stored.rename(renamed)
                stored = renamed
            parsed = parse_file(stored, filename)
            output.append(store.add_document(comparison_id, side, parsed, str(stored)))
        except IngestionError as exc:
            failed = ParsedDocument(filename, suffix.lstrip('.'), sha256_file(temp_path),
                                    'не прочитана', 'needs_attention', [], [str(exc)])
            store.add_document(comparison_id, side, failed, '')
            raise HTTPException(status_code=422, detail=f"{filename}: {exc}") from exc
        finally:
            temp_path.unlink(missing_ok=True)
    store.set_comparison_status(comparison_id, "documents_ready")
    return {"documents": output}


@app.post("/api/comparisons/{comparison_id}/demo")
def load_demo(comparison_id: str, dataset: Literal['synthetic','organizer'] = 'synthetic') -> dict:
    _require_comparison(comparison_id)
    paths = [("before", settings.demo_before), ("after", settings.demo_after)]
    if (store.latest_run(comparison_id) or {}).get('status') == 'running':
        raise HTTPException(status_code=409, detail='Дождитесь окончания анализа')
    if dataset == 'organizer' and any(not path.is_file() for _, path in paths):
        raise HTTPException(status_code=422, detail='Оригиналы не подключены. Загрузите их самостоятельно или настройте BAQBAQ_DEMO_BEFORE/AFTER.')
    if dataset == 'synthetic':
        from .fixtures import document_bytes, DEMO_BEFORE, DEMO_AFTER
        output = []
        for side, lines in [('before', DEMO_BEFORE), ('after', DEMO_AFTER)]:
            name = f'SYNTHETIC_{side}.docx'
            with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as temp:
                temp.write(document_bytes(lines))
                temp_path = Path(temp.name)
            try:
                stored = store_upload(temp_path, settings.upload_dir, comparison_id, side)
                output.append(store.add_document(comparison_id, side, parse_file(stored, name), str(stored)))
            finally:
                temp_path.unlink(missing_ok=True)
        store.set_comparison_status(comparison_id, 'documents_ready')
        return {'documents':output,'dataset':'synthetic','note':'Общий открытый контрольный набор: реорганизация, перенос, потеря, дублирование. Не документы организатора.'}
    output = []
    for side, source in paths:
        try:
            stored = store_upload(source, settings.upload_dir, comparison_id, side)
            parsed = parse_file(stored, source.name)
            output.append(store.add_document(comparison_id, side, parsed, str(stored)))
        except IngestionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.set_comparison_status(comparison_id, "documents_ready")
    return {"documents": output, 'dataset':'organizer'}


@app.post("/api/comparisons/{comparison_id}/analyze")
def analyze(comparison_id: str) -> dict:
    item = _require_comparison(comparison_id)
    active_model = ModelAdapter(item["model_mode"], settings.model, settings.base_url)
    try:
        return Analyzer(store, active_model).run(comparison_id)
    except AnalysisBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ModelError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail='Анализ не завершён. Данные сохранены; повторите запуск или сообщите администратору.') from exc


@app.get("/api/comparisons/{comparison_id}/result")
def result(comparison_id: str, run_id: str | None = None) -> dict:
    _require_comparison(comparison_id)
    try:
        return store.analysis_result(comparison_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get('/api/comparisons/{comparison_id}/runs')
def runs(comparison_id: str) -> list[dict]:
    _require_comparison(comparison_id)
    return store.list_runs(comparison_id)


@app.get('/api/comparisons/{comparison_id}/progress')
def progress(comparison_id: str) -> dict:
    _require_comparison(comparison_id)
    run = store.latest_run(comparison_id)
    if not run:
        return {'status': 'idle', 'last_completed_tool': None}
    with store.connect() as db:
        event = db.execute("SELECT tool,status FROM trace_events WHERE run_id=? AND status='ok' ORDER BY sequence DESC LIMIT 1", (run['id'],)).fetchone()
    return {'status': run['status'], 'run_id': run['id'], 'last_completed_tool': event['tool'] if event else None}


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
    try:
        result_data = store.analysis_result(comparison_id, payload.run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    evidence: list[dict] = []
    selected_finding = None
    if payload.finding_id:
        finding = next((item for item in result_data["findings"] if item["id"] == payload.finding_id), None)
        if not finding:
            raise HTTPException(status_code=404, detail="Выбранный вывод не найден")
        selected_finding = {key:finding.get(key) for key in ('finding_type','title','summary','before_owner','after_owner','before_function','after_function','evidence_status','limitations')}
        evidence = [item for item in (finding.get("before_source"), finding.get("after_source")) if item]
        evidence += finding.get('before_context',[]) + finding.get('after_context',[])
    adapter = ModelAdapter(comparison_item["model_mode"], settings.model, settings.base_url)
    gateway = Gateway(store, comparison_id, (result_data.get('run') or {}).get('id'))
    try:
        reply = answer_with_tools(adapter, gateway, payload.question, evidence, selected_finding=selected_finding)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        gateway.persist_trace()
    message = store.save_message(
        comparison_id,
        (result_data.get("run") or {}).get("id"),
        payload.finding_id,
        payload.question,
        reply.text,
        reply.source_ids,
        reply.mode,
    )
    return {**message, 'request_id': gateway.request_id, 'trace': gateway.trace, 'api_usage': adapter.usage}


@app.get("/api/comparisons/{comparison_id}/report")
def report(comparison_id: str, format: Literal["markdown", "html"] = "markdown", run_id: str | None = None) -> Response:
    item = _require_comparison(comparison_id)
    try:
        result_data = store.analysis_result(comparison_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not result_data.get("run") or result_data["run"]["status"] not in {"completed", "partial"}:
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


@app.get('/', include_in_schema=False)
@app.get('/index.html', include_in_schema=False)
@app.get('/workspace', include_in_schema=False)
def workspace():
    return FileResponse(static_dir/'workspace.html')


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
