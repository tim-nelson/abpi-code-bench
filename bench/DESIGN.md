# ABPI Code Bench — active benchmark design

The active benchmark contains three adjudicated, clause-level tasks. Generated
data are rebuild products rather than repository source. Historical pre-repair
runs are archived and are not active benchmark results.

Every label is a PMCPA decision. An LLM may help curate future datasets, but no
LLM supplies ground truth for T1, T2 or T3.

## 1. Design principles

1. **A task must be answerable from relevant evidence.** A human reader must be
   able to make a reasoned forecast from the information shown. Deterministic
   certainty is not required; incomplete evidence creates a genuine prediction
   problem, not permission to ask an unrelated hidden-fact question.
2. **Items and elicitation are separate.** The same ordered item prefix is
   served under P1, P2 and P3, so comparisons can be paired exactly.
3. **Provenance reaches the source byte.** Every quoted span carries its source
   file, pane, offsets, length and hash through HTML/PDF → L1 → L2 → item.
4. **Leakage is refused, not trimmed away.** Only task-appropriate L2 segments
   pass into an item; the generator and an independent validator both check the
   shipped prompt surfaces.
5. **Cases, not clause rows, are the independence unit.** Ordering is
   case-aware and uncertainty intervals resample cases/source-report sibling
   groups.
6. **No silent failure.** Attempted calls, parsed calls, refusals and errors are
   reported. A P2 or P3 item is complete only when all requested repeats parse.

## 2. Active tasks

### T1 — full-information breach verdict

- **Input:** complaint, respondent-company response, the applicable Code clause
  text, and the leakage-safe metadata allowlist.
- **Question:** did the Panel rule a breach of this clause?
- **Label:** the Panel's per-clause `breach` / `no_breach` ruling.
- **Current supply:** 4,599 items.
- **Human reasoning:** weigh the allegation, response and cited evidence under
  the clause. Missing exhibits can limit confidence, but the evidence shown is
  directly connected to the adjudicated question.

### T2 — complaint-only breach forecast

- **Input:** the complaint, applicable Code clause text, and the same safe
  metadata; the response is withheld.
- **Question and label:** the same Panel breach outcome as T1.
- **Current supply:** 5,553 items.
- **Interpretation:** complaint-stage triage/pre-vetting, not a complete-record
  legal determination. A calibrated reader should reflect the irreducible
  uncertainty created by withholding the defence.
- **Pairing:** where both tasks exist for the same case/clause, T1 and T2 are an
  exact response-information pair. Their common-pair subsequences share an
  order; analysis uses the exact intersection selected by the two independent
  task prefixes.

`T2` replaces the old internal id `T1-triage`. Historical files keep their old
ids; active generated items use `T2`.

### T3 — appeal survival

- **Input:** complaint, response, the Panel ruling/reasoning under appeal,
  applicable clause text, and the per-clause appellant identity.
- **Question:** did the Panel ruling survive appeal?
- **Label:** `upheld` / `overturned` from the Appeal Board outcome.
- **Current supply:** 345 items. Twenty-one rows document byte-identical
  co-reported sibling cases folded into one item; each is scored once and its
  source-report sibling group is one uncertainty block.
- **Human reasoning:** assess whether the Panel's reasoning is robust to appeal.
  Appeal submissions and later evidence can leave genuine uncertainty; that is
  part of the calibration problem rather than an unrelated hidden label.

T3 may quote `panel_ruling`: that ruling is the premise. It must never quote an
Appeal Board outcome. Appellant attribution and the ruling header are decided
per clause; ambiguous rows are excluded with receipts.

## 3. Elicitation and deployment protocols

### P1 — stated confidence

One canonical request per item returns a verdict and probability that the
verdict matches the adjudicator.

### P2 — verdict-repeat agreement

Send the byte-identical canonical verdict-only prompt K times under one fixed
model configuration. Confidence is the frequency of the modal verdict.

- Initial K is 7.
- Repeats are numbered, so P2@3 and P2@7 use the first 3 or 7 calls.
- A later top-up to K=8/9/10 sends only the missing repeat indices.
- The initial condition may omit the temperature parameter. When supported,
  `--temperature X` fixes one value for the run. A different temperature is a
  different run, never mixed into one confidence estimate.
- Publisher renditions and block-order changes are not part of core P2. They
  may be studied separately later.

P2 is a K-call ensemble plus a stability signal; it is not compute-matched to
one-shot P1. Results must state that distinction.

### P3 — repeated stated-confidence linear pool

