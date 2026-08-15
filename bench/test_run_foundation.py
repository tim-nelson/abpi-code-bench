"""Offline invariants for the cumulative, zero-provider runner foundation."""

import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

import run


def item(task, case, clause, suffix):
    complaint_hash = f"complaint-{case}-{clause}"
    extract = f"[COMPLAINT]\nFacts for {case} clause {clause}."
    if task == "T1":
        extract += "\n\n[RESPONSE FROM THE RESPONDENT COMPANY]\nThe response."
    return {
        "item_id": f"{task}-{suffix}",
        "task": task,
        "case_number": case,
        "label": "breach",
        "split": "test",
        "inputs": {
            "clause_ref": {"clause": clause, "code_year": 2021,
                           "clause_text": f"Clause {clause} text."},
            "extract_text": extract,
            "extract_provenance": [{
                "kind": "complaint", "file": f"{case}.html", "pane": "report",
                "char_start": 1, "char_end": 10, "text_sha256": complaint_hash,
            }],
            "renditions": [],
            "metadata_shown": {
                "respondent": "Example Pharma", "code_year": 2021,
                "date_received": "2021-01-01", "complainant_category": "company",
                "complainant_anonymous": False, "complainant_contactable": True,
            },
        },
    }


def args():
    return SimpleNamespace(model="offline-test-model", max_tokens=128,
                           thinking="unset", rationale=False, effort="",
                           temperature=None, seed="runner-test-seed")


class CanonicalRankingTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            item("T1", "CASE/A", "1", "a1"),
            item("T2", "CASE/A", "1", "a1"),
            item("T1", "CASE/A", "2", "a2"),
            item("T2", "CASE/A", "2", "a2"),
            item("T1", "CASE/B", "1", "b1"),
            item("T2", "CASE/B", "1", "b1"),
            item("T2", "CASE/C", "9", "c-only"),
        ]

    def test_independent_ranks_interleave_extras_and_align_pair_order(self):
        forward = run.canonical_task_ranks(self.rows, "seed")
        reverse = run.canonical_task_ranks(list(reversed(self.rows)), "seed")
        self.assertEqual(forward, reverse)
        common = ("a1", "a2", "b1")
        order_t1 = sorted(common, key=lambda suffix: forward[f"T1-{suffix}"])
        order_t2 = sorted(common, key=lambda suffix: forward[f"T2-{suffix}"])
        self.assertEqual(order_t1, order_t2)
        # Independent boards need not give counterparts identical absolute
        # ranks: CASE/C is represented in T2's first case round.
        self.assertTrue(any(
            forward[f"T1-{suffix}"] != forward[f"T2-{suffix}"]
            for suffix in common
        ))
        self.assertLessEqual(forward["T2-c-only"], 3)
        # Case round-robin: both cases receive a first item before CASE/A gets
        # its second clause.
        a_first = min(forward["T1-a1"], forward["T1-a2"])
        a_second = max(forward["T1-a1"], forward["T1-a2"])
        self.assertLess(forward["T1-b1"], a_second)
        self.assertLess(a_first, a_second)

    def test_pair_identity_is_outcome_blind(self):
        left = item("T1", "CASE/Z", "7", "z")
        right = item("T2", "CASE/Z", "7", "z")
        right["label"] = "no_breach"
        self.assertNotEqual(left["label"], right["label"])
        self.assertEqual(run.complaint_pair_key(left), run.complaint_pair_key(right))
        ranks = run.canonical_task_ranks([left, right], "seed")
        self.assertEqual(ranks[left["item_id"]], ranks[right["item_id"]])

    def test_through_items_is_per_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            bank = pathlib.Path(tmp) / "items.jsonl"
            bank.write_text("".join(json.dumps(row) + "\n" for row in self.rows),
                            encoding="utf-8")
            picked = run.load_ranked_items(bank, ["T1", "T2"], [], 2, "seed")
            counts = {task: sum(row["task"] == task for row in picked)
                      for task in ("T1", "T2")}
            self.assertEqual(counts, {"T1": 2, "T2": 2})


