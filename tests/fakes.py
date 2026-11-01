"""Test doubles: an LLM backend that replays canned responses, and output builders."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from citefield.llm.base import Completion, Message
from citefield.merge import field_status
from citefield.output import (
    DocumentInfo,
    ExtractionResult,
    FieldResult,
    QuoteEvidence,
    Review,
    RunInfo,
    ScalarValue,
    Signals,
    Value,
    make_value_id,
)
from citefield.parsers.base import ParserInfo

Response = str | dict[str, Any] | Exception


class FakeBackend:
    """Replays responses in order. A dict or str is validated against the schema like a real
    model answer; an exception is raised."""

    name = "fake"

    def __init__(self, *responses: Response, context_window: int = 100_000) -> None:
        self.responses = list(responses)
        self.context_window = context_window
        self.calls: list[list[Message]] = []

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def complete_structured(self, messages: list[Message], schema: type[BaseModel]) -> Completion:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeBackend ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        text = response if isinstance(response, str) else json.dumps(response)
        return Completion(
            parsed=schema.model_validate_json(text), input_tokens=100, output_tokens=10
        )


def answer(name: str, *candidates: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "candidates": list(candidates)}


def candidate(
    raw_value: str,
    *quotes: tuple[int, str],
    context: str | None = None,
    raw_unit: str | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "raw_value": raw_value,
        "raw_unit": raw_unit,
        "context": context,
        "evidence": [{"page": page, "quote": quote} for page, quote in quotes],
        "model_confidence": confidence,
    }


def output(document_id: str = "doc", **fields: list[Value] | str) -> ExtractionResult:
    """A minimal output; each keyword is a field with its values, or a field status string."""
    results = []
    for name, values in fields.items():
        if isinstance(values, str):
            results.append(FieldResult(name=name, status=values))
        else:
            results.append(FieldResult(name=name, status=field_status(values), values=values))
    return ExtractionResult(
        citefield_version="0",
        document=DocumentInfo(
            document_id=document_id,
            path=f"{document_id}.pdf",
            sha256="0" * 64,
            units=1,
            parser=ParserInfo(name="fake", version="0"),
        ),
        run=RunInfo(
            run_id="r",
            started_at=datetime(2026, 10, 31, tzinfo=UTC),
            model="fake/model",
            prompt_version="extract-v1",
            schema_hash="",
            settings_hash="",
        ),
        fields=results,
    )


def value(
    field: str,
    raw_value: str,
    review: str = "pending",
    *,
    context: str | None = None,
    reviewed_value: ScalarValue = None,
    raw_unit: str | None = None,
    equivalents: tuple[str, ...] = (),
    evidence_match: float = 1.0,
    confidence: float | None = 0.9,
    parsed: ScalarValue = None,  # the typed value; defaults to raw_value
) -> Value:
    typed = raw_value if parsed is None else parsed
    return Value(
        value_id=make_value_id(field, typed, raw_value, context),
        status="found",
        raw_value=raw_value,
        raw_unit=raw_unit,
        value=typed,
        context=context,
        evidence=[QuoteEvidence(page=1, quote=raw_value)],
        signals=Signals(
            evidence_match=evidence_match, citation_count=1, model_confidence=confidence
        ),
        review=Review(status=review, reviewed_value=reviewed_value, equivalents=list(equivalents)),
    )
