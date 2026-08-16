"""Offline contract tests for the strict Gemini generateContent adapter."""

import json
import os
import pathlib
import sys
import tempfile
import unittest


BENCH = pathlib.Path(__file__).resolve().parent
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from providers import gemini_generate as adapter  # noqa: E402


def canonical_row(model=adapter.FLASH_MODEL, *, protocol="P1", task="T1",
                  repeat=1, call_id=None):
    call_id = call_id or f"call-t1-000001-r{repeat:03d}-1234567890abcdefabcd"
    props = {
        "answer": {"type": "string", "enum": adapter.EXPECTED_ANSWERS[task]},
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
    request = {
        "model": model, "max_tokens": 4096, "system": "System instructions.",
        "messages": [{"role": "user", "content": "Case and question."}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    row = {
        "schema_version": adapter.CANONICAL_CONTRACT,
        "call_id": call_id, "custom_id": call_id,
        "task": task, "item_id": f"{task}-test", "case_number": "AUTH/TEST",
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


def payload(model=adapter.FLASH_MODEL, *, answer="breach", probability=0.72,
            finish="STOP", text=None, parts=None):
    value = {"answer": answer}
    if probability is not None:
        value["probability"] = probability
    if parts is None:
        parts = [{"text": text if text is not None else json.dumps(value)}]
    return {
        "candidates": [{
            "content": {"role": "model", "parts": parts},
            "finishReason": finish, "index": 0,
        }],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20,
                          "thoughtsTokenCount": 64, "totalTokenCount": 184},
        "modelVersion": model, "responseId": "resp_01test",
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


BATCH_NAME = "batches/test-batch-123"
INPUT_FILE = "files/test-input-abc"
RESULTS_FILE = "files/test-results-xyz"
UPLOAD_URL = ("https://generativelanguage.googleapis.com/upload/v1beta/files"
              "?upload_id=xyz&upload_protocol=resumable")


class FakeTransport:
    """Offline stand-in for GeminiTransport; records every provider call."""

    def __init__(self, *, message=None, submit_error=None, results=None,
                 results_bytes=None, state="JOB_STATE_SUCCEEDED",
                 upload_state="ACTIVE"):
        self.message = message or payload()
        self.submit_error = submit_error
        self.results = results
        self.results_bytes = results_bytes
        self.state = state
        self.upload_state = upload_state
        self.uploaded_bytes = None
        self.calls = []

    def start_upload(self, num_bytes, display_name):
        self.calls.append(("START_UPLOAD", num_bytes, display_name))
        return UPLOAD_URL

    def finish_upload(self, upload_url, data):
        self.calls.append(("FINISH_UPLOAD", upload_url, data))
        self.uploaded_bytes = data
        return {"payload": {"file": {
            "name": INPUT_FILE, "state": self.upload_state,
            "sizeBytes": str(len(data)), "mimeType": "application/json",
        }}, "http_status": 200, "headers": {}}

    def json(self, method, path, body=None):
        self.calls.append((method, path, body))
        model = adapter.FLASH_MODEL
        if method == "POST" and path == adapter.generate_endpoint(model):
            return {"payload": self.message, "http_status": 200,
                    "headers": {"x-goog-request-id": "req_sync"}}
        if method == "POST" and path == adapter.batch_create_endpoint(model):
            if self.submit_error:
                raise self.submit_error
            return {"payload": {
                "name": BATCH_NAME,
                "metadata": {
                    "@type": ("type.googleapis.com/google.ai."
                              "generativelanguage.v1beta.GenerateContentBatch"),
                    "state": "JOB_STATE_PENDING",
                    "createTime": "2026-08-16T00:00:00Z",
                    "model": f"models/{model}",
                },
            }, "http_status": 200, "headers": {}}
        if method == "GET" and path == f"/v1beta/{INPUT_FILE}":
            return {"payload": {"name": INPUT_FILE, "state": "ACTIVE"},
                    "http_status": 200, "headers": {}}
        if method == "GET" and path == f"/v1beta/{BATCH_NAME}":
            terminal = self.state.endswith(("SUCCEEDED", "FAILED",
                                            "CANCELLED", "EXPIRED"))
            operation = {
                "name": BATCH_NAME, "done": terminal,
                "metadata": {
                    "state": self.state,
                    "createTime": "2026-08-16T00:00:00Z",
                    "batchStats": {"requestCount": "1",
                                   "successfulRequestCount": "1"},
                },
            }
            if self.state.endswith("SUCCEEDED"):
                operation["response"] = {"responsesFile": RESULTS_FILE}
            return {"payload": operation, "http_status": 200, "headers": {}}
        raise AssertionError((method, path, body))

    def bytes(self, path):
        self.calls.append(("GET_BYTES", path, None))
        assert path == f"/download/v1beta/{RESULTS_FILE}:download?alt=media"
        if self.results_bytes is not None:
            content = self.results_bytes
        else:
            rows = self.results or [{"key": canonical_row()["call_id"],
                                     "response": payload()}]
            content = "".join(json.dumps(row) + "\n" for row in rows).encode()
        return {"content": content, "http_status": 200, "headers": {}}


class CanonicalTests(unittest.TestCase):
    def test_request_body_is_deterministic_and_pinned(self):
        first = adapter.to_generate_body(canonical_row())
        second = adapter.to_generate_body(canonical_row())
        self.assertEqual(adapter.canonical_json(first),
                         adapter.canonical_json(second))
        config = first["generationConfig"]
        self.assertEqual(set(config), {"maxOutputTokens", "responseMimeType",
                                       "responseJsonSchema", "thinkingConfig"})
        self.assertEqual(config["maxOutputTokens"], 4096)
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["thinkingConfig"], {"thinkingLevel": "MEDIUM"})
        self.assertNotIn("temperature", config)
        self.assertNotIn("topP", config)
        self.assertNotIn("topK", config)
        self.assertEqual(first["systemInstruction"],
                         {"parts": [{"text": "System instructions."}]})
        self.assertEqual(first["contents"],
                         [{"role": "user", "parts": [{"text": "Case and question."}]}])
        pro = adapter.to_generate_body(canonical_row(adapter.PRO_MODEL))
        self.assertEqual(pro["generationConfig"]["thinkingConfig"],
                         {"thinkingLevel": "HIGH"})

    def test_assistant_history_maps_to_model_role(self):
        row = canonical_row()
        row["request"]["messages"].append({"role": "assistant", "content": "Noted."})
        row["request"]["messages"].append({"role": "user", "content": "Verdict?"})
        row["request_sha256"] = adapter.digest(row["request"])
        row["prompt_sha256"] = adapter.digest({
            "system": row["request"]["system"],
            "messages": row["request"]["messages"]})
        body = adapter.to_generate_body(row)
        self.assertEqual([content["role"] for content in body["contents"]],
                         ["user", "model", "user"])

    def test_rejects_unknown_or_alias_models(self):
        for bad in ("gemini-3.8-flash", "gemini-2.5-pro", "gemini-3.1-pro",
                    "gemini-3-pro-preview"):
            row = canonical_row()
            row["model"] = row["request"]["model"] = bad
            row["request_sha256"] = adapter.digest(row["request"])
            with self.assertRaisesRegex(adapter.AdapterError,
                                        "unsupported exact Gemini model"):
                adapter.validate_canonical_row(row)

    def test_rejects_thinking_effort_and_temperature_configuration(self):
        thinking = canonical_row()
        thinking["request"]["thinking"] = {"type": "adaptive"}
        thinking["request_sha256"] = adapter.digest(thinking["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "--thinking unset"):
            adapter.validate_canonical_row(thinking)
        effort = canonical_row()
        effort["request"]["output_config"]["effort"] = "medium"
        effort["request_sha256"] = adapter.digest(effort["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "--effort unset"):
            adapter.validate_canonical_row(effort)
        temp = canonical_row()
        temp["request"]["temperature"] = 0.0
        temp["request_sha256"] = adapter.digest(temp["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "temperature unset"):
            adapter.validate_canonical_row(temp)

    def test_rejects_hash_tampering_task_enum_and_p3_aggregation(self):
        row = canonical_row()
        row["request"]["messages"][0]["content"] += " tampered"
        with self.assertRaisesRegex(adapter.AdapterError, "request_sha256"):
            adapter.validate_canonical_row(row)
        wrong_enum = canonical_row(task="T3")
        wrong_enum["request"]["output_config"]["format"]["schema"]["properties"][
            "answer"]["enum"] = ["breach", "no_breach"]
        wrong_enum["request_sha256"] = adapter.digest(wrong_enum["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "answer enum"):
            adapter.validate_canonical_row(wrong_enum)
        p3 = canonical_row(protocol="P3")
        p3["aggregation"] = "other"
        with self.assertRaisesRegex(adapter.AdapterError, "linear_probability_pool"):
            adapter.validate_canonical_row(p3)

    def test_batch_preparation_is_deterministic_immutable_and_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            rows = [canonical_row(repeat=1), canonical_row(
                repeat=2, call_id="call-t1-000001-r002-abcdef1234567890abcd")]
            source = root / "canonical.jsonl"
            write_jsonl(source, rows)
            first, second = root / "batch-a.jsonl", root / "batch-b.jsonl"
            self.assertEqual(adapter.prepare_batch(source, first), 2)
            self.assertEqual(adapter.prepare_batch(source, second), 2)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            lines = [json.loads(line) for line in first.read_text().splitlines()]
            self.assertEqual([line["key"] for line in lines],
                             [row["call_id"] for row in rows])
            adapter.validate_batch_binding(source, first, 2)
            with self.assertRaisesRegex(adapter.AdapterError, "overwrite"):
                adapter.prepare_batch(source, first)

    def test_batch_binding_rejects_count_mismatch_and_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, batch = root / "canonical.jsonl", root / "batch.jsonl"
            write_jsonl(source, [canonical_row()])
            adapter.prepare_batch(source, batch)
            with self.assertRaisesRegex(adapter.AdapterError, "expected 2 requests"):
                adapter.validate_batch_binding(source, batch, 2)
            with self.assertRaisesRegex(adapter.AdapterError, "must be positive"):
                adapter.validate_batch_binding(source, batch, 0)
            tampered = json.loads(batch.read_text())
            tampered["request"]["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "LOW"}
            batch.unlink()
            write_jsonl(batch, [tampered])
            with self.assertRaisesRegex(adapter.AdapterError,
                                        "differs from the mechanical"):
                adapter.validate_batch_binding(source, batch, 1)
            widened = json.loads(batch.read_text())
            widened["request"]["generationConfig"]["temperature"] = 1.0
            batch.unlink()
            write_jsonl(batch, [widened])
            with self.assertRaisesRegex(adapter.AdapterError, "generationConfig"):
                adapter.validate_batch_binding(source, batch, 1)


class NormalizationTests(unittest.TestCase):
    def test_smoke_preserves_raw_usage_model_request_and_parses(self):
        row = canonical_row()
        result = adapter.execute_smoke(row, FakeTransport())
        self.assertEqual(result["parsed"], {"answer": "breach", "probability": 0.72})
        self.assertIsNone(result["error"])
        self.assertIsNone(result["quarantine"])
        self.assertEqual(result["usage"]["promptTokenCount"], 100)
        self.assertEqual(result["stop_reason"], "STOP")
        self.assertEqual(result["provider_response_id"], "resp_01test")
        self.assertEqual(result["response"]["raw"]["modelVersion"],
                         adapter.FLASH_MODEL)
        self.assertEqual(result["response"]["canonical_request"], row["request"])

    def test_probability_answer_and_schema_validation_fail_closed(self):
        row = canonical_row()
        outside = adapter.normalize_payload(row, payload(probability=1.1), {},
                                            requested_utc="now")
        self.assertEqual(outside["error"]["type"], "response_validation_error")
        self.assertTrue(outside["retry_safe"])
        bad_answer = adapter.normalize_payload(row, payload(answer="guilty"), {},
                                               requested_utc="now")
        self.assertIn("outside the enum", bad_answer["error"]["message"])
        extra = json.loads(payload()["candidates"][0]["content"]["parts"][0]["text"])
        extra["extra"] = True
        extra_row = adapter.normalize_payload(
            row, payload(text=json.dumps(extra)), {}, requested_utc="now")
        self.assertIn("fields must be exactly", extra_row["error"]["message"])
        not_json = adapter.normalize_payload(row, payload(text="verdict: breach"),
                                             {}, requested_utc="now")
        self.assertIn("invalid JSON", not_json["error"]["message"])

    def test_t2_answer_only_and_t3_enum(self):
        t2 = canonical_row(protocol="P2", task="T2")
        result = adapter.normalize_payload(
            t2, payload(probability=None), {}, requested_utc="now")
        self.assertEqual(result["parsed"], {"answer": "breach"})
        t3 = canonical_row(task="T3")
        result = adapter.normalize_payload(
            t3, payload(text=json.dumps({"answer": "upheld", "probability": 0.5})),
            {}, requested_utc="now")
        self.assertEqual(result["parsed"]["answer"], "upheld")

    def test_finish_reason_thought_parts_and_candidate_count_are_strict(self):
        row = canonical_row()
        truncated = adapter.normalize_payload(
            row, payload(finish="MAX_TOKENS"), {}, requested_utc="now")
        self.assertIn("finishReason='MAX_TOKENS'", truncated["error"]["message"])
        self.assertEqual(truncated["stop_reason"], "MAX_TOKENS")
        good_text = json.dumps({"answer": "breach", "probability": 0.72})
        thought = adapter.normalize_payload(row, payload(parts=[
            {"thought": True, "text": "internal reasoning"},
            {"text": good_text, "thoughtSignature": "sig"},
        ]), {}, requested_utc="now")
        self.assertEqual(thought["parsed"]["answer"], "breach")
        double = adapter.normalize_payload(row, payload(parts=[
            {"text": good_text}, {"text": good_text}]), {}, requested_utc="now")
        self.assertIn("exactly one text part", double["error"]["message"])
        non_text = adapter.normalize_payload(row, payload(parts=[
            {"inlineData": {"mimeType": "image/png"}}]), {}, requested_utc="now")
        self.assertIn("non-text response part", non_text["error"]["message"])
        empty = adapter.normalize_payload(row, {"candidates": []}, {},
                                          requested_utc="now")
        self.assertIn("exactly one candidate", empty["error"]["message"])
        blocked = adapter.normalize_payload(
            row, {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
            {}, requested_utc="now")
        self.assertIn("prompt blocked", blocked["error"]["message"])

    def test_model_identity_mismatch_is_quarantined_not_retried(self):
        row = canonical_row()
        other = payload(model="gemini-3.6-flash-002")
        result = adapter.normalize_payload(row, other, {}, requested_utc="now")
        self.assertIsNone(result["error"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(result["quarantine"]["type"],
                         "provider_model_identity_mismatch")
        self.assertEqual(result["quarantine"]["candidate_parsed"]["answer"], "breach")

    def test_ambiguous_smoke_transport_is_quarantined(self):
        row = canonical_row()

        class Ambiguous:
            def json(self, *args, **kwargs):
                raise adapter.ProviderHTTPError(None, None, None, "timeout",
                                                ambiguous=True)
        result = adapter.execute_smoke(row, Ambiguous())
        self.assertEqual(result["quarantine"]["type"], "ambiguous_provider_transport")
        self.assertFalse(result["retry_safe"])

        class Rejected:
            def json(self, *args, **kwargs):
                raise adapter.ProviderHTTPError(
                    400, None, {"error": {"code": 400, "message": "bad schema",
                                          "status": "INVALID_ARGUMENT"}},
                    "HTTP 400: bad schema")
        result = adapter.execute_smoke(row, Rejected())
        self.assertEqual(result["error"]["type"], "provider_http_error")
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertTrue(result["retry_safe"])


class BatchWorkflowTests(unittest.TestCase):
    def _prepared(self, root, rows=None):
        source, batch = root / "canonical.jsonl", root / "batch.jsonl"
        write_jsonl(source, rows or [canonical_row()])
        adapter.prepare_batch(source, batch)
        return source, batch

    def test_submit_uploads_exact_bytes_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            transport = FakeTransport()
            receipt = adapter.submit_batch(source, batch, 1, transport)
            self.assertIsNone(receipt["error"])
            self.assertIsNone(receipt["quarantine"])
            self.assertEqual(receipt["batch_name"], BATCH_NAME)
            self.assertEqual(receipt["input_file_name"], INPUT_FILE)
            self.assertEqual(receipt["model"], adapter.FLASH_MODEL)
            self.assertEqual(receipt["canonical_jsonl_sha256"],
                             adapter.file_sha256(source))
            self.assertEqual(transport.uploaded_bytes, batch.read_bytes())
            create = [call for call in transport.calls
                      if call[0] == "POST" and ":batchGenerateContent" in call[1]]
            self.assertEqual(create[0][2]["batch"]["inputConfig"],
                             {"fileName": INPUT_FILE})

    def test_submit_refuses_count_mismatch_before_any_provider_call(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            transport = FakeTransport()
            with self.assertRaisesRegex(adapter.AdapterError, "expected 3 requests"):
                adapter.submit_batch(source, batch, 3, transport)
            self.assertEqual(transport.calls, [])

    def test_ambiguous_submit_is_durably_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            exc = adapter.ProviderHTTPError(None, None, None, "timeout",
                                            ambiguous=True)
            receipt = adapter.submit_batch(source, batch, 1,
                                           FakeTransport(submit_error=exc))
            self.assertIsNone(receipt["error"])
            self.assertEqual(receipt["quarantine"]["type"],
                             "ambiguous_batch_submission")

    def test_submit_rejects_non_active_upload(self):
        with tempfile.TemporaryDirectory() as td:
            source, batch = self._prepared(pathlib.Path(td))
            transport = FakeTransport(upload_state="FAILED")
            receipt = adapter.submit_batch(source, batch, 1, transport)
            self.assertEqual(receipt["error"]["type"],
                             "provider_receipt_validation_error")
            self.assertIn("FAILED", receipt["error"]["message"])

    def test_state_normalization_accepts_both_documented_prefixes(self):
        self.assertEqual(adapter.normalize_batch_state("JOB_STATE_SUCCEEDED"),
                         "SUCCEEDED")
        self.assertEqual(adapter.normalize_batch_state("BATCH_STATE_RUNNING"),
                         "RUNNING")
        for bad in ("SUCCEEDED", "JOB_STATE_UNSPECIFIED", "", None, 3):
            with self.assertRaisesRegex(adapter.AdapterError, "unknown Gemini batch"):
                adapter.normalize_batch_state(bad)

    def test_download_requires_success_and_refuses_dir_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            with self.assertRaisesRegex(adapter.AdapterError, "not SUCCEEDED"):
                adapter.download_batch(BATCH_NAME, root / "failed",
                                       FakeTransport(state="JOB_STATE_FAILED"))
            download = root / "download"
            adapter.download_batch(BATCH_NAME, download, FakeTransport())
            self.assertTrue((download / "results.jsonl").exists())
            self.assertTrue((download / "batch.json").exists())
            with self.assertRaisesRegex(adapter.AdapterError, "refusing to reuse"):
                adapter.download_batch(BATCH_NAME, download, FakeTransport())

    def test_download_and_normalize_unordered_results(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            second_id = "call-t1-000002-r001-abcdef1234567890abcd"
            rows = [canonical_row(), canonical_row(
                repeat=1, call_id=second_id)]
            rows[1]["task_rank"] = 2
            source, _ = self._prepared(root, rows)
            # Provider returns results in reverse order; the key binding must
            # reconcile them to canonical order.
            results = [
                {"key": second_id,
                 "response": payload(text=json.dumps(
                     {"answer": "no_breach", "probability": 0.31}))},
                {"key": rows[0]["call_id"], "response": payload()},
            ]
            download = root / "download"
            adapter.download_batch(BATCH_NAME, download,
                                   FakeTransport(results=results))
            output = root / "normalized.jsonl"
            counts = adapter.normalize_batch(source, download, output)
            self.assertEqual(counts, {"expected": 2, "present": 2,
                                      "completed": 2, "failed": 0,
                                      "quarantined": 0, "missing": 0})
            normalized = adapter.read_jsonl(output)
            self.assertEqual([row["call_id"] for row in normalized],
                             [rows[0]["call_id"], second_id])
            self.assertEqual(normalized[1]["parsed"],
                             {"answer": "no_breach", "probability": 0.31})
            self.assertIn("raw_batch_row", normalized[0]["response"])
            self.assertEqual(normalized[0]["response"]["batch"]["batch_name"],
                             BATCH_NAME)

    def test_normalize_missing_row_is_reported_not_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            second_id = "call-t1-000002-r001-abcdef1234567890abcd"
            rows = [canonical_row(), canonical_row(repeat=1, call_id=second_id)]
            source, _ = self._prepared(root, rows)
            download = root / "download"
            adapter.download_batch(
                BATCH_NAME, download,
                FakeTransport(results=[{"key": rows[0]["call_id"],
                                        "response": payload()}]))
            output = root / "normalized.jsonl"
            counts = adapter.normalize_batch(source, download, output)
            self.assertEqual(counts["missing"], 1)
            self.assertEqual(counts["completed"], 1)
            normalized = adapter.read_jsonl(output)
            self.assertEqual(len(normalized), 1)
            self.assertEqual(normalized[0]["call_id"], rows[0]["call_id"])

    def test_normalize_rejects_unknown_duplicate_and_unkeyed_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, _ = self._prepared(root)
            call_id = canonical_row()["call_id"]
            cases = [
                ([{"key": "call-unknown-r001-ffffffffffffffffffff",
                   "response": payload()}], "unknown key"),
                ([{"key": call_id, "response": payload()},
                  {"key": call_id, "response": payload()}], "duplicate batch result"),
                ([{"response": payload()}], "no key binding"),
                ([{"key": call_id}], "neither response nor error"),
            ]
            for index, (results, message) in enumerate(cases):
                download = root / f"download-{index}"
                adapter.download_batch(BATCH_NAME, download,
                                       FakeTransport(results=results))
                with self.assertRaisesRegex(adapter.AdapterError, message):
                    adapter.normalize_batch(source, download,
                                            root / f"normalized-{index}.jsonl")

    def test_batch_error_row_normalizes_retry_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, _ = self._prepared(root)
            failure = [{"key": canonical_row()["call_id"],
                        "error": {"code": 8, "message": "Resource exhausted",
                                  "status": "RESOURCE_EXHAUSTED"}}]
            download = root / "download"
            adapter.download_batch(BATCH_NAME, download,
                                   FakeTransport(results=failure))
            output = root / "normalized.jsonl"
            counts = adapter.normalize_batch(source, download, output)
            self.assertEqual(counts["failed"], 1)
            row = adapter.read_jsonl(output)[0]
            self.assertTrue(row["retry_safe"])
            self.assertEqual(row["error"]["type"], "provider_batch_error")
            self.assertEqual(row["error"]["code"], "RESOURCE_EXHAUSTED")
            self.assertIn("Resource exhausted", row["error"]["message"])

    def test_receipts_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "receipt.json"
            adapter.write_json_exclusive(path, {"a": 1})
            with self.assertRaisesRegex(adapter.AdapterError, "overwrite"):
                adapter.write_json_exclusive(path, {"a": 2})
            rows_path = pathlib.Path(td) / "rows.jsonl"
            adapter.write_jsonl_exclusive(rows_path, [{"a": 1}])
            with self.assertRaisesRegex(adapter.AdapterError, "overwrite"):
                adapter.write_jsonl_exclusive(rows_path, [{"a": 2}])

    def test_resolve_batch_name_requires_clean_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            good = root / "good.json"
            adapter.write_json_exclusive(good, {"batch_name": BATCH_NAME,
                                                "error": None, "quarantine": None})
            self.assertEqual(adapter.resolve_batch_name(None, good), BATCH_NAME)
            bad = root / "bad.json"
            adapter.write_json_exclusive(bad, {"batch_name": BATCH_NAME,
                                               "quarantine": {"type": "x"}})
            with self.assertRaisesRegex(adapter.AdapterError, "not resolved-successful"):
                adapter.resolve_batch_name(None, bad)
            with self.assertRaisesRegex(adapter.AdapterError, "exactly one"):
                adapter.resolve_batch_name(BATCH_NAME, good)
            with self.assertRaisesRegex(adapter.AdapterError, "invalid Gemini batch"):
                adapter.resolve_batch_name("operations/123", None)


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is not None:
            os.environ["GEMINI_API_KEY"] = self._saved

    def test_env_file_literal_parse_reads_only_gemini_key(self):
        with tempfile.TemporaryDirectory() as td:
            env = pathlib.Path(td) / ".env"
            env.write_text(
                "# comment\n"
                "OPENAI_API_KEY=sk-other\n"
                "ANTHROPIC_API_KEY=sk-ant\n"
                "export GEMINI_API_KEY='g-key-123'\n"
                'GEMINI_API_KEY="g-key-123"\n',
                encoding="utf-8")
            self.assertEqual(adapter.load_api_key(env), "g-key-123")

    def test_env_file_conflicts_and_absence_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            env = pathlib.Path(td) / ".env"
            env.write_text("GEMINI_API_KEY=a\nGEMINI_API_KEY=b\n", encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterError, "conflicting"):
                adapter.load_api_key(env)
            env2 = pathlib.Path(td) / "other.env"
            env2.write_text("OPENAI_API_KEY=sk-other\n", encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterError, "not found"):
                adapter.load_api_key(env2)
            with self.assertRaisesRegex(adapter.AdapterError, "does not exist"):
                adapter.load_api_key(pathlib.Path(td) / "missing.env")

    def test_process_env_takes_precedence(self):
        os.environ["GEMINI_API_KEY"] = "g-env-key"
        try:
            self.assertEqual(adapter.load_api_key(None), "g-env-key")
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


class LiveGateTests(unittest.TestCase):
    def test_smoke_and_batch_submit_require_execute(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "canonical.jsonl"
            write_jsonl(source, [canonical_row()])
            batch = root / "batch.jsonl"
            adapter.prepare_batch(source, batch)
            code = adapter.main(["smoke", "--input", str(source),
                                 "--output", str(root / "smoke.jsonl")])
            self.assertEqual(code, 2)
            self.assertFalse((root / "smoke.jsonl").exists())
            code = adapter.main(["batch-submit", "--canonical", str(source),
                                 "--input", str(batch), "--expect-requests", "1",
                                 "--receipt", str(root / "receipt.json")])
            self.assertEqual(code, 2)
            self.assertFalse((root / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
