# L2 — canonical case objects (SPEC)

> Status: IMPLEMENTED (`l2/build.py` → `data/l2/cases.jsonl`; shipped
> schema_version string is `l2.3` after the complainant-metadata revision).
> Section 2's version literals lag the shipped string where a revision
> changed fields without a bump; the schema file `l2/schema.json` is
> authoritative for shape.

One corrected, canonical JSON object per **case** (not per page: multi-case
reports contribute to several case objects). L1 standardised location and
shape; **L2 standardises value**. The boundary rule, held everywhere:

> **L1 never repairs. L2 repairs with receipts.**

Every canonical value carries its *basis*: which source won, under which
rule, with pointers to the losing values. L2 contains **facts about cases**;
anything that is a choice about an experiment (what to show a model, what to
ask) belongs to `bench/`, not here.

## 0. Inputs

`data/l1/records.jsonl` (l1.5) + `data/l1/derived.jsonl` (l1d.2) +
`data/l1/pdf_records.jsonl` (l1p.1) + `l2/adjudications.json` (reviewed
hand decisions) + [pending] `data/code/` (ABPI Code texts per year).
Nothing else — never the HTML/PDF directly.

## 1. Principles

1. **Every correction is a rule, not an edit.** `l2/build.py` is a pure
   function of its inputs. Irreducible judgment calls live in
   `adjudications.json` (schema in §8), each entry carrying the case, the
   field, the chosen value, and a one-line justification. The build fails on
   an adjudication that no longer matches its target (stale-fix detection).
2. **Receipts.** Canonical scalars are objects:
   `{"value": …, "basis": "<rule-id>", "sources": {slot: value…}, "note": …}`
   — `sources` holds the L1 slot values that disagreed (or a single slot when
   they agreed; `basis: "unanimous"`).
3. **Offsets, not copies.** Segments reference L1 pane text by
   `(file, pane, char_start, char_end)`. L2 stays small and the audit chain
   stays intact.
4. **Same verification regime as L1**: JSON Schema, one key signature,
   byte-deterministic double build, plus L2-specific audits (§7).

## 2. Record shape (field groups)

```
{
  "schema_version": "l2.2",
  "case_number": {value, basis, sources},        // ONE canonical number
  "source_files": ["AUTH-1806-3-06__AUTH-1809-3-06.html"],  // >1 never; multi-case
                                                 // reports SHARE a file
  "sibling_cases": ["AUTH/1809/3/06"],           // co-reported cases
  "title":   {value, basis, sources},            // corrected h1
  "subject": {value, basis, sources},            // hero h2 vs cludo:description
  "parties": {
     "respondent": {value, basis},               // canonical company name
     "complainant": {
        "verbatim": "...",                       // as stated
        "category": enum,                        // §4 controlled vocabulary
        "anonymous": bool, "contactable": bool|null
     }
  },
  "code_year": {value, basis},                   // int; §4 resolution for the 54 nulls
  "procedure": {                                 // flags, each bool
     "voluntary_admission", "abridged", "paragraph_17",
     "outwith_scope", "inter_company", "no_report"    // the 55 stubs
  },
  "dates": {"received": {value ISO, basis}, "completed": {value ISO, basis}},
  "verdicts": [                                  // the heart
     {"clause": "12.1", "code_year": 2016,
      "occurrence": 0,                           // a clause CAN be ruled twice in one
                                                 // case (different materials); unique
                                                 // key is (clause, code_year, occurrence)
      "clause_slug": "clause-12-prescribing-information...",   // from chip href when present
      "panel": "breach"|"no_breach"|null,        // null = attribution not established
      "appeal_board": "breach"|"no_breach"|null, // null = not appealed / no ruling
      "final": "breach"|"no_breach",             // never null: a verdict row only
                                                 // exists where SOME source states one
      "dual_ruling": bool,                       // l2.2: the PANEL ruled BOTH ways in this
                                                 // case, so no single Panel ruling exists
                                                 // (panel=null)
      "dual_ruling_appeal_board": bool,          // R28 stage 1: the same about the APPEAL
                                                 // BOARD (appeal_board=null). A separate
                                                 // flag, never a second writer of the one
                                                 // above -- see §5b
      "flipped_on_appeal": bool,
      "rulings": [ {...} ],                      // §5b: one entry per attributed prose
                                                 // ruling, with the sentence and its offsets
      "basis": rule-id, "sources": {...}}        // meta/info/table/banner/prose slots
  ],                                             // [] for outwith-scope and the 55
                                                 // no-report stubs -- absence is a value
  "appeal": {"appealed": bool, "by": "none"|"respondent"|"complainant"|"both",
             "basis": …},                        // vocabulary normalised, §4; "none"
                                                 // (not null) when unappealed
  "sanctions": {"undertaking": bool,
                "additional": ["Public reprimand", …],   // from rescued chips + meta CSV
                "clause_2_censure": bool, "basis": …},
  "segments": [                                  // canonical narrative structure
     {"kind": "abstract"|"complaint"|"response"|"panel_ruling"|
              "appeal_comments"|"appeal_ruling"|"trailer"|
              "summary_rendition"|"abstract_rendition"|"other",
      "ref": {"file", "pane": "summary"|"report"|"flow",   // "flow" = pdf_records.flow_text
              "char_start", "char_end",
              "text_length": int,                // len of the slice, so a consumer
              "text_sha256": "…"},               // detects a changed L1 underneath
      "source": "html"|"pdf",                    // pdf for the 13 substitutions
      "leakage_attest": {"clean": bool, "checks": {...}, "checked_at_build": true}},
  ],
  // Paraphrase inventory for bench P1: indices into segments[], NOT bare refs.
  // The summary pane and report abstract STATE the outcome (usually at the
  // end), so the quotable rendition is the LEADING allegation span of each —
  // cut before the first ruling language — carried as a segment with its own
  // attest (kinds summary_rendition / abstract_rendition). null = no clean
  // leading span exists for that rendition.
  "renditions": {
     "summary": int|null, "report_abstract": int|null, "pdf_flow": int|null
  },
  "entities": {                                  // redaction/contamination support
     "companies": [...], "products": [...], "people_roles": [...]
  },
  "quality": {"source_integrity": …,             // from derived
              "pdf_substituted": bool,
              "known_text_defects": [...],       // e.g. ligature loss
              "era": int, "report_chars": int}
}
```

