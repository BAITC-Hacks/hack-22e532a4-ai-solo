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
from .semantic import authority, best_action, has_action, object_and_scope, scope_conflicts, similarity, tokens


from .registry import extract_units_and_functions
from .search import SearchIndex


def _owner_key(owner: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", owner.lower().replace("ё", "е")).strip()


def match_functions(before: list[FunctionAssertion], after: list[FunctionAssertion], scorer=similarity) -> tuple[list[CandidateMatch], dict[str, list[tuple[FunctionAssertion, float]]]]:
    candidates: dict[str, list[tuple[FunctionAssertion, float]]] = {}
    matches: list[CandidateMatch] = []
    for old in before:
        ranked = sorted(
            ((new, scorer(old.text, new.text)) for new in after if not scope_conflicts(old.scope, new.scope)), key=lambda item: item[1], reverse=True
        )
        candidates[old.id] = ranked
        if not ranked:
            continue
        new, score = ranked[0]
        if score < 0.38:
            continue
        same_owner = _owner_key(old.owner) == _owner_key(new.owner)
        relation = "preserved" if score >= 0.94 and same_owner else "changed" if same_owner else "transferred"
        old_words = set(tokens(old.object))
        components = [(fn, value) for fn, value in ranked[:5] if value >= .38 and len(old_words & set(tokens(fn.object))) >= 2]
        if len(components) >= 2 and score < .8:
            left_words, right_words = [set(tokens(fn.object)) & old_words for fn, _ in components[:2]]
            if left_words - right_words and right_words - left_words:
                matches.extend(CandidateMatch(old, fn, value, 'split') for fn, value in components[:2])
                continue
        matches.append(CandidateMatch(old, new, score, relation))
    targets = defaultdict(list)
    for match in matches:
        targets[match.after.id].append(match)
    for group in targets.values():
        if len({m.before.text for m in group}) > 1:
            for match in group:
                if match.relation != 'split' and match.score < .94:
                    match.relation = 'merged'
    return matches, candidates


def _structure_findings(before_units: list[dict[str, Any]], after_units: list[dict[str, Any]], after_spans=()) -> list[dict[str, Any]]:
    before = {item["name"].lower(): item for item in before_units}
    after = {item["name"].lower(): item for item in after_units}
    findings: list[dict[str, Any]] = []
    transformed_old, transformed_new = set(), set()
    for source in after_spans:
        text = source['original_text'].lower()
        if not re.search(r'\b(?:переименовать|переименован[аоы]?|преобразовать|преобразован[аоы]?)\b', text):
            continue
        if re.search(r'\b(?:не|проект|предлагается|рекомендуется)\b', text):
            continue
        old_keys = [key for key in before.keys()-after.keys() if key in text]
        new_keys = [key for key in after.keys()-before.keys() if key in text]
        if len(old_keys) != 1 or len(new_keys) != 1:
            continue
        old_key,new_key = old_keys[0],new_keys[0]
        old,new = before[old_key],after[new_key]
        transformed_old.add(old_key); transformed_new.add(new_key)
        findings.append({'type':'structure_transformed','title':'В документе указано преобразование подразделения',
            'summary':f"{old['name']} → {new['name']}; найдено явное указание в документе После.",
            'before_owner':old['name'],'after_owner':new['name'],
            'before_function':'Подразделение старого перечня','after_function':source['original_text'],
            'before_span_id':old['span_id'],'after_span_id':source['id'],
            'evidence_status':'MATCH','confidence':1,
            'limitations':'Подтверждён текст указания; полномочия подписанта, утверждение и дата вступления в силу требуют отдельной проверки.'})
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
    for key in sorted(after.keys() - before.keys() - transformed_new):
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
    for key in sorted(before.keys() - after.keys() - transformed_old):
        old = before[key]
        findings.append({'type':'structure_removed','title':'Подразделение не найдено в новом перечне',
            'summary':old['name'], 'before_owner':old['name'], 'before_function':'Присутствует в старом перечне',
            'before_span_id':old['span_id'], 'evidence_status':'UNKNOWN', 'confidence':0,
            'limitations':'Это не доказательство ликвидации: возможны переименование или неполный комплект.'})
    return findings


def _function_findings(before, after, matches, candidates, scorer=similarity):
    findings = []
    matched_after = {m.after.id for m in matches}
    by_before = defaultdict(list)
    for match in matches:
        by_before[match.before.id].append(match)
    labels = {"preserved": "Функция сохранена", "changed": "Функция изменена",
              "transferred": "Возможен перенос функции", "split": "Возможно разделение функции",
              "merged": "Возможно объединение функций"}
    for old in before:
        links = by_before.get(old.id, [])
        if links:
            for match in links:
                exact = old.text == match.after.text and old.owner == match.after.owner and old.owner != "Не установлен"
                findings.append({
                    "type": match.relation, "title": labels[match.relation],
                    "summary": f"Найден кандидат соответствия; оценка сходства {round(match.score * 100)}%. Это не вероятность правильности.",
                    "before_owner": old.owner, "after_owner": match.after.owner,
                    "before_function": old.text, "after_function": match.after.text,
                    "before_span_id": old.span_id, "after_span_id": match.after.span_id,
                    "evidence_status": "MATCH" if exact else "UNKNOWN", "confidence": match.score,
                    "limitations": "Связь требует проверки действия, объекта, области и полномочий. Владелец может быть не установлен.",
                })
        else:
            ranked = candidates.get(old.id, [])
            score = ranked[0][1] if ranked else 0
            findings.append({
                "type": "potential_loss", "title": "Функция не найдена в загруженном комплекте «После»",
                "summary": f"Обследовано кандидатов: {len(ranked)}; лучшее сходство {round(score*100)}%.",
                "before_owner": old.owner, "before_function": old.text, "before_span_id": old.span_id,
                "evidence_status": "UNKNOWN", "confidence": 1-score,
                "limitations": "Отсутствие совпадения не доказывает утрату функции в компании; результат ограничен комплектом и методом поиска.",
            })
    for new in after:
        if new.id in matched_after:
            continue
        best = max((scorer(new.text, old.text) for old in before), default=0.)
        findings.append({
            "type":"new", "title":"Функция без подтверждённого соответствия в комплекте «До»",
            "summary":f"Лучший кандидат имеет сходство {round(best*100)}%; требуется проверка.",
            "after_owner":new.owner, "after_function":new.text, "after_span_id":new.span_id,
            "evidence_status":"UNKNOWN", "confidence":1-best,
            "limitations":"Это не доказательство впервые созданной функции.",
        })
    return findings


def _overlap_findings(after: list[FunctionAssertion]) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(after):
        for right in after[index + 1 :]:
            same_owner = _owner_key(left.owner) == _owner_key(right.owner)
            if "не установлен" in {_owner_key(left.owner), _owner_key(right.owner)}:
                continue
            generic_phrases = ("прочих поручен", "организует работу", "обеспечивает выполнение")
            if any(phrase in left.text.lower() or phrase in right.text.lower() for phrase in generic_phrases):
                continue
            score = similarity(left.text, right.text)
            if not same_owner and left.authority == right.authority and score >= 0.78 and not scope_conflicts(left.scope, right.scope):
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
            if same_owner and "исполняет" in authority_pair and authority_pair & {"утверждает", "контролирует"} and object_score >= 0.68 and not scope_conflicts(left.scope, right.scope):
                conflicts.append(
                    {
                        "type": "potential_conflict",
                        "title": "Потенциальное совмещение исполнения и контроля",
                        "summary": "У одного владельца найдены исполнение и контроль близкого объекта; проверьте разделение обязанностей и меры независимости.",
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
    return duplicates + conflicts


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
        unread = [doc for doc in docs if doc['status'] != 'ready' or doc.get('warnings')]
        metadata = {'incomplete_documents': [doc['filename'] for doc in unread], 'algorithm_version': 'audit-v2'}
        self.store.snapshot_run(run_id, docs, metadata)
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
            if self.model.mode == 'live':
                before_functions = traced('model_extract_functions', {'side':'before'},
                    lambda:self.model.extract_functions(self.store,comparison_id,'before'))
                after_functions = traced('model_extract_functions', {'side':'after'},
                    lambda:self.model.extract_functions(self.store,comparison_id,'after'))
            index = SearchIndex(self.store)
            metadata['function_ids'] = [f.id for f in before_functions + after_functions]
            traced('prepare_search_index', {'mode': index.mode, 'model': index.model_name},
                   lambda: index.prepare([fn.text for fn in before_functions + after_functions]))
            metadata['search_mode'] = index.mode
            metadata['embedding_model'] = index.model_name if index.mode == 'fastembed' else None
            self.store.snapshot_run(run_id, docs, metadata)
            matches, candidates = traced(
                "compare_functions",
                {"before_count": len(before_functions), "after_count": len(after_functions), "threshold": 0.38},
                lambda: match_functions(before_functions, after_functions, index.score),
            )
            if self.model.mode == 'live':
                matches = traced('model_match_functions', {'before_count':len(before_functions)},
                    lambda:self.model.match_functions(self.store,before_functions,candidates))
            for match in matches:
                self.store.add_match(run_id, match, "MATCH" if match.score >= 0.52 else "UNKNOWN")
            structure = traced(
                "compare_structure", {"before_units": len(before_units), "after_units": len(after_units)},
                lambda: _structure_findings(before_units, after_units, self.store.get_spans(comparison_id, 'after')),
            )
            functional = traced(
                "check_coverage", {"candidate_depth": 5},
                lambda: _function_findings(before_functions, after_functions, matches, candidates, index.score),
            )
            overlap = traced(
                "check_overlap_and_independence", {"duplicate_threshold": 0.78, "conflict_object_threshold": 0.68},
                lambda: _overlap_findings(after_functions),
            )
            metadata['coverage'] = {old.id: {'examined_functions':len(after_functions),
                'eligible_candidates':len(candidates.get(old.id, [])),
                'top_candidates':[{'function_id':fn.id,'source_id':fn.span_id,'score':score} for fn,score in candidates.get(old.id, [])[:5]]}
                for old in before_functions}
            self.store.snapshot_run(run_id, docs, metadata)
            findings = structure + functional + overlap
            incomplete = bool(unread) or not before_functions or not after_functions
            if incomplete:
                for finding in findings:
                    if finding['type'] in {'potential_loss', 'new', 'structure_added'}:
                        finding['evidence_status'] = 'UNKNOWN'
                        finding['limitations'] += ' Комплект извлечён не полностью; вывод предварительный.'
                    if finding['type'] == 'potential_loss':
                        finding['type'] = 'insufficient_data'
                        finding['title'] = 'Недостаточно данных для проверки покрытия функции'
                findings.insert(0, {'type': 'insufficient_data', 'title': 'Анализ неполного комплекта',
                    'summary': 'Есть непрочитанные документы либо на одной стороне не извлечены функции.',
                    'evidence_status': 'UNKNOWN', 'confidence': 0,
                    'limitations': 'Нельзя трактовать отсутствие результатов как отсутствие рисков. ' + '; '.join(metadata['incomplete_documents'])})
            source_ids = [
                sid
                for item in findings
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
            self.store.finish_run(run_id, "partial" if incomplete else "completed")
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
