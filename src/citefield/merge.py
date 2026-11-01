"""Dedupe candidates across chunks and runs, and detect conflicts."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from citefield.output import (
    FieldStatus,
    QuoteEvidence,
    ScalarValue,
    Signals,
    Value,
    ValueStatus,
    make_value_id,
    normalize_text,
    same_value,
)


@dataclass
class Draft:
    """One verified candidate, before merging."""

    raw_value: str
    value: ScalarValue
    raw_unit: str | None = None
    status: ValueStatus = "found"
    unit: str | None = None
    derivation: str | None = None
    unit_resolved: bool | None = None
    context: str | None = None
    evidence: list[QuoteEvidence] = field(default_factory=list)
    matches: list[float] = field(default_factory=list)  # evidence_match per evidence item
    model_confidence: float | None = None


def merge(field_name: str, drafts: list[Draft]) -> list[Value]:
    """Group drafts by value and normalized context; keep all their evidence.

    Numbers within 1% are the same value (ADR-0021). Each group's value comes from its
    representative: the first draft stated in the field's unit, or the first draft.
    """
    groups: list[list[Draft]] = []
    for draft in drafts:
        for group in groups:
            first = group[0]  # compare with the first, so groups don't drift by chaining
            if _context_key(first) == _context_key(draft) and same_value(
                first.value, first.raw_value, draft.value, draft.raw_value
            ):
                group.append(draft)
                break
        else:
            groups.append([draft])

    values = []
    for group in groups:
        rep = next((d for d in group if d.status == "found" and d.unit is not None), group[0])
        value_id = make_value_id(field_name, rep.value, rep.raw_value, rep.context)
        evidence: dict[tuple[int, str], float] = {}
        for draft in group:
            for item, match in zip(draft.evidence, draft.matches, strict=True):
                key = (item.page, item.quote)
                evidence[key] = max(match, evidence.get(key, 0.0))
        confidences = [d.model_confidence for d in group if d.model_confidence is not None]
        values.append(
            Value(
                value_id=value_id,
                status=rep.status,
                raw_value=rep.raw_value,
                raw_unit=rep.raw_unit,
                value=rep.value,
                unit=rep.unit,
                derivation=rep.derivation,
                context=rep.context,
                evidence=[QuoteEvidence(page=page, quote=quote) for page, quote in evidence],
                signals=Signals(
                    evidence_match=max(evidence.values(), default=0.0),
                    citation_count=len({page for page, _ in evidence}),
                    model_confidence=max(confidences) if confidences else None,
                    unit_resolved=rep.unit_resolved,
                ),
            )
        )
    return values


def field_status(values: list[Value]) -> FieldStatus:
    """`not_found` with no values; `conflict` when two different values share a context, or
    either has none; `found` otherwise. Numbers within 1% aren't different (ADR-0021)."""
    if not values:
        return "not_found"
    for a, b in combinations(values, 2):
        if same_value(a.value, a.raw_value, b.value, b.raw_value):
            continue
        if (
            a.context is None
            or b.context is None
            or normalize_text(a.context) == normalize_text(b.context)
        ):
            return "conflict"
    return "found"


def _context_key(draft: Draft) -> str | None:
    return normalize_text(draft.context) if draft.context else None
