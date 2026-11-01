# 0020. Keep number values scalar in v1, and leave ranges and tolerances unparsed

- Status: Accepted
- Date: 2026-11-01

## Context

Number and integer fields get a parsed `value` in code ([design.md, Extraction](../design.md#extraction)), and `value` is a scalar in the output JSON. The generator manual in the corpus states many quantities as ranges or tolerances, not single numbers: `Normal Operating Pressure 25-60 psi (172-414 kPa)`, `COOLANT TEMP 170-200 °F`, `-25 to 0 °F`, `Trip Temperature 15 ±3 psi`. Number parsing is the next slice, so we have to decide what such a raw value becomes. The example configs already keep a range-valued field as a string (`ambient_operating_temperature`).

## Considered options

- **Scalar only**: a range or tolerance keeps `value: null` with a warning, and the candidate stays visible with its evidence. No change to the output JSON; a field the manual states as a range has to be a string field to be scored by value.
- **A range value**, such as `{min, max}` or `{nominal, tolerance}` in `value`: models what manuals state, but changes the output JSON, `value_id`, merging, conflict detection and evaluation matching, before any number field has been scored.
- **Reduce to one number** (one end, or the midpoint): no schema change, but a silent choice the manual doesn't make.

## Decision

We will keep `value` scalar for number and integer fields in v1. A raw value that states a range (two numbers joined by a dash or "to") or a tolerance (a number with ±) keeps `value: null` and gets a `number_not_scalar` warning with the field and the raw value; for a field with a unit, `unit_resolved` is false. The candidate, its evidence and its signals are kept like any other value that can't be parsed.

## Consequences

- The output JSON doesn't change and `output_schema_version` stays 1.
- Fields stated as ranges stay string fields in configs and are scored on the evaluation's text key ([ADR-0017](0017-match-eval-values-loosely-and-accept-reviewed-equivalents.md)).
- A number field that gets a range shows the reviewer a null value and a warning, never a guessed number.
- Revisit when the golden set shows number fields regularly stated as ranges, or a client needs ranges it can compare. A range value would be a new ADR and an output schema change.
