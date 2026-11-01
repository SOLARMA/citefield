import pytest

from citefield.config import FieldDef
from citefield.verify import (
    NumberParseError,
    clean_unit,
    evidence_match,
    normalize_for_match,
    parse_number,
    parse_value,
    partial_ratio,
)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Permission is granted", "Permissionisgranted"),
        ("pro-\ncedure", "procedure"),
        ("MEP-803A", "MEP803A"),
        ("ﬁlter", "filter"),
        ("“Output”", '"output"'),
        ("Engine  Manufacturer", "engine manufacturer"),
        ("10 kW – 60 Hz", "10 kW - 60 Hz"),
    ],
)
def test_normalize_for_match(a, b):
    assert normalize_for_match(a) == normalize_for_match(b)


def test_partial_ratio():
    assert partial_ratio("onan", "manufactureronanmodel") == 1.0
    assert partial_ratio("", "anything") == 0.0
    assert 0.85 < partial_ratio("manufacturer0nan", "enginemanufactureronanmodel") < 1.0


PAGES = {
    1: "Cover page",
    2: "Table 1. Leading Particulars.\nManufacturer Onan\nModel DN4M",
    3: "Cooling System:\nType Pressurized radiator and pump",
    5: "Firing Order 1, 3, 4, 2",
}


@pytest.mark.parametrize(
    "quote, page",
    [
        ("Manufacturer Onan", 2),
        ("manufacturer  onan", 2),  # case and whitespace
        ("Type Pressurized radiator and pump", 2),  # on the next page, within the window
    ],
)
def test_evidence_match_exact(quote, page):
    assert evidence_match(quote, page, PAGES, page_window=1) == 1.0


@pytest.mark.parametrize(
    "quote, page, low, high",
    [
        ("Manufacturer 0nan", 2, 0.85, 1.0),  # a near match keeps its score
        ("Manufacturer Cummins", 2, 0.4, 0.85),  # a partial match is reported, not zeroed
        ("Firing Order 1, 3, 4, 2", 2, 0.0, 0.85),  # three pages away
    ],
)
def test_evidence_match_reports_the_raw_best_score(quote, page, low, high):
    assert low <= evidence_match(quote, page, PAGES, page_window=1) < high


def test_evidence_match_is_zero_when_nothing_matched():
    assert evidence_match("Manufacturer Onan", 99, PAGES) == 0.0  # page doesn't exist
    assert evidence_match("zzzzqqqq", 2, PAGES) == 0.0


def test_evidence_match_window_zero_checks_only_the_cited_page():
    assert evidence_match("Type Pressurized radiator", 2, PAGES, page_window=0) < 0.85


@pytest.mark.parametrize(
    "text, locale, expected",
    [
        ("10", "auto", 10),
        ("  -25 ", "auto", -25),
        ("−7", "auto", -7),  # Unicode minus
        ("+3", "en", 3),
        ("516.4", "auto", 516.4),
        ("516,4", "auto", 516.4),
        (".99", "auto", 0.99),
        (",5", "auto", 0.5),
        ("0,500", "auto", 0.5),  # "0" can't start a thousands group
        ("1234.567", "auto", 1234.567),  # nor can four digits
        ("1,140.5", "auto", 1140.5),
        ("1.140,5", "auto", 1140.5),
        ("1,140,000", "auto", 1140000),
        ("1.140.000", "auto", 1140000),
        ("34.0", "auto", 34),  # a whole number is an int
        ("18.92*", "auto", 18.92),  # footnote mark
        ("1,140", "en", 1140),
        ("1,140", "it", 1.14),
        ("24.000", "de", 24000),
        ("24.000", "en", 24),
        ("516,4", "de", 516.4),
    ],
)
def test_parse_number(text, locale, expected):
    number = parse_number(text, locale)
    assert number == expected
    assert type(number) is type(expected)


@pytest.mark.parametrize(
    "text, locale, code",
    [
        ("24.000", "auto", "ambiguous_number"),
        ("1,140", "auto", "ambiguous_number"),
        ("25-60", "auto", "number_not_scalar"),
        ("170 – 200", "auto", "number_not_scalar"),
        ("-25 to 0", "auto", "number_not_scalar"),
        ("15 ±3", "auto", "number_not_scalar"),
        ("15 +/- 3", "auto", "number_not_scalar"),
        ("", "auto", "not_a_number"),
        ("approx. 10", "auto", "not_a_number"),
        ("10 kW", "auto", "not_a_number"),
        ("1,14,0", "auto", "not_a_number"),
        ("1.2.3,4,5", "auto", "not_a_number"),
        ("516,4", "en", "not_a_number"),  # the locale decides every form
        ("516.4", "de", "not_a_number"),
        ("1.140,5", "en", "not_a_number"),
    ],
)
def test_parse_number_rejects(text, locale, code):
    with pytest.raises(NumberParseError) as exc:
        parse_number(text, locale)
    assert exc.value.code == code


NUMBER = FieldDef(name="cylinders", description="Cylinders.", type="number")
INTEGER = FieldDef(name="cylinders", description="Cylinders.", type="integer")


