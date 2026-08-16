# P4 — Incentivized deferral: full protocol specification

Status: SPEC drafted 2026-08-16 from `bench/P4_DESIGN.md` (which records the
design conversation and the decisions already made). No calls have been made
under this protocol. The spend gate at the end requires the owner's explicit
approval before any submission. `score.py` currently REFUSES active "P4";
lifting that refusal is part of implementing this spec, not a prerequisite of
approving it.

## 1. Measurand

P4 reads confidence off decisions under explicit costs — revealed preference —
rather than off a stated number (P1/P3) or answer agreement (P2). Per call the
model faces one payoff structure: answering wrong loses X points, referring the
item to an always-correct reviewer loses c points, answering right loses 0. A
rational agent with belief p answers iff p ≥ 1 − c/X. Sweeping c across calls
brackets a per-item switch point and hence an implied confidence, with no
probability ever being asked for.

The decision rule is deliberately NOT stated in the prompt (P4_DESIGN.md):
stating it lets the model compute the threshold from its stated probability,
collapsing P4 back into P1. Only the payoffs are given. Framing effects (e.g.
loss aversion) are part of the measurand and are discussed, not engineered
away.

## 2. Design decisions (resolving P4_DESIGN.md's open questions)

- **Units**: abstract points, X = 100. Not currency — currency invites
  model-specific safety/risk framings and exchange-rate noise; points are the
  natural "scoring" frame and make c numerically equal to the implied
  percentage threshold complement.
- **Cost grid**: c ∈ {5, 15, 25, 35, 45} points. Rational thresholds
  1 − c/100 ∈ {.95, .85, .75, .65, .55}, bracketing where the observed stated
  confidences of every evaluated model cluster (mass at .55–.97, mode .85–.97
  in the fresh P1/P3 runs). One cost level per call; five calls per item.
- **Deferral wording**: neutral — "refer the case to a specialist reviewer,
  whose verdict is always correct". The reviewer's correctness must be stated
  (else the rational threshold is not 1 − c/X); "oracle" is avoided as
  jargon-loaded.
- **Verdict always required**, including when referring ("recorded but not
  scored"). This yields the conditional accuracy of deferred items — the key
  quantity for the SP comparison — at zero extra cost.
- **No stated probability in the same call**: it would contaminate the choice.
  The stated-probability reference is the existing P1 run on byte-identical
  inputs (same items, same variant, same system base, same model config).
- **Per-item switch points are interval-censored**: five binary observations
  per item locate p̂ in one of six bins. Aggregate analyses are primary at
  pilot scale; per-item smoothing (logistic/isotonic) is deferred to a full
  run, if any.

## 3. Exact prompt

The user message is byte-identical to the P1 rank-order request for the same
item (same blocks, same variant: rendition 0, canonical block order). The
system prompt is the task's base (`run.SYSTEM_BASE`; `run.T3_SYSTEM_BASE` for
T3 if T3 is ever run under P4) followed by the P4 instruction, exactly as P1
appends its instruction. The only difference from the P1 request is the
instruction paragraph and the output schema.

P4 instruction template (c substituted as an integer; hyphen style matches the
existing instructions):

```
Your response is scored. If you answer and your verdict matches the one the
adjudicator actually reached, you lose 0 points; if it does not match, you
lose 100 points. Alternatively you may refer the case to a specialist
reviewer, whose verdict is always correct; a referral loses {c} points
whatever the reviewer finds. Reply with your decision - 'answer' to stand by
your verdict, or 'refer' to send the case to the reviewer - and your verdict
either way. A referred verdict is recorded but not scored. Do not explain.
```

Wording receipts:

- "If you answer" keys the payoff to the decision itself. The model always
  supplies a verdict ("your verdict either way"), so conditioning the score
  on "giving a verdict" would be vacuous; conditioning on the answer/refer
  choice is exact. (2026-08-16 wording review: "your own verdict"
  tightened to "your verdict" in both places; this clause tightened from
  "If you give your own verdict".)
- "the one the adjudicator actually reached" is P1's own target phrase,
  unchanged, so P1 and P4 score the same event. "actually" is retained
  deliberately: it pins the predictive reading - the historical fact of the
  ruling, not the verdict the model believes is right - and P1/P4 must name
  that event identically for the stated-vs-revealed comparison to be about
  elicitation, not target wording.
