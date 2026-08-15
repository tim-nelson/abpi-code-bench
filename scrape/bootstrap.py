"""Recover publication-locked sources and rebuild the benchmark locally.

This is intentionally a thin composition layer over the existing collectors
and builders.  It targets the website shape recorded by the publication
manifests; it does not attempt to adapt to a future redesign.

Examples:

    python3 scrape/bootstrap.py --plan
    python3 scrape/bootstrap.py --check
    python3 scrape/bootstrap.py --fetch
    python3 scrape/bootstrap.py --build
    python3 scrape/bootstrap.py --all

``--fetch`` makes network requests but accepts only source bytes pinned by the
tracked manifests.  ``--build`` makes no provider/model calls.
"""

import argparse
import shlex
import shutil
import subprocess
import sys

from common import ROOT

PYTHON = sys.executable

CONTRACT_CHECK = [PYTHON, "scrape/verify_bootstrap.py"]
LOCAL_SOURCE_CHECKS = [
    [PYTHON, "scrape/verify_bootstrap.py", "--require-files"],
    [PYTHON, "scrape/verify.py"],
]
FETCH_COMMANDS = [
    [PYTHON, "scrape/fetch_cases.py", "--locked"],
    [PYTHON, "scrape/fetch_pdfs.py", "--locked"],
    [PYTHON, "scrape/fetch_code.py", "--locked"],
]
BUILD_COMMANDS = [
    [PYTHON, "scrape/parse_constitution.py"],
    [PYTHON, "l1/build.py"],
    [PYTHON, "l1/derive.py"],
    [
        "uv",
        "run",
        "--with",
        "pypdfium2==5.12.1",
        "python",
        "l1/build_pdf.py",
    ],
    [
        "uv",
        "run",
        "--with",
        "jsonschema==4.25.1",
        "python",
        "l1/validate.py",
    ],
    [PYTHON, "l1/coverage.py"],
    # Cold-start pass: the Code-PDF parser audits coverage against benchmark
    # references, while the benchmark consumes its output.  L2 and bench both
    # tolerate that file being absent, so make a reference-only first pass,
    # parse the PDFs, then rebuild the canonical outputs.
    [PYTHON, "l2/build.py"],
    [PYTHON, "bench/generate.py"],
    [
        "uv",
        "run",
        "--with",
        "pypdfium2==4.30.0",
        "python",
        "scrape/parse_code_pdfs.py",
    ],
    [PYTHON, "l2/build.py"],
    [PYTHON, "bench/generate.py"],
    # Re-run the parser's coverage refusal against the final case years/items;
    # then render the bank once more from those checked Code rows.
    [
        "uv",
        "run",
        "--with",
        "pypdfium2==4.30.0",
        "python",
        "scrape/parse_code_pdfs.py",
    ],
    [PYTHON, "bench/generate.py"],
    [
        "uv",
        "run",
        "--with",
        "jsonschema==4.25.1",
        "python",
        "l2/validate.py",
    ],
    [
        "uv",
        "run",
        "--with",
        "jsonschema==4.25.1",
        "python",
        "bench/validate.py",
    ],
    # Independent acceptance checks. These deliberately do not share the
    # generator's witnesses; a clone that merely schema-validates is not yet a
    # reconstructed benchmark.
    [
        "uv",
        "run",
        "--with",
        "jsonschema==4.25.1",
        "python",
        "verify/ruling_battery.py",
    ],
    [PYTHON, "verify/candidate_accounting.py"],
    [PYTHON, "verify/received_date_witnesses.py"],
    [PYTHON, "verify/vocabulary_coverage.py", "--strict"],
    [PYTHON, "verify/code_year_witnesses.py"],
    [
        "uv",
        "run",
        "--with",
        "pypdf==6.1.1",
        "python",
        "verify/pdf_clause_texts.py",
    ],
    [PYTHON, "bench/test_run_foundation.py"],
    [
        "uv",
        "run",
        "--with",
        "jsonschema==4.25.1",
        "python",
        "bench/test_fixture_selection.py",
    ],
    [PYTHON, "bench/score.py", "--self-test"],
    [PYTHON, "bench/probe.py", "--self-test", "--items", "bench/items.jsonl"],
]


def display(commands):
    for command in commands:
        print(f"  {shlex.join(command)}")


def run(command):
    print(f"\n$ {shlex.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def require_runtime(commands):
    executables = {command[0] for command in commands}
    missing = sorted(name for name in executables if shutil.which(name) is None)
    if missing:
        raise SystemExit(f"missing required executable(s): {', '.join(missing)}")


def plan():
    run(CONTRACT_CHECK)
    print("\nFetch publication-locked source bytes:")
    display(FETCH_COMMANDS)
    print("\nVerify the populated source checkout:")
    display(LOCAL_SOURCE_CHECKS)
    print("\nBuild and validate L1, L2 and benchmark items:")
    display(BUILD_COMMANDS)
    print(
        "\nNo provider/model command is part of this bootstrap. Live pages that "
        "no longer match the publication hashes are reported and refused."
    )


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true", help="show the pipeline")
    action.add_argument("--check", action="store_true", help="offline source check")
    action.add_argument("--fetch", action="store_true", help="fetch locked sources")
    action.add_argument("--build", action="store_true", help="rebuild from local sources")
    action.add_argument("--all", action="store_true", help="fetch, verify and build")
    args = parser.parse_args()

    if not any((args.check, args.fetch, args.build, args.all)):
        plan()
        return 0

    if args.check:
        for command in [CONTRACT_CHECK, *LOCAL_SOURCE_CHECKS]:
            run(command)
        return 0

    commands = []
    if args.fetch or args.all:
        commands.extend(FETCH_COMMANDS)
        commands.extend(LOCAL_SOURCE_CHECKS)
    if args.build or args.all:
        if args.build:
            commands.extend(LOCAL_SOURCE_CHECKS)
        commands.extend(BUILD_COMMANDS)
    require_runtime(commands)
    for command in commands:
        run(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
