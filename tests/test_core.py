from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from app.db import Store
from app.ingestion import IngestionError, normalize, parse_file, safe_filename
from app.model_adapter import ModelAdapter
from app.semantic import authority, scope_conflicts, similarity


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(
        f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def test_normalize_keeps_cyrillic_and_removes_punctuation():
    assert normalize("Контроль, Ёлка!") == "контроль елка"


def test_safe_filename_strips_directories():
    assert safe_filename("../../секрет.docx") == "секрет.docx"


def test_rejects_unsupported_extension(tmp_path: Path):
    path = tmp_path / "bad.exe"
    path.write_bytes(b"MZ")
    with pytest.raises(IngestionError):
        parse_file(path)


def test_docx_extracts_clause_and_exact_quote(tmp_path: Path):
    path = make_docx(tmp_path / "редакция_8.docx", ["3.4. БВА состоит из подразделений", "5.1. Контролирует качество аудита."])
    parsed = parse_file(path)
    assert parsed.revision == "редакция 8"
    assert parsed.spans[0].clause_label == "3.4"
    assert parsed.spans[1].text == "5.1. Контролирует качество аудита."


def test_xlsx_uses_sheet_and_cell_locator(tmp_path: Path):
    path = tmp_path / "roles.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Функции"
    sheet.append(["Владелец", "Функция"])
    sheet.append(["ДНМ", "Контролирует риски"])
    book.save(path)
    parsed = parse_file(path)
    assert parsed.status == "ready"
    assert parsed.spans[1].locator == "лист Функции, A2:B2"


def test_blank_pdf_requires_attention(tmp_path: Path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with path.open("wb") as fh:
        writer.write(fh)
    parsed = parse_file(path)
    assert parsed.status == "needs_attention"
    assert any("OCR" in warning for warning in parsed.warnings)


def test_semantic_match_survives_rephrasing():
    score = similarity(
        "взаимодействует с субъектами СВК и оценивает надежность результатов",
        "осуществляют взаимодействие с субъектами СВК, включая оценку качества результатов",
    )
    assert score >= 0.38


def test_unrelated_functions_do_not_match():
    assert similarity("проверяет качество аудита", "ведет кадровый учет отпусков") < 0.38


def test_scope_guard_separates_it_and_operations():
    assert scope_conflicts("ИТ, данные", "операционные, поддерживающие") is True


def test_authority_distinguishes_execution_and_control():
    assert authority("проводит проверку объекта") == "исполняет"
    assert authority("контролирует результаты проверки") == "контролирует"


def test_store_persists_comparison_after_reopen(tmp_path: Path):
    path = tmp_path / "state.db"
    first = Store(path)
    comparison, _ = first.create_comparison("Перезапуск", "offline", "restart-key")
    second = Store(path)
    assert second.get_comparison(comparison["id"])["name"] == "Перезапуск"


def test_idempotency_replays_same_request_and_rejects_changed(tmp_path: Path):
    store = Store(tmp_path / "idempotency.db")
    first, created = store.create_comparison("Один", "offline", "same-key")
    second, replay_created = store.create_comparison("Один", "offline", "same-key")
    assert created is True and replay_created is False and first["id"] == second["id"]
    with pytest.raises(ValueError):
        store.create_comparison("Другой", "offline", "same-key")


def test_exact_store_isolates_comparisons(tmp_path: Path):
    store = Store(tmp_path / "isolation.db")
    left, _ = store.create_comparison("Первое", "offline")
    right, _ = store.create_comparison("Второе", "offline")
    assert store.get_spans(left["id"]) == []
    assert store.get_spans(right["id"]) == []
    assert left["id"] != right["id"]


def test_offline_agent_is_explicit_and_cites_only_given_sources():
    adapter = ModelAdapter("offline", "unused")
    evidence = [{"id": "span_1", "filename": "a.docx", "revision": "ред. 1", "locator": "пункт 1", "clause_label": "1", "original_text": "Контролирует качество."}]
    reply = adapter.answer("Кто контролирует?", evidence)
    assert reply.mode == "offline"
    assert reply.source_ids == ["span_1"]
    assert "ограничен" in reply.text.lower()
