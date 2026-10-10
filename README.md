# Bulk Certificate Generator API

A Python API that accepts one request for many recipients, creates a personalized PDF certificate for each recipient, records each outcome, and provides job status and certificate downloads. It uses FastAPI, SQLAlchemy, SQLite by default, and ReportLab.

## Features

- Bulk certificate generation with one fixed PDF design.
- Streamlit CSV upload with a downloadable template and preflight row validation.
- Request validation and a maximum batch size of 1,000 recipients.
- Optional idempotency keys to prevent duplicate jobs when a client retries a request.
- Duplicate email detection within an uploaded or pasted batch to prevent accidental duplicate certificates.
- Search groups repeat certificates for the same email and course while keeping each job's download available.
- A relational record for each job and each recipient result.
- Background bulk processing with per-recipient progress saved as each candidate finishes.
- Paginated audit history for job and certificate state changes.
- Batch cancellation that finishes the current PDF and marks the remaining recipients as cancelled.
- Per-recipient failure isolation and job success/failure counts.
- Retry failed certificate generations without resubmitting the full batch.
- Candidate score tracking with a configurable pass mark (60% by default).
- By default, passing candidates receive completion certificates and below-threshold candidates receive participation certificates; an explicit option can withhold certificates from below-threshold candidates.
- Individual PDF downloads and a ZIP containing only successful certificates.
- A unique certificate ID and scannable QR code on every generated PDF, backed by a verification endpoint.
- Automated tests for validation, duplicate recipients, idempotency, CSV import, generation, status, failure isolation, and retrieval.

## Requirements and setup

Requires Python 3.10 or newer.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

SQLite is used by default. The database file `certificates.db` and generated PDFs are created locally and ignored by Git. To use another SQLAlchemy-compatible relational database, set `DATABASE_URL`, for example `postgresql+psycopg://user:password@localhost/certificates` (and install its driver).

## Run the application

```bash
uvicorn app.main:app --reload
```

The service is available at `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs`; the health check is `GET /health`.

To enable API key authentication, set a secret before starting the service. In PowerShell:

```powershell
$env:CERTIFICATE_API_KEY = "replace-with-a-long-random-secret"
uvicorn app.main:app --reload
```

When configured, private `/api` routes require `X-API-Key: <secret>`. Health checks and certificate verification remain public so deployment probes and QR scans continue to work. If the variable is unset, authentication is disabled for convenient local development. Keep the key out of source control and use a secret manager for deployed environments.

## Run the Streamlit testing UI

Keep the API running in one PowerShell window. In a second window, from the project folder run:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run streamlit_app.py
```

Open the local URL printed by Streamlit (usually `http://localhost:8501`). The page lets you enter a course and date, paste recipient rows or upload a CSV, choose whether certificates require a passing score, submit a generation job, watch its progress, cancel active work, inspect audit history, download successful PDFs, look up a job by ID, and search candidate/certificate records. Pass-mark gating is off by default. Search results are paginated. The API URL can be changed in the sidebar or supplied through the `CERTIFICATE_API_BASE_URL` environment variable when starting Streamlit.

CSV uploads are checked for malformed rows, invalid email addresses, duplicate email addresses, and batch-size limits before generation starts. A downloadable CSV template is provided in the UI. Use these columns in this order (the header row is optional):

```csv
name,email,score_percent
Asha Rao,asha@example.com,85
Ravi Kumar,ravi@example.com,52
```

Names containing commas should be quoted according to standard CSV rules. Email and score may be left blank; an unscored candidate receives a participation certificate. Scores are required only when pass-mark gating is enabled. Duplicate non-empty emails are rejected without case sensitivity; fix the listed rows and upload again.

The score column can also be omitted when scores are unavailable:

```csv
name,email
Taylor Morgan,taylor@example.com
Jordan Lee,
```

## API

### Submit a bulk generation request

`POST /api/jobs/`. Set `pass_mark` from 0 to 100 (default 60). `score_percent` is optional and, when present, must be from 0 to 100. By default (`require_pass: false`), candidates at or above the pass mark receive completion certificates, candidates below it receive participation certificates, and candidates without a score receive participation certificates. Set `require_pass: true` to withhold certificates from below-threshold candidates; every recipient must have a score, passing candidates receive completion certificates, and below-threshold candidates are recorded as `NOT_PASSED`.

