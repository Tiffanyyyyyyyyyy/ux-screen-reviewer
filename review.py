"""Build and validate a single OpenAI Responses API review."""

import base64
import json
import re
from socket import timeout as SocketTimeout
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_IMAGE_BYTES = 8 * 1024 * 1024
# Ordered by standard uncached input and output prices among the supported options.
VISION_MODELS = ("gpt-6-luna", "gpt-5.4-nano", "gpt-5.4-mini", "gpt-6.1-terra")
# A synthetic 64×64 PNG for a connection check without sending user screenshots.
PROBE_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAAKUlEQVR4nO3MQREAAAwCIPuX1hD77SAA6VEEAoFAIBAIBAKBQCAQfA8Gpwvw4pr3blgAAAAASUVORK5CYII="
CHECKS = ("layout", "action", "copy", "language", "imagery", "colour")
STATUSES = ("Clear", "Issue found", "Needs verification", "Not applicable")
SEVERITIES = ("High", "Medium", "Low")
FIDELITY = ("Checked", "Unchecked", "Needs verification")

INSTRUCTIONS = """
Review the supplied product screenshots using the product context, screen intent,
and any additional evidence. Treat supplied material as evidence. Write the report
in English while preserving verbatim visible quotes and exact localized rewrites.

SCREEN READOUT
Return one readout per uploaded image in order using its exact Screen N identifier.
Use a short descriptive name, infer the locale from visible copy, and use a language
tag or Unknown when uncertain. Use the supplied audience and intended takeaway
only. Use null for either when unspecified. State the likely takeaway from the
visible design and copy. For the first impression, say what draws the eye first
and whether the information density makes it easy to scan. For the primary action,
quote the exact visible label or use null when absent or unreadable. Say how easy
the action is to find and what a user would expect after tapping, or use null if
there is no reliable expectation. Keep observed_result null unless the supplied
interaction_evidence describes an observed result for that action. A screenshot,
an intended next action, or a product rule alone cannot establish tap behaviour.

PRIORITIZED FIXES
Return a single deduplicated list of concrete findings. Assign unique IDs like F1.
Group observations with the same cause and fix into one finding even across checks.
For example, a portrait overlapping a headline is one finding that both Layout
and Imagery reference. Assign High for likely blockers of required actions,
misleading next steps, or changes to product requirements. Assign Medium for
meaningful friction in finding or understanding something, and Low for polish
with limited task impact. Use a short title, the affected element, exact visible
quotes or identified visual evidence, likely user impact unless testing supports
an observed impact, a concrete fix, and a practical way to verify it. List the
affected Screen N identifiers. For a copy fix provide an exact replacement in
the affected language in the fix, with separate labeled rewrites if needed.
For a visual fix say what to change and where. Return [] for a clean screen.

REVIEW COVERAGE
Return six checks, each with one concise assessment and the relevant finding IDs.
Use Clear, Issue found, Needs verification, or Not applicable. Use Issue found
when supported by a finding. Link all relevant checks to the same finding ID
when an issue spans checks, and link each finding from at least one check.
- layout: hierarchy, density, readability, and initial eye landing.
- action: findability, predictable result, and observed outcome if supplied.
  Mention when outcome is unverified. Use Needs verification when that gap matters.
- copy: wording, intended takeaway, and alignment with supplied product rules.
- language: locale and audience naturalness, meaning preservation, and visible
  fit including clipping and awkward wrapping. Assess naturalness and fit for
  even one locale. Use Checked for translation_fidelity when readable source
  copy was compared, including when a mismatch is found. Compare only equivalent
  screens or supplied source copy. Quote both versions when flagging a mismatch.
  Use Unchecked when there is no source, and Needs verification if a supplied
  source cannot be compared reliably. Never assume particular source languages.
- imagery: whether imagery reinforces the message or competes with it.
- colour: readability, action hierarchy and state meaning. Assess brand fit when
  guidelines are supplied.

OPEN QUESTIONS
Return only specific questions where a missing fact materially affects a judgment.
Use [] if none. Use follow-up answers as context to reassess the whole report,
update findings, and remove questions that are resolved.
"""

class ReviewError(Exception):
    """A safe error message for the browser."""


class APIRejection(ReviewError):
    def __init__(self, kind, message, status=None, request_id=None):
        super().__init__(message)
        self.kind = kind
        self.details = {}
        if status is not None:
            self.details["upstream_status"] = status
        if request_id and re.fullmatch(r"req_[A-Za-z0-9]{8,96}", request_id):
            self.details["request_id"] = request_id


