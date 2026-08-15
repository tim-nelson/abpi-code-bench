# Item-bank defect register — combined audit findings (2026-08-02)

Two independent Opus audits (60 items T3/prose-focused, seed 42; 45 items
random quality, seed 7) read against the primary ruling text, then an
adversarial re-audit of the complainant metadata (25 items sampled, 9 wrong)
which produced D6–D8. Mechanical sweeps corpus-wide where noted. Status: D1–D4
and the D5/R1/R2 items marked FIXED below are done (l1d.3 / l2.3 / bench,
2026-08-02); D6–D8 are done (l2.4, 2026-08-02). **Cleared to spend 2026-08-02**
after audit round 3 — see the clearance entry at the foot of this file, which
supersedes the "not cleared" wording that stood here until 2026-08-05.
R3/R8/R10/R11/R12/R13 landed on 2026-08-05 and re-close the gate: the protocol
at the foot gates spending on a re-audit after fixes, and that round has not
been run. Those six changed no label, split, extract or item_id (checked against
the pre-fix item copies in `bench/subsets/`), but they did change 74 prompts.

## Current freeze — assurance repair complete (2026-08-12)

This dated entry supersedes the bank census immediately below while preserving
the register's chronology. Assurance Batch 6 is complete: 162 cases / 1,116
bundle-validated items, 312 Luna flags, then 85 confirmed / 117 already
registered / 110 refuted / 0 uncertain by Sol. The compact archive is
`audits/2026-08-12_assurance_batch_06.md`; the repair and complete verification
record is `audits/2026-08-12_assurance_repair_and_freeze.md`.

**RESOLVED in this pass:** bounded ruling-tail, bare-terminal, `accordingly`,
missing-preposition and exact stray-period recall; exact foreign-recap and
hypothetical refusals; 12 closed-list prose-only verdict rows; appeal-axis dual
state; response/complaint boundary and matter-heading containment; exact
response-attest false positives; PDF-owned date receipts; the AUTH/2461 date
typo; reviewed case/clause Code years; respondent, slug, complainant role,
contactability and explicit-anonymity metadata; undotted-clause supplementary
text; T5 receipt loading and CASE/0302 setting curation. Each semantic judgment
is exhaustive-by-guard or SHA-pinned; independent validator registries have
dead-entry checks.

Frozen artifacts: **2,004 L2 cases / 7,702 verdict rows / 182 adjudications**;
**10,497 main items** (T1 4,599 / T1-triage 5,553 / T3 345) and 7,618 main
exclusions; **29 T5 items** and 2,605 T5 exclusions. Against the 2026-08-11
freeze, 12 verdict rows were added and none removed, no existing `final` value
changed, and the full field-level diff found no unexplained change. L1/L2/bank
validators, ruling/candidate/date/vocabulary/Code-year/PDF checks, score/probe
self-tests, T5 determinism and second-build hashes all pass.

Main-bank identity accounting is also closed: 186 label/split-preserving
migrations (184 boundary, two Code-year) take the migration log 410 → 596;
after those pairs, there are 304 true additions and 12 retirements, each with a
durable exclusion. No published-board absence or score is newly introduced,
but 28 archived T3 prompts and 19 Phase A prompts are input-stale and must not
be described as fresh answers to the repaired prompts.

**Still open by design:** the registered multi-case, no-report, unresolved
Code-year, prose/list contradiction and PDF reading-order limitations. T4
remains withdrawn. Git-clone-only reproduction and ordinary GitHub publication
are also not solved by a semantic repair: see `../../docs/REPRODUCIBILITY.md`
for the ignored source snapshot and existing >100 MiB history blocker.

Current bank: 10,045 items (T1 4,303 / T1-triage 5,426 / T3 316) over 2,004 L2
cases — **superseded by the 2026-08-09/10 repair waves: now 10,027 items
(T1 4,298 / T1-triage 5,415 / T3 314), 310 items renamed via
`bench/id_migrations.jsonl` (261 wave-2 + 49 prose-promotion), 18 removed
with exclusion rows (14 + 4); see the 2026-08-08/09/10 sections and the
prompt-condition entry**; **7,096** excluded item-candidates enumerated in `bench/exclusions.jsonl`
(was 6,841 — R13 added the 255 rows that were being dropped without one; no
item changed, and the item_ids are byte-identical across that fix).
Chain verified byte-deterministic (`l1/derive.py` → `l2/build.py` →
`bench/generate.py`, run twice, all five artefacts sha-identical) with all three
validators green. l2.4 changes metadata only, and that is checked rather
than asserted: the bank was regenerated from the PRE-fix `cases.jsonl` and
compared to the post-fix bank field by field over all 10,045 items. The same
10,045 item_ids, and the only values that differ anywhere in the two files are
`complainant_anonymous` (4,518 items), `complainant_category` (990),
`complainant_contactable` (468) and `tags` (357 items, all additions:
`burden_of_proof_candidate` +335, `anonymous_complainant` +31, none lost).
Extract text, labels, splits, clause refs and provenance are byte-identical.

## What is verified GOOD (keep; do not destabilise)

- Provenance character-exact: 105/105 sampled extracts re-slice and
  sha-match. Zero defects.
- **SUPERSEDED 2026-08-10 by R24 — the "zero" was measured with a pattern
  blind to decimal clause numbers; true count was 4 extract items + 2
  rendition items, bounded by an independent decimal-safe sweep.**
  Original claim: **Ruling-language leakage: zero, corpus-wide** (all 11,588 T1/T1-triage/T4
  extracts) — the attest + tripwire layers work.
- Clause texts all correct where present; the `(28.6)` prefixes are the 2021
  Code's OFFICIAL cross-references to pre-2021 numbering, not artifacts.
- Sibling-split rule holds corpus-wide; no case straddles splits.
- The ~10k `verdict_unappealed` T1/T1-triage label spine is sound (20/22
  sampled correct across both audits; the negated-prose "contradictions" are
  tokenizer noise, labels right).

## Defects, priority order

### D1 — T4 `outwith_scope` is a keyword false friend. REMOVE the task for v1.
`l2/build.py` OUTWITH_RE matches bare "outwith" — ordinary Scottish-English
in these reports ("outwith the licence/SPC/SOP"). All 19 positive items are
false (some carry BREACHES); the 75 genuine outwith cases (status literally
'No breach, outwith the scope of the Code') have no quotable complaint (0–47
char reports) so the true class is structurally absent. On one sampled item
the trigger word sits INSIDE the extract — a learnable spurious cue.
FIX: procedure.outwith_scope from the status line (`cludo:status`), not the
keyword; T4 dropped from the bank pending redesign (DESIGN §2 note).

### D2 — RESPONSE boundary misses the post-2023 heading form. Fixes 700 items + the holdout.
`l1/derive.py` NORMALISE has `^RESPONSE\s*$` only; "BAYER'S RESPONSE" etc.
never matches. Effects: (a) 700 complaint-only items (563 T1-triage, 137 T4)
contain the defence — spec violation, sometimes answer-determinative;
(b) T1 coverage collapses in the recent era: 63 (2023) → 3 (2024) → 0 (2025),
so the post-cutoff contamination holdout is unbuildable.
FIX in derive NORMALISE: also match `^[A-Z][A-Z'’& .-]{0,40}('|’)?S RESPONSE\s*$`
(and plain `^RESPONSE FROM ...$` variants if observed). Chain: derive →
L2 → items rebuild.

### D3 — Appeal attribution must be prose-only; back-fill corrupted T1 and T3.
Confirmed by both audits. The site's outcome lists state the POST-APPEAL
position; L2 back-filled `panel` from them (`verdict_upheld_appeal_prose*`,
orientation rules), so: T3 `overturned` (87) is an artefact of dual-listing
(10/10 sampled wrong — dual rulings on different materials, not flips); T3
`upheld` hides real flips (5/25 wrong, +2 ambiguous); 75+ T1 items carry the
Appeal Board's outcome as the "Panel ruling" (14/14 and 3/3 sampled wrong).
FIX in `l2/build.py` verdict resolution:
- `panel` ONLY from panel-ruling prose; `appeal_board` ONLY from
  appeal-section prose; lists ONLY set `final`. No orientation guessing.