If `CERTIFICATE_API_KEY` is configured, send it in the `X-API-Key` request header for all `/api` endpoints except public certificate verification. The Streamlit tester has a password field for the key and also reads it from the same environment variable.

```json
{
  "course_name": "Python Fundamentals",
  "completion_date": "2026-10-09",
  "pass_mark": 60,
  "require_pass": false,
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com", "score_percent": 85},
    {"name": "Ravi Kumar", "email": "ravi@example.com", "score_percent": 52}
  ]
}
```

Example with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: course-python-2026-batch-001" \
  -d '{"course_name":"Python Fundamentals","completion_date":"2026-10-09","pass_mark":60,"recipients":[{"name":"Asha Rao","email":"asha@example.com","score_percent":85}]}'
```

For safe retries after a timeout, send a unique `Idempotency-Key` header for the logical batch and reuse that same key only when retrying the identical request. The API returns the existing job instead of generating duplicates. Reusing a key with a different request body returns `409 Conflict`. Keys are stored with the job record, so this protection persists across API restarts. The header is optional; requests without it create a new job each time.

The response includes the job ID, pass mark, whether pass gating was enabled, counts for passed/not-passed/unscored candidates and generated/failed certificates, plus each recipient's score, outcome, and `certificate_type` (`COMPLETION` or `PARTICIPATION`) when a PDF was issued. An absent score and its `passed` value appear as `null`; the certificate type is participation. The certificate title and wording match its type. Scores and pass marks remain in job results rather than on the certificate. Successful results include a `download_url`; only below-threshold results in gated jobs have status `NOT_PASSED` and no download URL.

### Check job status and results

`POST /api/jobs/` returns `202 Accepted` with the new job ID and an initial `PROCESSING` snapshot. The API schedules generation with FastAPI `BackgroundTasks`; each recipient's result is committed as soon as it finishes, so a failure does not block other candidates and progress can be polled. `GET /api/jobs/{job_id}` returns the latest job summary and recipient results. Its `processed` count excludes recipients still in `PROCESSING`, and `progress_percent` reports the processed share of the batch. Final status is `COMPLETED` if all certificates succeeded, or `COMPLETED_WITH_ERRORS` if one or more generations failed. A cancelled job moves through `CANCELLING` to `CANCELLED`; unstarted recipients have result status `CANCELLED`. A below-pass candidate in a gated job has `NOT_PASSED` and counts as processed, but does not count as a generation failure.

If one or more PDFs failed, call `POST /api/jobs/{job_id}/retry-failed`. It returns `202 Accepted` and reprocesses only results in `FAILED` state, keeping each candidate's existing certificate ID and the original batch size. A job that is active or has no failed results returns `409 Conflict`. Poll the job status endpoint to follow the retry. The Streamlit UI offers the same action for completed jobs with failures.

To stop a large batch, call `POST /api/jobs/{job_id}/cancel`. A running PDF is allowed to finish; the worker then marks remaining recipients `CANCELLED` and closes the job with status `CANCELLED`. A completed job cannot be cancelled (`409 Conflict`). The Streamlit UI shows a cancel action while a job is active.

`GET /api/jobs/{job_id}/events?offset=0&limit=100` returns a paginated audit history. Events record job creation, each recipient's generated/failed/not-issued/cancelled outcome, retry requests, cancellation requests, and the final job state. Events include timestamps and certificate IDs where relevant, without storing extra recipient personal data.

### Search candidate and certificate records

`GET /api/certificates/search` returns paginated results. Use `q` to search candidate names, emails, or course names; `course` to narrow by course; `passed=true|false` to filter assessment outcomes; `status=PROCESSING|SUCCESS|NOT_PASSED|FAILED|CANCELLED` to filter certificate outcomes; and `score_min`/`score_max` to filter scores. `offset` and `limit` control pagination (maximum limit is 200). For example:

```text
GET /api/certificates/search?q=asha&passed=true&score_min=60&limit=25
```

Each result includes candidate, course, score (nullable), assessment outcome (nullable when unscored), certificate type, certificate status, and an individual download URL when a certificate exists. In the Streamlit UI, historical certificates for the same email and course appear under one candidate group with their separate job IDs and downloads. Records without email addresses remain separate because a name alone cannot safely identify a person.

### Retrieve a certificate

`GET /api/certificates/{certificate_id}/download` returns the PDF for a successful result. A missing ID returns `404`; a known result without a generated certificate (not passed or generation failed) returns `409`.

### Verify a certificate

Every issued PDF prints its unique certificate ID and includes a QR code that opens `GET /api/certificates/{certificate_id}/verify`. The endpoint returns `valid: true` plus the recipient name, course, and completion date only when the database record and generated PDF are present. Unknown IDs return `404`; records without an issued PDF return `valid: false`.

The QR code uses `CERTIFICATE_PUBLIC_BASE_URL`, which defaults to `http://127.0.0.1:8000` for local development. Before generating certificates on a deployed site, set it to the public HTTPS base URL, for example `https://certificates.example.com`. Set it before generation; previously generated QR codes keep the URL embedded in their PDFs.

