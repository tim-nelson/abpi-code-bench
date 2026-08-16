# P4 — Incentivized deferral (design draft, pre-spec)

Status: DRAFT captured 2026-08-16 from the design conversation. The full
specification now lives in `bench/P4_SPEC.md` (drafted 2026-08-16); this file
remains the record of the design rationale and the session's operational
state. The spend gate in the spec still needs owner sign-off before any calls.

## Idea (2026-08-16)

Give the model an explicit payoff structure per item: answering wrong costs
X, consulting an oracle costs c, answering correctly costs 0. The model
CHOOSES between answering and deferring; sweeping c over calls reveals a
switch point c\*, implying confidence p̂ = 1 − c\*/X. This is confidence
read off decisions under incentives — revealed preference — completing the
elicitation triangle: P1/P3 stated (mentalist), P2 behavioral consistency,
P4 revealed preference. The retired legacy-P3 "lottery/BDM" pilot (n=30,
pre-repair bank) was a cousin and hinted revealed < stated calibration for
Sonnet; P4 replicates that question properly.

## Design decisions already made

- One cost scenario per call (menus/switch-point self-reports collapse back
  into stated confidence). K cost levels × N items per model.
- Do NOT state the decision rule (p < 1 − c/X) in the prompt — only the
  payoffs; otherwise models compute the threshold from their stated
  probability and we re-measure P1 with extra steps.
- Framing effects (loss aversion at high stakes) are part of the measurand;
  discuss, don't engineer away.
- Compare vs SP: SP = deployer thresholds the reported confidence; P4 =
  model self-thresholds under costs. At matched oracle budget, which
  deferral policy has lower risk? Nobody's current setup answers this.
- Pilot scoping: ~5 cost levels × N=50 items × {claude-sonnet-5,
  gpt-5.6-sol} ≈ 750 calls/model, a few dollars each (batch APIs).
- Naming: the P4 label is reserved in score.py (active "P4" refuses until
  this protocol claims it); SP is the offline analysis, renamed 2026-08-16.

## Open design questions for the spec

- Cost grid (e.g. c/X ∈ {.05,.15,.25,.35,.45}) and whether X is stated in
  currency, points, or abstract units.
- Whether the deferral option's wording biases (neutral "refer to a
  specialist reviewer" vs "oracle").
- Per-item switch-point estimation with 5 binary observations (isotonic /
  logistic per item vs aggregate-only claims).
- Structured output: {"decision": "answer"|"defer", "answer": ...} schema;
  answer required even when deferring? (Yes — gives conditional accuracy of
  deferred items, the key SP comparison quantity.)
- Whether stated probability is ALSO elicited in the same call (risks
  contaminating the choice; probably a separate condition or omitted).

## Dissertation gaps noted 2026-08-16 (to fold in during the next docs pass)

1. P2 ≡ P3-vote equivalence: full five-model table (FINDINGS §0.3 has the
   summary; chat has the per-model numbers — recompute offline any time)
   as an appendix table; currently one sentence in results.tex.
2. Methods: class-imbalance-as-design-feature rationale (base-rate
   preservation is what makes calibration measurement ecologically valid;
   rebalancing would distort it; cost is minority-class precision, fix is
   N, not rebalancing) — check benchmark.tex covers it.
3. Methods: serving-host provenance for live-executor arms (hosts recorded
   per receipt; probes unpinned, pinning policy for full programs).
4. Memorisation probes remain pending for all 11 snapshots — caveat is in
   results.tex; keep until probes run.
5. Discussion: Kimi truncation as structured-output reliability finding;
   derived-P2 methodology note if P2 boards are referenced.

## Operational state (2026-08-16 end of session)

- Balances ≈: OpenRouter ~$18 remaining of 33.09; xAI ~$2.7; Anthropic
  small surplus; OpenAI ~ exhausted (topped after negative episode).
- Next execution order: this spec → spend gate → pilot; N=300 cheap arms via
  run-live --concurrency 4 (~$35); expensive N=300 via native batch APIs;
  memorisation probes (~$5); Qwen 3.6 / MiniMax M3 P1 probes as candidates.
