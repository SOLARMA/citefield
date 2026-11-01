import pytest
from fakes import FakeBackend, answer, candidate, output, value
from pdfgen import write_pdf

from citefield.config import Config
from citefield.evaluate import EvalError, evaluate, score
from citefield.extraction import Extractor
from citefield.output import FieldReview

F = "engine_manufacturer"


def config(*names, examples=()):
    names = names or (F,)
    return Config(
        fields=[{"name": n, "description": n, "examples": list(examples)} for n in names],
        settings={"llm": {"model": "fake/model"}},
    )


def confirmed_absent(result, name):
    result.field(name).review = FieldReview(status="confirmed_absent")
    return result


@pytest.mark.parametrize(
    ("golden", "predicted", "expected"),
    [
        pytest.param(
            [value(F, "Onan", "accepted")],
            [value(F, "onan ")],
            {"true_positives": 1, "false_positives": 0, "false_negatives": 0},
            id="normalized match",
        ),
        pytest.param(
            [value(F, "Onan", "accepted")],
            [],
            {"true_positives": 0, "false_negatives": 1},
            id="missed",
        ),
        pytest.param(
            [value(F, "Onan", "accepted"), value(F, "Kubota", "rejected")],
            [value(F, "Onan"), value(F, "Kubota")],
            {"true_positives": 1, "false_positives": 1, "unjudged": 0},
            id="rejected value predicted again",
        ),
        pytest.param(
            [value(F, "Onan", "accepted")],
            [value(F, "Onan"), value(F, "Cummins")],
            {"true_positives": 1, "false_positives": 1, "unjudged": 1},
            id="value nobody judged",
        ),
        pytest.param(
            [value(F, "Onan Corp", "edited", reviewed_value="Onan")],
            [value(F, "Onan Corp")],
            {"true_positives": 0, "false_positives": 1, "false_negatives": 1, "unjudged": 0},
            id="edited: the original is a negative",
        ),
        pytest.param(
            [value(F, "Onan Corp", "edited", reviewed_value="Onan")],
            [value(F, "Onan")],
            {"true_positives": 1, "false_negatives": 0},
            id="edited: the reviewed value is a positive",
        ),
        pytest.param(
            [value(F, "Onan", "added")],
            [value(F, "Onan")],
            {"true_positives": 1},
            id="added",
        ),
        pytest.param(
            [value(F, "Onan", "accepted", context="engine")],
            [value(F, "Onan", context="generator set")],
            {"true_positives": 1, "context_mismatches": 1},
            id="context differs: still a match, counted",
        ),
        pytest.param(
            [value(F, "Genuine ANTCLABS BLTouch", "accepted")],
            [value(F, "Genuine ANTCLABS BLTouch®")],
            {"true_positives": 1, "false_positives": 0},
            id="trademark sign folded",
        ),
        pytest.param(
            [value(F, '9" x 9" x 9 1/4"', "accepted")],
            [value(F, "9\u201d x 9\u201d x 9 1/4\u201d")],
            {"true_positives": 1},
            id="curly quotes straightened",
        ),
        pytest.param(
            [value(F, "162 x 162 x 181 mm", "accepted")],
            [value(F, "162 x162 x181mm")],
            {"true_positives": 1},
            id="whitespace removed",
        ),
        pytest.param(
            [value(F, "1.75mm or 2.85mm", "accepted")],
            [value(F, "1.75mm or 2.85mm*")],
            {"true_positives": 1},
            id="footnote asterisk removed",
        ),
        pytest.param(
            [value(F, "18.92", "accepted", raw_unit="lb")],
            [value(F, "18.92lb")],
            {"true_positives": 1, "false_positives": 0},
            id="raw unit appended",
        ),
        pytest.param(
            [value(F, "18.92lb", "accepted", raw_unit="lb")],
            [value(F, "18.92 lb")],
            {"true_positives": 1},
            id="raw unit not appended twice",
        ),
        pytest.param(
            [value(F, "18.92", "accepted", raw_unit="lb")],
            [value(F, "18.92")],
            {"true_positives": 0, "false_positives": 1, "false_negatives": 1},
            id="missing unit: no match",
        ),
        pytest.param(
            [value(F, "5 C to 32 C", "accepted", equivalents=["5 C to 32 C (41 F to 113 F)"])],
            [value(F, "5 C to 32 C (41 F to 113 F)")],
            {"true_positives": 1, "false_positives": 0, "false_negatives": 0},
            id="equivalent form",
        ),
        pytest.param(
            [value(F, '6 3/8"', "accepted", equivalents=["162 mm"])],
            [value(F, '6 3/8"'), value(F, "162 mm")],
            {"true_positives": 1, "false_positives": 0, "false_negatives": 0},
            id="two forms of one value count once",
        ),
        pytest.param(
            [value(F, '6 3/4"', "edited", reviewed_value='6 3/8"', equivalents=["162 mm"])],
            [value(F, "162 mm")],
            {"true_positives": 1, "false_negatives": 0},
            id="equivalent of an edited value",
        ),
        pytest.param(
            [value(F, '6 3/8"', "accepted", equivalents=["162 mm"])],
            [value(F, "163 mm")],
            {"true_positives": 0, "false_positives": 1, "false_negatives": 1, "unjudged": 1},
            id="equivalent not predicted: one miss",
        ),
        pytest.param(
            [value(F, "Onan", "accepted"), value(F, "Kubota")],
            [value(F, "Cummins")],
            {"documents": 0, "false_positives": 0, "false_negatives": 0},
            id="pending value: field not reviewed",
        ),
        pytest.param(
            [],
            [value(F, "Onan")],
            {"documents": 0, "false_positives": 0},
            id="no values and not confirmed absent: not reviewed",
        ),
    ],
)
def test_score_field(golden, predicted, expected):
    report = score([(output(**{F: golden}), output(**{F: predicted}))], config())
    field = report.fields[0]
    assert {k: getattr(field, k) for k in expected} == expected


