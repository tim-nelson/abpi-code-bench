# Reproducibility and source retrieval

The public reproducibility target is a **clean clone plus live retrieval of the
publication-locked sources**: the repository carries the locks and the code,
and the sources and generated datasets are rebuilt from them.

For a fixed set of retrieved source bytes, the construction pipeline is
deterministic. A later live retrieval is necessarily conditional: the PMCPA
must still serve the same bytes. The fetchers compare every response with the
tracked byte count and SHA-256 and refuse changed content.

## What the clone contains

The tracked retrieval locks cover:

- 1,902 completed-case URLs and their expected HTML filenames, sizes and
  SHA-256 hashes;
- 13 case-report PDF URLs needed where the HTML report is absent or wrong;
- a recipe for extracting pages 144–147 of one 170-page review PDF;
- 493 historical/current ABPI Code HTML and PDF resources, plus three
  explicitly recorded unavailable URLs;
- parsing code, schemas, repair/adjudication receipts and independent
  verification programs.

The public history contains the reproducibility core — retrieval locks,
parsing and evaluation code, schemas, curation receipts, the defect register
and the tests — re-laid from the private working history, so its commit
identifiers are its own. The identifiers cited in `bench/code_lineage.json`
and `bench/review/DEFECTS.md` refer to this history; a private run manifest
that names a pre-publication commit refers to the same tree under its old
identifier.

## One-command rebuild

From the repository root:

```bash
python3 scrape/bootstrap.py --plan
python3 scrape/bootstrap.py --all
```

`--all` runs three phases:

1. retrieve only the locked roster (it does not rediscover or expand the live
   case list);
2. verify every local source against the publication locks;
3. build and validate Code tables, L1, L2 and the T1/T2/T3 item bank.

It makes network requests to the PMCPA source URLs. It makes no provider or LLM
call.

The phases can also be run separately:

```bash
python3 scrape/bootstrap.py --fetch   # populate missing locked sources
python3 scrape/bootstrap.py --check   # offline source/hash verification
python3 scrape/bootstrap.py --build   # offline generated-data rebuild
```

All fetchers are resumable. Existing verified files are skipped. A file is
written only after its response matches the expected length and hash; a changed
source fails rather than updating the lock.

## Build sequence

`bootstrap.py --build` composes the existing scripts rather than introducing a
second pipeline. In outline it runs:

```text
Code constitution parser
L1 HTML build and derivation
L1 PDF build, validation and source-coverage check
L2 build
benchmark generation
historical-Code PDF clause parsing
final L2 and benchmark rebuild
L2 and benchmark validation
```

The first L2/benchmark pass intentionally permits the generated PDF-clause
table to be absent. That creates the reference census needed by the independent
Code-PDF parser; L2 and the benchmark are then rebuilt from its output. This
breaks the old cold-start cycle without shipping a prebuilt dataset.

The expected final census is 2,004 L2 cases and 10,497 active items (T1 4,599,
T2 5,553, T3 345). Acceptance also runs the independent witnesses listed in
`docs/WORKING_RULES.md`. The current release was additionally checked by comparing the
generated artifacts from two complete builds byte-for-byte; that release check
is recorded separately rather than being repeated inside every `--build`.

## Runtime requirements

- Python 3 (the builders are standard-library-first);
- `uv` for pinned temporary build dependencies;
- `jsonschema==4.25.1`;
- `pypdfium2==5.12.1` for case-report PDF parsing;
- `pypdfium2==4.30.0` for historical Code PDF parsing;
- Ghostscript for the four-page derived PDF.

The derived PDF recipe removes volatile metadata and canonicalises the trailer
identifier. It is byte-stable with the tested Ghostscript build, but the
repository does not yet pin Ghostscript itself. Its extracted text and case
identity are independently verified; a different Ghostscript version is not
promised to create a byte-identical container.

## What can stop a future rebuild

This deliberately lean setup targets the website at publication time. It does
not contain compatibility code for a future redesign. A source may later move,
disappear or change while keeping the same URL. In that event the locked fetch
fails and the operator must recover the publication bytes from an external web
archive or other lawful source; the script must not accept different bytes
silently.

Permanent byte recovery independent of the publisher would require a separate
source archive. That is not part of the public GitHub repository or the present
scope.

## Model-run boundary

Benchmark construction and model evaluation are separate. Live models drift,
sample and require private credentials, so rerunning a model is not part of
rebuilding the item bank. Each fresh run records the exact item-bank hash,
model/config hash, prompt/request hashes, stable call IDs and cumulative ranks.
Only results registered in `bench/active_results.json` are active; historical
run directories are excluded by default.

Run horizons are nested checkpoints, not independent experiments: growing a
run from `--through-items 100` to `200` extends the same deterministic
per-task ranking, keeps every existing call ID, and exports only calls that
lack a completed receipt. A run's identity is its creation config
(`config_hash`), which pins the sha256 of the planner code that created it.
Growth after a reviewed code edit is lineage-verified: the old and new code
hashes must both appear in the tracked registry `bench/code_lineage.json`,
and every stored catalog row must re-render byte-identically (matching
`request_sha256` and `prompt_sha256`) under the exporting code before any new
call is planned. The run keeps its creation config unchanged, and each such
export appends an auditable `growth_events` entry to the run manifest.
Unregistered code drift, or any row that renders differently, refuses the
growth and requires a new run directory.
