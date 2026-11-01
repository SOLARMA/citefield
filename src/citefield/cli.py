"""Command-line interface: a thin layer over the library."""

# Commands and flags that are parsed but not built yet stay out of --help until their slice
# lands: `reextract`, `proposals`, `extract --workers` and `eval --parser`.

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import yaml
from pydantic import ValidationError

from citefield import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="citefield",
        description="Extract metadata from technical manuals, with a citation for every value.",
    )
    parser.add_argument("--version", action="version", version=f"citefield {__version__}")
    # A subcommand added without help= is left out of the command list; the metavar keeps
    # it out of the usage line too.
    sub = parser.add_subparsers(
        dest="command", required=True, metavar="{extract,inspect,validate-config,eval}"
    )

    extract = sub.add_parser("extract", help="extract fields; one JSON per document")
    extract.add_argument("path", help="file or directory")
    extract.add_argument("-c", "--config", required=True)
    extract.add_argument("-o", "--output", required=True)
    extract.add_argument("--dry-run", action="store_true", help="print the plan, no LLM calls")
    extract.add_argument("--workers", type=int, default=1, help=argparse.SUPPRESS)

    reextract = sub.add_parser("reextract")
    reextract.add_argument("path")
    reextract.add_argument("--previous", required=True)

    inspect = sub.add_parser("inspect", help="show the parsed text and low-text units, no LLM")
    inspect.add_argument("path")
    inspect.add_argument("--parser")

    validate = sub.add_parser("validate-config", help="check the schema and units")
    validate.add_argument("config")

    proposals = sub.add_parser("proposals")
    proposals_sub = proposals.add_subparsers(dest="proposals_command", required=True)
    report = proposals_sub.add_parser("report", help="count recurring proposals across outputs")
    report.add_argument("outputs")

    evaluate = sub.add_parser("eval", help="evaluate against the golden set")
    evaluate.add_argument("outputs", help="folder of reviewed outputs (the golden set)")
    evaluate.add_argument("-c", "--config", required=True)
    evaluate.add_argument("--parser", help=argparse.SUPPRESS)
    evaluate.add_argument(
        "--predictions", help="score the outputs in this folder instead of extracting again"
    )
    evaluate.add_argument("--documents", help="folder holding the manuals, matched by SHA-256")
    evaluate.add_argument("--json", help="also write the report to this JSON file")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {
        "validate-config": _validate_config,
        "inspect": _inspect,
        "extract": _extract,
        "eval": _eval,
    }
    command = commands.get(args.command)
    if command is None:
        print(f"citefield {args.command}: not implemented yet", file=sys.stderr)
        return 2
    try:
        return command(args)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


class _CliError(Exception):
    """A problem to report on stderr, exiting with status 1."""


def _load_config(path: str):
    from citefield.config import load_config

    try:
        return load_config(path)
    except OSError as exc:
        raise _CliError(f"can't read config: {exc}") from exc
    except yaml.YAMLError as exc:
        raise _CliError(f"{path}: invalid YAML: {exc}") from exc
    except ValidationError as exc:
        raise _CliError(f"{path}: invalid config\n{exc}") from exc


