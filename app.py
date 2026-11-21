import json
import os
import re
from io import BytesIO

from flask import Flask, Request, jsonify, render_template, request

from review import MAX_IMAGE_BYTES, ReviewError, build_payload, image_input, request_review, select_vision_model

MAX_SCREENSHOTS = 4
CONTEXT_FIELDS = ("audience", "flow_stage", "product_rules", "source_copy", "interaction_evidence")
INTENT_FIELDS = ("takeaway", "next_action")


class InMemoryRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        # Keep uploaded files out of Werkzeug's default disk-backed spool.
        return BytesIO()


app = Flask(__name__)
app.request_class = InMemoryRequest
app.config.update(
    MAX_CONTENT_LENGTH=MAX_SCREENSHOTS * MAX_IMAGE_BYTES + 384 * 1024,
    MAX_FORM_PARTS=MAX_SCREENSHOTS + len(CONTEXT_FIELDS) + len(INTENT_FIELDS) + 1,
)

# This local app has one active connection per Flask process.
ui_connection = None


def model_settings():
    return ui_connection or (
        os.getenv("OPENAI_API_KEY", "").strip(),
        os.getenv("OPENAI_MODEL", "").strip(),
    )


@app.get("/")
def index():
    key, model = model_settings()
    return render_template(
        "index.html", review_enabled=bool(key), model=model,
        max_screenshots=MAX_SCREENSHOTS, connection_verified=ui_connection is not None,
    )


@app.post("/api/connection")
def connect():
    global ui_connection
    if request.content_length and request.content_length > 4096:
        return jsonify(error="The connection details are too large."), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Enter an OpenAI API key."), 400
    key = data.get("api_key")
    if key == "":
        key = model_settings()[0]
    if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,1024}", key):
        return jsonify(error="Enter a valid API key without spaces."), 400
    try:
        model = select_vision_model(key)
    except ReviewError as error:
        return api_error(error)
    ui_connection = (key, model)
    return jsonify(model=model), 200, {"Cache-Control": "no-store"}


def api_error(error):
    return jsonify(error=str(error), **getattr(error, "details", {})), 502, {"Cache-Control": "no-store"}


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error=f"Upload up to {MAX_SCREENSHOTS} screenshots of 8 MiB or less each."), 413


@app.post("/api/review")
def review():
    global ui_connection
    api_key, model = model_settings()
    if not api_key:
        return jsonify(error="Connect to OpenAI before starting a review."), 503

    uploads = [file for file in request.files.getlist("screenshots") if file.filename]
    if not 1 <= len(uploads) <= MAX_SCREENSHOTS:
        return jsonify(error=f"Add between 1 and {MAX_SCREENSHOTS} screenshots."), 400
    context = {field: request.form.get(field, "").strip() for field in CONTEXT_FIELDS}
    intent = {field: request.form.get(field, "").strip() for field in INTENT_FIELDS}
    if any(len(value) > 2000 for value in (*context.values(), *intent.values())):
        return jsonify(error="Keep each context and intent field to 2,000 characters or less."), 400

    try:
        answers = json.loads(request.form.get("answers", "[]"))
    except ValueError:
        return jsonify(error="The follow-up answers could not be read."), 400
    if not isinstance(answers, list) or len(answers) > 30 or any(
        not isinstance(item, dict) or set(item) != {"question", "answer"}
        or not isinstance(item["question"], str) or not 0 < len(item["question"].strip()) <= 2000
        or not isinstance(item["answer"], str) or not 0 < len(item["answer"].strip()) <= 1000
        for item in answers
    ):
        return jsonify(error="Send up to 30 open-question answers of 1,000 characters each."), 400

    try:
        images = [image_input(file.read(MAX_IMAGE_BYTES + 1)) for file in uploads]
    except ValueError as error:
        return jsonify(error=str(error)), 400

    try:
        if not model:
            model = select_vision_model(api_key)
            # Preserve a connection set in another request while this check was running.
            if ui_connection is None:
                ui_connection = (api_key, model)
        report = request_review(api_key, build_payload(model, images, context, intent, answers), len(images))
    except ReviewError as error:
        return api_error(error)

    for readout in report["screens"]:
        readout["audience"] = context["audience"] or None
        readout["intended_takeaway"] = intent["takeaway"] or None
        if not context["interaction_evidence"]:
            readout["primary_action"]["observed_result"] = None
    if len(images) == 1 and not context["source_copy"]:
        report["coverage"]["language"]["translation_fidelity"] = "Unchecked"
    return jsonify(report=report), 200, {"Cache-Control": "no-store"}