def test_parse_value_numbers():
    assert parse_value(NUMBER, "2.5", None, "auto").value == 2.5
    assert parse_value(INTEGER, "4", "cylinders", "auto").value == 4  # no field unit: ignored
    assert parse_value(INTEGER, "4.0", None, "auto").value == 4
    parsed = parse_value(INTEGER, "2.5", None, "auto")
    assert parsed.value is None and parsed.warning.code == "not_a_number"
    assert parsed.warning.raw == "2.5" and parsed.unit_resolved is None
    parsed = parse_value(NUMBER, "1,140", None, "auto")
    assert parsed.value is None and parsed.warning.code == "ambiguous_number"
    assert parse_value(NUMBER, "1,140", None, "en").value == 1140


def unit_field(unit, type="number"):
    return FieldDef(name="quantity", description="A quantity.", type=type, unit=unit)


@pytest.mark.parametrize(
    "unit, raw_value, raw_unit, value, status, derivation",
    [
        ("kg", "516.4", "kg", 516.4, "found", None),
        ("kg", "516.4", "kilograms", 516.4, "found", None),  # same unit, other spelling
        ("kg", "516.40", "kg", 516.4, "found", None),  # stated values aren't rounded
        ("kg", "1,140", "lb.", 517.1, "derived", "1,140 lb. = 517.1 kg"),
        ("kg", "5", "lb", 2.268, "derived", "5 lb = 2.268 kg"),  # within 1% of the stated 2.27
        ("kg", "1,140 lb.", None, 517.1, "derived", "1,140 lb. = 517.1 kg"),  # unit in value
        ("kg", "18.92lb", None, 8.582, "derived", "18.92 lb = 8.582 kg"),
        ("kg", "18.92*", "lb", 8.582, "derived", "18.92* lb = 8.582 kg"),
        ("L", "9", "gal.", 34.07, "derived", "9 gal. = 34.07 L"),  # at least 4 figures
        ("L/h", ".99", "gal. per hour", 3.748, "derived", ".99 gal. per hour = 3.748 L/h"),
        ("degC", "170", "°F", 76.67, "derived", "170 °F = 76.67 degC"),
        ("degC", "170", "deg F", 76.67, "derived", "170 deg F = 76.67 degC"),
        ("V", "120", "VAC", 120, "found", None),
        ("A", "52", "amps", 52, "found", None),
        ("kW", "10", "KW", 10, "found", None),
        ("mm", "6.5", '"', 165.1, "derived", '6.5 " = 165.1 mm'),
        ("kPa", "25", "psig", 172.4, "derived", "25 psig = 172.4 kPa"),
        ("W", "10", "kW", 10000, "derived", "10 kW = 10000 W"),
        ("bph", "400", "bottles/min", 24000, "derived", "400 bottles/min = 24000 bph"),
    ],
)
def test_unit_conversion(unit, raw_value, raw_unit, value, status, derivation):
    units = ["bottle = [count]", "bottles = bottle", "bph = bottle / hour"]
    parsed = parse_value(unit_field(unit), raw_value, raw_unit, "en", units)
    assert parsed.warning is None
    assert (parsed.value, parsed.status, parsed.derivation) == (value, status, derivation)
    assert parsed.unit == unit and parsed.unit_resolved is True


@pytest.mark.parametrize(
    "unit, raw_value, raw_unit, code",
    [
        ("kg", "516.4", None, "unit_unresolved"),  # the unit is never assumed
        ("kg", "516.4", "  ", "unit_unresolved"),
        ("kg", "516.4", "furlongs per fortnight squared", "unit_unresolved"),
        ("A", "52", "amps per", "unit_unresolved"),
        ("kg", "52", "A", "unit_unresolved"),  # wrong dimension
        ("L", "2", "cu in.", None),  # an alias, not a misread mass × length
        ("kPa", "25-60", "psi", "number_not_scalar"),
        ("kPa", "25-60 psi", None, "number_not_scalar"),
        ("degC", "-25 to 0 °F", None, "number_not_scalar"),
        ("degC", "5° C to 32° C", None, "number_not_scalar"),
        ("degC", "5 °C – 32 °C", None, "number_not_scalar"),
        ("kPa", "15 ±3 psi", None, "number_not_scalar"),
        ("kg", "1,140", "lb", "ambiguous_number"),
        ("K/min", "5", "°C", "unit_unresolved"),
        ("kg", "about 5", "kg", "not_a_number"),
    ],
)
def test_unit_conversion_failures(unit, raw_value, raw_unit, code):
    parsed = parse_value(unit_field(unit), raw_value, raw_unit, "auto")
    if code is None:
        assert parsed.warning is None and parsed.unit_resolved is True
        return
    assert parsed.value is None and parsed.unit_resolved is False
    assert parsed.warning.code == code and parsed.warning.raw == raw_value


def test_integer_unit_fields():
    field = unit_field("kW", type="integer")
    assert parse_value(field, "10", "kW", "en").value == 10
    assert parse_value(field, "13.4", "hp", "en").value == 10  # converted, then rounded
    parsed = parse_value(field, "9.5", "kW", "en")
    assert parsed.value is None and parsed.warning.code == "not_a_number"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("lb.", "lb"),
        ("gal. per hour", "gal/hour"),
        ("° F", "°F"),
        ("V  AC", "V"),
        ("cu. in.", "inch**3"),
    ],
)
def test_clean_unit(text, expected):
    assert clean_unit(text) == expected
