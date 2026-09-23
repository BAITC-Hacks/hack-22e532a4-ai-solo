# Build readiness

Дата проверки: 2026-09-23 (Asia/Qyzylorda).

## Выполненные команды

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe scripts\verify.py
```

## Фактический результат

- Python compile: PASS.
- Pytest: **17/17 PASS**.
- Предварительно размеченная suite: **24/24 PASS**.
- Related-class precision: **1.000**.
- Related-class recall: **1.000**.
- Demo ingestion: редакция 8 — 491 spans; редакция 9 — 490 spans.
- Demo full analysis: PASS в offline-режиме; найдено сохранение ДНМ/ДККМ, добавление ДИТААД/ДОА и кандидат переноса 5.4.4 → 5.3.3.

## Поддержка

| Возможность | Статус |
|---|---|
| DOCX | IMPLEMENTED + TESTED |
| Text PDF | IMPLEMENTED; blank/scan guard TESTED |
| XLSX | IMPLEMENTED + TESTED |
| SQLite persistence/isolation | TESTED |
| Offline analysis + exact evidence | IMPLEMENTED + TESTED |
| Live OpenAI-compatible model | IMPLEMENTED; NOT TESTED (нет credentials) |
| Markdown/HTML report | IMPLEMENTED |
| DOCX/PDF report | NOT IMPLEMENTED |
| OCR, legacy DOC/XLS | NOT IMPLEMENTED |

## Известное предупреждение

Starlette TestClient использует deprecated alias AnyIO; это предупреждение зависимости и не влияет на выполнение 17 тестов.