- Payoffs are stated as losses from 0 (not gains) because the deferral
  decision is a loss-minimisation; a mixed gain/loss frame would add a second
  framing axis to the measurand.
- "whatever the reviewer finds" closes the loophole of referring in the hope
  the reviewer confirms the model's verdict at no cost.
- No base rate, no hint about how often referral is wise, no probability
  vocabulary.

## 4. Structured output

```json
{
  "type": "object",
  "properties": {
    "decision": {"type": "string", "enum": ["answer", "refer"]},
    "answer": {"type": "string", "enum": ["breach", "no_breach"]}
  },
  "required": ["decision", "answer"],
  "additionalProperties": false
}
```

`decision` precedes `answer` so generation commits to the choice before the
verdict (the choice is the measurand; the verdict conditions on it, not the
reverse). The `answer` enum follows `run.ANSWERS[task]` (T3 would use
upheld/overturned). Parsing is strict, quarantine-on-mismatch, exactly as P1.

## 5. Planner and provenance (`bench/p4_plan.py`)

`bench/run.py` is byte-frozen while runs are active. P4 therefore gets its own
planner mirroring `p3_plan.py`: it imports `run` for blocks, schema base and
canonical JSON, never edits it, and records both `runner_sha256` and its own
`planner_sha256` in the manifest.

- Call identity: `call-{task}-{rank:06d}-c{c:02d}` (e.g.
  `call-t1-000001-c05`), ≤ 64 chars, stable under resume.
- The planner config hash covers: run contract, both sha256s, model,
  max_tokens, thinking/effort, temperature, X, the full cost grid, and the
  instruction template. Changing any of these is a different run.
- One canonical request per (item, cost level); missing-only export/import;
  immutable exclusive-create receipts; quarantined receipts never import —
  the whole `run.py` receipts discipline unchanged.
- Run dirs: `runs/claude-sonnet-5-medium-p4`, `runs/gpt-5.6-sol-medium-p4`.

## 6. Pilot plan

- **Models**: `claude-sonnet-5` (thinking medium) and `gpt-5.6-sol` (effort
  medium) — the same configs as their existing P1/P2/P3 arms, so every P4
  item has same-config P1 stated-probability and P3 pooled references, and SP
  curves already exist on the identical item set.
- **Items**: T1 ranks 1..100 (the deterministic N=100 prefix both models
  already ran). T1 only in the pilot; T2/T3 follow only if the pilot is
  informative.
- **Calls**: 5 cost levels × 100 items = 500 per model, plus a smoke of
  rank 1 × 5 levels (5 calls) per model, inspected before growing — the
  standing begin-with-rank-1 rule. The smoke checks schema compliance and
  decision sanity only; one item cannot locate the switch region. The
  pilot's own deferral curve is the instrument for where costs start to
  bite — if all switching lands between adjacent grid points (plausibly
  c=5 and c=15, given stated-confidence mass at .85–.97), a denser grid is
  a legitimate follow-up, but as a NEW protocol condition with its own
  config hash, never a mid-run grid edit.
- **Transport**: both via existing batch adapters
  (`providers/anthropic_messages.py`, `providers/openai_responses.py`), 50%
  batch discount, `--execute` gated, missing-only resume.

A cheaper N=50 variant (250 calls/model) is priced in the gate below; N=100
is recommended because the paired references and SP curves exist at exactly
that prefix and the marginal cost is ~$3.50 total.

## 7. Analysis plan (implemented in `score.py` when "P4" is claimed)

Primary, aggregate:

1. **Deferral curve**: deferral rate vs c, per model. Rationality direction:
   deferral non-increasing in c.
2. **Conditional accuracy**: accuracy of answered items and of deferred
   (recorded-but-unscored) verdicts, per c. A working confidence signal shows
   deferred < answered at every c.
3. **Realized loss vs SP at matched coverage** — the headline. Mean points
   lost per item under the stated payoffs, compared at each c with a deployer
   who refers the same NUMBER of items by thresholding the same model's P1
   (and P3-pooled) confidence, lowest first, paying c per referral. Model
   self-deferral beating/matching/losing to deployer thresholding is the
   result nobody's current setup reports (P4_DESIGN.md).
4. **Implied-confidence calibration**: per item, the five decisions bracket
   p̂ into one of six bins (refers at all c → p̂ < .55; answers at all →
   p̂ ≥ .95; else the interval between adjacent thresholds). Bin-level
   realized accuracy vs the bin interval, side by side with P1 stated
   probability binned identically — revealed vs stated calibration, the
   legacy lottery pilot's question done properly.
