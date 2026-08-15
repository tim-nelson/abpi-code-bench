# pmcpa-bench — ABPI Code Bench

An LLM confidence-elicitation benchmark built from adjudicated PMCPA cases
(UK pharmaceutical self-regulation), supporting an Oxford Statistics
dissertation on behaviourist uncertainty quantification.

The active benchmark has three tasks:

| task | model input | prediction |
| --- | --- | --- |
| T1 | complaint and company response | whether the tested Code clause was breached |
| T2 | complaint only | the same clause-level verdict, before seeing the response |
| T3 | complaint, response and Panel ruling | whether the Panel ruling survived appeal |

For the current source roster, the deterministic build produces 10,497 items:
4,599 T1, 5,553 T2 and 345 T3. The 1,902 source reports expand to 2,004
canonical cases because some reports adjudicate several linked cases.

Only complete post-repair runs explicitly named in
`bench/active_results.json` populate the private results site. Earlier Phase A
and pre-repair T3 runs remain historical evidence and are excluded from active
boards. [FINDINGS.md](docs/FINDINGS.md) is an archive of pilot findings rather
than a statement of current benchmark performance.

## Rebuild from a clone

Downloaded PMCPA pages, PDFs, generated datasets and the private review website
are not public-repository inputs. A clone instead carries the source roster,
URLs, publication byte counts and SHA-256 hashes needed to retrieve and verify
the source material, then rebuild every data layer.

```bash
python3 scrape/bootstrap.py --plan   # inspect the exact fetch/build sequence
python3 scrape/bootstrap.py --all    # fetch locked sources, verify, build, validate
```

The bootstrap targets the website structure and source bytes present at the
project's publication snapshot. It deliberately does not try to adapt to a
future redesign. If a live source has changed, disappeared or no longer matches
the publication hash, retrieval fails visibly instead of silently producing a
different benchmark. See [REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) for the
precise contract and separate `--fetch`, `--check` and `--build` commands.

No LLM or provider API is called during source retrieval or benchmark
construction.

## Data pipeline

| stage | purpose | generated output |
| --- | --- | --- |
| `scrape/` | retrieve case reports, missing-case PDFs and historical ABPI Codes | `data/html/`, `data/pdf/`, `data/code/{html,pdf}/` |
| [L1](l1/README.md) | lossless, observational parsing of each report | `data/l1/` |
| [L2](l2/SPEC.md) | canonical cases, repairs and receipts | `data/l2/cases.jsonl` |
| [benchmark](bench/DESIGN.md) | construct and validate T1/T2/T3 items | `bench/items.jsonl` |
| [runner](bench/README.md) | fixed cumulative item order, P1/P2/P3 requests and durable resume | `bench/runs/<run>/` |
| [scorer](bench/APPROACH.md) | accuracy, calibration, discrimination and offline P4 selective prediction | `scores.json` |

L1 never repairs source content. L2 may repair it only with an explicit receipt.
Every excluded item candidate receives a durable reason, and validators
independently re-slice quoted text back to its source.

## Evaluation workflow

Items have one deterministic, case-aware order within each task. Evaluation is
cumulative: rank 1 is a configuration check, then the same prefix can grow to
10, 20, 50, 100, 200, 300 or beyond. Comparisons between models use the exact
common prefix, never whichever rows happened to parse successfully.

- P1 asks once for a verdict and stated probability of correctness.
- P2 repeats the byte-identical verdict prompt under one fixed model
  configuration. The initial horizon is K=7; K=3, K=7 and later top-ups are
  nested views of the same repeat ledger.
- P3 repeats P1's byte-identical answer-and-probability request K times and
  linearly pools the probabilities after orienting every draw to the same task
  label. It is a separate K-call method, not a relabelled P1 result.
- P4 makes no new model calls. It evaluates deferral/risk–coverage policies
  offline from any completed P1, P2 or P3 confidence signal.

The runner is dry-run/offline by default. Provider calls are never started
without an explicit approval covering the model, configuration, tasks,
prefix, call count and estimated cost. Batch exports have stable call IDs and
can be resumed after credit or transport failures without paying for completed
calls again.

## Project map

- [Measurement approach](bench/APPROACH.md)
- [Task and protocol design](bench/DESIGN.md)
- [Benchmark harness](bench/README.md)
- [Reproducibility contract](docs/REPRODUCIBILITY.md)
- [Defect and repair register](bench/review/DEFECTS.md)
- [Historical findings](docs/FINDINGS.md)
- [Dissertation draft](dissertation/)
- [Agent/project rules](docs/WORKING_RULES.md)

The dissertation brief at the repository root is a required project input and
must not be removed.