Send P1's byte-identical answer-and-probability request K times at one fixed
model configuration. Each draw is converted to a probability of the task's
fixed positive label (`breach` for T1/T2; `overturned` for T3). The primary P3
estimate is their equal-weight linear probability pool. The pooled answer is
the side of 0.5 selected by that probability, and confidence is the probability
assigned to the selected answer.

- Initial K is 7; the scored system binds its exact evaluated K.
- Repeat indices are stable, so odd prefixes K=1/3/5/7 and later odd top-ups
  reuse existing calls. Odd K also keeps the secondary vote view from acquiring
  an even-vote tie.
- Single-draw performance, within-item dispersion and an answer-vote view are
  secondary diagnostics. They do not replace the predeclared linear-pool
  result.
- P3 costs K calls and is reported on its own boards. It is not mixed with P1
  one-shot confidence or P2 verdict-repeat agreement.

### SP — offline selective prediction

SP (formerly presented as P4; the P4 label is reserved for a planned
incentivized-deferral protocol) makes no model calls. For each completed P1/P2/P3 confidence signal and
threshold, accept answers at or above the threshold and defer the rest. Report
coverage, selective risk, AURC and task/label-specific error transitions.
Equal-confidence items enter together. Breach-specific missed-breach/false-flag
fields are reported only for T1/T2 row sets, so any later declared asymmetric
cost can be applied without rerunning a model.

The historical P3 lottery experiment is archived exploratory work and is not
part of the active run plan. Its P3 identifier is explicitly legacy: archived
files are not renamed, and the scorer uses their absent/v1 run contract to
distinguish it from the current protocol namespace.

## 4. Canonical cumulative prefixes

Each task has one deterministic, case-aware order derived from a published
rule/seed and the exact item-bank hash. Physical JSONL order is never the
evaluation order.

- `--through-items N` means ranks 1…N **within each selected task**.
- T1/T2 matched items share a relative pair ordering, while task-only items
  remain interleaved in their own task; paired analysis uses the exact selected
  intersection rather than assuming equal absolute ranks.
- Checkpoints such as 1, 10, 20, 50, 100, 200 and 300 are nested views of one
  run, not separate experiments.
- Cross-model comparisons use the exact common intended prefix, including
  attempted items; they never intersect only the successfully parsed rows.
- Adding or repairing cases changes the bank hash and may change ranks. The
  runner refuses to extend an existing run across that boundary; any future
  refresh starts a new explicitly recorded bank/order condition.

Engineering checks at N=1 and N=10 do not determine when to stop based on
accuracy. Any accuracy-driven stopping is reported as exploratory.

## 5. Leakage and metadata boundary

T1/T2 quote only complaint/response segments whose L2 leakage attest is clean.
T3 additionally quotes a Panel ruling block screened for any Appeal Board
outcome. The model never sees the case number, completion date, outcome tables,
sanction fields, procedure flags, subject/hero line, appeal result or analysis
tags.

The safe metadata allowlist is respondent, complainant category/anonymity/
contactability, Code year and complaint-received date. T3 alone also receives
the Panel ruling for the clause and appellant identity because those define its
premise.

## 6. Contamination and analysis

- Run an exact-input/task-specific recall probe for each evaluated model.
- Use completion/publication date, not complaint-received year, when defining
  any post-cutoff or recency stratum.
- Report task-level accuracy, Brier and AUROC as primary descriptive metrics;
  ECE is secondary because small samples make it bin-sensitive.
- Report attempted/scored counts, refusals and errors.
- Use case/source-report-blocked uncertainty and paired differences for
  cross-model and T1/T2 comparisons.
- Report both item-weighted results and case-weighted sensitivity where cases
  contribute different numbers of clauses.

## 7. Deferred task construction

There is deliberately no T4 merely to complete a sequence.

- Material-only prediction is a possible future case-by-case curation project.
  An LLM may propose exact material spans and clause mappings, but source-pinned
  human/referee verification must decide admission and labels.
- Additional-sanction forecasting is not an active task. `Advertisement` is
  nearly a procedural proxy for Clause 2; genuinely discretionary sanctions
  are sparse and often depend on later representations, history or audits not
  present in a complaint/response input.
- Clause identification, Code-year counterfactuals and other task families may
  be added later under descriptive designs without changing T1–T3.

## 8. Known validity limits

1. Many reports describe rather than reproduce the original promotional
   material or exhibits.
2. T2 intentionally omits the defence; T3 can omit later appeal evidence.
   These are deliberately incomplete information conditions. Their empirical
   effect on answers and confidence is measured without prescribing a direction.
3. The target is agreement with a particular adjudicator under its burden of
   proof, not a claim of universal or platonic compliance.
4. Public cases may have appeared in training data; model-specific probes and
   temporal strata qualify interpretation.
