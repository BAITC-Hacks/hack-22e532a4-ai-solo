from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import Any, Callable

from .db import Store, stable_id
from .domain import CandidateMatch, FunctionAssertion
from .model_adapter import ModelAdapter, ModelError
from .semantic import authority, best_action, has_action, object_and_scope, scope_conflicts, similarity


LEADING_OWNER_RE = re.compile(
    r"^(?P<owner>Главный аудитор|Директор(?:а|ы)?\s+.{1,190}?|Руководител(?:ь|и)\s+.{1,160}?)"
    r"(?=\s*:\s*$|\s+(?:обязан|обязаны|организует|организуют|осуществляет|осуществляют|"
    r"обеспечивает|обеспечивают|готовит|готовят|запрашивает|запрашивают|участвует|участвуют|"
    r"анализирует|анализируют|взаимодействует|взаимодействуют|проводит|проводят)\b)",
    re.IGNORECASE,
)
UNIT_RE = re.compile(r"(Департамент[^.;|]{3,160}?)(?:\s*\(([А-ЯA-ZЁ]{2,12})\))?(?:[.;]|$)", re.IGNORECASE)
CLAUSE_PREFIX_RE = re.compile(r"^\s*(?:\d+\.)+\s*")


def _clean_owner(text: str) -> str:
    text = CLAUSE_PREFIX_RE.sub("", text).strip(" :;.")
    return text[:220] or "Не установлен"


def _section(label: str | None) -> str | None:
    if not label:
        return None
    bits = label.split(".")
    return ".".join(bits[:2]) if len(bits) >= 2 else bits[0]


def extract_units_and_functions(
    store: Store, comparison_id: str, side: str
) -> tuple[list[dict[str, Any]], list[FunctionAssertion], list[dict[str, Any]]]:
    spans = store.get_spans(comparison_id, side)
    units: list[dict[str, Any]] = []
    functions: list[FunctionAssertion] = []
    roles: list[dict[str, Any]] = []
    owner_by_section: dict[str, str] = {}
    current_owner = "Не установлен"
    current_main: str | None = None
    current_document: str | None = None
    active_structure = False

    for span in spans:
        if span["document_id"] != current_document:
            current_document = span["document_id"]
            current_owner = "Не установлен"
            current_main = None
        label = span.get("clause_label")
        text = span["original_text"].strip()
        if label:
            current_main = label.split(".", 1)[0]
        if label == "3.4":
            active_structure = True
        elif label and label.startswith("3.") and label != "3.4":
            try:
                active_structure = float(label) < 3.5
            except ValueError:
                active_structure = False

        if active_structure or ("структурн" in text.lower() and "департамент" in text.lower()):
            for match in UNIT_RE.finditer(text):
                name = re.sub(r"\s+", " ", match.group(1)).strip(" ,")
                abbr = match.group(2)
                if name.lower().startswith("департамент") and len(name.split()) >= 2:
                    store.add_org_unit(comparison_id, side, name, abbr, span["id"])
                    units.append({"name": name, "abbreviation": abbr, "span_id": span["id"]})

        owner_text = CLAUSE_PREFIX_RE.sub("", text)
        owner_match = LEADING_OWNER_RE.search(owner_text)
        if owner_match and label and label.startswith("5."):
            current_owner = _clean_owner(owner_match.group("owner"))
            section = _section(label)
            if section:
                owner_by_section[section] = current_owner
            store.add_role(comparison_id, side, current_owner, span["id"])
            roles.append({"name": current_owner, "span_id": span["id"]})

        if current_main not in {"4", "5", "6", "7", "8", "9", "10", "11", "12"}:
            continue
        section = _section(label)
        if current_main == "5":
            owner = owner_by_section.get(section or "", current_owner)
        else:
            owner = "БВА / Главный аудитор"
        if owner == "Не установлен":
            continue
        if not has_action(text):
            continue
        if len(text) < 35:
            continue
        action = best_action(text)
        object_text, scope = object_and_scope(text, action)
        function_id = stable_id("fn", comparison_id, side, span["id"], action)
        fn = FunctionAssertion(
            id=function_id,
            side=side,
            owner=owner,
            action=action,
            object=object_text,
            scope=scope,
            authority=authority(text),
            text=text,
            span_id=span["id"],
            document_id=span["document_id"],
            clause_label=label,
        )
        store.add_function(comparison_id, fn)
        functions.append(fn)

    dedup_units = {item["name"].lower(): item for item in units}
    dedup_roles = {item["name"].lower(): item for item in roles}
    return list(dedup_units.values()), functions, list(dedup_roles.values())


