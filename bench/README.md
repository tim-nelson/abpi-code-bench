# ABPI Code Bench harness

This directory builds, validates, serves and scores the active T1/T2/T3 bank.
The measurement rationale is in [DESIGN.md](DESIGN.md) and
[APPROACH.md](APPROACH.md).

| stage | command | output |
| --- | --- | --- |
| generate | `python3 -B bench/generate.py` | `bench/items.jsonl`, `bench/exclusions.jsonl` |
| validate | `uv run --with 'jsonschema==4.25.1' python -B bench/validate.py` | independent refusals/exit status |
| plan/export | `python3 -B bench/run.py ...` | dry-run text or resumable request ledger |
| score | `python3 -B bench/score.py --run <dir>` | `scores.json` |

Generated item data is not a public-repository input. Recreate it from locked
sources with `python3 scrape/bootstrap.py --all`.

## Tasks

- **T1 — full-information clause verdict.** The prompt shows the clean
  complaint and company response, then asks whether one specified Code clause
  was breached.
- **T2 — complaint-only clause verdict.** It asks the same question before the
  response is shown. Exact T1/T2 counterparts share a relative pair order and
  are analysed on the exact selected intersection.
- **T3 — appeal survival.** The prompt shows the pre-appeal information set and
  Panel ruling, then asks whether that ruling was upheld or overturned.

T2 was called `T1-triage` in historical runs. The legacy string remains
readable for archive scoring but is not emitted by the active generator.

## Protocols

| protocol | calls per item | confidence |
| --- | ---: | --- |
| P1 | 1 | model's stated probability that its answer is correct |
| P2 | K (initially 7) | modal-answer frequency across byte-identical requests |
| P3 | K (initially 7) | linear pool of repeated, oriented stated probabilities |
| SP | 0 new calls | threshold/risk–coverage analysis of completed P1/P2/P3 rows |

The protocol namespace was reordered before fresh results began. Archived
files are not renamed: legacy P2 means stated confidence, legacy P1 means
repeated verdicts, and legacy P3 means the retired lottery experiment. Their
manifest version keeps those historical meanings distinct from current P1–P3
and SP (offline selective prediction, formerly presented as P4; the P4 label
is reserved for a planned incentivized-deferral protocol).

P2 fixes the canonical prompt and model configuration. Repeat number is ledger
metadata only and never enters the request, so K=10 is a strict top-up of K=7.
The initial condition may omit the temperature parameter; `--temperature X`
sets one supported value on every request. A temperature change is a different
run, not another repeat. Repeated identical answers are a valid result.

P2 uses K times the calls of P1 and also ensembles the verdict. It is a chosen
system-level method, not a compute-matched causal comparison with P1.

P3 has its own offline planner, `bench/p3_plan.py`. It repeats the exact P1
answer-and-probability request, orients every probability to a fixed task label
and takes an equal-weight linear pool. P3 also uses K calls per item and is a
separate system-level method. Its evaluated K is exact; single-draw, dispersion
and answer-vote summaries are secondary views of the same completed repeats.

## Deterministic cumulative order

The runner assigns one stable, case-aware rank independently within each task:

1. one item per case is placed before a second item from that case;
2. T1 and T2 share an outcome-blind case/pair ordering, so their common-pair
   subsequences have the same relative order;
3. T2-only cases and items remain interleaved in T2, so an exact counterpart
   need not have the same absolute rank in T1 and T2;
4. ordering is deterministic from the published seed and full item bank;
5. task/split filters are applied after ranking.

The core order is deliberately not recent-first: early prefixes are intended
to spread across the corpus rather than estimate only the newest Code era.
Completion/publication-year results remain available as explicit strata.

`--through-items N` therefore means ranks 1..N **in every selected task**. For
example, T1,T2 at N=20 plans up to 40 items, not 20 total. A split filter may
return fewer because ranks remain absolute rather than being renumbered.

N=1 is the configuration check. Later checkpoints (10, 20, 50, 100, 200, 300)
extend the same prefix; they are not independent samples. A model completed
through 250 and one completed through 200 can be compared on the exact first
200 of a task, then on 250 after the second is topped up. T1-versus-T2 analysis
uses the exact paired intersection present in both selected prefixes.

## Safe offline planning

Dry-run is the default and writes nothing:

```bash
python3 -B bench/run.py --protocol P1 --tasks T1 --through-items 1
python3 -B bench/run.py --protocol P2 --tasks T1 --through-items 1 \
  --through-repeats 7
python3 -B bench/p3_plan.py --tasks T1 --through-items 1 \
  --through-repeats 7
```

