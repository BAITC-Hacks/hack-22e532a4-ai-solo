from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_evaluation_state = tempfile.TemporaryDirectory(prefix='baqbaq-probe-')
os.environ['BAQBAQ_DB_PATH'] = str(Path(_evaluation_state.name) / 'probe.db')
os.environ['BAQBAQ_UPLOAD_DIR'] = str(Path(_evaluation_state.name) / 'uploads')
os.environ['BAQBAQ_MODEL_MODE'] = 'offline'
os.environ['BAQBAQ_AUTH_PASSWORD_HASH'] = ''
os.environ['BAQBAQ_AUTH_SECRET'] = ''

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "benchmarks" / "eval.json"
OUT_JSON = ROOT / "results" / "evaluation.json"
OUT_MD = ROOT / "results" / "evaluation.md"


def git_info() -> tuple[str, bool | None]:
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
        return revision, dirty
    except Exception:
        return "unavailable", None


def check(actual: dict, expected: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for key, value in expected.items():
        if key == "score_min" and actual["score"] < value:
            errors.append(f"score {actual['score']:.3f} < {value:.3f}")
        elif key == "score_max" and actual["score"] > value:
            errors.append(f"score {actual['score']:.3f} > {value:.3f}")
        elif key not in {"score_min", "score_max"} and actual.get(key) != value:
            errors.append(f"{key}: {actual.get(key)!r} != {value!r}")
    return not errors, errors


def main() -> int:
    raw = DATASET.read_bytes()
    cases = json.loads(raw)
    client = TestClient(app)
    health = client.get("/api/health").json()
    results = []
    for case in cases:
        payload = {
            "before_text": case["before_text"],
            "after_text": case["after_text"],
            "before_scope": case.get("before_scope", "общая область"),
            "after_scope": case.get("after_scope", "общая область"),
        }
        response = client.post("/api/evaluate/probe", json=payload)
        actual = response.json() if response.status_code == 200 else {"http_status": response.status_code}
        passed, errors = check(actual, case["expect"])
        results.append({
            "id": case["id"], "category": case["category"], "expected": case["expect"],
            "actual": actual, "status": "PASS" if passed else "FAIL", "errors": errors,
        })

    revision, dirty = git_info()
    passed = sum(item["status"] == "PASS" for item in results)
    category_counts = defaultdict(lambda: Counter(total=0, passed=0, failed=0))
    for item in results:
        bucket = category_counts[item["category"]]
        bucket["total"] += 1
        bucket["passed" if item["status"] == "PASS" else "failed"] += 1

    labelled = [item for item in results if "related" in item["expected"]]
    tp = sum(item["expected"].get("related") is True and item["actual"].get("related") is True for item in labelled)
    fp = sum(item["expected"].get("related") is False and item["actual"].get("related") is True for item in labelled)
    fn = sum(item["expected"].get("related") is True and item["actual"].get("related") is False for item in labelled)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    report = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "git_dirty": dirty,
        "mode": health["model_mode"],
        "model": health["model"],
        "live_verification": "NOT TESTED" if not health["live_ready"] else "AVAILABLE_NOT_RUN",
        "summary": {"passed": passed, "total": len(results), "failed": len(results) - passed},
        "categories": {key: dict(value) for key, value in category_counts.items()},
        "related_class": {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall},
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Результаты встроенной проверки BaqBaq", "",
        f"- Итог: **{passed}/{len(results)} PASS**", f"- Режим: `{health['model_mode']}`",
        f"- Live verification: **{report['live_verification']}**", f"- Dataset SHA-256: `{report['dataset_sha256']}`", "",
        "## По категориям", "", "| Категория | PASS | Всего |", "|---|---:|---:|",
    ]
    for category, value in report["categories"].items():
        lines.append(f"| {category} | {value['passed']} | {value['total']} |")
    lines += ["", "## Размеченный класс related", "", f"Precision: {precision:.3f}" if precision is not None else "Precision: не измерено", f"Recall: {recall:.3f}" if recall is not None else "Recall: не измерено", "", "## Случаи", "", "| ID | Категория | Статус | Ошибка |", "|---|---|---|---|"]
    for item in results:
        lines.append(f"| {item['id']} | {item['category']} | {item['status']} | {'; '.join(item['errors']) or '—'} |")
    lines += ["", "> Это включённая инженерная проверка, а не независимый научный benchmark. Live LLM отдельно не запускался без credentials."]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"evaluation: {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
