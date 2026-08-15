"""Offline contract tests for the strict Anthropic Messages adapter."""

import json
import pathlib
import sys
import tempfile
import unittest


BENCH = pathlib.Path(__file__).resolve().parent
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from providers import anthropic_messages as adapter  # noqa: E402


def canonical_row(model=adapter.SONNET_MODEL, *, protocol="P1", repeat=1,
                  call_id=None):
    call_id = call_id or f"call-t1-000001-r{repeat:03d}-1234567890abcdefabcd"
    props = {
        "answer": {"type": "string", "enum": ["breach", "no_breach"]},
    }
    if protocol in ("P1", "P3"):
        props["probability"] = {
            "type": "number",
            "description": "Probability between 0 and 1 that the answer is correct.",
        }
    schema = {
        "type": "object", "properties": props,
        "required": list(props), "additionalProperties": False,
    }
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    request = {
        "model": model, "max_tokens": 4096, "system": "System instructions.",
        "messages": [{"role": "user", "content": "Case and question."}],
        "output_config": output_config,
    }
    if model == adapter.SONNET_MODEL:
        request["thinking"] = {"type": "adaptive"}
        output_config["effort"] = "medium"
    row = {
        "schema_version": adapter.CANONICAL_CONTRACT,
        "call_id": call_id, "custom_id": call_id,
        "task": "T1", "item_id": "T1-test", "case_number": "AUTH/TEST",
        "split": "test", "task_rank": 1, "item_rank": 1,
        "repeat_index": repeat, "protocol": protocol, "model": model,
        "config_hash": "a" * 64,
        "prompt_sha256": adapter.digest({
            "system": request["system"], "messages": request["messages"]}),
        "request_sha256": adapter.digest(request), "stage": "verdict",
        "request": request,
    }
    if protocol == "P3":
        row["aggregation"] = adapter.P3_AGGREGATION
    return row


