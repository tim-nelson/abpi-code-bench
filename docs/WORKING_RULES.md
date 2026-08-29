# Working rules — ABPI Code Bench

ABPI Code Bench is an LLM confidence-elicitation benchmark built from PMCPA
case adjudications for an Oxford Statistics dissertation on behaviourist
uncertainty quantification.

The dissertation brief (Ivanova, *Behaviourist approaches to confidence
elicitation in LLMs*) is the framing document for the benchmark. It is not
distributed with this repository.

## Read first

1. `README.md` — active scope and workflow.
2. `docs/REPRODUCIBILITY.md` — clean-clone source retrieval and build.
3. `bench/APPROACH.md` and `bench/DESIGN.md` — measurement and task design.
4. `bench/review/DEFECTS.md` — defect/repair register.
5. `l1/README.md` and `l2/SPEC.md` — data-layer specifications.

## Active scope

The source pipeline contains 2,004 canonical L2 cases. The deterministic main
bank contains 10,497 items across 1,515 primary `case_number` values (1,593
when folded sibling case numbers are included):

- T1: 4,599 complaint-plus-response clause-verdict items;
- T2: 5,553 complaint-only clause-verdict items;
- T3: 345 appeal-survival items.

`T2` is the active name of the task previously called `T1-triage`. New generated
items, docs and results use `T2`; archived run files retain the task string they
were actually served.

No T4 is currently active. The material-only task and discretionary-sanction
forecasting both require substantial new curation and may be added later as
separate projects. Do not relabel their small/weak legacy banks as core tasks.

Active evaluation protocols are:

- P1: one verdict plus stated probability of correctness;
- P2: K byte-identical verdict requests at one fixed model configuration,
  initially K=7;
- P3: K byte-identical answer-and-probability requests, combined by an
  equal-weight linear probability pool;
- P4: incentivized deferral (bench/P4_SPEC.md): per call the model chooses
  answer (wrong costs X=100 points) or refer to an always-correct reviewer
  (costs c), swept over a fixed grid; two-stage protocol — the
  anchors+dominance qualification gates whether a model's deferral may be
  read as implied confidence at all (six of eleven models are payoff-blind
  and carry "disqualified" as their result). Scored by bench/p4_score.py;
  scope T1/T2/T3 at N=100 per task for qualified models.
- SP: offline selective-prediction/risk–coverage analysis over fresh
  results, with no model call. Formerly presented as P4; renamed because it
  is a derived analysis, not a call protocol. The P4 label was subsequently
  claimed (2026-08-16) by the incentivized-deferral protocol above.

The old P3 lottery protocol and all pre-repair runs are historical/exploratory.
Archived identifiers are immutable and interpreted under their legacy manifest
version; they do not collide with the active P1--P3/SP namespace. The
active-results registry is `bench/active_results.json`; only complete,
provenance-verified fresh runs may enter it.

## Pipeline

```text
scrape/bootstrap.py
  -> locked case HTML/PDF and historical ABPI Code sources
l1/build.py + l1/derive.py + l1/build_pdf.py
  -> observational source records
l2/build.py
  -> canonical cases with repair receipts
bench/generate.py
  -> T1/T2/T3 items and recorded exclusions
bench/run.py
  -> offline request plans and resumable result ledgers
bench/score.py
  -> accuracy/calibration/discrimination/P3 analyses
```

Downloaded sources, generated data and the private review website are not
public-repository inputs. `python3 scrape/bootstrap.py --all` must recover and
rebuild the benchmark from the tracked publication locks. It never calls a
model.

## Data rules

- L1 observes; it never repairs. L2 repairs only with an explicit receipt and
  source basis.
- Null means “not stated”; an empty string means present-but-empty; an empty
  list means parsed and found nothing. Never infer a positive fact from
  silence.
- Every excluded item candidate receives a recorded, reasoned exclusion row.
- Do not silently cap, guess, impute or discard failures.
- A validator must independently reproduce its witness. Do not validate a
  builder with the same parser or inference rule that produced the value.
