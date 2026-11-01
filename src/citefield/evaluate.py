"""Evaluation against the golden set built from reviewed values."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, computed_field

from citefield.config import Config, FieldDef
from citefield.output import (
    ExtractionResult,
    FieldResult,
    NumberKey,
    ScalarValue,
    TokenUsage,
    Value,
    normalize_text,
    number_key,
    numbers_match,
    written_precision,
)
from citefield.verify import parse_value

if TYPE_CHECKING:
    from citefield.extraction import Extractor

# Signal ranges for accuracy by signal: [low, high), the last one closed.
SIGNAL_RANGES = ((0.0, 0.5), (0.5, 0.85), (0.85, 1.0))
SCORED_SIGNALS = ("evidence_match", "model_confidence")

# Match key folding (ADR-0017): no whitespace or trademark signs, straight quotes.
_DROP = re.compile(r"[\s\u00ae\u2122\u00a9]+")
_QUOTES = str.maketrans(
    {"\u201c": '"', "\u201d": '"', "\u2033": '"', "\u2018": "'", "\u2019": "'", "\u2032": "'"}
)


class FieldScore(BaseModel):
    """Counts for one field, summed over the documents where it was reviewed."""

    name: str
    documents: int = 0  # documents where the field was reviewed
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    unjudged: int = 0  # false positives the reviewer never saw: candidates for the golden set
    context_mismatches: int = 0  # true positives whose context differs from the golden one
    confirmed_absent: int = 0
    not_found_correct: int = 0  # of the confirmed-absent fields, predicted with no values
    errors: int = 0  # predicted field status "error"

    @computed_field
    @property
    def precision(self) -> float | None:
        return _ratio(self.true_positives, self.true_positives + self.false_positives)

    @computed_field
    @property
    def recall(self) -> float | None:
        return _ratio(self.true_positives, self.true_positives + self.false_negatives)

    @computed_field
    @property
    def not_found_accuracy(self) -> float | None:
        return _ratio(self.not_found_correct, self.confirmed_absent)

    def add(self, other: FieldScore) -> None:
        for name in type(self).model_fields:
            if name != "name":
                setattr(self, name, getattr(self, name) + getattr(other, name))


class SignalBucket(BaseModel):
    """Share of predicted values in a signal range that the golden set holds as correct."""

    signal: str
    low: float | None  # None for values where the signal is null
    high: float | None
    values: int = 0
    correct: int = 0

    @computed_field
    @property
    def precision(self) -> float | None:
        return _ratio(self.correct, self.values)


class UnjudgedValue(BaseModel):
    document_id: str
    field: str
    value_id: str
    raw_value: str
    context: str | None = None


class PredictionInfo(BaseModel):
    document_id: str
    run_id: str
    model: str
    prompt_version: str
    settings_hash: str
    tokens: TokenUsage


class EvalReport(BaseModel):
    golden: str | None = None  # folder of the golden outputs
    predictions: list[PredictionInfo]
    fields: list[FieldScore]
    total: FieldScore
    signals: list[SignalBucket]
    unjudged: list[UnjudgedValue] = []
    warnings: list[str] = []

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


class EvalError(Exception):
    """The golden set or the predictions can't be loaded or paired."""


def evaluate(
    outputs: str | Path,
    config: Config,
    *,
    predictions: str | Path | None = None,
    documents: str | Path | None = None,
    extractor: Extractor | None = None,
) -> EvalReport:
    """Score extraction against the reviewed outputs in `outputs`.

    By default each golden document is extracted again with `config` (or `extractor`). The
    document is found at its recorded path, or by SHA-256 in `documents`. With `predictions`,
    the outputs already in that folder are scored instead, and nothing is extracted.
    """
    golden = _load_outputs(outputs)
    if not golden:
        raise EvalError(f"{outputs}: no output files")
    if predictions is not None:
        by_id = {p.document.document_id: p for p in _load_outputs(predictions)}
        pairs = []
        for g in golden:
            p = by_id.get(g.document.document_id)
            if p is None:
                raise EvalError(f"{predictions}: no output for {g.document.document_id}")
            if p.document.sha256 != g.document.sha256:
                raise EvalError(
                    f"{predictions}: {g.document.document_id} was extracted from a different "
                    "file than the golden output"
                )
            pairs.append((g, p))
    else:
        if extractor is None:
            from citefield.extraction import Extractor

            extractor = Extractor(config)
        by_hash = _hash_files(Path(documents)) if documents is not None else {}
        pairs = []
        for g in golden:
            path = _locate(g, by_hash, documents)
            pairs.append((g, extractor.extract(path, document_id=g.document.document_id)))
    return score(pairs, config).model_copy(update={"golden": str(outputs)})


