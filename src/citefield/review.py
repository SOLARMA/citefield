"""Apply review decisions, and carry reviews over on re-extraction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from citefield.config import Config, FieldDef
from citefield.merge import field_status
from citefield.output import (
    ExtractionResult,
    FieldReview,
    QuoteEvidence,
    Review,
    ScalarValue,
    Signals,
    Value,
    make_value_id,
)
from citefield.verify import ParsedValue, evidence_match, parse_value


class ValueDecision(BaseModel):
    value_id: str
    status: Literal["pending", "accepted", "edited", "rejected"]
    reviewed_value: ScalarValue = None  # required when status is "edited"
    also_accept: list[str] = []  # equivalent forms, for an accepted or edited value
    note: str | None = None

    @model_validator(mode="after")
    def _edited_needs_value(self) -> ValueDecision:
        if (self.status == "edited") != (self.reviewed_value is not None):
            raise ValueError(f"value {self.value_id}: reviewed_value goes with status 'edited'")
        if self.also_accept and self.status not in ("accepted", "edited"):
            raise ValueError(f"value {self.value_id}: also_accept needs status accepted or edited")
        return self


class AddedValue(BaseModel):
    """A value the reviewer found and the LLM missed. It needs evidence like any other."""

    raw_value: str
    value: ScalarValue = None  # defaults to raw_value, parsed when apply_review has the config
    raw_unit: str | None = None
    unit: str | None = None
    context: str | None = None
    evidence: list[QuoteEvidence]
    also_accept: list[str] = []  # equivalent forms
    note: str | None = None

    @model_validator(mode="after")
    def _needs_evidence(self) -> AddedValue:
        if not self.evidence:
            raise ValueError(f"added value {self.raw_value!r} needs evidence")
        return self


class FieldDecision(BaseModel):
    status: Literal["pending", "confirmed_absent"] | None = None  # None: leave as is
    note: str | None = None
    values: list[ValueDecision] = []
    added: list[AddedValue] = []


class Decisions(BaseModel):
    """What a reviewer decided about one output file, keyed by field name."""

    reviewer: str | None = None
    reviewed_at: datetime | None = None  # defaults to now
    fields: dict[str, FieldDecision] = {}


def load_decisions(path: str | Path) -> Decisions:
    """Load decisions from a YAML file."""
    return Decisions.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def apply_review(
    output: str | Path | ExtractionResult,
    decisions: Decisions | dict[str, Any],
    *,
    pages: Mapping[int, str] | None = None,
    page_window: int = 1,
    config: Config | None = None,
) -> ExtractionResult:
    """Write review decisions into a copy of an output. Raises `ValueError` for a field or
    value_id the output doesn't have, or a field confirmed absent that keeps unrejected values.

    `pages` maps 1-based page numbers to the parsed text of the document, to verify the
    evidence of added values like any other; without it their `evidence_match` is 0. With
    `config`, the raw value of an added value is parsed and converted like an extracted one,
    and a value that can't be parsed adds its warning to the output; without it, the value
    is the raw value as written.
    """
    if isinstance(output, ExtractionResult):
        result = output.model_copy(deep=True)
    else:
        result = ExtractionResult.from_json(output)
    decisions = Decisions.model_validate(decisions)
    reviewer = decisions.reviewer
    reviewed_at = decisions.reviewed_at or datetime.now().astimezone()
    fdefs = {f.name: f for f in config.fields} if config else {}

    for name, decision in decisions.fields.items():
        try:
            field = result.field(name)
        except KeyError:
            raise ValueError(f"field {name!r} is not in the output") from None
        by_id = {v.value_id: v for v in field.values}
        for item in decision.values:
            value = by_id.get(item.value_id)
            if value is None:
                raise ValueError(f"field {name!r}: no value with id {item.value_id}")
            reviewed = item.status != "pending"
            value.review = value.review.model_copy(
                update={
                    "status": item.status,
                    "reviewed_value": item.reviewed_value,
                    "equivalents": item.also_accept,
                    "reviewer": reviewer if reviewed else None,
                    "reviewed_at": reviewed_at if reviewed else None,
                    "note": item.note,
                }
            )
        for added in decision.added:
            parsed = _parse_added(added, fdefs.get(name), config)
            if parsed.warning is not None and parsed.warning not in result.warnings:
                result.warnings.append(parsed.warning)  # once, if applied again
            value = _added_value(
                name, added, parsed, reviewer, reviewed_at, pages or {}, page_window
            )
            existing = by_id.get(value.value_id)
            if existing is None:
                field.values.append(value)
            elif existing.review.status == "added":  # decisions applied again
                field.values[field.values.index(existing)] = value
            else:
                raise ValueError(
                    f"field {name!r}: added value {added.raw_value!r} is already in the output "
                    f"as {value.value_id}; accept or edit that one instead"
                )
            by_id[value.value_id] = value
        if decision.added and field.status != "error":
            field.status = field_status(field.values)

        if decision.status == "confirmed_absent":
            if any(v.review.status != "rejected" for v in field.values):
                raise ValueError(
                    f"field {name!r}: can't confirm absent while it has values not rejected"
                )
            field.review = FieldReview(
                status="confirmed_absent",
                reviewer=reviewer,
                reviewed_at=reviewed_at,
                note=decision.note,
            )
        elif decision.status == "pending":
            field.review = FieldReview(note=decision.note)
    return result


def _parse_added(added: AddedValue, fdef: FieldDef | None, config: Config | None) -> ParsedValue:
    """An added value parsed like an extracted one when the config is given; otherwise the
    reviewer's own value, or the raw value as written."""
    if added.value is None and config is not None and fdef is not None:
        locale = config.settings.extraction.number_locale
        return parse_value(fdef, added.raw_value, added.raw_unit, locale, config.units)
    value = added.raw_value if added.value is None else added.value
    return ParsedValue(value, unit=added.unit)


def _added_value(
    field_name: str,
    added: AddedValue,
    parsed: ParsedValue,
    reviewer: str | None,
    reviewed_at: datetime,
    pages: Mapping[int, str],
    page_window: int,
) -> Value:
    return Value(
        value_id=make_value_id(field_name, parsed.value, added.raw_value, added.context),
        status=parsed.status,
        raw_value=added.raw_value,
        raw_unit=added.raw_unit,
        value=parsed.value,
        unit=parsed.unit,
        derivation=parsed.derivation,
        context=added.context,
        evidence=added.evidence,
        signals=Signals(
            evidence_match=max(
                evidence_match(e.quote, e.page, pages, page_window=page_window)
                for e in added.evidence
            ),
            citation_count=len({e.page for e in added.evidence}),
            unit_resolved=parsed.unit_resolved,
        ),
        review=Review(
            status="added",
            equivalents=added.also_accept,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            note=added.note,
        ),
    )
