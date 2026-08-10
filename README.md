# pmcpa-bench — ABPI Code Bench

An LLM confidence-elicitation benchmark built from 1,902 adjudicated PMCPA
cases (UK pharmaceutical self-regulation), supporting an Oxford Statistics
dissertation on behaviourist uncertainty quantification.

**Start here:** [`docs/FINDINGS.md`](docs/FINDINGS.md) (every measured result,
with caveats) · [`docs/WORKING_RULES.md`](docs/WORKING_RULES.md) (conventions, pipeline, hard rules) ·
[`bench/review/DEFECTS.md`](bench/review/DEFECTS.md) (how label quality was
established) · [`bench/APPROACH.md`](bench/APPROACH.md) (measurement design) ·
[`dissertation/`](dissertation/) (LaTeX draft) · [`site/`](site/) (review
website: `cd site && npm install && npm run data && npm run dev`).

Headlines so far: 10,045 audited items; verbalised confidence well calibrated
on average (ECE 0.080, n=150) yet 70% confident and 4% right on rulings that
expert adjudicators reversed (a 0.665 gap, n=78); inner-crowd confidence is
earned by the stronger model and fabricated by the weaker; a written
rationale improves accuracy and Brier together; memorisation probes 262/262
clean at the recall level.

---

## Layer 0 — the scraper (raw HTML only)

Fetches completed case reports from <https://www.pmcpa.org.uk/cases/completed-cases/>
and stores the full, unmodified HTML.

## Run

```bash
cd scrape
python3 collect_index.py          # build data/case_urls.jsonl
python3 fetch_cases.py            # fetch everything outstanding
python3 fetch_cases.py --limit 5  # or just the next 5
python3 fetch_cases.py --dry-run  # show what would be fetched
python3 verify.py                 # check disk against index + manifest
```

Stdlib only — no dependencies.

**Current state: complete.** 1902/1902 cases fetched, 128.5 MB, verified intact
(no missing files, no truncation, no checksum or naming mismatches).

## Pipeline

| stage | what | output |
| --- | --- | --- |
| scrape | fetch raw HTML | `data/html/`, `data/manifest.jsonl` |
| [survey](survey.md) | structural measurement, no parsing | `survey.md` |
| [L1](l1/README.md) | one standardised JSON record per page — observations only, 100% of visible `<main>` content | `data/l1/records.jsonl` |
| [derive](l1/derive.py) | classifier verdicts (heading confidence/normalisation, abstract boundary, source integrity) computed from the records alone — no HTML | `data/l1/derived.jsonl` |
| [pdf](l1/build_pdf.py) | verbatim records for the 13 cases whose HTML report is wrong/absent, from the verified PDFs (pypdfium2 glyph geometry, measured column order, oracle-verified against the HTML summary pane) | `data/l1/pdf_records.jsonl` |
| [L2](l2/SPEC.md) | canonical corrected case objects — value standardisation with receipts; L1 never repairs, L2 repairs with a recorded basis | `data/l2/cases.jsonl` (2,004 cases) |
| [bench](bench/DESIGN.md) | ABPI Code Bench — items, runners (P1/P2/P3), probes, scoring; audit register in [bench/review/DEFECTS.md](bench/review/DEFECTS.md) | `bench/items.jsonl` (10,045) + `bench/runs/` |
| [dissertation](dissertation/) | LaTeX draft (6,864 words; §2/§3 placeholders pending) | `dissertation/main.pdf` |

```bash
python3 l1/build.py                              # -> data/l1/records.jsonl (202 MB), byte-deterministic
python3 l1/derive.py                             # -> data/l1/derived.jsonl (10 MB)
uv run --with pypdfium2 python l1/build_pdf.py   # -> data/l1/pdf_records.jsonl (5 MB)
uv run --with jsonschema python l1/validate.py   # 1902 records + 1902 derived + 13 pdf, all valid
python3 l1/coverage.py                           # every visible <main> token is in its record
```

## Output