The output includes the exact prompts, rank, call ID, request hash and call
arithmetic. `--live` deliberately fails closed; this command cannot submit a
provider request.

After the owner approves an exact provider/model/configuration/spend envelope,
persist and export the missing canonical calls:

```bash
python3 -B bench/run.py \
  --protocol P1 --model <exact-model-id> --tasks T1 --through-items 1 \
  --run-dir bench/runs/<model>-p1 \
  --export-batch /tmp/<model>-p1-n1.jsonl
```

The export format is provider-neutral JSONL: every row has a stable
`custom_id`/`call_id` and canonical request object. It is not itself an
Anthropic/OpenAI submission envelope. The provider-specific adapter is selected
only once the approved model/provider is known.

## Import and resume

Normalize downloaded provider results to one JSONL row per call:

```json
{"custom_id":"call-...","parsed":{"answer":"breach","probability":0.72},"response":{}}
```

For P2 omit `probability`. A failed row may instead carry `"error":"..."`.
Importing never overwrites a completed receipt:

```bash
python3 -B bench/run.py \
  --run-dir bench/runs/<model>-p1 \
  --import-results /tmp/<model>-p1-results.jsonl
```

After every import or top-up, re-score the run:

```bash
python3 -B bench/score.py --run bench/runs/<model>-p1
```

An active run is one named in `bench/active_results.json`. The exporter's gate
(`export_site_data.require_complete_active_run`) refuses any active run that
still has a pending, failed, duplicate, dropped or stale call, so a credit
interruption never promotes a partial run; import the resumed receipts and
re-score. The private review site's one-command refresh and build are not part
of the public repository.

To grow rank 1 to rank 20, reuse the exact run directory/configuration and
export to a new file with `--through-items 20`. Completed call IDs are omitted;
only missing/failed calls are exported. `--retry-ids <file>` can narrow that
set after a credit or transport failure.

The run manifest refuses a changed item-bank hash, model/protocol/config hash
or request identity. Start a new run directory for any such change.

## Scoring and SP

```bash
python3 -B bench/score.py --run bench/runs/<run>
python3 -B bench/score.py --run bench/runs/<run> --through-items 200 \
  --out bench/runs/<run>/scores-through-200.json
python3 -B bench/score.py --run bench/runs/<p2-run> --through-repeats 3 \
  --out bench/runs/<p2-run>/scores-k3.json
```

Scoring reports accuracy, Brier score, equal-mass ECE, AUROC, reliability bins
and source-report-sibling-blocked intervals, plus an equal-primary-case
sensitivity. P3 additionally reports its linear-pool primary view, repeated-call
draws, single-draw estimand, within-item dispersion and secondary vote view.
SP adds the attainable confidence-threshold risk–coverage curve and AURC to
every completed confidence signal without any API call. When T1 and T2 are both present, the scorer also
reports their exact paired intersection, answer changes and paired metric
deltas. P2/P3 items are included only when every repeat through the requested
K parsed successfully; effective K is never silently reduced. Prefix views
require a named `--out`, so they cannot overwrite the canonical `scores.json`.

For T1-versus-T2 conclusions use the exact paired intersection. For model
comparisons use the lowest common task rank and matching hashes/configuration,
not a model-specific successful subset.

## Fixture and offline tests

The fixture contains four invented cases and no real report sentence:

```bash
python3 -B bench/fixtures/build_fixture.py
python3 -B bench/generate.py --use-fixture \
  --out /tmp/pmcpa-fixture-items.jsonl \
  --exclusions /tmp/pmcpa-fixture-exclusions.jsonl
uv run --with 'jsonschema==4.25.1' python -B bench/validate.py \
  --use-fixture --items /tmp/pmcpa-fixture-items.jsonl \
  --exclusions /tmp/pmcpa-fixture-exclusions.jsonl
uv run --with 'jsonschema==4.25.1' python -B bench/test_fixture_selection.py
python3 -B bench/test_run_foundation.py
python3 -B bench/score.py --self-test
python3 -B bench/probe.py --self-test --items /tmp/pmcpa-fixture-items.jsonl
```

It generates 11 items: T1 4, T2 5 and T3 2, without overwriting the real bank.
The tests prove explicit fixture selection, relative pair ordering, per-task
prefixes, byte-identical P2 top-ups, fail-closed live mode and
export/import/retry idempotence.
