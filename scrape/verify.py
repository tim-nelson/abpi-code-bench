"""Step 1c: check what actually landed on disk against the index and manifest.

Read-only. Run it after any fetch to confirm the corpus is complete and intact.
"""

import collections
import hashlib
import sys

from common import CASE_URLS, HTML_DIR, MANIFEST, case_number_to_filename, read_jsonl
from fetch_cases import extract_case_number


def main():
    cases = read_jsonl(CASE_URLS)
    manifest = read_jsonl(MANIFEST)
    files = sorted(HTML_DIR.glob("*.html"))
    problems = []

    print(f"index    : {len(cases)} case URLs")
    print(f"manifest : {len(manifest)} rows")
    print(f"html     : {len(files)} files")

    # Latest manifest row wins, so re-fetches supersede earlier failures.
    latest = {}
    for row in manifest:
        latest[row["url"]] = row

    ok = {u: r for u, r in latest.items() if r.get("http_status") == 200}
    print(f"\nURLs with a successful fetch: {len(ok)}")

    missing = [c["url"] for c in cases if c["url"] not in ok]
    if missing:
        problems.append(f"{len(missing)} indexed URLs never fetched successfully")
        for u in missing[:10]:
            print(f"  MISSING {u}")

    orphans = set(ok) - {c["url"] for c in cases}
    if orphans:
        problems.append(f"{len(orphans)} fetched URLs are not in the index")

    # Files on disk vs manifest
    on_disk = {f.name for f in files}
    expected = {r["filename"] for r in ok.values() if r.get("filename")}
    if expected - on_disk:
        problems.append(f"{len(expected - on_disk)} manifest files absent from disk")
        for n in sorted(expected - on_disk)[:10]:
            print(f"  NO FILE {n}")
    if on_disk - expected:
        problems.append(f"{len(on_disk - expected)} files on disk not in manifest")
        for n in sorted(on_disk - expected)[:10]:
            print(f"  UNTRACKED {n}")

    # Duplicate case numbers
    nums = collections.Counter(
        r["case_number"] for r in ok.values() if r.get("case_number")
    )
    dups = {n: c for n, c in nums.items() if c > 1}
    if dups:
        problems.append(f"{len(dups)} case numbers appear more than once")
        for n, c in list(dups.items())[:10]:
            print(f"  DUP x{c} {n}")

    # Integrity: size, closing tag, checksum, and filename/content agreement
    print("\nChecking file integrity ...")
    tiny = truncated = bad_hash = mismatch = 0
    sizes = []
    by_name = {r["filename"]: r for r in ok.values() if r.get("filename")}
    for f in files:
        raw = f.read_bytes()
        sizes.append(len(raw))
        if len(raw) < 5000:
            tiny += 1
            print(f"  TINY {f.name} ({len(raw)}b)")
        text = raw.decode("utf-8", errors="replace")
        if "</html>" not in text[-2000:]:
            truncated += 1
            print(f"  TRUNCATED {f.name}")
        row = by_name.get(f.name)
        if row:
            if row.get("sha256") and hashlib.sha256(raw).hexdigest() != row["sha256"]:
                bad_hash += 1
                print(f"  HASH MISMATCH {f.name}")
            in_page = extract_case_number(text)
            if in_page and case_number_to_filename(in_page) != f.name:
                mismatch += 1
                print(f"  NAME/CONTENT MISMATCH {f.name} contains {in_page!r}")

    for label, count in (
        ("suspiciously small (<5KB)", tiny),
        ("missing closing </html>", truncated),
        ("sha256 mismatch", bad_hash),
        ("filename != case number in page", mismatch),
    ):
        if count:
            problems.append(f"{count} files {label}")

    if sizes:
        sizes.sort()
        total = sum(sizes)
        print(
            f"\nsize: total {total / 1e6:.1f} MB | "
            f"min {min(sizes):,} | median {sizes[len(sizes) // 2]:,} | max {max(sizes):,}"
        )

    print()
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {len(files)} files, all {len(cases)} indexed cases present and intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
