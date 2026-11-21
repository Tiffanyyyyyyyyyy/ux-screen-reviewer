# UX Screen Reviewer

A local Flask app for reviewing product screenshots individually or as a flow.

## Setup and run

Use Python 3.10 or newer. From this directory on macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app run --host 127.0.0.1 --port 5000
```

Open http://127.0.0.1:5000. Under **Model connection**, paste your OpenAI API key
and select **Connect**. The app tests supported vision models in order of standard
token pricing, starting with `gpt-6-luna`. If OpenAI reports that a model is
unavailable to your project, it tries the next one. The connection test sends a
synthetic image and may incur a small API charge. The selected model is shown
after the check succeeds. Flask holds the connection in server memory until the
process stops. Stop Flask with Ctrl+C. **Load sample** works without a key.

### Optional server environment setup

To start with a connection configured, copy the example, set the key
in `.env`, then load the variables before starting Flask:

```sh
cp .env.example .env
# Edit .env and set OPENAI_API_KEY. Leave OPENAI_MODEL blank for automatic selection.
set -a
source ./.env
set +a
python -m flask --app app run --host 127.0.0.1 --port 5000
```

With an environment key and no model override, the app selects a model on the
first review. A successful UI connection takes precedence until Flask restarts.

If the connection fails, follow the diagnosis shown in the app. It distinguishes
key rejection, project permissions, billing or quota, rate limits, model access,
and network errors. If OpenAI provides a request ID, include it when contacting
support. See the [OpenAI API error guide](https://developers.openai.com/api/docs/guides/error-codes).

## Review screenshots

Add one to four PNG, JPEG, or WebP screenshots, up to 8 MiB each. Files can be
added in multiple selections and removed individually. Add flow screenshots
in order and include equivalent screenshots in different languages when you want
a translation comparison. The model infers locales from visible text and can
report an unknown locale when the evidence is insufficient. English is optional.

Add any known audience, flow stage, product rules, intended takeaway, and next
action. Under **Additional evidence**, you can paste source copy or describe an
observed tap result. Select **Review screen**. A failed request keeps the
screenshots and text available for retry.

The report shows each screenshot with its readout, a list of fixes sorted by
severity, six coverage checks, and any open questions. Coverage links point to
the same fix when one issue affects several checks. A single-locale review
still assesses wording and fit. Translation fidelity is unchecked without
source copy. **Load sample** previews the report layout without an API key.

Use **Download text** to save the visible report as a UTF-8 text file. To clarify
an open question, enter an answer and select **Re-evaluate report**. The app
resends the original screenshots, context, and intent with your answers. Use
**Add screenshots** and run a new review to include more screens in the current
set. **Refresh workspace** and **Start new review** clear the screenshots and report
while keeping the API connection, context, and intent. They work without reloading
the page.

The app holds uploads in memory and sends them to OpenAI only when you request
a review. It requests `store: false` and does not write screenshots, prompts,
model responses, or keys to application logs. Keep `.env` and screenshot copies
outside Git. Run the server on loopback without debug mode.

## Tests

```sh
python -m unittest discover -s tests -v
```

The tests use mocked OpenAI API responses and do not require a key or network.

API references: [Images and vision](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
and [API pricing](https://developers.openai.com/api/docs/pricing).
