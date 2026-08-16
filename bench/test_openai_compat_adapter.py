"""Offline tests for the strict OpenAI-compatible chat-completions adapter.

No network access: every provider interaction goes through a fake transport.
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


BENCH = pathlib.Path(__file__).resolve().parent
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from providers import openai_compat as adapter  # noqa: E402
import run  # noqa: E402


XAI_MODEL = "grok-4.6"
DEEPSEEK_MODEL = "deepseek-v4-pro"
OPENROUTER_MODEL = "moonshotai/kimi-k3"


def canonical_row(call_id="call-t1-000001-r001-1234567890abcdef", *,
                  probability=True, protocol="P1", model=XAI_MODEL, task="T1"):
    properties = {
        "answer": {"type": "string", "enum": adapter.EXPECTED_ANSWERS[task]},
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
        },
    }
    row = {
        "schema_version": "pmcpa.zero-provider.v2",
        "call_id": call_id,
        "custom_id": call_id,
        "task": task,
        "item_id": f"{task}-test",
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
    if protocol == "P3":
        row["aggregation"] = adapter.P3_AGGREGATION
    return row


def chat_payload(*, answer="breach", probability=0.73, model=XAI_MODEL,
                 finish_reason="stop", content=None):
    if content is None:
        value = {"answer": answer}
        if probability is not None:
            value["probability"] = probability
        content = json.dumps(value)
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1755300000,
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content},
        }],
        "usage": {"prompt_tokens": 111, "completion_tokens": 22,
                  "total_tokens": 133},
    }


def http_error(status, *, message="failed", retry_after=None, code="err_code"):
    payload = None
    if status is not None:
        payload = {"error": {"message": message, "code": code}}
    return adapter.ProviderHTTPError(
        status, f"req_err_{status}" if status is not None else None, payload,
        f"HTTP {status}: {message}" if status is not None
        else f"transport error: {message}",
        retry_after=retry_after)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


class FakeTransport:
    """Scripted transport; raises if called more often than scripted."""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def json(self, method, path, body=None):
        self.calls.append((method, path, adapter.canonical_json(body)))
        if not self.outcomes:
            raise AssertionError("unexpected extra provider call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"payload": outcome, "http_status": 200,
                "headers": {"x-request-id": f"req_{len(self.calls)}"}}


def distinct_row(index, *, model=XAI_MODEL):
    """A canonical row whose user message (and therefore body) is unique."""
    row = canonical_row(f"call-t1-{index:06d}-r001-{index:020d}", model=model)
    row["request"]["messages"][0]["content"] = f"Case and question {index}."
    row["prompt_sha256"] = adapter.digest({
        "system": row["request"]["system"],
        "messages": row["request"]["messages"],
    })
    row["request_sha256"] = adapter.digest(row["request"])
    return row


class KeyedTransport:
    """Thread-safe scripted transport keyed on the user-message content.

    Concurrent workers finish in nondeterministic order, so outcomes are
    per-call scripts rather than one global sequence.  A call with no
    remaining scripted outcome fails the test (catches double submission).
    """

    def __init__(self, outcomes):
        self._lock = threading.Lock()
        self.outcomes = {key: list(value) for key, value in outcomes.items()}
        self.calls = []

    def json(self, method, path, body=None):
        key = body["messages"][1]["content"]
        with self._lock:
            self.calls.append(key)
            if not self.outcomes.get(key):
                raise AssertionError(f"unexpected provider call for {key!r}")
            outcome = self.outcomes[key].pop(0)
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return {"payload": outcome, "http_status": 200,
                "headers": {"x-request-id": f"req_{key}"}}


class BodyTranslationTests(unittest.TestCase):
    def test_byte_identical_rows_produce_byte_identical_bodies(self):
        first = canonical_row()
        second = canonical_row("call-t1-000001-r002-abcdef1234567890")
        body_a = adapter.to_chat_body(first, "xai")
        body_b = adapter.to_chat_body(second, "xai")
        self.assertEqual(adapter.canonical_json(body_a),
                         adapter.canonical_json(body_b))

    def test_xai_dialect_pins_schema_and_max_completion_tokens(self):
        body = adapter.to_chat_body(canonical_row(), "xai")
        self.assertEqual(body["model"], XAI_MODEL)
        self.assertEqual(body["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)
        self.assertNotIn("top_p", body)
        self.assertNotIn("reasoning_effort", body)  # documented only for grok-4.3
        self.assertNotIn("thinking", body)
        self.assertNotIn("provider", body)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["messages"][0], {
            "role": "system", "content": "System instructions.",
        })
        self.assertEqual(body["messages"][1]["role"], "user")
        fmt = body["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["json_schema"]["name"], "pmcpa_bench_output")
        self.assertIs(fmt["json_schema"]["strict"], True)
        self.assertEqual(fmt["json_schema"]["schema"]["properties"]["answer"]["enum"],
                         ["breach", "no_breach"])

    def test_deepseek_dialect_is_json_object_with_embedded_schema_and_thinking(self):
        row = canonical_row(model=DEEPSEEK_MODEL)
        body = adapter.to_chat_body(row, "deepseek")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_tokens"], 4096)
        self.assertNotIn("max_completion_tokens", body)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        system = body["messages"][0]["content"]
        self.assertTrue(system.startswith("System instructions."))
        self.assertIn("json", system)  # documented DeepSeek JSON-mode requirement
        schema = row["request"]["output_config"]["format"]["schema"]
        self.assertTrue(system.endswith(
            adapter.canonical_json(schema).decode("utf-8")))
        # The user message carries no schema addendum.
        self.assertEqual(body["messages"][1]["content"], "Case and question.")

    def test_openrouter_requires_parameters_and_records_pin_host(self):
        row = canonical_row(model=OPENROUTER_MODEL)
        unpinned = adapter.to_chat_body(row, "openrouter")
        self.assertEqual(unpinned["provider"], {"require_parameters": True})
        pinned = adapter.to_chat_body(row, "openrouter", pinned_host="moonshotai")
        self.assertEqual(pinned["provider"], {
            "require_parameters": True,
            "order": ["moonshotai"],
            "allow_fallbacks": False,
        })
        with self.assertRaisesRegex(adapter.AdapterError, "pin-host"):
            adapter.to_chat_body(canonical_row(), "xai", pinned_host="moonshotai")

    def test_rejects_temperature_effort_thinking_and_hash_mismatch(self):
        temperature = canonical_row()
        temperature["request"]["temperature"] = 0
        temperature["request_sha256"] = adapter.digest(temperature["request"])

        effort = canonical_row()
        effort["request"]["output_config"]["effort"] = "medium"
        effort["request_sha256"] = adapter.digest(effort["request"])

        thinking = canonical_row()
        thinking["request"]["thinking"] = {"type": "adaptive"}
        thinking["request_sha256"] = adapter.digest(thinking["request"])

        stale_hash = canonical_row()
        stale_hash["request"]["system"] += " changed"

        for row, pattern in ((temperature, "temperature"),
                             (effort, "effort must be unset"),
                             (thinking, "thinking must be unset"),
                             (stale_hash, "request_sha256")):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(adapter.AdapterError, pattern):
                    adapter.validate_canonical_row(row, "xai")

    def test_provider_and_model_gating_is_fail_closed(self):
        with self.assertRaisesRegex(adapter.AdapterError, "unknown provider"):
            adapter.validate_canonical_row(canonical_row(), "mystery")
        with self.assertRaisesRegex(adapter.AdapterError, "not pinned"):
            adapter.validate_canonical_row(canonical_row(), "deepseek")
        with self.assertRaisesRegex(adapter.AdapterError, "not pinned"):
            adapter.validate_canonical_row(
                canonical_row(model="grok-2-latest"), "xai")

        with tempfile.TemporaryDirectory() as tmp:
            mixed = pathlib.Path(tmp) / "mixed.jsonl"
            write_jsonl(mixed, [
                canonical_row(model=DEEPSEEK_MODEL),
                canonical_row("call-t1-000002-r001-1234567890abcdef",
                              model="deepseek-v4-flash"),
            ])
            with self.assertRaisesRegex(adapter.AdapterError, "mixes models"):
                adapter.load_canonical_rows(mixed, "deepseek")

    def test_p2_and_p3_schema_contracts(self):
        p2 = canonical_row(probability=False, protocol="P2")
        adapter.validate_canonical_row(p2, "xai")

        p3 = canonical_row(protocol="P3")
        adapter.validate_canonical_row(p3, "xai")
        p3_missing = canonical_row(protocol="P3")
        del p3_missing["aggregation"]
        with self.assertRaisesRegex(adapter.AdapterError, "aggregation"):
            adapter.validate_canonical_row(p3_missing, "xai")

        p2_with_probability = canonical_row(probability=True, protocol="P2")
        with self.assertRaisesRegex(adapter.AdapterError, "output fields"):
            adapter.validate_canonical_row(p2_with_probability, "xai")


class ExecuteCallTests(unittest.TestCase):
    def test_success_receipt_preserves_raw_usage_request_id_and_parsed(self):
        transport = FakeTransport([chat_payload()])
        sleeps = []
        receipt = adapter.execute_call(canonical_row(), transport, "xai",
                                       sleep_fn=sleeps.append)
        self.assertEqual(receipt["schema_version"],
                         "pmcpa.openai-compat-normalized.v1")
        self.assertEqual(receipt["provider"], "xai")
        self.assertIsNone(receipt["pinned_host"])
        self.assertEqual(receipt["parsed"], {"answer": "breach", "probability": 0.73})
        self.assertIsNone(receipt["error"])
        self.assertIsNone(receipt["quarantine"])
        self.assertFalse(receipt["retry_safe"])
        self.assertEqual(receipt["stop_reason"], "stop")
        self.assertEqual(receipt["provider_request_id"], "req_1")
        self.assertEqual(receipt["provider_response_id"], "chatcmpl-test123")
        self.assertEqual(receipt["usage"]["total_tokens"], 133)
        self.assertEqual(receipt["response"]["raw"], chat_payload())
        self.assertEqual(receipt["response"]["attempts"], 1)
        self.assertEqual(receipt["response"]["base_url"], "https://api.x.ai/v1")
        self.assertEqual(receipt["response"]["endpoint"], "/chat/completions")
        self.assertEqual(sleeps, [])
        self.assertEqual(transport.calls[0][0:2], ("POST", "/chat/completions"))

    def test_429_is_retried_and_retry_after_is_honored(self):
        transport = FakeTransport([
            http_error(429, retry_after=7.0), chat_payload()])
        sleeps = []
        receipt = adapter.execute_call(canonical_row(), transport, "xai",
                                       sleep_fn=sleeps.append)
        self.assertIsNone(receipt["error"])
        self.assertEqual(receipt["response"]["attempts"], 2)
        self.assertEqual(sleeps, [7.0])
        self.assertEqual(len(transport.calls), 2)
        # Both attempts posted byte-identical bodies.
        self.assertEqual(transport.calls[0][2], transport.calls[1][2])

    def test_400_is_terminal_never_retried(self):
        transport = FakeTransport([http_error(400, message="bad schema")])
        sleeps = []
        receipt = adapter.execute_call(canonical_row(), transport, "xai",
                                       sleep_fn=sleeps.append)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sleeps, [])
        self.assertIsNone(receipt["parsed"])
        self.assertIsNone(receipt["quarantine"])
        self.assertEqual(receipt["error"]["type"], "provider_http_error")
        self.assertEqual(receipt["error"]["http_status"], 400)
        self.assertEqual(receipt["error"]["code"], "err_code")
        self.assertFalse(receipt["retry_safe"])
        self.assertEqual(receipt["response"]["attempts"], 1)

    def test_5xx_and_transport_errors_retry_to_exhaustion(self):
        transport = FakeTransport([http_error(500)] * 4)
        sleeps = []
        receipt = adapter.execute_call(canonical_row(), transport, "xai",
                                       sleep_fn=sleeps.append)
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(sleeps, [2.0, 4.0, 8.0])
        self.assertTrue(receipt["retry_safe"])
        self.assertEqual(receipt["error"]["type"], "provider_http_error")
        self.assertEqual(receipt["response"]["attempts"], 4)

        transport = FakeTransport([http_error(None, message="timed out")] * 4)
        receipt = adapter.execute_call(canonical_row(), transport, "xai",
                                       sleep_fn=lambda _: None)
        self.assertEqual(len(transport.calls), 4)
        self.assertTrue(receipt["retry_safe"])
        self.assertIn("transport error", receipt["error"]["message"])

    def test_invalid_json_schema_violation_and_truncation_quarantine(self):
        cases = [
            ("not JSON", chat_payload(content="I think it is a breach.")),
            ("out of range", chat_payload(probability=1.2)),
            ("outside enum", chat_payload(answer="guilty")),
            ("truncated", chat_payload(finish_reason="length")),
            ("extra field", chat_payload(
                content=json.dumps({"answer": "breach", "probability": 0.7,
                                    "reasoning": "because"}))),
        ]
        for label, payload in cases:
            with self.subTest(label=label):
                receipt = adapter.execute_call(
                    canonical_row(), FakeTransport([payload]), "xai",
                    sleep_fn=lambda _: None)
                self.assertIsNone(receipt["parsed"])
                self.assertIsNone(receipt["error"])
                self.assertFalse(receipt["retry_safe"])
                self.assertEqual(receipt["quarantine"]["type"],
                                 "response_validation_failure")
                self.assertEqual(receipt["response"]["raw"], payload)

    def test_model_identity_mismatch_quarantines_with_candidate(self):
        receipt = adapter.execute_call(
            canonical_row(),
            FakeTransport([chat_payload(model="grok-4.6-0709")]), "xai",
            sleep_fn=lambda _: None)
        self.assertIsNone(receipt["parsed"])
        self.assertIsNone(receipt["error"])
        self.assertEqual(receipt["quarantine"]["type"],
                         "provider_model_identity_mismatch")
        self.assertEqual(receipt["quarantine"]["candidate_parsed"],
                         {"answer": "breach", "probability": 0.73})

    def test_openrouter_pinned_host_is_recorded_in_every_receipt(self):
        row = canonical_row(model=OPENROUTER_MODEL)
        payload = chat_payload(model=OPENROUTER_MODEL)
        good = adapter.execute_call(row, FakeTransport([payload]), "openrouter",
                                    pinned_host="moonshotai",
                                    sleep_fn=lambda _: None)
        self.assertEqual(good["pinned_host"], "moonshotai")
        self.assertEqual(good["response"]["pinned_host"], "moonshotai")

        failed = adapter.execute_call(row, FakeTransport([http_error(403)]),
                                      "openrouter", pinned_host="moonshotai",
                                      sleep_fn=lambda _: None)
        self.assertEqual(failed["pinned_host"], "moonshotai")
        self.assertEqual(failed["response"]["pinned_host"], "moonshotai")


class RunLiveTests(unittest.TestCase):
    def _canonical_file(self, tmp, n=2, model=XAI_MODEL):
        rows = [canonical_row(f"call-t1-{index:06d}-r001-{index:020d}",
                              model=model) for index in range(1, n + 1)]
        path = pathlib.Path(tmp) / "canonical.jsonl"
        write_jsonl(path, rows)
        return path, rows

    def test_max_calls_refusal_makes_no_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical, _ = self._canonical_file(tmp, n=3)
            output = pathlib.Path(tmp) / "out.jsonl"
            transport = FakeTransport()
            with self.assertRaisesRegex(adapter.AdapterError, "exceeds"):
                adapter.run_live(canonical, output, "xai", max_calls=2,
                                 transport=transport)
            self.assertEqual(transport.calls, [])
            self.assertFalse(output.exists())

    def test_resume_skips_existing_receipts_and_appends_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            output = tmp / "out.jsonl"
            first_only = tmp / "first.jsonl"
            canonical, rows = self._canonical_file(tmp, n=2)
            write_jsonl(first_only, [rows[0]])

            counts = adapter.run_live(
                first_only, output, "xai", max_calls=5,
                transport=FakeTransport([chat_payload()]),
                sleep_fn=lambda _: None)
            self.assertEqual(counts["attempted"], 1)
            before = output.read_bytes()

            resumed_transport = FakeTransport([chat_payload(answer="no_breach")])
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=5,
                transport=resumed_transport, sleep_fn=lambda _: None)
            self.assertEqual(counts, {"planned": 2, "skipped": 1, "attempted": 1,
                                      "completed": 1, "failed": 0,
                                      "quarantined": 0})
            self.assertEqual(len(resumed_transport.calls), 1)
            after = output.read_bytes()
            self.assertTrue(after.startswith(before))  # append-only integrity
            receipts = adapter.read_jsonl(output)
            self.assertEqual([r["call_id"] for r in receipts],
                             [rows[0]["call_id"], rows[1]["call_id"]])
            self.assertEqual(receipts[1]["parsed"]["answer"], "no_breach")

    def test_error_receipts_are_terminal_for_resume_never_rebilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, _ = self._canonical_file(tmp, n=1)
            output = tmp / "out.jsonl"
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=1,
                transport=FakeTransport([http_error(400)]),
                sleep_fn=lambda _: None)
            self.assertEqual(counts["failed"], 1)

            untouched = FakeTransport()
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=1, transport=untouched,
                sleep_fn=lambda _: None)
            self.assertEqual(counts, {"planned": 1, "skipped": 1, "attempted": 0,
                                      "completed": 0, "failed": 0,
                                      "quarantined": 0})
            self.assertEqual(untouched.calls, [])

    def test_existing_output_integrity_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._canonical_file(tmp, n=1)
            output = tmp / "out.jsonl"
            receipt = adapter.execute_call(
                rows[0], FakeTransport([chat_payload()]), "xai",
                sleep_fn=lambda _: None)

            write_jsonl(output, [receipt, receipt])
            with self.assertRaisesRegex(adapter.AdapterError, "duplicate receipt"):
                adapter.run_live(canonical, output, "xai", max_calls=1,
                                 transport=FakeTransport())

            foreign = dict(receipt)
            foreign["call_id"] = "call-t1-999999-r001-00000000000000000099"
            write_jsonl(output.with_name("foreign.jsonl"), [foreign])
            with self.assertRaisesRegex(adapter.AdapterError, "does not belong"):
                adapter.run_live(canonical, output.with_name("foreign.jsonl"),
                                 "xai", max_calls=1, transport=FakeTransport())

            output.with_name("alien.jsonl").write_text(
                '{"schema_version": "other", "call_id": "x"}\n', encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterError, "receipt"):
                adapter.run_live(canonical, output.with_name("alien.jsonl"),
                                 "xai", max_calls=1, transport=FakeTransport())

    def test_sleep_between_calls_and_progress_printing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, _ = self._canonical_file(tmp, n=3)
            output = tmp / "out.jsonl"
            sleeps = []
            progress = []
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=3,
                transport=FakeTransport([chat_payload()] * 3),
                sleep_ms=50, sleep_fn=sleeps.append,
                progress_every=1, print_fn=progress.append)
            self.assertEqual(counts["completed"], 3)
            self.assertEqual(sleeps, [0.05, 0.05])  # between calls only
            self.assertEqual(len(progress), 3)
            self.assertIn("progress: 1/3", progress[0])
            self.assertIn("completed=3", progress[2])

    def test_mixed_outcomes_are_counted_and_all_receipted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._canonical_file(tmp, n=3)
            output = tmp / "out.jsonl"
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=3,
                transport=FakeTransport([
                    chat_payload(),
                    http_error(400),
                    chat_payload(content="not json"),
                ]),
                sleep_fn=lambda _: None)
            self.assertEqual(counts, {"planned": 3, "skipped": 0, "attempted": 3,
                                      "completed": 1, "failed": 1,
                                      "quarantined": 1})
            receipts = adapter.read_jsonl(output)
            self.assertEqual([r["call_id"] for r in receipts],
                             [row["call_id"] for row in rows])


class ConcurrencyTests(unittest.TestCase):
    """The pooled run-live path preserves every sequential guarantee."""

    def _distinct_canonical(self, tmp, n, name="canonical.jsonl"):
        rows = [distinct_row(index) for index in range(1, n + 1)]
        path = pathlib.Path(tmp) / name
        write_jsonl(path, rows)
        return path, rows

    @staticmethod
    def _key(row):
        return row["request"]["messages"][0]["content"]

    def test_concurrent_run_yields_exactly_one_whole_line_receipt_per_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            n = 12
            canonical, rows = self._distinct_canonical(tmp, n)
            output = tmp / "out.jsonl"
            outcomes = {
                self._key(row): [chat_payload(probability=index / 100)]
                for index, row in enumerate(rows, 1)
            }
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=n,
                transport=KeyedTransport(outcomes),
                sleep_fn=lambda _: None, concurrency=4)
            self.assertEqual(counts, {"planned": n, "skipped": 0, "attempted": n,
                                      "completed": n, "failed": 0,
                                      "quarantined": 0})
            raw = output.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            lines = raw.splitlines()
            self.assertEqual(len(lines), n)
            by_id = {}
            for line in lines:
                receipt = json.loads(line)  # every line parses whole
                self.assertEqual(receipt["schema_version"],
                                 "pmcpa.openai-compat-normalized.v1")
                self.assertNotIn(receipt["call_id"], by_id)  # exactly once
                by_id[receipt["call_id"]] = receipt
            self.assertEqual(set(by_id), {row["call_id"] for row in rows})
            for index, row in enumerate(rows, 1):
                # Each receipt carries its own call's response, never a
                # neighbour's (no cross-thread mixing).
                self.assertEqual(by_id[row["call_id"]]["parsed"],
                                 {"answer": "breach", "probability": index / 100})
            # The strict resume reader accepts the whole file as-is.
            self.assertEqual(len(adapter.read_existing_receipts(
                output, set(by_id))), n)

    def test_resume_under_concurrency_skips_receipted_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._distinct_canonical(tmp, 8)
            first_only = tmp / "first.jsonl"
            write_jsonl(first_only, rows[:3])
            output = tmp / "out.jsonl"

            counts = adapter.run_live(
                first_only, output, "xai", max_calls=8,
                transport=KeyedTransport(
                    {self._key(row): [chat_payload()] for row in rows[:3]}),
                sleep_fn=lambda _: None, concurrency=2)
            self.assertEqual(counts["completed"], 3)
            before = output.read_bytes()

            # Outcomes exist ONLY for the un-receipted calls: any resubmission
            # of a receipted call_id would raise inside KeyedTransport.
            resumed = KeyedTransport(
                {self._key(row): [chat_payload()] for row in rows[3:]})
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=8, transport=resumed,
                sleep_fn=lambda _: None, concurrency=3)
            self.assertEqual(counts, {"planned": 8, "skipped": 3, "attempted": 5,
                                      "completed": 5, "failed": 0,
                                      "quarantined": 0})
            self.assertEqual(sorted(resumed.calls),
                             sorted(self._key(row) for row in rows[3:]))
            after = output.read_bytes()
            self.assertTrue(after.startswith(before))  # append-only integrity
            receipts = adapter.read_jsonl(output)
            self.assertEqual({r["call_id"] for r in receipts},
                             {row["call_id"] for row in rows})
            self.assertEqual(len(receipts), 8)

    def test_shared_cooldown_honors_retry_after_and_delays_other_workers(self):
        # Deterministic single-thread proof of the collective-backoff maths.
        clock = {"now": 100.0}
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        cooldown = adapter.SharedCooldown(now_fn=lambda: clock["now"])
        receipt = adapter.execute_call(
            canonical_row(),
            FakeTransport([http_error(429, retry_after=7.0), chat_payload()]),
            "xai", sleep_fn=fake_sleep, cooldown=cooldown)
        self.assertIsNone(receipt["error"])
        self.assertEqual(receipt["response"]["attempts"], 2)
        self.assertEqual(sleeps, [7.0])  # Retry-After honored, once

        # A second worker starting mid-cooldown waits out the remainder
        # before submitting anything.
        clock["now"] = 103.0
        other_sleeps = []

        def other_sleep(seconds):
            other_sleeps.append(seconds)
            clock["now"] += seconds

        other = adapter.execute_call(
            canonical_row("call-t1-000002-r001-1234567890abcdef"),
            FakeTransport([chat_payload()]), "xai",
            sleep_fn=other_sleep, cooldown=cooldown)
        self.assertIsNone(other["error"])
        self.assertEqual(other_sleeps, [4.0])  # remaining shared cooldown

    def test_429_retry_after_under_concurrency_delays_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._distinct_canonical(tmp, 4)
            output = tmp / "out.jsonl"
            outcomes = {self._key(row): [chat_payload()] for row in rows}
            outcomes[self._key(rows[1])] = [
                http_error(429, retry_after=5.0), chat_payload()]
            sleeps = []  # list.append is thread-safe under the GIL
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=4,
                transport=KeyedTransport(outcomes),
                sleep_fn=sleeps.append, concurrency=2)
            self.assertEqual(counts["completed"], 4)
            self.assertEqual(counts["failed"], 0)
            receipts = {r["call_id"]: r for r in adapter.read_jsonl(output)}
            self.assertEqual(len(receipts), 4)
            self.assertEqual(
                receipts[rows[1]["call_id"]]["response"]["attempts"], 2)
            self.assertIn(5.0, sleeps)  # the 429'd worker honored Retry-After

    def test_terminal_4xx_under_concurrency_is_receipted_and_others_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._distinct_canonical(tmp, 6)
            output = tmp / "out.jsonl"
            outcomes = {self._key(row): [chat_payload()] for row in rows}
            outcomes[self._key(rows[2])] = [http_error(404, message="gone")]
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=6,
                transport=KeyedTransport(outcomes),
                sleep_fn=lambda _: None, concurrency=3)
            self.assertEqual(counts, {"planned": 6, "skipped": 0, "attempted": 6,
                                      "completed": 5, "failed": 1,
                                      "quarantined": 0})
            receipts = {r["call_id"]: r for r in adapter.read_jsonl(output)}
            self.assertEqual(set(receipts), {row["call_id"] for row in rows})
            failed = receipts[rows[2]["call_id"]]
            self.assertEqual(failed["error"]["http_status"], 404)
            self.assertFalse(failed["retry_safe"])  # terminal, never re-billed

    def test_exhausted_retries_stop_new_submissions_but_finish_in_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._distinct_canonical(tmp, 3)
            output = tmp / "out.jsonl"
            release = threading.Event()

            def blocked_success():
                if not release.wait(timeout=10):
                    raise AssertionError("in-flight call was never released")
                return chat_payload()

            # Queue order: rows[0] blocks in flight on one worker; rows[1]
            # exhausts transport retries on the other; rows[2] must then
            # never be submitted.
            transport = KeyedTransport({
                self._key(rows[0]): [blocked_success],
                self._key(rows[1]): [http_error(None, message="net down")]
                * adapter.RETRY_MAX_ATTEMPTS,
                self._key(rows[2]): [chat_payload()],
            })
            result = {}

            def target():
                result["counts"] = adapter.run_live(
                    canonical, output, "xai", max_calls=3, transport=transport,
                    sleep_fn=lambda _: None, concurrency=2)

            runner = threading.Thread(target=target)
            runner.start()
            deadline = time.time() + 10
            while time.time() < deadline:  # wait for the failure receipt
                if output.exists() and rows[1]["call_id"] in output.read_text(
                        encoding="utf-8"):
                    break
                time.sleep(0.01)
            release.set()  # let the in-flight call finish
            runner.join(timeout=10)
            self.assertFalse(runner.is_alive())

            counts = result["counts"]
            self.assertEqual(counts["attempted"], 2)
            self.assertEqual(counts["completed"], 1)
            self.assertEqual(counts["failed"], 1)
            receipts = {r["call_id"]: r for r in adapter.read_jsonl(output)}
            # In-flight call finished and was receipted; the unstarted call
            # has no receipt, so it stays retriable.
            self.assertEqual(set(receipts),
                             {rows[0]["call_id"], rows[1]["call_id"]})
            self.assertTrue(receipts[rows[1]["call_id"]]["retry_safe"])
            self.assertNotIn(self._key(rows[2]), transport.calls)

            # Resume submits ONLY the unattempted call.
            resumed = KeyedTransport({self._key(rows[2]): [chat_payload()]})
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=3, transport=resumed,
                sleep_fn=lambda _: None, concurrency=2)
            self.assertEqual(counts, {"planned": 3, "skipped": 2, "attempted": 1,
                                      "completed": 1, "failed": 0,
                                      "quarantined": 0})

    def test_concurrency_default_is_one_and_sequential_path_is_unchanged(self):
        args = adapter.build_parser().parse_args([
            "run-live", "--canonical", "c.jsonl", "--output", "o.jsonl",
            "--provider", "xai", "--max-calls", "1", "--execute",
        ])
        self.assertEqual(args.concurrency, 1)

        with self.assertRaisesRegex(adapter.AdapterError, "concurrency"):
            adapter.run_live("unused.jsonl", "unused-out.jsonl", "xai",
                             max_calls=1, transport=FakeTransport(),
                             concurrency=0)

        # Explicit concurrency=1 reproduces the sequential contract exactly:
        # canonical receipt order, between-call sleeps only, per-attempt
        # progress lines (mirrors the pre-flag expectations).
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical, rows = self._distinct_canonical(tmp, 3)
            output = tmp / "out.jsonl"
            sleeps = []
            progress = []
            counts = adapter.run_live(
                canonical, output, "xai", max_calls=3,
                transport=KeyedTransport(
                    {self._key(row): [chat_payload()] for row in rows}),
                sleep_ms=50, sleep_fn=sleeps.append,
                progress_every=1, print_fn=progress.append, concurrency=1)
            self.assertEqual(counts["completed"], 3)
            self.assertEqual(sleeps, [0.05, 0.05])  # between calls only
            self.assertEqual(len(progress), 3)
            self.assertIn("progress: 1/3", progress[0])
            self.assertEqual([r["call_id"] for r in adapter.read_jsonl(output)],
                             [row["call_id"] for row in rows])

    def test_cli_passes_concurrency_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])
            counts = {"planned": 1, "skipped": 0, "attempted": 1,
                      "completed": 1, "failed": 0, "quarantined": 0}
            with mock.patch.object(adapter, "run_live",
                                   return_value=counts) as run_live, \
                    mock.patch.object(adapter, "_transport",
                                      return_value=FakeTransport()):
                rc = adapter.main([
                    "run-live", "--canonical", str(canonical),
                    "--output", str(tmp / "out.jsonl"), "--provider", "xai",
                    "--max-calls", "1", "--concurrency", "3", "--execute",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(run_live.call_args.kwargs["concurrency"], 3)

            with mock.patch.object(adapter, "_transport") as factory:
                rc = adapter.main([
                    "run-live", "--canonical", str(canonical),
                    "--output", str(tmp / "out.jsonl"), "--provider", "xai",
                    "--max-calls", "1", "--concurrency", "0", "--execute",
                ])
            self.assertEqual(rc, 2)  # refused before any credential is read
            factory.assert_not_called()


class ImportCompatibilityTests(unittest.TestCase):
    def test_completed_receipt_imports_into_the_real_append_only_ledger(self):
        canonical = canonical_row()
        normalized = adapter.execute_call(
            canonical, FakeTransport([chat_payload()]), "xai",
            sleep_fn=lambda _: None)
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
            self.assertEqual(imported["response"]["request_id"], "req_1")
            self.assertEqual(imported["response"]["provider"], "xai")

    def test_failed_receipt_imports_as_failed(self):
        canonical = canonical_row()
        normalized = adapter.execute_call(
            canonical, FakeTransport([http_error(400)]), "xai",
            sleep_fn=lambda _: None)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            catalog_row = {
                **canonical,
                "allowed_answers": ["breach", "no_breach"],
                "variant": {"index": 0, "rendition": 0,
                            "block_order": ["clause", "metadata", "extract"],
                            "temperature": None},
            }
            write_jsonl(run_dir / "requests.jsonl", [catalog_row])
            results = pathlib.Path(tmp) / "normalized.jsonl"
            write_jsonl(results, [normalized])
            counts = run.import_results(run_dir, results)
            self.assertEqual(counts["failed"], 1)
            self.assertEqual(counts["completed"], 0)


class CredentialTests(unittest.TestCase):
    def test_only_the_registered_key_is_read_per_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text(
                "OPENAI_API_KEY=not-this-one\n"
                "XAI_API_KEY='xai-file-key'\n"
                "export DEEPSEEK_API_KEY=deepseek-file-key\n"
                "OPENROUTER_API_KEY=openrouter-file-key\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(adapter.load_api_key("xai", path), "xai-file-key")
                self.assertEqual(adapter.load_api_key("deepseek", path),
                                 "deepseek-file-key")
                self.assertEqual(adapter.load_api_key("openrouter", path),
                                 "openrouter-file-key")
            with mock.patch.dict(os.environ, {"XAI_API_KEY": "xai-env-key"},
                                 clear=True):
                self.assertEqual(adapter.load_api_key("xai", path), "xai-env-key")

    def test_missing_and_conflicting_assignments_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text("XAI_API_KEY=one\nXAI_API_KEY=two\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(adapter.AdapterError, "conflicting"):
                    adapter.load_api_key("xai", path)
                with self.assertRaisesRegex(adapter.AdapterError, "not found"):
                    adapter.load_api_key("deepseek", path)


class CliTests(unittest.TestCase):
    def test_run_live_without_execute_makes_no_call_and_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])
            output = tmp / "out.jsonl"
            with mock.patch.object(adapter, "_transport") as factory:
                rc = adapter.main([
                    "run-live", "--canonical", str(canonical),
                    "--output", str(output), "--provider", "xai",
                    "--max-calls", "1",
                ])
            self.assertEqual(rc, 2)
            factory.assert_not_called()
            self.assertFalse(output.exists())

    def test_run_live_cli_executes_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])
            output = tmp / "out.jsonl"
            transport = FakeTransport([chat_payload()])
            with mock.patch.object(adapter, "_transport", return_value=transport):
                rc = adapter.main([
                    "run-live", "--canonical", str(canonical),
                    "--output", str(output), "--provider", "xai",
                    "--max-calls", "1", "--execute",
                ])
            self.assertEqual(rc, 0)
            receipts = adapter.read_jsonl(output)
            self.assertEqual(receipts[0]["parsed"],
                             {"answer": "breach", "probability": 0.73})

    def test_run_live_cli_refuses_cap_before_reading_any_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [
                canonical_row(),
                canonical_row("call-t1-000002-r001-1234567890abcdef"),
            ])
            with mock.patch.object(adapter, "_transport") as factory:
                rc = adapter.main([
                    "run-live", "--canonical", str(canonical),
                    "--output", str(tmp / "out.jsonl"), "--provider", "xai",
                    "--max-calls", "1", "--execute",
                ])
            self.assertEqual(rc, 2)
            factory.assert_not_called()

    def test_smoke_without_execute_and_existing_receipt_block_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])
            output = tmp / "result.jsonl"
            rc = adapter.main([
                "smoke", "--input", str(canonical), "--output", str(output),
                "--provider", "xai",
            ])
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())

            output.write_text("already reserved\n", encoding="utf-8")
            transport = FakeTransport([chat_payload()])
            with mock.patch.object(adapter, "_transport", return_value=transport):
                rc = adapter.main([
                    "smoke", "--input", str(canonical), "--output", str(output),
                    "--provider", "xai", "--execute",
                ])
            self.assertEqual(rc, 2)
            self.assertEqual(transport.calls, [])

    def test_smoke_cli_success_and_quarantine_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            canonical = tmp / "canonical.jsonl"
            write_jsonl(canonical, [canonical_row()])

            good = tmp / "good.jsonl"
            with mock.patch.object(adapter, "_transport",
                                   return_value=FakeTransport([chat_payload()])):
                rc = adapter.main([
                    "smoke", "--input", str(canonical), "--output", str(good),
                    "--provider", "xai", "--execute",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(adapter.read_jsonl(good)[0]["parsed"]["answer"],
                             "breach")

            bad = tmp / "bad.jsonl"
            with mock.patch.object(
                    adapter, "_transport",
                    return_value=FakeTransport([chat_payload(content="not json")])):
                rc = adapter.main([
                    "smoke", "--input", str(canonical), "--output", str(bad),
                    "--provider", "xai", "--execute",
                ])
            self.assertEqual(rc, 3)
            self.assertEqual(adapter.read_jsonl(bad)[0]["quarantine"]["type"],
                             "response_validation_failure")


if __name__ == "__main__":
    unittest.main()
