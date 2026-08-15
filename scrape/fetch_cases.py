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
  python3 fetch_cases.py --locked    # recover the publication-locked corpus

``--locked`` is the reproducibility path.  It treats the latest row for each
URL in the tracked ``data/manifest.jsonl`` as an immutable publication lock:
the response must match the recorded byte count and SHA-256 before it is
written, and the manifest is never changed.  The default mode remains the
collector for making a new dated corpus and therefore appends observations to
the manifest.
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
    ap.add_argument(
        "--locked",
        action="store_true",
        help=(
            "fetch exactly the bytes pinned by the tracked publication manifest; "
            "do not update that manifest"
        ),
    )
    args = ap.parse_args()

    cases = read_jsonl(CASE_URLS)
    if not cases:
        print(f"No URLs in {CASE_URLS} -- run collect_index.py first")
        return 1
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    manifest_rows = read_jsonl(MANIFEST)
    latest = {}
    for row in manifest_rows:
        latest[row["url"]] = row

    # Case numbers resolved on a previous run (for URLs the index had no number
    # for) so we can skip them without re-fetching.  In locked mode, latest-row
    # semantics match scrape/verify.py: an old success cannot silently override
    # a later failed publication record.
    if args.locked:
        locked = {
            url: row
            for url, row in latest.items()
            if row.get("filename") and row.get("http_status") == 200
        }
        index_urls = {row["url"] for row in cases}
        if set(locked) != index_urls:
            missing = len(index_urls - set(locked))
            extra = len(set(locked) - index_urls)
            print(
                "Publication lock does not match data/case_urls.jsonl: "
                f"{missing} missing URL(s), {extra} extra URL(s)."
            )
            return 1
        malformed = [
            url
            for url, row in locked.items()
            if not row.get("sha256") or not isinstance(row.get("bytes"), int)
        ]
        if malformed:
            print(f"Publication lock has {len(malformed)} row(s) without bytes/SHA-256.")
            return 1
        resolved = {url: row["filename"] for url, row in locked.items()}
    else:
        locked = {}
        resolved = {
            r["url"]: r["filename"]
            for r in manifest_rows
            if r.get("filename") and r.get("http_status") == 200
        }
    # Guard against two URLs claiming the same output file.
    owner = {fn: url for url, fn in resolved.items()}

    fetched = skipped = failed = 0
    mode = "publication-locked" if args.locked else "live"
    print(f"{len(cases)} cases in index; {len(resolved)} resolved; mode={mode}\n")

    for rec in cases:
        url = rec["url"]
        known = rec.get("case_number") or None
        lock_row = locked.get(url)
        # The index value is not always usable (party names, no number at all),
        # in which case we fall back to reading it off the page after fetching.
        if args.locked:
            filename = lock_row["filename"]
            known = lock_row.get("case_number") or known
        else:
            filename = (case_number_to_filename(known) if known else None) or resolved.get(
                url
            )

        destination = HTML_DIR / filename if filename else None
        if destination and destination.exists():
            if not args.locked:
                skipped += 1
                continue
            raw = destination.read_bytes()
            if (
                len(raw) == lock_row["bytes"]
                and hashlib.sha256(raw).hexdigest() == lock_row["sha256"]
            ):
                skipped += 1
                continue
            print(f"  CORRUPT {filename}; attempting locked recovery")

        if args.dry_run:
            label = "WOULD RECOVER" if args.locked else "WOULD FETCH"
            print(f"{label} {url}  -> {filename or '(name from page)'}")
            fetched += 1
            if args.limit and fetched >= args.limit:
                break
            continue

        try:
            status, raw, final_url = fetch(url)
        except Exception as exc:  # noqa: BLE001 - never halt the run on one page
            stage = "fetch-locked" if args.locked else "fetch"
            log_failure(stage, url, f"{type(exc).__name__}: {exc}")
            if not args.locked:
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
            if not args.locked:
                append_jsonl(MANIFEST, entry)
            failed += 1
        else:
            if args.locked:
                got_sha256 = hashlib.sha256(raw).hexdigest()
                if len(raw) != lock_row["bytes"] or got_sha256 != lock_row["sha256"]:
                    log_failure(
                        "fetch-locked",
                        url,
                        "live response differs from publication lock "
                        f"(bytes {len(raw)} != {lock_row['bytes']} or "
                        f"sha256 {got_sha256} != {lock_row['sha256']})",
                    )
                    failed += 1
                    if args.limit and (fetched + failed) >= args.limit:
                        break
                    time.sleep(CRAWL_DELAY)
                    continue
                # The filename and identity were adjudicated when the lock was
                # published.  Do not rename from mutable page contents now.
                destination.write_bytes(raw)
                fetched += 1
                print(f"  {status}  {len(raw):>7,}b  {filename}  [locked]")
                if args.limit and (fetched + failed) >= args.limit:
                    break
                time.sleep(CRAWL_DELAY)
                continue

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
    return 1 if args.locked and failed else 0


if __name__ == "__main__":
    sys.exit(main())
