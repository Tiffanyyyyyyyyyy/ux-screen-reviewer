import base64
import io
import json
import unittest
from unittest.mock import patch

from review import CHECKS, ReviewError, build_payload, image_input, request_review

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOW0AAAAASUVORK5CYII="
)
def clean_report(*locales):
    return {
        "screens": [{
            "screen": f"Screen {index}", "name": "Checkout", "locale": locale,
            "audience": None, "intended_takeaway": None, "likely_takeaway": "Review the order total.",
            "first_impression": "The total draws the eye first.",
            "primary_action": {"label": None, "findability": "No visible action.",
                               "expected_result": None, "observed_result": None},
        } for index, locale in enumerate(locales, 1)],
        "findings": [],
        "coverage": {
            check: {"status": "Clear", "assessment": "Readable and easy to scan.", "finding_ids": [],
                    **({"translation_fidelity": "Unchecked"} if check == "language" else {})}
            for check in CHECKS
        },
        "open_questions": [],
    }


REPORT = clean_report("en", "fr")
REPORT["findings"].append({
    "id": "F1", "title": "Restore payment timing", "severity": "High",
    "screens": ["Screen 2"], "element": "Payment button",
    "evidence": "Screen 1 says “Pay now”; Screen 2 says “Payer”.",
    "impact": "Shoppers may miss that the charge is immediate.",
    "fix": "Payer maintenant",
    "verification": "Compare the labels side by side and check the French button width.",
})
REPORT["coverage"]["language"] = {
    "status": "Issue found", "assessment": "The French label drops the timing.",
    "finding_ids": ["F1"], "translation_fidelity": "Checked",
}


def model_response(report):
    return {"status": "completed", "output": [{
        "type": "message",
        "content": [{"type": "output_text", "text": json.dumps(report)}],
    }]}


class ModelReviewTests(unittest.TestCase):
    def test_sends_both_labeled_screens_and_parses_report(self):
        image = image_input(PNG)
        payload = build_payload("test-model", [image, image], {"audience": "First-time shopper"}, {"next_action": "Pay now"})
        with patch("review.urlopen", return_value=io.BytesIO(json.dumps(model_response(REPORT)).encode())) as send:
            self.assertEqual(request_review("fake-key", payload, 2), REPORT)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-key")
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        content = body["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], [
            "input_text", "input_text", "input_image", "input_text", "input_image",
        ])
        self.assertEqual(json.loads(content[0]["text"]), {
            "product_context": {"audience": "First-time shopper"}, "screen_intent": {"next_action": "Pay now"},
        })
        self.assertIn("Screen 1. Infer its locale", content[1]["text"])
        self.assertIn("Screen 2. Infer its locale", content[3]["text"])
        for index in (2, 4):
            self.assertEqual(base64.b64decode(content[index]["image_url"].split(",")[1]), PNG)

    def test_french_only_checks_language_fit_without_source_copy(self):
        payload = build_payload("test-model", [image_input(PNG)], {}, {})
        report = clean_report("fr")
        report["coverage"]["language"]["assessment"] = "The French phrasing is natural and fits."
        with patch("review.urlopen", return_value=io.BytesIO(json.dumps(model_response(report)).encode())):
            self.assertEqual(request_review("fake-key", payload, 1), report)
        self.assertEqual(report["coverage"]["language"]["translation_fidelity"], "Unchecked")
        self.assertIsNone(report["screens"][0]["primary_action"]["observed_result"])

    def test_one_portrait_overlap_finding_links_layout_and_imagery(self):
        payload = build_payload("test-model", [image_input(PNG)], {}, {})
        report = clean_report("fr")
        report["findings"].append({
            "id": "F1", "title": "Move the portrait away from the headline", "severity": "Medium",
            "screens": ["Screen 1"], "element": "Hero headline and portrait",
            "evidence": "The portrait covers the last line of the headline.",
            "impact": "Readers may miss part of the headline.",
            "fix": "Place the portrait below the headline in the hero.",
            "verification": "Check the headline at the narrowest supported width.",
        })
        for check in ("layout", "imagery"):
            report["coverage"][check].update(status="Issue found", finding_ids=["F1"])
        with patch("review.urlopen", return_value=io.BytesIO(json.dumps(model_response(report)).encode())):
            self.assertEqual(request_review("fake-key", payload, 1), report)
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["coverage"]["layout"]["finding_ids"], report["coverage"]["imagery"]["finding_ids"])

    def test_rejects_incomplete_refused_and_malformed_model_responses(self):
        payload = build_payload("test-model", [image_input(PNG)], {}, {})
        cases = [
            ({"status": "incomplete"}, "did not finish"),
            ({"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "raw upstream response"}
            ]}]}, "declined"),
            (model_response({"layout": "raw upstream response"}), "invalid report"),
        ]
        for response, message in cases:
            with self.subTest(message=message), patch(
                "review.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
            ):
                with self.assertRaisesRegex(ReviewError, message):
                    request_review("fake-key", payload, 1)
