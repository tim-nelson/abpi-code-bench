"""Offline checks for the public source-retrieval bootstrap.

The tracked manifests are the public locks; the downloaded HTML/PDF bytes
are deliberately ignored.  This script first checks that the locks agree
with one another.  ``--require-files`` additionally checks a populated local
checkout without making requests or changing files.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys

from common import CASE_URLS, HTML_DIR, MANIFEST, ROOT, read_jsonl

PDF_LOCK = ROOT / "scrape" / "pdf_sources.json"
PDF_NEEDED = ROOT / "investigation" / "pdf_needed.json"
PDF_DIR = ROOT / "data" / "pdf"
PDF_MANIFEST = PDF_DIR / "manifest.jsonl"
CODE_DIR = ROOT / "data" / "code"
CODE_MANIFEST = CODE_DIR / "manifest.jsonl"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_rows(rows):
    latest = {}
    for row in rows:
        latest[row["url"]] = row
    return latest


def load_pdf_manifest():
    if not PDF_MANIFEST.exists():
        return {}
    return {
        row["file"]: row
        for row in read_jsonl(PDF_MANIFEST)
        if row.get("file")
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-files",
        action="store_true",
        help="also require every ignored source file and verify its hash",
    )
    args = parser.parse_args()
    problems = []

    cases = read_jsonl(CASE_URLS)
    case_urls = [row.get("url") for row in cases]
    if len(case_urls) != len(set(case_urls)):
        problems.append("data/case_urls.jsonl contains duplicate URLs")

    html_latest = latest_rows(read_jsonl(MANIFEST))
    html_lock = {
        url: row
        for url, row in html_latest.items()
        if row.get("http_status") == 200
    }
    if set(case_urls) != set(html_lock):
        problems.append(
            "the latest HTML publication-lock rows do not exactly match the case roster"
        )
    html_names = []
    for url, row in html_lock.items():
        name = row.get("filename")
        html_names.append(name)
        if not name or not isinstance(row.get("bytes"), int):
            problems.append(f"HTML lock row lacks filename/byte count: {url}")
        if not SHA256_RE.fullmatch(row.get("sha256", "")):
            problems.append(f"HTML lock row has invalid SHA-256: {url}")
    if len(html_names) != len(set(html_names)):
        problems.append("HTML publication lock maps more than one URL to a filename")

    pdf_lock = json.loads(PDF_LOCK.read_text(encoding="utf-8"))
    downloads = pdf_lock.get("downloads", [])
    derivations = pdf_lock.get("derivations", [])
    download_files = [row.get("file") for row in downloads]
    download_urls = [row.get("url") for row in downloads]
    if len(download_files) != len(set(download_files)):
        problems.append("PDF publication lock contains duplicate filenames")
    if len(download_urls) != len(set(download_urls)):
        problems.append("PDF publication lock contains duplicate URLs")
    for row in downloads:
        if not isinstance(row.get("bytes"), int) or row["bytes"] <= 0:
            problems.append(f"PDF lock has invalid byte count: {row.get('file')}")
        if not SHA256_RE.fullmatch(row.get("sha256", "")):
            problems.append(f"PDF lock has invalid SHA-256: {row.get('file')}")

    needed = json.loads(PDF_NEEDED.read_text(encoding="utf-8"))
    needed_pairs = {
        (entry["file"].removesuffix(".html") + (
            "" if len(entry["pdf_urls"]) == 1 else f"__{i}"
        ) + ".pdf", url)
        for entry in needed
        for i, url in enumerate(entry["pdf_urls"])
    }
    lock_pairs = {(row["file"], row["url"]) for row in downloads}
    if needed_pairs != lock_pairs:
        problems.append(
            "scrape/pdf_sources.json does not exactly cover investigation/pdf_needed.json"
        )
    for row in derivations:
        if row.get("source_file") not in download_files:
            problems.append(f"derived PDF has unknown source: {row.get('file')}")
        if row.get("file") in download_files:
            problems.append(f"derived PDF collides with a download: {row.get('file')}")
        if not (
            isinstance(row.get("first_page"), int)
            and isinstance(row.get("last_page"), int)
            and row["first_page"] <= row["last_page"]
        ):
            problems.append(f"derived PDF has invalid page range: {row.get('file')}")

    code_latest = latest_rows(read_jsonl(CODE_MANIFEST))
    code_locked = {url: row for url, row in code_latest.items() if row.get("file")}
    code_exclusions = {url: row for url, row in code_latest.items() if not row.get("file")}
    code_files = []
    for url, row in code_locked.items():
        code_files.append(row.get("file"))
        if not isinstance(row.get("bytes"), int) or row.get("bytes", 0) <= 0:
            problems.append(f"Code lock row has invalid byte count: {url}")
        if not SHA256_RE.fullmatch(row.get("sha256", "")):
            problems.append(f"Code lock row has invalid SHA-256: {url}")
    for url, row in code_exclusions.items():
        if row.get("http_status") in (None, 200):
            problems.append(f"Code exclusion lacks a failed HTTP status: {url}")
    code_by_file = {}
    for row in code_locked.values():
        prior = code_by_file.setdefault(row["file"], row)
        if (prior.get("bytes"), prior.get("sha256")) != (
            row.get("bytes"), row.get("sha256")
        ):
            problems.append(
                f"Code URL aliases disagree on locked bytes: {row['file']}"
            )

    if args.require_files:
        for row in html_lock.values():
            path = HTML_DIR / row["filename"]
            if not path.exists():
                problems.append(f"missing HTML: {path.name}")
            elif path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                problems.append(f"HTML differs from publication lock: {path.name}")

        local_pdf_manifest = load_pdf_manifest()
        for row in downloads:
            path = PDF_DIR / row["file"]
            if not path.exists():
                problems.append(f"missing PDF: {path.name}")
            elif path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                problems.append(f"PDF differs from publication lock: {path.name}")
            local = local_pdf_manifest.get(row["file"])
            if not local or local.get("sha256") != row["sha256"]:
                problems.append(f"local PDF manifest is missing/stale: {row['file']}")

        for row in derivations:
            path = PDF_DIR / row["file"]
            local = local_pdf_manifest.get(row["file"])
            if not path.exists():
                problems.append(f"missing derived PDF: {path.name}")
            elif not local or local.get("sha256") != sha256(path):
                problems.append(f"derived PDF manifest is missing/stale: {row['file']}")

        for row in code_locked.values():
            path = CODE_DIR / row["file"]
            if not path.exists():
                problems.append(f"missing Code source: {row['file']}")
            elif path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                problems.append(f"Code source differs from publication lock: {row['file']}")

    print(
        f"source locks: {len(cases)} case URLs, {len(html_lock)} locked HTML, "
        f"{len(downloads)} locked case PDF, {len(derivations)} PDF derivation, "
        f"{len(code_locked)} locked Code resources, "
        f"{len(code_exclusions)} pinned Code exclusions"
    )
    if args.require_files:
        print("local source files: required")
    else:
        print("local source files: not required (locks-only check)")

    if problems:
        print("PROBLEMS:")
        for problem in problems[:30]:
            print(f"  - {problem}")
        if len(problems) > 30:
            print(f"  - ... and {len(problems) - 30} more")
        return 1
    print("OK: the public source-retrieval locks are internally consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
