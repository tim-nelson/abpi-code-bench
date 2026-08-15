"""Offline cumulative planner for repeated stated-probability calls (P1R).

This is deliberately separate from :mod:`run`: changing ``run.py`` would
invalidate the immutable runner hash recorded by existing active runs.  P1R is
a fresh P1 condition, not a migration of the one-call P1 ledger.

The planner has no provider execution path.  It writes provider-neutral
canonical JSONL for ``bench/providers/openai_responses.py`` (or another
adapter), and imports normalized results through the existing durable ledger.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import re
import sys
import time
from types import SimpleNamespace

try:  # Script execution puts bench/ first on sys.path.
    import run
except ModuleNotFoundError:  # Package-style import in tests.
    from bench import run


BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
PROTOCOL = "P1"
PROTOCOL_CONDITION = "repeated_stated_probability"
AGGREGATION = "linear_probability_pool"
PLANNER_NAME = "bench/p1r_plan.py"
ACTIVE_TASKS = ("T1", "T2", "T3")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def planner_sha256() -> str:
    return run.sha256(pathlib.Path(__file__).resolve())


def runner_sha256() -> str:
    return run.sha256(pathlib.Path(run.__file__).resolve())


def planner_config(args: SimpleNamespace) -> dict:
    """Immutable model/request condition; K is intentionally absent."""
    return {
        "contract": run.RUN_CONTRACT,
        "runner_sha256": runner_sha256(),
        "planner_sha256": planner_sha256(),
        "protocol": PROTOCOL,
        "protocol_condition": PROTOCOL_CONDITION,
        "aggregation": AGGREGATION,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "rationale": False,
        "effort": args.effort or None,
        "temperature": None,
        "seed": str(args.seed),
    }


def _base_variant() -> dict:
    return {
        "index": 0,
        "rendition": 0,
        "block_order": run.BLOCK_ORDERS[0],
        "temperature": None,
    }


def validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("--through-repeats must be >= 1")
    if k % 2 == 0:
        raise ValueError("P1R requires an odd --through-repeats K")


def build_call_plan(items: list[dict], through_repeats: int,
                    args: SimpleNamespace) -> tuple[list[dict], dict]:
    """Return a stable 1..K rectangle of byte-identical P1 requests."""
    validate_k(through_repeats)
    config = planner_config(args)
    config_hash = run.digest(config)
    calls = []
    for item in items:
        if item.get("task") not in ACTIVE_TASKS:
            raise ValueError(f"P1R does not support task {item.get('task')!r}")
        if not isinstance(item.get("_task_rank"), int) or item["_task_rank"] < 1:
            raise ValueError(f"{item.get('item_id')}: missing positive _task_rank")
        base = _base_variant()
        request = run.request_params(item, PROTOCOL, base, args)
        prompt_hash = run.digest({
            "system": request["system"],
            "messages": request["messages"],
        })
        request_hash = run.digest(request)
        safe_task = re.sub(r"[^A-Za-z0-9]+", "-", item["task"]).strip("-").lower()
        for repeat_index in range(1, through_repeats + 1):
            identity = {
                "contract": run.RUN_CONTRACT,
                "task": item["task"],
                "task_rank": item["_task_rank"],
                "item_id": item["item_id"],
                "repeat_index": repeat_index,
                "protocol": PROTOCOL,
                "protocol_condition": PROTOCOL_CONDITION,
                "aggregation": AGGREGATION,
                "model": args.model,
                "config_hash": config_hash,
                "stage": "verdict",
                "c": None,
                "request_sha256": request_hash,
            }
            call_id = (
                f"call-p1r-{safe_task}-{item['_task_rank']:06d}-"
                f"r{repeat_index:03d}-{run.digest(identity)[:20]}"
            )
            if len(call_id) > 64:
                raise ValueError(f"provider custom_id would exceed 64 characters: {call_id}")
            variant = {
                **base,
                "index": repeat_index - 1,
                "repeat_index": repeat_index,
                "block_order": list(base["block_order"]),
            }
            calls.append({
                "schema_version": run.RUN_CONTRACT,
                "call_id": call_id,
                "task": item["task"],
                "item_id": item["item_id"],
                "case_number": item["case_number"],
                "split": item["split"],
                "task_rank": item["_task_rank"],
                "item_rank": item["_task_rank"],
                "repeat_index": repeat_index,
                "protocol": PROTOCOL,
                "protocol_condition": PROTOCOL_CONDITION,
                "aggregation": AGGREGATION,
                "model": args.model,
                "config_hash": config_hash,
                "prompt_sha256": prompt_hash,
                "request_sha256": request_hash,
                "stage": "verdict",
                "variant": variant,
                "allowed_answers": list(run.ANSWERS[item["task"]]),
                # Defensive copy: a caller cannot mutate one repeat only.
                "request": copy.deepcopy(request),
            })
    calls.sort(key=lambda row: (
        row["task_rank"], run.task_order_key(row["task"]),
        row["repeat_index"], row["call_id"],
    ))
    ids = [row["call_id"] for row in calls]
    if len(ids) != len(set(ids)):
        raise AssertionError("P1R call identity collision")
    _validate_rectangle(calls, through_repeats)
    return calls, config


def _validate_rectangle(calls: list[dict], k: int) -> None:
    by_item: dict[str, list[dict]] = {}
    for call in calls:
        by_item.setdefault(call["item_id"], []).append(call)
    wanted = list(range(1, k + 1))
    for item_id, rows in by_item.items():
        repeats = sorted(row["repeat_index"] for row in rows)
        if repeats != wanted:
            raise ValueError(f"{item_id}: P1R plan is not exactly repeats 1..{k}")
        if len({row["request_sha256"] for row in rows}) != 1:
            raise ValueError(f"{item_id}: P1R request hashes differ across repeats")
        if len({run.canonical_json(row["request"]) for row in rows}) != 1:
            raise ValueError(f"{item_id}: P1R requests are not byte-identical")


def _all_ranked_items(items_path: pathlib.Path, seed: str) -> dict[str, dict]:
    rows = run.load_ranked_items(items_path, [], [], 0, seed)
    return {row["item_id"]: row for row in rows}


def cumulative_items(items_path: pathlib.Path, tasks: list[str], splits: list[str],
                     through_items: int, seed: str,
                     existing_catalog: dict[str, dict]) -> list[dict]:
    """Union existing items with the requested N prefix, without shrinking."""
    all_items = _all_ranked_items(items_path, seed)
    existing_ids = {row["item_id"] for row in existing_catalog.values()}
    unknown = existing_ids - set(all_items)
    if unknown:
        raise ValueError("catalog item(s) absent from immutable item bank: "
                         + ", ".join(sorted(unknown)[:5]))
    selected_ids = {
        item_id for item_id, item in all_items.items()
        if item["task"] in tasks
        and (not splits or item["split"] in splits)
        and item["_task_rank"] <= through_items
    }
    chosen = [all_items[item_id] for item_id in existing_ids | selected_ids]
    chosen.sort(key=lambda row: (
        row["_task_rank"], run.task_order_key(row["task"]), row["item_id"],
    ))
    return chosen


def _manifest_path(run_dir: pathlib.Path) -> pathlib.Path:
    return run_dir / "manifest.json"


def _load_manifest(run_dir: pathlib.Path) -> dict | None:
    path = _manifest_path(run_dir)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: manifest must be an object")
    return value


def _read_catalog_strict(run_dir: pathlib.Path) -> dict[str, dict]:
    """Read the append-only catalogue, refusing even identical duplicates."""
    path = run_dir / "requests.jsonl"
    if not path.exists():
        return {}
    catalog = {}
    for row in run.read_jsonl(path):
        call_id = row.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError(f"{path}: request row without call_id")
        if call_id in catalog:
            raise ValueError(f"{path}: duplicate call_id {call_id}")
        catalog[call_id] = row
    return catalog


def _validate_existing(run_dir: pathlib.Path, items_path: pathlib.Path,
                       config: dict, target_k: int,
                       catalog: dict[str, dict]) -> dict | None:
    manifest = _load_manifest(run_dir)
    if manifest is None:
        if catalog:
            raise ValueError(f"{run_dir}: requests catalog exists without manifest")
        stray = [name for name in ("responses.jsonl", "ledger.jsonl", "scores.json")
                 if (run_dir / name).exists()]
        if stray:
            raise ValueError(f"{run_dir}: result files exist without a P1R manifest: "
                             + ", ".join(stray))
        return None
    config_hash = run.digest(config)
    immutable = {
        "contract": run.RUN_CONTRACT,
        "items_sha256": run.sha256(items_path),
        "config_hash": config_hash,
        "protocol": PROTOCOL,
        "protocol_condition": PROTOCOL_CONDITION,
        "aggregation": AGGREGATION,
    }
    for key, wanted in immutable.items():
        if manifest.get(key) != wanted:
            raise ValueError(
                f"{_manifest_path(run_dir)}: immutable {key} mismatch; use a new run directory"
            )
    if run.digest(manifest.get("config")) != manifest.get("config_hash"):
        raise ValueError(f"{_manifest_path(run_dir)}: config_hash does not bind config")
    expected_planner = {
        "name": PLANNER_NAME,
        "sha256": config["planner_sha256"],
        "request_builder": "bench/run.py",
        "request_builder_sha256": config["runner_sha256"],
    }
    if manifest.get("planner") != expected_planner:
        raise ValueError(f"{_manifest_path(run_dir)}: immutable planner provenance mismatch")
    old_k = int(manifest.get("through_repeats") or manifest.get("k") or 0)
    if old_k < 1 or manifest.get("k") != old_k:
        raise ValueError(f"{_manifest_path(run_dir)}: inconsistent P1R repeat horizon")
    if manifest.get("n_calls_planned") != len(catalog):
        raise ValueError(f"{_manifest_path(run_dir)}: catalogue/manifest call count mismatch")
    if manifest.get("n_items_planned") != len({row.get("item_id") for row in catalog.values()}):
        raise ValueError(f"{_manifest_path(run_dir)}: catalogue/manifest item count mismatch")
    _validate_rectangle(list(catalog.values()), old_k)
    catalog_k = max((int(row.get("repeat_index") or 0)
                     for row in catalog.values()), default=0)
    if target_k < max(old_k, catalog_k):
        raise ValueError(
            f"P1R horizon cannot shrink from K={max(old_k, catalog_k)} to K={target_k}"
        )
    return manifest


def _manifest_value(existing: dict | None, items_path: pathlib.Path, config: dict,
                    calls: list[dict], through_items: int, through_repeats: int,
                    tasks: list[str], splits: list[str]) -> dict:
    now = _utc_now()
    if existing is None:
        manifest = {
            "contract": run.RUN_CONTRACT,
            "created_utc": now,
            "items_path": str(items_path.resolve()),
            "items_sha256": run.sha256(items_path),
            "config": config,
            "config_hash": run.digest(config),
            "protocol": PROTOCOL,
            "protocol_condition": PROTOCOL_CONDITION,
            "aggregation": AGGREGATION,
            "model": config["model"],
            "python": sys.version.split()[0],
            "max_through_items_by_task": {},
            "tasks_filter_history": [],
            "splits_filter_history": [],
            "planner": {
                "name": PLANNER_NAME,
                "sha256": config["planner_sha256"],
                "request_builder": "bench/run.py",
                "request_builder_sha256": config["runner_sha256"],
            },
        }
    else:
        manifest = copy.deepcopy(existing)
    horizons = manifest.setdefault("max_through_items_by_task", {})
    for task in sorted({row["task"] for row in calls}, key=run.task_order_key):
        horizon = max(row["task_rank"] for row in calls if row["task"] == task)
        horizons[task] = max(int(horizons.get(task, 0)), int(horizon))
    history = manifest.setdefault("tasks_filter_history", [])
    if tasks not in history:
        history.append(list(tasks))
    split_history = manifest.setdefault("splits_filter_history", [])
    if splits not in split_history:
        split_history.append(list(splits))
    manifest.update({
        "through_items": max(horizons.values(), default=0),
        "requested_through_items": int(through_items),
        "through_repeats": int(through_repeats),
        "k": int(through_repeats),
        "n_items_planned": len({row["item_id"] for row in calls}),
        "n_calls_planned": len(calls),
        "n_items": len({row["item_id"] for row in calls}),
        "n_calls": len(calls),
        "tasks_filter": list(tasks),
        "splits_filter": list(splits),
        "seed": config["seed"],
        "max_tokens": config["max_tokens"],
        "thinking": config["thinking"],
        "rationale": False,
        "effort": config["effort"],
        "temperature": None,
        "temperatures": [],
        "provider": "offline-export",
        "updated_utc": now,
    })
    return manifest


def _write_manifest_atomic(run_dir: pathlib.Path, manifest: dict) -> None:
    path = _manifest_path(run_dir)
    temp = path.with_suffix(".json.tmp")
    try:
        with temp.open("x", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        temp.replace(path)
    except Exception:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def _canonical_export_row(call: dict) -> dict:
    row = {key: call[key] for key in (
        "schema_version", "call_id", "task", "item_id", "case_number",
        "split", "task_rank", "item_rank", "repeat_index", "protocol",
        "protocol_condition", "aggregation", "model", "config_hash",
        "prompt_sha256", "request_sha256", "stage", "request",
    )}
    row["custom_id"] = call["call_id"]
    return row


def export_batch(run_dir: pathlib.Path, output_path: pathlib.Path,
                 items_path: pathlib.Path, tasks: list[str], splits: list[str],
                 through_items: int, through_repeats: int,
                 args: SimpleNamespace, retry_ids_path: pathlib.Path | None = None) -> dict:
    """Append a cumulative P1R rectangle and export incomplete calls only."""
    validate_k(through_repeats)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite batch file {output_path}")
    if through_items < 1:
        raise ValueError("--through-items must be >= 1")
    run_dir.mkdir(parents=True, exist_ok=True)
    catalog = _read_catalog_strict(run_dir)
    items = cumulative_items(
        items_path, tasks, splits, through_items, str(args.seed), catalog,
    )
    if not items:
        raise ValueError("no items matched the requested task/split prefix")
    calls, config = build_call_plan(items, through_repeats, args)
    existing_manifest = _validate_existing(
        run_dir, items_path, config, through_repeats, catalog,
    )
    planned = {row["call_id"]: row for row in calls}
    unknown_old = set(catalog) - set(planned)
    if unknown_old:
        raise ValueError("existing catalog is not a subset of the cumulative plan: "
                         + ", ".join(sorted(unknown_old)[:5]))
    for call_id, old in catalog.items():
        if run.canonical_json(old) != run.canonical_json(planned[call_id]):
            raise ValueError(f"request identity {call_id} changed")
    allowed = run.read_retry_ids(retry_ids_path) if retry_ids_path else None
    if allowed is not None:
        unknown = allowed - set(planned)
        if unknown:
            raise ValueError("retry list contains IDs outside the P1R plan: "
                             + ", ".join(sorted(unknown)[:5]))
    completed = run.read_completed(run_dir)
    missing = [row for row in calls
               if row["call_id"] not in completed
               and (allowed is None or row["call_id"] in allowed)]
    manifest = _manifest_value(
        existing_manifest, items_path, config, calls, through_items,
        through_repeats, tasks, splits,
    )
    # Catalog is append-only. A crash between catalog and manifest writes is
    # recoverable by rerunning the same command; no completed receipt is lost.
    run.persist_call_catalog(run_dir, calls)
    _write_manifest_atomic(run_dir, manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as fh:
        for call in missing:
            fh.write(json.dumps(_canonical_export_row(call), ensure_ascii=False,
                                sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {
        "planned": len(calls),
        "completed": len(set(completed) & set(planned)),
        "exported": len(missing),
        "items": len({row["item_id"] for row in calls}),
        "k": through_repeats,
        "config_hash": run.digest(config),
        "path": str(output_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline planner for repeated stated-probability P1 calls")
    parser.add_argument("--items", type=pathlib.Path, default=DEFAULT_ITEMS)
    parser.add_argument("--tasks", default=",".join(ACTIVE_TASKS))
    parser.add_argument("--splits", default="")
    parser.add_argument("--through-items", type=int, default=None)
    parser.add_argument("--through-repeats", type=int, default=None)
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--thinking", choices=("unset", "adaptive", "disabled"),
                        default="unset")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--seed", default="pmcpa-bench")
    parser.add_argument("--run-dir", type=pathlib.Path)
    parser.add_argument("--export-batch", type=pathlib.Path)
    parser.add_argument("--retry-ids", type=pathlib.Path)
    parser.add_argument("--import-results", type=pathlib.Path)
    parser.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        raise SystemExit("p1r_plan.py makes no provider calls; export canonical JSONL")
    if args.import_results:
        if not args.run_dir:
            raise SystemExit("--import-results requires --run-dir")
        try:
            result = run.import_results(args.run_dir, args.import_results)
        except (ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"imported: completed={result['completed']} failed={result['failed']} "
              f"duplicates={result['duplicate']} remaining={result['missing_after_import']}")
        return 0
    if not args.model:
        raise SystemExit("planning/export requires an explicit --model identity")
    if args.through_items is None or args.through_repeats is None:
        raise SystemExit("planning/export requires --through-items and --through-repeats")
    tasks = [value.strip() for value in args.tasks.split(",") if value.strip()]
    splits = [value.strip() for value in args.splits.split(",") if value.strip()]
    unknown_tasks = set(tasks) - set(ACTIVE_TASKS)
    if unknown_tasks:
        raise SystemExit("P1R supports only active T1/T2/T3: "
                         + ", ".join(sorted(unknown_tasks)))
    if not tasks:
        raise SystemExit("--tasks selected no tasks")
    try:
        validate_k(args.through_repeats)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.export_batch:
        if not args.run_dir:
            raise SystemExit("--export-batch requires --run-dir")
        try:
            result = export_batch(
                args.run_dir, args.export_batch, args.items, tasks, splits,
                args.through_items, args.through_repeats, args, args.retry_ids,
            )
        except (ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"planned={result['planned']} completed={result['completed']} "
              f"exported={result['exported']} items={result['items']} K={result['k']}")
        print(f"config={result['config_hash']} -> {result['path']}")
        return 0
    if args.run_dir:
        raise SystemExit("--run-dir without --export-batch/--import-results would be ambiguous")
    ranked = run.load_ranked_items(
        args.items, tasks, splits, args.through_items, args.seed)
    calls, config = build_call_plan(ranked, args.through_repeats, args)
    print(f"DRY RUN: {len(calls)} calls over {len(ranked)} items, K={args.through_repeats}")
    print(f"protocol={PROTOCOL} condition={PROTOCOL_CONDITION} aggregation={AGGREGATION}")
    print(f"model={args.model} config={run.digest(config)}")
    print("No network call was made and nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
