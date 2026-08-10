# How the benchmark measures uncertainty — and how we keep it honest

> Companion to `DESIGN.md` (what the items are). This is the measurement
> story: what a "confidence" is here, how each elicitation method produces
> one, and what "accurate confidence" means against adjudicated ground truth.

## 1. The object being measured

For every item the model produces an **answer** (breach / no_breach, upheld /
overturned, in / outwith scope) and, by one of several elicitation methods, a
**confidence** c ∈ [0,1]. The ground truth is what the adjudicator actually
decided. A confidence method is GOOD to the extent that c behaves like
P(answer matches the adjudicator):

- **Calibration** — of all answers given with c≈0.7, ~70% should be right.
  Measured by reliability diagrams and ECE (equal-mass bins).
- **Proper score** — Brier = mean (c·correct-indicator gap)²; rewards being
  right AND knowing how right you are; strictly proper, so honesty is the
  optimal policy.
- **Discrimination** — does c rank correct answers above wrong ones (AUROC),
  and does refusing the least-confident x% cut error fastest
  (accuracy–coverage / selective risk, the deferral analysis)?

One item bank, many elicitation methods: comparisons are within-item, so
differences are attributable to the METHOD, not to item mix.

## 2. The elicitation methods (implemented status)

| id | method | paradigm | status |
| --- | --- | --- | --- |
| P2 | stated probability alongside the answer | mentalist self-report | live |
| P1 | answer-only over K perturbed presentations; confidence = modal-answer frequency | behaviourist | live (perturbations: publisher renditions, block order, resampling; temperature only on models that accept it — gpt-4.1/4o, sonnet-4-6, haiku-4-5) |
| P3 | BDM lottery: choose between betting on your answer and a sure payoff c, sweep c to indifference | behaviourist, revealed preference | live; piloted n=30 (FINDINGS §4.3: implied confidence inversely ranked correctness at pilot scale) |
| P4 | answer-or-defer at threshold τ | decision-level | free at scoring time: any confidence signal sweeps τ offline; no new calls |

The dissertation's cross-paradigm question (B2) is exactly the P2-vs-P1/P3
divergence, per item, per model. First partial data (2026-08-02, sonnet):
P2 compresses into a 0.55–0.75 band (over-confident at the bottom,
under-confident at the top); P1 with weak perturbation diversity polarises
toward 1.0 and is over-confident everywhere. Opposite failure modes on the
same model — measured, not assumed.

## 3. What "accurate given the true data" means here

Average calibration is not the whole story: the corpus gives us item classes
whose APPROPRIATE confidence is known in advance, so miscalibration can be
localised rather than averaged away:

- **Appeal flips** (Panel overturned on appeal): the best-informed humans
  disagreed. A method that outputs c≈1 on these is over-confident whichever
  answer it gives.
- **Burden-of-proof traps** (anonymous, non-contactable complainant; ruled on
  proof not merits): a model reasoning on merits alone should be — and can be
  measured being — systematically wrong with high confidence.
- **Abridged/admitted cases** (company accepted the breach): designed as
  near-certain items, but the bank carries NONE — all six genuine abridged
  cases are summary-only stubs with no quotable ruling prose (DEFECTS R15).
  A design intention, not a live class.
- **T1 vs T1-triage** (same case, defence hidden): confidence SHOULD fall
  when the defence is hidden; whether it does is a per-method measurement.
- Measured null so far: showing the actual clause text moved neither
  accuracy nor confidence (paired n=83, sonnet) — Code knowledge is not the
  binding constraint at this scale; case judgment is.

## 4. Honesty safeguards (all mechanical, all in the repo)

1. **Leakage**: extracts only from segments whose leakage attest is
   machine-verified (L2 validator recomputes it independently of the
   builder); a final tripwire drops outcome-citing items at generation; the
   validator re-checks every quoted span on every run of `bench/validate.py`.
2. **Contamination**: cases are public. Every item carries
   `probe_status: untested` until the memorisation probe runs; probed-
   contaminated items are quarantined, reported separately, never deleted.
   The rolling post-cutoff holdout (PMCPA publishes continuously) is the
   clean track.
3. **No silent failure**: refusals, parse failures and API errors are
   recorded per call and reported as coverage, never dropped from
   denominators silently.
4. **Statistics**: items within a case share narrative, so all CIs are
   case-blocked bootstrap. Dev split for iteration; test split untouched
   until the design freezes.
5. **Reproducibility**: items regenerate byte-identically; every run
   archives its exact prompts, parameters and raw responses; provenance
   traces every quoted character back to the fetched HTML/PDF.

## 5. Run plan — EXECUTED (kept for the record; results in `docs/FINDINGS.md` §4)

Phase A (after the item audit passes): one cheap model pair —
**claude-sonnet-5** and **claude-haiku-4-5** — on a FIXED 150-item dev
subset, protocols P2 and P1 (K=7; haiku also sweeps temperature, which it
accepts). Deliverables: per-method reliability diagrams, Brier/ECE/AUROC,
within-item P2-vs-P1 divergence, and the known-class breakdowns (§3).
Estimated spend: ≈$6–8 sonnet, ≈$1 haiku.

Phase B (decision point): whichever method looks most informative gets the
wider item sweep and the stronger models; P3 (BDM) gets implemented if the
P2/P1 divergence justifies the third paradigm. Full-bank runs and test-split
scoring only after the form freeze and contamination pass.
