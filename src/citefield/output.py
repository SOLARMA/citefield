"""Result models and JSON Schema export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from citefield.config import FieldDef, stable_hash
from citefield.parsers.base import ParserInfo

OUTPUT_SCHEMA_VERSION = 1

FieldStatus = Literal["found", "not_found", "conflict", "error"]
ValueStatus = Literal["found", "derived"]
ReviewStatus = Literal["pending", "accepted", "edited", "rejected", "added"]
FieldReviewStatus = Literal["pending", "confirmed_absent"]
ScalarValue = str | int | float | bool | None


class QuoteEvidence(BaseModel):
    kind: Literal["quote"] = "quote"
    page: int  # 1-based physical page of the PDF, not the printed page label
    quote: str


Evidence = QuoteEvidence


class Signals(BaseModel):
    evidence_match: float
    citation_count: int
    run_agreement: float | None = None
    model_confidence: float | None = None
    unit_resolved: bool | None = None


class Review(BaseModel):
    status: ReviewStatus = "pending"
    reviewed_value: ScalarValue = None
    equivalents: list[str] = []  # other forms of the same fact the evaluation also accepts
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    note: str | None = None
    carried_from: str | None = None
    previous: Any = None


class FieldReview(BaseModel):
    status: FieldReviewStatus = "pending"
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    note: str | None = None


class Value(BaseModel):
    value_id: str
    status: ValueStatus
    raw_value: str
    raw_unit: str | None = None
    value: ScalarValue = None
    unit: str | None = None
    derivation: str | None = None
    context: str | None = None
    evidence: list[Evidence]
    signals: Signals
    review: Review = Review()


class FieldResult(BaseModel):
    name: str
    status: FieldStatus
    values: list[Value] = []
    error: str | None = None  # set when status is "error"
    review: FieldReview = FieldReview()


class Proposal(BaseModel):
    definition: FieldDef
    possible_duplicate_of: str | None = None
    values: list[Value] = []


class OutputWarning(BaseModel):
    code: str
    message: str | None = None
    field: str | None = None
    units: list[int] | None = None
    raw: str | None = None


class DocumentInfo(BaseModel):
    document_id: str
    path: str
    sha256: str
    units: int
    parser: ParserInfo


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0


class RunInfo(BaseModel):
    run_id: str
    started_at: datetime
    model: str
    prompt_version: str
    schema_hash: str
    settings_hash: str
    n_runs: int = 1
    chunks: int = 1
    llm_calls: int = 0
    tokens: TokenUsage = TokenUsage()


class ExtractionResult(BaseModel):
    citefield_version: str
    output_schema_version: int = OUTPUT_SCHEMA_VERSION
    document: DocumentInfo
    run: RunInfo
    fields: list[FieldResult]
    proposals: list[Proposal] = []
    warnings: list[OutputWarning] = []

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> ExtractionResult:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def field(self, name: str) -> FieldResult:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(name)


def json_schema() -> dict[str, Any]:
    """JSON Schema of the output file."""
    return ExtractionResult.model_json_schema()


def normalize_text(text: str) -> str:
    """Case- and whitespace-insensitive form used to compare values and contexts."""
    return " ".join(text.casefold().split())


def value_key(value: ScalarValue, raw_value: str) -> ScalarValue:
    """What makes two values the same: the parsed value, or the raw text when there is none,
    normalized."""
    key = value if value is not None else raw_value
    return normalize_text(key) if isinstance(key, str) else key


# Two numbers within this share of the larger magnitude are the same value (ADR-0021).
NUMBER_TOLERANCE = 0.01


def same_value(a: ScalarValue, a_raw: str, b: ScalarValue, b_raw: str) -> bool:
    """Whether two values are the same: numbers within `NUMBER_TOLERANCE`, others by
    `value_key`."""
    if is_number(a) and is_number(b):
        return numbers_match(a, b)
    return value_key(a, a_raw) == value_key(b, b_raw)


def numbers_match(a: float, b: float) -> bool:
    return abs(a - b) <= NUMBER_TOLERANCE * max(abs(a), abs(b))


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def make_value_id(field: str, value: ScalarValue, raw_value: str, context: str | None) -> str:
    """Stable id from field name, normalized value and normalized context."""
    norm_context = normalize_text(context) if context else None
    return stable_hash([field, value_key(value, raw_value), norm_context])[:16]
