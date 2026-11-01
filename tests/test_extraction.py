import pytest
from fakes import FakeBackend, answer, candidate
from pdfgen import write_pdf

from citefield.config import Config
from citefield.extraction import ExtractionError, Extractor
from citefield.output import ExtractionResult
from citefield.planning import DocumentTooLarge

PAGES = [
    "OPERATOR'S MANUAL\nGENERATOR SET, TACTICAL QUIET, 10 KW",
    "Table 1. Leading Particulars.\nModel MEP-803A (60 Hz)\nModel MEP-813A (400 Hz)\n"
    "Engine:\nManufacturer Onan\nModel DN4M",
    "Cooling System:\nType Pressurized radiator and pump",
]

FIELDS = [
    {"name": "engine_manufacturer", "description": "Engine manufacturer."},
    {"name": "model_designation", "description": "Model designation of the machine."},
    {
        "name": "machine_category",
        "description": "Type of machine.",
        "type": "enum",
        "values": ["generator", "compressor", "other"],
    },
    {"name": "refrigerant_type", "description": "Refrigerant used."},
]


@pytest.fixture
def manual(tmp_path):
    return write_pdf(tmp_path / "tm-generator.pdf", PAGES)


def extractor(*responses, fields=FIELDS, **settings):
    config = Config(fields=fields, settings={"llm": {"model": "fake/model"}, **settings})
    return Extractor(config, backend=FakeBackend(*responses))


def good_response():
    return {
        "fields": [
            answer("engine_manufacturer", candidate("Onan", (2, "Manufacturer Onan"))),
            answer(
                "model_designation",
                candidate("MEP-803A", (2, "Model MEP-803A (60 Hz)"), context="60 Hz"),
                candidate("MEP-813A", (2, "Model MEP-813A (400 Hz)"), context="400 Hz"),
            ),
            answer("machine_category", candidate("Generator", (1, "GENERATOR SET"))),
            answer("refrigerant_type"),
        ]
    }


def test_extract_end_to_end(manual, tmp_path):
    ex = extractor(good_response())
    result = ex.extract(manual)

    assert result.document.document_id == "tm-generator"
    assert result.document.units == 3
    assert result.run.prompt_version == "extract-v1"
    assert result.run.llm_calls == 1
    assert result.run.tokens.input == 100

    engine = result.field("engine_manufacturer")
    assert engine.status == "found"
    (value,) = engine.values
    assert value.value == "Onan"
    assert value.status == "found"
    assert value.evidence[0].page == 2
    assert value.signals.evidence_match == 1.0
    assert value.signals.citation_count == 1
    assert value.signals.unit_resolved is None
    assert value.review.status == "pending"

    models = result.field("model_designation")
    assert models.status == "found"
    assert [v.context for v in models.values] == ["60 Hz", "400 Hz"]

    category = result.field("machine_category")
    assert category.values[0].value == "generator"
    assert category.values[0].raw_value == "Generator"

    assert result.field("refrigerant_type").status == "not_found"
    assert {"code": "low_text_units"}.items() <= result.warnings[0].model_dump().items()

    target = tmp_path / "out.json"
    result.to_json(target)
    assert ExtractionResult.from_json(target) == result


def test_prompt_marks_pages_and_lists_fields(manual):
    ex = extractor(good_response())
    ex.extract(manual)
    user = ex.backend.calls[0][1].content
    assert "[[page 2]]\nTable 1. Leading Particulars." in user
    assert "- name: machine_category" in user
    assert "allowed values: generator, compressor, other" in user


def test_unverified_quote_is_kept_and_flagged(manual):
    response = {
        "fields": [answer("engine_manufacturer", candidate("Cummins", (2, "Manufacturer Cummins")))]
    }
    result = extractor(response, fields=FIELDS[:1]).extract(manual)
    value = result.field("engine_manufacturer").values[0]
    assert value.value == "Cummins"
    assert 0.0 < value.signals.evidence_match < 0.85  # "Manufacturer" matches, "Cummins" doesn't
    (warning,) = [w for w in result.warnings if w.code == "evidence_not_verified"]
    assert (warning.field, warning.raw) == ("engine_manufacturer", "Cummins")


def test_verified_quote_has_no_warning(manual):
    result = extractor(good_response()).extract(manual)
    assert not [w for w in result.warnings if w.code == "evidence_not_verified"]


def test_candidate_without_evidence_is_dropped(manual):
    response = {"fields": [answer("engine_manufacturer", candidate("Onan"))]}
    result = extractor(response, fields=FIELDS[:1]).extract(manual)
    assert result.field("engine_manufacturer").status == "not_found"