def test_precision_and_recall():
    golden = output(**{F: [value(F, "a", "accepted"), value(F, "b", "accepted")]})
    predicted = output(**{F: [value(F, "a"), value(F, "c"), value(F, "d")]})
    total = score([(golden, predicted)], config()).total
    assert (total.precision, total.recall) == (round(1 / 3, 4), 0.5)


def test_not_found_accuracy():
    absent = "refrigerant_type"
    golden = confirmed_absent(output(**{absent: "not_found"}), absent)
    cfg = config(absent)
    right = score([(golden, output(**{absent: "not_found"}))], cfg).total
    wrong = score([(golden, output(**{absent: [value(absent, "R-134a")]}))], cfg).total
    assert (right.confirmed_absent, right.not_found_correct, right.not_found_accuracy) == (1, 1, 1)
    assert (wrong.not_found_correct, wrong.false_positives, wrong.unjudged) == (0, 1, 1)


def test_values_in_examples_are_left_out():
    golden = output(**{F: [value(F, "Onan", "accepted"), value(F, "Kubota", "accepted")]})
    predicted = output(**{F: [value(F, "Onan"), value(F, "Kubota")]})
    field = score([(golden, predicted)], config(examples=["kubota"])).fields[0]
    assert (field.true_positives, field.false_positives) == (1, 0)


def test_examples_are_left_out_by_match_key():
    golden = output(**{F: [value(F, "Kubota", "accepted")]})
    predicted = output(**{F: [value(F, "Kubota®")]})
    field = score([(golden, predicted)], config(examples=["kubota"])).fields[0]
    assert (field.true_positives, field.false_positives) == (0, 0)


@pytest.mark.parametrize(
    ("golden", "message"),
    [
        (
            [value(F, "Onan", "accepted", equivalents=["Kubota"]), value(F, "Kubota", "accepted")],
            "matches two golden values",
        ),
        (
            [value(F, "Onan", "accepted", equivalents=["Kubota"]), value(F, "Kubota", "rejected")],
            "is also a negative",
        ),
        (
            [value(F, "Onan Corp", "edited", reviewed_value="Onan", equivalents=["Onan Corp"])],
            "is also a negative",
        ),
    ],
)
def test_equivalent_colliding_with_another_value_is_an_error(golden, message):
    with pytest.raises(EvalError, match=message):
        score([(output(**{F: golden}), output(**{F: []}))], config())


