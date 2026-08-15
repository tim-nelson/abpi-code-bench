"""Offline regressions for score-input bindings and active-run verification."""

import hashlib
import json
import pathlib
import tempfile
import unittest

try:
    import export_site_data as exporter
    import run
    import score
except ModuleNotFoundError:  # repository-root unittest invocation
    from bench import export_site_data as exporter
    from bench import run, score


def one_item():
    return {
        "item_id": "T1-provenance", "task": "T1", "case_number": "TEST/P",
        "label": "breach", "split": "test",
        "tags": ["sibling_group:TEST/P"],
        "contamination": {},
        "inputs": {
            "clause_ref": {"clause": "1", "code_year": 2021,
                           "clause_text": "Clause 1 text."},
            "extract_text": "[COMPLAINT]\nA complaint.\n\n"
                            "[RESPONSE FROM THE RESPONDENT COMPANY]\nA response.",
            "extract_provenance": [{
                "kind": "complaint", "file": "test.html", "pane": "report",
                "char_start": 0, "char_end": 12, "text_sha256": "complaint-hash",
            }],
            "renditions": [],
            "metadata_shown": {
                "respondent": "Example", "code_year": 2021,
                "date_received": "2021-01-01", "complainant_category": "company",
                "complainant_anonymous": False, "complainant_contactable": True,
            },
        },
    }


def t3_item():
    item = one_item()
    item.update({
        "item_id": "T3-provenance",
        "task": "T3",
        "label": "upheld",
    })
    item["inputs"]["metadata_shown"].update({
        "panel_ruling_for_clause": "breach",
        "appellant": "respondent",
    })
    return item


def write_results(path, calls, answers):
    path.write_text("".join(
        json.dumps({
            "call_id": call["call_id"],
            "parsed": parsed,
            "response": {"offline": True},
        }) + "\n"
        for call, parsed in zip(calls, answers)
    ), encoding="utf-8")


def board_item(item_id, rank):
    item = json.loads(json.dumps(one_item()))
    item["item_id"] = item_id
    item["case_number"] = f"TEST/{rank}"
    item["tags"] = [f"sibling_group:TEST/{rank}"]
    return item


def board_record(item_id, rank, protocol, repeat, answer, probability=None,
                 prompt_token="shared"):
    request = {"prompt": f"{protocol}:{item_id}:{prompt_token}"}
    parsed = {"answer": answer}
    if probability is not None:
        parsed["probability"] = probability
    return {
        "call_id": f"{protocol}-{item_id}-{repeat}",
        "item_id": item_id,
        "task": "T1",
        "task_rank": rank,
        "item_rank": rank,
        "repeat_index": repeat,
        "protocol": protocol,
        "request": request,
        "request_sha256": score.canonical_sha256(request),
        "prompt_sha256": hashlib.sha256(
            f"{protocol}:{item_id}:{prompt_token}".encode()).hexdigest(),
        "parsed": parsed,
        "error": None,
        "_parsed_receipt_count": 1,
        "_receipt_status": "parsed",
    }


