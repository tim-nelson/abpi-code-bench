# ABPI Code Bench — benchmark design

> Status: BUILT (bank + runners live; results in `docs/FINDINGS.md`). This
> document remains the task-design rationale. Post-build deltas: **T4 was
> withdrawn** (DEFECTS D1 — its positive class was a keyword false friend and
> the true class has no quotable text); P3 is implemented; T5 exists as a
> 27-item probe (`T5_DESIGN.md`). Public name **ABPI Code Bench**
> (abpicodebench.com); dataset/repo ID stays `pmcpa-bench`.

The benchmark serves a dissertation on **behaviourist confidence elicitation**
(Ivanova brief, PDF at repo root): deriving uncertainty from observable
behaviour (answer frequencies under perturbation, lottery choices) rather than
self-reported probabilities, evaluated with Brier/ECE, cross-paradigm
divergence analysis, and selective-prediction curves. It also has a direct
industry reading: pre-vetting promotional material against the ABPI Code is a
real job (the final signatory's), with real asymmetric costs.

Every label below is something the PMCPA actually adjudicated. **No LLM-judge
anywhere in the ground truth** (the brief's C3: verifiable rewards).

## 0. Measured ground-truth inventory (2026-08-02, corpus = 1,902 cases)

| asset | count | notes |
| --- | ---: | --- |
| breach-only cases | 554 | at least one clause upheld, none rejected |
| no-breach-only cases | 643 | includes burden-of-proof rulings |
| mixed cases | 650 | breach AND no-breach clauses in one case |
| appeals (respondent) | 206 | L2-normalised (was ~220 estimated) |
| appeals (complainant) | 83 | L2-normalised (was ~49 estimated); +6 'both', 67 unresolved |
| outwith-scope rulings | 97 cases, **0 items** | the abstention class. NOT available: all 97 have zero verdicts and zero quotable segments. The 116 previously stated here was the false-friend KEYWORD count that DEFECTS D1 discredited, not a case count |
| no outcome recorded | 55 | stubs; kept, never items for T1 |
| abridged procedure | 6 cases, **0 items** | company accepted breach. NOT an available anchor: see DEFECTS R15 — the six genuine cases are summary-only and yield no quotable ruling prose, so the class is structurally absent from the bank |
| voluntary admissions | ~124 | self-reported breaches |
| cases with a Code year | 1,848 | 12 distinct years 2003–2024; 54 to resolve in L2 |
| distinct respondents | 342 | entity inventory for redaction |
| PDF-substituted cases | 13 | `data/l1/pdf_records.jsonl` |

Case-level base rate for "any breach" ≈ (554+650)/1847 ≈ 65%; clause-level
rate to be computed at L2 (mixed cases contribute both polarities, so
clause-level items can be balanced by construction).

## 1. Design principles

1. **Items and elicitation protocols are orthogonal.** One item bank; every
   item servable under every protocol in §3. Calibration comparisons across
   protocols are then within-item.
2. **Provenance to the character.** Every item carries pointers into L1
   (`file`, pane, char offsets) via L2. The audit chain
   HTML/PDF → L1 → L2 → item never breaks.
3. **Leakage is a data property, not a script property.** Items only ever
   quote L2 segments whose `leakage_attest` is clean (§5). No generator
   re-implements safety.
4. **Contamination-first.** These rulings are public and old; assume
   memorisation until an item proves otherwise (§6).
5. **Difficulty is measured, not asserted.** Appeal flips anchor known points
   on the confidence scale; everything else is calibrated relative to them.
   Abridged admissions and outwith-scope stubs were designed as two further
   anchors and are NOT available: both classes exist in L2 (6 and 97 cases) and
   both yield zero items, because the PMCPA publishes them as summary-only
   stubs with no quotable ruling prose. See DEFECTS R15. Appeal flips are the
   only anchor the bank actually carries.

## 2. Task families

Also usable as split names: `T1-verdict`, `T2-clauses`, … Estimated item
counts are pre-filtering (leakage attest + contamination quarantine shrink
them).

### T1 — Breach verdict (the workhorse)
- **Input:** complaint segment (+ optionally response segment), the clause
  text from the applicable Code year, case metadata minus outcomes.
- **Question:** did the Panel rule a breach of this clause?
- **Label:** per-(clause, code_year) Panel ruling. Binary.
- **Scale:** thousands of clause-level items across ~1,800 cases.
- **Variants:** complaint-only vs complaint+response (measures how much the
  defence moves confidence — industry-relevant: pre-vetting sees no defence).

### T2 — Clause triage (multi-label)
- **Input:** allegation only. **Question:** which clauses are engaged?
- **Label:** set of clauses actually ruled on (either polarity).
- The industry pre-vetting task; scored as multi-label with per-clause
  confidence.

### T3 — Appeal survival (the calibration gold)
- **Input:** Panel ruling + reasoning segment + appellant identity.
- **Appellant is per-clause, not per-case** (2026-08-10, DEFECTS R18(b)): the
  premise names who appealed, and `case.appeal.by` is one value for a report
  that can split its appeals by clause (AUTH/1871/7/06: Sanofi appealed
  3.2/7.2/7.4, the complainant 2/9.1). Where a single party appealed the
  case-level value stands; where both did, the report's own words decide —
  the "was appealed by <party>" sentence after the ruling, else the APPEAL
  BY/FROM section's scope sentence — and where neither speaks, the item is
  excluded (`t3_appellant_undecided_for_clause`). All 14 `both` items:
  10 decided, 4 excluded.
- **The quoted ruling block's header is per-segment** (2026-08-10, R32(i)).
  `[PANEL RULING UNDER APPEAL]` used to be stamped on every panel_ruling span;
  a block gets it now only where the report says that ruling was appealed, or
  it is the item's only ruling block, or an APPEAL BY scope sentence covers a
  clause it rules on. Otherwise `[PANEL RULING]`. The premise still rides the
  question line; the extract stops asserting it.
- **Question:** does this ruling survive appeal?
- **Label:** Appeal Board outcome. ~269 appealed cases, clause-level flips.
- **Why gold:** on flipped rulings the best-informed humans disagreed, so a
  model at 0.99 confidence is miscalibrated *whichever way it answers*. This
  is the reference class for the over-confidence analyses (pillar B2).

### T4 — Scope / abstention — **WITHDRAWN (see DEFECTS D1); retained as design rationale**
- **Input:** complaint. **Question:** is this within the Code's remit at all?
- **Label:** outwith-scope rulings vs in-scope cases. (Class size is 97 cases as of R3; the 116 previously stated was D1's discredited keyword count. The task stays WITHDRAWN — the widening made the class 33% larger and no more buildable, since all 97 are stubs.)
- Feeds selective prediction (B3): the correct behaviour on some inputs is
  refusal, with a measured class.

### T5 — Counterfactual pairs (two sub-benchmarks, labelled differently)
- **T5a natural:** adjudicated near-miss pairs from the 650 mixed cases —
  similar conduct, opposite per-clause outcomes. Fully ground-truthed.
- **T5b synthesized:** breaching claim vs minimal compliant rewrite (the
  Panel's reasoning states exactly why the claim failed, so the fix is
  well-defined). Label is *compliant by construction*, expert-spot-checked —
  reported separately, never blended with adjudicated labels.
- Measures false-positive behaviour and confidence symmetry.

### T6 — Code-year sensitivity
- **Input:** same conduct, clause text from two Code years (e.g. 2016 vs
  2021). **Question:** breach under *this* text?
- **Label:** the year-specific ruling where it exists; year-pairs where
  renumbering/rewording changed the answer are the discriminating items.
- Tests whether the model reads the regulation or pattern-matches folklore.
  Requires the Code-texts pipeline (§7).

### T7 — Severity
- **Input:** established breach + case facts. **Question:** sanctions beyond
  undertaking? Clause 2 censure?
- **Label:** the rescued `Additional sanctions` chips (63 multi-sanction
  cases) + Clause 2 rulings. Ordinal; the high-stakes tail.

### Designed failure-mode class (cuts across T1/T4)
**Burden-of-proof traps:** anonymous, non-contactable complainant; allegation
plausible on its merits; ruled **no breach** because nothing was proven
(e.g. AUTH/2461/12/11). A model reasoning on merits alone is confidently
wrong in a systematic direction — the pre-registered prediction for B2 is
that verbalized confidence stays high while inner-crowd consistency drops.
Complainant-type metadata (L2 `complainant.category`) selects the class:
187+ "Anonymous" plus contactability from case text.

## 3. Elicitation protocols (the dissertation axis)

| id | protocol | pillar | notes |
| --- | --- | --- | --- |
| P1 | verdict only, K perturbations → answer-frequency confidence | A1 | perturbation sets in §4 |
| P2 | verdict + stated probability | baseline | the mentalist foil for B2 |
| P3 | BDM lottery: answer, then lottery-vs-sure-payoff c, sweep c to indifference | A2 | binary T1/T3/T4 are the ideal substrate |
| P4 | answer-or-defer at threshold τ → accuracy-coverage, selective risk | B3 | cost framing from industry: missed breach ≫ false flag |

## 4. Perturbation sets (for P1)

1. **Publisher-written paraphrases — the corpus's unique asset.** Each case
   has up to three independently written renditions: summary pane, report
   abstract (located by `abstract_boundary`), and PDF flow (13 cases). No
   LLM-synthesized paraphrase validity worries.
2. Mechanical: entity redaction variants (§6), clause-presentation order,
   in-context example shuffles, plain resampling.
   **Temperature is NOT an available axis on current Claude models**
   (measured 2026-08-02: sonnet-5 / opus-5 / fable-5 / opus-4-7/4-8 reject
   the parameter; only sonnet-4-6 / haiku-4-5 accept it). Resampling at the
   default is still stochastic, but it is not a controlled perturbation.
3. Translation: quarantined exploratory track only (decided 2026-08-02) —
   never mixed into the core inner-crowd score; legal-terms-of-art
   translation loss confounds the uncertainty signal.

Caveat on axis 1 (measured by the L2 build): the publisher renditions state
the outcome in their tails, so only their leading allegation spans are
quotable (`abstract_rendition` refused on 112 cases where the boundary is
not oracle-measured — refusals, not silent inclusions).

Second caveat on axis 1, added 2026-08-10 (DEFECTS R32(ii); this guarded the
tails for leakage and said nothing about COVERAGE). Renditions are
case-level and items are clause-level, and `run.py` REPLACES the extract with
the rendition under P1/P3 — so on a multi-matter report a retelling of matter
1 can stand in for an item about a clause ruled in matter 3, which changes the
information set rather than paraphrasing it (AUTH/2015/7/07, proven). A
rendition is now kept for an item only where the case has ONE segment of the
quoted kind, or the retelling names the clause, or it carries the report's own
heading for every matter the clause was ruled in. Measured: 7,541 kept as
single-matter, 146 on the matter heading, 8 on the clause number; **972
(item, rendition) pairs dropped over 552 items and 162 cases**, each with a
`rendition_not_covering` row. A dropped rendition costs perturbation levels,
exactly as the zero-rendition items already do.

## 5. Leakage rules

An item's quoted text MUST come from L2 segments with `leakage_attest:
clean`, which certifies: no ruling language (RULING_RE class patterns), no
outcome banner or outcome-table content, abstract span excluded, no
`Additional sanctions` text. Metadata shown to the model is exactly the
SHOWABLE allowlist in `l2/SPEC.md` §6b — imported, never re-derived. The
attest is recomputed by the L2 validator, not trusted from the builder.

The sanctions needle carries a DISTINCTIVENESS FLOOR since 2026-08-10
(DEFECTS R31): one of the nine chip labels the corpus uses is the single
generic word 'Advertisement', which 407 of the 1,649 pages that do NOT carry
the chip use in ordinary prose (24.7%, against ≤1.33% for the other eight), so
it refused 142 of the 146 segments that failed this check and nothing else —
including both segments of AUTH/2008/6/07's matter "2 Quick Guide
'Advertisement Feature'". Every chip label is decided in
`SANCTION_NEEDLE_USED` and an undeclared one refuses the build. Where a
matter's segments are refused, `bench/generate.py` no longer falls back to
another matter's: the item is excluded with an `own_matter_unquotable` row.

Quotable segment kinds are task-specific (decided 2026-08-02, after the
harness surfaced the T3 tension):

| task | may quote | task-specific metadata exceptions |
| --- | --- | --- |
| T1, T2 | complaint, response | none |
| T1-triage | complaint | none |
| T3 | complaint, response, **panel_ruling** (the ruling under appeal IS the input) | `panel_ruling_for_clause`, `appellant` (PER CLAUSE since 2026-08-10, R18(b)); never appeal_* segments |
| T4 | complaint | none |
| T5/T6/T7 | per their definitions, same attest discipline | declared when built |

Rendition segments (`summary_rendition`, `abstract_rendition`) are quotable
wherever their base kind would be — they are paraphrases of the allegation
portion only (`l2/SPEC.md` §2: the outcome-stating tail is cut before
attest).

## 6. Splits and contamination policy

- **Sibling rule (decided 2026-08-02):** cases sharing a source report
  (`sibling_cases` in L2) are always assigned to the same split — shared
  narrative text would otherwise leak across train/test.
- **Era splits** by Code year (2003–2024) — also the natural drift study.
- **Rolling post-cutoff holdout:** PMCPA publishes continuously; the scraper
  is resumable. Quarterly refresh; items from cases completed after a target
  model's training cutoff form the uncontaminated test set. Cutoff metadata
  recorded per evaluated model.
- **Memorisation probe per item:** from redacted parties/facts alone, can the
  model produce the case number or outcome? If yes → item quarantined (kept,
  labelled `contaminated`, reported separately).
- **Redaction variants:** company and product names swapped from the L2
  entity inventory; report both redacted and verbatim tracks.

## 7. Dependencies

| dependency | status |
| --- | --- |
| L2 canonical cases (`l2/SPEC.md`) | spec drafted, build pending |
| ABPI Code texts per year | **fetch in progress** (`scrape/fetch_code.py`). Discovery found SIX interactive editions — 2014, 2015, 2016, 2019, 2021, 2024 — not three; supplementary information captured (Clause 2's 'particular censure' wording lives there). 2003–2012 availability on the site is uncertain; if absent, pre-2014 eras run without clause-text display (T1) and T6 covers 2014–2024 |
| expert spot-check for T5b | recruit a signatory-experienced reviewer |

## 8. Known validity limits (stated up front)

1. We rarely have the promotional material itself — only the case's quoted
   claims and descriptions. Items are "allegation + context", not raw ad
   copy. (True of every party downstream of the PMCPA, including the Appeal
   Board reading the papers.)
2. T5b labels are constructed, not adjudicated. Reported separately, always.
3. Panel rulings are decisions of a specific body with its own standards
   (balance of probabilities; complainant bears proof). The benchmark
   measures agreement with *the adjudicator*, not with platonic compliance —
   for calibration research that is a feature: the target is a real,
   consistent decision process.
4. Old cases may be paraphrased in models' training data even where outcomes
   aren't memorised verbatim; the post-cutoff track is the clean answer, the
   probe+redaction track the volume answer.

## 9. Decisions and open questions

Decided 2026-08-02 (recommended and confirmed / pending final sign-off where
marked):

1. **Multi-case reports: one item per case** with `sibling_cases`
   cross-referenced; siblings share a split (§6). DECIDED.
2. **T1 headline = complaint+response** (matches the Panel's information set
   — the label is a function of both sides); **`T1-triage`
   (complaint-only)** is a first-class variant measuring calibration under
   irreducible uncertainty and the industry pre-vetting framing. The paired
   confidence delta (does confidence move appropriately when the defence is
   revealed?) is a pre-registered B2 analysis. RECOMMENDED — confirm.
3. **Headline metric = clause-level Brier + ECE with case-blocked
   (clustered-bootstrap) confidence intervals** — clauses within a case share
   narrative and are correlated. Case-level any-breach accuracy (base rate
   ≈65%) reported as the secondary, human-readable number; clause-set
   micro-F1 belongs to T2. RECOMMENDED — confirm.
4. **T7 severity: in v1**, small and reported separately. RECOMMENDED —
   confirm.
5. **Translation perturbations: quarantined exploratory track only**, never
   mixed into the core inner-crowd score (confound: uncertainty vs
   translation loss on legal terms of art). RECOMMENDED — confirm.