def test_conflict_without_context(manual):
    response = {
        "fields": [
            answer(
                "engine_manufacturer",
                candidate("Onan", (2, "Manufacturer Onan")),
                candidate("Cummins", (2, "Manufacturer Onan")),
            )
        ]
    }
    result = extractor(response, fields=FIELDS[:1]).extract(manual)
    field = result.field("engine_manufacturer")
    assert field.status == "conflict"
    assert len(field.values) == 2


def test_unknown_enum_value_warns(manual):
    response = {"fields": [answer("machine_category", candidate("welder", (1, "GENERATOR SET")))]}
    result = extractor(response, fields=FIELDS[2:3]).extract(manual)
    value = result.field("machine_category").values[0]
    assert value.value is None
    assert value.raw_value == "welder"
    assert any(w.code == "enum_value_unknown" for w in result.warnings)


def test_invalid_response_is_retried(manual):
    ex = extractor("{not json", good_response())
    result = ex.extract(manual)
    assert result.run.llm_calls == 2
    assert result.field("engine_manufacturer").status == "found"


def test_batch_that_keeps_failing_marks_its_fields_error(manual):
    ex = extractor(
        "{bad",
        "{bad",
        {"fields": [answer("machine_category"), answer("refrigerant_type")]},
        extraction={"field_batch_size": 2},
        llm={"model": "fake/model", "max_retries": 1},
    )
    result = ex.extract(manual)
    assert result.run.llm_calls == 3
    assert result.field("engine_manufacturer").status == "error"
    assert "invalid response after 2 attempt(s)" in result.field("engine_manufacturer").error
    assert result.field("model_designation").status == "error"
    assert result.field("machine_category").status == "not_found"


def test_api_error_is_not_retried(manual):
    ex = extractor(RuntimeError("rate limited"), fields=FIELDS[:1])
    result = ex.extract(manual)
    assert result.run.llm_calls == 1
    assert result.field("engine_manufacturer").error == "RuntimeError: rate limited"


def test_unexpected_field_warns(manual):
    response = {"fields": [answer("engine_manufacturer"), answer("colour")]}
    result = extractor(response, fields=FIELDS[:1]).extract(manual)
    assert any(w.code == "unexpected_field" and w.raw == "colour" for w in result.warnings)


def test_unsupported_types_keep_raw_value(manual):
    fields = [{"name": "has_wheels", "description": "Wheels.", "type": "boolean"}]
    response = {"fields": [answer("has_wheels", candidate("yes", (2, "Manufacturer Onan")))]}
    result = extractor(response, fields=fields).extract(manual)
    assert result.field("has_wheels").values[0].value is None
    assert any(w.code == "unsupported_field_type" for w in result.warnings)


def test_numbers_are_converted_to_the_field_unit(manual):
    fields = [{"name": "dry_weight", "description": "Dry weight.", "type": "number", "unit": "kg"}]
    response = {
        "fields": [
            answer("dry_weight", candidate("1,140", (2, "Manufacturer Onan"), raw_unit="lb."))
        ]
    }
    ex = extractor(response, fields=fields, extraction={"number_locale": "en"})
    value = ex.extract(manual).field("dry_weight").values[0]
    assert (value.status, value.value, value.unit) == ("derived", 517.1, "kg")
    assert value.derivation == "1,140 lb. = 517.1 kg"
    assert value.signals.unit_resolved is True


@pytest.mark.parametrize(
    "locale, value, warning", [("auto", None, "ambiguous_number"), ("en", 1140, None)]
)
def test_number_locale_reaches_parsing(manual, locale, value, warning):
    fields = [{"name": "run_hours", "description": "Hours.", "type": "integer"}]
    response = {"fields": [answer("run_hours", candidate("1,140", (2, "Manufacturer Onan")))]}
    ex = extractor(response, fields=fields, extraction={"number_locale": locale})
    result = ex.extract(manual)
    assert result.field("run_hours").values[0].value == value
    assert [w.code for w in result.warnings if w.field == "run_hours"] == (
        [warning] if warning else []
    )


def test_plan_and_too_large(manual):
    ex = extractor(extraction={"field_batch_size": 3})
    plan = ex.plan(ex.parse(manual))
    assert (plan.chunks, len(plan.batches), plan.calls) == (1, 2, 2)
    small = extractor(chunking={"max_input_tokens": 50})
    with pytest.raises(DocumentTooLarge):
        small.plan(small.parse(manual))


def test_missing_model_and_n_runs(manual):
    with pytest.raises(ExtractionError, match="settings.llm.model"):
        Extractor(Config(fields=FIELDS)).backend  # noqa: B018
    with pytest.raises(ExtractionError, match="n_runs"):
        ex = extractor(extraction={"n_runs": 3})
        ex.plan(ex.parse(manual))
