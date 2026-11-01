"""Evidence matching, number parsing and unit conversion."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from citefield.config import FieldDef, NumberLocale, unit_registry
from citefield.output import OutputWarning, ScalarValue, ValueStatus

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"'})
# Hyphens and dashes are dropped entirely: PDFs break words at line ends ("pro-\ncedure"),
# and the model may or may not keep the hyphen of a code like "MEP-803A".
_DASHES_AND_SPACE = re.compile(r"[\s\-‐‑‒–—―­]+")
# Matching blocks shorter than this aren't worth aligning a window on.
_MIN_ANCHOR = 3

# Field types whose raw values are parsed in code; the others keep value null for now.
PARSED_TYPES = frozenset({"string", "enum", "number", "integer"})

_MINUS = str.maketrans({"−": "-"})
_NUMBER_TEXT = r"[+-]?[\d.,]*\d[\d.,]*"
# Two numbers joined by a dash or "to", with up to two unit words after the first:
# "25-60", "-25 to 0", "170 – 200", "5° C to 32° C" (ADR-0020).
_RANGE = re.compile(
    rf"{_NUMBER_TEXT}\s*(?:[^\d\s\-–—]+\s*){{0,2}}(?:[-–—]|to)\s*{_NUMBER_TEXT}", re.IGNORECASE
)
_TOLERANCE = re.compile(r"±|\+/-|\+-")
# Integer part grouped in thousands by `sep` ("1,140,000"), or plain digits.
_GROUPED = r"(?:[1-9]\d{{0,2}}(?:{sep}\d{{3}})+|\d+)"
# The decimal mark of each locale; the other separator groups thousands.
_DECIMAL_MARK = {"en": ".", "it": ",", "de": ","}
# A number followed by its unit, as in a raw value "1,140 lb." with no raw unit.
_NUMBER_AND_UNIT = re.compile(r"(.*?\d\*?)\s*([^\d\s.,+\-−±*].*)")

# Unit spellings from manuals that pint doesn't know or misreads, after `clean_unit`'s
# other steps. The dimension check catches misreadings this table misses ("cu in" parses
# as centi-atomic-mass-unit × inch), so it only has to cover common spellings.
_UNIT_ALIASES = {
    **dict.fromkeys(["VAC", "VDC", "V AC", "V DC", "Vac", "Vdc"], "V"),
    **dict.fromkeys(["amp", "amps", "Amp", "Amps"], "A"),  # pint: "amps" is attometer/second
    "KW": "kW",
    "RPM": "rpm",
    "psig": "psi",
    **dict.fromkeys(['"', "″"], "inch"),
    **dict.fromkeys(["'", "′"], "foot"),
    "cu in": "inch**3",
    "cu ft": "foot**3",
    "fl oz": "fluid_ounce",
    "Nm": "N*m",
    **dict.fromkeys(["ft lb", "ft-lb", "lb ft", "lb-ft", "ft·lb"], "ft*lbf"),
}


class NumberParseError(ValueError):
    """A raw value that isn't one number; `code` is the warning code to report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_number(text: str, locale: NumberLocale = "auto") -> int | float:
    """The number written in `text`, read with `locale`'s decimal mark.

    With `auto`, a form that reads as a different number with `.` or `,` as the decimal mark
    ("24.000", "1,140") is ambiguous (ADR-0022). Ranges and tolerances aren't one number
    (ADR-0020). A whole number is returned as an int, so 34.0 and 34 are the same value.
    Trailing footnote marks are ignored: "18.92*" is 18.92.
    """
    body = text.strip().rstrip("*").rstrip().translate(_MINUS)
    if _is_range_or_tolerance(body):
        raise NumberParseError("number_not_scalar", f"{text!r} is a range or tolerance")
    marks = (".", ",") if locale == "auto" else (_DECIMAL_MARK[locale],)
    readings = {n for mark in marks if (n := _read_number(body, mark)) is not None}
    if not readings:
        raise NumberParseError("not_a_number", f"{text!r} is not a number")
    if len(readings) > 1:
        raise NumberParseError(
            "ambiguous_number", f"{text!r} reads as {' or '.join(map(str, sorted(readings)))}"
        )
    return readings.pop()


