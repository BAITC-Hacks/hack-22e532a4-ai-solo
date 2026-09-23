from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from typing import Iterable


STOP = {
    "и", "в", "во", "на", "по", "с", "со", "к", "ко", "из", "за", "для", "при", "о", "об", "от",
    "до", "а", "но", "или", "что", "это", "как", "его", "ее", "их", "также", "иных", "иные",
    "общества", "общество", "настоящего", "соответствии", "части", "включая", "может", "должен",
    "директор", "директора", "департамент", "департамента", "направление", "направления",
}

CONCEPT_ROOTS = (
    (("взаимодейств",), "взаимодейств"),
    (("контрол", "монитор", "надзор", "провер"), "контрол"),
    (("готов", "формир", "составл", "разрабаты"), "формир"),
    (("запраш", "получ", "предостав"), "получ"),
    (("информац", "сведен", "данн"), "информац"),
    (("выполн", "исполн"), "исполн"),
    (("оцен", "оцени"), "оцен"),
    (("предлож", "рекомендац"), "предлож"),
    (("работ", "деятельн"), "деятельн"),
)

ACTION_ROOTS = (
    "организ", "осуществ", "обеспеч", "контрол", "анализ", "готов", "взаимодейств", "запраш",
    "разрабаты", "представ", "утверж", "провер", "выяв", "оцени", "провод", "формир", "координир",
    "руковод", "монитор", "соглас", "участв", "информ", "определ", "рассматри", "несет", "поруч",
)


@lru_cache(maxsize=8192)
def tokens(text: str) -> tuple[str, ...]:
    values = re.findall(r"[a-zа-яәіңғүұқөһ0-9]{3,}", text.lower().replace("ё", "е"))
    return tuple(stem(value) for value in values if value not in STOP)


def stem(word: str) -> str:
    endings = (
        "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "ание", "ение", "овать", "ирует",
        "ации", "ении", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам",
        "ям", "ах", "ях", "ом", "ем", "ами", "ями", "у", "ю", "а", "я", "ы", "и", "е", "о",
    )
    for ending in endings:
        if len(word) - len(ending) >= 4 and word.endswith(ending):
            word = word[: -len(ending)]
            break
    for roots, canonical in CONCEPT_ROOTS:
        if any(word.startswith(root) for root in roots):
            return canonical
    return word.rstrip("ь")


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    norm_l = math.sqrt(sum(value * value for value in left.values()))
    norm_r = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_l * norm_r) if norm_l and norm_r else 0.0


@lru_cache(maxsize=8192)
def _ngrams(text: str, size: int = 3) -> Counter[str]:
    compact = " ".join(tokens(text))
    if len(compact) < size:
        return Counter({compact: 1}) if compact else Counter()
    return Counter(compact[index : index + size] for index in range(len(compact) - size + 1))


@lru_cache(maxsize=131072)
def similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(tokens(left)), set(tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union)
    containment = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    subword = _cosine(_ngrams(left), _ngrams(right))
    return round(0.42 * jaccard + 0.28 * containment + 0.30 * subword, 4)


def best_action(text: str) -> str:
    for raw in re.findall(r"[а-яё]+", text.lower()):
        if any(raw.startswith(root) for root in ACTION_ROOTS):
            return raw
    return "описывает"


def authority(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("утверждает", "согласовывает", "одобряет")):
        return "утверждает"
    if any(word in lower for word in ("контролирует", "контролируют", "проверяет", "проверяют", "оценивает", "оценивают", "оценк", "надзор")):
        return "контролирует"
    if any(word in lower for word in ("консульт", "рекоменд", "предложен")):
        return "консультирует"
    return "исполняет"


def object_and_scope(text: str, action: str) -> tuple[str, str]:
    lower = text.lower()
    pos = lower.find(action)
    tail = text[pos + len(action) :] if pos >= 0 else text
    tail = re.sub(r"^[\s,:;—-]+", "", tail)
    object_text = re.split(r"[.;]", tail, maxsplit=1)[0][:260].strip()
    scopes = []
    for marker in ("ИТ", "данн", "операцион", "поддержива", "рисков", "СВК", "аудит", "качества"):
        if (bool(re.search(r'\bит\b', lower)) if marker == 'ИТ' else marker.lower() in lower):
            scopes.append(marker)
    return object_text or text[:220], ", ".join(dict.fromkeys(scopes)) or "общая область"


def scope_conflicts(left: str, right: str) -> bool:
    l, r = left.lower(), right.lower()
    it = (bool(re.search(r'\bит\b', l)) or "данн" in l, bool(re.search(r'\bит\b', r)) or "данн" in r)
    ops = ("операцион" in l or "поддержива" in l, "операцион" in r or "поддержива" in r)
    return (it[0] and ops[1] and not it[1]) or (it[1] and ops[0] and not it[0])


def has_action(text: str) -> bool:
    lower = text.lower()
    words = re.findall(r'[а-яё]+', lower)
    return any(word.startswith(root) for word in words
               if not word.startswith(('информац', 'организацион', 'контрольн', 'проверочн', 'оценочн', 'подготовлен'))
               for root in ACTION_ROOTS)
