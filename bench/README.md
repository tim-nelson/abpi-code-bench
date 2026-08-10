# ABPI Code Bench — harness

> Status: SKELETON, built and verified against a **fixture**. `data/l2/cases.jsonl`
> is being built in parallel; nothing here has been run against real case text.
> Design rationale lives in [DESIGN.md](DESIGN.md); the record shape this
> consumes is [`l2/SPEC.md`](../l2/SPEC.md) §2.

Four stages, stdlib-only except where noted. Every stage is deterministic:
same inputs → same bytes.

| stage | what | output |
| --- | --- | --- |
| [generate](generate.py) | L2 cases → benchmark items for T1, T1-triage, T3, T4 | `bench/items.jsonl` |
| [validate](validate.py) | schema + the invariants a schema cannot express | exit code |
| [run](run.py) | serve items under protocol P1 or P2 | `bench/runs/<ts>/responses.jsonl` |
| [score](score.py) | accuracy, Brier, ECE, reliability bins, case-blocked CIs | `bench/runs/<ts>/scores.json` |

## Run it

```bash
python3 bench/generate.py --use-fixture                      # -> bench/items.jsonl
uv run --with jsonschema python bench/validate.py --use-fixture

python3 bench/run.py --protocol P2 --limit 3                 # DRY RUN: prints prompts, no API call
python3 bench/run.py --protocol P1 --k 5 --limit 3           # DRY RUN

uv run --with anthropic python bench/run.py --protocol P2 --limit 10 --live
python3 bench/score.py --run bench/runs/<timestamp>
```

Drop `--use-fixture` once `data/l2/cases.jsonl` is real; `generate.py` defaults
to it and falls back to the fixture with a loud banner if it is absent.

## Scale up incrementally

The project owner's policy, and the reason `--dry-run` is the default and
`--limit` defaults to 10:

1. **Dry-run first, and read the prompts.** Every leakage bug that survives the
   attest shows up as text on screen. Nothing is written and nothing is spent.
2. **Smoke-run 10 items** on the cheap default model. Read
   `responses.jsonl` — not the score. Check the model answered the question
   asked, that structured output parsed, and that no call hit `max_tokens`.
3. **Score the smoke run.** Confirm coverage is 10/10 and the reliability bins
   are populated. A run that scores 6 of 10 is a harness bug, not a result.
4. **Only then increase** `--limit`, then `--k`, then the model. One axis at a
   time, so a change in the numbers has one candidate cause.

A P1 run costs `limit × k` calls. `--limit 100 --k 5` is 500 calls; check the
arithmetic before pressing enter.

## What an item is

One line of `items.jsonl`, conforming to [`item_schema.json`](item_schema.json):
an extract of quoted case text, a clause reference, the case metadata the model
is allowed to see, and the adjudicated label. Four hard rules, enforced in
`generate.py` and re-checked independently in `validate.py`:

1. **Quoted text comes only from L2 segments whose `leakage_attest.clean` is
   true and whose `kind` is `complaint` or `response`** (DESIGN §5). Nothing is
   ever trimmed to rescue a dirty segment — the item is not generated. The
   attest is *not* recomputed here (DESIGN §1.3: leakage is a data property);
   the generator only checks that `clean` agrees with its own `checks` map.
2. **`metadata_shown` is an allowlist**: respondent, complainant category /
   anonymity / contactability, Code year, date received. Everything else is
   withheld — including the procedure flags (`abridged` and `voluntary_admission`
   imply the outcome, `outwith_scope` *is* the T4 label), the `subject` line
   (L2 C4: the hero h2 routinely states the outcome), the case number, and
   `dates.completed`.
3. **T1 shows complaint + response; T1-triage shows complaint only.** A case
   with no clean response segment yields a triage item and no T1 item, and says
   so in the skip report.
4. **Siblings share a split.** Cases are grouped by union-find over both shared
   `source_files` and declared `sibling_cases`, and the split is a hash of the
   group key (DESIGN §6).

`tags` are analysis-side only and can encode outcome-bearing facts
(`appeal_flip`, `abridged`). **`run.py` never renders them.**

## Protocols

| id | what the model returns | confidence |
| --- | --- | --- |
| P2 | answer + stated probability, via structured output | the stated probability |
| P1 | answer only, over K perturbed presentations | frequency of the modal answer |

