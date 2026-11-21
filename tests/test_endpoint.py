import io
import json
import os
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from app import app
from review import VISION_MODELS
from test_review import PNG, REPORT, clean_report, model_response


class ReviewEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_returns_a_sample_model_report(self):
        with patch.dict(os.environ, OPENAI_API_KEY="fake-key", OPENAI_MODEL="test-model"), patch(
            "review.urlopen", return_value=io.BytesIO(json.dumps(model_response(REPORT)).encode())
        ) as send:
            response = self.client.post("/api/review", data={
                "screenshots": [(io.BytesIO(PNG), "screen-a.png"), (io.BytesIO(PNG), "screen-b.png")],
                "audience": "First-time shopper",
                "flow_stage": "Payment",
                "next_action": "Pay now",
            })
        self.assertEqual(response.status_code, 200)
        expected = clean_report("en", "fr")
        expected.update(REPORT)
        expected["screens"] = [dict(screen, audience="First-time shopper") for screen in REPORT["screens"]]
        self.assertEqual(response.json, {"report": expected})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        send.assert_called_once()
        self.assertNotIn(b"fake-key", response.data)

    def test_reevaluation_sends_answers_with_original_review_inputs(self):
        answers = [{"question": "When is the payment authorized?", "answer": "On confirmation."}]
        report = clean_report("es", "de")
        for screen in report["screens"]:
            screen["audience"] = "Guest shoppers"
        with patch.dict(os.environ, OPENAI_API_KEY="fake-key", OPENAI_MODEL="test-model"), patch(
            "review.urlopen", return_value=io.BytesIO(json.dumps(model_response(report)).encode())
        ) as send:
            response = self.client.post("/api/review", data={
                "screenshots": [(io.BytesIO(PNG), "screen-a.png"), (io.BytesIO(PNG), "screen-b.png")],
                "audience": "Guest shoppers",
                "flow_stage": "Payment",
                "next_action": "Confirm payment",
                "answers": json.dumps(answers),
            })
        self.assertEqual(response.json, {"report": report})
        payload = json.loads(send.call_args.args[0].data)
        content = payload["input"][0]["content"]
        self.assertEqual([item["type"] for item in content].count("input_image"), 2)
        self.assertEqual(json.loads(content[0]["text"])["open_question_answers"], answers)

    def test_unconfigured_server_does_not_attempt_review(self):
        with patch.dict(os.environ, OPENAI_API_KEY="", OPENAI_MODEL=""), patch("review.urlopen") as send:
            self.assertEqual(self.client.post("/api/review").status_code, 503)
        send.assert_not_called()

    def test_connection_checks_vision_and_falls_back_if_cheapest_is_unavailable(self):
        report = clean_report("Unknown")
        with patch("app.ui_connection", None), patch(
            "review.urlopen", side_effect=[
                rejection(404, "model_not_found"),
                io.BytesIO(json.dumps(model_response(report)).encode()),
            ],
        ) as send:
            response = self.client.post("/api/connection", json={"api_key": "fake-key"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"model": VISION_MODELS[1]})
            requests = [call.args[0] for call in send.call_args_list]
            self.assertEqual([json.loads(request.data)["model"] for request in requests], list(VISION_MODELS[:2]))
            for request in requests:
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("Authorization"), "Bearer fake-key")
                payload = json.loads(request.data)
                self.assertFalse(payload["store"])
                self.assertEqual(payload["input"][0]["content"][-1]["type"], "input_image")
            page = self.client.get("/")
            self.assertIn(VISION_MODELS[1].encode(), page.data)
            self.assertNotIn(b"fake-key", page.data)
            self.assertNotIn(b'id="model"', page.data)
            self.assertIn(b'API key verified', page.data)
            self.assertIn(b'Replace API key', page.data)

    def test_rejections_are_specific_and_preserve_current_configuration(self):
        cases = [
            (401, "invalid_api_key", "rejected this API key"),
            (401, "ip_not_authorized", "IP allowlist"),
            (401, "organization_membership_required", "organization membership"),
            (401, None, "missing API permissions"),
            (403, None, "denied permission"),
            (429, "insufficient_quota", "insufficient quota"),
            (429, "rate_limit_exceeded", "rate-limiting"),
        ]
        for status, code, diagnosis in cases:
            message = "Missing scopes: model.request. Sensitive diagnostic." if status == 401 and code is None else "Sensitive diagnostic."
            with self.subTest(status=status, code=code), patch(
                "app.ui_connection", ("existing-key", "existing-model")
            ), patch("review.urlopen", side_effect=rejection(status, code, message)) as send:
                response = self.client.post("/api/connection", json={"api_key": "fake-key"})
                self.assertEqual(response.status_code, 502)
                self.assertIn(diagnosis, response.json["error"])
                self.assertEqual(response.json["upstream_status"], status)
                self.assertEqual(response.json["request_id"], "req_example1234")
                self.assertNotIn(b"Sensitive diagnostic", response.data)
                self.assertIn(b"existing-model", self.client.get("/").data)
                send.assert_called_once()


def rejection(status, code, message="Sensitive diagnostic."):
    headers = Message()
    headers["x-request-id"] = "req_example1234"
    body = io.BytesIO(json.dumps({"error": {"code": code, "message": message}}).encode())
    return HTTPError("https://api.openai.com/v1/responses", status, "Rejected", headers, body)