def score(pairs: Iterable[tuple[ExtractionResult, ExtractionResult]], config: Config) -> EvalReport:
    """Score each prediction against its golden (reviewed) output.

    Only fields in `config` that were reviewed count: a field is reviewed when it has no
    pending values and has at least one reviewed value or is confirmed absent.
    """
    pairs = sorted(pairs, key=lambda pair: pair[0].run.started_at)
    scores = {f.name: FieldScore(name=f.name) for f in config.fields}
    buckets = {
        signal: [SignalBucket(signal=signal, low=lo, high=hi) for lo, hi in SIGNAL_RANGES]
        + [SignalBucket(signal=signal, low=None, high=None)]
        for signal in SCORED_SIGNALS
    }
    unjudged: list[UnjudgedValue] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()  # (document_id, value_id) of positives already counted

    for golden, predicted in pairs:
        doc_id = golden.document.document_id
        if golden.run.schema_hash != config.schema_hash():
            warnings.append(f"{doc_id}: golden output was made with a different field config")
        golden_names = {f.name for f in golden.fields}
        for name in sorted(golden_names - scores.keys()):
            warnings.append(f"{doc_id}: field {name!r} is not in the config; skipped")
        for fdef in config.fields:
            if fdef.name not in golden_names:
                warnings.append(f"{doc_id}: field {fdef.name!r} is not in the golden output")
                continue
            g_field = golden.field(fdef.name)
            if not _reviewed(g_field):
                continue
            try:
                p_field = predicted.field(fdef.name)
            except KeyError:
                p_field = FieldResult(name=fdef.name, status="error", error="not extracted")
            truth = _truth(doc_id, fdef, g_field, seen, config)
            scores[fdef.name].add(_score_field(doc_id, fdef, truth, p_field, buckets, unjudged))

    total = FieldScore(name="all")
    for s in scores.values():
        total.add(s)
    return EvalReport(
        predictions=[
            PredictionInfo(
                document_id=p.document.document_id,
                run_id=p.run.run_id,
                model=p.run.model,
                prompt_version=p.run.prompt_version,
                settings_hash=p.run.settings_hash,
                tokens=p.run.tokens,
            )
            for _, p in pairs
        ],
        fields=list(scores.values()),
        total=total,
        signals=[b for signal in SCORED_SIGNALS for b in buckets[signal]],
        unjudged=unjudged,
        warnings=warnings,
    )


@dataclass
class _Positive:
    """A reviewed value that counts as correct, matched by any of its forms."""

    contexts: set[str] = field(default_factory=set)


@dataclass
class _Truth:
    """The reviewed values of one field in one golden output, by match key."""

    positives: list[_Positive] = field(default_factory=list)
    by_key: dict[Key, _Positive] = field(default_factory=dict)
    negatives: set[Key] = field(default_factory=set)
    excluded: set[Key] = field(default_factory=set)
    confirmed_absent: bool = False


def _reviewed(f: FieldResult) -> bool:
    statuses = [v.review.status for v in f.values]
    if "pending" in statuses:
        return False
    return bool(statuses) or f.review.status == "confirmed_absent"