`score.py` puts both on one axis: `p` = the model's confidence in the answer it
gave, `o` = whether that answer matched the adjudicator. Brier is
`mean((p-o)^2)`; ECE uses 10 equal-mass bins, with edges extended so items
sharing a `p` never straddle a bin (P1's `p` only takes K+1 values).
Confidence intervals resample **cases**, not items.

### Phase-1 perturbations for P1

- **rendition swap** — an alternate publisher-written telling of the same
  complaint/response, where L2 supplies one (see the rendition finding below).
- **block order** — the order of the CLAUSE / CASE DETAILS / EXTRACT blocks.
  DESIGN §4.2 calls this "clause-presentation order"; with one clause under
  test per item, block order is its faithful analogue. Orders that render
  identically (T4 has no clause block) are collapsed, and the dry run reports
  `distinct prompts: N of M calls` so a degenerate plan is visible.
- **temperature** — **not available on current models.** `temperature`,
  `top_p` and `top_k` are rejected outright by `claude-opus-5`,
  `claude-sonnet-5`, `claude-fable-5` and `claude-opus-4-7/4-8`. `run.py` drops
  the axis and says so; repeated variants become resamples, which still measure
  decoding stochasticity but are not a temperature sweep. To sweep temperature,
  pick a model that still accepts sampling parameters (`claude-sonnet-4-6`,
  `claude-haiku-4-5`) and pass `--temperatures`.

Defaults: `--model claude-sonnet-5` (cheap smoke runs — state the model in any
writeup), `--thinking adaptive`, `--max-tokens 4096`. Thinking is a live
confound for a calibration study; it is recorded in every run manifest.

## The fixture

`fixtures/cases.fixture.jsonl` — four invented cases conforming exactly to
`l2/SPEC.md` §2, covering a clean breach, a burden-of-proof no-breach with an
anonymous non-contactable complainant, an appeal flip (one clause overturned,
one upheld), and an outwith-scope ruling. Cases 1 and 2 are co-reported, so the
sibling rule is exercised; case 2's response segment fails the attest, so the
T1 skip path is exercised; case 3 carries a `pdf_flow` rendition.

**All content is invented.** Case numbers are `TEST/xxxx/x/26`; the companies,
products and text do not exist and no sentence is taken from a real report.

Because L2 segments carry offsets rather than text, the fixture ships
`fixtures/l1_panes.fixture.json` — stand-in L1 pane text that the same slicing
code path reads. Both files are generated:

```bash
python3 bench/fixtures/build_fixture.py
```

From the fixture: **15 items** — T1 4, T1-triage 5, T3 2, T4 4.

## Deviations and open questions

Flagged for the owner. Each is a deliberate choice, cheap to reverse.

1. **`extract_provenance` is an array, not a single object.** The brief sketched
   one `{file, pane, char_start, char_end}`. A T1 extract quotes two disjoint
   spans (complaint and response), which one object cannot describe without
   claiming text between them that was not quoted. "Provenance to the
   character" (DESIGN §1.2) requires the array. `validate.py` re-slices every
   entry and reproduces `extract_text` byte for byte.

2. **T3 shows the Panel ruling in `metadata_shown`.** DESIGN §2 T3 makes the
   Panel ruling an *input* to appeal-survival; DESIGN §5 says metadata excludes
   all outcome fields. For T3 the Panel ruling is the premise of the question,
   not its label (the label is the Appeal Board outcome) — without it the
   question is ill-posed. `panel_ruling_for_clause` and `appellant` are
   permitted on T3 and on no other task, enforced in both the schema and the
   validator.

3. **T3 quotes complaint + response, not the ruling and reasoning.** DESIGN §2
   T3 wants the reasoning segment, but `panel_ruling` segments fail the attest
   by construction, and the phase-1 leakage rule admits only `complaint` and
   `response`. Lifting this needs L2 to emit an attest class for reasoning text
   with the disposition removed — a spec change, not a harness change.

4. **Renditions are mostly unusable, which blunts P1's flagship perturbation.**
   DESIGN §4.1 calls the three publisher-written renditions the corpus's unique
   asset. But `renditions.*` in L2 is a bare ref with no attest of its own, and
   the summary pane and report abstract both state the outcome — so neither can
   be quoted. A rendition is usable here only when it *contains* clean
   complaint/response segments, which in practice means `pdf_flow` on the 13
   PDF cases. **Recommendation: L2 should express renditions as segment
   indices, not bare refs**, so each carries its own attest.

5. **`splits` land 8 train / 7 dev / 0 test on the fixture.** Three sibling
   groups and a 60/20/20 hash; not a bug, just too few groups. Era splits and
   the post-cutoff holdout (DESIGN §6) are not implemented.

6. **Not implemented in phase 1**: the memorisation probe (every item ships
   `contamination.probe_status: "untested"`), redaction variants, protocols P3
   and P4, and tasks T2 and T5–T7. `clause_ref.clause_text` is always null
   until `data/code/` exists (DESIGN §7); affected items carry the tag
   `no_clause_text`, and the prompt tells the model the clause text is
   unavailable.
