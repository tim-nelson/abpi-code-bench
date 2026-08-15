"""Native P3 planner and auditable paid-receipt migration.

P3 repeats the exact P1 stated-confidence request K times and linearly pools
the oriented probabilities.  This planner never calls a provider.  The
``migrate-receipts`` command can seed a fresh native-P3 run from the frozen
P1+``repeated_stated_probability`` source run without copying or rewriting its
provider bodies.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from types import SimpleNamespace

try:
    import p1r_plan as legacy_plan
    import run
except ModuleNotFoundError:
    from bench import p1r_plan as legacy_plan
    from bench import run


BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
PROTOCOL = "P3"
AGGREGATION = "linear_probability_pool"
REQUEST_TEMPLATE_PROTOCOL = "P1"
PLANNER_NAME = "bench/p3_plan.py"
ACTIVE_TASKS = ("T1", "T2", "T3")
ALIAS_SCHEMA = "pmcpa.receipt-alias.v1"
ALIAS_MIGRATION_ID = "pmcpa.receipt-alias.p1r-to-p3.v1"
ALIAS_REGISTRY = "receipt_aliases.jsonl"
CONFIG_EQUIVALENCE_FIELDS = (
    "contract", "runner_sha256", "model", "max_tokens", "thinking",
    "rationale", "effort", "temperature", "seed",
)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def planner_sha256() -> str:
    return run.sha256(pathlib.Path(__file__).resolve())


def planner_config(args: SimpleNamespace) -> dict:
    """Immutable native-P3 request condition; K stays outside the hash."""
    return {
        "contract": run.RUN_CONTRACT,
        "runner_sha256": run.sha256(pathlib.Path(run.__file__).resolve()),
        "planner_sha256": planner_sha256(),
        "protocol": PROTOCOL,
        "aggregation": AGGREGATION,
        "request_template_protocol": REQUEST_TEMPLATE_PROTOCOL,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "rationale": False,
        "effort": args.effort or None,
        "temperature": None,
        "seed": str(args.seed),
    }


def validate_k(k: int) -> None:
    if k < 1 or k % 2 == 0:
        raise ValueError("P3 requires a positive odd --through-repeats K")


def build_call_plan(items: list[dict], through_repeats: int,
                    args: SimpleNamespace) -> tuple[list[dict], dict]:
    """Stable native-P3 1..K rectangle using the unchanged P1 prompt body."""
    validate_k(through_repeats)
    config = planner_config(args)
    config_hash = run.digest(config)
    calls = []
    base = {
        "index": 0, "rendition": 0,
        "block_order": run.BLOCK_ORDERS[0], "temperature": None,
    }
    for item in items:
        if item.get("task") not in ACTIVE_TASKS:
            raise ValueError(f"P3 does not support task {item.get('task')!r}")
        if not isinstance(item.get("_task_rank"), int) or item["_task_rank"] < 1:
            raise ValueError(f"{item.get('item_id')}: missing positive _task_rank")
        request = run.request_params(item, REQUEST_TEMPLATE_PROTOCOL, base, args)
        prompt_hash = run.digest({
            "system": request["system"], "messages": request["messages"],
        })
        request_hash = run.digest(request)
        safe_task = re.sub(r"[^A-Za-z0-9]+", "-", item["task"]).strip("-").lower()
        for repeat_index in range(1, through_repeats + 1):
            identity = {
                "contract": run.RUN_CONTRACT,
                "task": item["task"], "task_rank": item["_task_rank"],
                "item_id": item["item_id"], "repeat_index": repeat_index,
                "protocol": PROTOCOL, "aggregation": AGGREGATION,
                "model": args.model, "config_hash": config_hash,
                "stage": "verdict", "c": None,
                "request_sha256": request_hash,
            }
            call_id = (
                f"call-p3-{safe_task}-{item['_task_rank']:06d}-"
                f"r{repeat_index:03d}-{run.digest(identity)[:20]}"
            )
            if len(call_id) > 64:
                raise ValueError(f"provider custom_id exceeds 64 characters: {call_id}")
            variant = {
                **base, "index": repeat_index - 1,
                "repeat_index": repeat_index,
                "block_order": list(base["block_order"]),
            }
            calls.append({
                "schema_version": run.RUN_CONTRACT,
                "call_id": call_id,
                "task": item["task"], "item_id": item["item_id"],
                "case_number": item["case_number"], "split": item["split"],
                "task_rank": item["_task_rank"], "item_rank": item["_task_rank"],
                "repeat_index": repeat_index, "protocol": PROTOCOL,
                "aggregation": AGGREGATION, "model": args.model,
                "config_hash": config_hash, "prompt_sha256": prompt_hash,
                "request_sha256": request_hash, "stage": "verdict",
                "variant": variant,
                "allowed_answers": list(run.ANSWERS[item["task"]]),
                "request": copy.deepcopy(request),
            })
    calls.sort(key=lambda row: (
        row["task_rank"], run.task_order_key(row["task"]),
        row["repeat_index"], row["call_id"],
    ))
    ids = [row["call_id"] for row in calls]
    if len(ids) != len(set(ids)):
        raise AssertionError("P3 call identity collision")
    by_item = {}
    for call in calls:
        by_item.setdefault(call["item_id"], []).append(call)
    for item_id, rows in by_item.items():
        if sorted(row["repeat_index"] for row in rows) != list(
                range(1, through_repeats + 1)):
            raise ValueError(f"{item_id}: P3 plan is not exactly repeats 1..{through_repeats}")
        if len({run.canonical_json(row["request"]) for row in rows}) != 1:
            raise ValueError(f"{item_id}: P3 requests are not byte-identical")
    return calls, config


def _manifest_value(existing: dict | None, items_path: pathlib.Path, config: dict,
                    calls: list[dict], through_items: int, k: int,
                    tasks: list[str], splits: list[str]) -> dict:
    now = _utc_now()
    if existing is None:
        manifest = {
            "contract": run.RUN_CONTRACT,
            "created_utc": now,
            "items_path": str(items_path.resolve()),
            "items_sha256": run.sha256(items_path),
            "config": config, "config_hash": run.digest(config),
            "protocol": PROTOCOL, "aggregation": AGGREGATION,
            "model": config["model"], "python": sys.version.split()[0],
            "max_through_items_by_task": {},
            "tasks_filter_history": [], "splits_filter_history": [],
            "planner": {
                "name": PLANNER_NAME, "sha256": config["planner_sha256"],
                "request_builder": "bench/run.py",
                "request_builder_sha256": config["runner_sha256"],
                "request_template_protocol": REQUEST_TEMPLATE_PROTOCOL,
            },
        }
    else:
        manifest = copy.deepcopy(existing)
    horizons = manifest.setdefault("max_through_items_by_task", {})
    for task in sorted({row["task"] for row in calls}, key=run.task_order_key):
        horizon = max(row["task_rank"] for row in calls if row["task"] == task)
        horizons[task] = max(int(horizons.get(task, 0)), int(horizon))
    for key, value in (("tasks_filter_history", list(tasks)),
                       ("splits_filter_history", list(splits))):
        history = manifest.setdefault(key, [])
        if value not in history:
            history.append(value)
    manifest.update({
        "through_items": max(horizons.values(), default=0),
        "requested_through_items": int(through_items),
        "through_repeats": int(k), "k": int(k),
        "n_items_planned": len({row["item_id"] for row in calls}),
        "n_calls_planned": len(calls),
        "n_items": len({row["item_id"] for row in calls}), "n_calls": len(calls),
        "tasks_filter": list(tasks), "splits_filter": list(splits),
        "seed": config["seed"], "max_tokens": config["max_tokens"],
        "thinking": config["thinking"], "rationale": False,
        "effort": config["effort"], "temperature": None, "temperatures": [],
        "provider": "offline-export", "updated_utc": now,
    })
    return manifest


def _validate_existing(run_dir: pathlib.Path, items_path: pathlib.Path,
                       config: dict, target_k: int, catalog: dict) -> dict | None:
    path = run_dir / "manifest.json"
    if not path.exists():
        if catalog:
            raise ValueError(f"{run_dir}: requests catalog exists without manifest")
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": run.RUN_CONTRACT, "items_sha256": run.sha256(items_path),
        "config_hash": run.digest(config), "protocol": PROTOCOL,
        "aggregation": AGGREGATION,
    }
    for key, wanted in expected.items():
        if manifest.get(key) != wanted:
            raise ValueError(f"{path}: immutable {key} mismatch; use a new run directory")
    if run.digest(manifest.get("config")) != manifest.get("config_hash"):
        raise ValueError(f"{path}: config_hash does not bind config")
    old_k = max(int(manifest.get("through_repeats") or 0),
                max((int(row.get("repeat_index") or 0)
                     for row in catalog.values()), default=0))
    if target_k < old_k:
        raise ValueError(f"P3 horizon cannot shrink from K={old_k} to K={target_k}")
    return manifest


def _canonical_export_row(call: dict) -> dict:
    row = {key: call[key] for key in (
        "schema_version", "call_id", "task", "item_id", "case_number",
        "split", "task_rank", "item_rank", "repeat_index", "protocol",
        "aggregation", "model", "config_hash", "prompt_sha256",
        "request_sha256", "stage", "request",
    )}
    row["custom_id"] = call["call_id"]
    return row


def export_batch(run_dir: pathlib.Path, output_path: pathlib.Path,
                 items_path: pathlib.Path, tasks: list[str], splits: list[str],
                 through_items: int, through_repeats: int,
                 args: SimpleNamespace, retry_ids_path: pathlib.Path | None = None) -> dict:
    """Create/extend a native P3 catalog and export incomplete calls only."""
    validate_k(through_repeats)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite batch file {output_path}")
    if through_items < 1:
        raise ValueError("--through-items must be >= 1")
    run_dir.mkdir(parents=True, exist_ok=True)
    catalog = run.read_call_catalog(run_dir)
    items = legacy_plan.cumulative_items(
        items_path, tasks, splits, through_items, str(args.seed), catalog)
    if not items:
        raise ValueError("no items matched the requested task/split prefix")
    calls, config = build_call_plan(items, through_repeats, args)
    existing = _validate_existing(run_dir, items_path, config, through_repeats, catalog)
    planned = {row["call_id"]: row for row in calls}
    if set(catalog) - set(planned):
        raise ValueError("existing catalog is not a subset of the native P3 plan")
    for call_id, old in catalog.items():
        if run.canonical_json(old) != run.canonical_json(planned[call_id]):
            raise ValueError(f"request identity {call_id} changed")
    allowed = run.read_retry_ids(retry_ids_path) if retry_ids_path else None
    if allowed is not None and allowed - set(planned):
        raise ValueError("retry list contains IDs outside the native P3 plan")
    completed = run.read_completed(run_dir)
    missing = [row for row in calls if row["call_id"] not in completed
               and (allowed is None or row["call_id"] in allowed)]
    run.persist_call_catalog(run_dir, calls)
    manifest = _manifest_value(existing, items_path, config, calls,
                               through_items, through_repeats, tasks, splits)
    legacy_plan._write_manifest_atomic(run_dir, manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as fh:
        for call in missing:
            fh.write(json.dumps(_canonical_export_row(call), ensure_ascii=False,
                                sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"planned": len(calls), "completed": len(set(completed) & set(planned)),
            "exported": len(missing), "items": len({row["item_id"] for row in calls}),
            "k": through_repeats, "config_hash": run.digest(config),
            "path": str(output_path)}


def _strict_parsed(result: dict, call: dict) -> dict:
    parsed = result.get("parsed", result.get("output"))
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if not isinstance(parsed, dict):
        raise ValueError(f"{call['call_id']}: completed result needs parsed object")
    answer = parsed.get("answer")
    probability = parsed.get("probability")
    if answer not in call["allowed_answers"]:
        raise ValueError(f"{call['call_id']}: invalid answer {answer!r}")
    if (not isinstance(probability, (int, float)) or isinstance(probability, bool)
            or not 0 <= float(probability) <= 1):
        raise ValueError(f"{call['call_id']}: invalid probability {probability!r}")
    return {"answer": answer, "probability": float(probability)}


def import_results(run_dir: pathlib.Path, results_path: pathlib.Path) -> dict:
    """Native-P3 equivalent of run.import_results, retaining probabilities."""
    catalog = run.read_call_catalog(run_dir)
    results = run.read_jsonl(results_path)
    completed = run.read_completed(run_dir)
    seen, actions = set(), []
    for result in results:
        call_id = result.get("call_id") or result.get("custom_id")
        if not call_id or call_id in seen or call_id not in catalog:
            raise ValueError(f"{results_path}: invalid/duplicate/unknown call_id {call_id!r}")
        seen.add(call_id)
        call = catalog[call_id]
        error = result.get("error")
        parsed = None if error else _strict_parsed(result, call)
        old = completed.get(call_id)
        if old is not None:
            if parsed is not None and run.canonical_json(old.get("parsed")) != run.canonical_json(parsed):
                raise ValueError(f"{call_id}: completed call cannot be overwritten")
            actions.append(("duplicate", call, result, old.get("parsed")))
        else:
            actions.append(("failed" if error else "completed", call, result, parsed))
    now = _utc_now()
    response_rows, ledger_rows = [], []
    counts = {"completed": 0, "failed": 0, "duplicate": 0}
    for status, call, result, parsed in actions:
        counts[status] += 1
        if status == "duplicate":
            continue
        event = {
            "schema_version": run.RUN_CONTRACT, "call_id": call["call_id"],
            "status": status, "task": call["task"], "item_id": call["item_id"],
            "task_rank": call["task_rank"], "repeat_index": call["repeat_index"],
            "protocol": PROTOCOL, "aggregation": AGGREGATION,
            "model": call["model"], "config_hash": call["config_hash"],
            "imported_utc": now, "error": result.get("error"),
            "response": result.get("response") if status == "failed" else None,
            "stop_reason": result.get("stop_reason"),
            "requested_utc": result.get("requested_utc"),
        }
        if status == "completed":
            event["parsed"] = parsed
            response_rows.append({
                "schema_version": run.RUN_CONTRACT, "call_id": call["call_id"],
                "item_id": call["item_id"], "task": call["task"],
                "case_number": call["case_number"], "protocol": PROTOCOL,
                "aggregation": AGGREGATION, "model": call["model"],
                "config_hash": call["config_hash"], "task_rank": call["task_rank"],
                "item_rank": call["item_rank"], "repeat_index": call["repeat_index"],
                "variant": call["variant"], "request": call["request"],
                "response": result.get("response"), "parsed": parsed, "error": None,
                "stop_reason": result.get("stop_reason"),
                "requested_utc": result.get("requested_utc"), "imported_utc": now,
            })
        ledger_rows.append(event)
    run.append_jsonl(run_dir / "responses.jsonl", response_rows)
    run.append_jsonl(run_dir / "ledger.jsonl", ledger_rows)
    counts["missing_after_import"] = len(set(catalog) - set(run.read_completed(run_dir)))
    return counts


def _raw_jsonl_index(path: pathlib.Path) -> dict[str, tuple[dict, str]]:
    out = {}
    with path.open("rb") as fh:
        for raw in fh:
            row = json.loads(raw)
            call_id = row.get("call_id")
            if not call_id or call_id in out:
                raise ValueError(f"{path}: missing/duplicate call_id {call_id!r}")
            out[call_id] = row, hashlib.sha256(raw).hexdigest()
    return out


def migrate_receipts(target_run: pathlib.Path, source_run: pathlib.Path) -> dict:
    """Seed equivalent native-P3 calls with references to frozen P1R receipts."""
    target_manifest = json.loads((target_run / "manifest.json").read_text())
    source_manifest = json.loads((source_run / "manifest.json").read_text())
    if (target_manifest.get("protocol"), target_manifest.get("aggregation")) != (
            PROTOCOL, AGGREGATION):
        raise ValueError("target is not a native P3 linear-pool run")
    if (source_manifest.get("protocol"), source_manifest.get("protocol_condition"),
            source_manifest.get("aggregation")) != (
            "P1", legacy_plan.PROTOCOL_CONDITION, AGGREGATION):
        raise ValueError("source is not the frozen P1 repeated-probability condition")
    target_config, source_config = target_manifest["config"], source_manifest["config"]
    mismatches = [key for key in CONFIG_EQUIVALENCE_FIELDS
                  if target_config.get(key) != source_config.get(key)]
    if mismatches:
        raise ValueError("source/target model-request config differs: " + ", ".join(mismatches))

    target_catalog = run.read_call_catalog(target_run)
    source_requests = _raw_jsonl_index(source_run / "requests.jsonl")
    source_responses = _raw_jsonl_index(source_run / "responses.jsonl")
    source_ledger = _raw_jsonl_index(source_run / "ledger.jsonl")
    target_by_key = {
        (row["item_id"], int(row["task_rank"]), int(row["repeat_index"])): row
        for row in target_catalog.values()
    }
    aliases = []
    for source_call_id, (source_call, source_request_row_hash) in sorted(
            source_requests.items(), key=lambda value: (
                value[1][0]["task_rank"], run.task_order_key(value[1][0]["task"]),
                value[1][0]["repeat_index"])):
        source_response = source_responses.get(source_call_id)
        source_event = source_ledger.get(source_call_id)
        if source_response is None or source_event is None:
            raise ValueError(f"source call {source_call_id} lacks response/ledger receipt")
        response, response_row_hash = source_response
        event, ledger_row_hash = source_event
        if event.get("status") != "completed" or response.get("parsed") != event.get("parsed"):
            raise ValueError(f"source call {source_call_id} is not one coherent completion")
        key = (source_call["item_id"], int(source_call["task_rank"]),
               int(source_call["repeat_index"]))
        target = target_by_key.get(key)
        if target is None:
            raise ValueError(f"target plan has no equivalent P3 call for {key}")
        for field in ("task", "item_id", "case_number", "split", "task_rank",
                      "item_rank", "repeat_index", "model", "prompt_sha256",
                      "request_sha256", "allowed_answers"):
            if target.get(field) != source_call.get(field):
                raise ValueError(f"{source_call_id}: source/target {field} differs")
        if run.canonical_json(target["request"]) != run.canonical_json(source_call["request"]):
            raise ValueError(f"{source_call_id}: source/target provider body differs")
        parsed = _strict_parsed(response, target)
        aliases.append({
            "schema_version": ALIAS_SCHEMA,
            "migration_id": ALIAS_MIGRATION_ID,
            "target_run_id": target_run.name,
            "target_call_id": target["call_id"],
            "source_run_id": source_run.name,
            "source_call_id": source_call_id,
            "source_request_row_sha256": source_request_row_hash,
            "source_response_row_sha256": response_row_hash,
            "source_ledger_row_sha256": ledger_row_hash,
            "request_sha256": target["request_sha256"],
            "prompt_sha256": target["prompt_sha256"],
            "parsed_sha256": run.digest(parsed),
            "config_equivalence_fields": list(CONFIG_EQUIVALENCE_FIELDS),
        })

    registry = target_run / ALIAS_REGISTRY
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in aliases)
    if registry.exists():
        if registry.read_text(encoding="utf-8") != payload:
            raise ValueError(f"{registry}: existing alias registry differs")
    else:
        with registry.open("x", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())

    completed = run.read_completed(target_run)
    now = _utc_now()
    response_rows, ledger_rows, duplicates = [], [], 0
    source_response_values = {call_id: row for call_id, (row, _) in source_responses.items()}
    target_by_id = target_catalog
    for alias in aliases:
        target = target_by_id[alias["target_call_id"]]
        parsed = _strict_parsed(
            source_response_values[alias["source_call_id"]], target)
        old = completed.get(target["call_id"])
        if old is not None:
            if (run.canonical_json(old.get("parsed")) != run.canonical_json(parsed)
                    or old.get("receipt_alias") != alias):
                raise ValueError(f"{target['call_id']}: existing completion is not this alias")
            duplicates += 1
            continue
        response_rows.append({
            "schema_version": run.RUN_CONTRACT, "call_id": target["call_id"],
            "item_id": target["item_id"], "task": target["task"],
            "case_number": target["case_number"], "protocol": PROTOCOL,
            "aggregation": AGGREGATION, "model": target["model"],
            "config_hash": target["config_hash"], "task_rank": target["task_rank"],
            "item_rank": target["item_rank"], "repeat_index": target["repeat_index"],
            "variant": target["variant"], "request": target["request"],
            "response": None, "parsed": parsed, "error": None,
            "receipt_alias": alias, "imported_utc": now,
        })
        ledger_rows.append({
            "schema_version": run.RUN_CONTRACT, "call_id": target["call_id"],
            "status": "completed", "task": target["task"],
            "item_id": target["item_id"], "task_rank": target["task_rank"],
            "repeat_index": target["repeat_index"], "protocol": PROTOCOL,
            "aggregation": AGGREGATION, "model": target["model"],
            "config_hash": target["config_hash"], "imported_utc": now,
            "parsed": parsed, "error": None, "response": None,
            "receipt_alias": alias,
        })
    run.append_jsonl(target_run / "responses.jsonl", response_rows)
    run.append_jsonl(target_run / "ledger.jsonl", ledger_rows)
    manifest = json.loads((target_run / "manifest.json").read_text())
    migration = {
        "schema_version": ALIAS_SCHEMA, "migration_id": ALIAS_MIGRATION_ID,
        "source_run_id": source_run.name, "n_aliases": len(aliases),
        "alias_registry": ALIAS_REGISTRY,
        "alias_registry_sha256": run.sha256(registry),
        "source_files": {name: run.sha256(source_run / name) for name in (
            "manifest.json", "requests.jsonl", "responses.jsonl", "ledger.jsonl")},
        "provider_bodies_copied": False,
    }
    existing_migrations = manifest.setdefault("receipt_migrations", [])
    if existing_migrations and migration not in existing_migrations:
        raise ValueError("target manifest already records a different receipt migration")
    if not existing_migrations:
        existing_migrations.append(migration)
        legacy_plan._write_manifest_atomic(target_run, manifest)
    return {"aliased": len(response_rows), "duplicates": duplicates,
            "total_aliases": len(aliases),
            "missing_after_migration": len(set(target_catalog) - set(run.read_completed(target_run))),
            "registry": str(registry)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=pathlib.Path, default=DEFAULT_ITEMS)
    parser.add_argument("--tasks", default=",".join(ACTIVE_TASKS))
    parser.add_argument("--splits", default="")
    parser.add_argument("--through-items", type=int)
    parser.add_argument("--through-repeats", type=int)
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--thinking", choices=("unset", "adaptive", "disabled"), default="unset")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--seed", default="pmcpa-bench")
    parser.add_argument("--run-dir", type=pathlib.Path)
    parser.add_argument("--export-batch", type=pathlib.Path)
    parser.add_argument("--import-results", type=pathlib.Path)
    parser.add_argument("--migrate-receipts", type=pathlib.Path,
                        help="frozen P1R source run to alias into --run-dir")
    parser.add_argument("--retry-ids", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.migrate_receipts:
        if not args.run_dir:
            raise SystemExit("--migrate-receipts requires --run-dir")
        result = migrate_receipts(args.run_dir, args.migrate_receipts)
        print(f"aliased={result['aliased']} duplicates={result['duplicates']} "
              f"total={result['total_aliases']} missing={result['missing_after_migration']}")
        print(f"registry={result['registry']}")
        return 0
    if args.import_results:
        if not args.run_dir:
            raise SystemExit("--import-results requires --run-dir")
        result = import_results(args.run_dir, args.import_results)
        print(f"imported: completed={result['completed']} failed={result['failed']} "
              f"duplicates={result['duplicate']} remaining={result['missing_after_import']}")
        return 0
    if not args.model or args.through_items is None or args.through_repeats is None:
        raise SystemExit("planning requires --model, --through-items and --through-repeats")
    tasks = [value.strip() for value in args.tasks.split(",") if value.strip()]
    splits = [value.strip() for value in args.splits.split(",") if value.strip()]
    if set(tasks) - set(ACTIVE_TASKS) or not tasks:
        raise SystemExit("P3 supports only active T1/T2/T3")
    if args.export_batch:
        if not args.run_dir:
            raise SystemExit("--export-batch requires --run-dir")
        result = export_batch(args.run_dir, args.export_batch, args.items, tasks,
                              splits, args.through_items, args.through_repeats,
                              args, args.retry_ids)
        print(f"planned={result['planned']} completed={result['completed']} "
              f"exported={result['exported']} items={result['items']} K={result['k']}")
        print(f"config={result['config_hash']} -> {result['path']}")
        return 0
    ranked = run.load_ranked_items(args.items, tasks, splits,
                                   args.through_items, args.seed)
    calls, config = build_call_plan(ranked, args.through_repeats, args)
    print(f"DRY RUN: {len(calls)} native P3 calls over {len(ranked)} items")
    print(f"aggregation={AGGREGATION} config={run.digest(config)}")
    print("No network call was made and nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