def _truth(
    doc_id: str,
    fdef: FieldDef,
    g_field: FieldResult,
    seen: set[tuple[str, str]],
    config: Config,
) -> _Truth:
    truth = _Truth(confirmed_absent=g_field.review.status == "confirmed_absent")

    def form_key(form: ScalarValue, what: str) -> Key:
        key = _form_key(fdef, form, config)
        if key is None:
            raise EvalError(
                f"{doc_id}: field {fdef.name!r}: {what} {form!r} doesn't parse as a number "
                f"in {fdef.unit or 'no unit'}"
            )
        return key

    def main_key(v: Value) -> Key:
        if v.review.status == "edited":
            return form_key(v.review.reviewed_value, "reviewed value")
        return _value_key(fdef, v)

    def equivalent_key(main: Key, form: str) -> Key:
        """A number's equivalents are parsed; those of text, such as a range, stay text."""
        if isinstance(main, NumberKey):
            return form_key(form, "equivalent")
        return match_key(fdef, form)

    def golden_keys(v: Value) -> set[Key]:
        """The reviewed value's match key, and those of its equivalent forms."""
        main = main_key(v)
        return {main} | {equivalent_key(main, e) for e in v.review.equivalents}

    # Values in the prompt's examples measure copying, not extraction.
    truth.excluded = {
        k if (k := _form_key(fdef, e, config)) is not None else match_key(fdef, e)
        for e in fdef.examples
    }
    equivalents: list[tuple[_Positive, Key, Value]] = []
    for v in g_field.values:
        review = v.review
        if fdef.type in ("number", "integer") and isinstance(v.value, str):
            raise EvalError(
                f"{doc_id}: field {fdef.name!r}: value {v.raw_value!r} is text in a number "
                "field; apply the review decisions again with the config"
            )
        if review.status in ("accepted", "added", "edited"):
            if review.carried_from and (doc_id, v.value_id) in seen:
                truth.excluded |= golden_keys(v)  # carried over: counted once already
                continue
            seen.add((doc_id, v.value_id))
            # Values with the same main form are one positive, with all their contexts.
            key = main_key(v)
            positive = _get(truth.by_key, key)
            if positive is None:
                positive = truth.by_key[key] = _Positive()
                truth.positives.append(positive)
            positive.contexts.add(_context(v))
            equivalents.append((positive, key, v))
        if review.status in ("edited", "rejected"):
            truth.negatives.add(_value_key(fdef, v))
    for positive, main, v in equivalents:
        for e in v.review.equivalents:
            key = equivalent_key(main, e)
            existing = _get(truth.by_key, key)
            if existing is not None and existing is not positive:
                raise EvalError(f"{doc_id}: field {fdef.name!r}: {e!r} matches two golden values")
            truth.by_key.setdefault(key, positive)
            if _has(truth.negatives, key):
                raise EvalError(f"{doc_id}: field {fdef.name!r}: {e!r} is also a negative")
    truth.negatives = {n for n in truth.negatives if not _has(truth.by_key, n)}
    return truth


def _score_field(
    doc_id: str,
    fdef: FieldDef,
    truth: _Truth,
    p_field: FieldResult,
    buckets: dict[str, list[SignalBucket]],
    unjudged: list[UnjudgedValue],
) -> FieldScore:
    s = FieldScore(name=fdef.name, documents=1)
    if p_field.status == "error":
        s.errors = 1
    # Predictions grouped by the positive they match, or by key when they match none, so
    # several forms of one golden value count once.
    matched: dict[int, tuple[_Positive, list[Value]]] = {}
    unmatched: dict[Key, list[Value]] = {}
    for v in p_field.values:
        key = _value_key(fdef, v)
        if _has(truth.excluded, key):
            continue
        positive = _get(truth.by_key, key)
        if positive is not None:
            matched.setdefault(id(positive), (positive, []))[1].append(v)
        else:
            unmatched.setdefault(_find(unmatched, key, default=key), []).append(v)

    for positive, values in matched.values():
        s.true_positives += 1
        if {_context(v) for v in values} != positive.contexts:
            s.context_mismatches += 1
        _count_signals(values, True, buckets)
    for key, values in unmatched.items():
        s.false_positives += 1
        if not _has(truth.negatives, key):
            s.unjudged += 1
            unjudged.extend(
                UnjudgedValue(
                    document_id=doc_id,
                    field=fdef.name,
                    value_id=v.value_id,
                    raw_value=v.raw_value,
                    context=v.context,
                )
                for v in values
            )
        _count_signals(values, False, buckets)
    s.false_negatives = len(truth.positives) - len(matched)
    if truth.confirmed_absent:
        s.confirmed_absent = 1
        s.not_found_correct = int(p_field.status == "not_found")
    return s


