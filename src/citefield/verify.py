"""Evidence matching, number parsing and unit conversion."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher

from citefield.config import FieldDef, NumberLocale
from citefield.output import OutputWarning, ScalarValue

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
# Two numbers joined by a dash or "to": "25-60", "-25 to 0", "170 – 200" (ADR-0020).
_RANGE = re.compile(rf"{_NUMBER_TEXT}\s*(?:[-–—]|to)\s*{_NUMBER_TEXT}", re.IGNORECASE)
_TOLERANCE = re.compile(r"±|\+/-|\+-")
# Integer part grouped in thousands by `sep` ("1,140,000"), or plain digits.
_GROUPED = r"(?:[1-9]\d{{0,2}}(?:{sep}\d{{3}})+|\d+)"
# The decimal mark of each locale; the other separator groups thousands.
_DECIMAL_MARK = {"en": ".", "it": ",", "de": ","}


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
    if _TOLERANCE.search(body) or _RANGE.fullmatch(body):
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


def parse_value(
    field: FieldDef, raw_value: str, locale: NumberLocale
) -> tuple[ScalarValue, OutputWarning | None]:
    """The typed value of `raw_value` for `field`, and a warning when it can't be parsed."""
    if field.type == "string":
        return raw_value, None
    if field.type == "enum":
        value = {v.casefold(): v for v in field.values or []}.get(raw_value.casefold())
        if value is None:
            return None, OutputWarning(code="enum_value_unknown", field=field.name, raw=raw_value)
        return value, None
    if field.type in ("number", "integer"):
        if field.unit is not None:
            return None, None  # unit conversion isn't built yet
        try:
            number = parse_number(raw_value, locale)
        except NumberParseError as exc:
            return None, OutputWarning(
                code=exc.code, field=field.name, raw=raw_value, message=str(exc)
            )
        if field.type == "integer" and not isinstance(number, int):
            return None, OutputWarning(
                code="not_a_number",
                field=field.name,
                raw=raw_value,
                message=f"{raw_value!r} is not an integer",
            )
        return number, None
    return None, None  # not in PARSED_TYPES yet


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
