#!/usr/bin/env python3
"""Materialize immutable N1/N20/N50/N100 Anthropic execution frontiers.

This is an offline-only staging utility.  It takes a cumulative N100 canonical
export already persisted by ``run.py`` or ``p3_plan.py`` and writes disjoint,
deterministic wave files plus exact Message Batch translations.  It never
reads credentials and has no network code.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

BENCH = pathlib.Path(__file__).resolve().parents[1]
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))
from providers import anthropic_messages as adapter


OFFICIAL_DOCS = {
    "models": "https://platform.claude.com/docs/en/about-claude/models/overview",
    "sonnet5": "https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5",
    "thinking": "https://platform.claude.com/docs/en/build-with-claude/extended-thinking",
    "structured_outputs": "https://platform.claude.com/docs/en/build-with-claude/structured-outputs",
    "batch": "https://platform.claude.com/docs/en/build-with-claude/batch-processing",
    "pricing": "https://platform.claude.com/docs/en/about-claude/pricing",
}

RATES = {
    # 50% Message Batch rates at 2026-08-14. Sonnet 5's introductory
    # $2/$10 standard pricing runs through 2026-08-31.
    adapter.SONNET_MODEL: {"batch_input_per_mtok": 1.0,
                           "batch_output_per_mtok": 5.0},
    adapter.HAIKU_MODEL: {"batch_input_per_mtok": 0.5,
                          "batch_output_per_mtok": 2.5},
}


def _write_jsonl_exclusive(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    adapter.write_jsonl_exclusive(path, rows)


def _prompt_bytes(row: dict[str, Any]) -> int:
    """Serialized request bytes, used only for a conservative offline bound."""
    return len(adapter.canonical_json(row["request"]))


def _cost_bound(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Deterministic ceiling pending a real N1 usage calibration.

    Provider input tokenization is intentionally not guessed as an exact
    count. UTF-8 request bytes plus 1,024 tokens of per-call framing is a
    deliberately loose local upper planning bound; the output bound assumes
    every response consumes its full 4,096-token maximum. The real cost gate
    must be recomputed from the successful N1 receipt before submission.
    """
    request_bytes = sum(_prompt_bytes(row) for row in rows)
    n = len(rows)
    input_bound = request_bytes + 1024 * n
    output_bound = adapter.MAX_OUTPUT_TOKENS * n
    rates = RATES[model]
    cost = (input_bound / 1_000_000 * rates["batch_input_per_mtok"]
            + output_bound / 1_000_000 * rates["batch_output_per_mtok"])
    return {
        "n_requests": n,
        "serialized_request_utf8_bytes": request_bytes,
        "input_token_planning_upper_bound": input_bound,
        "max_output_tokens": output_bound,
        "batch_cost_hard_ceiling_usd": round(cost, 6),
        "caveat": ("Not an Anthropic token count. Re-cost from actual N1 usage before "
                   "each paid wave; hard ceiling assumes max output on every call."),
    }


