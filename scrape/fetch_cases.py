"""Step 1b: fetch each case page and save the raw HTML, unmodified.

Reads data/case_urls.jsonl, writes:
  data/html/<CASE-NUMBER>.html   full response bytes, exactly as received
  data/manifest.jsonl            url, case number, status, timestamp, size, sha256
  data/logs/failures.log         anything that did not come back 200

Resumable: a case whose HTML file already exists is skipped without a request,
so the run can be stopped and restarted freely.

Usage:
  python3 fetch_cases.py             # everything outstanding
  python3 fetch_cases.py --limit 1   # just the next one (used for the first few)
  python3 fetch_cases.py --dry-run   # show what would be fetched
"""

import argparse
import hashlib
import html as html_mod
import re
import sys
import time

from common import (
    CASE_URLS,
    CRAWL_DELAY,
    HTML_DIR,
    MANIFEST,
    append_jsonl,
    case_number_to_filename,
    fetch,
    log_failure,
    parse_case_numbers,
    read_jsonl,
    utc_now,
)

# <div class="info-holder-name">Case number</div>
# <div class="info-holder-text"><span class="search-request">AUTH/1795/2/06</span></div>
CASE_NO_RE = re.compile(
    r'info-holder-name"\s*>\s*Case number\s*</div>\s*'
    r'<div class="info-holder-text"\s*>(.*?)</div>',
    re.S | re.I,
)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def strip_tags(html):
    return html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def extract_case_number(html):
    """Raw 'Case number' text from a case page, or None if it holds no number.

    Falls back to the <h1>, which is "AUTH/1795/2/06 - General Practitioner v
    MSD". That fallback matters: at least one page has party names in the case
    number field ("Anonymous complainant v Vifor") and only the h1 carries the
    real number.
    """
    for pattern in (CASE_NO_RE, H1_RE):
        m = pattern.search(html)
        if m:
            val = strip_tags(m.group(1))
            if parse_case_numbers(val):
                return val
    return None


def slug_filename(url):
    """Last-resort filename when no case number can be found anywhere."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return "NOCASENO-" + re.sub(r"[^A-Za-z0-9._-]", "-", slug) + ".html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after N fetches")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases = read_jsonl(CASE_URLS)
    if not cases:
        print(f"No URLs in {CASE_URLS} -- run collect_index.py first")
        return 1
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    # Case numbers resolved on a previous run (for URLs the index had no number
    # for) so we can skip them without re-fetching.
    resolved = {
        r["url"]: r["filename"]
        for r in read_jsonl(MANIFEST)
        if r.get("filename") and r.get("http_status") == 200
    }
    # Guard against two URLs claiming the same output file.
    owner = {fn: url for url, fn in resolved.items()}

    fetched = skipped = failed = 0
    print(f"{len(cases)} cases in index; {len(resolved)} already fetched\n")

    for rec in cases:
        url = rec["url"]
        known = rec.get("case_number") or None
        # The index value is not always usable (party names, no number at all),
        # in which case we fall back to reading it off the page after fetching.
        filename = (case_number_to_filename(known) if known else None) or resolved.get(
            url
        )

        if filename and (HTML_DIR / filename).exists():
            skipped += 1
            continue

        if args.dry_run:
            print(f"WOULD FETCH {url}  -> {filename or '(name from page)'}")
            fetched += 1
            if args.limit and fetched >= args.limit:
                break
            continue

        try:
            status, raw, final_url = fetch(url)
        except Exception as exc:  # noqa: BLE001 - never halt the run on one page
            log_failure("fetch", url, f"{type(exc).__name__}: {exc}")
            append_jsonl(
                MANIFEST,
                {
                    "url": url,
                    "case_number": known,
                    "filename": None,
                    "http_status": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "fetched_at": utc_now(),
                },
            )
            failed += 1
            time.sleep(CRAWL_DELAY)
            if args.limit and (fetched + failed) >= args.limit:
                break
            continue

        entry = {
            "url": url,
            "final_url": final_url,
            "case_number": known,
            "filename": None,
            "http_status": status,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at": utc_now(),
        }

        if status != 200:
            log_failure("fetch", url, f"HTTP {status}")
            append_jsonl(MANIFEST, entry)
            failed += 1
        else:
            html = raw.decode("utf-8", errors="replace")
            # Prefer the index value, but only if a case number can be parsed
            # from it; otherwise read it off the page.
            if not (known and parse_case_numbers(known)):
                known = extract_case_number(html)
            case_number = known
            filename = case_number_to_filename(case_number) if case_number else None
            if not filename:
                filename = slug_filename(url)
                log_failure("fetch", url, "no case number found; named from slug")

            prior = owner.get(filename)
            if prior and prior != url:
                # Same case number from two URLs -- keep both, flag it.
                log_failure(
                    "fetch", url, f"filename collision with {prior} on {filename}"
                )
                filename = filename[:-5] + "--dup.html"

            entry["case_number"] = case_number
            entry["case_numbers"] = parse_case_numbers(case_number)
            entry["filename"] = filename
            # Raw bytes, unmodified -- nav/chrome and all.
            (HTML_DIR / filename).write_bytes(raw)
            owner[filename] = url
            append_jsonl(MANIFEST, entry)
            fetched += 1
            print(f"  {status}  {len(raw):>7,}b  {filename}")

        if args.limit and (fetched + failed) >= args.limit:
            break
        time.sleep(CRAWL_DELAY)

    print(f"\nfetched {fetched}, skipped {skipped}, failed {failed}")
    print(f"html -> {HTML_DIR}")
    print(f"manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
