"""Offline tests for the strict OpenAI Responses/Batch adapter."""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


BENCH = pathlib.Path(__file__).resolve().parent
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from providers import openai_responses as adapter  # noqa: E402
import run  # noqa: E402


def canonical_row(call_id="call-t1-000001-r001-1234567890abcdef", *,
                  probability=True, protocol="P1", model=adapter.MODEL):
    properties = {
        "answer": {"type": "string", "enum": ["breach", "no_breach"]},
    }
    required = ["answer"]
    if probability:
        properties["probability"] = {
            "type": "number",
            "description": "Probability between 0 and 1 that the answer is correct.",
        }
        required.append("probability")
    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    request = {
        "model": model,
        "max_tokens": adapter.MAX_OUTPUT_TOKENS,
        "system": "System instructions.",
        "messages": [{"role": "user", "content": "Case and question."}],
        "output_config": {
            "format": {"type": "json_schema", "schema": schema},
            "effort": adapter.REASONING_EFFORT,
        },
    }
    return {
        "schema_version": "pmcpa.zero-provider.v2",
        "call_id": call_id,
        "custom_id": call_id,
        "task": "T1",
        "item_id": "T1-test",
        "case_number": "AUTH/TEST",
        "split": "test",
        "task_rank": 1,
        "item_rank": 1,
        "repeat_index": 1,
        "protocol": protocol,
        "model": model,
        "config_hash": "a" * 64,
        "prompt_sha256": adapter.digest({
            "system": request["system"], "messages": request["messages"],
        }),
        "request_sha256": adapter.digest(request),
        "stage": "verdict",
        "request": request,
    }


def native_p3_row(call_id="call-p3-t1-000001-r001-1234567890abcdef"):
    row = canonical_row(call_id, protocol="P3")
    row["aggregation"] = adapter.P3_AGGREGATION
    return row