class CumulativeCallTests(unittest.TestCase):
    def setUp(self):
        self.item = {**item("T1", "CASE/A", "1", "a1"), "_task_rank": 1}
        self.args = args()

    def test_p2_top_up_is_strict_superset_and_requests_are_identical(self):
        seven, config7 = run.build_call_plan([self.item], "P2", 7, self.args)
        ten, config10 = run.build_call_plan([self.item], "P2", 10, self.args)
        self.assertEqual(config7, config10)
        self.assertEqual([c["call_id"] for c in seven],
                         [c["call_id"] for c in ten[:7]])
        self.assertEqual({c["request_sha256"] for c in ten},
                         {ten[0]["request_sha256"]})
        self.assertEqual({run.canonical_json(c["request"]) for c in ten},
                         {run.canonical_json(ten[0]["request"])})
        self.assertEqual([c["repeat_index"] for c in ten], list(range(1, 11)))

    def test_p2_optional_temperature_is_fixed_and_hashed(self):
        self.args.temperature = 0.7
        calls, config = run.build_call_plan([self.item], "P2", 3, self.args)
        self.assertEqual(config["temperature"], 0.7)
        self.assertEqual({call["request"].get("temperature") for call in calls}, {0.7})
        self.assertEqual({call["request_sha256"] for call in calls},
                         {calls[0]["request_sha256"]})

    def test_p1_has_one_stable_repeat_index_and_probability_schema(self):
        calls, _ = run.build_call_plan([self.item], "P1", 99, self.args)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["repeat_index"], 1)
        schema = calls[0]["request"]["output_config"]["format"]["schema"]
        self.assertIn("probability", schema["required"])

    def test_manifest_counts_cumulative_catalog_across_task_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            t1 = self.item
            t2 = {**item("T2", "CASE/B", "2", "b2"), "_task_rank": 1}
            bank = tmp / "items.jsonl"
            bank.write_text(
                "".join(json.dumps({k: v for k, v in row.items()
                                     if k != "_task_rank"}) + "\n"
                        for row in (t1, t2)),
                encoding="utf-8",
            )
            run_dir = tmp / "run"
            calls_t1, config = run.build_call_plan([t1], "P1", 1, self.args)
            calls_t2, config_t2 = run.build_call_plan([t2], "P1", 1, self.args)
            self.assertEqual(config, config_t2)
            run.export_batch(run_dir, tmp / "t1.jsonl", calls_t1, self.args,
                             bank, config, 1, 1, ["T1"], [])
            run.export_batch(run_dir, tmp / "t2.jsonl", calls_t2, self.args,
                             bank, config, 1, 1, ["T2"], [])
            manifest = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(manifest["n_items_planned"], 2)
            self.assertEqual(manifest["n_calls_planned"], 2)
            self.assertEqual(manifest["max_through_items_by_task"], {"T1": 1, "T2": 1})
            self.assertEqual(manifest["tasks_filter_history"], [["T1"], ["T2"]])
            self.assertEqual(len(run.read_jsonl(run_dir / "requests.jsonl")), 2)

    def test_live_flag_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "makes no provider calls"):
            run.main(["--live"])

    def test_p3_is_offline_only_not_a_runner_protocol(self):
        with self.assertRaises(SystemExit):
            run.main(["--protocol", "P3"])

    def test_export_requires_explicit_model(self):
        with self.assertRaisesRegex(SystemExit, "explicit --model"):
            run.main(["--export-batch", "/tmp/should-not-exist.jsonl",
                      "--run-dir", "/tmp/should-not-exist-run"])

    def test_export_import_resume_and_retry_are_missing_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bank = tmp / "items.jsonl"
            bank.write_text(json.dumps({k: v for k, v in self.item.items()
                                        if k != "_task_rank"}) + "\n", encoding="utf-8")
            run_dir = tmp / "run"
            calls2, config = run.build_call_plan([self.item], "P2", 2, self.args)
            first_batch = tmp / "batch-1.jsonl"
            first = run.export_batch(run_dir, first_batch, calls2, self.args,
                                     bank, config, 1, 2, ["T1"], [])
            self.assertEqual(first["exported"], 2)
            manifest = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(manifest["through_repeats"], 2)

            result_file = tmp / "results-1.jsonl"
            result_file.write_text(json.dumps({
                "call_id": calls2[0]["call_id"],
                "parsed": {"answer": "breach"},
                "response": {"offline": True},
            }) + "\n", encoding="utf-8")
            imported = run.import_results(run_dir, result_file)
            self.assertEqual(imported["completed"], 1)
            response = run.read_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(response["repeat_index"], 1)
            self.assertEqual(response["call_id"], calls2[0]["call_id"])

            calls3, _ = run.build_call_plan([self.item], "P2", 3, self.args)
            second_batch = tmp / "batch-2.jsonl"
            second = run.export_batch(run_dir, second_batch, calls3, self.args,
                                      bank, config, 1, 3, ["T1"], [])
            self.assertEqual(second["exported"], 2)
            exported_ids = {row["call_id"] for row in run.read_jsonl(second_batch)}
            self.assertEqual(exported_ids, {calls3[1]["call_id"], calls3[2]["call_id"]})

            retry = tmp / "retry.txt"
            retry.write_text(calls3[0]["call_id"] + "\n" + calls3[1]["call_id"] + "\n",
                             encoding="utf-8")
            retry_batch = tmp / "batch-retry.jsonl"
            retried = run.export_batch(run_dir, retry_batch, calls3, self.args,
                                       bank, config, 1, 3, ["T1"], [], retry)
            self.assertEqual(retried["exported"], 1)
            self.assertEqual(run.read_jsonl(retry_batch)[0]["call_id"],
                             calls3[1]["call_id"])

            duplicate = tmp / "duplicate.jsonl"
            duplicate.write_text(result_file.read_text(), encoding="utf-8")
            self.assertEqual(run.import_results(run_dir, duplicate)["duplicate"], 1)
            conflicting = tmp / "conflicting.jsonl"
            conflicting.write_text(json.dumps({
                "call_id": calls3[0]["call_id"],
                "parsed": {"answer": "no_breach"},
            }) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
                run.import_results(run_dir, conflicting)
            self.assertEqual(len(run.read_jsonl(run_dir / "responses.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
