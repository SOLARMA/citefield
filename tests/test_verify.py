import pytest

from citefield.config import FieldDef
from citefield.verify import (
    NumberParseError,
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


def test_parse_value_numbers():
    number = FieldDef(name="cylinders", description="Cylinders.", type="number")
    integer = FieldDef(name="cylinders", description="Cylinders.", type="integer")
    assert parse_value(number, "2.5", "auto") == (2.5, None)
    assert parse_value(integer, "4", "auto") == (4, None)
    assert parse_value(integer, "4.0", "auto") == (4, None)
    value, warning = parse_value(integer, "2.5", "auto")
    assert value is None and warning.code == "not_a_number" and warning.raw == "2.5"
    value, warning = parse_value(number, "1,140", "auto")
    assert value is None and warning.code == "ambiguous_number"
    assert parse_value(number, "1,140", "en") == (1140, None)