## 3. The corrections catalogue (measured defect classes → rules)

| # | defect class | measured scale | resolution rule |
| --- | --- | ---: | --- |
| C1 | case-number field dirty (multi-case, party names, spacing) | 103 | parse via `CASE_NUM_RE`; h1 fallback; the Vifor case via adjudication |
| C2 | title-line typos (`ok_with_title_typo`) | 32 | h1 wins; report title line recorded in sources |
| C3 | `<title>` vs `<h1>` disagreement | 45 | h1 wins (title carries procedural suffixes/mojibake); both kept |
| C4 | hero h2 vs `cludo:description` disagreement | 46 real (804 files have EMPTY description — h2 is sole source there, not a winner; 1041 agree) | h2 wins (page-visible); semantic flips (e.g. AUTH-1877 'Alleged breach' vs 'No breach') → adjudication list |
| C5 | missing code year | 54 | infer from received date + Code commencement table (from `data/code/`); basis `inferred_from_date`, never silent |
| C6 | appeal vocabulary variants | ~8 forms | fold to {none, respondent, complainant, both}; casing/wording table in build |
| C7 | complainant vocabulary variants | dozens | controlled vocabulary §4; verbatim always kept |
| C8 | report pane belongs to another case | 4 | segments point at `pdf_records` (the PDF substitution) |
| C9 | summary-only / empty report | 9 | same |
| C10 | date formats | mixed | ISO-8601; both source slots kept |
| C11 | clause lists: bare numbers vs (year, clause) | all pre-chip eras | key every verdict by (code_year, clause); chip hrefs give slugs 2016+ |
| C12 | outcome slot conflicts (meta vs info vs table vs prose) | 322 both-ways clauses; 650 mixed cases | §5 resolution; Panel and Appeal Board NEVER collapsed |

## 4. Controlled vocabularies

- **appeal.by**: `none | respondent | complainant | both` (fold table from the
  8+ observed forms; 64 empty → `none` only when report text confirms, else
  adjudication).
- **complainant.category**: `anonymous | health_professional | company |
  employee_or_ex_employee | member_of_public | media | director_initiated |
  voluntary_admission | organisation | other` — mapped from the meta field
  (case-folded) + case text; `verbatim` always retained.
- **verdict values**: `breach | no_breach` only. "Upheld/not upheld" prose
  maps to these; anything unmappable is an adjudication, not a third value.

## 5. Verdict resolution (C12) — the one hard algorithm

