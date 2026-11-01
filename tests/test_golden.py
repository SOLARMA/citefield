"""The golden set in corpus/golden/ must keep loading as the output and config models change.
Reads only the JSON and YAML files; the manuals are not needed."""

from pathlib import Path

import pytest

from citefield import ExtractionResult, apply_review, load_config
from citefield.review import load_decisions

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = sorted((ROOT / "corpus" / "golden").glob("*/*.json"))


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_golden_output_matches_its_config(path):
    result = ExtractionResult.from_json(path)
    config = load_config(ROOT / "examples" / f"{path.parent.name}.yaml")
    assert [f.name for f in result.fields] == [f.name for f in config.fields]

    decisions = path.with_suffix(".review.yaml")
    if decisions.exists():
        apply_review(result, load_decisions(decisions), config=config)
