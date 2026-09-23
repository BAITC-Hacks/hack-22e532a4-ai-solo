from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(namespace: str, *parts: str) -> str:
    value = "|".join(parts)
    return f"{namespace}_{uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:20]}"


class AnalysisBusy(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS comparisons (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    model_mode TEXT NOT NULL DEFAULT 'offline',
                    idempotency_key TEXT UNIQUE,
                    request_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS comparison_access (
                    comparison_id TEXT PRIMARY KEY REFERENCES comparisons(id) ON DELETE CASCADE,
                    owner_key TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_comparison_owner ON comparison_access(owner_key);
                CREATE TABLE IF NOT EXISTS run_snapshots (
                    run_id TEXT PRIMARY KEY REFERENCES analysis_runs(id),
                    documents_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS model_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    side TEXT NOT NULL CHECK(side IN ('before','after')),
                    filename TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE(comparison_id, side, sha256)
                );
                CREATE TABLE IF NOT EXISTS source_spans (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    side TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    clause_label TEXT,
                    kind TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(document_id, ordinal, locator)
                );
                CREATE INDEX IF NOT EXISTS idx_spans_comparison ON source_spans(comparison_id, side);
                CREATE VIRTUAL TABLE IF NOT EXISTS source_spans_fts USING fts5(
                    span_id UNINDEXED, comparison_id UNINDEXED, normalized_text,
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS org_units (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    side TEXT NOT NULL,
                    name TEXT NOT NULL,
                    abbreviation TEXT,
                    span_id TEXT REFERENCES source_spans(id),
                    UNIQUE(comparison_id, side, name)
                );
                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    side TEXT NOT NULL,
                    name TEXT NOT NULL,
                    span_id TEXT REFERENCES source_spans(id),
                    UNIQUE(comparison_id, side, name)
                );
                CREATE TABLE IF NOT EXISTS function_assertions (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    side TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    action TEXT NOT NULL,
                    object_text TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    assertion_text TEXT NOT NULL,
                    span_id TEXT NOT NULL REFERENCES source_spans(id),
                    document_id TEXT NOT NULL REFERENCES document_versions(id),
                    clause_label TEXT,
                    verified_status TEXT NOT NULL DEFAULT 'extracted'
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    model_mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS function_matches (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    before_id TEXT REFERENCES function_assertions(id),
                    after_id TEXT REFERENCES function_assertions(id),
                    score REAL NOT NULL,
                    relation TEXT NOT NULL,
                    evidence_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    finding_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    before_owner TEXT,
                    after_owner TEXT,
                    before_function TEXT,
                    after_function TEXT,
                    before_span_id TEXT REFERENCES source_spans(id),
                    after_span_id TEXT REFERENCES source_spans(id),
                    evidence_status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    limitations TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id, sort_order);
                CREATE TABLE IF NOT EXISTS trace_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    safe_params_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    result TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
                    run_id TEXT REFERENCES analysis_runs(id),
                    finding_id TEXT REFERENCES findings(id),
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    model_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_comparison(
        self, name: str, model_mode: str, idempotency_key: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        request_hash = hashlib.sha256(
            json.dumps({"name": name, "model_mode": model_mode}, sort_keys=True).encode()
        ).hexdigest()
        with self._lock, self.connect() as db:
            if idempotency_key:
                old = db.execute(
                    "SELECT * FROM comparisons WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if old:
                    if old["request_hash"] != request_hash:
                        raise ValueError("Idempotency-Key уже использован для другого запроса")
                    return dict(old), False
            now = utcnow()
            cid = f"cmp_{uuid.uuid4().hex[:16]}"
            db.execute(
                "INSERT INTO comparisons VALUES (?,?,?,?,?,?,?,?)",
                (cid, name, "draft", model_mode, idempotency_key, request_hash, now, now),
            )
            row = db.execute("SELECT * FROM comparisons WHERE id=?", (cid,)).fetchone()
            return dict(row), True

    def bind_comparison(self, comparison_id: str, owner_key: str) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO comparison_access VALUES (?,?)',(comparison_id,owner_key))

    def owns_comparison(self, comparison_id: str, owner_key: str) -> bool:
        with self.connect() as db:
            return db.execute('SELECT 1 FROM comparison_access WHERE comparison_id=? AND owner_key=?',(comparison_id,owner_key)).fetchone() is not None

    def list_comparisons(self, owner_key: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT c.*,
                (SELECT count(*) FROM document_versions d WHERE d.comparison_id=c.id AND d.side='before') before_count,
                (SELECT count(*) FROM document_versions d WHERE d.comparison_id=c.id AND d.side='after') after_count
                FROM comparisons c WHERE (? IS NULL OR EXISTS
                (SELECT 1 FROM comparison_access a WHERE a.comparison_id=c.id AND a.owner_key=?))
                ORDER BY c.created_at DESC""", (owner_key,owner_key)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM comparisons WHERE id=?", (comparison_id,)).fetchone()
            return dict(row) if row else None

    def set_comparison_status(self, comparison_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE comparisons SET status=?, updated_at=? WHERE id=?",
                (status, utcnow(), comparison_id),
            )

    def add_document(
        self,
        comparison_id: str,
        side: str,
        parsed: Any,
        stored_path: str,
    ) -> dict[str, Any]:
        did = stable_id("doc", comparison_id, side, parsed.sha256)
        with self._lock, self.connect() as db:
            existing = db.execute("SELECT * FROM document_versions WHERE id=?", (did,)).fetchone()
            if existing:
                return self._document_row(db, existing)
            db.execute(
                """INSERT INTO document_versions
                (id,comparison_id,side,filename,revision,file_type,sha256,stored_path,status,warnings_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    did,
                    comparison_id,
                    side,
                    parsed.filename,
                    parsed.revision,
                    parsed.file_type,
                    parsed.sha256,
                    stored_path,
                    parsed.status,
                    json.dumps(parsed.warnings, ensure_ascii=False),
                    utcnow(),
                ),
            )
            for span in parsed.spans:
                sid = stable_id("span", did, str(span.ordinal), span.locator, span.text)
                db.execute(
                    """INSERT INTO source_spans
                    (id,document_id,comparison_id,side,ordinal,locator,clause_label,kind,original_text,normalized_text,warnings_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sid,
                        did,
                        comparison_id,
                        side,
                        span.ordinal,
                        span.locator,
                        span.clause_label,
                        span.kind,
                        span.text,
                        span.normalized_text,
                        json.dumps(span.warnings, ensure_ascii=False),
                    ),
                )
                db.execute(
                    "INSERT INTO source_spans_fts(span_id,comparison_id,normalized_text) VALUES (?,?,?)",
                    (sid, comparison_id, span.normalized_text),
                )
            row = db.execute("SELECT * FROM document_versions WHERE id=?", (did,)).fetchone()
            return self._document_row(db, row)

    def _document_row(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["warnings"] = json.loads(item.pop("warnings_json"))
        item["span_count"] = db.execute(
            "SELECT count(*) FROM source_spans WHERE document_id=?", (item["id"],)
        ).fetchone()[0]
        return item

    def list_documents(self, comparison_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM document_versions WHERE comparison_id=? ORDER BY side, created_at",
                (comparison_id,),
            ).fetchall()
            return [self._document_row(db, row) for row in rows]

    def get_spans(self, comparison_id: str, side: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT s.*, d.filename, d.revision, d.file_type
                   FROM source_spans s JOIN document_versions d ON d.id=s.document_id
                   WHERE s.comparison_id=?"""
        params: list[Any] = [comparison_id]
        if side:
            query += " AND s.side=?"
            params.append(side)
        query += " ORDER BY d.created_at, s.ordinal"
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def get_span(self, span_id: str, comparison_id: str | None = None) -> dict[str, Any] | None:
        query = """SELECT s.*, d.filename, d.revision, d.file_type
                   FROM source_spans s JOIN document_versions d ON d.id=s.document_id
                   WHERE s.id=?"""
        params: list[Any] = [span_id]
        if comparison_id:
            query += " AND s.comparison_id=?"
            params.append(comparison_id)
        with self.connect() as db:
            row = db.execute(query, params).fetchone()
            return dict(row) if row else None

    def search_spans(self, comparison_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        tokens = [t for t in query.replace('"', " ").split() if len(t) > 2]
        if not tokens:
            return []
        fts = " OR ".join(f'"{token}"' for token in tokens[:8])
        with self.connect() as db:
            rows = db.execute(
                """SELECT s.*, d.filename, d.revision, bm25(source_spans_fts) rank
                FROM source_spans_fts
                JOIN source_spans s ON s.id=source_spans_fts.span_id
                JOIN document_versions d ON d.id=s.document_id
                WHERE source_spans_fts MATCH ? AND source_spans_fts.comparison_id=?
                ORDER BY rank LIMIT ?""",
                (fts, comparison_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def begin_run(self, comparison_id: str, model_mode: str, model: str, input_hash: str) -> str:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM analysis_runs WHERE comparison_id=? AND status='running'", (comparison_id,)).fetchone():
                raise AnalysisBusy('Анализ этого сравнения уже выполняется. Дождитесь результата.')
            db.execute(
                """INSERT INTO analysis_runs
                (id,comparison_id,status,model_mode,model,prompt_version,input_hash,started_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, comparison_id, "running", model_mode, model, "baqbaq-2026-09-23", input_hash, utcnow()),
            )
            # Canonical assertions are stable records referenced by immutable prior runs.
            # Re-extraction uses deterministic IDs and upserts instead of deleting history.
        self.set_comparison_status(comparison_id, "analyzing")
        return run_id

    def recover_interrupted_runs(self):
        """Single-worker service startup: preserve interrupted runs, never pretend completion."""
        with self.connect() as db:
            rows = db.execute("SELECT DISTINCT comparison_id FROM analysis_runs WHERE status='running'").fetchall()
            db.execute("UPDATE analysis_runs SET status='failed',completed_at=?,error=? WHERE status='running'",
                       (utcnow(), 'Процесс был перезапущен до завершения анализа. Создайте новый запуск.'))
            for row in rows:
                db.execute("UPDATE comparisons SET status='error' WHERE id=?", (row[0],))

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.connect() as db:
            row = db.execute("SELECT comparison_id FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            db.execute(
                "UPDATE analysis_runs SET status=?,completed_at=?,error=? WHERE id=?",
                (status, utcnow(), error, run_id),
            )
        if row:
            self.set_comparison_status(row[0], "ready" if status == "completed" else "partial" if status == "partial" else "error")

    def add_trace(
        self,
        run_id: str,
        sequence: int,
        tool: str,
        safe_params: dict[str, Any],
        source_ids: list[str],
        result: str,
        status: str,
        duration_ms: int,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO trace_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"trace_{uuid.uuid4().hex[:16]}",
                    run_id,
                    sequence,
                    tool,
                    json.dumps(safe_params, ensure_ascii=False),
                    json.dumps(source_ids, ensure_ascii=False),
                    result,
                    status,
                    duration_ms,
                    utcnow(),
                ),
            )

    def add_org_unit(self, comparison_id: str, side: str, name: str, abbreviation: str | None, span_id: str) -> str:
        oid = stable_id("unit", comparison_id, side, name)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO org_units VALUES (?,?,?,?,?,?)",
                (oid, comparison_id, side, name, abbreviation, span_id),
            )
        return oid

    def add_role(self, comparison_id: str, side: str, name: str, span_id: str) -> str:
        rid = stable_id("role", comparison_id, side, name)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO roles VALUES (?,?,?,?,?)",
                (rid, comparison_id, side, name, span_id),
            )
        return rid

    def add_function(self, comparison_id: str, fn: Any) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO function_assertions
                (id,comparison_id,side,owner,action,object_text,scope,authority,assertion_text,span_id,document_id,clause_label,verified_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fn.id,
                    comparison_id,
                    fn.side,
                    fn.owner,
                    fn.action,
                    fn.object,
                    fn.scope,
                    fn.authority,
                    fn.text,
                    fn.span_id,
                    fn.document_id,
                    fn.clause_label,
                    "extracted",
                ),
            )

    def add_match(self, run_id: str, match: Any, evidence_status: str) -> None:
        mid = stable_id("match", run_id, match.before.id, match.after.id, match.relation)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO function_matches VALUES (?,?,?,?,?,?,?)",
                (mid, run_id, match.before.id, match.after.id, match.score, match.relation, evidence_status),
            )

    def add_finding(self, run_id: str, comparison_id: str, finding: dict[str, Any], order: int) -> str:
        fid = stable_id(
            "finding",
            run_id,
            finding["type"],
            finding.get("before_span_id") or "",
            finding.get("after_span_id") or "",
            finding["title"],
            finding.get('before_function') or '',
            finding.get('after_function') or '',
        )
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO findings
                (id,run_id,comparison_id,finding_type,title,summary,before_owner,after_owner,before_function,
                after_function,before_span_id,after_span_id,evidence_status,confidence,limitations,sort_order)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid,
                    run_id,
                    comparison_id,
                    finding["type"],
                    finding["title"],
                    finding["summary"],
                    finding.get("before_owner"),
                    finding.get("after_owner"),
                    finding.get("before_function"),
                    finding.get("after_function"),
                    finding.get("before_span_id"),
                    finding.get("after_span_id"),
                    finding["evidence_status"],
                    finding["confidence"],
                    finding.get("limitations", ""),
                    order,
                ),
            )
        return fid

    def latest_run(self, comparison_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM analysis_runs WHERE comparison_id=? ORDER BY started_at DESC LIMIT 1",
                (comparison_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_runs(self, comparison_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM analysis_runs WHERE comparison_id=? ORDER BY started_at DESC', (comparison_id,))]

    def snapshot_run(self, run_id: str, documents: list, metadata: dict) -> None:
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO run_snapshots VALUES (?,?,?)',
                       (run_id, json.dumps(documents, ensure_ascii=False), json.dumps(metadata, ensure_ascii=False)))

    def cache_get(self, key: str):
        with self.connect() as db:
            row = db.execute('SELECT payload_json FROM model_cache WHERE cache_key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO model_cache VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def analysis_result(self, comparison_id: str, run_id: str | None = None) -> dict[str, Any]:
        if run_id:
            with self.connect() as db:
                row = db.execute('SELECT * FROM analysis_runs WHERE id=? AND comparison_id=?', (run_id, comparison_id)).fetchone()
                if not row:
                    raise KeyError('Запуск не найден в этом сравнении')
                run = dict(row)
        else:
            run = self.latest_run(comparison_id)
        if not run:
            return {"run": None, "findings": [], "trace": [], "summary": {}}
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM findings WHERE run_id=? ORDER BY sort_order", (run["id"],)
            ).fetchall()
            findings: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["before_source"] = self.get_span(item["before_span_id"], comparison_id) if item["before_span_id"] else None
                item["after_source"] = self.get_span(item["after_span_id"], comparison_id) if item["after_span_id"] else None
                decision = db.execute(
                    "SELECT * FROM review_decisions WHERE finding_id=? ORDER BY created_at DESC LIMIT 1",
                    (item["id"],),
                ).fetchone()
                item["review"] = dict(decision) if decision else None
                item['review_history'] = [dict(r) for r in db.execute('SELECT * FROM review_decisions WHERE finding_id=? ORDER BY created_at', (item['id'],))]
                findings.append(item)
            trace_rows = db.execute(
                "SELECT * FROM trace_events WHERE run_id=? ORDER BY sequence", (run["id"],)
            ).fetchall()
            trace = []
            for row in trace_rows:
                item = dict(row)
                item["safe_params"] = json.loads(item.pop("safe_params_json"))
                item["source_ids"] = json.loads(item.pop("source_ids_json"))
                trace.append(item)
        counts: dict[str, int] = {}
        for item in findings:
            counts[item["finding_type"]] = counts.get(item["finding_type"], 0) + 1
        with self.connect() as db:
            snapshot = db.execute('SELECT * FROM run_snapshots WHERE run_id=?', (run['id'],)).fetchone()
        metadata = json.loads(snapshot['metadata_json']) if snapshot else {}
        functions = metadata.get('functions', [])
        for finding in findings:
            for side in ('before', 'after'):
                context_ids = {f.get('context_span_id') for f in functions
                               if f['span_id'] == finding.get(side+'_span_id') and f.get('context_span_id')}
                finding[side+'_context'] = [self.get_span(sid, comparison_id) for sid in sorted(context_ids)]
        return {"run": run, "findings": findings, "trace": trace, "summary": counts,
                'documents': json.loads(snapshot['documents_json']) if snapshot else [],
                'metadata': metadata}

    def save_review(self, finding_id: str, decision: str, note: str) -> dict[str, Any]:
        rid = f"review_{uuid.uuid4().hex[:16]}"
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM findings WHERE id=?", (finding_id,)).fetchone()
            if not exists:
                raise KeyError(finding_id)
            db.execute(
                "INSERT INTO review_decisions VALUES (?,?,?,?,?)",
                (rid, finding_id, decision, note, utcnow()),
            )
            row = db.execute("SELECT * FROM review_decisions WHERE id=?", (rid,)).fetchone()
            return dict(row)

    def save_message(
        self,
        comparison_id: str,
        run_id: str | None,
        finding_id: str | None,
        question: str,
        answer: str,
        source_ids: list[str],
        model_mode: str,
    ) -> dict[str, Any]:
        mid = f"msg_{uuid.uuid4().hex[:16]}"
        with self.connect() as db:
            db.execute(
                "INSERT INTO agent_messages VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, comparison_id, run_id, finding_id, question, answer, json.dumps(source_ids), model_mode, utcnow()),
            )
            row = db.execute("SELECT * FROM agent_messages WHERE id=?", (mid,)).fetchone()
            item = dict(row)
            item["source_ids"] = json.loads(item.pop("source_ids_json"))
            return item
