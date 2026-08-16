"""P1 presentation-order robustness probe planner (offline; no provider calls).

Serves the exact P1 request with one change: the block order is the second
permutation (clause, extract, metadata) instead of the canonical first
(clause, metadata, extract). Everything else — instruction, schema, items,
model condition — is byte-identical to the registered P1 arm, so per-item
comparison against it isolates presentation order. This is a robustness
probe: its run is never registered; the comparison is offline.

Mirrors p4_plan.py's receipts discipline; never edits frozen run.py.
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

try:
    import p1r_plan as legacy_plan
    import run
except ModuleNotFoundError:
    from bench import p1r_plan as legacy_plan
    from bench import run

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
PROTOCOL = "P1"
PROTOCOL_CONDITION = "alternate_block_order"
PLANNER_NAME = "bench/p1_order_plan.py"
ACTIVE_TASKS = ("T1", "T2", "T3")
ALT_ORDER = run.BLOCK_ORDERS[1]  # (clause, extract, metadata)


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def planner_config(args):
    return {
        "contract": run.RUN_CONTRACT,
        "runner_sha256": run.sha256(pathlib.Path(run.__file__).resolve()),
        "planner_sha256": run.sha256(pathlib.Path(__file__).resolve()),
        "protocol": PROTOCOL, "protocol_condition": PROTOCOL_CONDITION,
        "block_order": list(ALT_ORDER),
        "model": args.model, "max_tokens": args.max_tokens,
        "thinking": args.thinking, "rationale": False,
        "effort": args.effort or None, "temperature": None,
        "seed": str(args.seed),
    }


def build_call_plan(items, args):
    config = planner_config(args)
    config_hash = run.digest(config)
    variant = {"index": 0, "rendition": 0,
               "block_order": ALT_ORDER, "temperature": None}
    calls = []
    for item in items:
        if item.get("task") not in ACTIVE_TASKS:
            raise ValueError(f"unsupported task {item.get('task')!r}")
        request = run.request_params(item, PROTOCOL, variant, args)
        canonical_variant = {**variant, "block_order": run.BLOCK_ORDERS[0]}
        canonical = run.request_params(item, PROTOCOL, canonical_variant, args)
        if request["messages"] == canonical["messages"]:
            raise AssertionError(
                f"{item['item_id']}: alternate order rendered identically to "
                "canonical; probe would measure nothing")
        if request["system"] != canonical["system"]:
            raise AssertionError(f"{item['item_id']}: system drifted with order")
        safe_task = re.sub(r"[^A-Za-z0-9]+", "-", item["task"]).strip("-").lower()
        identity = {
            "contract": run.RUN_CONTRACT, "task": item["task"],
            "task_rank": item["_task_rank"], "item_id": item["item_id"],
            "protocol": PROTOCOL, "protocol_condition": PROTOCOL_CONDITION,
            "model": args.model, "config_hash": config_hash,
            "request_sha256": run.digest(request),
        }
        call_id = (f"call-p1o-{safe_task}-{item['_task_rank']:06d}-"
                   f"{run.digest(identity)[:20]}")
        calls.append({
            "schema_version": run.RUN_CONTRACT, "call_id": call_id,
            "task": item["task"], "item_id": item["item_id"],
            "case_number": item["case_number"], "split": item["split"],
            "task_rank": item["_task_rank"], "item_rank": item["_task_rank"],
            "repeat_index": 1, "protocol": PROTOCOL,
            "model": args.model, "config_hash": config_hash,
            "prompt_sha256": run.digest({"system": request["system"],
                                         "messages": request["messages"]}),
            "request_sha256": run.digest(request), "stage": "verdict",
            "variant": {**variant, "block_order": list(ALT_ORDER)},
            "allowed_answers": list(run.ANSWERS[item["task"]]),
            "request": copy.deepcopy(request),
        })
    calls.sort(key=lambda r: (run.task_order_key(r["task"]), r["task_rank"], r["call_id"]))
    if len({r["call_id"] for r in calls}) != len(calls):
        raise AssertionError("call identity collision")
    return calls, config


def _canonical_export_row(call):
    row = {key: call[key] for key in (
        "schema_version", "call_id", "task", "item_id", "case_number", "split",
        "task_rank", "item_rank", "repeat_index", "protocol", "model",
        "config_hash", "prompt_sha256", "request_sha256", "stage", "request")}
    row["custom_id"] = call["call_id"]
    return row


def export_batch(run_dir, output_path, items_path, tasks, splits,
                 through_items, args):
    if pathlib.Path(output_path).exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    run_dir = pathlib.Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    catalog = run.read_call_catalog(run_dir)
    items = legacy_plan.cumulative_items(
        items_path, tasks, splits, through_items, str(args.seed), catalog)
    calls, config = build_call_plan(items, args)
    planned = {r["call_id"]: r for r in calls}
    if set(catalog) - set(planned):
        raise ValueError("existing catalog is not a subset of the plan")
    for call_id, old in catalog.items():
        if run.canonical_json(old) != run.canonical_json(planned[call_id]):
            raise ValueError(f"request identity {call_id} changed")
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("config_hash") != run.digest(config):
            raise ValueError(f"{manifest_path}: immutable config_hash mismatch")
    completed = run.read_completed(run_dir)
    missing = [r for r in calls if r["call_id"] not in completed]
    run.persist_call_catalog(run_dir, calls)
    manifest = {
        "contract": run.RUN_CONTRACT, "created_utc": _utc_now(),
        "items_path": str(pathlib.Path(items_path).resolve()),
        "items_sha256": run.sha256(items_path),
        "config": config, "config_hash": run.digest(config),
        "protocol": PROTOCOL, "protocol_condition": PROTOCOL_CONDITION,
        "model": config["model"], "n_calls": len(calls),
        "n_items": len({r["item_id"] for r in calls}),
        "tasks_filter": list(tasks), "splits_filter": list(splits),
        "through_items": int(through_items),
        "planner": {"name": PLANNER_NAME, "sha256": config["planner_sha256"],
                    "request_builder": "bench/run.py",
                    "request_builder_sha256": config["runner_sha256"]},
        "provider": "offline-export", "updated_utc": _utc_now(),
    }
    legacy_plan._write_manifest_atomic(run_dir, manifest)
    with pathlib.Path(output_path).open("x", encoding="utf-8") as fh:
        for call in missing:
            fh.write(json.dumps(_canonical_export_row(call), ensure_ascii=False,
                                sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"planned": len(calls), "completed": len(set(completed) & set(planned)),
            "exported": len(missing)}


def _strict_parsed(result, call):
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


def import_results(run_dir, results_path):
    run_dir = pathlib.Path(run_dir)
    catalog = run.read_call_catalog(run_dir)
    completed = run.read_completed(run_dir)
    seen, response_rows, ledger_rows = set(), [], []
    counts = {"completed": 0, "failed": 0, "duplicate": 0}
    now = _utc_now()
    for result in run.read_jsonl(results_path):
        call_id = result.get("call_id") or result.get("custom_id")
        if not call_id or call_id in seen or call_id not in catalog:
            raise ValueError(f"invalid/duplicate/unknown call_id {call_id!r}")
        seen.add(call_id)
        call = catalog[call_id]
        error = result.get("error")
        parsed = None if error else _strict_parsed(result, call)
        if call_id in completed:
            counts["duplicate"] += 1
            continue
        status = "failed" if error else "completed"
        counts[status] += 1
        event = {"schema_version": run.RUN_CONTRACT, "call_id": call_id,
                 "status": status, "task": call["task"], "item_id": call["item_id"],
                 "task_rank": call["task_rank"], "protocol": PROTOCOL,
                 "protocol_condition": PROTOCOL_CONDITION, "model": call["model"],
                 "config_hash": call["config_hash"], "imported_utc": now,
                 "error": error, "stop_reason": result.get("stop_reason")}
        if status == "completed":
            event["parsed"] = parsed
            response_rows.append({
                "schema_version": run.RUN_CONTRACT, "call_id": call_id,
                "item_id": call["item_id"], "task": call["task"],
                "case_number": call["case_number"], "protocol": PROTOCOL,
                "protocol_condition": PROTOCOL_CONDITION, "model": call["model"],
                "config_hash": call["config_hash"], "task_rank": call["task_rank"],
                "item_rank": call["item_rank"], "repeat_index": 1,
                "variant": call["variant"], "request": call["request"],
                "response": result.get("response"), "parsed": parsed,
                "error": None, "stop_reason": result.get("stop_reason"),
                "imported_utc": now})
        ledger_rows.append(event)
    run.append_jsonl(run_dir / "responses.jsonl", response_rows)
    run.append_jsonl(run_dir / "ledger.jsonl", ledger_rows)
    counts["missing_after_import"] = len(set(catalog) - set(run.read_completed(run_dir)))
    return counts


def self_test(items_path):
    args = SimpleNamespace(model="self-test-model", max_tokens=4096,
                           thinking="unset", effort="medium", seed="pmcpa-bench")
    items = run.load_ranked_items(items_path, ["T1"], [], 2, args.seed)
    calls, config = build_call_plan(items, args)
    calls2, config2 = build_call_plan(items, args)
    assert run.digest(config) == run.digest(config2)
    assert [c["call_id"] for c in calls] == [c["call_id"] for c in calls2]
    assert len(calls) == 2
    for call in calls:
        schema = call["request"]["output_config"]["format"]["schema"]
        assert set(schema["properties"]) == {"answer", "probability"}
        assert call["variant"]["block_order"] == list(ALT_ORDER)
    good = _strict_parsed({"parsed": {"answer": calls[0]["allowed_answers"][0],
                                      "probability": 0.5}}, calls[0])
    assert good["probability"] == 0.5
    print(f"p1_order_plan self-test PASS: order {ALT_ORDER}, "
          f"config={run.digest(config)[:12]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--items", type=pathlib.Path, default=DEFAULT_ITEMS)
    ap.add_argument("--tasks", default="T1,T2,T3")
    ap.add_argument("--splits", default="")
    ap.add_argument("--through-items", type=int)
    ap.add_argument("--model")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--thinking", choices=("unset", "adaptive", "disabled"),
                    default="unset")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--seed", default="pmcpa-bench")
    ap.add_argument("--run-dir", type=pathlib.Path)
    ap.add_argument("--export-batch", type=pathlib.Path)
    ap.add_argument("--import-results", type=pathlib.Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test(args.items)
    if args.import_results:
        if not args.run_dir:
            raise SystemExit("--import-results requires --run-dir")
        counts = import_results(args.run_dir, args.import_results)
        print(f"imported: completed={counts['completed']} failed={counts['failed']} "
              f"duplicates={counts['duplicate']} "
              f"remaining={counts['missing_after_import']}")
        return 0
    if not args.model or args.through_items is None:
        raise SystemExit("planning requires --model and --through-items")
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    if args.export_batch:
        if not args.run_dir:
            raise SystemExit("--export-batch requires --run-dir")
        result = export_batch(args.run_dir, args.export_batch, args.items,
                              tasks, splits, args.through_items, args)
        print(f"planned={result['planned']} completed={result['completed']} "
              f"exported={result['exported']}")
        return 0
    ranked = run.load_ranked_items(args.items, tasks, splits,
                                   args.through_items, args.seed)
    calls, config = build_call_plan(ranked, args)
    print(f"DRY RUN: {len(calls)} P1 alternate-order calls; "
          f"config={run.digest(config)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
