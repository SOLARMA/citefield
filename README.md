# citefield

**Extract metadata from technical manuals, with a citation for every value.**

citefield is a Python library and CLI that reads a technical manual, extracts the fields you define in a YAML config, and returns JSON in which every value cites a verbatim quote and its page. A human can then review the result value by value.

> **Status:** early development. What works today: PDF manuals; string, enum and number fields, with quotes verified and units converted in code; evaluation against hand-reviewed outputs. Planned: tables as evidence, Markdown input, field proposals and re-extraction that keeps earlier reviews. The plan is in the [design](https://github.com/aleche28/citefield/blob/main/docs/design.md).

## Why

LLMs are good at finding values in long documents and bad at proving where those values came from. citefield keeps the LLM's job narrow:

1. **The LLM only proposes.** Code verifies, humans decide.
2. **No evidence, no value.** Every value cites a quote and its page, and code checks that the quote really appears in the document.
3. **Deterministic where possible.** Unit conversion, evidence checks and merging are code, not prompts.
4. **Reviews are kept.** Human decisions are stored in the output and become the evaluation set.

## Quick start

Install the CLI (citefield is on PyPI from the first release, 0.1.0; until then, [install from source](https://github.com/aleche28/citefield/blob/main/docs/guides/getting-started.md#install)):

```sh
uv tool install citefield   # or: pipx install citefield
```

citefield calls the model through LiteLLM, which reads the provider's API key from the environment. The example configs use Claude Haiku 5.5 on OpenRouter:

```sh
export OPENROUTER_API_KEY=sk-or-...
```

Define the fields you want:

```yaml
fields:
  - name: dry_weight
    description: Dry weight of the generator set (less basic issue items), one per model.
    type: number
    unit: kg

settings:
  llm:
    model: openrouter/anthropic/claude-haiku-5.5
    temperature: null # not accepted by the model
  extraction:
    number_locale: en # the manual writes 1,140 for 1140
```

Run the extraction:

```bash
citefield extract tm-9-6115-642-10.pdf -c fields.yaml -o out/
```

Get values with their evidence and confidence signals:

```json
{
  "name": "dry_weight",
  "status": "found",
  "values": [
    {
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
      }
    }
  ]
}
```

The [getting-started guide](https://github.com/aleche28/citefield/blob/main/docs/guides/getting-started.md) walks through the whole loop on a manual from the [test corpus](https://github.com/aleche28/citefield/blob/main/corpus/README.md): checking the config and the parsed text, extracting, reading the output, reviewing it and measuring accuracy. [`examples/`](https://github.com/aleche28/citefield/tree/main/examples) has configs for every corpus manual.

## License

[Apache 2.0](https://github.com/aleche28/citefield/blob/main/LICENSE). The manuals in [`corpus/`](https://github.com/aleche28/citefield/blob/main/corpus/README.md) keep their own licenses, listed there. The design is inspired by Luca Bianchi's workshop _Documents Are Not Knowledge_ (Codemotion Milan 2026); see the [acknowledgements](https://github.com/aleche28/citefield/blob/main/docs/design.md#acknowledgements-and-licensing).