def response_payload(*, answer="breach", probability=0.73,
                     model=adapter.MODEL, status="completed"):
    value = {"answer": answer}
    if probability is not None:
        value["probability"] = probability
    return {
        "id": "resp_test123",
        "object": "response",
        "status": status,
        "model": model,
        "error": None,
        "incomplete_details": None,
        "output": [{
            "id": "msg_test123",
            "type": "message",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": json.dumps(value),
                "annotations": [],
            }],
        }],
        "usage": {
            "input_tokens": 111,
            "output_tokens": 22,
            "total_tokens": 133,
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FakeSmokeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload or response_payload()
        self.error = error
        self.calls = []

    def json(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if self.error:
            raise self.error
        return {
            "payload": self.payload,
            "http_status": 200,
            "headers": {"x-request-id": "req_sync_123"},
        }


class FakeBatchTransport:
    def __init__(self):
        self.uploads = []
        self.json_calls = []
        self.byte_calls = []
        self.statuses = []

    def upload_batch_file(self, path):
        self.uploads.append(pathlib.Path(path))
        return {
            "payload": {"id": "file-input123", "purpose": "batch"},
            "http_status": 200,
            "headers": {"x-request-id": "req_upload"},
        }

    def json(self, method, path, body=None, **kwargs):
        self.json_calls.append((method, path, body))
        if method == "POST" and path == "/batches":
            return {
                "payload": {
                    "id": "batch_test123",
                    "object": "batch",
                    "endpoint": adapter.ENDPOINT,
                    "input_file_id": "file-input123",
                    "completion_window": "24h",
                    "status": "validating",
                    "output_file_id": None,
                    "error_file_id": None,
                },
                "http_status": 200,
                "headers": {"x-request-id": "req_create"},
            }
        if method == "GET" and path == "/batches/batch_test123":
            status = self.statuses.pop(0) if self.statuses else "completed"
            return {
                "payload": {
                    "id": "batch_test123",
                    "object": "batch",
                    "endpoint": adapter.ENDPOINT,
                    "input_file_id": "file-input123",
                    "completion_window": "24h",
                    "status": status,
                    "output_file_id": "file-output123" if status == "completed" else None,
                    "error_file_id": "file-errors123" if status == "completed" else None,
                    "request_counts": {"total": 2, "completed": 1, "failed": 1},
                },
                "http_status": 200,
                "headers": {"x-request-id": "req_status"},
            }
        raise AssertionError(f"unexpected fake JSON call: {(method, path, body)}")

    def bytes(self, path):
        self.byte_calls.append(path)
        if path == "/files/file-output123/content":
            content = b'{"custom_id":"success"}\n'
        elif path == "/files/file-errors123/content":
            content = b'{"custom_id":"failure"}\n'
        else:
            raise AssertionError(f"unexpected fake bytes call: {path}")
        return {
            "content": content,
            "http_status": 200,
            "headers": {"x-request-id": "req_file"},
        }


class CanonicalTranslationTests(unittest.TestCase):
    def test_responses_translation_is_exact_and_model_invisible_repeats_match(self):
        row = canonical_row()
        other = canonical_row("call-t1-000001-r002-abcdef1234567890")
        body = adapter.to_responses_body(row)
        self.assertEqual(body, adapter.to_responses_body(other))
        self.assertEqual(body["model"], adapter.MODEL)
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertEqual(body["max_output_tokens"], 4096)
        self.assertFalse(body["store"])
        self.assertNotIn("temperature", body)
        self.assertEqual(body["input"][0], {
            "role": "system", "content": "System instructions.",
        })
        fmt = body["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["name"], "pmcpa_bench_output")
        self.assertIs(fmt["strict"], True)

    def test_rejects_model_effort_temperature_and_hash_mismatches(self):
        cases = []
        wrong_model = canonical_row()
        wrong_model["request"]["model"] = "gpt-5.6-sol"
        wrong_model["request_sha256"] = adapter.digest(wrong_model["request"])
        cases.append((wrong_model, "request.model"))

        wrong_effort = canonical_row()
        wrong_effort["request"]["output_config"]["effort"] = "low"
        wrong_effort["request_sha256"] = adapter.digest(wrong_effort["request"])
        cases.append((wrong_effort, "effort"))

        temperature = canonical_row()
        temperature["request"]["temperature"] = 0
        temperature["request_sha256"] = adapter.digest(temperature["request"])
        cases.append((temperature, "temperature unset"))

        stale_hash = canonical_row()
        stale_hash["request"]["system"] += " changed"
        cases.append((stale_hash, "request_sha256"))

        for row, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(adapter.AdapterError, pattern):
                    adapter.validate_canonical_row(row)

    def test_only_current_v2_contract_is_submittable(self):
        row = canonical_row()
        adapter.validate_canonical_row(row)
        row["schema_version"] = "pmcpa.zero-provider.v1"
        with self.assertRaisesRegex(adapter.AdapterError, "unsupported canonical"):
            adapter.validate_canonical_row(row)

    def test_native_p3_is_probability_bearing_and_requires_linear_pool(self):
        p1 = canonical_row()
        p3 = native_p3_row()
        adapter.validate_canonical_row(p3)
        self.assertEqual(adapter.to_responses_body(p3),
                         adapter.to_responses_body(p1))
        normalized = adapter.execute_smoke(p3, FakeSmokeTransport())
        self.assertEqual(normalized["parsed"], {
            "answer": "breach", "probability": 0.73,
        })

        missing_aggregation = native_p3_row()
        missing_aggregation.pop("aggregation")
        with self.assertRaisesRegex(adapter.AdapterError, "requires aggregation"):
            adapter.validate_canonical_row(missing_aggregation)

        conditioned = native_p3_row()
        conditioned["protocol_condition"] = "repeated_stated_probability"
        with self.assertRaisesRegex(adapter.AdapterError, "must not carry"):
            adapter.validate_canonical_row(conditioned)

        wrong_schema = native_p3_row()
        schema = wrong_schema["request"]["output_config"]["format"]["schema"]
        schema["properties"].pop("probability")
        schema["required"].remove("probability")
        wrong_schema["request_sha256"] = adapter.digest(wrong_schema["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "output fields"):
            adapter.validate_canonical_row(wrong_schema)

    def test_terra_is_supported_but_models_cannot_mix_or_mismatch(self):
        luna = canonical_row()
        terra = canonical_row(
            "call-t1-000002-r001-1234567890abcdef",
            model=adapter.TERRA_MODEL,
        )
        adapter.validate_canonical_row(terra)
        self.assertEqual(
            adapter.to_responses_body(terra)["model"], adapter.TERRA_MODEL)
        normalized = adapter.execute_smoke(
            terra,
            FakeSmokeTransport(response_payload(model=adapter.TERRA_MODEL)),
        )
        self.assertEqual(normalized["parsed"], {
            "answer": "breach", "probability": 0.73,
        })

        mismatched = canonical_row(model=adapter.TERRA_MODEL)
        mismatched["request"]["model"] = adapter.MODEL
        mismatched["request_sha256"] = adapter.digest(mismatched["request"])
        with self.assertRaisesRegex(adapter.AdapterError, "match top-level"):
            adapter.validate_canonical_row(mismatched)

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "mixed.jsonl"
            write_jsonl(source, [luna, terra])
            with self.assertRaisesRegex(adapter.AdapterError, "mixes models"):
                adapter.prepare_batch(source, pathlib.Path(tmp) / "never.jsonl")

    def test_prepare_batch_preserves_ids_rejects_duplicates_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            source = tmp / "canonical.jsonl"
            output = tmp / "batch.jsonl"
            rows = [canonical_row(), canonical_row(
                "call-t2-000001-r001-abcdef1234567890")]
            write_jsonl(source, rows)
            self.assertEqual(adapter.prepare_batch(source, output), 2)
            prepared = adapter.read_jsonl(output)
            self.assertEqual([row["custom_id"] for row in prepared],
                             [row["call_id"] for row in rows])
            self.assertEqual({row["url"] for row in prepared}, {"/v1/responses"})
            self.assertEqual({row["body"]["model"] for row in prepared}, {adapter.MODEL})
            with self.assertRaisesRegex(adapter.AdapterError, "overwrite"):
                adapter.prepare_batch(source, output)

            duplicate_source = tmp / "duplicates.jsonl"
            write_jsonl(duplicate_source, [rows[0], rows[0]])
            with self.assertRaisesRegex(adapter.AdapterError, "duplicate call ID"):
                adapter.prepare_batch(duplicate_source, tmp / "never.jsonl")


class SmokeNormalizationTests(unittest.TestCase):
    def test_success_preserves_raw_response_request_id_usage_and_parsed(self):
        canonical = canonical_row()
        transport = FakeSmokeTransport()
        result = adapter.execute_smoke(canonical, transport)
        self.assertIsNone(result["error"])
        self.assertEqual(result["parsed"], {"answer": "breach", "probability": 0.73})
        self.assertEqual(result["provider_request_id"], "req_sync_123")
        self.assertEqual(result["provider_response_id"], "resp_test123")
        self.assertEqual(result["usage"]["total_tokens"], 133)
        self.assertEqual(result["response"]["raw"], transport.payload)
        self.assertEqual(transport.calls[0][0:2], ("POST", "/responses"))
        self.assertEqual(transport.calls[0][3]["client_request_id"], canonical["call_id"])

    def test_wrong_response_model_and_refusal_are_retryable_errors(self):
        canonical = canonical_row()
        wrong_model = adapter.execute_smoke(
            canonical, FakeSmokeTransport(response_payload(model="gpt-5.6-sol")))
        self.assertIsNone(wrong_model["parsed"])
        self.assertIsNone(wrong_model["error"])
        self.assertEqual(wrong_model["quarantine"]["type"],
                         "provider_model_identity_mismatch")
        self.assertEqual(wrong_model["quarantine"]["candidate_parsed"],
                         {"answer": "breach", "probability": 0.73})

        refusal_payload = response_payload()
        refusal_payload["output"][0]["content"] = [{
            "type": "refusal", "refusal": "Cannot comply.",
        }]
        refusal = adapter.execute_smoke(
            canonical, FakeSmokeTransport(refusal_payload))
        self.assertIsNone(refusal["parsed"])
        self.assertIn("model refusal", refusal["error"]["message"])

    def test_http_error_becomes_durable_failed_receipt_without_request_body(self):
        error = adapter.ProviderHTTPError(
            429, "req_rate", {"error": {"code": "rate_limit", "message": "Try later"}},
            "HTTP 429: Try later")
        result = adapter.execute_smoke(canonical_row(), FakeSmokeTransport(error=error))
        self.assertEqual(result["error"]["code"], "rate_limit")
        self.assertEqual(result["provider_request_id"], "req_rate")
        self.assertIsNone(result["parsed"])
        self.assertNotIn("request", result["response"])

    def test_probability_range_and_ambiguous_transport_fail_closed(self):
        out_of_range = adapter.execute_smoke(
            canonical_row(), FakeSmokeTransport(response_payload(probability=1.2)))
        self.assertIsNone(out_of_range["parsed"])
        self.assertIn("outside [0, 1]", out_of_range["error"]["message"])

        timeout = adapter.ProviderHTTPError(
            None, None, None, "transport error: timed out", ambiguous=True,
            client_request_id=canonical_row()["call_id"])
        quarantined = adapter.execute_smoke(
            canonical_row(), FakeSmokeTransport(error=timeout))
        self.assertIsNone(quarantined["error"])
        self.assertFalse(quarantined["retry_safe"])
        self.assertEqual(quarantined["quarantine"]["type"],
                         "ambiguous_provider_transport")

    def test_normalized_smoke_row_imports_into_the_real_append_only_ledger(self):
        canonical = canonical_row()
        normalized = adapter.execute_smoke(canonical, FakeSmokeTransport())
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            catalog_row = {
                **canonical,
                "allowed_answers": ["breach", "no_breach"],
                "variant": {
                    "index": 0,
                    "rendition": 0,
                    "block_order": ["clause", "metadata", "extract"],
                    "temperature": None,
                },
            }
            write_jsonl(run_dir / "requests.jsonl", [catalog_row])
            results = pathlib.Path(tmp) / "normalized.jsonl"
            write_jsonl(results, [normalized])
            counts = run.import_results(run_dir, results)
            self.assertEqual(counts["completed"], 1)
            imported = run.read_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(imported["parsed"], normalized["parsed"])
            self.assertEqual(imported["response"]["request_id"], "req_sync_123")


class CredentialTests(unittest.TestCase):
    def test_literal_dotenv_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text(
                "ANTHROPIC_API_KEY=ignored\n"
                "OPENAI_API_KEY='from-file'\n"
                "OPENAI_API_KEY_EXTRA=also-ignored\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(adapter.load_api_key(path), "from-file")
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "from-env"}, clear=True):
                self.assertEqual(adapter.load_api_key(path), "from-env")

    def test_conflicting_dotenv_assignments_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY=one\nOPENAI_API_KEY=two\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(adapter.AdapterError, "conflicting"):
                    adapter.load_api_key(path)


class BatchWorkflowTests(unittest.TestCase):
    def _prepared(self, tmp, n=2):
        canonical = pathlib.Path(tmp) / "canonical.jsonl"
        batch = pathlib.Path(tmp) / "batch.jsonl"
        rows = [canonical_row(
            f"call-t1-{index:06d}-r001-{index:020d}") for index in range(1, n + 1)]
        write_jsonl(canonical, rows)
        adapter.prepare_batch(canonical, batch)
        return canonical, batch, rows

    def test_submit_validates_exact_count_and_preserves_file_batch_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical, batch, _ = self._prepared(tmp)
            transport = FakeBatchTransport()
            receipt = adapter.submit_batch(canonical, batch, 2, transport)
            self.assertIsNone(receipt["error"])
            self.assertEqual(receipt["input_file_id"], "file-input123")
            self.assertEqual(receipt["batch_id"], "batch_test123")
            self.assertEqual(receipt["upload"]["headers"]["x-request-id"], "req_upload")
            self.assertEqual(receipt["batch"]["headers"]["x-request-id"], "req_create")
            self.assertEqual(transport.json_calls[0][2], {
                "input_file_id": "file-input123",
                "endpoint": "/v1/responses",
                "completion_window": "24h",
            })

            untouched = FakeBatchTransport()
            with self.assertRaisesRegex(adapter.AdapterError, "expected 3"):
                adapter.submit_batch(canonical, batch, 3, untouched)
            self.assertEqual(untouched.uploads, [])

            tampered = pathlib.Path(tmp) / "tampered.jsonl"
            provider_rows = adapter.read_jsonl(batch)
            provider_rows[0]["body"]["input"][1]["content"] += " tampered"
            write_jsonl(tampered, provider_rows)
            with self.assertRaisesRegex(adapter.AdapterError, "differs from the canonical"):
                adapter.submit_batch(canonical, tampered, 2, untouched)
            self.assertEqual(untouched.uploads, [])

    def test_wait_polls_and_download_creates_immutable_receipts(self):
        transport = FakeBatchTransport()
        transport.statuses = ["validating", "in_progress", "completed"]
        receipt = adapter.wait_batch(
            "batch_test123", transport, poll_seconds=1, timeout_seconds=10,
            sleep_fn=lambda _: None)
        self.assertEqual(receipt["status"], "completed")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp) / "download"
            downloaded = adapter.download_batch("batch_test123", output_dir, transport)
            self.assertEqual(downloaded["status"], "completed")
            self.assertTrue((output_dir / "output.jsonl").exists())
            self.assertTrue((output_dir / "errors.jsonl").exists())
            self.assertTrue((output_dir / "batch.json").exists())
            with self.assertRaisesRegex(adapter.AdapterError, "refusing to reuse"):
                adapter.download_batch("batch_test123", output_dir, transport)

    def test_normalize_reconciles_success_error_and_missing_by_custom_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical_path = tmp / "canonical.jsonl"
            output_path = tmp / "normalized.jsonl"
            rows = [canonical_row(
                f"call-t1-{index:06d}-r001-{index:020d}") for index in range(1, 4)]
            write_jsonl(canonical_path, rows)
            download = tmp / "download"
            download.mkdir()
            (download / "batch.json").write_text(json.dumps({
                "provider": "openai",
                "batch_id": "batch_test123",
                "input_file_id": "file-input123",
                "output_file_id": "file-output123",
                "error_file_id": "file-errors123",
                "checked_utc": "2026-08-14T12:00:00Z",
            }), encoding="utf-8")
            write_jsonl(download / "output.jsonl", [{
                "id": "batch_req_success",
                "custom_id": rows[0]["call_id"],
                "response": {
                    "status_code": 200,
                    "request_id": "req_batch_success",
                    "body": response_payload(),
                },
                "error": None,
            }])
            write_jsonl(download / "errors.jsonl", [{
                "id": "batch_req_failure",
                "custom_id": rows[1]["call_id"],
                "response": None,
                "error": {"code": "batch_expired", "message": "Not executed in time"},
            }])
            counts = adapter.normalize_batch(canonical_path, download, output_path)
            self.assertEqual(counts, {
                "expected": 3, "present": 2, "completed": 1, "failed": 1,
                "quarantined": 0, "missing": 1,
            })
            normalized = adapter.read_jsonl(output_path)
            self.assertEqual([row["call_id"] for row in normalized],
                             [rows[0]["call_id"], rows[1]["call_id"]])
            self.assertIsNone(normalized[0]["error"])
            self.assertEqual(normalized[1]["error"]["code"], "batch_expired")
            self.assertEqual(normalized[0]["response"]["batch"]["batch_id"],
                             "batch_test123")
            self.assertEqual(normalized[0]["provider_batch_request_id"],
                             "batch_req_success")

    def test_normalize_rejects_unknown_and_duplicate_provider_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical_path = tmp / "canonical.jsonl"
            row = canonical_row()
            write_jsonl(canonical_path, [row])
            download = tmp / "download"
            download.mkdir()
            (download / "batch.json").write_text(json.dumps({
                "provider": "openai", "batch_id": "batch_test",
            }), encoding="utf-8")
            unknown = {
                "id": "batch_req", "custom_id": "unknown",
                "response": None, "error": {"message": "failed"},
            }
            write_jsonl(download / "errors.jsonl", [unknown])
            with self.assertRaisesRegex(adapter.AdapterError, "unknown custom_id"):
                adapter.normalize_batch(canonical_path, download, tmp / "never.jsonl")

            (download / "errors.jsonl").unlink()
            provider_row = {
                "id": "batch_req", "custom_id": row["call_id"],
                "response": None, "error": {"message": "failed"},
            }
            write_jsonl(download / "output.jsonl", [provider_row])
            write_jsonl(download / "errors.jsonl", [provider_row])
            with self.assertRaisesRegex(adapter.AdapterError, "duplicate custom_id"):
                adapter.normalize_batch(canonical_path, download, tmp / "never2.jsonl")

    def test_live_cli_fails_before_key_or_transport_without_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])
            rc = adapter.main([
                "smoke", "--input", str(canonical),
                "--output", str(tmp / "result.jsonl"),
            ])
            self.assertEqual(rc, 2)
            self.assertFalse((tmp / "result.jsonl").exists())

    def test_existing_smoke_receipt_blocks_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            output = tmp / "result.jsonl"
            write_jsonl(canonical, [canonical_row()])
            output.write_text("already reserved\n", encoding="utf-8")
            transport = FakeSmokeTransport()
            with mock.patch.object(adapter, "_transport", return_value=transport):
                rc = adapter.main([
                    "smoke", "--input", str(canonical), "--output", str(output),
                    "--execute",
                ])
            self.assertEqual(rc, 2)
            self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
