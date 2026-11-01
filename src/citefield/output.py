"""Result models and JSON Schema export."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

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


class _Parsed(Protocol):
    value: ScalarValue
    raw_value: str
    status: ValueStatus


@dataclass(frozen=True)
class NumberKey:
    """A number as compared: its value, and whether it was converted or stated.

    `width` is a stated value's written precision, half a unit of its last digit ("1.9" is
    ±0.05); 0 for a converted value, or a number given without its text.
    """

    value: int | float
    width: float = 0.0
    derived: bool = False


def number_key(v: _Parsed) -> NumberKey | None:
    """The `NumberKey` of a parsed value, or None when its value isn't a number."""
    if not is_number(v.value):
        return None
    if v.status == "derived":
        return NumberKey(v.value, derived=True)
    return NumberKey(v.value, written_precision(v.raw_value, v.value))


def written_precision(raw: str, value: float) -> float:
    """Half a unit of the last digit written in `raw`, which states `value`: "1.9" is
    ±0.05, and "1,140" is ±0.5 when read as 1140. 0 when the digits don't give `value`."""
    digits = re.sub(r"\D", "", raw).lstrip("0")
    if not digits or not value:
        return 0.0
    exponent = math.log10(abs(value) / int(digits))
    if abs(exponent - round(exponent)) > 1e-9:
        return 0.0  # other digits in the text, such as a unit "m3"
    return 0.5 * 10 ** round(exponent)


def numbers_match(a: NumberKey, b: NumberKey) -> bool:
    """Within `NUMBER_TOLERANCE`, or a converted number within the written precision of a
    stated one (ADR-0021): 114 cu. in. is 1.868 L, inside "1.9 L"."""
    diff = abs(a.value - b.value)
    if diff <= NUMBER_TOLERANCE * max(abs(a.value), abs(b.value)):
        return True
    if a.derived != b.derived:
        return diff <= (b if a.derived else a).width
    return False


def same_value(a: _Parsed, b: _Parsed) -> bool:
    """Whether two values are the same: numbers by `numbers_match`, others by `value_key`."""
    ka, kb = number_key(a), number_key(b)
    if ka is not None and kb is not None:
        return numbers_match(ka, kb)
    return value_key(a.value, a.raw_value) == value_key(b.value, b.raw_value)


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def make_value_id(field: str, value: ScalarValue, raw_value: str, context: str | None) -> str:
    """Stable id from field name, normalized value and normalized context."""
    norm_context = normalize_text(context) if context else None
    return stable_hash([field, value_key(value, raw_value), norm_context])[:16]