Sources, in evidence order: (a) info-holder Breach/No-breach clause lists
(now complete after the l1.5 chip fix, with year-scoped slugs), (b) meta
clause CSVs, (c) outcome table rows, (d) banner headings, (e) ruling prose
(via derived segment boundaries). Rules:

1. Parse each source to a set of (code_year, clause, polarity).
2. **Panel vs Appeal Board attribution** — REWRITTEN in l2.2, after
   bench/review/DEFECTS.md D3 found the l2.1 rule wrong wherever two audits
   checked it. Prose attributes; lists do not. Where a case was appealed,
   `panel` comes ONLY from `panel_ruling` prose and `appeal_board` ONLY from
   appeal-side prose (`appeal_ruling` plus the appellant's grounds, which the
   l1d.2 `APPEAL_GROUNDS` boundary now separates from the Panel's ruling), and
   in each case only where the sentence's ruling BODY is the one being credited.
   The info/meta lists state the FINAL position and set `final` alone: the
   AUTH-1841 pattern (same clause in both polarity slots) is no longer oriented
   by who appealed, because doing so wrote the Appeal Board's outcome into
   `panel` on 75+ items and made T3's `overturned` class an artefact of dual
   listing. A clause the Panel's own prose rules BOTH ways, or that both lists
   name on an unappealed case, gets `dual_ruling: true` and `panel: null`
   rather than a chosen side.
3. Unappealed cases: `final = panel`. This is the one place a list still
   attributes, and it is not an inference — with no appeal there is no other
   body that could have ruled. Prose disagreeing with the list is a build
   WARNING (`prose_contradicts_unappealed_list`) resolved case by case in
   `adjudications.json`, never silently.
4. `flipped_on_appeal := appealed AND panel != appeal_board != null`.
5. Every verdict keeps `sources` showing what each location said. Nothing is
   discarded.

Validation: per case, the verdict set must be consistent with the banner
headings and outcome-table rows (cross-check, not source); clause-level
counts reconcile against the meta CSV cardinalities.

**What the row-creation rule COSTS, measured (DEFECTS R27, 2026-08-10).** Only
the outcome lists create rows, so a clause the Panel's prose explicitly rules
on but no list names gets no row and no label: **417 (case, clause) pairs
across 262 cases** (384 after conservatively removing precedent-risk and
renumbering-gloss neighbours; 12/12 of a random sample verified genuine, e.g.
AUTH/2175/10/08 *"No breach of Clauses 18.1 and 18.4 was ruled"*). These are
mostly no_breach, so the absence is **not label-neutral**. It is a documented
limitation of this design pending a stage-2 decision about whether prose may
create rows and on what witnesses — not a defect to be patched here, and
`rulings` (§5b) inherits it exactly: a prose ruling with no row has nowhere to
be recorded.

## 5b. Per-regard rulings (R28 stage 1)

A clause can be ruled on more than once in one case, in different regards —
different materials, different matters of a multi-matter report — and the
scalars `panel` / `appeal_board` can hold only one polarity each. Until l2.4
that fact was recorded only as a refusal (`dual_ruling`, panel=null), which
says a dual exists and nothing about what the two rulings were. `rulings` is
the record:

```
rulings: [{
  "body": "panel"|"appeal_board",
  "polarity": "breach"|"no_breach",
  "regard": <the report's own MATTER heading, verbatim>|null,
  "regard_ref": {"basis": "matter_heading", "char_start", "char_end"}|null,
  "quote": <the ruling sentence, verbatim>,
  "char_start", "char_end",                  // offsets in the pane, not the segment
  "file", "pane": "report"|"summary"|"flow",
  "segment_kind": "panel_ruling"|"appeal_ruling"|"appeal_comments"|null,
  "source_frame": <which reader frame attributed it>
}]
```

Rules:

1. **Every attributed prose ruling gets an entry, including single ones.** A
   clause ruled once carries a one-entry list; `len(rulings) > 1` is then the
   per-regard fact, and no consumer needs a special case. `[]` is legitimate:
   the outcome lists alone made the row.
2. **Attributed, not present.** An entry exists exactly where the statement
   moved `prose_panel_*` / `prose_appeal_board_*` — so a precedent citation, a
   refused body, an irrealis sentence in the appellant's grounds and an
   unregistered undertaking recital all contribute nothing, the same way they
   contribute nothing to the scalars. The list is what the build READ.
3. **The quote is the receipt and it is re-sliced.** `l2/validate.py` cuts
   `pane[char_start:char_end]` again, requires it to equal `quote` byte for
   byte, and then re-reads it with its own token walk (`v_ruling_statement`) to
   confirm the polarity and clause — a different implementation from the
   builder's frames, per the F1 rule.
4. **`regard` is quoted, never composed.** It is the report's own matter
   heading covering the sentence (`l2/build.py matter_headings`: a heading
   that carries an enumerator, has no terminal punctuation, is 1–40 words, is
   not page furniture, and is immediately followed by a structural boundary —
   642 headings over 216 files). Where the report names no matter, `regard` is
   null and the quote carries the burden. Single-matter reports and the 13 PDF
   substitutions (whose flow text has no section structure) are all null.
5. **`source_frame`** names the reader frame that attributed the statement
   (`passive`, `active`, `uphold`, `coordinated`, `passive_that_clause`,
   `active_that_clause`, `not_warranted`), or `dual_read_registry` for a half
   no frame can read and a reviewer supplied with offsets. Frames are tried in
   a fixed order; the first to read a (polarity, clause) owns it.

**The two dual flags are per AXIS and must stay that way.** `dual_ruling` is
about the Panel, `dual_ruling_appeal_board` about the Appeal Board. They are
not folded into one because they refuse different things: on AUTH/1841/5/06 the
Panel ruled Clause 7.2 once (so the T1 label is sound) and the Appeal Board
ruled it both ways (so no panel→board transition exists for T3). One flag
would have deleted ~40 correctly-labelled T1/T2 items to describe an
appeal-side fact. `dual_ruling_appeal_board ⇒ appeal_board is null`, and the
receipts must show both polarities attributed to the Board; both are validator
invariants.

**Duals are READ, not pattern-decided.** Both axes gather candidates two ways —
what the reader attributes both ways, and what a deliberately looser screen
(`dual_screen`: no precedent guard, no body attribution, plus the
'... and ruled accordingly' frame) sees both ways — and every candidate must
appear in `l2/build.py DUAL_READ` with a reading. 82 rows today — 27 panel (24
read as not-dual, 3 genuine and adjudicated) and 55 appeal (40 dual, 15 not). `check_dual_read_coverage` refuses the build on an unread candidate
AND on a registry row that is no longer a candidate. A reading that says *not a
dual* changes nothing about the row; a reading that says *dual* sets the
appeal-axis flag directly, but on the PANEL axis it must go through
`adjudications.json` — a code table may record a reading, overwriting a
published Panel ruling is an adjudication's job.

## 6. Segments and the leakage attest

Segments are assembled from `derived.jsonl` heading verdicts (confidence ≥
medium for canonical boundaries; `abstract_boundary` for the abstract span)
over L1 pane text — or over `pdf_records` flow for the 13 substitutions.
The attest, recomputed by the validator on the SLICED TEXT (never trusted
from the builder — no shared witness with the layer it audits):

```
leakage_attest.checks = {
  "no_ruling_language":  no RULING_RE-class match,
  "no_outcome_banner":   no banner-heading string inside the span,
  "no_outcome_table":    no outcome-table row text inside the span,
  "outside_abstract":    span ∩ abstract span = ∅  (report pane only),
  "no_sanctions_text":   no additional-sanctions chip text,
  "no_outcome_heading":  no outcome-stating HEADLINE inside the span
}
clean := all checks pass
```

`complaint`/`response` segments are the only kinds bench T1/T2 may quote;
their attest failing means the item is not generated — never trimmed by hand.

**`no_ruling_language`, stated so both readings can be written from it**
(DEFECTS R24, 2026-08-10 — the previous wording said only "RULING_RE-class
match", so the validator's only way to implement it was to copy the builder's
regex, and both copies carried the same three holes). A span carries ruling
language when either frame appears **inside one sentence**, where a `.` ends a
sentence only if the next character is not a digit — a decimal point inside a
clause number does not:

- **F1** a ruling body (`Panel`, `Appeal Board`) followed within 90 characters
  by a ruling verb (`rul*`, `consider*`, `noted`, `accept*`, `uph*`, `decid*`);
- **F2** a breach statement (`[no] breach(es) of Clause(s) N` / `[the] Code
  [N]`) followed within 70 characters by `was`/`were` — optionally one adverb —
  `ruled`.

An **F2** hit is exempt when a case number that is not the source file's own
sits within 120 characters of it: that is another case's ruling quoted as
precedent, which complaints and responses do legitimately. **F1 is never
exempt** — it names the ruling body, and a party restating another body's
ruling is the D3 hazard. `verify/ruling_battery.py` is the case list all three
implementations (builder, validator, `bench/generate.py`'s tripwire) are held
to.

**`no_outcome_heading`** (DEFECTS R26, same day). `no_outcome_banner` matches
the headings `l1/derive.py` classified as banners, and its rule is three
literal strings (`NO BREACH`, `BREACH OF THE CODE`, `BREACH OF CLAUSE`), so
the publisher's other word orders slip through: `Breach of undertaking
Clause 2`, `VPRIV press release breach Clause 2`. The check refuses a span
containing a heading that (a) sits above the first body section, (b) is a
headline rather than a sentence — L1's own `has_terminal_punctuation` receipt
— and (c) names both a breach and a clause number. (c) is what separates the
outcome from the ~55 breach-of-undertaking SUBJECT lines, and (b) what
separates it from party statements in the abstract's prose.

## 6b. Showable metadata — the single source of truth

Consumers must not rediscover which fields leak outcomes (the `subject` can
literally read 'No breach of the Code'; `procedure.abridged` /
`voluntary_admission` / `outwith_scope` imply or ARE labels). The allowlist
of fields a benchmark may show a model is defined HERE and imported by
`bench/generate.py`, never re-derived:

SHOWABLE = case_number, title, parties.respondent, complainant.category,
complainant.anonymous, complainant.contactable, code_year, dates.received.
Task-specific exceptions are declared per task in `bench/DESIGN.md` §5 (T3
additionally shows the Panel ruling under test and the appellant — that is
the task). Everything else — verdicts, sanctions, appeal, subject, all
procedure flags, dates.completed, quality — is never shown.

## 6c. Nullability rules

- Receipts may carry `value: null` only with a basis explaining why
  (`unresolved_pending_code_dates`, `refused_no_measured_boundary`, …).
- `verdicts: []` and `segments: []` are legitimate for outwith-scope and
  no-report stubs.
- `verdicts[].final` is never null (§2); `panel`/`appeal_board` may be.
- `verdicts[].rulings` is `[]` when no prose ruling was attributed — the
  outcome lists alone made the row. `regard`/`regard_ref` are null together,
  and null means the report names no matter for that ruling (§5b).
- `renditions.*` null = no leakage-clean leading span exists.
- `complainant.contactable` null = not stated (meta says 'non-contactable'
  on ~38 pages; phase-2 fills those, the rest stay null).

## 7. Verification regime

1. JSON Schema (`l2/schema.json`) + one key signature over all cases.
2. Byte-deterministic double build.
3. **Receipts audit**: every canonical value's `sources` non-empty; every
   `basis` is a registered rule id or adjudication id; every adjudication
   used at least once (dead-fix detection).
4. **Attest recheck** on sliced text, independent implementation (§6). Same
   discipline for `rulings` (§5b.3): every quote re-slices from the pane and is
   re-read by the validator's own token walk, never the builder's frames.
5. **Reconciliation counts**: Σ case objects = 1,902 + (multi-case expansion);
   verdict polarity totals vs the L1 meta CSV totals; appeal counts vs the
   fold table; the 13 PDF substitutions all present and marked.
6. **Coverage descendant**: every clause number appearing in any L1 outcome
   slot appears in some verdict's `sources` (nothing silently dropped).

## 8. adjudications.json

```
[{"id": "adj-0001", "case": "AUTH/3303/1/20", "field": "case_number",
  "value": "AUTH/3303/1/20", "rule_displaced": "meta_first",
  "justification": "meta field holds party names; h1 carries the number",
  "reviewed_by": "tim", "date": "2026-08-02"}]
```

Small, reviewed, append-only. The build refuses an adjudication whose target
value has changed since review (sha of the source slots recorded).

**Fields a reviewed entry may target.** Each is pinned to a sha of the
evidence the reviewer read, which is not always the same evidence:

| field | value | pinned to | what it displaces |
|---|---|---|---|
| `case_number`, `code_year`, `dates.received`, `dates.completed` | the value | the canonical's `sources` | a resolution rule |
| `appeal.by` | `respondent`/`complainant`/`both` | the appeal object's `sources` | the PMCPA's own slot |
| `verdicts[<clause>]` | a polarity, or `dual` | the row's receipts | the outcome LISTS (`final`, and `panel` on an unappealed case) |
| `verdicts[<clause>].code_year` | a year | the row's receipts | the year arbitration (R20) |
| `verdicts[<clause>].attribution` | `{panel?, appeal_board?}`, each a polarity or `dual` | the row's receipts | nothing — it FILLS what prose-only attribution could not read |
| `verdicts[<clause>].clause` | the corrected clause number, or `null` to delete the row | the four outcome slots, the two chip lists AND the clause numbers the report names | the hand-typed slot, BEFORE any row is built |
| `parties.complainant.category` | a §4 category value | the complainant record's `sources` | the meta-slot vocabulary, or a prose reading |

Four of these are newer than the table above them and carry their own
discipline:

* `parties.complainant.category` (the closing round) moves ONLY `category`,
  writing the adjudication id into `field_basis.category` as well as `basis`
  and leaving `anonymous`, `contactable` and their bases exactly as the
  readings folded them — an adjudication that could reach those would rewrite
  a whole complainant from one reviewed sentence about their role. The
  complainant is resolved per PAGE and published per CASE, so a co-reported
  pair needs one row EACH (adj-0155/0156) or the two cases publish
  contradictory categories for one document. `procedure.inter_company` is
  derived from the category and so is derived per case, after the
  adjudication.

* `.attribution` (Q3 / R6 / R28 stage-2) lands in a SEPARATE `attribution_basis`
  on the row, not in `basis`. `basis` has to keep saying which rule produced
  the row, because `check_dual_read_coverage` reads it to exempt a
  rule-decided dual — overwriting it made the build refuse three rows that
  had already been read. It never touches `final`: it says who ruled what,
  not what the case's outcome was.
* `.clause` (N2) is applied to the slot-derived token sets *before*
  `resolve_verdicts` runs, so the prose attribution, the year arbitration,
  the receipts and the ruling records are all computed for the clause the
  report actually ruled — as if the slot had been typed right. Prose-derived
  structures are never rewritten: they already name the right clause. Every
  correction is written to `data/l2/clause_slot_corrections.jsonl`, which
  `bench/generate.py` reads to book a durable exclusion row for a DELETED
  row's item-candidates and the item-id migration ledger reads to see a
  renamed item as renamed. Two guards refuse: a rename onto a clause the slots
  already state (it would merge two rows), and two siblings of one shared
  page correcting the same token differently.
* Independently of any adjudication, the build REFUSES on a published verdict
  row whose clause the case's own text never names, unless the row is
  declared in `CLAUSE_WITNESS_READ` with the reading. 28 of 7,696 rows were
  in that state when the check was written; 20 were corrected, 7 deleted and
  1 kept.

## 9. Build order

1. Freeze this spec (open decisions below) →
2. `l2/build.py` skeleton: identity + vocabularies + dates (C1–C7, C10) →
3. Segments + attest (§6) →
4. Verdict resolution (§5) — hardest; expect the adjudication file to grow
   here →
5. Entities + renditions + quality →
6. `l2/validate.py` + audits →
7. Hand-generate the 10 DESIGN.md pressure-test items straight from
   `cases.jsonl`.

Parallel track (independent): `scrape/fetch_code.py` → `data/code/` — ABPI
Code texts per year (2016/2019/2021 interactive pages; earlier years as
PDFs), with the standard manifest discipline. Needed by C5 and bench T1/T6.

## 10. Decisions and open questions

1. **DECIDED (2026-08-02): one L2 object per case**, `sibling_cases`
   cross-referenced. Consequence for bench: siblings share report text, so
   they must always be assigned to the same split (recorded in DESIGN.md §6).
2. **DECIDED (2026-08-02): respondent canonicalisation happens in L2** — the
   redaction variants and memorisation probes depend on the entity
   inventory, so it cannot wait for bench time.
3. **DECIDED (2026-08-02): verdicts carry an `occurrence` key** — a clause
   can be ruled twice in one case, and deterministic downstream item ids
   collide without it (found by the bench harness as a consumer).
4. Open: `people_roles` in entities — extract now (cheap via meta
   complainant) or defer NER-style extraction until a benchmark needs it?

Phase-1 build measurements that correct this spec's briefed numbers:
multi-case files are **97** (not 104), contributing 199 cases → 2,004 case
objects; complainant appeals **85** (not ~49), respondent 202, both 6,
unresolved 67; `Sanctions applied` carries exactly one value corpus-wide
('Undertaking received') — presence-only information.
