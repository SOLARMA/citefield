"""Review an output by hand: write a decisions template, edit it, apply it.

    uv run python scripts/review.py template out/tm.json > tm.review.yaml
    # set each status to accepted, edited (with reviewed_value) or rejected; add missed values;
    # set a field's status to confirmed_absent when the manual doesn't state it
    uv run python scripts/review.py apply out/tm.json tm.review.yaml -o golden/tm.json \
        -c examples/generator.yaml

A stand-in for a review UI, which is out of scope for v1. Not part of the package.
"""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from citefield.config import load_config
from citefield.output import ExtractionResult
from citefield.parsers import ParseError, get_parser, parse_document
from citefield.review import apply_review, load_decisions


def template(result: ExtractionResult) -> str:
    """YAML decisions for every value, keeping decisions already in the output."""
    lines = [
        f"# Review of {result.document.document_id} ({result.document.path})",
        f"# run {result.run.run_id}, model {result.run.model}",
        "reviewer: null",
        "fields:",
    ]
    for field in result.fields:
        lines.append(f"  {field.name}:")
        lines.append(f"    # field status: {field.status}")
        if field.error:
            lines.append(f"    # error: {_one_line(field.error)}")
        lines.append(f"    status: {field.review.status}  # or confirmed_absent")
        value_lines = []
        for v in field.values:
            context = f" ({v.context})" if v.context else ""
            if v.review.status == "added":
                value_lines.append(f"      # added by the reviewer: {_quote(v.raw_value)}{context}")
                continue
            value_lines.append(f"      # {_quote(v.raw_value)}{context}, {v.raw_unit or 'no unit'}")
            if v.status == "derived":
                value_lines.append(f"      #   {v.derivation}")
            for e in v.evidence:
                value_lines.append(f"      #   p.{e.page}: {_quote(e.quote)}")
            value_lines.append(
                f"      #   evidence_match {v.signals.evidence_match}, "
                f"model_confidence {v.signals.model_confidence}"
            )
            value_lines.append(f"      - value_id: {v.value_id}")
            value_lines.append(f"        status: {v.review.status}")
            if v.review.reviewed_value is not None:
                value_lines.append(f"        reviewed_value: {_quote(v.review.reviewed_value)}")
            if v.review.equivalents:
                value_lines.append(f"        also_accept: {_quote(v.review.equivalents)}")
        has_values = any(line.startswith("      - value_id") for line in value_lines)
        lines.append("    values:" if has_values else "    values: []")
        lines.extend(value_lines)
        lines.append(
            "    added: []  # - {raw_value: ..., context: ..., evidence: [{page: 1, quote: ...}]}"
        )
    return "\n".join(lines) + "\n"


def _quote(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _one_line(text: str) -> str:
    return " ".join(text.split())[:200]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("template", help="print a decisions template for an output")
    t.add_argument("output")
    a = sub.add_parser("apply", help="write the decisions into a copy of the output")
    a.add_argument("output")
    a.add_argument("decisions")
    a.add_argument("-o", "--reviewed", required=True, help="where to write the reviewed output")
    a.add_argument("--document", help="the manual, if not at the path recorded in the output")
    a.add_argument("-c", "--config", required=True, help="the field config, to parse added values")
    args = parser.parse_args(argv)

    if args.command == "template":
        sys.stdout.write(template(ExtractionResult.from_json(args.output)))
        return 0
    result = ExtractionResult.from_json(args.output)
    try:
        # Parse the manual again to verify the quotes of added values.
        parser = get_parser(result.document.parser.name)
        document = parse_document(args.document or result.document.path, parser)
        if document.sha256 != result.document.sha256:
            raise ValueError(f"{document.path}: not the file this output was made from")
        pages = {u.locator.page: u.text for u in document.units if u.locator.page is not None}
        config = load_config(args.config)
        verify = config.settings.verification
        reviewed = apply_review(
            result,
            load_decisions(args.decisions),
            pages=pages,
            page_window=verify.page_window,
            config=config,
        )
    except (OSError, ValueError, ParseError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for w in reviewed.warnings[len(result.warnings) :]:  # from parsing added values
        print(f"warning: {w.field}: added value {w.raw!r}: {w.message or w.code}", file=sys.stderr)
    for field in reviewed.fields:
        for v in field.values:
            if v.review.status == "added" and v.signals.evidence_match < verify.fuzzy_min_ratio:
                print(
                    f"warning: {field.name}: added value {v.raw_value!r} has evidence_match "
                    f"{v.signals.evidence_match}; copy the quote from `citefield inspect`",
                    file=sys.stderr,
                )
    reviewed.to_json(args.reviewed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
