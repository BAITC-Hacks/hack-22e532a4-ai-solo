# Архитектура BaqBaq

## Границы MVP

Один локальный процесс FastAPI, SQLite и автономный frontend. Нет микросервисов, внешнего vector DB и фоновой инфраструктуры. Это сознательный выбор для воспроизводимого хакатонного MVP.

## Два слоя памяти

```text
DocumentVersion ──1:N── SourceSpan  ← immutable exact evidence
      │                    │
      │                    └── original_text + normalized_text + locator
      │
      └── FunctionAssertion ← semantic interpretation with source_id
                    │
                    ├── FunctionMatch (1:1 / split candidate / transfer)
                    └── Finding ── ReviewDecision
                                └── TraceEvent
```

**Exact layer** — SQLite является источником истины. Хранятся SHA-256, сторона комплекта, версия, оригинальный текст, нормализованный текст, устойчивый ID и локатор. Одинаковый пункт двух редакций создаёт два разных `SourceSpan`.

**Semantic layer** — функция представляется действием, объектом, владельцем, областью и полномочием. Детерминированный индекс использует русскую нормализацию, concept roots, token containment и character trigrams. Он генерирует кандидатов, но exact resolver всегда проверяет источники перед выдачей.

## Путь анализа

1. Ingestion проверяет размер, расширение и magic bytes; файл получает безопасное имя и хеш.
2. Parser создаёт ordered `SourceSpan`: DOCX paragraph/table, PDF page/block, XLSX sheet/range.
3. Registry extractor находит подразделения, роли и атомарные функции.
4. Candidate matcher строит гибридное сходство и связи сохранения/изменения/переноса/разделения.
5. Coverage check не объявляет потерю при наличии смыслового кандидата.
6. Overlap check сравнивает разных владельцев и отсекает различающиеся ИТ/операционные области.
7. Independence check различает исполнение, утверждение и контроль.
8. Evidence resolver проверяет, что каждый `source_id` принадлежит текущему `comparison_id`.
9. Findings, trace и отчёт закрепляются за immutable `AnalysisRun`.

## Агент

Agent toolset доступен через единый API: search spans, read exact source, compare candidates, check coverage и read analysis result. Offline-agent выдаёт extractive ответ и явно помечает режим. Live adapter использует Responses API с ограниченным числом источников; свободно написанные моделью цитаты не считаются доказательством.

## Надёжность

- Транзакции SQLite, WAL и foreign keys.
- Deterministic IDs для документов, spans и functions.
- Idempotency key для создания сравнения с конфликтом при изменённом payload.
- Изоляция поиска и resolver по `comparison_id`.
- Старые runs не переписываются при повторном анализе.
- Ошибка parser/model не превращается в «рисков нет».

## Безопасность

Документы считаются недоверенными данными. Макросы и команды не исполняются; путь нормализуется; ключ модели остаётся на backend; trace содержит только безопасные параметры и не хранит chain-of-thought.
