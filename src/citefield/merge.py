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
    value_key,
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
    """Group drafts by normalized value and context; keep all their evidence."""
    groups: dict[str, list[Draft]] = {}
    for draft in drafts:
        value_id = make_value_id(field_name, draft.value, draft.raw_value, draft.context)
        groups.setdefault(value_id, []).append(draft)

    values = []
    for value_id, group in groups.items():
        first = group[0]
        evidence: dict[tuple[int, str], float] = {}
        for draft in group:
            for item, match in zip(draft.evidence, draft.matches, strict=True):
                key = (item.page, item.quote)
                evidence[key] = max(match, evidence.get(key, 0.0))
        confidences = [d.model_confidence for d in group if d.model_confidence is not None]
        values.append(
            Value(
                value_id=value_id,
                status=first.status,
                raw_value=first.raw_value,
                raw_unit=first.raw_unit,
                value=first.value,
                unit=first.unit,
                derivation=first.derivation,
                context=first.context,
                evidence=[QuoteEvidence(page=page, quote=quote) for page, quote in evidence],
                signals=Signals(
                    evidence_match=max(evidence.values(), default=0.0),
                    citation_count=len({page for page, _ in evidence}),
                    model_confidence=max(confidences) if confidences else None,
                    unit_resolved=first.unit_resolved,
                ),
            )
        )
    return values


def field_status(values: list[Value]) -> FieldStatus:
    """`not_found` with no values; `conflict` when two different values share a context, or
    either has none; `found` otherwise."""
    if not values:
        return "not_found"
    for a, b in combinations(values, 2):
        if value_key(a.value, a.raw_value) == value_key(b.value, b.raw_value):
            continue
        if (
            a.context is None
            or b.context is None
            or normalize_text(a.context) == normalize_text(b.context)
        ):
            return "conflict"
    return "found"