def _read_number(body: str, mark: str) -> int | float | None:
    """`body` read with `mark` as the decimal mark, or None if it isn't valid that way."""
    sep = "," if mark == "." else "."
    pattern = rf"([+-]?)({_GROUPED.format(sep=re.escape(sep))})?(?:{re.escape(mark)}(\d+))?"
    match = re.fullmatch(pattern, body)
    if match is None or not (match[2] or match[3]):
        return None
    number = float(f"{match[1]}{(match[2] or '0').replace(sep, '')}.{match[3] or '0'}")
    return int(number) if number.is_integer() else number


@dataclass
class ParsedValue:
    """The typed value of a raw value, and how it was obtained."""

    value: ScalarValue = None
    status: ValueStatus = "found"
    unit: str | None = None
    derivation: str | None = None
    unit_resolved: bool | None = None
    warning: OutputWarning | None = None


def parse_value(
    field: FieldDef,
    raw_value: str,
    raw_unit: str | None,
    locale: NumberLocale,
    units: Sequence[str] = (),
) -> ParsedValue:
    """The typed value of `raw_value` for `field`, with a warning when it can't be parsed.

    A number for a field with a unit is converted to that unit; `units` are the config's
    extra unit definitions.
    """
    if field.type == "string":
        return ParsedValue(raw_value)
    if field.type == "enum":
        value = {v.casefold(): v for v in field.values or []}.get(raw_value.casefold())
        if value is None:
            return ParsedValue(warning=_warning("enum_value_unknown", field, raw_value))
        return ParsedValue(value)
    if field.type not in ("number", "integer"):
        return ParsedValue()  # not in PARSED_TYPES yet

    resolved = False if field.unit is not None else None
    number_text, unit_text = raw_value, raw_unit
    if field.unit is not None and not raw_unit and not _is_range_or_tolerance(raw_value):
        if match := _NUMBER_AND_UNIT.fullmatch(raw_value.strip()):
            number_text, unit_text = match[1], match[2]
    try:
        number = parse_number(number_text, locale)
    except NumberParseError as exc:
        return ParsedValue(
            unit_resolved=resolved, warning=_warning(exc.code, field, raw_value, str(exc))
        )
    if field.unit is None:
        return _whole_if_integer(field, ParsedValue(number), raw_value)
    return _convert(field, number, number_text, unit_text, raw_value, units)


def clean_unit(text: str) -> str:
    """A unit as written in a manual, in a form pint parses: "gal. per hour" is "gal/hour"."""
    unit = re.sub(r"(?<=[A-Za-z])\.", "", text)  # "lb.", "cu. in."
    unit = re.sub(r"\s+per\s+", "/", unit.strip())
    unit = re.sub(r"(°|\bdeg)\s+(?=[CFK]\b)", "°", unit)  # "° F", "deg F"
    unit = " ".join(unit.split())
    return _UNIT_ALIASES.get(unit, unit)


