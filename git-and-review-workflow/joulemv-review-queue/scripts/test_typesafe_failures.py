"""Failure recovery must preserve evidence, disclose gaps, and keep caches safe."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import typesafe_triage as jev


def payloads(count=4):
    evidence = {
        "id": "case", "kind": "feedback", "focal_id": "feedback",
        "subject": "Reviewer's unanswered fix", "head_sha": "head",
        "context_complete": True, "pr": {"number": 1, "author": "author"},
        "events": [
            {"id": "feedback", "actor": "reviewer", "actor_type": "User",
             "at": "2026-10-01T00:00:00Z", "body": "Please fix this.", "url": "url"},
            {"id": "reply", "actor": "author", "actor_type": "User",
             "at": "2026-10-01T01:00:00Z", "body": "Fixed; please re-review.", "url": "reply-url"},
        ],
    }
    return [jev.build_payload(dict(evidence, id=f"case-{i}", subject=f"Feedback {i}"), "jev-1.13.0")
            for i in range(count)]


def response(payload, probability=0.95):
    return {"model": payload["model"], "answers": {
        q: {"type": "noul", "noul": probability} for q in payload["questions"]},
        "usage": {"input_tokens": 100, "output_tokens": len(payload["questions"])} }


def http_error(status=400, error_type="max_tokens_exceeded"):
    return HTTPError("https://api.typesafe.ai", status, "private provider message", {},
                     io.BytesIO(json.dumps({"detail": {"error_type": error_type,
                                                       "message": "PRIVATE EVIDENCE"}}).encode()))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)

    def test_overflow_halves_questions_without_losing_any_context(self):
        evidence = payloads(8)
        expected = jev.inference_context(evidence[0]["state"])
        seen = []

        def limited(payload, key):
            seen.append(copy.deepcopy(payload))
            self.assertEqual(payload["state"], expected)
            if len(payload["questions"]) > 2:
                raise jev.TypeSafeRequestError("max_tokens_exceeded", 400)
            return response(payload)

        with patch.object(jev, "request", side_effect=limited):
            result = jev.evaluate(evidence, "key", workers=1, cache_dir=self.cache)
        self.assertEqual(len(seen), 7)
        self.assertTrue(all(r["suggestion"] == "yes" for r in result["results"]))
        self.assertEqual(result["metrics"]["requests"], 7)
        self.assertEqual(result["metrics"]["failed_requests"], 3)
        self.assertEqual(result["metrics"]["retries"], 6)
        self.assertEqual(result["metrics"]["batch_splits"], 3)
        # Successful smaller batches use the exact same complete single-case key.
        with patch.object(jev, "request") as request:
            warm = jev.evaluate(evidence, "key", cache_dir=self.cache)
        request.assert_not_called()
        self.assertEqual(warm["metrics"]["cache_hits"], 8)

    def test_singleton_overflow_is_explicit_and_never_cached(self):
        with patch.object(jev, "request", side_effect=jev.TypeSafeRequestError("max_tokens_exceeded", 400)):
            first = jev.evaluate(payloads(1), "key", cache_dir=self.cache)
            second = jev.evaluate(payloads(1), "key", cache_dir=self.cache)
        self.assertEqual(first["results"][0]["error_type"], "max_tokens_exceeded")
        self.assertEqual(first["results"][0]["http_status"], 400)
        self.assertEqual(first["metrics"]["unavailable_cases"], 1)
        self.assertEqual(second["metrics"]["requests"], 1)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_oversized_state_has_bounded_calls_and_explicit_unprocessed_cases(self):
        with patch.object(jev, "request", side_effect=jev.TypeSafeRequestError("max_tokens_exceeded", 400)) as request:
            result = jev.evaluate(payloads(16), "key", workers=1, cache_dir=self.cache)
        self.assertEqual(request.call_count, jev.MAX_ADAPTIVE_REQUESTS)
        self.assertEqual(result["metrics"]["requests"], jev.MAX_ADAPTIVE_REQUESTS)
        self.assertEqual(result["metrics"]["unavailable_cases"], 16)
        self.assertIn("overflow_retry_budget_exhausted", {r["error_type"] for r in result["results"]})
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_request_failure_does_not_retry_unrelated_batches_or_expose_error_text(self):
        with patch.object(jev, "request", side_effect=ValueError("PRIVATE EVIDENCE")):
            result = jev.evaluate(payloads(4), "key", cache_dir=self.cache)
        self.assertEqual(result["metrics"]["requests"], 1)
        self.assertEqual(result["metrics"]["failed_requests"], 1)
        self.assertTrue(all(r["error_type"] == "invalid_response" for r in result["results"]))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_invalid_answers_and_uncertainty_are_counted(self):
        with patch.object(jev, "request", return_value={"answers": {"q0": {"type": "noul", "noul": 0.5}}}):
            result = jev.evaluate(payloads(2), "key", cache_dir=self.cache)
        self.assertEqual(result["metrics"]["uncertain_cases"], 1)
        self.assertEqual(result["metrics"]["unavailable_cases"], 1)
        self.assertEqual(result["results"][1]["error_type"], "invalid_answer")
        self.assertEqual(list(self.cache.iterdir()), [])


class RequestTests(unittest.TestCase):
    def test_http_overflow_code_is_allowlisted_and_provider_message_is_private(self):
        with patch.object(jev, "urlopen", side_effect=http_error()):
            with self.assertRaises(jev.TypeSafeRequestError) as caught:
                jev.request({"state": "private"}, "secret")
        self.assertEqual(caught.exception.error_type, "max_tokens_exceeded")
        self.assertEqual(caught.exception.http_status, 400)
        self.assertNotIn("PRIVATE", str(caught.exception))
        with patch.object(jev, "urlopen", side_effect=http_error(400, "PRIVATE EVIDENCE")):
            with self.assertRaises(jev.TypeSafeRequestError) as caught:
                jev.request({}, "secret")
        self.assertEqual(caught.exception.error_type, "http_error")
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_transient_retry_success_counts_every_http_attempt(self):
        payload = payloads(1)[0]
        good = io.BytesIO(json.dumps(response(next(jev.batches([payload]))[0])).encode())
        with patch.object(jev, "urlopen", side_effect=[http_error(429), good]), patch.object(jev.time, "sleep"):
            result = jev.evaluate([payload], "secret")
        self.assertEqual(result["metrics"]["requests"], 2)
        self.assertEqual(result["metrics"]["failed_requests"], 1)
        self.assertEqual(result["metrics"]["retries"], 1)
        self.assertEqual(result["results"][0]["suggestion"], "yes")

    def test_transient_failure_has_three_attempt_limit(self):
        with patch.object(jev, "urlopen", side_effect=[http_error(529) for _ in range(3)]) as request, patch.object(jev.time, "sleep"):
            result = jev.evaluate(payloads(1), "secret")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result["metrics"]["requests"], 3)
        self.assertEqual(result["metrics"]["failed_requests"], 3)
        self.assertEqual(result["metrics"]["retries"], 2)
        self.assertEqual(result["results"][0]["http_status"], 529)

    def test_connection_failure_is_explicit(self):
        with patch.object(jev, "urlopen", side_effect=URLError("PRIVATE EVIDENCE")):
            result = jev.evaluate(payloads(1), "secret")
        self.assertEqual(result["results"][0]["error_type"], "connection_error")
        self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
