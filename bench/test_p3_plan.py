"""Focused offline tests for native P3 planning and paid-receipt aliases."""

import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import p1r_plan
    import p3_plan
    import run
    import score
except ModuleNotFoundError:
    from bench import p1r_plan, p3_plan, run, score


def fixture_item(suffix):
    return {
        "item_id": f"T1-{suffix}", "task": "T1",
        "case_number": f"CASE/{suffix}", "label": "breach", "split": "test",
        "tags": [f"sibling_group:CASE/{suffix}"],
        "inputs": {
            "clause_ref": {"clause": "1", "code_year": 2021,
                           "clause_text": "Clause 1."},
            "extract_text": "[COMPLAINT]\nFacts.\n\n"
                            "[RESPONSE FROM THE RESPONDENT COMPANY]\nResponse.",
            "extract_provenance": [{
                "kind": "complaint", "file": f"{suffix}.html", "pane": "report",
                "char_start": 0, "char_end": 5, "text_sha256": f"hash-{suffix}",
            }],
            "renditions": [],
            "metadata_shown": {
                "respondent": "Example", "code_year": 2021,
                "date_received": "2021-01-01", "complainant_category": "company",
                "complainant_anonymous": False, "complainant_contactable": True,
            },
        },
    }


def args():
    return SimpleNamespace(
        model="gpt-5.6-luna", max_tokens=4096, thinking="unset",
        rationale=False, effort="medium", temperature=None, seed="pmcpa-bench",
    )


class NativeP3PlanTests(unittest.TestCase):
    def test_native_schema_and_probability_import(self):
        ranked = [{**fixture_item("A"), "_task_rank": 1}]
        calls, config = p3_plan.build_call_plan(ranked, 3, args())
        self.assertEqual(config["protocol"], "P3")
        self.assertNotIn("protocol_condition", config)
        self.assertEqual(config["request_template_protocol"], "P1")
        self.assertEqual({call["protocol"] for call in calls}, {"P3"})
        self.assertEqual({call.get("protocol_condition") for call in calls}, {None})
        self.assertEqual({call["aggregation"] for call in calls},
                         {"linear_probability_pool"})
        self.assertEqual(len({run.canonical_json(call["request"]) for call in calls}), 1)

    def test_aliases_preserve_source_bytes_and_leave_only_native_missing(self):
        with tempfile.TemporaryDirectory() as value:
            root = pathlib.Path(value)
            bank = root / "items.jsonl"
            bank.write_text("".join(json.dumps(fixture_item(suffix)) + "\n"
                                    for suffix in ("A", "B")), encoding="utf-8")
            source, target = root / "runs" / "source-p1r", root / "target-p3"
            source_batch = root / "source.jsonl"
            p1r_plan.export_batch(source, source_batch, bank, ["T1"], [], 1, 3, args())
            source_calls = run.read_jsonl(source_batch)
            source_results = root / "source-results.jsonl"
            source_results.write_text("".join(json.dumps({
                "call_id": call["call_id"],
                "parsed": {"answer": "breach", "probability": 0.71},
                "response": {"immutable_provider_body": call["call_id"]},
            }) + "\n" for call in source_calls), encoding="utf-8")
            run.import_results(source, source_results)
            source_hashes = {name: run.sha256(source / name) for name in (
                "manifest.json", "requests.jsonl", "responses.jsonl", "ledger.jsonl")}

            first_export = root / "all-p3.jsonl"
            p3_plan.export_batch(target, first_export, bank, ["T1"], [], 2, 3, args())
            migrated = p3_plan.migrate_receipts(target, source)
            self.assertEqual((migrated["aliased"], migrated["total_aliases"],
                              migrated["missing_after_migration"]), (3, 3, 3))
            self.assertEqual(source_hashes, {name: run.sha256(source / name)
                                            for name in source_hashes})
            aliases = run.read_jsonl(target / p3_plan.ALIAS_REGISTRY)
            self.assertEqual(len(aliases), 3)
            self.assertTrue(all(row["schema_version"] == p3_plan.ALIAS_SCHEMA
                                for row in aliases))
            target_responses = run.read_jsonl(target / "responses.jsonl")
            self.assertTrue(all(row["response"] is None for row in target_responses))
            self.assertTrue(all(row["receipt_alias"]["source_response_row_sha256"]
                                for row in target_responses))

            records, _ = score.cumulative_records(
                run.read_jsonl(target / "requests.jsonl"), target_responses,
                run.read_jsonl(target / "ledger.jsonl"), 0, 3,
                score.REPEATED_STATED)
            target_manifest = json.loads((target / "manifest.json").read_text())
            with mock.patch.object(score, "BENCH", root):
                audit = score.verify_receipt_aliases(records, target, target_manifest)
                self.assertEqual((audit["n_alias_calls"], audit["n_native_calls"]),
                                 (3, 0))
                corrupted = json.loads(json.dumps(records))
                corrupted[0]["receipt_alias"]["source_response_row_sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "alias/registry mismatch"):
                    score.verify_receipt_aliases(corrupted, target, target_manifest)

            missing_export = root / "missing-p3.jsonl"
            result = p3_plan.export_batch(
                target, missing_export, bank, ["T1"], [], 2, 3, args())
            self.assertEqual((result["planned"], result["completed"], result["exported"]),
                             (6, 3, 3))
            self.assertEqual({row["protocol"] for row in run.read_jsonl(missing_export)},
                             {"P3"})

            native_results = root / "native-results.jsonl"
            missing_calls = run.read_jsonl(missing_export)
            native_results.write_text("".join(json.dumps({
                "call_id": call["call_id"],
                "parsed": {"answer": "breach", "probability": 0.63},
                "response": {"native": True},
            }) + "\n" for call in missing_calls), encoding="utf-8")
            imported = p3_plan.import_results(target, native_results)
            self.assertEqual((imported["completed"], imported["missing_after_import"]),
                             (3, 0))
            parsed = [row["parsed"] for row in run.read_jsonl(target / "responses.jsonl")]
            self.assertTrue(all("probability" in row for row in parsed))


if __name__ == "__main__":
    unittest.main()