### Download all successful certificates

`GET /api/jobs/{job_id}/certificates.zip` returns a ZIP archive containing all certificates whose PDF generation succeeded. Below-threshold candidates receive participation certificates by default and are excluded only when pass gating is enabled. Generation failures are always excluded. A job with no downloadable certificates returns `409`.

## Architecture and design decisions

- `app/routes.py` handles HTTP requests, validation through Pydantic schemas, database persistence, and per-recipient error isolation.
- `app/main.py` enforces optional API key authentication for private API routes using constant-time key comparison; health and QR verification remain public.
- `app/models.py` stores generation jobs, ordered recipient outcomes, and append-only job audit events in relational tables.
- `app/certificates.py` renders one fixed landscape PDF design using ReportLab; its title and wording identify participation or completion according to the job mode.
- Certificate IDs are the unique UUIDs already assigned to generation results; the same ID appears in the PDF and verification URL.
- Generated PDFs are stored under `generated/{job_id}/`; set `CERTIFICATE_OUTPUT_DIR` to change the storage directory.
- Scores and pass outcomes are tracked for reporting. By default, a score at or above `pass_mark` earns a completion certificate and a score below it earns a participation certificate. Optional `require_pass` gating withholds the latter. Both values are percentages from 0 to 100. The supplied score is trusted input and is not independently verified against an exam system. Duplicate emails in one job are rejected case-insensitively; repeated submissions in different jobs remain available as separate history.
- Search uses the relational candidate/job records and returns bounded pages instead of loading every certificate into the UI at once.
- FastAPI `BackgroundTasks` keeps the implementation simple while the API records and exposes progress per recipient. It runs in the API process, so a process restart can interrupt an active job; a durable queue such as Celery or RQ with a worker would be appropriate for production deployments that need restart recovery or multiple workers.
- Retrying reuses the original result record and certificate ID, so a recovered recipient remains attached to the original batch and its history. Only `FAILED` generations can be retried; successful, processing, and `NOT_PASSED` results are left as they are.
- Cancellation is cooperative: it is checked between recipient generations and does not interrupt a PDF renderer that is already running. The event endpoint provides an ordered, paginated record of job and recipient transitions.
- An optional idempotency key and SHA-256 digest of the validated request are stored on each job. Matching retries return the stored job, while changed payloads using the same key are rejected; a unique database index protects concurrent submissions.
- SQLite keeps local setup simple; `DATABASE_URL` supports configuring another SQLAlchemy-compatible relational database.

## Tests

```bash
pytest
```

The tests use an isolated in-memory SQLite database and temporary PDF directory. They cover job creation, idempotent submissions and key conflicts, request and duplicate-email validation, CSV import checks, PDF generation, job status, progress, audit history and cancellation, individual generation failure and retry, eligibility decisions, individual and ZIP downloads, not-found behavior, search filters, and pagination.

## Learning and future scope

This project demonstrates request validation, REST API design, relational persistence, API key authentication, PDF generation, assessment eligibility rules, audit history, and resilient batch processing. Future improvements could add a durable background worker, cloud object storage, configurable certificate templates, and integration with an independently verified assessment system.