class ScoreProvenanceTests(unittest.TestCase):
    def test_legacy_protocol_ids_keep_archived_semantics(self):
        item = one_item()
        by_id = {item["item_id"]: item}
        stated, dropped = score.aggregate([{
            "item_id": item["item_id"],
            "parsed": {"answer": "breach", "probability": 0.8},
        }], by_id, "P2")
        self.assertFalse(dropped)
        self.assertEqual(stated[0]["p"], 0.8)

        repeated, dropped = score.aggregate([{
            "item_id": item["item_id"], "parsed": {"answer": "breach"},
        }, {
            "item_id": item["item_id"], "parsed": {"answer": "no_breach"},
        }, {
            "item_id": item["item_id"], "parsed": {"answer": "breach"},
        }], by_id, "P1")
        self.assertFalse(dropped)
        self.assertAlmostEqual(repeated[0]["p"], 2 / 3)

    def test_binding_hashes_bytes_and_records_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            items = tmp / "items.jsonl"
            items.write_bytes(b"one\ntwo\n")
            got = score.scoring_input_bindings(
                items, tmp / "manifest.json", tmp / "requests.jsonl",
                tmp / "responses.jsonl", tmp / "ledger.jsonl")
            self.assertEqual(got["schema_version"], score.SCORING_INPUTS_SCHEMA)
            self.assertEqual(got["items"]["sha256"],
                             hashlib.sha256(b"one\ntwo\n").hexdigest())
            self.assertEqual(got["items"]["bytes"], 8)
            for role in ("manifest", "requests", "responses", "ledger"):
                self.assertEqual(got[role]["present"], False)
                self.assertIsNone(got[role]["sha256"])
                self.assertIsNone(got[role]["bytes"])

    def test_modern_score_binds_inputs_and_exporter_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bank = tmp / "items.jsonl"
            bank.write_text(json.dumps(one_item()) + "\n", encoding="utf-8")
            run_dir = tmp / "run"
            batch = tmp / "batch.jsonl"
            self.assertEqual(run.main([
                "--items", str(bank), "--protocol", "P1", "--tasks", "T1",
                "--through-items", "1", "--model", "offline-provenance-test",
                "--thinking", "unset", "--run-dir", str(run_dir),
                "--export-batch", str(batch),
            ]), 0)
            call = run.read_jsonl(batch)[0]
            result = tmp / "result.jsonl"
            result.write_text(json.dumps({
                "call_id": call["call_id"],
                "parsed": {"answer": "breach", "probability": 0.8},
                "response": {"offline": True},
            }) + "\n", encoding="utf-8")
            self.assertEqual(run.main([
                "--run-dir", str(run_dir), "--import-results", str(result),
            ]), 0)
            self.assertEqual(score.main([
                "--run", str(run_dir), "--items", str(bank), "--draws", "2",
            ]), 0)

            scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
            self.assertEqual(scores["coverage"]["items_receipted"], 1)
            self.assertNotIn("items_attempted", scores["coverage"])
            bindings = scores["scoring_inputs"]
            self.assertTrue(all(bindings[role]["present"] for role in (
                "items", "manifest", "requests", "responses", "ledger")))
            self.assertEqual(
                bindings["request_config"]["manifest_config_hash"],
                bindings["request_config"]["request_config_hashes"][0],
            )
            self.assertEqual(
                bindings["request_config"]["sha256"],
                score.request_config_binding(
                    json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")),
                    run.read_jsonl(run_dir / "requests.jsonl"),
                )["sha256"],
            )
            checked_scores, _ = exporter.require_complete_active_run(
                "synthetic", run_dir, bank)
            self.assertEqual(checked_scores["overall"]["n"], 1)

            scores_path = run_dir / "scores.json"
            stale_scores = json.loads(scores_path.read_text(encoding="utf-8"))
            stale_scores["scoring_inputs"]["request_config"]["sha256"] = "0" * 64
            scores_path.write_text(json.dumps(stale_scores) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "request_config binding mismatch"):
                exporter.require_complete_active_run("synthetic", run_dir, bank)
            scores_path.write_text(json.dumps(scores, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")

            responses = run_dir / "responses.jsonl"
            responses.write_text(responses.read_text(encoding="utf-8") + "\n",
                                 encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "responses binding mismatch"):
                exporter.require_complete_active_run("synthetic", run_dir, bank)

            requests = run.read_jsonl(run_dir / "requests.jsonl")
            requests[0]["config_hash"] = None
            (run_dir / "requests.jsonl").write_text(
                json.dumps(requests[0]) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "config hashes disagree"):
                score.main([
                    "--run", str(run_dir), "--items", str(bank), "--draws", "2",
                ])

    def test_no_active_runs_does_not_touch_legacy_archive(self):
        self.assertEqual(exporter.collect_runs([]), [])

    def test_automatic_boards_separate_protocols_and_use_exact_common_prefix(self):
        first, second = board_item("T1-first", 1), board_item("T1-second", 2)
        items = {row["item_id"]: row for row in (first, second)}
        condition = {"seed": "same", "rationale": False,
                     "temperature": None, "splits": []}
        candidates = [{
            "run_id": "model-a-p1", "model": "model-a", "protocol": "P1",
            "task": "T1", "contract": score.ACTIVE_RUN_CONTRACT,
            "condition": condition,
            "records": [
                board_record("T1-first", 1, "P1", 1, "breach", 0.8),
                board_record("T1-second", 2, "P1", 1, "breach", 0.7),
            ],
            "sequence": ("T1-first", "T1-second"), "horizon": 2, "k": 1,
        }, {
            "run_id": "model-b-p1", "model": "model-b", "protocol": "P1",
            "task": "T1", "contract": score.ACTIVE_RUN_CONTRACT,
            "condition": condition,
            "records": [board_record(
                "T1-first", 1, "P1", 1, "breach", 0.6)],
            "sequence": ("T1-first",), "horizon": 1, "k": 1,
        }, {
            "run_id": "model-a-p2", "model": "model-a", "protocol": "P2",
            "task": "T1", "contract": score.ACTIVE_RUN_CONTRACT,
            "condition": condition,
            "records": [
                board_record("T1-first", 1, "P2", repeat, answer)
                for repeat, answer in enumerate(
                    ("breach", "no_breach", "breach"), 1)
            ],
            "sequence": ("T1-first",), "horizon": 1, "k": 3,
        }]
        boards = exporter._automatic_cumulative_boards(
            score, items, candidates, ["model-a", "model-b"], draws=2, seed=7)
        self.assertEqual({board["protocol"] for board in boards}, {"P1", "P2"})
        p1 = next(board for board in boards if board["protocol"] == "P1")
        p2 = next(board for board in boards if board["protocol"] == "P2")
        self.assertEqual(p1["n_items"], 1)
        self.assertEqual(len(p1["entries"]), 2)
        self.assertTrue(p1["comparison"]["cross_model"])
        self.assertEqual(p1["entries"][0]["source_prefix_n"], 2)
        self.assertEqual(p2["k"], 3)
        self.assertFalse(p2["comparison"]["rankable"])
        self.assertTrue(any(
            "no cross-model ranking" in caveat
            for caveat in p2["comparison"]["caveats"]))
        self.assertTrue(all(
            "early cumulative prefix" in board["comparison"]["caveats"][0]
            for board in boards))

    def test_p2_k3_then_k7_top_up_retains_exact_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bank = tmp / "items.jsonl"
            bank.write_text(json.dumps(one_item()) + "\n", encoding="utf-8")
            run_dir = tmp / "p2-run"

            batch3 = tmp / "batch-k3.jsonl"
            common = [
                "--items", str(bank), "--protocol", "P2", "--tasks", "T1",
                "--through-items", "1", "--model", "offline-prefix-test",
                "--thinking", "unset", "--run-dir", str(run_dir),
            ]
            self.assertEqual(run.main([
                *common, "--through-repeats", "3", "--export-batch", str(batch3),
            ]), 0)
            calls3 = run.read_jsonl(batch3)
            result3 = tmp / "result-k3.jsonl"
            write_results(result3, calls3, [
                {"answer": "breach"}, {"answer": "no_breach"},
                {"answer": "breach"},
            ])
            self.assertEqual(run.main([
                "--run-dir", str(run_dir), "--import-results", str(result3),
            ]), 0)
            self.assertEqual(score.main([
                "--run", str(run_dir), "--items", str(bank), "--draws", "2",
            ]), 0)
            at_k3 = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
            self.assertEqual(at_k3["k"], 3)
            self.assertAlmostEqual(at_k3["overall"]["mean_confidence"], 2 / 3)

            batch7 = tmp / "batch-k7.jsonl"
            self.assertEqual(run.main([
                *common, "--through-repeats", "7", "--export-batch", str(batch7),
            ]), 0)
            calls7 = run.read_jsonl(batch7)
            self.assertEqual(len(calls7), 4)
            result7 = tmp / "result-k7.jsonl"
            write_results(result7, calls7, [{"answer": "breach"}] * 4)
            self.assertEqual(run.main([
                "--run-dir", str(run_dir), "--import-results", str(result7),
            ]), 0)
            self.assertEqual(score.main([
                "--run", str(run_dir), "--items", str(bank), "--draws", "2",
            ]), 0)
            at_k7 = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
            self.assertEqual(at_k7["k"], 7)
            self.assertEqual(at_k7["coverage"]["calls_planned"], 7)
            self.assertAlmostEqual(at_k7["overall"]["mean_confidence"], 6 / 7)

            prefix_path = run_dir / "scores-k3.json"
            self.assertEqual(score.main([
                "--run", str(run_dir), "--items", str(bank),
                "--through-repeats", "3", "--out", str(prefix_path),
                "--draws", "2",
            ]), 0)
            prefix = json.loads(prefix_path.read_text(encoding="utf-8"))
            self.assertEqual(prefix["k"], 3)
            self.assertEqual(prefix["coverage"]["calls_planned"], 3)
            self.assertEqual(prefix["coverage"]["receipt_calls_outside_horizon"], 4)
            self.assertAlmostEqual(prefix["overall"]["mean_confidence"], 2 / 3)

    def test_t3_integrated_p3_keeps_generic_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bank = tmp / "items.jsonl"
            bank.write_text(json.dumps(t3_item()) + "\n", encoding="utf-8")
            run_dir = tmp / "t3-run"
            batch = tmp / "batch.jsonl"
            self.assertEqual(run.main([
                "--items", str(bank), "--protocol", "P1", "--tasks", "T3",
                "--through-items", "1", "--model", "offline-t3-test",
                "--thinking", "unset", "--run-dir", str(run_dir),
                "--export-batch", str(batch),
            ]), 0)
            result = tmp / "result.jsonl"
            write_results(result, run.read_jsonl(batch), [
                {"answer": "overturned", "probability": 0.9},
            ])
            self.assertEqual(run.main([
                "--run-dir", str(run_dir), "--import-results", str(result),
            ]), 0)
            self.assertEqual(score.main([
                "--run", str(run_dir), "--items", str(bank), "--draws", "2",
            ]), 0)
            scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
            final = scores["per_task"]["T3"]["p4"]["curve"][-1]
            self.assertFalse(
                scores["per_task"]["T3"]["p4"]
                ["breach_directional_metrics_applicable"])
            self.assertEqual(final["misclassifications"], 1)
            self.assertEqual(final["misclassification_transitions"], [{
                "task": "T3", "true_label": "upheld",
                "predicted_answer": "overturned", "count": 1,
            }])
            self.assertIsNone(final["missed_breaches"])


if __name__ == "__main__":
    unittest.main()