def _convert(
    field: FieldDef,
    number: int | float,
    number_text: str,
    unit_text: str | None,
    raw_value: str,
    units: Sequence[str],
) -> ParsedValue:
    """`number` in `unit_text`, converted to the field's unit (ADR-0021)."""
    import pint

    assert field.unit is not None
    if not unit_text or not unit_text.strip():
        message = f"no unit stated; the field's unit is {field.unit}"
        return _unresolved(field, raw_value, message)
    registry = unit_registry(tuple(units))
    try:
        source = registry.parse_units(clean_unit(unit_text))
    except Exception:  # pint raises several unrelated types for text it can't parse
        return _unresolved(field, raw_value, f"unknown unit {unit_text!r}")
    target = registry.parse_units(field.unit)
    if source == target:
        parsed = ParsedValue(number, unit=field.unit, unit_resolved=True)
        return _whole_if_integer(field, parsed, raw_value)
    try:
        converted = registry.Quantity(number, source).to(target).magnitude
    except pint.PintError:  # mostly DimensionalityError: a unit of another quantity
        return _unresolved(field, raw_value, f"{unit_text!r} doesn't convert to {field.unit}")
    value = _round_significant(converted, max(_significant_figures(number_text) + 1, 4))
    if field.type == "integer":
        value = round(value)
    return ParsedValue(
        value,
        status="derived",
        unit=field.unit,
        derivation=f"{number_text.strip()} {unit_text.strip()} = {value} {field.unit}",
        unit_resolved=True,
    )


def _whole_if_integer(field: FieldDef, parsed: ParsedValue, raw_value: str) -> ParsedValue:
    """`parsed`, or a `not_a_number` warning when an integer field states a fraction."""
    if field.type != "integer" or isinstance(parsed.value, int):
        return parsed
    warning = _warning("not_a_number", field, raw_value, f"{raw_value!r} is not an integer")
    return ParsedValue(unit_resolved=parsed.unit_resolved, warning=warning)


def _unresolved(field: FieldDef, raw_value: str, message: str) -> ParsedValue:
    return ParsedValue(
        unit_resolved=False, warning=_warning("unit_unresolved", field, raw_value, message)
    )


def _warning(code: str, field: FieldDef, raw: str, message: str | None = None) -> OutputWarning:
    return OutputWarning(code=code, field=field.name, raw=raw, message=message)


def _is_range_or_tolerance(text: str) -> bool:
    body = text.strip().translate(_MINUS)
    return bool(_TOLERANCE.search(body) or _RANGE.match(body))


def _significant_figures(number_text: str) -> int:
    """Written digits, without leading zeros: "1,140" has 4, ".99" has 2, "9" has 1."""
    return max(len(re.sub(r"\D", "", number_text).lstrip("0")), 1)


def _round_significant(x: float, figures: int) -> int | float:
    rounded = float(f"{x:.{figures}g}")
    return int(rounded) if rounded.is_integer() else rounded


def normalize_for_match(text: str) -> str:
    """Fold ligatures, case, quotes, dashes, hyphenation and whitespace.

    Whitespace is removed rather than collapsed, because some PDFs lose the spaces between
    words ("Permissionisgranted") and others add spaces inside them.
    """
    text = unicodedata.normalize("NFKC", text).casefold().translate(_QUOTES)
    return _DASHES_AND_SPACE.sub("", text)


def partial_ratio(needle: str, haystack: str) -> float:
    """Best similarity (0-1) between `needle` and any same-length window of `haystack`."""
    if not needle:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle):
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    best = 0.0
    blocks = SequenceMatcher(None, haystack, needle, autojunk=False).get_matching_blocks()
    starts = {max(0, block.a - block.b) for block in blocks if block.size >= _MIN_ANCHOR}
    for start in starts:
        window = haystack[start : start + len(needle)]
        best = max(best, SequenceMatcher(None, needle, window, autojunk=False).ratio())
        if best == 1.0:
            break
    return best


def evidence_match(
    quote: str,
    page: int,
    pages: Mapping[int, str],
    *,
    page_window: int = 1,
) -> float:
    """Best match (0-1) of `quote` on `page` ± `page_window`; 0 when nothing matched.

    `pages` maps 1-based page numbers to their text.
    """
    needle = normalize_for_match(quote)
    best = 0.0
    for number in range(page - page_window, page + page_window + 1):
        text = pages.get(number)
        if text is None:
            continue
        best = max(best, partial_ratio(needle, normalize_for_match(text)))
        if best == 1.0:
            break
    return round(best, 4)