def _validate_config(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    print(f"{args.config}: OK, {len(config.fields)} fields")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from citefield.parsers import ParseError, get_parser, parse_document

    try:
        document = parse_document(args.path, get_parser(args.parser or "pdfplumber"))
    except (ValueError, ParseError) as exc:
        raise _CliError(str(exc)) from exc
    for unit in document.units:
        label = f"page {unit.locator.page}" if unit.locator.page else unit.id
        flag = " · low text" if unit.low_text else ""
        print(f"[[{label}{flag}]]")
        print(unit.text.rstrip())
        print()
    low = [u.locator.page or u.id for u in document.low_text_units]
    print(
        f"--- {Path(args.path).name}: {len(document.units)} units, parser "
        f"{document.parser.name} {document.parser.version}, {len(low)} low-text: {low}"
    )
    return 0


def _extract(args: argparse.Namespace) -> int:
    from citefield.extraction import ExtractionError, Extractor
    from citefield.parsers import ParseError
    from citefield.planning import DocumentTooLarge

    extractor = Extractor(_load_config(args.config))
    root = Path(args.path)
    if root.is_dir():
        files = sorted(p for p in root.iterdir() if p.is_file() and extractor.parser.supports(p))
    elif root.is_file():
        files = [root]
    else:
        raise _CliError(f"{root}: no such file or directory")
    if not files:
        raise _CliError(f"{root}: no supported documents")

    out = Path(args.output)
    failures = 0
    for path in files:
        try:
            if args.dry_run:
                plan = extractor.plan(extractor.parse(path))
                print(
                    f"{path}: {plan.chunks} chunk(s) × {len(plan.batches)} field batch(es) × "
                    f"{plan.n_runs} run(s) = {plan.calls} call(s); document "
                    f"{plan.document_tokens} tokens, largest prompt {plan.max_prompt_tokens} "
                    f"of {plan.input_budget} budget"
                )
                continue
            result = extractor.extract(path)
        except (ExtractionError, ParseError, DocumentTooLarge) as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
            continue
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{result.document.document_id}.json"
        result.to_json(target)
        counts = Counter(f.status for f in result.fields)
        summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        print(
            f"{path} -> {target}: {summary}; {result.run.llm_calls} call(s), "
            f"{result.run.tokens.input}+{result.run.tokens.output} tokens"
        )
    return 1 if failures else 0


def _eval(args: argparse.Namespace) -> int:
    from citefield.evaluate import EvalError, evaluate
    from citefield.extraction import ExtractionError
    from citefield.parsers import ParseError
    from citefield.planning import DocumentTooLarge

    if args.parser:
        raise _CliError("--parser is not supported yet")
    config = _load_config(args.config)
    try:
        report = evaluate(
            args.outputs, config, predictions=args.predictions, documents=args.documents
        )
    except (EvalError, ExtractionError, ParseError, DocumentTooLarge) as exc:
        raise _CliError(str(exc)) from exc
    except ValidationError as exc:
        raise _CliError(f"invalid output file\n{exc}") from exc
    if args.json:
        report.to_json(args.json)

    for p in report.predictions:
        print(
            f"{p.document_id}: {p.model}, {p.prompt_version}, "
            f"{p.tokens.input}+{p.tokens.output} tokens"
        )
    print()
    width = max(len(s.name) for s in [*report.fields, report.total])
    print(
        f"{'field':<{width}}  docs  tp  fp  fn  unjudged  precision  recall  ctx≠  "
        "not_found  errors"
    )
    for s in [*report.fields, report.total]:
        if s.documents == 0:
            continue
        not_found = f"{s.not_found_correct}/{s.confirmed_absent}" if s.confirmed_absent else "-"
        print(
            f"{s.name:<{width}}  {s.documents:>4}  {s.true_positives:>2}  "
            f"{s.false_positives:>2}  {s.false_negatives:>2}  {s.unjudged:>8}  "
            f"{_fmt(s.precision):>9}  {_fmt(s.recall):>6}  {s.context_mismatches:>4}  "
            f"{not_found:>9}  {s.errors:>6}"
        )
    unreviewed = [s.name for s in report.fields if s.documents == 0]
    if unreviewed:
        print(f"not reviewed in any document: {', '.join(unreviewed)}")

    print()
    print("signal            range        values  correct  precision")
    for b in report.signals:
        if b.values == 0:
            continue
        span = "null" if b.low is None else f"{b.low:.2f}-{b.high:.2f}"
        print(f"{b.signal:<16}  {span:<11}  {b.values:>6}  {b.correct:>7}  {_fmt(b.precision):>9}")

    if report.unjudged:
        print("\nunjudged predictions, not in the golden set yet:")
        for u in report.unjudged:
            context = f" ({u.context})" if u.context else ""
            print(f"  {u.document_id} · {u.field} · {u.value_id}: {u.raw_value!r}{context}")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


if __name__ == "__main__":
    sys.exit(main())
