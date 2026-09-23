from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from typing import Any


TYPE_LABELS = {
    "structure_preserved": "Структура: сохранено",
    "structure_added": "Структура: добавлено",
    "structure_removed": "Структура: не найдено в новом комплекте",
    "structure_transformed": "Структура: явно указанное преобразование",
    "preserved": "Функция сохранена",
    "changed": "Функция изменена",
    "transferred": "Перенос функции",
    "split": "Разделение функции",
    "merged": "Объединение функции",
    "new": "Новая формулировка",
    "potential_loss": "Потенциальная потеря",
    "possible_duplicate": "Возможное дублирование",
    "potential_conflict": "Потенциальный конфликт интересов",
    "insufficient_data": "Недостаточно данных",
}


def _source(source: dict[str, Any] | None) -> str:
    if not source:
        return "—"
    label = source.get("clause_label") or source.get("locator")
    return f"{source['filename']}, {source['revision']}, {label}"


def markdown_report(comparison: dict[str, Any], result: dict[str, Any]) -> str:
    findings = result.get("findings", [])
    counts = Counter(item["finding_type"] for item in findings)
    priorities = {'insufficient_data':0,'potential_loss':1,'potential_conflict':2,'possible_duplicate':3,'changed':4,'transferred':5,'split':6,'merged':7}
    findings = sorted(findings,key=lambda item: priorities.get(item['finding_type'], 10))
    run = result.get('run') or {}
    metadata = result.get('metadata') or {}
    lines = [
        f"# Аналитическое заключение BaqBaq — {comparison['name']}",
        "",
        f"Анализ завершён: {(result.get('run') or {}).get('completed_at') or 'не завершён'}",
        f"Режим модели: **{run.get('model_mode', comparison['model_mode'])}**",
        "",
        "> Выводы носят рекомендательный характер и требуют проверки ответственным сотрудником.",
        "",
        "## Сводка",
        "",
        f"Состояние анализа: {(result.get('run') or {}).get('status', 'не выполнен')}",
        "",
    ]
    if (result.get('run') or {}).get('status') == 'partial':
        lines += ['> НЕПОЛНЫЙ АНАЛИЗ: отсутствие вывода не означает отсутствие риска.', '']
    for warning in metadata.get('extraction_warnings', []):
        lines.append(f"- Предупреждение извлечения [{warning.get('side')}, {warning.get('source_id') or 'источник не установлен'}]: {warning['reason']}")
    risk_count = sum(counts[k] for k in ('potential_loss','potential_conflict','possible_duplicate'))
    pending = sum(not f.get('review') or f['review']['decision']=='review' for f in findings)
    lines += [f"Индикаторов риска: {risk_count}. Выводов без окончательного решения сотрудника: {pending}.",
              'Сначала проверьте риски и неполные данные; сохранённые функции приведены в конце реестра.', '',
              '## Приоритетные действия', '']
    actions={'potential_loss':'Проверить полный комплект и назначенного владельца обязанности.',
             'possible_duplicate':'Сверить область ответственности и полномочия обоих владельцев.',
             'potential_conflict':'Проверить разделение исполнения и независимого контроля.',
             'insufficient_data':'Добавить читаемые документы и пересчитать анализ.'}
    for item in findings:
        if item['finding_type'] in actions:
            lines.append(f"- {item['title']} — {_source(item.get('before_source') or item.get('after_source'))}: {actions[item['finding_type']]}")
    if not risk_count and not counts['insufficient_data']:
        lines.append('В этом запуске алгоритм не сформировал индикаторов риска. Это не гарантия их отсутствия.')
    lines += ['', '## Состав и показатели', '']
    for document in result.get('documents', []):
        lines.append(f"- Комплект {document['side']}: {document['filename']}; SHA-256 {document['sha256']}; {document['status']}")
    if not findings:
        lines += ["Анализ не выполнен или не сформировал проверяемых выводов.", ""]
    else:
        for key, count in sorted(counts.items()):
            lines.append(f"- {TYPE_LABELS.get(key, key)}: {count}")
        lines += ["", "## Реестр изменений и рисков", ""]
        for index, item in enumerate(findings, 1):
            lines += [
                f"### {index}. {item['title']}",
                "",
                item["summary"],
                "",
                f"- Тип: {TYPE_LABELS.get(item['finding_type'], item['finding_type'])}",
                f"- Статус доказательств: {item['evidence_status']}",
                f"- Оценка алгоритма: {round(item['confidence'] * 100)}/100 (не вероятность)",
                f"- До: {item.get('before_owner') or '—'} — {item.get('before_function') or '—'}",
                f"- После: {item.get('after_owner') or '—'} — {item.get('after_function') or '—'}",
                f"- Источник до: {_source(item.get('before_source'))}",
                f"- Источник после: {_source(item.get('after_source'))}",
                f"- Ограничение: {item.get('limitations') or 'Не указано'}",
                f"- Решение сотрудника: {(item.get('review') or {}).get('decision', 'не проверено')}",
                f"- Комментарий сотрудника: {(item.get('review') or {}).get('note', '')}",
                "",
            ]
            for source in (item.get('before_source'), item.get('after_source')):
                if source:
                    lines += [f"- Точная цитата [{source['side']}, {source['id']}]: {source['original_text']}", '']
            for source in item.get('before_context', []) + item.get('after_context', []):
                if source:
                    lines += [f"- Вводный пункт [{_source(source)}]: {source['original_text']}", '']
    lines += [
        "## Ограничения",
        "",
        "- Анализ ограничен загруженными документами и не устанавливает юридический факт реорганизации.",
        "- Отсутствие формулировки в комплекте «После» не доказывает утрату функции во всей организации.",
        "- Сходство обязанностей не доказывает дублирование без проверки объекта, области и полномочий.",
        "- Потенциальный конфликт интересов является индикатором для проверки, а не утверждением о нарушении.",
        "",
        "## Рекомендации",
        "",
        "1. Проверить выводы со статусом UNKNOWN и приложить недостающие распорядительные основания.",
        "2. Зафиксировать решения сотрудника в карточках выводов.",
        "3. Экспортировать заключение после завершения ручной проверки источников.",
        "",
        "## Прослеживаемость",
        "",
        f"Analysis run: `{(result.get('run') or {}).get('id', 'не выполнен')}`.",
        f"SHA-256 входа: {run.get('input_hash', 'не указан')}",
        f"SHA-256 кода анализа: {metadata.get('source_sha256', 'не указан')}",
        f"Алгоритм: {metadata.get('algorithm_version', 'не указан')}; поиск: {metadata.get('search_mode', 'не указан')}; модель: {run.get('model_name', 'не указана')}",
        f"Последнее решение сотрудника: {max((f['review']['created_at'] for f in findings if f.get('review')),default='решений нет')}",
        "Машинный результат закреплён за запуском. Этот экспорт включает текущие решения сотрудника; сохраните файл для фиксации версии заключения.",
        "Цитаты в интерфейсе и отчёте подставлены из exact store, а не из свободного ответа модели.",
    ]
    return "\n".join(lines)


