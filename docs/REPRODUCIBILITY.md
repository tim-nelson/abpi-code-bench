# Reproducibility and source snapshot

The build logic is deterministic for a fixed set of source bytes. That is a
different claim from saying that a Git clone, or a later refetch of the live
PMCPA website, reproduces those bytes. At the v2 freeze these three levels are:

| Level | Status | Meaning |
| --- | --- | --- |
| Logic + complete source snapshot | **Supported** | The tracked code, Code corpus and adjudications can rebuild L1, L2 and both benchmark banks from the frozen HTML/PDF bytes. Rebuild generated files twice and compare hashes. |
| Live refetch | **Best effort, not a frozen reproduction** | The tracked URL list can repopulate most source files, but live pages may change or disappear. One PDF extract and its verification are not recreated automatically. |
| Git clone only | **Not supported for a full rebuild** | Raw case HTML, case PDFs and L1 are deliberately ignored. The tracked prebuilt L2 and benchmark files can be used, but their provenance cannot be rebuilt or fully validated from a clone alone. |

## What the complete source snapshot means

The frozen external-input bundle is not in Git. It consists of:

- `data/html/`: 1,902 HTML files. The committed `data/manifest.jsonl` has a
  latest successful row and SHA-256 for every URL (128,501,517 source bytes).
- `data/pdf/`: 14 PDFs plus `data/pdf/manifest.jsonl` (3,451,100 PDF bytes).
  These cover 13 cases whose HTML report is absent or wrong. The four-page
  `AUTH-2063-10-07__pages144-147.pdf` is a locally derived extract of the
  170-page source Review; `scrape/fetch_pdfs.py` does not recreate the extract
  or its hand-verified manifest row.

The snapshot must include the manifests as well as the documents. L1 records
the source hashes and offsets; `l1/build_pdf.py` refuses a PDF whose hash does
not match its manifest. `data/l1/` is ignored too, but it is generated rather
than an external input.

The other build inputs are tracked: the case URL/index tables, `data/code/`
(including the Code PDFs and extracted clause tables), schemas, L2
adjudications, benchmark settings and generator/validator code. HTML alone is
therefore not sufficient, but a checkout plus the complete case-source
snapshot is.

With that snapshot placed at the paths above, the construction sequence is:

```bash
python3 scrape/verify.py
python3 l1/build.py
python3 l1/derive.py
uv run --with 'pypdfium2==5.12.1' python l1/build_pdf.py
uv run --with 'jsonschema==4.25.1' python l1/validate.py
python3 l1/coverage.py
python3 l2/build.py
uv run --with 'jsonschema==4.25.1' python l2/validate.py
python3 bench/generate.py
uv run --with 'jsonschema==4.25.1' python bench/validate.py
python3 bench/t5_generate.py --verify
```

Then run the remaining acceptance checks listed in `docs/WORKING_RULES.md`, including the
candidate, date, vocabulary, Code-year and PDF-clause checks. Rebuild each
generated artifact twice and compare bytes before accepting a reproduction.

## Why a refetch is not equivalent

`python3 scrape/fetch_cases.py` uses the committed URL list and will fetch
files missing from `data/html/`. It appends new rows to the existing manifest;
the latest successful row wins. `scrape/verify.py` proves that those newly
fetched files match those new rows, not that they match the 2026-08-01 frozen
hashes. Rerunning `collect_index.py` may also change the corpus roster.

`python3 scrape/fetch_pdfs.py` can refetch the 13 original PDF reports, but the
AUTH/2063 four-page extract and the recorded human case-number verification
remain manual steps. Consequently, refetching is useful for making a new
dated corpus. It is not a recovery mechanism for the frozen one.

## Runtime pinning still needed

Most builders are standard-library Python, but the repository does not declare
a supported Python version and has no `pyproject.toml`, requirements file,
`.python-version` or Python lockfile. Python and `uv` must be installed by the
operator. The commands above pin the two observed build-time packages:
`jsonschema==4.25.1` and `pypdfium2==5.12.1`. The independent Code PDF check
also refuses anything other than `pypdf==6.1.1`, while the separate Code PDF
parser documents `pypdfium2==4.30.0`. The Python interpreter itself remains
unlocked, so the frozen hashes and second-build comparison remain the final
acceptance criterion.

The review site has a committed `site/package-lock.json`; use `npm ci` for the
locked dependency graph. There is no repository-level Node version declaration.
The locked Vite package requires Node `^20.19.0 || >=22.12.0`.

Live benchmark runs are a separate reproducibility boundary: they require the
operator's API credentials from ignored `.env` state and remain subject to
provider/model drift. Archived response and score files are tracked evidence;
re-running a model is not part of rebuilding the item banks.

## GitHub publication blockers

The repository has no configured Git remote or Git LFS configuration. The
existing baseline commit `f45ba25` contains `bench/items.jsonl` as a
133,551,686-byte blob, above GitHub's 100 MiB per-file Git limit. Because the
blob is already in history, adding a smaller successor alone would not remove
the blocker. A publication decision is still required: rewrite/shard the bank,
migrate it to LFS, or publish it as a versioned release asset with checksums.

There is also no `LICENSE` or `CITATION.cff`, and the repository records no
decision authorising redistribution of the ignored PMCPA source snapshot.
Do not infer source-redistribution permission from the scraper or manifests.