| Path | What |
| --- | --- |
| `data/case_urls.jsonl` | one record per case: URL, case number (when known), source, `lastmod` |
| `data/html/<CASE-NUMBER>.html` | full response bytes, exactly as received |
| `data/manifest.jsonl` | URL, case number, filename, HTTP status, timestamp, size, sha256 |
| `data/logs/failures.log` | anything that did not return 200, plus naming anomalies |
| `data/logs/run.log` | stdout of the last full run |
| `data/pdf/` | case-report PDFs for the 13 cases in `investigation/pdf_needed.json` whose HTML report pane is wrong or absent, plus `manifest.jsonl` with sha256 and per-file case-number verification (fetched 2026-08-01, all 13 verified correct) |
| `archive/` | one-shot survey/investigation/verification artefacts from the L1 iteration rounds — see `archive/README.md` |

## Filenames

From the case number, not the URL slug:
`CASE/0748/09/25` → `CASE-0748-09-25.html`, `AUTH/3048/6/18` → `AUTH-3048-6-18.html`.

The site's own **Case number field is dirty** — worth knowing before you parse.
103 of 1902 records are not the plain `AUTH/1234/5/06` form:

| Shape | Example | Count |
| --- | --- | --- |
| one report, several cases | `AUTH/1806/3/06 and AUTH/1809/3/06` (up to 5 at once) | ~95 |
| party names appended | `AUTH/3134/12/18 Complainant v Shield` | 3 |
| space instead of slash | `CASE 0277/08/24` | 2 |
| missing slash | `AUTH2024/7/07` | 1 |
| **no case number at all** | `Anonymous complainant v Vifor` | 1 |
| trailing tabs / entities | `AUTH/3558/9/21\t\t` | 6 |

So `common.parse_case_numbers()` *parses* numbers out with a regex rather than
string-munging the field. Consequences:

- A report covering several cases is named for all of them, joined by `__`:
  `AUTH-1806-3-06__AUTH-1809-3-06.html`. Unambiguous and reversible — 104 files.
- Where the case-number field holds no number, the `<h1>` is used instead. That
  is the only way `AUTH/3303/1/20` (the Vifor case) gets a correct filename.
- Every manifest row carries a `case_numbers` list, so multi-case reports are
  queryable without re-parsing the filename.

`normalise_names.py` re-applies this rule to files already on disk (idempotent;
backs up the manifest first).

## How the index is built

The listing page contains **no case links** — it is rendered client-side by Cludo
search. So `collect_index.py` unions two independent sources:

1. **`sitemap.xml`** — authoritative and stable, but URL-only.
2. **The Cludo search API** (`api.cludo.com`, customer 2562, engine 11712) — the
   same endpoint the listing page calls. Auth is
   `Authorization: SiteKey <base64("2562:11712:SearchKey")>`, built client-side
   in Cludo's public JS. Carries case number, dates and status.

As of 2026-08-01 the sitemap had 1902 cases and Cludo 1878 unique. Cludo reports
`TotalDocument: 1905`, but that count includes ~22 duplicate URLs and a couple of
non-case entries, and its `"*"` paging is relevance-ordered and unstable between
runs — so it under-reports. After normalising (drop `#fragment`/`?query`,
lowercase the path, force a trailing slash) Cludo is a strict subset of the
sitemap. **1902 is the working total.**

24 cases appear only in the sitemap and so have no case number from the index;
for those, `fetch_cases.py` reads the case number out of the page HTML
(`info-holder` block, with the `<h1>` as fallback).

## Politeness

`robots.txt` names only `user-agent: cludo` (`Disallow: /settings/`,
`Crawl-delay: 5`). We honour the 5s delay regardless and stay off `/settings/`.
User-Agent identifies the project and carries a contact address — see
`scrape/common.py`.

## Resuming

A case whose HTML file already exists is skipped without a request, so the run
can be stopped and restarted freely. Failures are logged and never halt the run.

On the 2026-08-01 run, 20 of 1902 requests (1.1%) died with `ECONNRESET` — no
HTTP rejection, no 429, and each URL succeeded on the very next attempt, so it
is server-side TCP flakiness rather than rate limiting. All 20 were recovered by
simply re-running. `fetch()` now retries transient network errors twice with
backoff, so a repeat run should not need the sweep.

Entries in `failures.log` are a historical record, not outstanding work — run
`verify.py` for the current state.