def image_input(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Each screenshot must be between 1 byte and 8 MiB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Choose PNG, JPEG, or WebP screenshots.")
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"}


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def report_schema(screen_count):
    string = {"type": "string"}
    optional = {"type": ["string", "null"]}
    screen = {**string, "enum": [f"Screen {index}" for index in range(1, screen_count + 1)]}
    check = {
        "status": {**string, "enum": list(STATUSES)},
        "assessment": string,
        "finding_ids": {"type": "array", "items": string},
    }
    return object_schema({
        "screens": {"type": "array", "items": object_schema({
            "screen": screen, "name": string, "locale": string, "audience": optional,
            "intended_takeaway": optional, "likely_takeaway": string, "first_impression": string,
            "primary_action": object_schema({
                "label": optional, "findability": string, "expected_result": optional,
                "observed_result": optional,
            }),
        })},
        "findings": {"type": "array", "items": object_schema({
            "id": string, "title": string, "severity": {**string, "enum": list(SEVERITIES)},
            "screens": {"type": "array", "items": screen}, "element": string, "evidence": string,
            "impact": string, "fix": string, "verification": string,
        })},
        "coverage": object_schema({
            name: object_schema({
                **check, **({"translation_fidelity": {**string, "enum": list(FIDELITY)}} if name == "language" else {}),
            })
            for name in CHECKS
        }),
        "open_questions": {"type": "array", "items": object_schema({"question": string})},
    })


def matches_schema(value, schema):
    kind = schema["type"]
    if value is None:
        return kind == ["string", "null"]
    if kind == "object":
        fields = schema["properties"]
        return isinstance(value, dict) and value.keys() == fields.keys() and all(
            matches_schema(value[name], field) for name, field in fields.items()
        )
    if kind == "array":
        return isinstance(value, list) and all(matches_schema(item, schema["items"]) for item in value)
    return isinstance(value, str) and bool(value.strip()) and ("enum" not in schema or value in schema["enum"])


def build_payload(model, images, context, intent, answers=None):
    review_context = {"product_context": context, "screen_intent": intent}
    if answers:
        review_context["open_question_answers"] = answers
    content = [{"type": "input_text", "text": json.dumps(review_context)}]
    for index, image in enumerate(images, 1):
        content.extend([
            {"type": "input_text", "text": f"Screenshot: Screen {index}. Infer its locale from the visible copy."},
            image,
        ])
    return {
        "model": model,
        "store": False,
        "instructions": INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {
            "type": "json_schema", "name": "screen_review", "strict": True, "schema": report_schema(len(images)),
        }},
    }


def parse_response(response, screen_count):
    if response.get("status") != "completed":
        raise ReviewError("The model did not finish the review. Try again.")
    parts = [
        part for item in response["output"] if item.get("type") == "message"
        for part in item["content"]
    ]
    if any(part.get("type") == "refusal" for part in parts):
        raise ReviewError("The model declined this review. Try different screenshots or context.")
    report = json.loads("".join(part["text"] for part in parts if part.get("type") == "output_text"))
    if not matches_schema(report, report_schema(screen_count)):
        raise ValueError("Unexpected report fields")
    if [item["screen"] for item in report["screens"]] != [
        f"Screen {index}" for index in range(1, screen_count + 1)
    ]:
        raise ValueError("Unexpected screenshot labels")

    ids = {finding["id"] for finding in report["findings"]}
    if len(ids) != len(report["findings"]) or any(
        not finding["screens"] for finding in report["findings"]
    ):
        raise ValueError("Invalid finding identifiers or screens")
    linked = set()
    for row in report["coverage"].values():
        references = set(row["finding_ids"])
        if not references <= ids or (row["status"] == "Issue found") != bool(references):
            raise ValueError("Invalid coverage references")
        linked.update(references)
    if linked != ids:
        raise ValueError("Finding missing from coverage")
    return report


