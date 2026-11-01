# Test corpus

Real manuals for development runs and evaluation. Every file here is public domain or openly licensed, so the repository can redistribute it ([ADR-0011](../docs/adr/0011-use-only-redistributable-manuals-as-test-data.md)). The files are stored byte for byte as downloaded; compare `shasum -a 256 corpus/manuals/*.pdf` with the table below.

The corpus is not part of the citefield package and does not fall under its Apache 2.0 license: each file keeps the license listed here. CI doesn't read the manuals; unit tests use small synthetic fixtures.

| File | Document | License | Source | SHA-256 |
| ---- | -------- | ------- | ------ | ------- |
| [tm-9-6115-642-10.pdf](manuals/tm-9-6115-642-10.pdf) | US Army TM 9-6115-642-10, Operator's Manual for Generator Set, Skid Mounted, Tactical Quiet, 10 kW (MEP-803A, MEP-813A), 15 September 2010. Headquarters, Departments of the Army, Air Force and U.S. Marine Corps. 137 pages. | Public domain (US government work). Cover: "Distribution Statement A. Approved for public release; distribution is unlimited." | [liberatedmanuals.com mirror](https://www.liberatedmanuals.com/TM-9-6115-642-10.pdf), downloaded 2026-10-30. Official publisher: [Army Publishing Directorate](https://armypubs.army.mil). | `beedf1005f0aaabe997a10a4853dbde970181ab43e96779b6d68e0c45b33bad2` |
| [lulzbot-taz-sidekick-user-guide-v1.2.pdf](manuals/lulzbot-taz-sidekick-user-guide-v1.2.pdf) | LulzBot TAZ SideKick User Guide v1.2 (02 09 22), by LulzBot, 2022. 100 pages. LulzBot® is a registered trademark of FAME 3D, LLC. | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (page 4). Unmodified. | [download.lulzbot.com](https://download.lulzbot.com/SideKick/Documents/SK_User_Guide.pdf), downloaded 2026-10-30. | `9ef45d18ff84f91cee609b584bac2a16299b00cb321474e771c2f520ffaf55b6` |

**What each one exercises**

- **TM 9-6115-642-10**: a use and maintenance manual with a "Leading Particulars" table (dimensions, weights, engine and electrical data). Values are given per model (MEP-803A vs MEP-813A) and in two units, e.g. "1,140 lb. (516.4 kg)".
- **TAZ SideKick User Guide**: technical specifications and maintenance chapters for two models side by side (289 and 747). The two-column layout makes pdfplumber interleave sidebar text with the body, and stacked fractions extract badly ("6 3/” … 8 8 8").

**Candidates not yet included**

- [LulzBot TAZ 6 User Manual](https://download.lulzbot.com/TAZ/6.0/documentation/manual/TAZ-6-Manual.pdf) (CC BY-SA, 23 MB): pdfplumber drops spaces between words, a hard case for quote matching.
- [TM 9-6115-464-12](https://manuals.chudov.com/Military-Generators/MEP-113A/TM-9-6115-464-12.pdf) (Distribution Statement A, operator and unit maintenance): its text layer looks like OCR output, a robustness case.

**Adding a manual**: it must be public domain or carry an open license that allows redistribution (no "freely downloadable" proprietary manuals). Add the file unmodified under `manuals/` with a lowercase kebab-case name, and a row here with the document, license and where it is stated, source URL with download date, and SHA-256.

## Golden set

`golden/<config>/` holds the reviewed outputs that `citefield eval` scores against, one per manual, made with the config of the same name in [`examples/`](../examples/). `generator` and `3d-printer` cover string and enum fields; `generator-numbers` and `3d-printer-numbers` cover number fields with units, on the same manuals. Next to each output, `<document_id>.review.yaml` holds the reviewer's decisions. The outputs quote the manuals, so those quotes keep the manual's license (CC BY-SA 4.0 for the LulzBot guide). CI checks that every golden output still loads and matches its config.

To review or extend a golden output (from the repository root):

```sh
# set statuses in the YAML: accepted, edited (with reviewed_value) or rejected; add missed
# values under `added` with a quote and page; list other forms of the same fact under
# `also_accept`; set a field to confirmed_absent if the manual doesn't state it
uv run python scripts/review.py apply corpus/golden/generator/tm-9-6115-642-10.json \
  corpus/golden/generator/tm-9-6115-642-10.review.yaml \
  -o corpus/golden/generator/tm-9-6115-642-10.json -c examples/generator.yaml
uv run citefield eval corpus/golden/generator -c examples/generator.yaml
```

`uv run python scripts/review.py template <output.json>` writes a fresh decisions file for a new output.
