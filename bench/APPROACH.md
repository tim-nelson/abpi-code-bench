# Measuring uncertainty in the active benchmark

This document describes how T1, T2 and T3 outputs become confidence and
deployment measurements. Task construction is in [DESIGN.md](DESIGN.md).

## 1. Scored object

For each item a protocol produces:

- an answer (`breach` / `no_breach`, or `upheld` / `overturned`); and
- confidence `p` that this answer matches the PMCPA adjudication.

Let `o=1` when the answer matches the label and `o=0` otherwise.

- **Accuracy:** mean `o`.
- **Brier:** mean `(p-o)²`; lower is better.
- **Discrimination:** AUROC of confidence for correct versus incorrect
  answers.
- **Calibration:** reliability summaries and ECE. ECE is secondary because it
  is unstable and bin-sensitive on small task prefixes.
- **Selective prediction (SP):** risk/accuracy as low-confidence items are
  deferred, plus AURC and the two binary error directions.

Scores are reported per task. Any pooled summary is secondary because the
three tasks answer different questions.

## 2. Active protocols

| id | confidence source | calls per item |
| --- | --- | ---: |
| P1 | one stated probability alongside the verdict | 1 |
| P2 | modal-answer frequency across byte-identical verdict-only requests | K |
| P3 | linear pool of K byte-identical stated-probability draws | K |
| SP | offline threshold sweep over a completed P1, P2 or P3 signal | 0 |

P2 starts at K=7. Repeat indices are stable, so P2@3 is the first three calls
and later top-ups extend rather than replace the evidence. A P2 item with fewer
than the requested number of parsed calls is incomplete, never silently scored
at a smaller effective K.

P3 repeats the exact P1 request. Each answer/probability draw is oriented to
the same fixed positive label before an equal-weight linear probability pool
is taken. Its primary answer, confidence, Brier, calibration and SP view all
come from that pool at the declared odd K. Single-draw performance,
within-item dispersion and modal-vote performance are secondary diagnostics.

P1, P2 and P3 differ in compute and aggregation. P2 and P3 each include a
K-call ensemble answer; neither is a compute-matched causal contrast with
one-shot P1. We report these as operational system comparisons with call counts
and exact K visible.

## 3. Paired information comparison

T1 and T2 share the same clause outcome. T1 adds the respondent's response;
T2 withholds it. On their exact paired intersection we measure:

- answer changes;
- accuracy/Brier changes;
- changes in one-shot, verdict-repeat and repeated-stated confidence; and
- the distribution of confidence changes when response information is added,
  without prescribing its direction before seeing results.

T2 uncertainty is not a defect: it represents complaint-stage triage. The
analysis must not describe T2 as a complete-record legal decision.

## 4. Honest denominators and uncertainty

Every report includes:

- intended item rank and attempted count;
- completed and parsed calls;
- incomplete P2/P3 groups;
- refused/errored calls and retry status; and
- exact model snapshot and request configuration.

Cross-model comparisons use exact shared task ranks, not the intersection of
successful responses. Transient errors are retried by stable call id; persistent
errors stay visible.

Clauses from one case share narrative and are correlated. Confidence intervals
therefore resample canonical cases/source-report sibling groups. Report a
case-weighted sensitivity beside the ordinary item-weighted score when a few
cases contribute many clauses.

## 5. Cumulative execution

One canonical case-aware ordering supports nested checkpoints:

1. N=1 configuration check.
2. N=10–20 prompt/output inspection.
3. Fixed cost-based extensions such as 50, 100, 200 and 300.
4. Full T3 where affordable and useful.

These are prefixes of one run family, not independent replications. Accuracy
must not secretly determine the final stopping point; if it does, the analysis
is labelled exploratory.

## 6. Contamination and human answerability

Cases are public, so each model receives an exact-input/task-specific recall
probe. Completion/publication date defines temporal strata. A complaint date
does not establish that an item post-dates model training.

T1–T3 have a direct reasoning chain from shown evidence to adjudicated label.
That is a construct-validity claim, not an empirical claim about human
accuracy. A blinded human subset would be a useful later baseline; no human
performance number is asserted until measured.

## 7. Historical evidence

Pre-repair Phase A, original T3 and legacy P3 lottery runs are exploratory
archive material.
Their prompts differ from the active bank and they do not populate the active
leaderboard. Fresh results begin from an empty leaderboard.

Archived protocol identifiers are immutable. In those absent/v1-contract
files, legacy P2 means stated confidence and legacy P1 means repeated verdicts;
the scorer maps them explicitly rather than rewriting historical runs.