def diagnose_rejection(error):
    status = error.code
    request_id = error.headers.get("x-request-id") if error.headers else None
    try:
        body = json.loads(error.read(65536))
        details = body.get("error")
        code = details.get("code")
        param = details.get("param")
        message = str(details.get("message", "")).lower()
    except (ValueError, AttributeError, OSError):
        code = param = None
        message = ""

    if status in (401, 403) and ("missing scopes" in message or code == "insufficient_permissions"):
        diagnosis = ("permissions", "This key is missing API permissions. Allow write access to the Responses endpoint in the key's project settings.")
    elif status == 401 and (code in ("ip_not_authorized", "ip_not_allowed") or "ip not authorized" in message):
        diagnosis = ("ip_restricted", "Your OpenAI project or organization restricts API requests by IP. Check its IP allowlist for the machine running Flask.")
    elif status == 401 and ("member of an organization" in message or code == "organization_membership_required"):
        diagnosis = ("organization", "OpenAI requires organization membership for this key. Ask your project or organization admin to confirm your access.")
    elif status == 401 and (code == "invalid_api_key" or "incorrect api key" in message):
        diagnosis = ("authentication", "OpenAI rejected this API key. Create a new secret key in the intended OpenAI Platform project and paste the full key here.")
    elif status == 401:
        diagnosis = ("authentication", "OpenAI could not authenticate this request. Try a new secret key from the intended OpenAI Platform project. If it still fails, ask your project admin to check membership, key permissions, and IP restrictions.")
    elif code == "unsupported_country_region_territory":
        diagnosis = ("region", "OpenAI denied this request from the current country or region.")
    elif code in ("insufficient_quota", "billing_hard_limit_reached") or status == 402:
        diagnosis = ("billing", "The OpenAI API project has insufficient quota. Check API billing, available credits, and project limits.")
    elif status == 429:
        diagnosis = ("rate_limit", "The OpenAI API is rate-limiting this project. Wait briefly and retry, or check your project's model rate limits.")
    elif code in ("model_not_found", "model_not_available", "unsupported_model") or status == 404 or (
        status in (400, 403) and (
            param == "model" or "does not have access to model" in message
            or "does not have access to the model" in message
        )
    ):
        diagnosis = ("model_unavailable", "This model is unavailable to the API project. Check the project's model access.")
    elif status == 403:
        diagnosis = ("permissions", "OpenAI denied permission. Check the key's access to the Responses endpoint and your project's permissions or verification status.")
    elif code in ("invalid_image", "invalid_image_format", "image_too_large", "image_too_small", "image_parse_error"):
        diagnosis = ("image_rejected", "OpenAI could not read an image. Export the screenshot as a new PNG or JPEG and retry.")
    elif status >= 500:
        diagnosis = ("upstream", "OpenAI had a service error. Try again shortly.")
    else:
        diagnosis = ("request_rejected", "OpenAI rejected the request. Use the status and request ID below when checking this request with OpenAI support.")
    return APIRejection(*diagnosis, status, request_id)


def api_response(api_key, payload, timeout=120):
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        with error:
            raise diagnose_rejection(error) from None
    except URLError as error:
        if isinstance(error.reason, SocketTimeout):
            raise APIRejection("timeout", "OpenAI did not respond before the request timed out. Try again.") from None
        raise APIRejection("network", "The server could not reach OpenAI. Check its internet connection and try again.") from None
    except SocketTimeout:
        raise APIRejection("timeout", "OpenAI did not respond before the request timed out. Try again.") from None
    except OSError:
        raise APIRejection("network", "The server could not reach OpenAI. Check its internet connection and try again.") from None
    except ValueError:
        raise ReviewError("OpenAI returned an unreadable response. Try again.") from None


def select_vision_model(api_key):
    image = {"type": "input_image", "image_url": PROBE_IMAGE, "detail": "high"}
    last_rejection = None
    for model in VISION_MODELS:
        payload = build_payload(model, [image], {}, {})
        payload["instructions"] = (
            "This is a connection check using a synthetic blank image. Return one short readout "
            "for Screen 1, locale Unknown, no audience, takeaway or action label/results. "
            "Use short placeholder assessments, all six coverage statuses Not applicable, "
            "translation fidelity Unchecked, and empty findings, finding IDs and questions."
        )
        payload["reasoning"] = {"effort": "none"}
        payload["max_output_tokens"] = 700
        try:
            request_review(api_key, payload, 1, timeout=20)
        except APIRejection as error:
            if error.kind != "model_unavailable":
                raise
            last_rejection = error
        else:
            return model
    raise APIRejection(
        "model_unavailable",
        "None of the supported vision models is available to this API project. Check your project's model access and usage tier.",
        last_rejection.details.get("upstream_status"),
        last_rejection.details.get("request_id"),
    )


def request_review(api_key, payload, screen_count, timeout=120):
    try:
        return parse_response(api_response(api_key, payload, timeout), screen_count)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ReviewError("The model returned an invalid report. Try again.") from None