- The prose tokenizer must handle negation ("did not consider that there had
  been breaches of…" is NOT breach-prose) and enumerated lists
  ("no breach of Clauses 3.1, 3.2, 7.2, 7.4 and 7.10").
- Dual-ruled (clause stated in both lists, unappealed) → no binary T1 item
  (excluded, reason recorded), not resolved-to-breach.
- T3 eligibility: appealed AND panel non-null AND appeal_board non-null AND
  the Board demonstrably considered THIS clause. Never-appealed clauses on
  appealed cases are not T3 items.
- Panel-ruling segments must END at `APPEAL BY …` / `APPEAL FROM …` headings
  (add to the derive boundary vocabulary alongside APPEAL_BOARD_RULING) —
  33/35 T3 extracts currently run into the appellant's grounds.
- Known list-error adjudication: AUTH/2386/2/11 clause 12.1 is no_breach
  (site lists say breach; Panel prose explicit). Audit the remaining 82
  unappealed prose-contradiction items the same way (1 wrong in 11 sampled).

### D4 — Undeclared multi-case pages contaminate extracts and duplicate labels.
Multi-case reports are detected by `__` filenames only. Single-named pages
holding several cases (AUTH/2629 contains Chiesi + Bayer responses;
AUTH/2294 splices AUTH/2296+2297 complaints) have `sibling_cases: []`, so
extracts mix parties and page-level outcome lists copy to all embedded
cases. Also 1,164 items (9.7%) are byte-identical sibling duplicates; where
a joint ruling bound only one respondent (AUTH/2160/2161 Roche-only), one
copy is wrong.
FIX: detect `CASE AUTH/...` banners INSIDE report panes (derive-level
segmentation by case), attribute segments and rulings per case, exclude
cases whose text cannot be separated; de-duplicate identical sibling items
(emit once, list both case numbers) before any scoring.

### D5 — smaller, after D1–D4
- **FIXED (l2.3)** `complainant_contactable` null on ALL items → the
  burden-of-proof trap class is now selectable: stated on 712 cases / 4,385
  items (43.7%), read from the report opening ('an anonymous, non-contactable
  complainant'), null elsewhere rather than defaulted.
- **FIXED (l2.3)** Metadata falsehoods. Complainant category / anonymous /
  contactable are now derived PROSE-FIRST from the report opening with the meta
  token as fallback, each field with its own basis and the quoted sentence in
  `sources.prose_quotes`. AUTH/3873/01/24 now reads anonymous, non-contactable,
  health_professional — as its extract always said. Corpus-wide against an
  independent prose test: anonymous-FALSE-but-prose-says-anonymous 2,062 → 0
  items; category `other`/`anonymous` against a stated self-description
  2,488 → 0 (2,220 of them health professionals).
  - **THE "F1" CLAIM ABOVE WAS CIRCULAR — CORRECTED IN l2.4 (2026-08-02).**
    The "independent prose test" was not independent: it re-used `l2/build.py`'s
    own regexes as its oracle, so it could only ever report that the builder
    agrees with itself. Both zeroes were true and meaningless. The adversarial
    re-audit read the same fields against the primary text and found 9 of 25
    sampled items wrong — a rate the "corpus-wide 0" had concealed. The lesson,
    recorded here because it is cheap to repeat: **an acceptance test that
    imports the builder's patterns tests nothing.** l2.4's acceptance test
    (D6–D8 below) is a separate implementation — sentence-split, tokenise, scan
    word LISTS with word-distance anchoring, no alternation regex anywhere —
    reading `data/l1/records.jsonl` and comparing against `bench/items.jsonl`.
    It disagrees with the builder in 6 classes, and each class is explained and
    counted rather than tuned away.

### D6 — `complainant.anonymous` read silence as False. FIXED (l2.4), 4,487 items.
The field was two-valued: True when the word 'anonymous' appeared in the meta
slot or the h1, False otherwise. "Otherwise" included the 2023+ site metadata,
which is the bare word 'Complainant' and states nothing at all, so the bank
published "Complainant anonymous: no" on ~1,000 cases whose sources never said
so — an invented fact in a field a model is asked to reason from.
FIX: tri-state. True needs a STATEMENT ('an anonymous complainant', 'wished to
remain anonymous', or the meta/h1 token 'Anonymous'); False needs a statement of
NAMING ('a named, contactable complainant' — 19 files; or a complainant slot
naming companies the corpus knows as respondents, i.e. an inter-company
complaint — 267 cases); anything else is null and renders 'not recorded'.
Measured: a bare `\bnamed\b` is a false friend 168 times over ('a named
hospital', 'the CCGs named by the complainant'), so naming requires one of the
two attested complainant-describing forms. One file states both naming and
anonymity (AUTH/3464/1/21, 'A named contactable complainant … and wished to
remain anonymous'); anonymity wins and the note records the conflict.
Cases 715/1,289/0 → 721/285/998 (true/false/null); items 4,185/5,844/0 →
4,216/1,326/4,487. The audit predicted ~2,275 items of false→null; the actual
4,518 is larger because the audit counted the classes it sampled, while the
rule applies equally to every pre-2023 role token — 'Pharmacist v AstraZeneca'
names a role, not a person, and states nothing about anonymity either way.

### D7 — contactability recorded the state on RECEIPT, not the final state. FIXED (l2.4), 344 items.
48 files state contactability twice — 'an anonymous complainant, who was
originally contactable but later became non-contactable', 'The complainant has
now become non-contactable' — and the builder kept the FIRST statement, so the
bank said 'contactable: yes' about complainants the PMCPA could no longer
reach. That is the exact fact the burden-of-proof trap class turns on.
FIX: all statements are collected in document order and the transition decides.
Every one of the 48 describes the same direction (contactable →
non-contactable) and the corpus attests no transition the other way, so a
disagreement resolves to False; both statements are quoted
(`prose_quotes.contactable` = the statement that decided it,
`prose_quotes.contactable_superseded` = the one it overrode) and the note says
so. Position alone would have been wrong on AUTH/2816/12/15, which states
non-contactable first and 'initially contactable but later could not be
contacted' second.
53 cases / 344 items resolve by the final-state rule; 52 cases / 332 items flip
True → False. `burden_of_proof_candidate` (anonymous AND contactable false)
1,821 → 2,156 items over 300 cases, and all four cases the audit named
(AUTH/3497/3/21, AUTH/3918/6/24, CASE/0257/08/24, AUTH/3903/5/24) now carry it.
- **Beyond the brief, flagged:** 'uncontactable' (one word) is the same
  statement in the corpus' older register — 'An anonymous and uncontactable
  general practitioner', 23 files, 2008–2025 — and was not read at all, which
  left those cases' contactability null. It is now read alongside
  'non-contactable'. Separable and separately counted: 16 cases / 70 items go
  null → False on that spelling alone, and 2 of the 48 transition files
  (AUTH/3451/1/21, AUTH/3503/4/21) need it to be seen as transitions at all.

### D8 — role extraction: window, frame, vocabulary, and `anonymous` as a category. FIXED (l2.4).
(a) **Window.** The self-description capture ran to 90 characters and cut the
role in half ('described him/herself a[s a cardiac specialist]'); it now runs to
the sentence-clause end or 200 characters. The OTHER 90-character constant, the
anchor window that decides whether a token is about the complainant, split the
corpus' own standard opening: in 'An anonymous, contactable health professional
who described themselves as a general practitioner complained about …',
`contactable` is 84 characters from `complained` and `anonymous` is 95, so the
same sentence had its contactability read and its anonymity refused
(AUTH/3170/3/19, AUTH/3202/6/19 published 'not recorded' while their first line
said 'An anonymous … complainant'). Also 200 now. 6 cases gain anonymity, 12
gain contactability, all hand-checked.
(b) **Frame.** Added 'who stated that he/she was a <role>'. The corpus also
writes it without the relative pronoun ('The complainant stated that he/she was
a consultant oncologist' — AUTH/2880/10/16, a role the audit listed as missed),
and the bare form matches 77 times, mostly narrative ('stated that she was
extremely upset'). Two grammatical guards, not a keyword list: the copula must
be followed by a DETERMINER (a role is a noun phrase, so 'was extremely upset'
and 'was visited by' are refused), and the SUBJECT must be the complainant —
either the relative pronoun or the complainant noun itself — which refuses 'If
visitors to the website stated that they were a health professional …'
(AUTH/3303/1/20). Result: 18 captures over 16 files, every one a stated role.
(c) **Vocabulary.** cardiac specialist / cardiac expert / fertility specialist /
optometrist / healthcare practitioner / 'heath professional' (the source's own
typo, tolerated not corrected) → health_professional; ex-contractor and
contractor → employee_or_ex_employee, in the prose rules and in the meta
vocabulary. `specialist` is admitted bare because all 6 corpus
self-descriptions using it are clinical; `expert` is bound to 'cardiac expert'
because it appears exactly once and an unqualified expert is a role in no field.
- **Necessary precision guard.** Widening the capture to 200 characters made
  LIST ORDER dangerous: 'described themselves as an ex-employee of Cephalon UK
  [who] complained that a medical affairs manager … was not a qualified doctor'
  read as health_professional because 'doctor' outranked 'employee' in the rule
  list. The rules now resolve by POSITION in the capture — the role is the head
  of the phrase — with list order only breaking ties. Six cases were wrong that
  way (AUTH/2409/6/11, AUTH/2504/5/12, AUTH/2722/7/14, AUTH/3468/2/21,
  AUTH/3495/3/21, AUTH/2697/1/14-class); position fixes all six and changes
  nothing the 90-character capture got right.
(d) **`anonymous` is not a category.** The meta rule that mapped the token
'Anonymous' to `complainant.category` is removed and the value is out of the
`l2/schema.json` enum — cheaply feasible, because it is an enum VALUE and the
key set is untouched, so the SPEC §7.1 one-key-signature contract is unaffected
and `bench/item_schema.json` (which types the field `string|null`, no enum) did
not need to change. 185 cases / 947 items move: 178 to `other` (the anonymity
boolean carries the fact), 5 to health_professional and 2 to
employee_or_ex_employee where the prose states a role.
Category overall: health_professional 802 → 818 cases, other 235 → 400,
employee_or_ex_employee 149 → 153, `anonymous` 185 → 0.

### l2.4 acceptance — what the INDEPENDENT oracle says
`python3 bench/review/accept_complainant_metadata.py` — re-runnable, exits
non-zero if any named failure regresses or any disagreement loses its
explanation. Separate implementation (see the F1 correction above), reading raw
L1 report panes and comparing 10,045 items: anonymity agrees on 8,412, contactability on
9,665, category on 3,125 of the 3,427 items where the oracle read a role at
all. All 9 items the re-audit named are corrected AND confirmed by the oracle
independently. Every disagreement class is accounted for:
- 190 cases / 1,195 items — anonymity False from the meta slot naming the
  complainant (inter-company). The oracle reads prose only and cannot see it.
- 46 cases / 338 items — anonymity True from the meta/h1 token 'Anonymous'.
  Same reason.
- 44 cases / 321 items — the builder's contactability quote is verbatim in the
  pane; the oracle's own (deliberately different) opening window missed it.
- 11 cases / 84 items — the builder read anonymity prose the oracle did not.
  Nine are genuine ('wished to remain anonymous' ×4, 'an anonymous source', 'an
  anonymous non-contactable employee', …); one is R8 below; one is R9 below.
- 3 cases / 29 items — the builder read the substituted PDF flow (the HTML pane
  is empty or belongs to another case); the oracle reads the HTML pane.
- 1 case / 20 items — AUTH/3625/3/22: the oracle read a self-description at
  char 2,295, inside the COMPLAINT body, past the opening boundary at 2,283.
  The builder reads the opening only, by spec.
The category classes that remain are the oracle's own false positives, verified
by hand on examples: role words in a narrative tail ('stated that he was
shocked at what the company allowed its representatives to get away with'), and
the documented `complainant_meta_structural_category` rule (8 cases where the
meta slot says Director and the prose says health professional; the structural
value stands and the role goes in the note).
- Union-respondent display ("Roche and Chugai") on single-respondent items.
  **OPEN.**
- **FIXED (bench)** Provenance chunks ordered by kind, not document order —
  `quotable`/`pick_segments` now sort by `(file, pane, char_start, char_end)`.
  834 items in the rebuilt bank are re-ordered by the change: 521 T1 (the audit
  counted 522 on the pre-fix bank; the difference is items this round removed
  for other reasons, not a narrower rule) and all 313 T3 — every T3 item had the
  Panel ruling wedged BETWEEN the complaint and the response, because
  'panel_ruling' sorts between them alphabetically.
- Fused words from inline-tag close-up ("et alwas") — measure scale; likely
  source-faithful; document if so. **OPEN.**

## Pre-spend fix round (2026-08-02) — what else changed

Two changes that are not defect entries but that the re-audit must know about:

- **`bench/exclusions.jsonl` — the bank's negative space, made durable.**
  `bench/generate.py` writes one row per excluded item-candidate
  `{case_number, task, clause, reason, detail}`, every run, even when empty.
  Skips used to go to stdout only, which is why the one WRONG exclusion the
  audit found was invisible to anyone reading the bank rather than watching the
  build. `bench/validate.py` checks the log's shape and asserts that no
  candidate excluded under a hard rule (`dual_ruling`, `multi_case_undeclared`,
  `tripwire`, `tripwire_outcome_banner`) also sits in `items.jsonl`.
  Current counts: `no_usable_segments` 3159, `dual_ruling` 2538, `t3_no_appeal_board_ruling` 512, `sibling_duplicate` 455, `no_panel_ruling` 196, `multi_case_undeclared` 135, `t3_neither_ruling_attributed` 59, `tripwire` 21, `t3_no_panel_ruling` 14, `tripwire_outcome_banner` 7 — 7,096 rows total (regenerated 2026-08-05).

- **Spliced-outcome-banner tripwire.** Some report panes carry the standing
  banner line inside the body flow ('… a corporate patron of the club. NO BREACH
  OF THE CODE Under the agreement, …'). The attest's `no_outcome_banner` check
  could not see it, because it matches the banner headings derive RECORDED and
  derive only collects headings above the first body section — on
  AUTH-1861-7-06 `banner_headings` is empty. `BANNER_TRIPWIRE` matches the
  upper-case run case-sensitively, on every task including T3, and was counted
  corpus-wide before enabling: it fires on exactly the 7 audit items
  (AUTH/1861/7/06 cl 2, 9.1, 19.1; AUTH/2248/7/09 cl 2, 9.1, 15.2, 19.1) and
  nothing else — collateral damage zero.

- **Ruling-prose tokenizer, three additions** (`l2/build.py`). The root fix is
  in `frame_statements`: a match whose span swallowed a later 'no breach' used
  to be DISCARDED, losing the real statement with it; it is now re-read from
  the inner 'no breach'. That alone recovers AUTH/3483/3/21 cl 2
  ('… a ruling of a breach of Clause 2 was not warranted and no breach of
  Clause 2 was ruled') → `appeal_board: no_breach`, overturned, T3-eligible.
  Two new frames handle the phrasings that produced the audit's mislabels:
  coordinated ('no breach of Clause 22 was ruled together with no breach of
  Clause 2' — AUTH/2008/6/07 cl 2) and anaphoric ('… and the Panel ruled no
  breach of that clause', antecedent resolved to the sentence's most recent
  clause number, refused when the sentence names none — AUTH/2823/2/16 cl 15.4).
  Both clauses are now `verdict_dual_panel_prose` / `dual_ruling` and excluded
  from every binary task. Net effect on T3 recall: eligible clause-rows
  494/1,033 → 495/1,031 — 4 rows gained (AUTH/2811/12/15 cl 18.1,
  AUTH/3335/4/20 cl 23.1, AUTH/3483/3/21 cl 2, AUTH/3869/12/23 cl 2) and 4 lost
  to newly-detected dual rulings (AUTH/2008/6/07 cl 2, AUTH/2614/6/13 cl 12.2,
  AUTH/2823/2/16 cl 15.4, AUTH/3010/1/18 cl 7.9). The additions are precise, not
  broad: they move 15 verdict rows in total.

## Quarantine (already applied via tags, pending rebuild)
T3 entirely; T1/T1-triage items with basis `verdict_upheld_appeal_prose*`
or appeal-oriented bases; all T4.

## Post-fix residuals (2026-08-02, from the D1–D4 fix round)

- R1: **FIXED (l2.3)** irrealis guard. Appeal-side sentences of the form
  "hoped/requested/asked that …" and "submitted that … would" no longer credit
  the Appeal Board. AUTH/1902+1903 cl 18.1 `appeal_board` breach → null
  (basis `verdict_appealed_prose_attributed` → `verdict_appealed_unattributed`).
- R2: **FIXED (l1d.3 + l2.3)** appeal-side boundary completion. `COMMENTS FROM
  <party>` (256 headings) and a standalone `APPEAL BOARD CONSIDERATION` (31; the
  other 80 occurrences of those words are date-trailer lines and are excluded by
  anchoring the pattern to end-of-string) are now named in derive's vocabulary.
  They are NOT unconditional boundaries: `l2/build.py html_boundaries` applies
  the positional test, so a COMMENTS FROM heading terminates the Panel's ruling
  only when it sits after an `APPEAL BY …` heading or between a Panel ruling and
  a later Appeal Board heading — 237 of 256 qualify, and the other 19 are
  Paragraph-17 / interim-case comments gathered at PANEL stage
  ('COMMENTS FROM OTSUKA EUROPE ON THE REPORT FROM THE PANEL'), which open
  nothing. Evidence: AUTH/1984/4/07 `panel_ruling` 17570–33599 → 17570–27329,
  so its 2 T3 items no longer quote the complainant's appeal comments as the
  ruling under appeal.
- R3: **FIXED (l2.5, 2026-08-05)** outwith status variants folded in.
  The rule matched ONE exact string; the status slot is hand-typed with 193
  distinct values, and 24 further files state the same disposal in seven other
  spellings — 'No breach not within the scope of the Code' (16), 'outwith the
  scope of the Code' (2), 'No breach ruled as not within the scope of the Code'
  (2), and one each of four more including the typo 'out with'. Same disposal,
  same document shape: median report length 12 characters for the exact-match
  group and 45 for the variants, i.e. stubs either way. **72 → 96 files,
  73 → 97 cases.** Zero items change — all 24 are stubs with no quotable
  segment — so this is a descriptive tag correction, not a label change.
  Does NOT reopen D1: D1's false friends were in narrative prose ('outwith the
  licence', 'outwith the SPC') and the fix was to read the status slot instead;
  widening the pattern inside that slot adds no body text, and the new
  `check_outwith_coverage()` guard proves it — any status naming the Code's
  scope that the rule does not decide REFUSES the build and names the string.
  The guard was verified non-vacuous by narrowing the rule and confirming it
  fires (a lesson from R12, where a first-cut cross-check passed trivially).
- R4: `RESPONSE FROM <COMPANY>` heading form deliberately not added
  (collides with 'RESPONSE FROM THE COMPLAINANT'); costs 1 file. **OPEN.**
- R5: sibling-folded items label BOTH cases from page-level lists; where a
  ruling bound one respondent only (AUTH/2424+2425: Lilly not responsible),
  the folded label is wrong for one case. Full per-case attribution remains
  out of scope; re-audit must measure this error class. **OPEN** — now 455
  folded rows, every one of them a `sibling_duplicate` row in
  `bench/exclusions.jsonl`, so the class is enumerable for the first time.

## New residuals found in the pre-spend fix round (2026-08-02)

- R6: **appeal sections that carry no heading L1 can see.** AUTH/3809/8/23 marks
  its appellant's grounds as `BAYER’S APPEAL` — a possessive form the
  `^APPEAL (BY|FROM)` pattern does not match (3 such headings corpus-wide over
  3 files; 2 of those files have no `APPEAL BY/FROM` heading at all) — and its
  `APPEAL BOARD RULING` line is not recorded as a section heading by L1 at all.
  The whole appeal therefore sits inside one `panel_ruling` segment
  (57549–112678) and no appeal-side prose exists to read, so the F6 flip-class
  rows cl 3.6 and 24.2 keep `appeal_board: null` and produce no T3 item. Not
  fixable from `derived.jsonl` where L1 recorded no heading; needs either an L1
  section-detection change (frozen) or an adjudication. Scale: 2 files.
- R7: **companion cases that are not siblings.** AUTH/2470/1/12 and
  AUTH/2471/1/12 are separate documents (4,464 vs 3,103 words, 773 words shared
  in runs of ≥30), so the L2 sibling union correctly does not link them — it
  unions on shared `source_files` and declared `sibling_cases`, and these share
  neither. They land in DIFFERENT splits (train / dev), which puts ~773 words of
  shared narrative across the split boundary. Companion-case detection is out of
  scope as briefed; recorded so the re-audit can measure the class rather than
  rediscover this instance.

## New residuals from the l2.4 metadata round (2026-08-02)

- R8: **FIXED (l2.5)** one anonymity false friend, now guarded.
  AUTH/2956/5/17 (meta slot 'Ex-employee', h1 'Ex-employee v Napp') resolves
  `anonymous: true` from 'in any patient specific data and the information
  sought was anonymous in nature' — the DATA was anonymous, not the
  complainant. Identical before and after l2.4 (the 90-character anchor already
  admitted it; the 200-character anchor neither caused nor cured it). Scale
  measured, not guessed: of the 342 cases where prose ALONE sets anonymity
  (no meta 'Anonymous' token), this is the only quote whose token attaches to
  no person or complaint noun — 9 others flagged by the same sweep were checked
  by hand and are genuine. 1 case / 6 items. Not patched: the fix is either a
  narrow negative guard (refuse 'information/data/survey … was anonymous') or an
  adjudication, and `parties.complainant` is not currently an adjudicable field
  — `apply_adjudication` expects a `canon`-shaped value object. Fixed with the
  narrow guard: a data/information noun plus a copula immediately before the
  token (`ANON_ABOUT_EVIDENCE_RE`). Deliberately narrow, because R8's own sweep
  established this is a class of ONE and a broad rule would invent coverage.
  AUTH/2956/5/17 now publishes `anonymous: null` — not recorded — which is the
  honest reading: the report states the data was anonymised and says nothing
  about the complainant. Verified corpus-wide: exactly 1 case changed. The
  earlier "1 case / 6 items" scale in this entry was WRONG: the case carries
  **14** items, all of which had a prompt-visible field change.
- R9: **sibling panes share one complainant reading.** AUTH/3022/2/18 and
  AUTH/3023/2/18 are one pane describing two complainants — 'Two contactable
  health professionals, an anonymous "concerned oncologist" (Case
  AUTH/3022/2/18) and an oncologist specialising in GI tumours who wanted to
  maintain confidentiality (Case AUTH/3023/2/18)'. The anonymity statement
  belongs to 3022; both cases receive it. Same structural class as R5
  (per-case attribution inside a shared pane, out of scope); recorded so the
  re-audit measures it rather than rediscovers it. **OPEN.**
- R10: **FIXED (l2.5)** `\bpharma\b` in the industry role rule matched company names. With
  the 200-character capture, 'healthcare journalist submitted a complaint about
  the UCB Pharma website' (AUTH/2972/8/17) and 'a senior key account manager at
  UCB Pharma' (AUTH/3030/4/18) classify as an `industry` self-description. No
  published VALUE changes — `industry` is not in SPEC §4, so it maps to the
  meta category, which is already correct in both cases (media,
  employee_or_ex_employee) — but the `note` string said 'prose self-description
  is an industry role' when it is not. Note-only, 2 cases. Fixed at the
  application site rather than in the pattern: Python's `re` has no
  variable-width lookbehind, so `role_hit_is_company_name()` rejects a `pharma`
  hit preceded by a capitalised word. Verified corpus-wide: exactly the 2 notes
  cleared, no category changed.

- R17: **FIXED (l2.6)** Panel-ruling recall gap in a specific prose frame. Round 4
  auditor A found that L2 misses Panel rulings phrased as "did not consider
  that … warranted a ruling of a breach of Clause N": swept inside PANEL RULING
  sections, L2 reads the frame 55 times and misses ~16. Confirmed by hand on
  AUTH/1847/6/06 cl 2 and AUTH/3137/12/18 cl 8.1; AUTH/2649/10/13 cl 14.1 is a
  related anaphor-across-sentence miss. These candidates are currently excluded
  as `no_panel_ruling`, so R13 made the class VISIBLE rather than causing it —
  the exclusion reason is a true description of L2 and a false description of
  the case report. Pre-existing D3-class defect. Fixing it ADDS items, so it
  changes the bank and needs its own audit round.

### Found 2026-08-05 by adversarial review of the fixes themselves

Recorded because they were found by an INDEPENDENT reviewer reading the six
fixes above, not by the person who made them. Two of the three are consequences
of a fix, which is the class of defect self-review is worst at seeing.

- R14: **FIXED (l2.6)** via adj-0005; see round 4 below. The entry stands as the
  record of how it was found. Original text:
- R14 (original): **a received date no witness supports.** AUTH/3293/1/20: the
  canonical value is 2020-12-30, the report trailer says 30 December 2019, and
  the case number encodes **January 2020**. R12's tie-break compared the case
  number's YEAR only, so 2020 matched 2020 and the row was reported as "value
  corroborated, the trailer is the typo" — but the case number's MONTH (01)
  contradicts the value's (12), so nothing actually supports 2020-12-30. The
  coherent reading is receipt on 30 December 2019 with the case numbered in the
  new year, which would make the TRAILER right and the value wrong by a year —
  but that is inference, not evidence, so the value is left standing and the
  disagreement is stated. `verify/received_date_witnesses.py` now uses the month
  and EXITS NON-ZERO on this row. Resolve as an entry in `l2/adjudications.json`
  (reviewed, justified, source-pinned) rather than by widening a rule.
- R15: **OPEN — R11 emptied a designed anchor class.** `bench/items.jsonl` now
  carries **0 items tagged `abridged`** (previously 21, all four of them false
  positives R11 correctly removed). The six genuine abridged cases produce no
  items at all: CASE/0308/10/24 via `no_panel_ruling`, the other five via
  `no_usable_segments` — abridged reports are summaries with no quotable ruling
  prose. R11's note "tag-only, no label changed" is true and misleading. The
  consequence is that `bench/DESIGN.md:32` ("abridged procedure | 6+ |
  near-certain items") and DESIGN.md:55-56 ("abridged admissions ... anchor
  known points on the confidence curve") now describe a class that is
  STRUCTURALLY ABSENT from the bank — the same failure mode that withdrew T4 in
  D1. Either the anchor is redesigned to draw on the summary text, or DESIGN.md
  stops claiming it. Not a data defect; a design claim the data no longer
  supports.
- R16: **OPEN — pre-l2.4 runs underpin FINDINGS rows and are not reproducible.**
  Seven runs in the 2026-08-02 morning cohort were executed against the bank
  built BEFORE l2.4's metadata fixes, and their item_ids no longer all exist:
  `20260802T101120Z` (gpt-5.1, n=150 — the third row of the FINDINGS §4.2
  table) has 38 of 150 item_ids absent from the current bank; `…101115Z` 63/250,
  `…101116Z` 67/250, `…101118Z` 38/150, `…101119Z` 37/150, `…101117Z` 10/10,
  `…105928Z` 22/83. This predates 2026-08-05 and is NOT a consequence of the six
  fixes, but it contradicts FINDINGS' opening promise that every number traces
  to an artefact in this repo. **Disclosed 2026-08-05** with a per-run table in
  FINDINGS §2.2 rather than re-run: re-running is live spend and the archive is
  append-only. Stays OPEN because disclosure is a mitigation, not a fix -- the
  durable remedy is to re-run those cells, and R17 has since moved the bank
  again, so the gap widens with every correction. **2026-08-06: the drift now
  BREAKS `bench/export_site_data.py --n 0`** — it refuses at the leaderboard
  stage because `T3-0bb7eb561480f074` (referenced by the archived sonnet
  t3-appeal run, 1 of its 297 ids) no longer exists in the bank. Proven
  pre-existing on 2026-08-06: the same refusal occurs against the bank as it
  stood before that day's clause-text change. Consequence: per-item site
  data is current, but `leaderboard.json`/`meta.json` are frozen at their
  2026-08-05 content until either the exporter learns to report (not
  silently skip) absent ids per board, or the affected cells are re-run.
  **RESOLVED at the exporter (2026-08-09):** export_site_data.py now
  resolves renamed ids through `bench/id_migrations.jsonl`, scores boards
  on surviving ids, and exports per-board `accounting` (n_expected /
  n_scored / n_mapped / n_absent with the absent ids and their exclusion
  reasons — joined through each run's ARCHIVED item witness in
  bench/subsets/, so today's bank never testifies about the past); it
  still hard-refuses on label mismatches, witness-less boards, and
  divergent expectations (all four guards proven to fire). The mystery id
  itself is explained: AUTH/3174/3/19 cl 2, removed as a `dual_ruling` by
  an earlier rebuild. The UI carries the accounting line per board and a
  dated prompt-condition note on archived answers (gated on each call's
  own created_utc). R16 itself STAYS OPEN — reported truncation is
  disclosure, and the durable remedy remains re-running the affected
  cells. New small residual, same family: per-item review capture
  (localStorage `abpi-review-v1`) keys on item_id, so reviews saved under
  the 261 old ids orphan silently on the item (recoverable via export);
  recommended fix is shipping the old→new map beside meta.json and a
  one-time key rewrite behind `abpi-review-v2`.

### Found 2026-08-05 by external witness (see docs/ABPI_SOURCES.md)

These three came from outside the pipeline, not from re-auditing it. The PMCPA
webinar dates the abridged complaints procedure to October 2024 and the Annual
Reports publish their own case counts; neither was reachable from inside the
corpus, and both R11 and R12 had survived every internal check.

- R11: **FIXED (l2.5)** `procedure.abridged` matched company prose, not the
  case title. The origin comment measured the two-word phrase as matching
  '10 pages, all of them the procedure, stated in the case title line'. That
  was true when written and decayed as the corpus grew to 2025: still 10
  matches, but only SIX are the title line. The other four are a company
  arguing for the reform before it existed ('once the new abridged case
  management process is introduced', AUTH/3891/4/24), one citing it in a
  Clause 2 argument (CASE/0513/03/25), and two cases the case preparation
  manager STARTED under the abridged procedure and moved to the full one
  (CASE/0678/07/25, CASE/0715/09/25) — those two mention it precisely because
  it did not apply. Fix reads the summary pane's title line only (all six
  genuine matches sit at offset 29, right after 'Case Summary <case number> ';
  the four false positives never appear in the summary). External check: the
  2025 Annual Report states 'only five complaints progressing through the
  abridged procedure in 2025'; the survivors are one 2024 and five 2025.
  Tag-only — no label changed, no item_id changed.
- R12: **FIXED (l2.5)** date trailers are now compared, and a third witness
  decides. The trailer lines were deliberately recorded-but-not-compared (the
  RULES comment named AUTH/3008/1/18 as the reason: two witnesses cannot settle
  a disagreement, so reading them was deferred as phase 2 rather than resolved
  wrongly). The third witness for `received` is the case number, which encodes
  month/year of receipt and comes from the listing rather than the report body;
  for `completed` it is the constraint that a case cannot complete before it
  arrives. Result: **13 received dates corrected** (trailer + case number agree
  against the slots), 6 where the slots are corroborated and the trailer is the
  typo, and for completed 2 corrected / 3 corroborated / 8 stated as unresolved.
  Sub-year differences are NOT errors and are not treated as such: across 233
  received and 138 completed disagreements the modal gap is the report stating
  one day later than the slots (39% and 62%), a convention difference, so the
  slots are kept under `date_slots_over_trailer_same_year` and the parsed
  trailer date is recorded in `sources` on every case. No label changed, no
  item_id changed. The corrections do not touch the post-cutoff holdout, which
  DESIGN.md keys on the COMPLETION date, and whose disagreements span 2011–2022.
- R13: **FIXED (bench, 2026-08-05)** — found while verifying R11/R12. Seven L2
  cases carry verdict rows AND segments but produce neither a bench item nor an
  exclusion row: AUTH/2709/4/14, AUTH/2790/8/15, AUTH/2863/8/16, AUTH/2898/11/16,
  AUTH/2901/11/16, AUTH/2945/3/17, CASE/0308/10/24. Every other case with
  verdicts resolves to one or the other (1,486 produce items, 409 have exclusion
  rows only). This is a silent drop of exactly the kind the durable-exclusions
  rule exists to prevent, and it is PRE-EXISTING — item_ids are byte-identical
  across the R11/R12 rebuild, so nothing here caused it. Reproduce with the
  set difference of `data/l2` case numbers against `bench/items.jsonl` and
  `bench/exclusions.jsonl`. Note the comparison must use `case_number.value`,
  not the raw `index_case_number`: the latter is the site's display string and
  is compound on multi-case reports ('AUTH/1790/1/06 & AUTH/1791/1/06'), which
  inflates the apparent count from 7 to 112.

  Diagnosis: TWO silent paths, not one. (a) A verdict row L2 could not attribute
  to the Panel skipped the `make()` branch for T1 and T1-triage without
  recording anything — every row in all seven cases has `panel: null`. (b) In
  the T3 branch the `exclude()` for an unresolved Appeal Board ruling sat inside
  `if panel in (...)`, so the one state where NEITHER side is attributed — the
  case about which least is known — was the one state that left no trace.
  Fixed: `no_panel_ruling` (196 rows) and `t3_neither_ruling_attributed` (59
  rows). No item added or removed; item_ids byte-identical.

  Guarded against recurrence by `verify/candidate_accounting.py`, which
  reconstructs the candidate set from L2 — independently of how generate.py
  walks it, so it does not share a witness with the code it audits — and fails
  if any candidate resolves to neither an item nor a reasoned row. Currently
  16,742 candidates: 10,045 items, 6,352 clause-level rows, 361 covered by a
  case-level row, 0 unaccounted.

### Found 2026-08-06 during the dataset review (the T3 setting line)

- R18: **OPEN — T3 `appellant` is case-level; five items assert an appeal
  nobody made, and at least one label is wrong.** `metadata_shown.appellant`
  is copied from `case.appeal.by` (generate.py, T3 branch) — one value per
  case — while the ruling under test is clause-level. Five of 316 T3 items
  combine `appellant: respondent` with `panel_ruling_for_clause: no_breach`,
  so the rendered question reads "The Panel ruled no breach … The respondent
  company appealed that ruling" — but a company does not appeal a ruling in
  its own favour; in all five cases the company's appeal was against breach
  rulings on OTHER clauses. Items: T3-04631ffe0f577308 (AUTH/1859/6/06 cl
  18.1), T3-892d831a4b40270e / T3-e60626cda8179e2a (AUTH/3028/3/18 cl 15.2 /
  cl 2), T3-a28cf69df3a05795 (AUTH/3535/7/21 cl 2), T3-c0c42b4f4bc0495b
  (AUTH/2414/6/11 cl 20.1). The mirror cell (complainant-appealed breach) is
  empty across all 316, corroborating the case-level diagnosis. Read against
  the report for AUTH/2414/6/11, the defect is deeper than wording: the Panel
  ruled no breach of 20.1 in one regard AND "a breach of that clause was thus
  ruled" in another (a dual ruling in different regards; `dual_ruling: false`),
  AstraZeneca "appealed the Panel's rulings of breaches of Clauses 12.1, 18.1
  and 20.1", and the Appeal Board ruled "no breach of Clause 20.1 … The appeal
  on this point was successful." The appealed 20.1 ruling went breach →
  no_breach = overturned; the item is labelled **upheld** on the un-appealed
  half of a dual ruling that L2 attributed singly — R17's frame-recall family
  (the panel-side reader caught "ruled no breach of Clause 20.1 in this
  regard" and missed the breach sentence later in the same section). The
  other four cases are being read the same way (delegated 2026-08-06), plus a
  T3-wide sweep for both-polarity Panel rulings on a single clause. Fix
  options, none taken yet: (a) exclude the five as
  `t3_appellant_clause_mismatch`; (b) per-clause appellant from prose
  evidence — NOT inferred from ruling direction; (c) the 2414 cl 20.1 label
  needs L2 to represent dual rulings, not an item patch. Any fix changes
  prompts and possibly labels → the re-audit gate at the foot applies.
  Observation recorded alongside, not a defect: the 14 `appellant: both`
  items are perfectly separated by Panel ruling (breach→upheld 11/11,
  no_breach→overturned 3/3) — an n=14 accident of this bank, but the
  re-audit should watch it rather than rediscover it.

  **LARGELY FIXED (2026-08-09 wave-2 build).** adj-0008/adj-0009 mark the
  AUTH/2414/6/11 cl 20.1 and AUTH/1859/6/06 cl 18.1 verdicts dual (panel
  null — the state their sibling clauses already had), removing **6** items
  (the 2 wrong-label T3 items plus their 4 T1/T1-triage siblings, which
  were labelled off the same singly-attributed ruling), all with exclusion
  rows. adj-0010/adj-0011 set `appeal.by = complainant` for AUTH/3028/3/18
  and AUTH/3535/7/21 on the NEW `report_appeal_headings` witness in
  l2/build.py (352 headings over 273 files, 103 spellings, closed
  vocabulary with a proven refusal guard); the 3 affected items' premises
  are now true and their ids unchanged. The (respondent, no_breach) cell
  is EMPTY; T3 label split 229/85. STILL OPEN under this entry: (a) a real
  L2 dual-ruling representation (option c) — exclusion is honest but loses
  the 2 genuinely-overturned transitions; (b) AUTH/1790+1791/1/06, a true
  heading-vs-slot contradiction ("APPEAL BY ROCHE AND GLAXOSMITHKLINE" vs
  slot 'respondent'), refused into `by: null` (4 t3_appellant_unresolved
  rows, 0 items lost); (c) heading-over-"no appeal" on AUTH/2296/1/10 and
  AUTH/2825+2826/3/16 — recorded as warning class
  `appeal_heading_on_unappealed_slot`, unrepaired deliberately: repairing
  would delete T1 items on unadjudicated evidence.

  **Audit landed 2026-08-06** (adversarial Opus; two passes sharing no code
  with L1's ruling reader or L2's verdict folder; agreement baseline 306/316
  single-polarity exact, 8 no-hit, 2 both-polarity). Final count: **2 of 316
  T3 labels WRONG**, both dual Panel rulings L2 attributed singly, both truly
  overturned on the half that was actually appealed:
  - `T3-c0c42b4f4bc0495b` (AUTH/2414/6/11 cl 20.1) — breach half carried by
    the anaphor "a breach of that clause was thus ruled".
  - `T3-04631ffe0f577308` (AUTH/1859/6/06 cl 18.1) — breach half carried by
    "the arrangements were unacceptable in relation to Clause 18.1 and
    ruled accordingly"; the company appealed "rulings of breaches of
    Clauses 2, 9.1 and 18.1" and the Appeal Board "ruled no breach of
    Clause 18.1". L2 itself flagged cl 2 and 9.1 of this case as dual and
    excluded them; 18.1 escaped because its breach frame is one the
    panel-side reader does not recognise. The consistent repair is the one
    L2 already applied to its siblings: exclusion (or an L2 dual-ruling
    representation, per option (c)).
  The OTHER THREE of the five have CORRECT labels but a premise false **at
  source**, which reframes the entry: the PMCPA info slot "Appeal by
  respondent" (`appeal.by`, basis `appeal_fold_table`) is contradicted by
  the report's own `APPEAL BY THE COMPLAINANT` heading in 2 of the 149 T3
  cases — AUTH/3028/3/18 ("The complainant appealed the Panel's rulings of
  no breach of Clause 15.2 … and Clause 2") and AUTH/3535/7/21 ("Sobi
  accepted all of the rulings from the Panel in this case"). So 3/5 items
  are not case-level-vs-clause-level granularity but a wrong source value
  with a cheap independent witness (the `APPEAL BY …` heading; the 13 other
  apparent mismatches corpus-wide all resolve on company-name
  normalisation). Fix option (b) covers them; option (a) would hide a
  source-data defect that deserves its own witness check in l2.
  Sweep residue beyond the five: `T3-4ff3597575776cc8` (AUTH/3597/1/22
  cl 2, `overturned`, NOT in the eval subset) is CORRECT BUT FRAGILE — a
  dual L2 missed ("…consequently no breach of Clauses 5.1 and 2"
  unrecognised) where the attributed half happened to be the appealed half.
  Exhaustive anaphor resolution (34 frames), "ruled accordingly" resolution
  (3), an appeal-grounds cross-check (exactly 2 contradictions = the two
  wrong labels) and a mirror-direction sweep found nothing further: 310/316
  clean. For any future dual detector: the censure idiom "did not consider
  that the circumstances warranted a ruling of a breach of Clause 2" is a
  no-breach statement with a breach-shaped surface (11 T3 items) and must
  not fire. Screen recall limits are stated in the audit report
  (clause-anonymised rulings, non-that/this anaphora, verbless rulings):
  bench/review/audits/2026-08-06_t3_r18_audit.md.
  Both wrong-label items sit in the evaluated 297 (FINDINGS §4.1): under
  corrected labels the class split is 217/80 and a model that answered
  upheld on both loses 2 correct — sonnet 0.737 → ~0.731 overall,
  overturned-class accuracy 3/80; direction strengthens the exhibit, no
  conclusion changes. FINDINGS is NOT yet corrected: label changes are
  gated on a decision (exclude vs relabel vs L2 dual representation) and
  the re-audit protocol at the foot.
  Separate observation, not R18: AUTH/3591/12/21's three T3 items quote the
  report's clause-anonymised summary rendering as their `panel_ruling`
  extract rather than the full PANEL RULING section that names Clause 5.1 —
  worth its own look at extract-selection for summary-style reports.

### Found 2026-08-08 during the dataset review (code_year witnesses)

Provenance: a mechanical sweep (report-pane `\bYYYY Code\b` vs the tagged
year; 1,486 item-bearing cases: 557 pairs agree in prose, 894 reports name
no edition, 49 conflict) followed by an adversarial Opus read of all 49
with three cross-witnesses (info slots, received date, conduct date). The
governing doctrine is witnessed in-corpus, not assumed: the PMCPA rules
under the edition in force at the time of the CONDUCT (AUTH/2297/1/10:
2003 Code applied to a complaint received 2010-01-27 about a April-2004
advertisement — correct, not a defect; AUTH-3638-4-22: "given the items
appeared to have been live when the 2021 Code came into operation… they
were considered under the 2021 Code").

- R19: **OPEN — the "Applicable Code year" slot is wrong for ≥25 cases
  (≥168 items); two systematic modes, only prose can arbitrate.** Of the 49
  prose-conflict cases: 27 correct (6 regex false friends off "Code of
  Practice Review" trailers, the rest precedent/party/forward references),
  20 WRONG, 2 genuinely multi-edition (AUTH/2823/2/16, AUTH/3115/11/18 —
  one outcome list carrying two numberings). Mode 1: the slot records the
  CONSTITUTION AND PROCEDURE year, not the Code edition — 8 of the 20,
  concentrated 2008-tagged ("The complaint was considered under the 2006
  Code using the 2008 Constitution and Procedure", AUTH/2147/7/08; the
  PMCPA contradicts itself between two slots on one page: AUTH/2139/7/08
  has Applicable Code year '2008' four rows above 'No breach Clause(s) =
  2, 7.2, 9.1 and 19 of the 2006 Code & the 2008 Constitution and
  Procedure.'). Mode 2: boundary-period filing-vs-conduct confusion, BOTH
  directions (Jan–Jun 2019 alone: 3148/3187/3210 tagged 2019 ruled 2016;
  3166/3167 tagged 2016 ruled 2019) — so a date-based repair would be
  wrong about as often as the slot; the repair is per-case prose-witness
  adjudications in l2/adjudications.json. The 49 are a LOWER bound: a
  targeted sweep ("considered under the YYYY Code" family, 331 hits, known
  false positive AUTH/2220/3/09 — quote in the audit report) found 5 more
  wrong tags outside the flags: AUTH/1795/2/06 (2003→2006), AUTH/2736/9/14
  (2012→2014), AUTH/2843/4/16 (2015→2016), AUTH/3839/10/23 (2021→2019),
  CASE/0654/07/25 (2024→2021 — the respondent's own apology for citing the
  wrong edition is repeated by the metadata: "We apologise for incorrectly
  citing clauses of the 2024 Code instead of the 2021 Code"). Extreme
  member: AUTH/3200/5/19 tagged 2019, ruled under the 2001 Code (2002
  meeting). Full 49-row table with quotes:
  bench/review/audits/2026-08-08_code_year_audit.md.
  **FIXED (2026-08-09 wave-2 build): 31 adjudications** (adj-0012…adj-0042)
  — the audit's 28, plus AUTH/2425/8/11→2011 (sibling sharing 2424's
  single report; publishing two Code years for one document would break
  sibling de-duplication, which keys on (clause, code_year)), plus TWO
  found by the new standing verifier outside the audit's predicate
  (AUTH/2752/3/15→2015 "This case would thus be considered under the 2015
  Code"; AUTH/3186/5/19→2016). AUTH/3200/5/19→2001 makes 2001 a real
  code_year here (item_schema minimum 2003→1998; NO_SOURCE_YEARS gains
  2001; its 6 items excluded `no_source_document`). Standing guard:
  `verify/code_year_witnesses.py` — independent sentence-level reading
  (shares no code with the builder), classifies AUTH/2220/3/09's
  "considered under the 2008 Code" correctly, passes AUTH/2297/1/10 as
  flag-only, exit-1 proven by reverting adj-0042. Residual REFUSED, not
  repaired: AUTH/2135/6/08 + AUTH/2190/12/08 (the (2008, 3.3/20.1/20.2)
  pairs, 8 items) — neither report names any edition, and R19's own
  finding is that date-only inference errs in both directions; declared as
  KNOWN_ABSENT in the verifier, which fails if the bank stops carrying
  them. Fourth KNOWN_ABSENT member added 2026-08-10 by the 2001/2003
  parse: **(2003, 18.4), AUTH/1846/6/06, 2 items** — the printed 2003
  Clause 18 runs 18.1–18.3 and stops (p24, rendered and read); 18.4 first
  exists in 2006. The report rules "No breaches of Clauses 15.9, 18.1 and
  18.4" while naming "Clause 9.1 of the 2003 Code" elsewhere, and the
  complaint arrived 2006-06-01 — astride the edition boundary; wrong-year
  tag and mixed numbering are both live readings and nothing decides
  between them, so the pair is refused, not repaired. (The 2026-08-09
  fetch's spot-check that "18.4 exists" was a false positive on paragraph
  18.4 of the Constitution and Procedure — different document half.) This
  hit came from verify/code_year_witnesses.py's structure check the moment
  the 2003 inventory existed: R21's "close the coverage gap before
  trusting the clause-absence oracle" caution, discharged as designed.
  With the 2001+2003 editions parsed (2026-08-10: +175 items gained text;
  6/6 for 2001, 169/171 for 2003), the corpus-wide textless residue is
  **27 items across 10 (year, clause) pairs**, all evidence-backed
  refusals: this pair, the three 2008 KNOWN_ABSENT pairs (6 items — down
  from 8: the R19 renames moved AUTH/2147's 20.2 items to (2006, 20.2)
  where text exists), and six 2012 pairs (19 items: genuine edition
  divergences on undecidable/contradictory cases, plus AUTH/3115's
  withheld clauses).
- R20: **OPEN — generate.py's `year_of()` prefers the per-clause chip over
  the case slot; 137 items across 31 cases carry item-year ≠ case-year, 92
  cross the 2019/2021 RENUMBERING, and at least 8 items serve semantically
  unrelated clause text.** This is worse than R19's missing text — it is
  actively wrong prompt content: AUTH/3777/6/23 was ruled under the 2021
  Code ("The outcome under the 2021 Code was: … No Breach of Clause 6.1 …
  must not be misleading") but its item T1-a5a0e88d70d2c6ea serves the
  2019 Clause 6.1 — "No issue of a print journal may bear advertising for
  a particular product on more than two pages" — a page-count rule against
  a misleading-claims allegation. AUTH/3875/02/24 same shape (4 items);
  AUTH/3819/9/23 and AUTH/3895/5/24 chips leap FORWARD to 2024. Neither
  witness dominates: in AUTH/3722/1/23, AUTH/3723/1/23, AUTH/3557/9/21,
  AUTH/3680/8/22 the CHIP is right and the case slot wrong ("No Breach of
  Clause 9.1 (2016 Code)" on a 2021-tagged case). Repair needs the prose
  as arbiter per item-year, and the null-clause_text symptom should become
  a build-time refusal naming undecided (year, clause) pairs rather than a
  silent "not available" line. Past runs are affected: items in the 137
  with HTML-era chip years served wrong-edition clause text in every run
  that included them.
  **FIXED at the disagreement set (2026-08-09 wave-2 build).** Arbitration
  lives in l2/build.py's resolve_verdicts (year_of was already there;
  arbitrating downstream would have L2 publish one year and bench use
  another), as a WITNESS SET that fires only on disagreement: per-clause
  prose (65 rows) → case-level prose (32) → case slot, reachable only when
  the report names no edition and the clause exists in that edition's
  structure (17) → refusal (14 rows; 8 items excluded
  `code_year_undecided`, including AUTH/3722/1/23 where Clause 9.1 is
  ruled under BOTH the 2016 and 2019 Codes in one report — a dual-year
  clause is a refusal, not a tie). Deterministic artefact:
  `data/l2/code_year_arbitration.jsonl`, 128 rows, each carrying every
  witness, the level, and the deciding quote. AUTH/3115/11/18's four
  cross-numbered items stay refused. STILL OPEN: promoting per-clause
  prose over witnesses that already AGREE would re-key 183 items across 66
  cases, none of them read — measured, refused, and left for its own
  reading round.
  **RESIDUE CLOSED (2026-08-10, the reading round).** Population
  re-measured before reading: 134 (case, clause) rows across 72 cases,
  132 items — the register's 183/66 was measured at the wave-2 build,
  before R19's 31 case-year adjudications changed which witnesses agree
  (delta noted per docs rule); one row added that the builder's regex
  cannot see (AUTH/3115 cl 22.1, year-before-clause word order). Every
  row read and classified: adjudicator-ruling-frame 78, case-prep letter
  23, party submission 13, renumbering gloss 11, precedent 9, noise 0.
  Verdicts: **34 PROMOTE** (each on a ruling-frame quote; sharpest:
  AUTH/3729/1/23 2021→2019 where the 2021 numbering means Training/
  Prescribing-Information against a disguised-promotion ruling;
  AUTH/3185/4/19 2016→2019 decided by supplementary-information wording
  that exists only in 2019; AUTH/2779/7/15's five clauses 2015→2008 on
  "pragmatically decided to make rulings … according to the 2008 Code";
  AUTH/3115's 22.1/23.1 2012→2016 resolving R22(c)), **12 REFUSE** with
  exclusion rows (the 21.3 trial-by-trial cluster; five 13.1 rows where
  the Panel applies the Second 2012 Edition against a 2014–2016 clause
  number — promoting to 2016 was defensible since the text is identical
  across 2014/2015/2016, refused because picking one of three named
  editions is a guess), **88 KEEP** by class. Mechanism: adjudications
  gain `verdicts[<clause>].code_year`; arbitrate_year gains level 0,
  reachable ONLY from a reviewed row pinned to the verdict's receipts
  sha — un-read automatic promotion remains impossible, and three guards
  were proven to fire (stale sha, unwitnessed promotion, row pointing at
  an unpublished clause). Consequences: 49 items renamed
  (id_migrations.jsonl 261→310), 4 removed (`code_year_undecided`),
  census 10,031→**10,027** (T1 4,298 / T1-triage 5,415 / T3 314),
  textless 27→23; archived answers: 3 ids renamed, 0 removed; labels/
  splits/extracts/provenance 0 changes; T5 byte-identical. Found en
  route, repaired: AUTH/2100/2/08 2006→2003 (adj-0090, C&P mode, 0
  items). Found and NOT repaired, new R19 candidate: **AUTH/1819/4/06**
  — the Panel applies the 2006 Code's Jan–Apr 2006 transition provision
  while the slot says 2003; flagged for the everything-audit.
- R21: **CLOSED as tag-defect, OPEN as coverage gap — Clause 29 missing
  from data/code/clauses.jsonl for 2015 and 2016.** Independently
  investigated same day: the PMCPA's interactive site indexes the page for
  both editions and serves HTTP 200 with an EMPTY body — exactly three
  bodiless pages sitewide, all "Compliance with Undertakings" (2014 cl 26,
  2015 cl 29, 2016 cl 29); the parser refused correctly and logged all
  three (data/logs/failures.log, the only code-extract entries). The
  printed PDFs carry the clause (2015: 2275__…2015.pdf PDF p41; 2016:
  2257__…2016.pdf PDF p42), one paragraph, no subclauses, no supplementary
  information, byte-identical across 2015/2016/2019-print and the 2019
  interactive row after whitespace normalisation. The 20 affected items'
  code_year tags are CORRECT (reports name their editions while ruling on
  Clause 29). Repair is a backfill from the PDFs, but the blocker is a
  design decision, not code: generate.py's layer guard says a YEAR belongs
  wholly to one extraction (HTML 2014+ / PDF pre-2014); backfilling
  2015/2016 rows trips it by design. Either relax ownership to
  (year, clause) with the refusal re-armed at that grain, or a third file.
  CAUTION recorded for any future "clause absent from edition" oracle: 13
  of its 17 case-hits are this coverage gap, not tag defects; only
  (2008, 3.3/20.1/20.2) are genuine — close this gap before trusting that
  oracle.
- R22: **OPEN — the 2012 first/second-edition question, resolved per case
  with receipts; and the divergence table that blanked 180 items is half
  artefact.** Two independent auditors covered all 62 affected cases
  (bench/review/audits/2026-08-08_2012_edition_resolution.md). Verdicts:
  SECOND 45, SECOND+ADDENDUM 2 (AUTH/2698/1/14, AUTH/2705/3/14), FIRST 6
  (all received or conduct-dated before 1 July 2012), UNDECIDABLE 5
  (received AND conduct inside the 1 Jul–31 Oct 2012 transition, or
  conduct straddling 1 July — AUTH/2523/7/12, AUTH/2526/8/12,
  AUTH/2534/10/12, AUTH/2535/10/12, AUTH/2645/10/13), NEITHER 3, and 1
  CONTRADICTORY (AUTH/2528/8/12: the parties agree the FIRST edition
  applied — "As the press release was dated 28 June 2012, the 2012 Code
  applied" — while the Panel's own ruling paraphrases 14.5 in
  second-edition-only wording; left unresolved). Evidence quality is
  honest and uneven: 13 level-1 (report names the edition — all but one in
  the late half; the PMCPA only started naming editions once the Addendum
  arrived), 5 level-2 (quoted wording exclusive to one edition, e.g.
  AUTH/2584/3/13's 1.2 definition "administration, consumption,
  prescription, purchase, recommendation, sale, supply or use"), the rest
  date INFERENCE, marked as such. The PMCPA's own statable rule, quoted
  in-corpus: procedure follows the complaint date, substance follows the
  conduct date, and where wording is identical across candidate editions
  the Panel picks either and says so ("The clauses cited … were the same
  in the 2014 and Second 2012 Edition (amended) Codes, thus the Panel used
  the 2014 Code", AUTH/2698/1/14). Sub-findings: (a) BOTH auditors
  independently conclude 8 of the 16 recorded divergences are not textual
  differences (1.8, 3.1, 12.1, 14.2, 19.1, 22.2, 23.7 identical once
  whitespace-normalised — 19.1's whole supplementary block is
  character-identical, 5,518 chars each side; 4.1 differs only in a
  supplementary heading word) — the builder compared raw extracted strings
  and PDF line-break artefacts blanked roughly half the 180 items. A
  CONFIRMED LEAD, not yet a proven defect: neither auditor reproduced
  parse_code_pdfs.py's rendered() attachment, so the repair is to re-run
  the comparison whitespace-normalised INSIDE the builder and let the
  identical clauses fill with no edition decision. Genuinely divergent:
  4.10, 7.5, 14.5, 18.1, 21.3, 22.1, 23.1 (+16.4 vs the Addendum only).
  (b) THREE cases tagged 2012 pre-date both 2012 editions — AUTH/2410/6/11
  and AUTH/2424/8/11 completed in 2011, AUTH/2411/6/11 received June
  2011 — the 2011 Code governed; new R19 members from the PMCPA's own
  slots. (c) AUTH/3115/11/18 is a LIVE cross-Code numbering collision:
  its outcome list mixes 2012 numbering (20.1, 19.1) with 2016 numbering
  (23.1, 22.1), all items stamped 2012; the 20.1 items already render 2012
  text, and the 23.1/22.1 items (T1-961530312a16979c /
  T1-triage-0ebaa77bc48e38e0 / T1-ac18b563429bd402 /
  T1-triage-3a29b0dda9436643) would, if naively filled, serve the 2012
  Code's patient-organisation and advertising-to-public clauses against
  rulings about consultants and hospitality — R20-family, per-item year
  arbitration required. (d) The 21.3 cluster
  (AUTH/2657/2665/2666/2669/2670/2674/2676-11-13) is tagged 2012 but rules
  trial-by-trial under the 2006/2008/2011 Codes with only the framing
  clause under the Second 2012 Edition — multi-edition cases in the
  AUTH/2823/2/16 sense. (e) AUTH/2602/5/13's HTML report pane belongs to
  AUTH/2603/5/13 (already recorded in L2 as source_integrity:
  report_pane_mismatch); the PDF record is the canonical text.

- R23: **OPEN — the PDF clause builder's column-major reading order is
  wrong on two-band pages; 13 items are shipping affected text, 20 more
  are refused because of it.** Found 2026-08-09 while root-causing R22(a):
  on pages where a column carries two vertical bands (2256 p39 and
  kindred), reading all of the left column then all of the right attaches
  the wrong band — Clause 18.6's continuation lands INSIDE 19.1 ("…the
  payment of reasonable travel were provided and the information must be
  made public within three calendar months…", 475 chars of 18.6), and
  23.7's tail attaches to Clause 24.1. Detector: a supplementary block
  ending without terminal punctuation — 6 of 669 blocks in the four
  pre-2014 documents, ALL six this defect (2011 cl 10.1 + 18.6; 2012-first
  cl 10.1 + 18.6 + 23.7; 2012-second cl 10.1), declared in
  KNOWN_TRUNCATIONS with a build refusal on any seventh. Marked renderings
  are never served (`attachment_suspect`, per edition — 23.7 still serves
  from the clean second edition). LIVE residue: (2011, 10.1) + (2011,
  19.1) = 8 items and (2012, 10.2) + (2012, 11.2) = 5 items already carry
  text drawn from an affected page and may include or be missing a
  paragraph; 20 items are refused (`builder_block_attachment` and kin).
  The repair is a page-band reading-order model in `page_lines` — a real
  piece of work on ~2,400 items' source pages, not a patch; do it with its
  own verification round, not en passant.
  **FIXED (2026-08-10, its own verification round as demanded).** Band
  model: a clause section opening part-way down the left column splits the
  page into vertical bands read band-by-band; a boundary is the widest
  full-width zero-ink strip between left-column clause headings, and a
  rival strip must be beaten by BAND_TIE_PT (6.0pt, in the measured empty
  band between 3.94 and 9.83) or the page is refused into BAND_UNDECIDED.
  Strip size alone cannot decide (populations overlap: a 33.4pt strip sits
  INSIDE one clause's guidance while the smallest real boundary is
  14.3pt). All nine documents enumerated: 126 firings, 125 cut, 1 REFUSED
  (2257 p38, 14.31 vs 10.37pt — kept one band, attachment_suspect, in
  text this build never emits); only 24 pages change any stream's order,
  and the 6 order-changers in the pre-2014 documents are EXACTLY the six
  declared truncations, found independently. All six repaired with
  terminal punctuation restored; 10 further out-of-scope truncations in
  the 2014/2015/2016 gap-backfill documents also vanished. Six 2012
  subclauses stopped "diverging" between editions and reconciled out of
  by_edition (10.4, 18.6, 19.1, 23.7, 23.8, 24.1) — R22(a)'s prediction
  confirmed independently. Items: 14 changed/gained text (8 repaired 2011
  items, 6 recovered 2012 19.1 items incl. AUTH/2509/6/12's
  builder_block_attachment refusal); **delta per docs rule: the entry's
  "13 items shipping affected text" was an overcount — the 5
  (2012, 10.2)/(11.2) items were page-flagged but byte-identical; real
  live damage was 8**. PDF-level unresolved 25 → 19 (all remaining are
  genuine edition divergences / undecidable assignments / AUTH/3115).
  Census unchanged 10,031; field-diff clause_text-only; both guards
  proven to fire; full regime green; double-built. Standing caution: the
  band rule was measured on NINE documents — the 2001/2003 editions
  fetched 2026-08-09 must get the same per-firing classification when
  parsed, not a free pass.
- R22 correction (2026-08-09, delta noted per docs rule): the auditors'
  "8 of 16 are artefacts" list was wrong on two — 14.2 and 4.1 carry REAL
  source differences (supplementary heading wording), and 16.4 differs
  between the two editions as well, not only against the Addendum. The
  builder proved it by reproducing its own rendered() attachment: the
  genuinely divergent set is 4.1, 4.10, 7.5, 14.2, 14.5, 16.4, 18.1,
  21.3, 22.1, 23.1; the artefact set is 1.8 (our solidus join — fixed,
  corpus-decided), 3.1/12.1/22.2 (real print-glyph quirks, comparison now
  three-tier), 19.1/23.7 (R23's reading-order defect, not a diff at all).

### Prompt-condition change (2026-08-09) — T3 system base

Not a defect entry; bookkeeping the same way the head paragraph records "74
prompts changed". bench/run.py now serves T3 items a T3-specific system
base (`T3_SYSTEM_BASE`): the shared base falsely told T3 models "You will
not be shown any ruling … as the PMCPA Panel would" while the extract
contains PANEL RULING sections and the question asks about the Appeal
Board (found by the 2026-08-06 decidability panel, reviewer 3). Proven by
a 288-call before/after render diff: every non-T3 call byte-identical
(system, user, params — T1 36, T1-triage 36, T5 24), every T3 call (192,
both P3 stages included) changed in the SYSTEM half only, user messages
byte-identical. Consequence: **every archived T3 run — the sonnet n=297
board, the gpt-5.1 replication, the opus 15-item rung — was elicited under
the old framing; any T3 run after 2026-08-09 is a NEW CONDITION and must
not be pooled with them.** The site caveat: export_site_data.py renders
prompts through run.py, so after the next export the item pages will show
the new T3 system prompt beside archived answers elicited under the old
one — that juxtaposition needs a visible note in the UI before the next
export ships publicly. Extended same day, layer rebuild landed: **171 more
items' prompts changed** (clause_text null → text: 151×2012, 5×2015,
15×2016; field-diff verified as clause_text + no_clause_text tag only, on
top of the 3,050 from the 2026-08-06 pre-2014 backfill). Same pooling
rule: runs before and after are different prompt conditions for those
items. Wave-2 landed same day, counts final: **261 items RENAMED**
(`bench/id_migrations.jsonl`: R19-only 127, R19+R20 84, R20-only 50 —
regenerate with `bench/id_migrations.py`, which refuses if an item
vanishes with neither a new id nor an exclusion row), **14 items removed**
with exclusion rows (6 R18 dual-ruling, 8 `code_year_undecided`). Census
10,045 → **10,031** (T1 4,298 / T1-triage 5,419 / T3 314); T5
byte-identical. Archived-run impact: of the 297-board T3 ids — 286
unchanged, 8 renamed (mappable), **3 absent with reasons** (the R18 pair
plus `T3-0bb7eb561480f074` = AUTH/3174/3/19 cl 2, which the exporter's
archived-witness join now attributes to a `dual_ruling` exclusion from an
earlier rebuild — the R16 mystery id, explained; delta from this entry's
first "2 gone" wording noted per docs rule). Phase A board: 147 + 2 mapped
+ 1 absent (`T1-e89a2869b309b9fd`, AUTH/3723/1/23 cl 9.1,
`code_year_undecided`). Two census bases exist and disagree on scope, both
recorded: the wave-2 build counted 1,135 pre-wave-valid ids → 1,111
unchanged / 21 renamed / 3 gone; the exporter counts 942 distinct
answered ids against the current bank → 802 unchanged / 14 renamed / 126
unresolved (the extra unresolved are T5 answers, a separate file by
standing policy, and R16's pre-l2.4 ids that were already invalid before
the wave). Neither is wrong; they answer different questions.
Field-diff proof: beyond item_id, code_year (261 + 211 metadata),
clause_text (125), appellant (3) and the two derived tags, NOTHING moved —
labels 0, splits 0, extracts 0, provenance 0. Bookkeeping: adj-0002's
source_sha256 re-pinned (verdict `sources` gained the three year-receipt
keys, changing the receipts' shape; underlying values unchanged).

### Found 2026-08-10 by the everything-audit (round 1: first ~50 of 1,484 cases)

Provenance: gpt-5.6-sol checkers (Codex CLI, one per case, read-only) plus
the earlier sonnet shakedown produced 19 label-breaking flags; every one
was adversarially re-derived by an Opus verifier (default REFUTED when
ambiguous). Score: 7 confirmed, 3 refuted, 4 already-registered-and-real,
1 uncertain. Full report with all quotes:
bench/review/audits/2026-08-10_everything_audit_round1.md.

- R24: **OPEN — CRITICAL — a decimal-point hole in the ruling-language
  pattern defeats the leakage battery, its "independent" validator, and
  the generation tripwire simultaneously; 4 items ship with the answer in
  the prompt.** `[^.]{0,70}` in RULING_RE (l2/build.py:924) cannot cross
  the decimal point of a clause number: "No breach of Clause 2 was ruled"
  matches, "No breach of Clause 9.2 was ruled" does NOT — proven by
  direct execution. The same construction exists in l2/validate.py:64
  (V_RULING_RE — byte-identical to the builder's, so the "two independent
  readings" guarantee is nominal for this pattern, hole included),
  bench/generate.py:253 (TRIPWIRE) and bench/validate.py (imports it).
  Consequence 1: AUTH/1797/2/06 — a column-scrambled Code of Practice
  Review page — has its complaint segment running to end-of-pane,
  swallowing "No breach of Clause 9.2 was ruled." plus the case trailer;
  the span was attested `clean: true`. Four items (2 with the verbatim
  answer for their own clause, ALL FOUR IN THE TEST SPLIT):
  T1-27775a7a5c155947, T1-triage-004735b81d4daacb (cl 9.2, no_breach,
  answer printed), T1-3ec85bc93ef5e147, T1-triage-0457660ad715af63
  (cl 9.1, sibling-clause ruling + trailer visible). Damage BOUNDED by an
  independent decimal-safe sweep over all 10,027 items' extract_text +
  renditions: 10 non-T3 hits, 8 verified as legitimate precedent
  citations of other named cases; AUTH/1797 is the only case whose
  extract contains the report's date trailer. No archived run or subset
  served these items — no published number is affected; the bank and the
  zero-leakage CLAIM are. FINDINGS §2's "zero ruling-language
  corpus-wide" is false as written (corrected same day with the delta).
  Repairs: decimal-tolerant pattern in all four sites (fixture must
  include a decimal clause number to prove the guard), re-run the
  leakage sweep, fix or exclude the four items, and make l2/validate's
  reading ACTUALLY independent rather than a byte-copy.
- R25: **OPEN — segment-boundary corruption from two-column source pages
  (D4's family, new form).** AUTH/1798/2/06: the ENTIRE complaint input
  for T1-triage-935b57cec0e67152 and T1-triage-ea835e34784d2a42 is 194
  chars, truncated mid-sentence and spliced with a clause traceable to
  AUTH/1797/2/06's Bayer response — the neighbouring case's column bled
  in on a single-case page, invisible to the multi-case detectors (D4 is
  scoped to undeclared multi-case pages; `multi_case_undeclared` has 135
  rows, none here). Also: renditions have a 200-char floor
  (MIN_RENDITION_CHARS) but PRIMARY complaint/response segments have no
  floor at all — a 194-char primary input raised no guard. Repair: a
  primary-segment floor plus a column-order sanity check for Review-page
  cases (AUTH/1797's PANEL RULING precedes its COMPLAINT in the flattened
  text — the tell is measurable).
- R26: **OPEN — rendition leakage: the report_abstract rendition can name
  the clause and outcome in its HEAD.** DESIGN §4's caveat guards only
  rendition TAILS ("only their leading allegation spans are quotable"),
  but AUTH/1888/9/06's abstract OPENS "VOLUNTARY ADMISSION BY BAYER
  Breach of undertaking Clause 2" — served under the P1/P3 rendition axis
  for T1-triage-5e0042c65b6328d6 (cl 2, breach). Exhaustive sweep: 2
  leaky items corpus-wide (the other: T1-triage-4f0b40ee937c4025,
  AUTH/2528/8/12, "VPRIV press release breach Clause 2", TEST split).
  Repair: run the (fixed) leakage battery over rendition HEADS, exclude
  or trim the two.
- R27: **OPEN — the unmeasured recall cost of "only the outcome lists
  create verdict rows": 417 (case, clause) pairs across 262 cases where
  the Panel's prose explicitly rules and L2 publishes NO verdict row**
  (384 after conservatively removing precedent-risk and renumbering-gloss
  neighbours; 12/12 random sample verified genuine, e.g. AUTH/2175/10/08
  "No breach of Clauses 18.1 and 18.4 was ruled"). These are labels the
  bank does not have — mostly no_breach, so the absence is not
  label-neutral — and the design that causes it (resolve_verdicts: a
  prose ruling with no list entry raises a warning, never a verdict) is
  documented but its cost was never measured or stated in SPEC §5. Plus
  22 `ruled accordingly` rows where L2 has panel: null (10 on appealed
  bases — AUTH/1902+1903 cl 18.1 recoverable as 1 T1 + 1 T1-triage + 1
  T3 with Appeal Board prose "upheld the Panel's ruling"; the frame
  "contrary to the requirements of Clause N … and ruled accordingly" is
  R17/R18's family, still unreadable by L2). A DESIGN decision, not a
  patch: whether prose rulings may create rows (with what witnesses), or
  the recall cost gets stated in SPEC and FINDINGS as a measured
  limitation.
**R24/R25/R26 FIXED (2026-08-10 fix wave; full report
bench/review/audits/2026-08-10_r24_r25_r26_fix.md).** The hole was FOUR
holes: the decimal point, the "Clauses" plural (`\b` failing on the s),
the "Code N.M" spelling, and adverb-split verbs ("was THUS ruled" — 197
ruling sentences corpus-wide; the positive-passive form had NO tripwire
pattern at all). Fixed via `_GAP = (?:[^.]|\.(?=\d))` in builder+tripwire;
l2/validate.py now reads by token/window with NO regex to copy, and the
independence is DEMONSTRATED: reverting the builder fix makes the
validator fail the build naming AUTH/1797's segments ("build says True,
recomputation says False"). Standing battery verify/ruling_battery.py (17
rows against three implementations); SPEC §6 now STATES the check so both
readings can be written from prose. Corpus re-sweep, replacing the
corrected FINDINGS claim: 18,354 quoted texts (extracts + all renditions,
heads included), **0 attested leaks, 0 tripwire hits**; 9 raw-pattern
hits all verified precedent citations. AUTH/1797 REPAIRED not excluded
(`quotable_tail_cut`: a backward scan dropping trailing date-trailer/
whole-ruling-sentence sections — 18 segments shortened corpus-wide, only
1797's was attest-clean); its 4 items RENAME (provenance is hashed into
item_id — id_migrations.jsonl is now a cross-wave LOG, 265 rows). R25:
floor placed on the ASSEMBLED extract (MIN_EXTRACT_CHARS=200), removing
AUTH/1798's 2 items; the per-segment measurement — 160 items sit on a
sub-200-char segment but only 2 have a sub-200 extract — is REPORTED FOR
CALIBRATION, not acted on; column-tell measurement: the two tells
conjoined identify exactly AUTH-1797 (tell A alone: 3 modern multi-matter
false friends; neither tell fires on 1798 — the floor is what catches that
class). R26: fixed as a SIXTH attest check (`no_outcome_heading` —
headline-not-sentence + clause-number + pre-body, decided over all 389
breach-word headings / 19,214 segments), so leakage stays a data property;
THREE leaky renditions (register said 2 — new member AUTH/2335/7/10, no
live items) and **11 items** lose their report_abstract rendition (the
headline leaks for every item carrying it, not just own-clause items —
superset of the entry's 2). Census 10,027 → **10,028**: −2 floor, +3 NEW
items (the scoped precedent exemption makes AUTH/1854/6/06's complaint
quotable — its citation of Case AUTH/1756/9/05 was the only dirt).
Eval-subset impact zero; 0 archived rows touch any changed id (re-swept).
En-route findings, register lines: (i) `bench/generate.py --use-fixture`
had been BROKEN since l2.1 (renditions as bare refs vs segment indices —
TypeError on every T1-triage item): the fixture path had stopped being
exercised; repaired as a prerequisite for the guard proofs. (ii)
bench/t5_generate.py:307 loops the raw TRIPWIRE, so T5 does not get the
precedent exemption — no effect today (t5_items byte-identical) but the
layers now disagree by construction; align next time t5 is touched.
(iii) AUTH/3286/12/19's abstract rendition carries a party
SELF-admission sentence ("Bristol-Myers Squibb considered that it had
breached Clause 14.1.") — deliberately NOT caught (sentence, not
headline; admissions are evidence by design); recorded as the borderline
the rule chose not to swallow.

- R5 update (2026-08-10): the everything-audit CONFIRMED two live
  members and measured the slot-witnessed class. AUTH/1822/4/06 ships
  Clause 20.2 items whose allegation and ruling belong ENTIRELY to
  sibling AUTH/1823/4/06 (whose own rows were deleted as
  sibling_duplicate — the fold kept the wrong survivor); AUTH/1816/3/06
  ships cl 7.2 items for a clause it was never asked about ("In Case
  AUTH/1818/3/06 … No breach of Code 7.2 was ruled" — also unreadable by
  L2: the report writes "Code 7.2" where the pattern needs "Clause");
  1823's and 1818's genuine rows are the deleted ones. Only 5 of 1,902
  files carry per-case clause splits in their outcome slots; of those,
  1822 is the only one currently shipping wrong-case items (2 + 2
  T1/T1-triage). One false exclusion detail found en route:
  AUTH/1823/4/06 cl 4.1 "folded into AUTH/1822/4/06 (identical …
  label)" — false, the labels OPPOSE (1822 breach, 1823 no_breach).
- R18 update (2026-08-10): (i) NEW dual-ruling member on a task R18's
  sweep never covered (no appeal ⇒ no T3 item): AUTH/1895/10/06 cl 19.1
  — seven explicit per-meeting rulings of BOTH polarities ("a breach of
  Clause 19.1 … was ruled" for Coventry; "did not breach Clause 19.1 and
  thus no breach was ruled" ×6), L2 publishes panel: breach,
  dual_ruling: false; items T1-9defe9b59694eb4e and
  T1-triage-d654482bf290c8bd (both dev split) carry half the truth. A
  mechanical both-polarity sweep over item-bearing cases yields 24
  candidate (case, clause) rows with dual_ruling false — a READ LIST for
  the dual-ruling round, not a defect count (4 hand-checked: 1 real, 2
  precedent citations, 1 censure idiom). (ii) Residue (b) REDIAGNOSED:
  AUTH/1790+1791/1/06 is NOT a heading-vs-slot contradiction — Roche and
  GSK are the two RESPONDENTS ("MSD v ROCHE and GLAXOSMITHKLINE"; the
  body says "The respondents further noted…"), so the heading AGREES
  with the slot; the defect is one layer down: the PMCPA's own slots
  mis-side the parties (cludo:complainant "Merck Sharp & Dohme and
  Roche"), the shipped respondent metadata omits Roche, and the refusal
  needlessly costs 4 T3 candidates whose transitions are prose-witnessed
  ("The Appeal Board upheld the Panel's ruling of breaches of Clauses
  7.2 and 7.3").
- R19 update (2026-08-10): AUTH/1819/4/06 read in full — the sweep
  checker MISSED it (first measured false negative for the gpt-5.6-sol
  tier), and the right disposal is REFUSE, not repair: the Panel names
  BOTH editions and applies the 2003 Clause 19 test because the 2006
  requirement is disapplied in the Jan–Apr 2006 transition window;
  nothing on the page decides the slot. What would decide it: the
  journalist meeting's date vs 1 January 2006. Until then: R19's
  KNOWN_ABSENT treatment, 8 items.
- R17 update (2026-08-10): AUTH/1903/10/06's six exclusion rows are
  FALSE DESCRIPTIONS of the report (Lead 12 CONFIRMED): the Panel ruled
  on 18.1 in both sibling cases ("contrary to the requirements of Clause
  18.1 and ruled accordingly. This ruling was appealed.") and the Appeal
  Board upheld ("The Appeal Board upheld the Panel's ruling of a breach
  of the Code") — one frame unreadable by the panel reader, the other
  clause-anonymised. Recoverable: 1 T1 + 1 T1-triage + 1 T3 (labels
  breach/breach/upheld) after the sibling fold.

### Decisions taken (2026-08-10)

1. **Dual-ruling representation: option (c)/B, two stages.** Stage 1 (now):
   L2 represents multiple per-regard rulings on one clause, BOTH
   adjudication axes, with per-regard receipts; the scalar panel/
   appeal_board fields keep their existing refusal semantics for duals
   (downstream unchanged); appeal-axis dual DETECTION added (it did not
   exist — AUTH/1941's flattening). Stage 2 (later, separate decision):
   which regards become items. 2. **R31**: sanction-chip needles get a
   distinctiveness floor AND the generator refuses rather than falling
   back to another matter's segments. 3. **R32(i)(ii) + R18(b)**: approved,
   batched as ONE prompt-condition boundary. 4. The read queues (D3, N2,
   precedent-guard residue) proceed. The Codex sweep is PAUSED at ~350 of
   1,484 cases (ChatGPT plan limits exhausted); its remaining
   material/minor flags are triage input; the sweep resumes when limits
   allow.

### Wave C — triage and the final repair wave (2026-08-11; full reports
bench/review/audits/2026-08-11_wave_c_triage.md and
_wave_c_referee_repair.md)

729 material/minor flags triaged (4 sonnet batches: ~229 confirmed / ~386
refuted / ~93 registered / ~25 stale / 0 uncertain), then class-refereed
and repaired by opus. LANDED: the silent 6,000-char clause_text cap
REMOVED (R33-class find — FOUR copies including one in the "independent"
verifier, so it could never self-detect; all 38 truncated renderings were
legitimate whole clauses 6.6k–10.7k chars; 643 items + 2 T5 items
un-truncated; loud 20k refusal proven both ways); complainant-role frames
ruled DEFECT on the design comment's own third example (392 category
values corrected over 49 cases, all hand-read; the shared-blind-spot
oracle bypassed and a 101-value decided title vocabulary with build
refusal added; the opening-only WINDOW stays as documented design);
RULED_UPHELD_RE gains missing-"of" + guarded passive arm; adj-0154
recovers AUTH/2414 18.1 — **T3 318 → 328 (+9 upheld, +1 overturned)**;
voluntary-admission rendition headline added to no_outcome_heading (140
renditions dropped over 39 cases); director_initiated rewritten
positionally (7 NHS titles / 8 cases un-mislabelled); trailer parser
plural + composite; 2,877 exclusion-detail strings de-conflated. Bank
**10,205** (T1 4,372 / T1-triage 5,505 / T3 328); 0 renames, 0 label
changes, boards unchanged; prompt-input drift on served ids ~6–11% per
board, quantified in the report. NEW RESIDUALS (open): RULING_RE F1
recall cost (334 segments, fix REFUSED on the R24 argument — loosening
the leakage pattern mints unread spans); **own-case-number exposure:
3,156 items carry their own case number in served text — identity/
memorisation not outcome; redaction is an OWNER DESIGN DECISION and a
prompt boundary**; Code-PDF fused words source-faithful (166 items / 55
cases); T3 date-trailer-in-extract (107 items, no outcome revealed);
AUTH/1851/1898/2162/2317 segment defects (need per-case reading; the
multi-[COMPLAINT] shape is the corpus norm — no blanket rule is safe);
AUTH/2183 receipt-only miss; AUTH/2355 title-line role source; AUTH/2108
company gazetteer; AUTH/2059 STRUCTURAL_CATEGORIES review. Judgement
call flagged: AUTH/2361 employee→health_professional via the
complainant's own pen name — both readings true of one person.

### Read-queues round (2026-08-11; full report
bench/review/audits/2026-08-11_read_queues_round.md)

D3's hand-review DONE (10 rows: AUTH/2386's five + 1977's 8.1 were
list-column typos, prose right — 9 labels corrected breach→no_breach;
AUTH/2437 9.9 and AUTH/3432 4.3 are genuine DUALS, adjudicated;
AUTH/1822 4.1 was cross-case bleed, value stands; **AUTH/1823 4.1
REFUSED — the published breach IS wrong but the only per-case repair
mints two byte-identical prompts with opposite labels: R5's layer, not a
polarity adjudication; the wrong value stands, recorded**). N2 CLOSED
(20 slot renames onto the clause actually ruled — incl. the 7.10→10.1
transposition and 3388's 24.1→22.4; 7 phantom rows deleted incl. both
Constitution-paragraph artefacts; paragraph_17 procedure fixed;
`check_clause_witness_coverage` now REFUSES any published clause with no
witness in the case's own text, proven three ways). Stage-2 T3 recovery:
AUTH/2488 recovered (overturned transition real but no T3 — attest-dirty
response); AUTH/3809's six rows recovered (possessive-heading gap noted);
AUTH/1902+1903's predicted T3 REFUTED — the Appeal Board ruled 18.1 both
ways (dual_ruling_appeal_board), T1 pair generated instead. Precedent
registry closed at 30+2 read rows (AUTH/3641 refuted as recital;
AUTH/3763 28.5 gains attribution, 2 items). Census 10,192 → **10,195**
(T1 4,372 / T1-triage 5,505 / T3 **318** — two new T3 upheld via Q2);
adjudications 153; migrations 398; published boards UNTOUCHED (0 renamed
ids, no label changes on served items; 3 non-board served ids get
clause_text-only prompt drift, noted). code_year_witnesses fired
correctly mid-round on a stale KNOWN_ABSENT row (2190's 3.3→3.2
correction) — the guards guard.

### THE CLOSING AUDIT ROUND (2026-08-11) — the last wave before freeze

Five strands: a miss-rate measurement on the automated sweep, a
repair-class spot verification of every landed wave, the four parked
segment cases and three metadata one-offs, the AUTH/2361 precedence
question, and the own-case-number redaction.

**1. Miss rate of the gpt-5.6-sol checker, measured.** Sampling rule
fixed in advance: of the 492 cases the Codex sweep completed, the 27
whose result-file slug has character-sum ≡ 0 (mod 20); of those, the
**17 it attested `clean: true`** (82 items). Each was re-read
adversarially by an Opus reader on the full ticklist, told to default to
CONFIRMED CLEAN when ambiguous, and each re-implemented
`bench/validate.py`'s re-slice rule rather than importing it. Result:
**15 CONFIRMED CLEAN, 2 MISSES**.

- **AUTH/1848/6/06 — CONFIRMED MISS, label-premise, 8 items, FIXED
  (adj-0158).** Tagged 2003 by both PMCPA slots; the Panel's own
  reasoning quotes supplementary information that exists only from 2006:
  "The supplementary information to Clause 20.2 stated, inter alia, that
  meetings organized for or attended by journalists must comply with
  Clause 19. The supplementary information to Clause 19.1 stated that
  delegates must not be offered compensation merely for their time spent
  at meetings." Re-verified independently against
  `data/code/pdf_clauses.jsonl`: `compensate merely` and `time spent by`
  occur in the 2006 and 2008 clause-19 rows and in **no** 2001 or 2003
  row; `comply with Clause 19` occurs in the 2006 clause-20 row and in no
  2003 or 2001 row. Complaint received 19 June 2006, completed 24
  September 2006 — past the 2006 edition's 30 April transition. The two
  Clause 19.1 items had been served the 2003 clause, **which contains no
  prohibition on compensating a delegate for their time — the whole basis
  of the ruling was missing from the text the model was handed**; Clauses
  2 and 9.1 are textually identical across the editions, so the other six
  carried a false premise in the question header only. All 8 rename; two
  (`T3-02fdfacc840baf91`, `T3-671ea061f214e306`) sit on the archived T3
  boards, whose prompt era Wave B already closed.
  **Recall note:** neither reading could have caught this. The mechanical
  tier is one-sided (commencement(2003) precedes completion, so the
  impossible-cluster test is silent) and tier (a) needs a
  "considered under the YYYY Code" sentence, which this report does not
  contain. What decided it was EDITION-EXCLUSIVE VOCABULARY — the third
  time that witness has been the deciding one (R29's `subsistence` on
  AUTH/1896, the 2012 first/second-edition assignments). It is a reading,
  not a rule, and there is no guard to add.
- **AUTH/1984/4/07 — MISS, minor, 4 items.** A print running header
  spliced mid-sentence into the served response and (on the T3 pair) the
  quoted Panel ruling: "…did not mislead physicians **Code of Practice
  Review August 2007** especially in regard to the use of NHS
  resources…". Registered, not repaired: the register's own Wave C
  triage splits this family by sub-shape (header in its OWN `<p>` →
  REFUTED source-faithful; header spliced mid-sentence → CONFIRMED, with
  AUTH/2023/7/07 as the exact twin), so 1984 belongs on the confirmed
  side and joins that open class rather than being fixed alone.

**Measured miss rate.** On the sample as drawn: **2 of 17 clean-attested
cases (11.8%)**; counting the known prior false negative AUTH/1819/4/06
(also clean-attested, also code_year, disposal REFUSE), **3 of 18
(16.7%)**. Restricted to defects that reach a label or a premise:
**1 of 17 (5.9%)**, or 2 of 18 (11.1%) with AUTH/1819. The class is not
random: **all three misses are code_year**, and two of the three needed
edition-exclusive vocabulary to settle. Read the checker as
approximately 6% blind on label-bearing defects and ~90% blind on this
one class — which is why the code-year adjudications were done by hand.
The 15 clean confirmations were not cheap agreements: the readers
surfaced and correctly declined AUTH/1873/8/06 (2003 tag inside the 2006
transition window, but the two editions' Clause 4.1 are byte-identical,
so nothing served is wrong and no label turns on it), several
`no_outcome_heading` and burden-of-proof near-misses, and R27's cost on
AUTH/2274/10/09 (**two overturned T3 candidates lost there** — the
scarce class the whole T3 finding rests on; input for the R27 round).

**2. Repair-class spot verification — NO REGRESSION.** Three members of
each landed wave re-verified against the reports' own words in the
current bank: clause-text backfills (AUTH/3184 cl 29 2016; AUTH/2685 cl
18.1 second-2012 with AUTH/2517 as the first-2012 control; AUTH/2471 cl
19.1 2011, where R23's 475 spliced characters are gone — "three calendar
months" occurs 0 times); code-year adjudications (adj-0095 AUTH/2044,
adj-0097 AUTH/2048 — 2006 cl 22 is *Compliance with Undertakings* where
2008 cl 22 is *Relations with the Public and the Media*; adj-0106
AUTH/1896); dual detection (AUTH/1941's appeal-axis pair excluded and its
T1 items surviving, AUTH/2437 9.9, AUTH/3432 4.3); the prompt boundary
(AUTH/1871 7.2 respondent, AUTH/2528 22.1 complainant with one neutral
and eight under-appeal blocks, AUTH/2008's recovered matter at
34702–41105) — plus an independent bank-wide re-check that
`metadata_shown.appellant` takes exactly two values, **respondent 210 /
complainant 118, the `both` cell empty**; and the Wave C referee wave
(adj-0154's AUTH/2414 recovery, AUTH/2364's `writing as '<role>'` frame,
AUTH/2180+2181's positional `director_initiated`, plus the cap removal
checked bank-wide: **0 items at exactly 6,000 chars**, 38 combos spanning
6,605–10,742, all ending in terminal punctuation).

**3. The four parked segment cases — 2 real, 2 refuted.**

- **AUTH/1851/6/06 — REPAIRED by receipt.** The Clause 3.2 items served
  matter 2's complaint (report 8100–9926), which alleges Clauses 7, 7.2
  and 7.3, never names 3.2, and carries the spliced header "71 Code of
  Practice Review November 2006". Root cause named exactly: bench
  reconstructs the matter partition from ruling receipts
  (`matter_starts` reads `verdicts[].rulings[].regard_ref`) and matter 2
  has **no verdict row at all** — the Panel ruled the advertisement
  outside the UK Code's scope, so it never entered an outcome list
  (R27's shape) — leaving matter 1's span unbounded and
  `own_matter_refusal` unable to fire. Fixed via
  `MATTER_SCOPE_REFUSALS` in `bench/generate.py`: a quote-pinned
  per-case registry that drops the named span and **refuses the build if
  the text there moves**, plus a refusal if the registry fires on
  nothing. The two items keep a complete allegation and response (2,524
  and 1,012 chars) and RENAME (dev split, no board).
  **Why a registry and not a rule, measured:** partitioning from L2's
  full computed heading list and scoping `pick_segments` to the clause's
  own matter takes a served segment from **1,120 items across 156 cases
  (11% of the bank)**, and the heading list is incomplete in both
  directions (letter-enumerated matters like AUTH/2162's "B Letter to a
  hospital consultant"; headings split across two L1 sections) with 3
  running-header false positives in 642. **NEW OPEN RESIDUAL:** that
  1,120-item multi-matter scoping wave, which needs its own round, a
  re-split and a board re-derivation.
- **AUTH/1898/10/06 — REFUTED.** The page is DECLARED, not undeclared
  ("CASES AUTH/1898/10/06 and AUTH/1900/10/06 GENERAL PRACTITIONERS v
  PROCTER & GAMBLE"), both records carry each other as siblings, the
  9.1 ruling is a single joint sentence, the response is jointly headed,
  and AUTH/1900's rows are folded with `sibling_duplicate` rows. The
  flagged string is a case-marker heading absorbed into a segment tail
  (19 (file, segment) pairs corpus-wide, 92 items, **19 distinct values,
  every one a bare case marker**); a trim rule is decidable but was
  DELIBERATELY NOT TAKEN — it renames 92 items, and the redaction below
  reduces the string to "Case [CASE NO.]" at zero id churn. Registered
  as measured-and-declined.
- **AUTH/2162/8/08 — REFUTED.** The 7.4 substantiation allegation IS
  served, twice: "…could not be **substantiated in breach of Clause
  7.4**" at char 2,435 and "…in a **breach of Clauses 7.2 and 7.4**" at
  char 4,719, matching both matters the 7.4 verdict was ruled in. The
  original flag read the tail (a 77-char cross-reference response, whole
  and full-stopped) and generalised. It is a member of the 1,120-item
  over-inclusion class, not a defect of its own.
- **AUTH/2317/5/10 — ACCEPTED AND REGISTERED, with the order proved.**
  The scramble is in the publisher's own HTML (three line-fragments
  emitted as standalone `<p>` elements at bytes 60,869/60,952/61,009),
  so L1 is faithful and there is nothing for us to repair there. The
  correct order IS recoverable — the PMCPA's own *Code of Practice
  Review* November 2010, pp. 70–71 (`data/publications/text.jsonl`,
  `cop_review__2504__2010-november-review.pdf`) sets the same passage
  with every join intact, and each of the four displaced fragments has
  exactly one landing site — but the repair needs a MULTI-SPAN segment
  ref, a schema change, and this is freeze. Measured statement:
  **171 chars = 0.70% of the served response** on 4 T1 items (the 4
  T1-triage items are unaffected), no text lost, two sentence-joins
  broken, all four labels `no_breach`. Exclusion refused — it would cost
  4 items to remove 171 characters of complete, misordered text.
  **NEW OPEN RESIDUAL**, with the follow-up named: the `". <lowercase>"`
  tell surfaces candidates in AUTH/1911+1912, 2060, 2028, 1984+1985 and
  665 hits overall, and each needs the same Review-PDF cross-check —
  cheap now the route is known. No general detector is offered: the three
  tells measured (882 / 270 / 665 hits) are all dominated by bullet
  markers and `et al.`

**Three metadata one-offs.**

- **AUTH/2327/6/10 — RULE.** The complaint was taken up through a NAMED
  body ("The Medicines and Healthcare products Regulatory Agency (MHRA)
  advised the Authority… The matter was taken up as a complaint under the
  Code") and `prose_anonymous` was read off a sentence about a DIFFERENT
  actor, the MHRA's own informant. Sentence-level anchoring is not
  subject-level anchoring. Two guards, because one is not enough —
  skipping "anonymous source" lets the loop reach the PMCPA's standing
  "Anonymous complaints were accepted…", which is anchored on its own
  subject and sets the same wrong value. Decided over **every anonymity
  firing in the corpus (686 cases, each sentence recovered and read)**:
  the first shape occurs in 1 window, the second in 13, and together they
  move **exactly one published value**. The positive form was tried and
  REFUSED: the token attaches to 75 distinct head words and
  `complaint`/`complaints` is used both for the real complainant and for
  the procedural sentence, so no head-noun vocabulary can separate them.
  Delta: `anonymous true → null` on 6 items; AUTH/2370/11/10's receipt
  quote also improves (value unchanged).
- **AUTH/2108/3/08 + AUTH/2109/3/08 — ADJUDICATED (adj-0155/0156).**
  "Orphan Europe complained about the promotion of…"; "Orphan Europe SARL
  was the marketing authorization holder". `company` is reachable only by
  folding the complainant against the RESPONDENT vocabulary, and Orphan
  Europe is never a respondent here (fold key absent from all 217 keys).
  The general alternatives were measured and refused: an "inter-company"
  prose signal is right on 135 of 159 windows (85% — the R11 failure
  mode), and a company-name gazetteer would have to decide a 55-value
  residue containing `ESPRIT`, which is a professional group, not a firm.
  Both siblings carry a row: the complainant is resolved per PAGE and
  published per CASE. New plumbing:
  `apply_complainant_adjudication` (a third sibling of
  `apply_appeal_adjudication`, only `category` moves, pinned sha) and
  `inter_company` now derived per case AFTER the adjudication.
- **AUTH/2355/9/10 — h1 INADMISSIBLE, title line ADJUDICATED
  (adj-0157).** First, the flag's own premise was wrong: the role is not
  in the h1 (which reads "Complainant v Takeda") but in the report's own
  title line, "CASE AUTH/2355/9/10 MEMBER OF THE PUBLIC v TAKEDA" at
  report pane char 0 — the only occurrence of that phrase in the file.
  Making the title line a general source is REFUSED on measurement:
  applying CATEGORY_RULES to its complainant side disagrees with the
  published category on **379 of the 1,036 cases that parse, 177 of them
  company→other** — the title line is usually LESS informative than the
  meta slot. Restricted to the last-resort position it would decide 171
  cases over 57 values and move 7, i.e. the same reading written 57
  times, and two rows would need owner sign-off anyway (AUTH/2082/1/08,
  where the leading "DIRECTOR," is the MHRA's, not the PMCPA's;
  AUTH/2818/1/16, whose own opening describes a clinician). **NEW OPEN
  RESIDUAL: the other 6 members** (AUTH/2065+2066, 2082, 2818, 3735,
  3804), plus the note that AUTH/2065+2066 are not really this class at
  all — bare `representatives?` is in CATEGORY_RULES but missing from
  PROSE_ROLE_RULES' employee arm.

**4. AUTH/2361/10/10 — the precedence RULE (this round).** An
EMPLOYMENT STANDING outranks a professional role when one person attests
both. Written as a rule with its reason, not an exception: "An anonymous
complainant writing as an 'ExCephalon hospital specialist'" is an
ex-employee of Cephalon AND a hospital specialist, both true; the
employment is a RELATION TO A PARTY and the role is an ATTRIBUTE OF THE
PERSON, and it is the relation that shapes the evidence. The role is
never lost (note + `sources.prose_role_verbatim`). New basis
`complainant_employment_outranks_role`. **Sweep: 5 members, 42 items** —
AUTH/2361/10/10 (12), AUTH/3203/6/19 (6, "An anonymous employee who
described him/herself as a concerned health professional"),
AUTH/3204/6/19 (8), AUTH/3790/7/23 (8, "a health professional and
ex-employee") and AUTH/3454/1/21 (8), which was publishing
**`member_of_public` for a self-described "Sanofi representative"
against a Sanofi respondent** — a plainly wrong value the sweep found
and the rule fixes. **0 cases run the other way** (prose employment
against a meta role), where the prose-first default already gives
employment the value, so the rule is symmetric by construction.
**ONE MEMBER FOR TIM'S EYE:** on AUTH/3204/6/19 the employer named in
the prose is Otsuka while the respondent is GlaxoSmithKline — an
industry employee complaining about a competitor "in his/her private
capacity". The meta slot still states the employment standing and the
rule takes it; if the rule should be narrowed to employment TO THE
RESPONDENT, that is the one case that leaves.

**5. OWN-CASE-NUMBER REDACTION — the final prompt boundary (owner
decision).** In SERVED text only (`extract_text` + renditions at
generate time; L1 and L2 keep the raw text), the item's own and
co-reported case numbers are replaced with `[CASE NO.]`. OTHER cases'
numbers STAY — "In Case AUTH/1756/9/05 a breach of Clause 7.2 was ruled"
is the complainant's own argument put in front of the Panel, and it is
the span the tripwire's precedent exemption is built around. The channel
closed is IDENTITY/memorisation, not outcome.

*Anchored on the SERIAL, not the string*, because the corpus misspells
its own numbers in every other position, and serials are globally unique
(0 collisions over 2,004 cases; AUTH 1789–3926 does not meet CASE
209–838). The variant table, DECIDED over every own-serial occurrence in
the served text of the 10,205-item bank:

| spelling | occurrences | disposal |
|---|---|---|
| `AUTH/1851/6/06` and every separator malformation — `AUTH/3166//2/19`, `AUTH/2240/6//09`, `AUTH/ 3422/11/20`, `Case/0221/07/24`, `CASE AUTH2583/3/13`, and wrong-prefix/wrong-month forms (`Case AUTH/0274/08/24` for CASE/0274/08/24; `CASE AUTH/2102/3/08` for AUTH/2102/2/08) | 3,575 | REDACT |
| prefix + serial, no month/year — `Case 0216` | 8 (1 string) | REDACT |
| serial/month/year, no prefix — `0216/06/24`, `0496/03/25` | 18 (2 strings) | REDACT |
| bare serial inside an all-serial comma run — AUTH/2070/11/07's source artefact `2070, 2072, 2073, 1993, …`, every number a known case serial, and the only run of ≥3 comma-separated four-digit numbers in the bank | 6 (1 string) | REDACT own+siblings only |
| ISO date whose YEAR is the case's serial — `2007-05-31` in `metadata_shown.date_received` | 3 items | NOT a case number: **no dash form exists anywhere in the served corpus** |
| serial in any other context | 0 | — |

182 prefixed tokens (27 distinct) carry a serial no corpus case has —
every one hosted by a DIFFERENT case, i.e. citations of cases never
scraped — so **no own-case number hides behind an unknown serial** and
serial-anchoring loses nothing.

*Proofs.* (a) A build-time refusal re-derives the whole measurement over
every served string and stops the build naming any undecided spelling —
and it FIRED on the first run, on the ISO-date collision above, which is
how that row got decided instead of assumed. (b) `bench/validate.py`
carries an INDEPENDENT implementation — a hand-written character scanner
with no regular expression in the reader, the explicit answer to R24's
byte-copy lesson — used two ways: it re-slices the raw spans, re-derives
the redaction and compares (so the extract is still proved to be the
report's own words), and it runs a separate **standing zero-scan** over
`extract_text`, every rendition and every `metadata_shown` string.
Both agree on all 10,205 items. (c) Independence DEMONSTRATED the way
R24's fix demonstrated it: narrowing the builder's rule four ways —
dropping the no-month/year arm, allowing only a single slash, disabling
the bare form, disabling the comma run — makes the checker fail every
time, naming the item and quoting the string ("…the complaint in Case
0216 concerning…", "…receipt by Moderna of complaint 0496/03/25…",
"…selected the speakers; 2070, 2072, 2073, …"). (d)
`metadata_shown` is asserted clean, not assumed — the allowlist carries
no case number and both readings check it.

**Scale and consequences.** **3,153 items' served text changes** (T1
203/4,372 · T1-triage 2,919/5,505 · T3 31/328 — the T1-triage weight is
the `report_abstract` renditions, whose headlines open with the case
number). **0 items renamed by the redaction**: `item_id` hashes task +
case number + clause key + provenance SPANS, never the quoted
characters — verified over the whole bank. Minimum served extract after
redaction is 220 chars, so `MIN_EXTRACT_CHARS` is untouched either way.
**T5 gets the same rule, imported not re-typed**, and `t5_items.jsonl`
is BYTE-IDENTICAL: 0 of the 27 items' surfaces contain their own case
number (T5 material is the advertisement itself, which does not cite the
case it became). While there, the registered t5/bench divergence was
closed — `leak_hits` now calls `gen.tripwire_hit` instead of looping the
raw TRIPWIRE, so T5 gets the precedent exemption; byte-identical either
way, which is why it was safe to align now.

**Served-prompt drift per board** — the share of each board's items whose
prompt input moved in this ROUND (extract, renditions, clause text or
code year), resolved through the full `id_migrations` chain. `resolved`
is how many of the board's rows still map into the current bank; the
shortfall is earlier waves', not this one's.

| board | n | resolved | drift |
|---|---|---|---|
| Phase A (T1) | 150 | 145 | 34 (23.4%) |
| T3 · 297 | 297 | 276 | 22 (8.0%) |
| T3 · 297 r2 | 282 | 263 | 21 (8.0%) |
| T1 year subset | 220 | 210 | 9 (4.3%) |
| probe_era | 150 | 150 | 91 (60.7%) |
| Phase A rest | 120 | 115 | 30 (26.1%) |
| T3 rest | 267 | 248 | 20 (8.1%) |
| T5 probe | 24 | 24 | **0 (0.0%)** |

Two of the T3 boards' drifted rows are AUTH/1848's, where the CODE YEAR
in the question header moved as well as the text — the rest is
redaction. probe_era's 60.7% is the T1-triage weight again: it is a
rendition-heavy subset.

**6. Closing verification.** Full regime green: `l1/build` + `l1/derive`
+ `l1/validate` + `l1/coverage` ("every visible token in every `<main>`
region is present in its record"); `l2/build` + `l2/validate`;
`bench/generate` + `bench/validate`; `verify/candidate_accounting`,
`verify/received_date_witnesses`, `verify/vocabulary_coverage --strict`,
`verify/code_year_witnesses`, `verify/ruling_battery` (all three readers
agree), `verify/pdf_clause_texts` under the pinned pypdf==6.1.1.
**Census unchanged at 10,205 (T1 4,372 / T1-triage 5,505 / T3 328)**;
exclusions 7,908; adjudications **158**; migrations **400** (+2, both
AUTH/1851; the wave label is `2026-08-11-closing` — the read-queues
round's own 30 rows are wave `2026-08-11` and must not be overwritten).
Field-diff of the bank against the pre-round copy, every path attributed
and nothing left over: `inputs.renditions` 2,883 and `inputs.extract_text`
304 (the redaction, plus AUTH/1851 ×2); `metadata_shown.complainant_category`
53 (42 from the 2361 rule + 2 from adj-0155/0156 + 9 from adj-0157);
`tags` 16 (AUTH/2327's 6 `anonymous_complainant`, AUTH/2108+2109's 2
`inter_company`, AUTH/1848's 8 `code_year:`); `clause_ref.code_year` 8 and
`metadata_shown.code_year` 8 (adj-0158); `complainant_anonymous` 6
(AUTH/2327); `extract_provenance` 2 and `clause_ref.clause_text` 2
(AUTH/1851 and AUTH/1848's two Clause 19.1 items). 3,202 items touched,
**0 label changes, 0 split changes, 0 items lost**. Three consecutive
full builds sha-compared: `data/l2/cases.jsonl`, `bench/items.jsonl`,
`bench/exclusions.jsonl`, `bench/t5_items.jsonl`,
`bench/t5_exclusions.jsonl`, `data/l2/code_year_arbitration.jsonl` and
`data/l2/clause_slot_corrections.jsonl` identical across all three.
`bench/export_site_data.py` has NOT been run — the coordinator owns it,
and the review UI will show stale values until it is.

### THE PRE-FREEZE REPAIR PASS (2026-08-11) — three repairs, bounded

Three defects from the luna-leads verification, each re-verified against
the source before it was touched. One of the three did not survive
re-verification in the shape it was reported, and is registered instead.

**REPAIR 1 — AUTH/2505/5/12 clause 3.1 → 3.2 (adj-0159). LANDED.** The
premise defect the AUTH/1848 class is named for, one level finer:
`check_clause_witness_coverage` is silent because the numeral 3.1 IS in
the report — the Authority's scoping sentence types it, "the Authority
asked it to respond in relation to the requirements of Clauses 2, 3.1,
9.1, 15.2 and 15.9" (report 8678) — but it is not the number the Panel
ruled. The one disposing sentence is "**No breach of Clauses 3.2**, 9.1,
15.2, 15.9 and 2 was ruled" (24612–24670), so four of the five published
rows carry it as their witness and **the 3.1 row alone carries
`rulings: []`**. The respondent answers the same number ("ProStrakan did
not believe that **Clauses 3.2** or 15.2 had been breached", 13273) and
the substance decides between them: Abstral held a marketing
authorisation and the allegation is promotion for burns patients, which
the Panel states in terms — "companies had to be extremely careful in
ensuring that their medicines were not promoted for **unlicensed
indications**" (19907). 2011 Clause 3.2 is promotion outside the terms of
the authorisation; 3.1 is promotion *before* one is granted, which this
report never raises. Two items were served the 2011 Clause 3.1 text as
the clause under test. Both RENAME (`T1-abc8f35b829ea87e` →
`T1-a33492f23afecfaa`, `T1-triage-0bfe863a03b63ace` →
`T1-triage-d2f01b62fdf006cd`, wave `2026-08-11-freeze-repair`); the label
(`no_breach`) is unchanged, since all five rows are no_breach.

**The two siblings READ IN THE SAME SITTING AND LEFT ALONE**, both
recorded in `CLAUSE_PROSE_SIBLING_READ` (l2/build.py) with the reasoning:

- **AUTH/1992/4/07 (3.2 published, prose rules 3.1) — LEFT, and it is the
  reason there is no rule here.** The exact mirror: both slots, the
  Authority's scoping sentence (11778) and Sanofi-Aventis' answer (18647)
  all say **3.2**, and only the Panel's two closing sentences type "3.1"
  (29735, 31988). Subject again off-licence promotion of a licensed
  medicine — the complaint's own words are "the Code which explicitly
  forbade off-licence promotion" — which is 2006 Clause 3.2. Here the
  LIST is right and the PROSE is the typo. An auto-repair keyed on the
  shape would have corrected AUTH/2505 and corrupted this one.
- **AUTH/1857/6/06 (20.4 published, prose rules 20.2) — LEFT, and it
  costs nothing.** "No breaches of Clauses 20.1 and **20.2** were ruled"
  (40671), upheld as the same pair on appeal (84884); "20.4" appears in
  no ruling sentence in either pane. Which side is the typo was NOT
  settled and does not need to be: the 20.4 row is
  `verdict_appealed_unattributed` with `panel` null, so **bench builds no
  item from it** (0 items — the case's items are 2, 9.1 and 20.1). A
  repair with no consequence is churn.

**The finer guard, added as a WARNING.** New warning class
`published_clause_unwitnessed_prose_sibling`: a published row with **no
ruling witness of its own** while the Panel's prose rules a **sibling of
it that no outcome list names**. Measured before it was written: the
first conjunct alone is 458 rows; both together are **13 rows in 11
cases** (14 in 12 before adj-0159 — a repaired row acquires the witness
and stops firing, which is the guard behaving). Deliberately not a
refusal and not a rename, because the class does not point one way; the
AUTH/1992 mirror is the proof. The 13 split two ways: **7 same-rank
siblings** (the two read above plus **five NEW and unread** —
AUTH/1916/11/06 18.1|18.3, AUTH/2075/12/07 4.3|4.1, AUTH/2260/9/09
7.9|7.3, AUTH/2336/7/10 9.5|9.1, AUTH/3024/3/18 15.2|15.9) and **6
parent-for-child** (AUTH/1937/1/07 21|21.1, AUTH/1970/3/07 10|10.1,
AUTH/2823/2/16 20|20.1, AUTH/3131/12/18 ×3), which is usually not a
defect at all — the parent is what the outcome list published, the shape
`CLAUSE_WITNESS_READ`'s AUTH/2845/5/16 entry already names from the other
side. **NEW OPEN RESIDUAL: those five unread same-rank rows.**

**REPAIR 2 — the narrator-pass gate. LANDED, as a SECOND ROUND.**
`complainant_prose`'s narrator pass reads the SUBJECT OF A COMPLAINT
VERB, and its verb set has always included `alleged` and `queried`
alongside `complained` — but it was gated on `COMPLAINANT_ANCHOR_RE`,
inherited from the first pass, which requires the word `complain*`
somewhere in the sentence. That gate is right for the first pass
(anonymity, naming and contactability are claims *about* the complainant)
and wrong for this one, whose own verb is already the evidence. So
openings written as "An anonymous consultant neurologist alleged …" were
thrown away by a filter that exists for a different question.

**Not a widened gate — a second round, and the difference was measured.**
A single widened gate ALSO re-sources **44 categories that were already
read**: the wider set admits the report's title line, which carries no
full stop and so runs into the first abstract sentence, and that
sentence, being earlier, wins the break — turning the receipt "A
principal hospital pharmacist complained" into "v SERVIER Alleged breach
of undertaking A principal hospital pharmacist alleged". Trading 44 clean
quotes for heading furniture to gain 19 is not a fix. Round 1 keeps the
old gate; round 2 runs only where round 1 read nothing — wave C's own
discipline when it added this pass, and everything the old code produced
is produced unchanged, byte for byte.

Delta **19 cases**, every one of which had no prose category at all:
**3 move the published value** — AUTH/2500/4/12 ("an anonymous consultant
neurologist alleged") and AUTH/2510/6/12 ("an anonymous physician
alleged") `other` → `health_professional`, AUTH/2879/10/16 ("an anonymous
non-contactable member of the public alleged") `other` →
`member_of_public` — and **16 publish the same value and gain a prose
receipt**, `field_basis.category` moving `complainant_meta_vocabulary` →
`complainant_prose_narrator_role`, the precedence the function already
declares. **30 items** carry the new `metadata_shown.complainant_category`.
NO renames (category is shown metadata, not part of item identity), no
label, extract or split changes. The brief predicted 2 cases / 22 items;
the measured answer is 3 / 30.

**REPAIR 3 — the rendition_cut F1 false boundary. HALF LANDED, half
REFUTED.**

- **AUTH/2465/12/11 — CONFIRMED and repaired.** F1 fired at summary 378
  and report 454 on the **advertisement's own typography**, quoted by the
  retelling: "Beneath the heading '**Recommendations of the Consensus
  Panel**' was a diagram headed '**Qutenza may be considered** for the
  treatment of …'". `Panel` is the last word of one quoted advert heading
  and `considered` the fourth word of the next, 44 characters later, well
  inside F1's 90-character window; no adjudicator is speaking. Both
  renditions stopped at 378/454 characters — mid-description of the
  advert, **before the claim at issue**, the NICE treatment algorithm the
  whole complaint is about — and both were being served as P1/P3
  perturbation variants on `T1-triage-c57a72e0fa895098`. Fixed by
  `RULING_LANGUAGE_FALSE_MATCHES`, a quote-pinned per-file registry in
  the `MATTER_SCOPE_REFUSALS` idiom. Renditions restored to **1,820 and
  1,896 characters**, both ending immediately before the genuine "The
  Panel noted that the prominent title of the advertisement was …", and
  **both `clean: true` on all six leakage checks**.
  **The registry governs BOTH consumers of RULING_RE**, `rendition_cut`
  and `leakage_attest`'s `no_ruling_language`, because a cut that steps
  over a match the attest still counts produces a longer rendition that
  is then marked dirty and dropped — measured: the first implementation
  did exactly that. Blast radius measured over the whole file: the string
  occurs three times (summary 378, report 454, report 4305); the third
  falls in the gap between the abstract (ends 3828) and the complaint
  (starts 5000) and is **inside no segment at all**, so exactly two
  attests move, both back to clean, and both spans were read end to end.
  No general pattern loosening — **R24's argument stands**, and this is
  two read spans, not R24's 72 unread ones.
  `l2/validate.py` holds its **own** copy (`V_RULING_FALSE_MATCHES`) with
  its own reading. That is not a shared witness: what the independence
  rule forbids is sharing the *reading*, and this is a hand *decision*,
  of the same species as `CLAUSE_WITNESS_READ`. Proven load-bearing —
  narrowing only the validator's row made it **disagree with the build**
  on `segments[4] no_ruling_language`, which is exactly what a second
  reading is for. Both registries refuse on a dead row and both refusals
  were fired deliberately.
- **AUTH/3913/5/24 — REFUTED as a rendition defect.** The F1 false
  positive is real — "the PMCPA **Panel has accepted** in multiple
  previous cases that it is appropriate and non-promotional …" (report
  12230, a precedent recital) — but it lands in the **response segment's
  `no_ruling_language`**, not in any rendition cut. The renditions are
  both correct already: the summary cuts at 461 on `OUTCOME_INTRO_RE`
  ("The outcome under the 2021 Code was:"), and the report abstract is
  refused at char 20 because the pane opens on the banner "NO BREACH OF
  THE CODE", which is `rendition_refused_short_span` working. **The
  consequence is a different one**: the response (15,369 chars) is
  withheld, so the case ships 5 complaint-only T1-triage items and no T1
  pair. Admitting it is R24's exact trade — an F1 exemption on a
  precedent recital inside a party's own response — and is NOT taken
  here. **NEW OPEN RESIDUAL**, its own class: precedent recitals in
  RESPONSE segments, which cost T1 items rather than rendition length.

**AUTH/2494/3/12's three `rendition_not_covering` detail strings — LEFT,
registered.** The condition was "only if a one-line wording correction is
honest without repartitioning matters", and it is not. The string the
rows quote as "the report's heading" —
`4 All flavours of MOVICOL are priced at the prices listed above.'`
(report 22455–22520) — **is not a heading**: it is item 4 of a numbered
list of assumptions quoted from the Savings Calculator, inside matter 2's
COMPLAINT. It reaches `regard` because it is enumerated, sits immediately
before the RESPONSE boundary, and its terminal '.' is followed by a
closing curly quote so `has_terminal_punctuation` reads false. The real
headings are "1 Interactive map of savings", "2 Laxido Orange Savings
Calculator", "B Trustsaver Collection Leavepiece". So the sentence is
false because the underlying `regard` is wrong, not because the wording
is loose; softening it would make the sentence true and hide a real
`matter_headings` false positive, and would change the message for every
case that uses it. **NEW OPEN RESIDUAL**: `matter_headings` admits an
enumerated list item that precedes a structural boundary. Belongs with
the 1,120-item multi-matter scoping wave.

**Board and archive impact, quantified.** No published leaderboard board
changes value; no label, extract or split moves anywhere.

- `T1-triage-0bfe863a03b63ace` (Repair 1, renamed) carries archived
  answers in **two runs, neither a published board**: `20260802T101116Z`
  (claude-sonnet-5 P2 T1-triage dev, 250 calls / 100 scored — answered
  "breach" at p=0.6 against label `no_breach`, incorrect before and
  after) and `20260802T101119Z` (claude-opus-5 P2 — **dropped**, the 400
  credit-balance error). Both are members of the credit-exhausted quartet
  the leaderboard already lists under `excluded`; `leaderboard.json`
  contains `20260802T101116Z` zero times.
- Repair 2 puts **30 archived response rows across 12 run directories**
  (24 scored, 6 errored) on the documented **stale-prompt** footing:
  `complainant_category` is rendered into the prompt by run.py's
  `metadata_block()`, so those rows were produced against a CASE DETAILS
  block the current bank no longer generates. **One of them is on the
  PUBLISHED Phase A · 145 boards** — `T1-4fb071a1f5dc0c9f`
  (AUTH/2510/6/12) in both `phaseA-sonnet-P2` and `phaseA-haiku-P2`, plus
  its two source rungs and one memorisation probe. Labels, extract text
  and item_ids are unchanged, so the responses remain scoreable and
  comparable and **no board metric moves**. Disposal follows the standing
  precedent in `bench/runs/README.md`: documented, NOT re-run, because
  re-rolling one item inside a scored cell is a forking path and editing
  the archive is forbidden.

**Verification.** Full regime green: `l1/build` + `l1/derive` (both
byte-identical — L1 untouched), `l1/validate`, `l1/coverage`, `l2/build`
+ `l2/validate` (159 adjudications, 159 applied), `bench/generate` +
`bench/validate`, `verify/candidate_accounting`,
`verify/received_date_witnesses`, `verify/vocabulary_coverage --strict`,
`verify/code_year_witnesses`, `verify/ruling_battery` (all three readers
agree), `verify/pdf_clause_texts` under the pinned pypdf==6.1.1.
**Census unchanged at 10,205 (T1 4,372 / T1-triage 5,505 / T3 328)**;
exclusions 7,908 and `bench/exclusions.jsonl` **byte-identical to the
pre-round copy**; adjudications 159; migrations **410** (+2, wave
`2026-08-11-freeze-repair`, and the wave-collision guard was respected —
all six earlier waves carried forward intact). Field-diff fully
attributed with nothing left over: L2 **21 cases** (19 narrator-pass +
AUTH/2465's 2 rendition segments + AUTH/2505's clause row); items **33**
(30 `metadata_shown.complainant_category`, 2 `item_id` + `clause_ref`, 1
`inputs.renditions`); **0 label changes, 0 split changes, 0 items lost**.
Three consecutive full builds sha-compared identical on
`data/l2/cases.jsonl`, `bench/items.jsonl`, `bench/exclusions.jsonl`,
`data/l2/clause_slot_corrections.jsonl` (27 → 28 rows) and
`data/l2/audit_report.json`.

**Cosmetic residual, pre-existing, NOT fixed:** `bench/id_migrations.py`'s
closing summary prints "dropped: N item(s), all carrying an exclusion
row" from a raw key comparison that does not apply `slot_map`, so every
N2 clause rename is counted once as dropped and once as new. The mapping
itself is correct (the orphan check passes precisely because `slot_map`
redirected them); only the summary sentence over-claims. It has behaved
this way since the read-queues round's 20 renames.

### Everything-audit round 2 (2026-08-10; full reports
bench/review/audits/2026-08-10_everything_audit_round2A.md and _round2B.md)

27 label-breaking leads adversarially verified: **19 CONFIRMED, 5 refuted,
3 already-registered**. The register carries the rulings; the two audit
files carry every quote. New entries and updates:

**Mechanical subset FIXED (2026-08-10 second fix wave; full report
bench/review/audits/2026-08-10_round2_mechanical_fix.md): R28's N1-parity
half, the R28 precedent-guard false positive, R29 in full, R30 in full.**
Highlights, each receipted in the report: the adverb slot is generic MINUS
not/never (the corpus's "was not ruled" would invert under a naive fix); a
regression the plain fix caused was caught and repaired (frame resume at
list-end — recovered two clause rows no generation had ever read); the
three expected duals flipped PLUS three beyond-expectation attributions
handled by stop-and-report, yielding TWO NEW T3 ITEMS (AUTH/2519/6/12
overturned; AUTH/3700/10/22 upheld); AUTH/2246's wrong-half label stopped
shipping via a context-read registry (81-sentence closed set: 37
undertaking-anchored of which 36 read + 1 refused — the AUTH/2833 recital
proves sentence-local rules insufficient — 42 skipped, 2 MIXED declared,
unregistered-sentence build refusal); the panel-heading vocabulary is now
a decided 66-string table (11 declared negatives, coverage refusal) plus
a measured carrier-absorption rule (25 occurrences: 23 open rulings, 2
prepositional-object headings), giving ALL 14 R30 cases segments and —
side effect — **+78 new items** (63 T1: response segments that used to
swallow rulings now attest clean, so complaint+response serves for the
first time); R29's 16 adjudications (adj-0091…0106) landed with tier (e)
`edition_not_yet_in_operation` proven to fire and proven silent on
AUTH/2297 and AUTH/2135. Census 10,028 → **10,098** (T1 4,358 /
T1-triage 5,422 / T3 318); duals 887→894; id_migrations 328 (waves
logged); adj-0008's stated diagnosis corrected (the blocker was the
adverb, not anaphora — decision unchanged). Archived-run impact: three
renamed items are served (Phase A board ×2, probe_era ×1) with
byte-identical extracts — metadata-only drift; one removed dual item
appears in two 2026-08-02 pilot runs only. STILL OPEN in this round:
R28's representation decision, R31, R32, R18(b), D3's hand-review, N2's
41-item read list.

**R28 REPRESENTATION LANDED (2026-08-10, stage 1; full report
bench/review/audits/2026-08-10_dual_ruling_stage1.md).** Every verdict row
now carries `rulings` — per-regard receipts (body, polarity, verbatim
quote with pane offsets, matter-heading regard where the report states
one): 12,655 entries over 7,696 rows. Appeal-axis dual DETECTION built
(there was none — the old T3 exclusion reason "the appeal-side prose does
not state an Appeal Board ruling" was the opposite of true for that
class): 40 rows across 25 cases now flagged `dual_ruling_appeal_board`
(deliberately separate from `dual_ruling` — folding them would have
deleted ~40 sound T1 labels where only the Board ruled twice). All 82
read-list rows decided with quotes (27 panel: 3 genuine incl. NEW
AUTH/1899/10/06 15.4+15.9, unreadable twice over — anaphor + adverb;
55 appeal: 40 genuine, 15 classified recitals/conditionals/precedents);
genuine panel duals may only be set via adjudications (refusal proven);
AUTH/1941's unreadable half lives in a quote-pinned registry that refuses
if the text moves. Item effect: EXACTLY the AUTH/1941 T3 pair excluded —
census 10,096 (T3 316), published board 294 → **292**, both models lose 2
correct → **0.7329 each, still an exact tie**, per-class accuracies and
stated confidences essentially unmoved, so the collapse exhibit
strengthens. R18 residue (a) is CLOSED by this; 17 false
t3_no_appeal_board_ruling exclusion reasons corrected. STAGE 2 INPUT
recorded, not acted on: 8 rows where a genuine Appeal Board ruling is
recoverable behind a Panel recital (AUTH/2488/3/12 7.2 is `overturned` by
the report's own words) + 3 AUTH/3809 rows behind a swallowed segment —
each would CREATE T3 items and needs its own reading round; the "ruled
accordingly" reader frame likewise remains screen-only (R27/R18 residue).

- R28: **OPEN (original entry) — CONFIRMED dual rulings now span BOTH tasks and BOTH
  adjudication levels.** Seven more Panel-level duals confirmed
  (AUTH/2082 9.1 "and hence" tail; AUTH/2107 7.2 — a case with NO
  panel_ruling segment, see R30; AUTH/2273 7.9 parenthetical list;
  AUTH/2246 25 — **L2 published the WRONG half**, no_breach against the
  case's own outcome list, a precedent-guard false positive on a case
  number that is the undertaking's subject, not a citation; AUTH/2220
  15.4 and AUTH/2026 7.2 via the adverb hole; AUTH/1885 7.2 verbless
  "it thus followed"), plus AUTH/3476/2/21 14.1 found by N-sweep. AND a
  NEW AXIS: AUTH/1941/1/07's APPEAL BOARD ruled both ways on 7.3 and 7.4
  across matters — `T3-c80842dd8e7c863e` and `T3-feaef522747ddde8` are
  labelled `upheld` at half the truth and BOTH SIT IN THE PUBLISHED
  297-ITEM T3 BOARD for both models (same footprint as R18's pair; a
  24-row appeal-axis read list exists, 19 of them censure-idiom
  suspects). Four Panel-level prose frames are UNREACHABLE by the
  existing both-polarity sweep ("and hence" tails, parenthetical lists,
  verbless follows, missing segments) — the 24-row Panel read list is a
  floor. The dual-ruling REPRESENTATION decision (R18 option c) now
  covers ~20 confirmed members across T1/T1-triage/T3 and both
  adjudication bodies, and blocks: this entry, R18's residue, and the
  two published-board labels.
- R29: **OPEN — impossible code-year cluster CONFIRMED in full: 15 cases
  / 38 items tagged with an edition that commenced AFTER the case
  completed.** Seven item-bearing (AUTH/2035, 2039, 2043, 2044 — 10
  items ALL TEST, 2047, 2048, plus NEW member AUTH/2122/5/08) + 8
  zero-item members (3×2007→2008; 5×2011→2012 — the SAME class R19's
  wave repaired as adj-0037…0040 and left half-caught). Worst: AUTH/2048
  serves the 2008 "Relations with the Public and the Media" Clause 22
  text against a breach-of-undertaking ruling (2006's Clause 22;
  similarity 0.057); AUTH/2044's four test items serve rewritten 2008
  supplementary text. AUTH/1896/10/06 also CONFIRMED 2003→2006 on three
  witnesses incl. edition-exclusive vocabulary ("subsistence": 5×2006,
  0×2003). The mechanical detector — commencement(tagged) > completed,
  one-sided, keyed on COMPLETED (received-keying adds 7 legitimate
  straddlers incl. AUTH/2135, which STAYS refused) — has structurally
  zero false positives and belongs in verify/code_year_witnesses.py as a
  FAILING tier; commencement dates for all editions read from their own
  p2 sentences. Verifier recall note: the frame "The relevant Code at
  that time was the YYYY edition" is invisible to both readings
  (AUTH/1857, itself REFUTED — ruling attaches to the 2005 conduct).
- R30: **OPEN — 14 cases / 50 items have NO panel_ruling segment**
  because the heading vocabulary stops at `^PANEL RULING`: the corpus
  also writes PANEL MINUTE (3 files), PANEL DECISION (3), PANEL'S RULING
  (1), and 30 files have carrier-absorbed headings that contain but do
  not start with PANEL RULING. Labels rest solely on outcome lists;
  duals undetectable there (AUTH/2107 is the live example). Closed set,
  decide-every-value applies. No leakage (all 14 responses attested
  dirty; extracts complaint-only).
**R31 + R32 + R18(b) FIXED — the consolidated prompt boundary (2026-08-10,
Wave B; full report bench/review/audits/2026-08-10_wave_b_prompt_boundary.md).**
Sanction needles decided by measured base-rate floor (9-label closed set;
`advertisement` at 24.68% contamination dropped, the other eight ≤1.33%
kept; 146→4 sole-needle refusals — the 4 residual are sentence-level, a
recorded residual); generate now REFUSES `own_matter_unquotable` instead
of falling back (95 rows; re-probe shows **0 wrong-matter items**, was
32); per-segment appeal markers by witness ladder (339 UNDER APPEAL / 188
neutral; 21-sentence hand-read registry with build refusal; 68 T3 items'
headers corrected, 43 now carry no under-appeal marker — honest, flagged
for review); rendition coverage enforced (972 pairs dropped over 552
T1-triage items, each with a reasoned row); per-clause appellant from
per-ruling sentences + heading scope (the `both` cell is EMPTY: 8
corrected — AUTH/2528 splits 3 respondent / 3 complainant by its own
"This ruling was appealed by X" sentences — 4 excluded undecided, 2
already excluded upstream). Census 10,096 → **10,192** (+193 recovered by
the needle fix, −97, 40 renamed; T3 316). Boundary numbers: **69 same-id
prompt changes (all T3), 507 rendition-set-only changes**; archived T3
boards now score **276/297** with 63 of the survivors under a NEW
condition — the archived-run era is effectively closed for headline
claims; reruns are the path (standing policy). Post-wave count
refresh: parse_code_pdfs re-run (pinned engines) to update exclusion-row
n_items; pdf_clause_texts green under pypdf==6.1.1 (the environment had
drifted to 6.14.2 — run the verify scripts with the pins docs/WORKING_RULES.md
prescribes).

- R31: **OPEN (original entry) — items shown the WRONG MATTER's complaint.** On
  multi-matter cases, generate.py silently falls back when a matter's
  segments fail the attest, and AUTH/2008/6/07's matter-2 segments fail
  ONLY `no_sanctions_text` — whose needle for this case is the single
  generic word "Advertisement" (the chip label), which no "Advertisement
  Feature" matter can survive. 4 items ask about Clause 3.2 while shown
  text that never mentions it; measured class: 146 segments corpus-wide
  fail no_sanctions_text alone; **32 items across 7 cases** (all train)
  have a served complaint that never names their clause while an
  unserved one does. Repair: chip-needle discipline in the attest (the
  build's own docstring reasons this way for no_ruling_language and
  stops short), then rebuild.
- R32: **OPEN — two premise/presentation defects in T3 and renditions.**
  (i) generate.py stamps `[PANEL RULING UNDER APPEAL]` on EVERY
  panel_ruling segment: 70 of 314 T3 items carry multiple blocks, 35
  carry more blocks than the report has appeals (AUTH/2246: 7 blocks, 0
  headings); AUTH/1941's matter-2 block is provably not under appeal.
  (ii) Renditions are case-level while items are clause-level: on
  AUTH/2015/7/07, both renditions retell ONLY the SEP2/SEP3 allegation
  while the item asks about 7.3 (the preference claim) — and run.py
  REPLACES the extract with the rendition under P1/P3, silently changing
  the information set (268 at-risk items across 110 cases by clause-name
  proxy). Both are generator/design repairs that change prompts —
  sequence with the next prompt-condition boundary.
- R18 updates: (i) fix (b) is no longer optional — AUTH/1871/7/06
  upgrades the `appellant: both` cell from "observation" to PROVEN FALSE
  premise ("Both parties appealed that ruling" — only Sanofi appealed
  7.2/7.4, the complainant appealed 2/9.1); all 14 both-items are in
  eval splits (13 test, 1 dev), 2 in the published board. The APPEAL
  BY-clause headings carry the per-clause truth. (ii) N1-parity: the fix
  wave's adverb repair reached RULING_RE but NOT RULED_PASSIVE_RE (the
  verdict-evidence reader): "was thus/therefore ruled" reads as nothing
  — 32 sentences/29 cases, 3 contradicting published values. Proven by
  execution; two-character-class parity fix + fixture row.
- D3 update: AUTH/1977/3/07 8.1 re-found by the audit;
  `prose_contradicts_unappealed_list` now 10 rows / 6 cases — D3's
  promised hand-review of the class remains OPEN.
- N2 (round-2A): **22 (case, clause) pairs / 41 items whose clause is
  never named anywhere in their own case's text** — including
  AUTH/1978/3/07 where the PMCPA slot carries a 7.10↔10.1 TRANSPOSITION
  (the ruled clause has no item; the itemed clause is never mentioned)
  and AUTH/1921/11/06 where Constitution PARAGRAPH 17 was tokenised into
  Code CLAUSE 17 and the samples clause text is served on a website
  case (l2.procedure.paragraph_17 is false for that case, a second
  missed witness). Repair: build-time refusal when a slot-derived clause
  has no witness in the case's own text; the 41 are a read list.

## Round 4 (2026-08-05) — RUN, FAILED on R12, fixed, re-verified

Two adversarial Opus auditors on the six 2026-08-05 fixes: A read the fixed
VALUES against the primary case-report text; B hunted UNTRACED CONSEQUENCES,
the class that produced R15.

**R3, R8, R10, R11 CONFIRMED.** R11 gained corroboration the fixer never used:
`cludo:status` reads `'…(abridged)'` on exactly those 6 files corpus-wide.
R3 checked at 24/24, not the 8 sampled. R10's two surviving `industry role`
notes were both verified genuine, so the guard does not over-reject.

**R12 FAILED, and the failure was in the rule, not the values.** Its 13
corrections are all right, but the tie-break assumed the case number encodes
RECEIPT. It sometimes encodes processing — AUTH/3543/7/21 states it outright
("Whilst the case was received in August 2020, in error it was not processed
until July 2021") — and the case number and the meta/info slots are NOT
independent witnesses: both come from the PMCPA's record-keeping and carry a
late-logging error together. Two wrong values were published
(AUTH/3543/7/21, CASE/0251/07/25), each contradicted by explicit report text.

FIXED three ways:
1. The slots+case-number-against-the-trailer branch no longer resolves. It
   records `date_slots_trailer_disagrees_unresolved` and states the conflict,
   because those two witnesses share a cause. The reverse branch (trailer +
   case number against the slots, two DIFFERENT provenances) still decides,
   and that is the 13.
2. `check_date_coherence()` refuses the build when received > completed. A
   structural impossibility needs no witness vote, and it caught MORE than the
   heuristic did: AUTH/2484/2/12 (a two-month gap the same-year rule had
   waved through) and AUTH/2858/7/16 (basis `unanimous` — every witness agreed
   on an impossible pair).
3. `dates.received` and `dates.completed` are now adjudicable fields;
   adj-0003..0007 resolve the five, each pinned to its source sha.
   `reviewed_by` records who read the evidence (round-4 auditor A, independently
   of the fixer, plus the build-time coherence check). Four are evidence-reading
   — the report states the receipt date in words — and the fifth (adj-0007) sets
   a value to null because no evidence for it exists, which is the default
   "absence is a value" already mandates. No further sign-off is pending: the
   field is provenance, not a gate (adj-0002's reviewer is a document).

**R13 passes as bookkeeping** — all 10 sampled reasons faithfully describe L2 —
but 3 of 10 sampled candidates SHOULD have become items: the Panel does rule on
the clause, in a frame L2's prose reader misses. See R17.

Consequences B found and this round fixed: the abridged/outwith anchor claim
lived in four places, not the one R15 named (DESIGN.md ×3, APPROACH.md,
site methods page); DESIGN.md published `116` for outwith-scope, which is the
false-friend KEYWORD count D1 discredited, not a case count (now 97); two
standing guards printed their own worst finding and exited 0, both now proved
to fail on fixtures; `bench/validate.py`'s hard-reason cross-check did not
cover R13's 255 new rows; the site exporter capped an empty diagnostic class
silently. Counts corrected: 35 items' tags changed, not 5.

## Re-audit record (2026-08-02) — the protocol below was RUN and PASSED

Round 2 (post-D1–D4 rebuild, fresh seeds): T3-focused re-audit read 33 items
(15 overturned + 15 upheld sampled at seed 101, + acceptance cases) —
label accuracy 311/313 corpus-swept (99.4%), 4 named items removed at root
cause; T1-focused re-audit read 40 items (seed 202) — 45/45 label
judgements correct across the restored 2023–25 era, the flip class, random
regression and sibling folds, with corpus-wide mechanical separation checks
(0 defence-text bleeds in 9,725 extracts). Conditional passes; conditions
(metadata truthfulness; 7 banner-leak items) discharged by the F-round.

Round 3 (metadata gate, seed 303, 25 items adversarial): FAILED 9/25 —
led to the tri-state anonymity / final-state contactability / widened-role
fixes; all 9 named failures individually re-verified fixed by a
builder-independent oracle. CLEARED TO SPEND 2026-08-02 after this round;
first live runs followed the same day. (This paragraph is the clearance
entry the header's "not cleared" line anticipated; audit round counts used
elsewhere: round 1 = 105 items (60+45), round 2 = 73 (33+40), round 3 = 25.)

## Re-audit protocol after fixes
Fresh stratified sample (new seed), same two-auditor design, PLUS: every
class this register names gets sampled explicitly. No spending before the
re-audit passes.

### Amendment 2026-08-05 — scope of round 4, and why
Round 4 covers the six classes fixed on 2026-08-05 (R3, R8, R10, R11, R12,
R13) rather than re-sampling every class in the register. The reason, recorded
so the exemption is auditable rather than assumed: those six changed **no
label, split, extract text, provenance or item_id** — verified by diffing the
current bank against the pre-fix item copies in `bench/subsets/` (0 differences
on all of those fields), so the label spine round 3 cleared is untouched. What
DID change is 74 items' prose-visible metadata and 5 items' tags.

This is a REDUCTION from the protocol above and is not silent: the protocol
still stands for any change that touches a label. The register's own history is
the argument against going lighter than this — F1 circularity, and R11/R12
which survived every internal check — so round 4 keeps the two-auditor design
on the classes it does cover.

## Runner docstring lineage (2026-08-15)

`bench/run.py` received a documentation-only edit after the freeze-day runs
were planned: its module docstring's protocol summary was corrected from the
superseded numbering (P3 described as offline selective prediction) to the
active one (P3 pooled stated confidence via `p3_plan.py`; P4 offline
selective prediction). No request-building code changed. Because active
manifests pin `config.runner_sha256`, the site exporter now carries a
reviewed two-hash runner lineage: the pre-edit hash
`c2d603af374afba7dad5e226259d63061a7362732774201638617804799f90ba` (all runs
planned on or before 2026-08-15 afternoon: luna/terra/sonnet/haiku) and the
current post-edit hash (claude-opus-5 and gpt-5.6-sol runs). Any other
runner drift still fails closed. Standing rule going forward: treat
`bench/run.py` as byte-frozen while runs are active; extend the lineage set
only with a reviewed request-identical diff and a dated entry here.
