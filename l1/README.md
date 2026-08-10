# L1 — standardised records (v2.4, schema `l1.5`)

One JSON object per case page, identical in shape across all 1902.

**v2.4 changes** — records are now observations ONLY, and the coverage gap is closed:

12. **Classifier verdicts moved out of the records** into
    `data/l1/derived.jsonl`, built by [`derive.py`](derive.py) from
    `records.jsonl` alone (no HTML): `heading_confidence`,
    `heading_normalised`(`_v1`), `heading_v1_would_emit`, `abstract_boundary`,
    `source.integrity` (now `source_integrity`), `outcomes.banner_headings`
    and `markup.panel_ruling_standalone_heading`. Each has a measured error
    rate, which is exactly why they no longer live inside a file that claims
    to be a 100% honest representation. Verified: the derived values are
    byte-identical to the last in-record generation on all 1902 records.
13. **Three uncaptured sources added.** `identity.h2_text` (the hero subject
    line — disagrees with `cludo:description` on 850 pages, including
    'Alleged breach' vs 'No breach'), `identity.title_text` (+ parsed case
    numbers — disagrees with `<h1>` on 45 pages), and `aside_links[]` /
    `aside_headings[]` (case-report downloads including 6 `.docx` invisible to
    `markup.pdf_links`, advertised-sanctions links, one page's RELATED ADVICE
    block, and link text that mis-states a case number on AUTH-2102-2-08).
14. **info-holder values were TRUNCATED on 956 pages — now fixed.** The value
    div nests chip divs (`index-label` clause chips, `tag-label` sanction
    chips) and the old regex stopped at the first `</div>`: 'Breach Clause(s)'
    lost clauses after the first on 548 pages (AUTH-2818-1-16 read '12.1',
    source says '12.1 22.4'), 'No breach Clause(s)' on 676, 'Additional
    sanctions' on 63. `value_html` now also preserves the chips' clause links,
    which carry the Code-year context.
15. **A corpus-wide coverage check** ([`coverage.py`](coverage.py)) proves
    every visible token in every `<main>` region appears in its record (the
    only tolerated difference is the byte-uniform tab chrome). This is the
    check that found items 13–14. Determinism is also verified: two
    consecutive builds are byte-identical.

**v2.3 changes** — L1 is now intended to stand in for the HTML entirely:
9.  `meta` values are decoded to the author's value, not raw attribute bytes:
    `'&amp;lt;p&amp;gt;...'` becomes `'<p>Promotion of Arimidex</p>'`. Fixes
    1,154 records (60.7%), including 9 clause-label values.
10. Four previously-lost structures are now parallel offsets, `text` unchanged:
    `list_spans` (3,798), `links` with `href` (61),
    `images` with `src` (82), `line_breaks` (12,424).
11. Headings now use the SAME extractor as bodies. `txt()` and
    `text_and_spans()` disagreed, leaving `'1 st'` for `'1st'` in 2.54% of
    heading candidates and 11 mis-anchored spans.

**v2.2 changes** — see [`../verify/v22_build_report.md`](../verify/v22_build_report.md):
7. `emphasis_spans` on every section and pane — parallel offsets recording which
   words the source emphasised. 14,469 spans across 1,098 files (57.7%).
   Inline tags now close up instead of injecting a space, so `text` changed by
   −0.042% and all offsets were recomputed.
8. `heading_normalised` gains `CASE_TITLE`, `OUTCOME_BANNER` and
   `SUBHEADING_OR_CAPTION`. Null rate 82.8% -> 59.7%; the original six are
   unchanged and preserved as `heading_normalised_v1`.

**v2.1 changes** — see [`../verify/v21_build_report.md`](../verify/v21_build_report.md):
5. The 90-char heading cap is gone as a filter; length is evidence
   (`char_count`) and the gate is now a content signal (no terminal
   punctuation). Added 9,362 candidates, all at `low`.
6. Headings that open a quotation and never close it are demoted to `low`
   (`unbalanced_quote`): 212 demoted, 0 collateral.

**v2 changes** — see [`../verify/v2_build_report.md`](../verify/v2_build_report.md):
1. Heading detection is **graded, not binary**. Every plausible candidate is
   emitted with `heading_evidence` + `heading_confidence` (`high`/`medium`/`low`)
   and `heading_v1_would_emit`. L1 no longer picks a threshold; L2 does.
