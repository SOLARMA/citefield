import pytest
from pdfgen import write_pdf

from citefield.cli import main

CONFIG = """
fields:
  - name: engine_manufacturer
    description: Engine manufacturer.
settings:
  llm:
    model: openrouter/google/gemini-3.1-flash-lite
"""


def test_validate_config(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(CONFIG, encoding="utf-8")
    assert main(["validate-config", str(good)]) == 0
    assert "OK, 1 fields" in capsys.readouterr().out

    bad = tmp_path / "bad.yaml"
    bad.write_text("fields:\n  - name: Bad Name\n    description: x\n", encoding="utf-8")
    assert main(["validate-config", str(bad)]) == 1
    assert "snake_case" in capsys.readouterr().err

    assert main(["validate-config", str(tmp_path / "missing.yaml")]) == 1


def test_inspect(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "m.pdf", ["Engine manufacturer: Onan"])
    assert main(["inspect", str(pdf)]) == 0
    out = capsys.readouterr().out
    assert "[[page 1 · low text]]\nEngine manufacturer: Onan" in out
    assert "1 units, parser pdfplumber" in out


def test_extract_dry_run_makes_no_calls(tmp_path, capsys):
    config = tmp_path / "fields.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    pdf = write_pdf(tmp_path / "m.pdf", ["Engine manufacturer: Onan"])
    out = tmp_path / "out"
    assert main(["extract", str(pdf), "-c", str(config), "-o", str(out), "--dry-run"]) == 0
    assert "1 chunk(s) × 1 field batch(es) × 1 run(s) = 1 call(s)" in capsys.readouterr().out
    assert not out.exists()


def test_extract_dir_reports_broken_files_and_goes_on(tmp_path, capsys):
    config = tmp_path / "fields.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    write_pdf(docs / "good.pdf", ["Engine manufacturer: Onan"])
    (docs / "broken.pdf").write_text("<html>error 523</html>", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["extract", str(docs), "-c", str(config), "-o", str(out), "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "broken.pdf: can't parse" in captured.err
    assert "good.pdf: 1 chunk(s)" in captured.out


def test_eval_scores_saved_predictions(tmp_path, capsys):
    from fakes import output, value

    config = tmp_path / "fields.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    golden, predictions = tmp_path / "golden", tmp_path / "predictions"
    golden.mkdir()
    predictions.mkdir()
    f = "engine_manufacturer"
    output("tm", **{f: [value(f, "Onan", "accepted")]}).to_json(golden / "tm.json")
    output("tm", **{f: [value(f, "Onan"), value(f, "Kubota")]}).to_json(predictions / "tm.json")
    report = tmp_path / "report.json"

    args = ["eval", str(golden), "-c", str(config), "--predictions", str(predictions)]
    assert main([*args, "--json", str(report)]) == 0
    out = capsys.readouterr().out
    assert "engine_manufacturer     1   1   1   0         1       0.50    1.00" in out
    assert "evidence_match    0.85-1.00         2        1       0.50" in out
    assert "tm · engine_manufacturer" in out and "'Kubota'" in out
    assert '"precision": 0.5' in report.read_text(encoding="utf-8")

    assert main(["eval", str(tmp_path / "missing"), "-c", str(config)]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_help_hides_unbuilt_commands_and_flags(capsys):
    for argv, shown in (
        (["--help"], "validate-config"),
        (["extract", "--help"], "--dry-run"),
        (["eval", "--help"], "--predictions"),
    ):
        with pytest.raises(SystemExit):
            main(argv)
        out = capsys.readouterr().out
        assert shown in out, argv
        for hidden in ("reextract", "proposals", "--workers", "--parser", "SUPPRESS"):
            assert hidden not in out, (argv, hidden)
