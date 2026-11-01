import pytest

from citefield.merge import Draft, field_status, merge
from citefield.output import QuoteEvidence


def draft(value, *pages, context=None, match=1.0, confidence=0.9, raw=None, **parsed):
    return Draft(
        raw_value=raw or str(value),
        value=value,
        context=context,
        **parsed,
        evidence=[QuoteEvidence(page=p, quote=f"{value} on {p}") for p in pages],
        matches=[match] * len(pages),
        model_confidence=confidence,
    )


def test_same_value_merges_evidence():
    values = merge("engine", [draft("Onan", 2), draft("onan", 5, confidence=0.7)])
    assert len(values) == 1
    (value,) = values
    assert value.raw_value == "Onan"
    assert [e.page for e in value.evidence] == [2, 5]
    assert value.signals.citation_count == 2
    assert value.signals.model_confidence == 0.9
    assert value.review.status == "pending"


def test_value_id_is_stable_and_normalized():
    a = merge("engine", [draft("Onan  Corp", 2)])[0]
    b = merge("engine", [draft("onan corp", 9)])[0]
    c = merge("engine", [draft("Onan Corp", 2, context="60 Hz")])[0]
    assert a.value_id == b.value_id
    assert a.value_id != c.value_id


def test_evidence_match_is_best_of_evidence():
    d = draft("Onan", 2, 3)
    d.matches = [0.0, 0.92]
    assert merge("engine", [d])[0].signals.evidence_match == 0.92


def test_field_status():
    assert field_status([]) == "not_found"
    assert field_status(merge("m", [draft("A", 1)])) == "found"
    different_contexts = merge(
        "m", [draft("A", 1, context="60 Hz"), draft("B", 1, context="400 Hz")]
    )
    assert field_status(different_contexts) == "found"
    same_context = merge("m", [draft("A", 1, context="60 Hz"), draft("B", 1, context="60 hz")])
    assert field_status(same_context) == "conflict"
    missing_context = merge("m", [draft("A", 1, context="60 Hz"), draft("B", 1)])
    assert field_status(missing_context) == "conflict"
    same_value_two_contexts = merge("m", [draft("A", 1, context="x"), draft("A", 1)])
    assert field_status(same_value_two_contexts) == "found"


def weight(value, raw, page, unit_stated, context="MEP-803A"):
    """A draft of a number field in kg, stated in kg or converted from lb."""
    if unit_stated:
        parsed = {"status": "found", "unit": "kg", "unit_resolved": True, "raw_unit": "kg"}
    else:
        derivation = f"{raw} lb. = {value} kg"
        parsed = {"status": "derived", "unit": "kg", "unit_resolved": True, "raw_unit": "lb."}
        parsed["derivation"] = derivation
    return draft(value, page, context=context, raw=raw, **parsed)


def test_numbers_within_one_percent_merge():
    # "MEP-803A 1,140 lb. (516.4 kg)": 1,140 lb is 517.1 kg, 0.13% from the manual's 516.4.
    values = merge(
        "dry_weight", [weight(517.1, "1,140", 27, False), weight(516.4, "516.4", 28, True)]
    )
    assert len(values) == 1
    (value,) = values
    # the form stated in the field's unit represents the value, whatever the order
    assert (value.value, value.raw_value, value.status, value.derivation) == (
        516.4,
        "516.4",
        "found",
        None,
    )
    assert [e.page for e in value.evidence] == [27, 28]
    assert value.signals.citation_count == 2 and value.signals.unit_resolved is True
    alone = merge("dry_weight", [weight(516.4, "516.4", 28, True)])[0]
    assert value.value_id == alone.value_id


def test_converted_values_keep_the_first_when_none_is_stated_in_the_unit():
    values = merge(
        "dry_weight", [weight(517.1, "1,140", 27, False), weight(517.0, "1,140", 30, False)]
    )
    assert len(values) == 1 and values[0].evidence[0].page == 27 and values[0].status == "derived"


@pytest.mark.parametrize(
    "a, b, merged, status",
    [
        (100, 101, True, "found"),  # exactly 1% of the larger
        (100, 101.1, False, "conflict"),
        (-100, -100.5, True, "found"),
        (0, 0, True, "found"),
        (0, 0.001, False, "conflict"),
        (2.268, 2.27, True, "found"),  # "5 lb (2.27 kg)"
    ],
)
def test_number_tolerance(a, b, merged, status):
    values = merge("m", [draft(a, 1, context="x"), draft(b, 2, context="x")])
    assert (len(values) == 1) is merged
    assert field_status(values) == status


def test_groups_compare_with_their_first_value():
    # 100.9 is within 1% of 100, 101.8 isn't; chaining through 100.9 would merge all three.
    values = merge(
        "m",
        [draft(100, 1, context="x"), draft(100.9, 2, context="x"), draft(101.8, 3, context="x")],
    )
    assert [v.value for v in values] == [100, 101.8]


def test_close_numbers_in_other_contexts_are_no_conflict():
    values = merge("m", [draft(516.4, 1, context="MEP-803A"), draft(517.1, 2)])
    assert len(values) == 2  # contexts differ
    assert field_status(values) == "found"  # but they're the same value
