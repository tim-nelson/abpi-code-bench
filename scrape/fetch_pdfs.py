"""Fetch the case-report PDFs for cases whose HTML report pane is wrong or absent.

Driven by investigation/pdf_needed.json (13 cases as of 2026-08-01: 4 whose
report pane belongs to a different case, 9 whose report exists only as a PDF).
Writes data/pdf/<html-stem>.pdf plus data/pdf/manifest.jsonl with url, sha256,
size and %PDF magic check. Same politeness as the HTML scrape (5s delay,
identifying UA). Idempotent: files already on disk are not re-fetched.

Verification of the downloads (first-page case number vs expected case) was
done by hand on 2026-08-01 and recorded in the manifest's `verification`
field. One caveat lives there too: AUTH-2063-10-07.pdf is the full Code of
Practice Review No 59 (170pp); the case itself is at pdf pages 144-147,
extracted to AUTH-2063-10-07__pages144-147.pdf with ghostscript
(`gs -sDEVICE=pdfwrite -dFirstPage=144 -dLastPage=147`).

For a public reproduction use ``--locked``.  That mode reads the tracked
``scrape/pdf_sources.json``, accepts downloads only when their bytes and
SHA-256 match the publication lock, and recreates the one derived page extract.
The ignored ``data/pdf/manifest.jsonl`` is then generated locally for L1.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

from common import CRAWL_DELAY, DATA, ROOT, USER_AGENT, fetch

NEEDED = ROOT / "investigation" / "pdf_needed.json"
OUT_DIR = DATA / "pdf"
MANIFEST = OUT_DIR / "manifest.jsonl"
LOCK = ROOT / "scrape" / "pdf_sources.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def locked_manifest_row(entry, data, status):
    return {
        "file": entry["file"],
        "html_file": entry["html_file"],
        "case_number": entry["case_number"],
        "reason": entry["reason"],
        "url": entry["url"],
        "bytes": len(data),
        "sha256": digest(data),
        "magic_ok": data[:5] == b"%PDF-",
        "status": status,
        "n_pages": entry["n_pages"],
        "verified_case": entry["case_number"],
        "verification": entry["verification"],
        "case_matches": True,
    }


def locked_derivation_row(entry, data):
    return {
        "file": entry["file"],
        "html_file": entry["html_file"],
        "case_number": entry["case_number"],
        "reason": entry["reason"],
        "url": None,
        "bytes": len(data),
        "sha256": digest(data),
        "magic_ok": data[:5] == b"%PDF-",
        "status": "extracted_locally_from_publication_locked_source",
        "n_pages": entry["n_pages"],
        "verified_case": entry["case_number"],
        "verification": entry["verification"],
        "case_matches": True,
        "case_pages_in_pdf": [entry["first_page"], entry["last_page"]],
        "derived_from": entry["source_file"],
        "derivation_tool": entry["tool"],
        "derivation_canonicalization": "omit info date and zero trailer ID",
    }


def derive_locked_pdf(entry):
    gs = shutil.which("gs")
    if not gs:
        raise RuntimeError(
            "Ghostscript (`gs`) is required to extract PDF pages 144-147"
        )
    source = OUT_DIR / entry["source_file"]
    destination = OUT_DIR / entry["file"]
    temporary = destination.with_suffix(".tmp.pdf")
    command = [
        gs,
        "-q",
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-dOmitInfoDate",
        "-dDeterministicID",
        "-sDEVICE=pdfwrite",
        f"-dFirstPage={entry['first_page']}",
        f"-dLastPage={entry['last_page']}",
        f"-sOutputFile={temporary}",
        str(source),
    ]
    try:
        subprocess.run(command, check=True)
        data = temporary.read_bytes()
        if data[:5] != b"%PDF-":
            raise RuntimeError("Ghostscript output is not a PDF")
        # pdfwrite still emits a run-specific trailer ID.  It is fixed-width
        # metadata, so replacing its two 32-hex values in place leaves every
        # xref offset and all page content untouched while making repeated
        # extraction with the same Ghostscript version byte-stable.
        data, replacements = re.subn(
            rb"/ID \[<[0-9A-Fa-f]{32}><[0-9A-Fa-f]{32}>\]",
            b"/ID [<00000000000000000000000000000000>"
            b"<00000000000000000000000000000000>]",
            data,
        )
        if replacements != 1:
            raise RuntimeError(
                f"expected one Ghostscript trailer ID, found {replacements}"
            )
        temporary.write_bytes(data)
        os.replace(temporary, destination)
        return data
    finally:
        if temporary.exists():
            temporary.unlink()


def read_existing_manifest():
    if not MANIFEST.exists():
        return {}
    rows = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["file"]] = row
    return rows


def write_manifest(rows):
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def locked_main(args):
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    downloads = lock["downloads"]
    derivations = lock["derivations"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing_manifest = read_existing_manifest()
    rows = []
    fetched = skipped = failed = requests = 0

    for entry in downloads:
        destination = OUT_DIR / entry["file"]
        data = destination.read_bytes() if destination.exists() else None
        matches = data is not None and (
            len(data) == entry["bytes"] and digest(data) == entry["sha256"]
        )
        if matches:
            rows.append(locked_manifest_row(entry, data, "publication_locked"))
            skipped += 1
            continue

        if args.limit is not None and requests >= args.limit:
            break
        if args.dry_run:
            state = "corrupt" if data is not None else "missing"
            print(f"WOULD FETCH [{state}] {entry['url']} -> {entry['file']}")
            requests += 1
            continue

        try:
            status, candidate, _ = fetch(entry["url"])
        except Exception as exc:  # noqa: BLE001 - report all source gaps together
            print(f"FAIL {entry['url']}: {type(exc).__name__}: {exc}")
            failed += 1
            requests += 1
            time.sleep(CRAWL_DELAY)
            continue
        requests += 1
        if status != 200:
            print(f"FAIL {entry['url']}: HTTP {status}")
            failed += 1
        elif candidate[:5] != b"%PDF-":
            print(f"FAIL {entry['url']}: response is not a PDF")
            failed += 1
        elif len(candidate) != entry["bytes"] or digest(candidate) != entry["sha256"]:
            print(f"FAIL {entry['url']}: response differs from publication lock")
            failed += 1
        else:
            destination.write_bytes(candidate)
            rows.append(locked_manifest_row(entry, candidate, "publication_locked"))
            fetched += 1
            print(f"http_200  {len(candidate):>9}  {entry['file']}  [locked]")
        time.sleep(CRAWL_DELAY)

    complete_downloads = len(rows) == len(downloads)
    if complete_downloads:
        for entry in derivations:
            destination = OUT_DIR / entry["file"]
            data = destination.read_bytes() if destination.exists() else None
            existing = existing_manifest.get(entry["file"], {})
            already_generated = data is not None and data[:5] == b"%PDF-" and (
                digest(data) == entry.get("reference_sha256")
                or (
                    existing.get("status", "").startswith("extracted")
                    and existing.get("sha256") == digest(data)
                )
            )
            if args.dry_run:
                if not already_generated:
                    print(
                        f"WOULD DERIVE {entry['file']} from {entry['source_file']} "
                        f"pages {entry['first_page']}-{entry['last_page']}"
                    )
                continue
            try:
                if not already_generated:
                    data = derive_locked_pdf(entry)
                    print(
                        f"derived  {len(data):>9}  {entry['file']} "
                        f"from pages {entry['first_page']}-{entry['last_page']}"
                    )
                rows.append(locked_derivation_row(entry, data))
            except Exception as exc:  # noqa: BLE001 - concise bootstrap failure
                print(f"FAIL deriving {entry['file']}: {type(exc).__name__}: {exc}")
                failed += 1

    complete = len(rows) == len(downloads) + len(derivations)
    if not args.dry_run and complete and not failed:
        write_manifest(rows)
        print(f"manifest: {len(rows)} rows -> {MANIFEST}")
    elif not args.dry_run:
        print("manifest not written: source set is incomplete")

    if args.dry_run:
        missing = requests
        print(
            f"locked PDF plan: {len(downloads)} downloads, {skipped} present, "
            f"{missing} fetch(es) needed, {len(derivations)} derivation(s)"
        )
    else:
        print(f"fetched {fetched}, present {skipped}, failed {failed}")
    return 1 if failed else 0


def live_main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = json.loads(NEEDED.read_text(encoding="utf-8"))
    existing = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["file"]] = row

    rows = []
    fetched = 0
    for entry in entries:
        stem = entry["file"].removesuffix(".html")
        for i, url in enumerate(entry["pdf_urls"]):
            suffix = "" if len(entry["pdf_urls"]) == 1 else f"__{i}"
            dest = OUT_DIR / f"{stem}{suffix}.pdf"
            if dest.exists():
                # Keep the existing manifest row (it may carry verification
                # notes); fall back to a fresh one if the file predates it.
                row = existing.get(dest.name)
                if row:
                    rows.append(row)
                    continue
                data = dest.read_bytes()
                status = "already_on_disk"
            else:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = resp.read()
                        status = f"http_{resp.status}"
                except Exception as e:  # noqa: BLE001 -- log and continue, like fetch_cases
                    print(f"FAIL {url}: {e}")
                    rows.append({"file": dest.name, "url": url, "error": str(e)})
                    time.sleep(CRAWL_DELAY)
                    continue
                dest.write_bytes(data)
                fetched += 1
                time.sleep(CRAWL_DELAY)
            rows.append({
                "file": dest.name,
                "html_file": entry["file"],
                "case_number": (entry["case_number"] or "").strip(),
                "reason": entry["reason"].split(" ")[0],
                "url": url,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "magic_ok": data[:5] == b"%PDF-",
                "status": status,
            })
            print(f"{status:>16}  {len(data):>9}  {dest.name}")

    # Preserve rows for derived files (e.g. the 2063 page extract) that are
    # not driven by pdf_needed.json.
    for name, row in existing.items():
        if not any(r["file"] == name for r in rows):
            rows.append(row)

    with MANIFEST.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"{fetched} fetched; manifest: {len(rows)} rows -> {MANIFEST}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--locked",
        action="store_true",
        help="recover only the downloads pinned by scrape/pdf_sources.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.dry_run and not args.locked:
        parser.error("--dry-run is available with --locked")
    if args.limit is not None and not args.locked:
        parser.error("--limit is available with --locked")
    return locked_main(args) if args.locked else live_main()


if __name__ == "__main__":
    raise SystemExit(main())