def _count_signals(
    values: list[Value], correct: bool, buckets: dict[str, list[SignalBucket]]
) -> None:
    for v in values:
        for signal, signal_buckets in buckets.items():
            bucket = _bucket(signal_buckets, getattr(v.signals, signal))
            bucket.values += 1
            bucket.correct += correct


def _bucket(buckets: list[SignalBucket], x: float | None) -> SignalBucket:
    if x is None:
        return buckets[-1]
    for b in buckets[:-2]:
        if x < b.high:
            return b
    return buckets[-2]


def match_key(
    fdef: FieldDef, value: ScalarValue, raw_value: str | None = None, raw_unit: str | None = None
) -> ScalarValue:
    """What the evaluation compares: looser than `value_key`, which makes `value_id`.

    Text is casefolded, without whitespace or trademark signs, with straight quotes and no
    trailing footnote asterisks. A string field's raw unit is appended unless the text already
    ends with it, so "18.92lb" matches "18.92" with unit "lb".
    """
    key = value if value is not None else raw_value
    if not isinstance(key, str):
        return key
    text = _fold(key)
    if fdef.type == "string" and raw_unit and not text.endswith(unit := _fold(raw_unit)):
        text += unit
    return text


def _fold(text: str) -> str:
    return _DROP.sub("", text.casefold().translate(_QUOTES)).rstrip("*")


Key = ScalarValue | NumberKey


def _value_key(fdef: FieldDef, v: Value) -> Key:
    """What a value is compared by: its `NumberKey` for a number, else its match key."""
    return number_key(v) or match_key(fdef, v.value, v.raw_value, v.raw_unit)


def _form_key(fdef: FieldDef, form: ScalarValue, config: Config) -> Key | None:
    """Match key of a form written by hand: an example, an equivalent or a reviewed value.

    For a number field, a text form is parsed and converted like a raw value with its unit
    ("516.4 kg"), and None when it doesn't parse (ADR-0021).
    """
    if fdef.type not in ("number", "integer"):
        return match_key(fdef, form)
    if not isinstance(form, str):
        return NumberKey(form) if form is not None else None  # a number, without its text
    locale = config.settings.extraction.number_locale
    parsed = parse_value(fdef, form, None, locale, config.units)
    if parsed.value is None:
        return None
    if parsed.status == "derived":
        return NumberKey(parsed.value, derived=True)
    return NumberKey(parsed.value, written_precision(form, parsed.value))


_MISSING = object()


def _find(keys: Collection[Key], key: Key, default: object = _MISSING) -> object:
    """The key in `keys` that `key` matches: itself, or for a number the closest one that
    `numbers_match` (ADR-0021); `default` when none does."""
    if not isinstance(key, NumberKey):
        return key if key in keys else default
    close = [k for k in keys if isinstance(k, NumberKey) and numbers_match(k, key)]
    return min(close, key=lambda k: abs(k.value - key.value)) if close else default


def _get(mapping: Mapping[Key, _Positive], key: Key) -> _Positive | None:
    found = _find(mapping, key)
    return None if found is _MISSING else mapping[found]


def _has(keys: Collection[Key], key: Key) -> bool:
    return _find(keys, key) is not _MISSING


def _context(v: Value) -> str:
    return normalize_text(v.context) if v.context else ""


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _load_outputs(folder: str | Path) -> list[ExtractionResult]:
    folder = Path(folder)
    if not folder.is_dir():
        raise EvalError(f"{folder}: not a directory")
    return [ExtractionResult.from_json(p) for p in sorted(folder.glob("*.json"))]


def _hash_files(folder: Path) -> dict[str, Path]:
    if not folder.is_dir():
        raise EvalError(f"{folder}: not a directory")
    return {_sha256(path): path for path in sorted(folder.iterdir()) if path.is_file()}


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _locate(
    golden: ExtractionResult, by_hash: dict[str, Path], documents: str | Path | None
) -> Path:
    doc = golden.document
    if documents is not None:
        path = by_hash.get(doc.sha256)
        if path is None:
            raise EvalError(f"{documents}: no file matches {doc.document_id} (sha256)")
        return path
    path = Path(doc.path)
    if not path.is_file():
        raise EvalError(f"{doc.path}: not found; pass the folder that holds it as documents")
    if _sha256(path) != doc.sha256:
        raise EvalError(f"{doc.path}: not the file the golden output was made from")
    return path
