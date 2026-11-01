# citefield

**Extract metadata from technical manuals, with a citation for every value.**

citefield is a Python library and CLI that reads a technical manual (PDF or Markdown), extracts the fields you define in a YAML config, and returns JSON in which every value cites a verbatim quote or table cell and its page. A human can then review the result value by value.

> **Status:** early development. `citefield extract` runs end to end on PDFs for string and enum fields, with verified quotes, and `citefield eval` scores it against hand-reviewed outputs; numbers and units, tables, proposals and review carry-over come next. The plan is in [`docs/design.md`](docs/design.md), and [`examples/`](examples/) has configs for the manuals in [`corpus/`](corpus/README.md).

## Why

LLMs are good at finding values in long documents and bad at proving where those values came from. citefield keeps the LLM's job narrow:

1. **The LLM only proposes.** Code verifies, humans decide.
2. **No evidence, no value.** Every value cites a quote or table cell and its page, and code checks that the citation really appears in the document.
3. **Deterministic where possible.** Unit conversion, evidence checks and merging are code, not prompts.
4. **Reviews are kept.** Human decisions survive re-extraction and become the evaluation set.

## How it will look

Define the fields you want:

```yaml
units: # extra unit definitions for pint
  - "bottle = [count]"
  - "bph = bottle / hour"

fields:
  - name: production_speed
    description: Nominal production speed of the machine in bottles per hour.
    type: number
    unit: bph
    aliases: [output, throughput, "velocità di produzione"]
```

Run the extraction:

```bash
citefield extract manuals/filler-x200.pdf -c fields.yaml -o out/
```

Get values with their evidence and confidence signals:

```json
{
  "name": "production_speed",
  "status": "found",
  "values": [
    {
      "raw_value": "400",
      "raw_unit": "bottles/min",
      "value": 24000,
      "unit": "bph",
      "derivation": "400 bottles/min = 24000 bph",
      "evidence": [
        {
          "kind": "quote",
          "page": 12,
          "quote": "Output: 400 bottles/min (0.5 L PET)"
        }
      ],
      "signals": {
        "evidence_match": 0.97,
        "citation_count": 1,
        "model_confidence": 0.9
      }
    }
  ]
}
```

citefield can also propose fields that aren't in your config, re-run extraction on a new revision of a manual without losing earlier reviews, and measure accuracy against the values people have reviewed.

## License

[Apache 2.0](LICENSE). The manuals in [`corpus/`](corpus/README.md) keep their own licenses, listed there. The design is inspired by Luca Bianchi's workshop _Documents Are Not Knowledge_ (Codemotion Milan 2026); see the [acknowledgements](docs/design.md#acknowledgements-and-licensing).