W = "dry_weight"


def number_config(examples=()):
    field = {"name": W, "description": W, "type": "number", "unit": "kg"}
    return Config(
        fields=[{**field, "examples": list(examples)}],
        settings={"llm": {"model": "fake/model"}, "extraction": {"number_locale": "en"}},
    )


def weight(raw, parsed, review="pending", **kw):
    return value(W, raw, review, raw_unit="kg", parsed=parsed, **kw)


@pytest.mark.parametrize(
    ("predicted", "tp", "fp"),
    [
        ([weight("1,140", 517.1)], 1, 0),  # "1,140 lb." converted, 0.13% from 516.4
        ([weight("520", 520)], 1, 0),  # 0.7%
        ([weight("530", 530)], 0, 1),  # 2.6%
        ([weight("1,140", 517.1), weight("516.4", 516.4, context="x")], 1, 0),  # one golden value
        ([weight("700", 700), weight("701", 701, context="x")], 0, 1),  # one wrong value
    ],
)
def test_numbers_match_within_one_percent(predicted, tp, fp):
    golden = output(**{W: [weight("516.4", 516.4, "accepted")]})
    field = score([(golden, output(**{W: predicted}))], number_config()).fields[0]
    assert (field.true_positives, field.false_positives) == (tp, fp)


def test_number_forms_written_by_hand_are_parsed():
    golden = output(
        **{
            W: [
                weight("600", 600, "edited", reviewed_value="516.4 kg", equivalents=["1,140 lb."]),
                weight("50", 50, "accepted", equivalents=["110 lb"], context="empty"),
            ]
        }
    )
    predicted = output(**{W: [weight("517.1", 517.1), weight("49.9", 49.9), weight("600", 600)]})
    report = score([(golden, predicted)], number_config())
    field = report.fields[0]
    assert (field.true_positives, field.false_positives, field.unjudged) == (2, 1, 0)


@pytest.mark.parametrize(
    ("golden", "message"),
    [
        ([weight("516.4", 516.4, "accepted", equivalents=["about 500"])], "doesn't parse"),
        ([weight("516.4", 516.4, "accepted", equivalents=["516.4"])], "doesn't parse"),  # no unit
        ([weight("1", 1, "edited", reviewed_value="25-60 kg")], "doesn't parse"),
        (
            [
                weight("516.4", 516.4, "accepted"),
                weight("600", 600, "accepted", equivalents=["517 kg"], context="x"),
            ],
            "matches two golden values",
        ),
    ],
)
def test_number_forms_that_fail_are_errors(golden, message):
    with pytest.raises(EvalError, match=message):
        score([(output(**{W: golden}), output(**{W: []}))], number_config())


def test_text_in_a_number_field_is_an_error():
    # what apply_review leaves without the config: the added value as written
    golden = output(**{W: [weight("1,140", "1,140", "added")]})
    with pytest.raises(EvalError, match="apply the review decisions again with the config"):
        score([(golden, output(**{W: []}))], number_config())


def test_number_examples_are_left_out_within_one_percent():
    golden = output(**{W: [weight("500", 500, "accepted")]})
    predicted = output(**{W: [weight("500.2", 500.2)]})
    field = score([(golden, predicted)], number_config(examples=["1,100 lb"])).fields[0]
    assert (field.true_positives, field.false_positives) == (0, 0)


def unparsed(raw, review="pending"):
    v = weight(raw, None, review)
    v.value = None  # a range keeps value null (ADR-0020)
    return v


def test_unparsed_numbers_match_by_text():
    golden = output(**{W: [unparsed("25-60", "accepted")]})
    predicted = output(**{W: [unparsed("25 - 60")]})
    field = score([(golden, predicted)], number_config()).fields[0]
    assert field.true_positives == 1


