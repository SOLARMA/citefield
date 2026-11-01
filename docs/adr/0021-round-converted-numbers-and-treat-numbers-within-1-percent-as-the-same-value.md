# 0021. Round converted numbers to the raw value's precision, and treat numbers within 1%, or a conversion within a stated value's precision, as the same value

- Status: Accepted
- Date: 2026-11-01

## Context

Number values are converted to the field's unit with pint ([design.md, Extraction](../design.md#extraction)). Converted values carry float noise, and they rarely equal the manual's own conversion: the generator manual states `MEP-803A 1,140 lb. (516.4 kg)`, and 1,140 lb is 517.0953… kg. `value_id` hashes the normalized value ([design.md, Output JSON](../design.md#output-json)), merging groups candidates by it, and two different values in the same context make the field a `conflict`. So the two forms of one weight would be reported as a conflict. Manuals also round their own conversions coarsely: `Displacement 114 cu. in. (1.9 L)`, where 114 in³ is 1.868 L, 1.7% from the stated 1.9. The evaluation compares non-string values exactly, since [ADR-0017](0017-match-eval-values-loosely-and-accept-reviewed-equivalents.md) only had string fields to deal with.

## Considered options

Rounding a converted value:

- **No rounding**: float noise in the output and in `value_id`.
- **A fixed number of significant figures**: simple, but claims precision the manual doesn't state (`9 gal.` becomes 34.07 L).
- **The raw value's significant figures plus one**: keeps the manual's precision and one guard digit (`9 gal.` becomes 34 L, `1,140 lb.` 517.1 kg), but a one-digit raw value keeps only two figures, which can be off by about 5%: `5 lb (2.27 kg)` would give 2.3 kg, 1.3% from the manual's own 2.27, so the two forms would still be a conflict.
- **The raw value's significant figures plus one, and at least four**: rounding error stays below about 0.05%, far inside the tolerance below, at the cost of showing more digits than a short raw value has (`9 gal.` becomes 34.07 L).

Deciding that two numbers are the same value:

- **Exact equality after rounding**: 517.1 kg and 516.4 kg are still a conflict.
- **Bucketing**, such as rounding to two significant figures for `value_id`: ids stay stable across forms, but values either side of a bucket edge split, and different values inside one bucket merge.
- **A relative tolerance**: absorbs the authors' own rounding, but could merge two different values that are closer than the tolerance.
- **A tolerance that depends on precision for any two values** (1%, or the overlap of both values' written precision): covers coarse conversions, but also merges two stated values that differ, such as `1 kW` and `1.4 kW`, which is a discrepancy in the manual.
- **1%, or a converted value within a stated value's written precision**: covers the manual's coarse conversions (1.868 is inside the 1.85 to 1.95 that `1.9` stands for), while two stated values still compare at 1%. The precision has to be recovered from the raw text, but the parsed value already fixes how the locale read it.

## Decision

We will round a converted value (status `derived`) to the raw value's significant figures plus one, and to at least four, counting every written digit except leading zeros (`1,140` has 4 and keeps 5, `.99` has 2 and keeps 4, `9` has 1 and keeps 4). A value already stated in the field's unit (status `found`) is kept as parsed. A whole number is stored as an integer, so `34.0` and `34` are the same value.

Two parsed numbers of one field are the same value when they differ by at most 1% of the larger magnitude, or when one is converted (status `derived`) and lies within the written precision of the other, stated in the field's unit. The written precision is half a unit of the last digit of the raw value as the locale read it: `1.9` is ±0.05, `1,140` read as 1140 is ±0.5. A number without its text, such as a `reviewed_value` given as a number, has no written precision. When merging, candidates with the same normalized context and the same value form one value. Its raw value, unit, value and derivation come from a representative: the first candidate stated in the field's unit, or the first candidate if none is. Every candidate's evidence is kept, and `value_id` hashes the representative's value. Different values with the same or a missing context are a `conflict` only when they aren't the same value by this rule. Candidates whose value stays null group by raw text, as before.

In the evaluation, a number or integer prediction matches a golden value by the same rule, in the field's unit. An edited `reviewed_value` is a number in the field's unit, or text with its unit. That text, and an equivalent of a number field (`also_accept`, such as `516.4 kg`), is parsed and converted like a raw value and unit, and one that doesn't parse is an error. Values that stay null fall back to the text key of ADR-0017, and so do their equivalents, such as a range in another unit.

The 1% is a constant, not a setting.

## Consequences

- A quantity the manual states in two units becomes one value with both quotes, not a conflict.
- `value_id` depends on which form is the representative: a run that cites only `516.4 kg` gets a different id from one that also cites `1,140 lb.`. Carry-over on re-extraction, which isn't built yet, has to compare number values with the same tolerance, not by `value_id` alone.
- Two genuinely different values in the same context that are within 1% of each other merge silently, where they used to be a conflict, and so does a converted value within a stated value's precision. If the golden set shows such a case, make the tolerance a setting in a new ADR.
- Two converted values compare at 1% only: a converted value's own precision isn't used, so `5 lb` and `2.3 kg` converted to grams would still be a conflict.
- The output JSON doesn't change, and `value_id` doesn't change for string and enum fields.
- This extends ADR-0017 to number fields; the text key for strings is unchanged.
