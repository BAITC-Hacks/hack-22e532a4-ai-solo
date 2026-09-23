from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from typing import Any


TYPE_LABELS = {
    "structure_preserved": "Структура: сохранено",
    "structure_added": "Структура: добавлено",
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
    lines = [
        f"# Аналитическое заключение BaqBaq — {comparison['name']}",
        "",
        f"Сформировано: {datetime.now(timezone.utc).isoformat()}",
        f"Режим модели: **{comparison['model_mode']}**",
        "",
        "> Выводы носят рекомендательный характер и требуют проверки ответственным сотрудником.",
        "",
        "## Сводка",
        "",
    ]
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
                f"- Уверенность алгоритма: {round(item['confidence'] * 100)}%",
                f"- До: {item.get('before_owner') or '—'} — {item.get('before_function') or '—'}",
                f"- После: {item.get('after_owner') or '—'} — {item.get('after_function') or '—'}",
                f"- Источник до: {_source(item.get('before_source'))}",
                f"- Источник после: {_source(item.get('after_source'))}",
                f"- Ограничение: {item.get('limitations') or 'Не указано'}",
                "",
            ]
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