- Every prompt extract must re-slice byte-for-byte to clean allowed source
  spans. Labels, case number, completion date, procedure flags and other
  outcome-bearing fields are never rendered unless the task explicitly needs
  the field as an input (for example T3's Panel ruling).
- Sibling/co-reported cases remain grouped for splitting and uncertainty.
- Patterns over closed-vocabulary source fields must decide every observed
  value and refuse the undecided remainder.
- Repairs update the relevant adjudication/defect register as well as code.

## Cumulative evaluation rules

- Rank the whole item bank deterministically within each task before applying a
  task/split filter. Use one item per case before a second item from that case.
- Keep exact T1/T2 counterparts in the same relative pair order, but rank each
  whole task independently and analyse the exact selected intersection.
- `--through-items N` means ranks 1..N in each selected task. Successive N
  values are nested checkpoints, not independent experiments.
- A P2 top-up from K=7 to K=10 adds repeats 8–10; it never reruns 1–7. A P2
  item is scoreable at K only when all repeats 1..K have parsed receipts.
- Item horizons grow the same way: N-growth of an existing catalog is native
  and lineage-verified (bench/code_lineage.json registry + a full-catalog
  re-render byte-proof before any extension; growth_events logged in the
  manifest). Growth exports must name the single task being grown — never
  pass a multi-task filter to top up one task (see the 2026-08-16 DEFECTS
  incident). T3 currently stands at N=200 for all eleven models; T1/T2 at
  N=100.
- Public P2 boards report the derived vote-from-P3 view uniformly (native
  P2 ≡ P3-vote within noise; mixed native/derived boards are refused).
  Native P2 arms are frozen historical evidence.
- Compare models only on exact common item ranks and the same item-bank,
  protocol and configuration hashes. Report planned/attempted/parsed/error
  counts; never compare opportunistic successful subsets.
- Report tasks separately. Use case/sibling-blocked uncertainty and paired
  analysis for T1 versus T2.

## Provider-spend safety

`bench/run.py` is offline by default and has no enabled live-call path. It can
render prompts, export stable batch requests and import normalized receipts.

Before any provider submission, obtain the project owner's explicit approval
for:

- provider and exact model identifier;
- protocol, task set, item horizon and P2 repeat horizon;
- thinking/effort/temperature and token limits;
- exact maximum call count, token estimate and current price estimate;
- retry/resume behavior and spend cap.

Begin with rank 1, inspect it, then grow the same run. Provider credit failure
must leave completed calls terminal and permit failed/missing IDs only to be
retried. Never print or commit API keys; credentials live in ignored local
state.

## Verification after pipeline changes

Use pinned transient dependencies where shown:

```bash
python3 -B scrape/verify_bootstrap.py --require-files
python3 -B scrape/verify.py
python3 -B l1/build.py
python3 -B l1/derive.py
uv run --with 'pypdfium2==5.12.1' python -B l1/build_pdf.py
uv run --with 'jsonschema==4.25.1' python -B l1/validate.py
python3 -B l1/coverage.py
python3 -B l2/build.py
uv run --with 'jsonschema==4.25.1' python -B l2/validate.py
python3 -B bench/generate.py
uv run --with 'jsonschema==4.25.1' python -B bench/validate.py
uv run --with 'jsonschema==4.25.1' python -B verify/ruling_battery.py
python3 -B verify/candidate_accounting.py
python3 -B verify/received_date_witnesses.py
python3 -B verify/vocabulary_coverage.py --strict
python3 -B verify/code_year_witnesses.py
uv run --with 'pypdf==6.1.1' python -B verify/pdf_clause_texts.py
python3 -B bench/test_run_foundation.py
python3 -B bench/score.py --self-test
python3 -B bench/probe.py --self-test --items bench/items.jsonl
```

Rebuild generated artifacts twice and compare bytes. If the private site is
present, regenerate it from `bench/active_results.json` and run its checks so
it cannot display stale historical results as active.

## Repository/publication policy

- Raw/downloaded source bytes and generated datasets are ignored. Track the
  retrieval locks, code, schemas, curation/adjudication inputs and audits.
- The private `site/` directory is excluded from the public repository.
- Historical Phase A/T3 runs are excluded from active outputs. Do not delete or
  rewrite them during ordinary work; their archival deletion is a separate
  destructive operation requiring explicit approval. The public repository is a
  filtered re-layout of the private working history (see
  `docs/REPRODUCIBILITY.md`).
- Preserve unrelated user changes in the working tree.
- Prefer small, independently verifiable changes.

## Documentation rules

State measured claims with their denominator, task/protocol, item-bank hash or
snapshot and caveats. Do not promote historical findings into current results.
After fresh runs, update the active registry, the findings log and the
dissertation together; until then the dissertation's old numbers are pilot
evidence, not final benchmark claims.