2. `source.integrity` flags 4 pages whose report pane belongs to a different
   case (kept, never repaired), plus 32 title typos and 2 Paragraph 17 cases.
3. Double-escaped entities resolved by targeted substitution: 121 files -> 0.
4. `abstract_boundary` is placed by a **summary-pane oracle** (94.2% of cases,
   `is_measured: true`); the regex fallbacks are marked `is_measured: false`.

```bash
python3 l1/build.py                                  # -> data/l1/records.jsonl (202 MB)
python3 l1/derive.py                                 # -> data/l1/derived.jsonl (10 MB)
uv run --with pypdfium2 python l1/build_pdf.py       # -> data/l1/pdf_records.jsonl (5 MB)
uv run --with jsonschema python l1/validate.py       # non-zero exit = build fails
python3 l1/coverage.py                               # non-zero exit = <main> content escaped capture
```

Build takes ~30s. Validation covers all 1902 records against
[schema.json](schema.json) (and derived.jsonl against
[derived_schema.json](derived_schema.json)) plus invariants a schema cannot
express.

**Status: 1902/1902 valid, one key signature across the whole corpus, build
byte-deterministic, `<main>` coverage 100%.**

## PDF records (schema `l1p.1`)

The 13 cases whose HTML report pane is wrong or absent
([`../investigation/pdf_needed.json`](../investigation/pdf_needed.json)) get a
verbatim record from their verified PDF: [`build_pdf.py`](build_pdf.py) →
`data/l1/pdf_records.jsonl`, validated against
[`pdf_schema.json`](pdf_schema.json). Extraction is pypdfium2 glyph geometry
(no ML, no OCR); mechanisms adapted from the sylico papers pipeline. Per page:
`ink_source` (print vs OCR-under-scan declaration), measured column structure
(`refused` in words when ambiguous — thresholds sit in the empty band between
the corpus's two populations: single-column ≤ 0.537 row support, two-column
≥ 0.983), and `lines` in reading order (column-major, full-width barriers)
with style/script spans as parallel offsets, plus a continuous `flow_text`
with line-break hyphens rejoined.

Verification: the build refuses to run on a PDF that no longer hashes to the
manifest's pinned sha256; the expected case number must appear in the flow
(13/13); and the flow is word-aligned against the case's HTML **summary pane**
— an independent witness that is correct even where the report pane is not —
scoring 0.92–1.0 (residuals are HTML-side defects: lost fi-ligatures, curly
quotes). COMPLAINT → RESPONSE → PANEL RULING → APPEAL appear in the correct
order on every file that has them. Build is byte-deterministic. L2 substitutes
these for the report pane where `derived.jsonl` says `report_pane_mismatch`,
and uses them as the report body for the summary-only cases.

**Start here:** [`../investigation/L1_VALUES_GUIDE.md`](../investigation/L1_VALUES_GUIDE.md)
documents every field with real worked examples — including the known-wrong
values — and every metric with what it does not measure. It is written to be
read instead of the HTML.

Also: [`../investigation/li_audit.md`](../investigation/li_audit.md) (why
`<li>`/`<br>`/`<img>` flattening is closed) and
[`../verify/rater_analysis.md`](../verify/rater_analysis.md) (per-rater
agreement across all three blind rounds).

## The contract

- **Every key on every record.** No conditional keys.
- **Absence is a value:** `null` = genuinely absent in the source, `""` =
  present but empty, `[]` = parsed and found nothing. Corpus-wide the `meta`
  object has 0 nulls and 14,829 empty strings — every page really does emit all
  22 `cludo:*` tags, many of them empty.
- **Types never vary.** `filename_case_numbers` is a list even with one element.
- **L1 never repairs a value.**

## Standardisation rule applied

L1 standardises **location and shape**. L2 standardises **value**. The test for
each piece of variation was *could the two locations ever disagree?*

**No → one slot.** A heading is a heading whether it sits in `<p>`, `<h3>` or
`<td>`. All become one `sections[]` entry with `carrier_tag` and
`carrier_emphasised` kept as attributes.

**Yes → one fixed slot per source, conflict left standing.** The survey showed
these demonstrably disagree, so none is preferred here:

