# Getting started

This guide takes one manual from the test corpus through the whole loop: write a field config, check what the parser sees, extract, read the output, review it and measure accuracy. The running example is the US Army generator manual [TM 9-6115-642-10](../../corpus/README.md) with the number fields in [`examples/generator-numbers.yaml`](../../examples/generator-numbers.yaml). One extraction makes a single LLM call and costs about a cent with the recommended model.

What works today: PDF manuals, and string, enum and number fields (with units converted in code). Tables as evidence, field proposals and re-extraction with review carry-over are planned; see the [design](../design.md).

## Install

citefield needs Python 3.11 or later.

To use the CLI on your own manuals, install it from PyPI as a tool:

```sh
uv tool install citefield   # or: pipx install citefield
```

citefield isn't on PyPI until the first release (0.1.0); until then, install from source as below.

To follow this guide, clone the repository instead: the example configs, the corpus manuals and the review script live there and aren't part of the package.

```sh
git clone https://github.com/aleche28/citefield.git
cd citefield
uv sync
```

Run the commands below from the repository root, prefixed with `uv run` so they use the project's environment. Outputs record the manual's path as you passed it to `extract`, and the review and evaluation steps open the manual again at that path.

## Set up an LLM

citefield calls the model through [LiteLLM](https://docs.litellm.ai/docs/providers), so any provider LiteLLM supports works: the model name in the config picks the provider, and LiteLLM reads that provider's API key from the environment. The example configs use [OpenRouter](https://openrouter.ai):

```sh
export OPENROUTER_API_KEY=sk-or-...
```

There is no built-in default model: `settings.llm.model` must be set in the config, or `extract` stops with `settings.llm.model is not set`. The recommended model is Claude Haiku 5.5 through OpenRouter, which scored best in the [model bake-off](../evaluations/model-bakeoff-2026-10.md) ([ADR-0019](../adr/0019-use-claude-haiku-5-5-as-the-default-model.md)):

```yaml
settings:
  llm:
    model: openrouter/anthropic/claude-haiku-5.5
    temperature: null # Haiku doesn't accept a temperature; null leaves it out of the request
```

The default `temperature` is 0, for models that accept it. Other `settings.llm` keys set the reasoning effort, OpenRouter provider routing and retries; they are listed under [Configuration](../design.md#configuration) in the design.

## Write a field config

A config lists the fields to extract. Each field needs a snake_case `name` and a `description` that tells the model what to look for; `type` defaults to `string`. A number field takes a `unit`, and every value is converted to it in code with [pint](https://pint.readthedocs.io). An `enum` field takes the allowed `values`. Excerpt from `examples/generator-numbers.yaml`:

```yaml
schema_version: 1

fields:
  - name: dry_weight
    description: Dry weight of the generator set (less basic issue items), one per model.
    type: number
    unit: kg

  - name: fuel_consumption_rate
    description: Fuel consumption rate, one per output frequency.
    type: number
    unit: L/h

settings:
  llm:
    model: openrouter/anthropic/claude-haiku-5.5
    temperature: null
  extraction:
    number_locale: en # the manual writes 1,140 for 1140
```

"One per model" in a description asks for one value per variant, each with its own context. `number_locale: en` reads "1,140" as one thousand one hundred forty; under the default `auto`, a number that reads two ways keeps no value and gets an `ambiguous_number` warning ([ADR-0022](../adr/0022-leave-ambiguous-numbers-unparsed-under-auto-locale.md)). [`examples/generator.yaml`](../../examples/generator.yaml) has string and enum fields for the same manual.

Check the config before spending anything. This validates the schema and resolves every unit, without calling the LLM:

```console
$ uv run citefield validate-config examples/generator-numbers.yaml
examples/generator-numbers.yaml: OK, 11 fields
```

## Look at the parsed text

The model only sees the text the parser extracts, so look at it first. `inspect` prints every page; send it to a file or a pager:

```console
$ uv run citefield inspect corpus/manuals/tm-9-6115-642-10.pdf > tm.txt
$ tail -n 1 tm.txt
--- tm-9-6115-642-10.pdf: 137 units, parser pdfplumber 0.11.10, 27 low-text: [2, 6, 8, ...]
```

Each page starts with a marker such as `[[page 27]]`. Pages are numbered by their position in the PDF, not by the label printed on them: the page printed "0002" with the equipment data is page 27, and evidence cites it that way. A low-text page has little or no text layer, usually a drawing or a scan; its content is invisible to the model. Quotes in the output are checked against this text, and when you add a value during review, copy its quote from here.

## Plan and run the extraction

`--dry-run` shows how the run is split into calls and how many tokens it sends, without calling the LLM:

```console
$ uv run citefield extract corpus/manuals/tm-9-6115-642-10.pdf -c examples/generator-numbers.yaml -o out/ --dry-run
corpus/manuals/tm-9-6115-642-10.pdf: 1 chunk(s) × 1 field batch(es) × 1 run(s) = 1 call(s); document 37668 tokens, largest prompt 38311 of 191808 budget
```

The token counts are LiteLLM's estimate; Haiku bills about 1.5× as many input tokens. Then run it:

```console
$ uv run citefield extract corpus/manuals/tm-9-6115-642-10.pdf -c examples/generator-numbers.yaml -o out/
corpus/manuals/tm-9-6115-642-10.pdf -> out/tm-9-6115-642-10.json: 10 found, 1 not_found; 1 call(s), 65013+1670 tokens
```

`extract` also takes a folder and writes one JSON per manual, named after the file.

## Read the output

`out/tm-9-6115-642-10.json` records the document (path, SHA-256, parser) and the run (model, prompt version, config hashes, tokens), then one entry per field. A field's `status` is `found`, `not_found`, `conflict` (different values for the same context, all kept for you to choose) or `error` (the LLM call failed; the message is in `error`). Here is the first value of `dry_weight`:

```json
{
  "value_id": "8d7b888e3314d9e1",
  "status": "derived",
  "raw_value": "1,140",
  "raw_unit": "lb.",
  "value": 517.1,
  "unit": "kg",
  "derivation": "1,140 lb. = 517.1 kg",
  "context": "MEP-803A",
  "evidence": [
    { "kind": "quote", "page": 27, "quote": "Dry Weights (less Basic Issue Items):" },
    { "kind": "quote", "page": 27, "quote": "MEP-803A 1,140 lb. (516.4 kg)" }
  ],
  "signals": {
    "evidence_match": 1.0,
    "citation_count": 1,
    "run_agreement": null,
    "model_confidence": 0.9,
    "unit_resolved": true
  },
  "review": { "status": "pending", ... }
}
```

- `raw_value` and `raw_unit` are copied from the manual; `value` and `unit` are in the field's unit. The value's `status` is `found` when the manual states it in that unit and `derived` when code converted it, with the conversion in `derivation`.
- `context` says what the value applies to, here the model. `dry_weight` has a second value for the MEP-813A.
- `evidence` holds the quotes and the pages they're on.
- `signals` are separate measures, with no overall score. `evidence_match` is how well the best quote matches the text of its page (1.0 is verbatim); `model_confidence` is the model's own estimate and isn't calibrated. The full list is under [Evidence and confidence signals](../design.md#evidence-and-confidence-signals).

When code can't turn a raw value into a number, the value is kept with `value: null` for you to review, and `warnings` at the end of the file says why. In this run, "172-414 kPa" for the normal oil pressure is a range, which gets `number_not_scalar` ([ADR-0020](../adr/0020-keep-number-values-scalar-and-leave-ranges-unparsed.md)). Other codes are `not_a_number`, `unit_unresolved` (no unit, or one that doesn't convert to the field's) and `ambiguous_number`. A quote that doesn't match its page well gets `evidence_not_verified`. That isn't always a wrong value: the rated power's quote "Load Capacity 10 kW 10 kW" is a table row the parser laid out differently, and it scores 0.45. Look at the page in the `inspect` output to decide. The complete output format is under [Output JSON](../design.md#output-json).

## Review the output

Reviews are stored in the output file itself, value by value. Until there is a review UI, [`scripts/review.py`](../../scripts/review.py) writes a YAML file of decisions for you to fill in, then applies it. Write the template:

```sh
uv run python scripts/review.py template out/tm-9-6115-642-10.json > tm.review.yaml
```

Each value appears with its quotes and signals as comments, and a status to set:

```yaml
reviewer: null
fields:
  dry_weight:
    # field status: found
    status: pending  # or confirmed_absent
    values:
      # "1,140" (MEP-803A), lb.
      #   1,140 lb. = 517.1 kg
      #   p.27: "Dry Weights (less Basic Issue Items):"
      #   p.27: "MEP-803A 1,140 lb. (516.4 kg)"
      #   evidence_match 1.0, model_confidence 0.9
      - value_id: 8d7b888e3314d9e1
        status: pending
```

Set your name as `reviewer`, then for each value:

- `accepted`: the value is right.
- `edited`: the value needs correcting; give the right one as `reviewed_value`.
- `rejected`: the value is wrong.
- `also_accept`: other forms of the same fact for the evaluation to accept, such as `["516.4 kg"]` when the manual states both.

Add a value the model missed under the field's `added`, with a quote copied from the `inspect` output and its page. Set a field's own `status` to `confirmed_absent` when the manual doesn't state it at all; this manual gives no engine rated speed. Then apply the decisions to a copy of the output:

```sh
mkdir -p reviewed
uv run python scripts/review.py apply out/tm-9-6115-642-10.json tm.review.yaml \
  -o reviewed/tm-9-6115-642-10.json -c examples/generator-numbers.yaml
```

`apply` parses the manual again to check the quotes of added values and warns when one doesn't match its page. Pass `--document` if the manual has moved since the extraction.

## Measure accuracy

`citefield eval` scores outputs against a folder of reviewed outputs, the golden set. The corpus already has one for this config in [`corpus/golden/generator-numbers/`](../../corpus/golden/). With `--predictions`, it scores outputs you have already extracted and makes no LLM calls:

```console
$ uv run citefield eval corpus/golden/generator-numbers -c examples/generator-numbers.yaml --predictions out/
tm-9-6115-642-10: openrouter/anthropic/claude-haiku-5.5, extract-v1, 65013+1670 tokens

field                    docs  tp  fp  fn  unjudged  precision  recall  ctx≠  not_found  errors
dry_weight                  1   2   0   0         0       1.00    1.00     0          -       0
...
engine_rated_speed          1   0   0   0         0          -       -     0        1/1       0
all                        11  15   0   0         0       1.00    1.00     7        1/1       0
```

Per field: true and false positives and false negatives against the reviewed values, precision and recall, `ctx≠` for matches whose context differs from the reviewed one, and `not_found` for fields reviewed as absent that the run correctly left empty. Predictions the golden set doesn't cover yet are listed as unjudged, ready to review in. A second table shows precision by `evidence_match` and `model_confidence` range, which tells you how far to trust each signal. A field counts only in documents where it is fully reviewed, so your own `reviewed/` folder from the previous step works as a golden set too.

Without `--predictions`, `eval` extracts again with the current config and model, which is how to test a change to either; it calls the LLM once per manual. `--json report.json` saves the report. How matching works is under [Review, re-extraction and evaluation](../design.md#review-re-extraction-and-evaluation).

## Next steps

- Try the 3D printer manual: [`examples/3d-printer.yaml`](../../examples/3d-printer.yaml) and [`examples/3d-printer-numbers.yaml`](../../examples/3d-printer-numbers.yaml) on `corpus/manuals/lulzbot-taz-sidekick-user-guide-v1.2.pdf`, with golden sets of the same names.
- Write a config for your own manuals. Start with a few fields, read the `inspect` output for one manual, and build a small golden set before tuning descriptions.
- Read the [design](../design.md) for every config setting and the full output format.
