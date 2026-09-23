from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ParsedSpan:
    ordinal: int
    locator: str
    clause_label: str | None
    text: str
    normalized_text: str
    kind: str = "paragraph"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedDocument:
    filename: str
    file_type: str
    sha256: str
    revision: str
    status: str
    spans: list[ParsedSpan]
    warnings: list[str] = field(default_factory=list)


@dataclass
class FunctionAssertion:
    id: str
    side: str
    owner: str
    action: str
    object: str
    scope: str
    authority: str
    text: str
    span_id: str
    document_id: str
    clause_label: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateMatch:
    before: FunctionAssertion
    after: FunctionAssertion
    score: float
    relation: str


BUSINESS_TYPES = {
    "structure_preserved",
    "structure_added",
    "structure_removed",
    "structure_transformed",
    "preserved",
    "changed",
    "transferred",
    "split",
    "merged",
    "new",
    "potential_loss",
    "possible_duplicate",
    "potential_conflict",
    "insufficient_data",
}

EVIDENCE_STATUSES = {"MATCH", "CONFLICT", "UNKNOWN", "DENIED"}