| what | slots |
| --- | --- |
| case number | `identity.meta_case_number`, `.info_case_number`, `.h1_text`, `.filename_case_numbers`, `.index_case_number` |
| outcome | `outcomes.meta_clause_breach` / `.meta_clause_no_breach`, `.info_breach_clauses` / `.info_no_breach_clauses`, `.report_table_rows`, `.banner_headings` |
| dates | `dates.meta_received` / `.info_received` / `.report_trailer_lines` |
| appeal | `appeal.meta_appeal`, `.info_appeal_hearing` |

Worked examples, straight out of the built records:

```
AUTH-3303-1-20  meta_case_number = 'Anonymous complainant v Vifor '   parsed -> []
                h1_text          = 'AUTH/3303/1/20 - Anonymous complainant v Vifor'
                                                                      parsed -> ['AUTH/3303/1/20']
   L1 records both. L2 applies the fallback and emits one correct case_number.

AUTH-1841-5-06  meta_clause_breach    = '7.2 and 7.3'    <- the Panel
                meta_clause_no_breach = '7.2 and 7.3'    <- the Appeal Board
   The contradiction is preserved, not reconciled.
```

## `sections`

The shape is standardised; the content is not.

- `heading_text` is **verbatim** — `"PANEL RULING"`, `"PANEL RULING IN CASE
  AUTH/2546/11/12"`, `"Panel ruling"` all survive as written. All **3010**
  distinct heading strings observed in the survey are preserved.
- `heading_normalised` (and `heading_confidence`) are **no longer in the
  records** — they are verdicts, so they live in `derived.jsonl`, matched to
  sections by `(pane, index)`. The vocabulary and behaviour are unchanged:
  nothing is forced to fit, and the 3010 strings are deliberately not
  collapsed into a controlled vocabulary. That is an L2 decision.
- `char_start`/`char_end` slice `panes.<pane>.text` **exactly**; the validator
  asserts this on every section of every record.
- `heading_evidence` is `null` on an unheaded leading section (consistent with
  `carrier_tag`, already nullable).
- A pane with no headings and no text yields `[]`, so a stub's empty report pane
  contributes no sections (36 records).

## Known imperfect

Measured, not fixed. Detail in
[`../verify/v22_build_report.md`](../verify/v22_build_report.md).

1. **Emphasis is only ~35% recoverable.** 14,469 spans captured, but 347 of the
   531 `(emphasis added)` markers have no span — the source never encoded the
   emphasis they refer to. Not fixable in L1.
2. **Emphasis inside rulings is mostly typographic convention** — 427 spans,
   dominated by `et al` and `inter alia`. Filter before treating as salience.
3. **`SUBHEADING_OR_CAPTION` is 82.5% precise** (blind), bounded by
   `heading_confidence` rather than its own rule. `CASE_TITLE` 92.5%,
   `OUTCOME_BANNER` 95.0%, original six 90.9%.
4. **`CASE_TITLE` matches 6 sentences that open with a case number** (0.21%).
5. **`medium` carries ~20% reader-rejected noise**, by design. Genuine `high`
   over-firing measured at **0.00%** on 29 fresh cases.
6. **The `'Case Summary'` pane label is genuinely ambiguous** — raters split on
   whether site chrome counts as a heading. Tokenised as `CASE_SUMMARY` so it is
   droppable by name either way.
7. **Lists, line breaks, images and links are flattened or dropped**:
   11,481 `<br>`, 3,953 `<li>`, 1,476 `<ul>/<ol>`, 82 `<img>`, 65 `<a href>`
   (all external — no case cross-references exist to recover).
8. **Tables are NOT lost** — structure preserved in `tables[]` for all 592 files.
9. **6 false-positive sections (0.06%)** among the original six tokens: Word TOC
   `PAGEREF` artefacts and headings about *other* cases' rulings.
10. **15 files** have `PANEL RULING` only glued mid-sentence by a bad PDF→HTML
    conversion.

## Divergences from `survey.md`

Building L1 corrected two survey figures:

- **`caseItem` markup: 22 files, not 24.** The 2 extra hits were inside escaped
  `cludo:description` meta content, not real markup.
- **`PANEL RULING` inline-only: 15 files, not 46.** The survey's "standalone"
  regex required the element text to be *exactly* `PANEL RULING`, so it wrongly
  counted the `PANEL RULING IN CASE AUTH/…` files as inline.

Also fixed during the build: the survey's noted "trailing `<div` fragment"
caveat was a real slicing defect affecting **all 1902** report panes. The pane
now ends cleanly and 36 empty report panes read `""` rather than `"<div"`.
