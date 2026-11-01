# Architecture decision records

An ADR records one decision: the context, the options we considered, what we chose and what follows from it. [design.md](../design.md) describes the current design; the ADRs explain why it looks that way.

**Rules**

- Copy [template.md](template.md) to `NNNN-kebab-title.md` with the next free number.
- ADRs are append-only. To change a decision, write a new ADR with `Supersedes: ADR-NNNN`, and only update the old one's status to `Superseded` with a `Superseded by: ADR-NNNN` line.
- Statuses: `Proposed`, `Accepted`, `Rejected`, `Deprecated`, `Superseded`.
- Don't edit the table below by hand: run `uv run python scripts/check_docs.py --write`.

ADRs 0002–0010 were backfilled from design.md v1.0.0 when this log started.

<!-- adr-index:start -->
| ADR | Title | Status | Date |
| --- | ----- | ------ | ---- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted | 2026-10-30 |
| [0002](0002-license-under-apache-2-0.md) | License citefield under Apache 2.0 | Accepted | 2026-10-30 |
| [0003](0003-require-evidence-for-every-value-verified-in-code.md) | Require evidence for every value, verified in code | Accepted | 2026-10-30 |
| [0004](0004-pdfplumber-as-default-parser-with-optional-extras.md) | pdfplumber as the default parser, with optional extras | Accepted | 2026-10-30 |
| [0005](0005-litellm-as-default-llm-backend.md) | LiteLLM as the default LLM backend, behind a protocol | Accepted | 2026-10-30 |
| [0006](0006-named-confidence-signals-without-aggregate-score.md) | Expose named confidence signals, without an aggregate score | Accepted | 2026-10-30 |
| [0007](0007-store-reviews-in-the-output-file.md) | Store reviews in the output file and carry them over | Accepted | 2026-10-30 |
| [0008](0008-v1-scope-text-and-tables-library-and-cli.md) | Limit v1 to text and tables, as a library and CLI | Accepted | 2026-10-30 |
| [0009](0009-public-or-synthetic-test-data-only.md) | Use only public or synthetic manuals as test data | Superseded | 2026-10-30 |
| [0010](0010-reuse-workshop-ideas-not-code.md) | Reuse the workshop's ideas, not its code | Accepted | 2026-10-30 |
| [0011](0011-use-only-redistributable-manuals-as-test-data.md) | Use only redistributable or synthetic manuals as test data, committed in the repository | Accepted | 2026-10-30 |
| [0012](0012-llm-backends-return-token-usage.md) | LLM backends return token usage with each structured response | Accepted | 2026-10-30 |
| [0013](0013-report-evidence-match-as-the-raw-best-score.md) | Report evidence_match as the raw best score, and flag weak evidence with a warning | Accepted | 2026-10-30 |
| [0014](0014-record-field-level-review-in-the-output.md) | Record field-level review in the output, as a review object on each field | Accepted | 2026-10-31 |
| [0015](0015-score-evaluation-by-value-over-fully-reviewed-fields.md) | Score the evaluation by value, over fully reviewed fields only | Superseded | 2026-10-31 |
| [0016](0016-let-eval-score-saved-outputs.md) | Let eval score saved outputs as well as extract again | Accepted | 2026-10-31 |
| [0017](0017-match-eval-values-loosely-and-accept-reviewed-equivalents.md) | Match evaluation values on a looser key, and accept equivalent forms the reviewer declares | Accepted | 2026-10-31 |
| [0018](0018-add-openrouter-provider-routing-and-reasoning-effort-settings.md) | Add OpenRouter provider routing and reasoning effort to the LLM settings | Accepted | 2026-10-31 |
| [0019](0019-use-claude-haiku-5-5-as-the-default-model.md) | Use Claude Haiku 5.5 as the default model in the examples | Accepted | 2026-10-31 |
| [0020](0020-keep-number-values-scalar-and-leave-ranges-unparsed.md) | Keep number values scalar in v1, and leave ranges and tolerances unparsed | Accepted | 2026-11-01 |
| [0021](0021-round-converted-numbers-and-treat-numbers-within-1-percent-as-the-same-value.md) | Round converted numbers to the raw value's precision, and treat numbers within 1%, or a conversion within a stated value's precision, as the same value | Accepted | 2026-11-01 |
| [0022](0022-leave-ambiguous-numbers-unparsed-under-auto-locale.md) | Leave ambiguous numbers unparsed under the auto number locale | Accepted | 2026-11-01 |
<!-- adr-index:end -->