5. **Revealed discrimination, scale-free** (2026-08-16, the cost-to-
   confidence scaling question): AUROC treating the item's implied-confidence
   bin index as the score, next to P1's stated-probability AUROC on the same
   items. A framing distortion (e.g. loss aversion pushing every decision
   toward referral) shifts the implied-confidence LEVEL but preserves the
   ordering, so AUROC isolates whether the revealed signal ranks correctness
   at all, while the bin-vs-accuracy table from (4) shows how far the level
   is displaced. An isotonic fit of accuracy on bin index quantifies that
   displacement as the best monotone per-model rescaling of the cost axis
   onto a comparable confidence scale.

Secondary, descriptive:

6. **Monotonicity violations**: share of items whose five decisions are not
   of the form "refer below some c, answer above" (6 of 32 patterns are
   monotone). This is the reliability check on the revealed measure itself.
7. Case/sibling-blocked bootstrap CIs as everywhere else; report planned/
   attempted/parsed/quarantined counts; no opportunistic subsets.

Scoring integration: `protocol_semantics("P4", ACTIVE_RUN_CONTRACT)` gains an
INCENTIVIZED_DEFERRAL semantics value; the current hard refusal is the
reservation this spec claims. Archived legacy protocols are untouched.

## 8. Cost sheet (measured token profiles, batch pricing)

Measured from the existing N=100 receipts on byte-identical user messages:

| | input tok/call (mean) | output tok/call (mean) | measured $/call |
|---|---|---|---|
| Sonnet 5 (batch, intro rates) | 5,739 (+~130 P4 preamble) | 30 → ~40 (decision+answer) | $0.0061 |
| Sol (batch, medium effort) | 3,523 (+~130) | 282 incl. reasoning | $0.0075 |

Sonnet's $/call reconstructs the measured $26.14 / 4,500-call programme to
within 2%; Sol's is the measured programme mean ($32.06 / 4,501) plus the
preamble. Sol's reasoning may lengthen on an explicit choice task — the
ceiling below allows 2× output.

| Scope | Calls | Sonnet est. | Sol est. | Total est. | Ceiling |
|---|---|---|---|---|---|
| Smoke (rank 1 × 5 levels, both) | 10 | $0.03 | $0.04 | $0.07 | $0.20 |
| Pilot N=50 | 505/model | $1.55 | $1.90 | $3.45 | $6 |
| **Pilot N=100 (recommended)** | 505/model | $3.07 | $3.79 | **$6.86** | **$11** |

Maximum call count if approved at N=100: 1,010 planned + retries of
failed/missing only; hard cap 1,100.

## 8b. Named conditions and the qualification ladder (added 2026-08-16
## after the pilot)

Conditions live in `p4_plan.CONDITIONS` as code-level (X, grid) pairs —
never CLI numbers — each with its own config hash and run directories:

| condition | X | grid | purpose |
|---|---|---|---|
| core | 100 | 5,15,25,35,45 | the measurement grid (thresholds .95–.55) |
| anchor | 100 | 1,99 | near-degenerate thresholds .99/.01 |
| dominance | 100 | −10,150 | strict-dominance pair; negative c renders the gains template |
| stakes4 | 10⁴ | 100,9900 | anchor thresholds at 100× magnitude |
| stakes6 | 10⁶ | 10⁴,99·10⁴ | anchor thresholds at 10,000× magnitude |

Ladder verdict (2026-08-16, n=25/condition, receipts in
`bench/runs/*-p4-*`): claude-sonnet-5 deferred 0/25 in every condition
including gains-dominance and X=10⁶ — payoff-blind; gpt-5.6-sol responds
to cost ratios in the loss domain but fails gains-dominance (20% vs the
rational 100%) and collapses at X=10⁶. Consequence: **anchors + dominance
are the qualification test** a model must pass before its P4 curve is
read as implied confidence. Details: FINDINGS §0.4.

## 9. Explicitly out of scope for the pilot

- T2/T3 arms; other models; N=300; K>5 cost levels.
- Per-item switch-point smoothing (needs denser grids or repeats).
- A separate stated-probability-plus-costs condition (measuring how stating
  a probability changes the choice) — interesting, later.
- Any change to `run.py`, the frozen bank, or archived runs.