def _assert_repeat_identity(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["protocol"] not in ("P2", "P3"):
            continue
        groups.setdefault((row["protocol"], row["item_id"]), []).append(row)
    for (protocol, item_id), calls in groups.items():
        if len({adapter.canonical_json(row["request"]) for row in calls}) != 1:
            raise adapter.AdapterError(f"{protocol}/{item_id}: repeated requests differ")
        repeats = sorted(row["repeat_index"] for row in calls)
        # A staged rank wave contains every repeat for each included item.
        if repeats != list(range(1, 8)):
            raise adapter.AdapterError(f"{protocol}/{item_id}: expected repeats 1..7")


def stage(run_dir: pathlib.Path) -> dict[str, Any]:
    staging = run_dir / "staging"
    cumulative = staging / "cumulative-n100.canonical.jsonl"
    rows = adapter.load_canonical_rows(cumulative)
    model = rows[0]["model"]
    protocol = rows[0]["protocol"]
    if model not in RATES:
        raise adapter.AdapterError(f"unsupported model {model}")
    if len({row["protocol"] for row in rows}) != 1:
        raise adapter.AdapterError("cumulative file contains multiple protocols")
    expected = 300 if protocol == "P1" else 2100
    if len(rows) != expected:
        raise adapter.AdapterError(f"{run_dir}: expected {expected} N100 calls, found {len(rows)}")
    if max(row["task_rank"] for row in rows) != 100:
        raise adapter.AdapterError(f"{run_dir}: cumulative plan does not reach N100")
    if set(row["task"] for row in rows) != {"T1", "T2", "T3"}:
        raise adapter.AdapterError(f"{run_dir}: cumulative plan must cover T1/T2/T3")

    files: dict[str, Any] = {}
    if protocol == "P1":
        smoke = [row for row in rows if row["task"] == "T1" and row["task_rank"] == 1]
        if len(smoke) != 1:
            raise adapter.AdapterError("P1 synchronous validation frontier is not one call")
        smoke_path = staging / "smoke-t1-n001.canonical.jsonl"
        _write_jsonl_exclusive(smoke_path, smoke)
        files["smoke_t1_n001"] = {
            "canonical": smoke_path.name,
            "canonical_sha256": adapter.file_sha256(smoke_path),
            "n_requests": 1,
            "execution": "synchronous Messages API; must succeed and be imported first",
        }
    else:
        smoke = []

    phase_ranges = (("n020", 1, 20), ("n050", 21, 50), ("n100", 51, 100))
    for phase, lower, upper in phase_ranges:
        wave = [row for row in rows if lower <= row["task_rank"] <= upper
                and row["call_id"] not in {entry["call_id"] for entry in smoke}]
        _assert_repeat_identity(wave)
        canonical_path = staging / f"wave-{phase}.canonical.jsonl"
        provider_path = staging / f"wave-{phase}.anthropic-batch.json"
        _write_jsonl_exclusive(canonical_path, wave)
        adapter.prepare_batch(canonical_path, provider_path)
        adapter.validate_batch_binding(canonical_path, provider_path, len(wave))
        files[phase] = {
            "rank_interval": [lower, upper],
            "canonical": canonical_path.name,
            "canonical_sha256": adapter.file_sha256(canonical_path),
            "provider_batch": provider_path.name,
            "provider_batch_sha256": adapter.file_sha256(provider_path),
            "cost_gate": _cost_bound(wave, model),
            "dependency": ("successful/imported smoke" if phase == "n020" and protocol == "P1"
                           else "successful/imported preceding cumulative wave"),
        }

    plan = {
        "schema_version": "pmcpa.anthropic-staged-plan.v1",
        "offline_only": True,
        "paid_calls_made": 0,
        "run_dir": str(run_dir.resolve()),
        "model": model, "protocol": protocol,
        "condition": adapter.MODEL_CONFIGS[model],
        "max_tokens": adapter.MAX_OUTPUT_TOKENS,
        "tasks": ["T1", "T2", "T3"], "target_n_per_task": 100,
        "k": 1 if protocol == "P1" else 7,
        "cumulative_canonical": cumulative.name,
        "cumulative_canonical_sha256": adapter.file_sha256(cumulative),
        "cumulative_n_requests": len(rows),
        "batch_rates_usd_per_mtok": RATES[model],
        "official_documentation": OFFICIAL_DOCS,
        "resume_rule": ("Import completed normalized receipts into this run directory, then "
                        "regenerate from the cumulative planner: it exports only missing calls. "
                        "Never submit a staged successor before its dependency is imported."),
        "files": files,
    }
    plan_path = staging / "execution-plan.json"
    adapter.write_json_exclusive(plan_path, plan)
    return {"plan": str(plan_path), "model": model, "protocol": protocol,
            "calls": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dirs", nargs="+")
    args = ap.parse_args(argv)
    for value in args.run_dirs:
        print(json.dumps(stage(pathlib.Path(value)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