def payload(model=adapter.SONNET_MODEL, *, answer="breach", probability=0.72):
    value = {"answer": answer}
    if probability is not None:
        value["probability"] = probability
    return {
        "id": "msg_01test", "type": "message", "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": json.dumps(value)}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {
            "input_tokens": 100, "output_tokens": 20,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        },
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FakeTransport:
    def __init__(self, *, message=None, submit_error=None, results=None):
        self.message = message or payload()
        self.submit_error = submit_error
        self.results = results
        self.calls = []

    def json(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == adapter.MESSAGE_ENDPOINT:
            return {"payload": self.message, "http_status": 200,
                    "headers": {"request-id": "req_sync"}}
        if method == "POST" and path == adapter.BATCH_ENDPOINT:
            if self.submit_error:
                raise self.submit_error
            n = len(body["requests"])
            return {"payload": {
                "id": "msgbatch_01test", "type": "message_batch",
                "processing_status": "in_progress",
                "request_counts": {"processing": n, "succeeded": 0,
                                   "errored": 0, "canceled": 0, "expired": 0},
                "created_at": "2026-08-14T00:00:00Z", "ended_at": None,
                "expires_at": "2026-08-15T00:00:00Z", "results_url": None,
            }, "http_status": 200, "headers": {"request-id": "req_batch"}}
        if method == "GET" and path == f"{adapter.BATCH_ENDPOINT}/msgbatch_01test":
            return {"payload": {
                "id": "msgbatch_01test", "type": "message_batch",
                "processing_status": "ended",
                "request_counts": {"processing": 0, "succeeded": 1,
                                   "errored": 0, "canceled": 0, "expired": 0},
                "created_at": "2026-08-14T00:00:00Z",
                "ended_at": "2026-08-14T00:01:00Z",
                "expires_at": "2026-08-15T00:00:00Z",
                "results_url": "https://api.anthropic.com/v1/messages/batches/x/results",
            }, "http_status": 200, "headers": {"request-id": "req_status"}}
        raise AssertionError((method, path, body))

    def bytes(self, path):
        self.calls.append(("GET_BYTES", path, None))
        result = self.results or {
            "custom_id": canonical_row()["call_id"],
            "result": {"type": "succeeded", "message": payload()},
        }
        return {"content": (json.dumps(result) + "\n").encode(),
                "http_status": 200, "headers": {"request-id": "req_results"}}


class CanonicalTests(unittest.TestCase):
    def test_sonnet_condition_is_adaptive_medium(self):
        row = canonical_row()
        adapter.validate_canonical_row(row)
        params = adapter.to_message_params(row)
        self.assertEqual(params["thinking"], {"type": "adaptive"})
        self.assertEqual(params["output_config"]["effort"], "medium")

    def test_haiku_condition_omits_unsupported_thinking_and_effort(self):
        row = canonical_row(adapter.HAIKU_MODEL)
        adapter.validate_canonical_row(row)
        params = adapter.to_message_params(row)
        self.assertNotIn("thinking", params)
        self.assertNotIn("effort", params["output_config"])

    def test_rejects_alias_model_and_cross_model_config(self):
        alias = canonical_row(adapter.HAIKU_MODEL)
        alias["model"] = alias["request"]["model"] = "claude-haiku-4-5"
        alias["request_sha256"] = adapter.digest(alias["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "unsupported exact"):
            adapter.validate_canonical_row(alias)
        bad = canonical_row(adapter.HAIKU_MODEL)
        bad["request"]["thinking"] = {"type": "adaptive"}
        bad["request_sha256"] = adapter.digest(bad["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "thinking must be omitted"):
            adapter.validate_canonical_row(bad)
        null_thinking = canonical_row(adapter.HAIKU_MODEL)
        null_thinking["request"]["thinking"] = None
        null_thinking["request_sha256"] = adapter.digest(null_thinking["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "thinking must be omitted"):
            adapter.validate_canonical_row(null_thinking)

    def test_rejects_hash_tampering_protocol_schema_and_probability_shape(self):
        row = canonical_row()
        row["request"]["messages"][0]["content"] += " tampered"
        with self.assertRaisesRegex(adapter.AdapterError, "request_sha256"):
            adapter.validate_canonical_row(row)
        p3 = canonical_row(protocol="P3")
        p3["aggregation"] = "other"
        with self.assertRaisesRegex(adapter.AdapterError, "linear_probability_pool"):
            adapter.validate_canonical_row(p3)

    def test_rejects_custom_id_outside_anthropic_contract(self):
        row = canonical_row(call_id="call invalid")
        with self.assertRaisesRegex(adapter.AdapterError, "must match"):
            adapter.validate_canonical_row(row)

    def test_batch_preparation_is_exact_immutable_and_one_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, out = root / "canonical.jsonl", root / "batch.json"
            rows = [canonical_row(repeat=1), canonical_row(
                repeat=2, call_id="call-t1-000001-r002-abcdef1234567890abcd")]
            write_jsonl(source, rows)
            self.assertEqual(adapter.prepare_batch(source, out), 2)
            body = json.loads(out.read_text())
            self.assertEqual(body, {"requests": [adapter.to_batch_request(r) for r in rows]})
            adapter.validate_batch_binding(source, out, 2)
            with self.assertRaisesRegex(adapter.AdapterError, "overwrite"):
                adapter.prepare_batch(source, out)


class NormalizationTests(unittest.TestCase):
    def test_smoke_preserves_raw_usage_model_request_and_parses(self):
        row = canonical_row()
        result = adapter.execute_smoke(row, FakeTransport())
        self.assertEqual(result["parsed"], {"answer": "breach", "probability": 0.72})
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["response"]["raw"]["model"], adapter.SONNET_MODEL)
        self.assertEqual(result["response"]["canonical_request"], row["request"])

    def test_probability_range_and_schema_are_strict(self):
        row = canonical_row()
        invalid = adapter.normalize_payload(row, payload(probability=1.1), {},
                                            requested_utc="now")
        self.assertEqual(invalid["error"]["type"], "response_validation_error")
        extra_payload = payload()
        value = json.loads(extra_payload["content"][0]["text"])
        value["extra"] = True
        extra_payload["content"][0]["text"] = json.dumps(value)
        extra = adapter.normalize_payload(row, extra_payload, {}, requested_utc="now")
        self.assertIn("fields must be exactly", extra["error"]["message"])

    def test_model_identity_mismatch_is_quarantined_not_retried(self):
        row = canonical_row()
        other = payload(model="claude-sonnet-5-undocumented")
        result = adapter.normalize_payload(row, other, {}, requested_utc="now")
        self.assertIsNone(result["error"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(result["quarantine"]["type"],
                         "provider_model_identity_mismatch")

    def test_ambiguous_smoke_transport_is_quarantined(self):
        row = canonical_row()
        class Ambiguous:
            def json(self, *args, **kwargs):
                raise adapter.ProviderHTTPError(None, None, None, "timeout", ambiguous=True)
        result = adapter.execute_smoke(row, Ambiguous())
        self.assertEqual(result["quarantine"]["type"], "ambiguous_provider_transport")
        self.assertFalse(result["retry_safe"])


class BatchWorkflowTests(unittest.TestCase):
    def _prepared(self, root):
        source, batch = root / "canonical.jsonl", root / "batch.json"
        write_jsonl(source, [canonical_row()])
        adapter.prepare_batch(source, batch)
        return source, batch

    def test_submit_binds_exact_payload_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            transport = FakeTransport()
            receipt = adapter.submit_batch(source, batch, 1, transport)
            self.assertEqual(receipt["batch_id"], "msgbatch_01test")
            self.assertEqual(receipt["canonical_jsonl_sha256"], adapter.file_sha256(source))
            self.assertEqual(transport.calls[0][2], json.loads(batch.read_text()))

    def test_ambiguous_submit_is_durably_classified_not_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            exc = adapter.ProviderHTTPError(None, None, None, "timeout", ambiguous=True)
            receipt = adapter.submit_batch(source, batch, 1,
                                           FakeTransport(submit_error=exc))
            self.assertIsNone(receipt["error"])
            self.assertEqual(receipt["quarantine"]["type"],
                             "ambiguous_batch_submission")

    def test_download_and_normalize_succeeded_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, _ = self._prepared(root)
            download = root / "download"
            adapter.download_batch("msgbatch_01test", download, FakeTransport())
            output = root / "normalized.jsonl"
            counts = adapter.normalize_batch(source, download, output)
            self.assertEqual(counts, {"expected": 1, "present": 1,
                                      "completed": 1, "failed": 0,
                                      "quarantined": 0, "missing": 0})
            normalized = adapter.read_jsonl(output)[0]
            self.assertEqual(normalized["parsed"]["answer"], "breach")
            self.assertIn("raw_batch_row", normalized["response"])

    def test_batch_billing_error_normalizes_retry_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, _ = self._prepared(root)
            download = root / "download"
            failure = {
                "custom_id": canonical_row()["call_id"],
                "result": {"type": "errored", "error": {
                    "type": "error", "request_id": "req_billing",
                    "error": {"type": "billing_error", "message": "credit balance low"},
                }},
            }
            adapter.download_batch("msgbatch_01test", download,
                                   FakeTransport(results=failure))
            output = root / "normalized.jsonl"
            counts = adapter.normalize_batch(source, download, output)
            self.assertEqual(counts["failed"], 1)
            row = adapter.read_jsonl(output)[0]
            self.assertTrue(row["retry_safe"])
            self.assertEqual(row["error"]["code"], "billing_error")


if __name__ == "__main__":
    unittest.main()
