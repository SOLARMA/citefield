# citefield docs

| Where                          | What it holds                                                               |
| ------------------------------ | --------------------------------------------------------------------------- |
| [design.md](design.md)         | The current design: architecture, config, output, API. Kept up to date.     |
| [adr/](adr/README.md)          | Architecture decision records: why the design is the way it is. Append-only. |
| [evaluations/](evaluations/model-bakeoff-2026-10.md) | Evaluation reports, such as model comparisons on the golden set. |
| [guides/](guides/getting-started.md) | Task-oriented how-tos for users, starting with getting started. |

Folders are added when they have content. The planned one is:

- `reference/`: config schema, CLI and output JSON reference, generated from code where possible.

**Keeping docs in order**

- A change to a public contract (output JSON, signals, config schema, CLI, protocols), a default dependency or the v1 scope needs an ADR, and `design.md` updated to match.
- Every doc is linked from this page or from an index it links to.
- `uv run python scripts/check_docs.py` checks ADR numbering and metadata, the ADR index, supersede links and relative links; `--write` regenerates the ADR index. It also runs as part of the test suite.
- With Claude Code, the `adr` and `docs-maintenance` skills in `.claude/skills/` follow these rules.
