# 0022. Leave ambiguous numbers unparsed under the auto number locale

- Status: Accepted
- Date: 2026-11-01

## Context

`number_locale` is `auto`, `en`, `it` or `de` ([design.md, Configuration](../design.md#configuration)). Some forms read differently by locale: `24.000` is 24000 in Italian and German and 24 in English; `1,140` is 1140 in English and 1.14 in Italian and German. The design said an ambiguous form under `auto` gets a warning rather than a silent choice, and left open whether `auto` should instead follow the document's detected language. The corpus has two manuals, both English, and test data has to be redistributable ([ADR-0011](0011-use-only-redistributable-manuals-as-test-data.md)), so there is no Italian or German manual to test detection on.

## Considered options

- **Follow the document's detected language**: usually the right reading, but needs a language detector or heuristic, and test data we don't have.
- **Infer the locale from the document's unambiguous numbers** (`516.4`, `1.140,5`): no language detection, but still untested on Italian or German manuals.
- **Always warn**: an ambiguous form keeps `value: null`; users set `number_locale` for their manuals.

## Decision

We will always warn. Under `auto`, a number that reads as two different values with `.` or `,` as the decimal mark, such as `24.000` or `1,140`, is ambiguous: it keeps `value: null` and gets an `ambiguous_number` warning with the field and the raw value. Every other form is read the one way it can be read (`516.4`, `516,4`, `.99`, `0,500`, `1,140.5`, `1.140,5`, `1,140,000`). With `en`, `it` or `de` set, the locale decides every form.

## Consequences

- English manuals with thousands separators (`1,140 lb.`) get null values under `auto`, so the example configs set `number_locale: en`.
- The reviewer sees each ambiguous number as a warning, never as a guessed value.
- Revisit when the corpus has a non-English manual, or when ambiguous-number warnings dominate a golden set; detection would be a new ADR.