def test_carried_over_values_count_once():
    first = output("tm", **{F: [value(F, "Onan", "accepted")]})
    second = output("tm", **{F: [value(F, "Onan", "accepted")]})
    second.field(F).values[0].review.carried_from = first.run.run_id
    second.run.started_at = second.run.started_at.replace(year=2027)
    predicted = output("tm", **{F: [value(F, "Onan")]})
    total = score([(second, predicted), (first, predicted)], config()).total
    assert (total.true_positives, total.false_positives) == (1, 0)


def test_error_field_misses_everything():
    golden = output(**{F: [value(F, "Onan", "accepted")]})
    total = score([(golden, output(**{F: "error"}))], config()).total
    assert (total.errors, total.false_negatives) == (1, 1)


def test_accuracy_by_signal_range():
    golden = output(**{F: [value(F, "a", "accepted"), value(F, "b", "rejected")]})
    predicted = output(
        **{
            F: [
                value(F, "a", evidence_match=0.95, confidence=None),
                value(F, "b", evidence_match=0.3, confidence=0.85),
                value(F, "c", evidence_match=1.0, confidence=0.5),
            ]
        }
    )
    report = score([(golden, predicted)], config())
    buckets = {(b.signal, b.low): (b.values, b.correct) for b in report.signals}
    assert buckets[("evidence_match", 0.0)] == (1, 0)
    assert buckets[("evidence_match", 0.5)] == (0, 0)
    assert buckets[("evidence_match", 0.85)] == (2, 1)
    assert buckets[("model_confidence", 0.5)] == (1, 0)
    assert buckets[("model_confidence", 0.85)] == (1, 0)
    assert buckets[("model_confidence", None)] == (1, 1)


def test_warns_about_config_mismatch():
    golden = output(**{F: [value(F, "Onan", "accepted")], "firing_order": "not_found"})
    report = score([(golden, output(**{F: [value(F, "Onan")]}))], config(F, "engine_model"))
    assert any("different field config" in w for w in report.warnings)
    assert any("'firing_order' is not in the config" in w for w in report.warnings)
    assert any("'engine_model' is not in the golden output" in w for w in report.warnings)


def test_unjudged_values_are_listed():
    golden = output("tm", **{F: [value(F, "Onan", "accepted")]})
    predicted = output("tm", **{F: [value(F, "Cummins", context="engine")]})
    [unjudged] = score([(golden, predicted)], config()).unjudged
    assert (unjudged.document_id, unjudged.raw_value, unjudged.context) == (
        "tm",
        "Cummins",
        "engine",
    )


def test_evaluate_scores_saved_predictions(tmp_path):
    golden, predictions = tmp_path / "golden", tmp_path / "predictions"
    golden.mkdir()
    predictions.mkdir()
    output("tm", **{F: [value(F, "Onan", "accepted")]}).to_json(golden / "tm.json")
    output("tm", **{F: [value(F, "Onan")]}).to_json(predictions / "tm.json")

    report = evaluate(golden, config(), predictions=predictions)
    assert report.golden == str(golden)
    assert report.total.true_positives == 1

    (predictions / "tm.json").unlink()
    with pytest.raises(EvalError, match="no output for tm"):
        evaluate(golden, config(), predictions=predictions)


def test_evaluate_extracts_again(tmp_path):
    manual = write_pdf(tmp_path / "tm.pdf", ["Engine manufacturer: Onan"])
    cfg = config()
    first = Extractor(cfg, backend=FakeBackend(response("Onan"))).extract(manual)
    first.field(F).values[0].review.status = "accepted"
    golden = tmp_path / "golden"
    golden.mkdir()
    first.to_json(golden / "tm.json")

    again = Extractor(cfg, backend=FakeBackend(response("Kubota")))
    report = evaluate(golden, cfg, extractor=again)
    assert (report.total.true_positives, report.total.false_negatives) == (0, 1)

    manual.rename(tmp_path / "moved.pdf")
    with pytest.raises(EvalError, match="not found"):
        evaluate(golden, cfg, extractor=again)
    again = Extractor(cfg, backend=FakeBackend(response("Onan")))
    report = evaluate(golden, cfg, extractor=again, documents=tmp_path)
    assert report.total.true_positives == 1


def response(raw):
    return {"fields": [answer(F, candidate(raw, (1, f"Engine manufacturer: {raw}")))]}