def html_report(comparison: dict[str, Any], result: dict[str, Any]) -> str:
    md = markdown_report(comparison, result)
    blocks: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = html.escape(raw)
        if raw.startswith("### "):
            if in_list:
                blocks.append("</ul>")
                in_list = False
            blocks.append(f"<h3>{line[4:]}</h3>")
        elif raw.startswith("## "):
            if in_list:
                blocks.append("</ul>")
                in_list = False
            blocks.append(f"<h2>{line[3:]}</h2>")
        elif raw.startswith("# "):
            blocks.append(f"<h1>{line[2:]}</h1>")
        elif raw.startswith("- "):
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{line[2:]}</li>")
        elif raw.startswith("> "):
            blocks.append(f"<blockquote>{line[2:]}</blockquote>")
        elif re_numbered(raw):
            blocks.append(f"<p>{line}</p>")
        elif raw.strip():
            if in_list:
                blocks.append("</ul>")
                in_list = False
            blocks.append(f"<p>{line}</p>")
    if in_list:
        blocks.append("</ul>")
    body = "\n".join(blocks).replace("**", "")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Заключение BaqBaq</title>
<style>
@page{{margin:18mm}} body{{font:15px/1.55 system-ui,sans-serif;color:#202522;background:#f4f0e6;max-width:920px;margin:0 auto;padding:48px}}
h1,h2,h3{{font-family:Georgia,serif}} h1{{border-bottom:2px solid #225e63;padding-bottom:16px}} h2{{margin-top:36px;color:#225e63}}
h3{{margin-top:28px}} blockquote{{border-left:4px solid #a77a22;margin:24px 0;padding:10px 18px;background:#fffaf0}}
li{{margin:5px 0}} code{{font-family:Consolas,monospace}} @media print{{body{{background:white;padding:0}}}}
</style></head><body>{body}</body></html>"""


def re_numbered(value: str) -> bool:
    return len(value) > 2 and value[0].isdigit() and value[1:3] in {". ", ") "}