def _owner_key(owner: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", owner.lower().replace("ё", "е")).strip()


def match_functions(before: list[FunctionAssertion], after: list[FunctionAssertion]) -> tuple[list[CandidateMatch], dict[str, list[tuple[FunctionAssertion, float]]]]:
    candidates: dict[str, list[tuple[FunctionAssertion, float]]] = {}
    matches: list[CandidateMatch] = []
    for old in before:
        ranked = sorted(
            ((new, similarity(old.text, new.text)) for new in after), key=lambda item: item[1], reverse=True
        )[:5]
        candidates[old.id] = ranked
        if not ranked:
            continue
        new, score = ranked[0]
        if score < 0.38:
            continue
        same_owner = _owner_key(old.owner) == _owner_key(new.owner)
        relation = "preserved" if score >= 0.94 and same_owner else "changed" if same_owner else "transferred"
        if len([item for item in ranked if item[1] >= max(0.46, score - 0.08)]) >= 2 and score < 0.72:
            relation = "split"
        matches.append(CandidateMatch(old, new, score, relation))
    return matches, candidates


def _structure_findings(before_units: list[dict[str, Any]], after_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {item["name"].lower(): item for item in before_units}
    after = {item["name"].lower(): item for item in after_units}
    findings: list[dict[str, Any]] = []
    for key in sorted(before.keys() & after.keys()):
        old, new = before[key], after[key]
        findings.append(
            {
                "type": "structure_preserved",
                "title": "Подразделение сохранено в перечне",
                "summary": f"{old['name']} присутствует в обоих загруженных комплектах.",
                "before_owner": old["name"],
                "after_owner": new["name"],
                "before_function": "Присутствует в составе структуры",
                "after_function": "Присутствует в составе структуры",
                "before_span_id": old["span_id"],
                "after_span_id": new["span_id"],
                "evidence_status": "MATCH",
                "confidence": 0.99,
                "limitations": "Сохранение в перечне не доказывает неизменность всех функций.",
            }
        )
    for key in sorted(after.keys() - before.keys()):
        new = after[key]
        findings.append(
            {
                "type": "structure_added",
                "title": "Подразделение добавлено в перечень",
                "summary": f"{new['name']} найдено только в комплекте «После».",
                "after_owner": new["name"],
                "after_function": "Добавлено в перечисленный состав",
                "after_span_id": new["span_id"],
                "evidence_status": "MATCH",
                "confidence": 0.96,
                "limitations": "Для вывода о юридическом создании требуется распорядительное основание.",
            }
        )
    return findings


def _function_findings(
    before: list[FunctionAssertion],
    after: list[FunctionAssertion],
    matches: list[CandidateMatch],
    candidates: dict[str, list[tuple[FunctionAssertion, float]]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    matched_after: set[str] = set()
    by_before = {match.before.id: match for match in matches}
    labels = {
        "preserved": "Функция сохранена",
        "changed": "Функция изменена",
        "transferred": "Возможен перенос функции",
        "split": "Возможно разделение функции",
    }
    for old in before:
        match = by_before.get(old.id)
        if match:
            matched_after.add(match.after.id)
            limitations = (
                "Смысловое соответствие подтверждено текстами; изменение владельца требует проверки основания реорганизации."
                if match.relation in {"transferred", "split"}
                else "Сопоставление основано на загруженных версиях документов."
            )
            findings.append(
                {
                    "type": match.relation,
                    "title": labels[match.relation],
                    "summary": f"Сходство формулировок {round(match.score * 100)}%; сопоставлены действие, объект и область.",
                    "before_owner": old.owner,
                    "after_owner": match.after.owner,
                    "before_function": old.text,
                    "after_function": match.after.text,
                    "before_span_id": old.span_id,
                    "after_span_id": match.after.span_id,
                    "evidence_status": "MATCH" if match.score >= 0.52 else "UNKNOWN",
                    "confidence": match.score,
                    "limitations": limitations,
                }
            )
        else:
            top = candidates.get(old.id, [])
            top_score = top[0][1] if top else 0.0
            findings.append(
                {
                    "type": "potential_loss",
                    "title": "Функция не найдена в загруженном комплекте «После»",
                    "summary": f"Эквивалентное покрытие не найдено; лучший проверенный кандидат имеет сходство {round(top_score * 100)}%.",
                    "before_owner": old.owner,
                    "before_function": old.text,
                    "before_span_id": old.span_id,
                    "evidence_status": "UNKNOWN",
                    "confidence": round(1 - top_score, 4),
                    "limitations": "Это не доказательство утраты функции в компании: вывод ограничен загруженным комплектом.",
                }
            )

    for new in after:
        if new.id in matched_after:
            continue
        best = max((similarity(new.text, old.text) for old in before), default=0.0)
        if best >= 0.38:
            continue
        findings.append(
            {
                "type": "new",
                "title": "Новая формулировка функции",
                "summary": f"В комплекте «До» не найден близкий эквивалент; максимальное сходство {round(best * 100)}%.",
                "after_owner": new.owner,
                "after_function": new.text,
                "after_span_id": new.span_id,
                "evidence_status": "UNKNOWN",
                "confidence": round(1 - best, 4),
                "limitations": "Новая формулировка не обязательно означает впервые созданную функцию.",
            }
        )
    return findings


def _overlap_findings(after: list[FunctionAssertion]) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(after):
        for right in after[index + 1 :]:
            if _owner_key(left.owner) == _owner_key(right.owner):
                continue
            if "не установлен" in {_owner_key(left.owner), _owner_key(right.owner)}:
                continue
            generic_phrases = ("прочих поручен", "организует работу", "обеспечивает выполнение")
            if any(phrase in left.text.lower() or phrase in right.text.lower() for phrase in generic_phrases):
                continue
            score = similarity(left.text, right.text)
            if score >= 0.78 and not scope_conflicts(left.scope, right.scope):
                duplicates.append(
                    {
                        "type": "possible_duplicate",
                        "title": "Возможное дублирование функций",
                        "summary": f"У разных владельцев близки действие, объект и область (сходство {round(score * 100)}%).",
                        "before_owner": left.owner,
                        "after_owner": right.owner,
                        "before_function": left.text,
                        "after_function": right.text,
                        "before_span_id": left.span_id,
                        "after_span_id": right.span_id,
                        "evidence_status": "UNKNOWN",
                        "confidence": score,
                        "limitations": "Общие управленческие глаголы сами по себе не доказывают дублирование; требуется проверка границ ответственности.",
                    }
                )
            authority_pair = {left.authority, right.authority}
            object_score = similarity(left.object, right.object)
            if "исполняет" in authority_pair and authority_pair & {"утверждает", "контролирует"} and object_score >= 0.68:
                conflicts.append(
                    {
                        "type": "potential_conflict",
                        "title": "Потенциальное совмещение исполнения и контроля",
                        "summary": "Для близкого объекта у разных владельцев найдены исполнительные и контрольные полномочия.",
                        "before_owner": left.owner,
                        "after_owner": right.owner,
                        "before_function": left.text,
                        "after_function": right.text,
                        "before_span_id": left.span_id,
                        "after_span_id": right.span_id,
                        "evidence_status": "UNKNOWN",
                        "confidence": object_score,
                        "limitations": "Это индикатор для проверки, а не установленное нарушение или конфликт интересов.",
                    }
                )
    duplicates.sort(key=lambda item: item["confidence"], reverse=True)
    conflicts.sort(key=lambda item: item["confidence"], reverse=True)
    return duplicates[:12] + conflicts[:8]


class Analyzer:
    def __init__(self, store: Store, model: ModelAdapter):
        self.store = store
        self.model = model

    def run(self, comparison_id: str) -> dict[str, Any]:
        docs = self.store.list_documents(comparison_id)
        ready_before = [doc for doc in docs if doc["side"] == "before" and doc["status"] == "ready"]
        ready_after = [doc for doc in docs if doc["side"] == "after" and doc["status"] == "ready"]
        if not ready_before or not ready_after:
            raise ValueError("Для анализа нужен хотя бы один успешно разобранный документ в каждом комплекте")
        input_hash = hashlib.sha256(
            "|".join(sorted(doc["sha256"] for doc in docs)).encode()
        ).hexdigest()
        run_id = self.store.begin_run(comparison_id, self.model.mode, self.model.model, input_hash)
        sequence = 0

        def traced(tool: str, params: dict[str, Any], fn: Callable[[], Any], source_ids: list[str] | None = None) -> Any:
            nonlocal sequence
            sequence += 1
            started = time.perf_counter()
            try:
                result = fn()
                status, summary = "ok", _summarize(result)
                return result
            except Exception as exc:
                status, summary = "error", str(exc)
                raise
            finally:
                elapsed = int((time.perf_counter() - started) * 1000)
                self.store.add_trace(run_id, sequence, tool, params, source_ids or [], summary, status, elapsed)

        try:
            before_units, before_functions, before_roles = traced(
                "extract_registry", {"side": "before"}, lambda: extract_units_and_functions(self.store, comparison_id, "before")
            )
            after_units, after_functions, after_roles = traced(
                "extract_registry", {"side": "after"}, lambda: extract_units_and_functions(self.store, comparison_id, "after")
            )
            matches, candidates = traced(
                "compare_functions",
                {"before_count": len(before_functions), "after_count": len(after_functions), "threshold": 0.38},
                lambda: match_functions(before_functions, after_functions),
            )
            for match in matches:
                self.store.add_match(run_id, match, "MATCH" if match.score >= 0.52 else "UNKNOWN")
            structure = traced(
                "compare_structure", {"before_units": len(before_units), "after_units": len(after_units)},
                lambda: _structure_findings(before_units, after_units),
            )
            functional = traced(
                "check_coverage", {"candidate_depth": 5},
                lambda: _function_findings(before_functions, after_functions, matches, candidates),
            )
            overlap = traced(
                "check_overlap_and_independence", {"duplicate_threshold": 0.78, "conflict_object_threshold": 0.68},
                lambda: _overlap_findings(after_functions),
            )
            findings = structure + functional + overlap
            source_ids = [
                sid
                for item in findings[:20]
                for sid in (item.get("before_span_id"), item.get("after_span_id"))
                if sid
            ]
            evidence = [self.store.get_span(sid, comparison_id) for sid in dict.fromkeys(source_ids)]
            evidence = [item for item in evidence if item]
            model_review = traced(
                "model_review",
                {"mode": self.model.mode, "model": self.model.model, "finding_count": len(findings)},
                lambda: self.model.review_findings(findings, evidence),
                source_ids,
            )
            traced(
                "resolve_sources",
                {"finding_count": len(findings)},
                lambda: _validate_sources(self.store, comparison_id, findings),
                source_ids,
            )
            for order, finding in enumerate(findings):
                self.store.add_finding(run_id, comparison_id, finding, order)
            self.store.finish_run(run_id, "completed")
            result = self.store.analysis_result(comparison_id)
            result["model_review"] = model_review
            result["registry"] = {
                "before_units": before_units,
                "after_units": after_units,
                "before_roles": before_roles,
                "after_roles": after_roles,
            }
            return result
        except Exception as exc:
            self.store.finish_run(run_id, "failed", str(exc))
            raise


def _validate_sources(store: Store, comparison_id: str, findings: list[dict[str, Any]]) -> dict[str, int]:
    checked = 0
    for finding in findings:
        for key in ("before_span_id", "after_span_id"):
            span_id = finding.get(key)
            if span_id:
                span = store.get_span(span_id, comparison_id)
                if not span:
                    raise ValueError(f"Источник {span_id} не принадлежит сравнению")
                checked += 1
    return {"checked": checked}


def _summarize(value: Any) -> str:
    if isinstance(value, tuple):
        return ", ".join(_summarize(item) for item in value)
    if isinstance(value, list):
        return f"получено элементов: {len(value)}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)[:500]
    return str(value)[:500]
