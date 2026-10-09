# Bulk Certificate Generator API

A Python API that accepts one request for many recipients, creates a personalized PDF certificate for each recipient, records each outcome, and provides job status and certificate downloads. It uses FastAPI, SQLAlchemy, SQLite by default, and ReportLab.

## Features

- Bulk certificate generation with one fixed PDF design.
- Request validation and a maximum batch size of 1,000 recipients.
- A relational record for each job and each recipient result.
- Per-recipient failure isolation and job success/failure counts.
- Candidate score tracking with a configurable pass mark (60% by default).
- Certificates are generated only for candidates who meet the pass mark.
- Individual PDF downloads and a ZIP containing only successful certificates.
- Automated tests for validation, generation, status, failure isolation, and retrieval.

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

## Run the Streamlit testing UI

Keep the API running in one PowerShell window. In a second window, from the project folder run:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run streamlit_app.py
```

Open the local URL printed by Streamlit (usually `http://localhost:8501`). The page lets you enter a course, date, and recipients, submit a generation job, inspect the outcomes, download successful PDFs, look up a job by ID, and search candidate/certificate records. Search results are paginated. The API URL can be changed in the sidebar.

## API

### Submit a bulk generation request

`POST /api/jobs/`. Set `pass_mark` from 0 to 100 (default 60). Every candidate must include `score_percent` from 0 to 100. A candidate below the pass mark is recorded as `NOT_PASSED` and receives no certificate.

```json
{
  "course_name": "Python Fundamentals",
  "completion_date": "2026-10-09",
  "pass_mark": 60,
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
  -d '{"course_name":"Python Fundamentals","completion_date":"2026-10-09","pass_mark":60,"recipients":[{"name":"Asha Rao","email":"asha@example.com","score_percent":85}]}'
```

The response includes the job ID, pass mark, counts for passed/not-passed candidates and generated/failed certificates, plus each recipient's score and outcome. Candidates who pass receive a certificate showing their score and the pass mark. Successful results include a `download_url`; candidates who do not pass have status `NOT_PASSED` and no download URL.

### Check job status and results

`GET /api/jobs/{job_id}` returns the job summary and its recipient results. Status is `COMPLETED` if all certificates succeeded, or `COMPLETED_WITH_ERRORS` if one or more failed. Processing is synchronous in this version, so the POST response is the completed result; the status endpoint remains available for later lookup.

### Search candidate and certificate records

`GET /api/certificates/search` returns paginated results. Use `q` to search candidate names, emails, or course names; `course` to narrow by course; `passed=true|false` to filter assessment outcomes; `status=SUCCESS|NOT_PASSED|FAILED` to filter certificate outcomes; and `score_min`/`score_max` to filter scores. `offset` and `limit` control pagination (maximum limit is 200). For example:

```text
GET /api/certificates/search?q=asha&passed=true&score_min=60&limit=25
```

Each result includes candidate, course, score, assessment outcome, certificate status, and an individual download URL when a certificate exists.

### Retrieve a certificate

`GET /api/certificates/{certificate_id}/download` returns the PDF for a successful result. A missing ID returns `404`; a known result without a generated certificate (not passed or generation failed) returns `409`.

### Download all successful certificates

`GET /api/jobs/{job_id}/certificates.zip` returns a ZIP archive containing certificates for candidates who passed and whose PDF generation succeeded. Candidates who did not pass and generation failures are excluded. A job with no downloadable certificates returns `409`.

## Architecture and design decisions

- `app/routes.py` handles HTTP requests, validation through Pydantic schemas, database persistence, and per-recipient error isolation.
- `app/models.py` stores generation jobs and ordered recipient outcomes in relational tables.
- `app/certificates.py` renders the fixed landscape PDF using ReportLab.
- Generated PDFs are stored under `generated/{job_id}/`; set `CERTIFICATE_OUTPUT_DIR` to change the storage directory.
- Candidate eligibility is determined by `score_percent >= pass_mark`; both values are percentages from 0 to 100. The supplied candidate score is trusted input and is not independently verified against an exam system.
- Search uses the relational candidate/job records and returns bounded pages instead of loading every certificate into the UI at once.
- Synchronous processing keeps the deadline-sized implementation simple and easy to run. A background queue such as Celery or RQ would be a suitable next step for larger batches or long-running rendering.
- SQLite keeps local setup simple; `DATABASE_URL` supports configuring another SQLAlchemy-compatible relational database.

## Tests

```bash
pytest
```

The tests use an isolated in-memory SQLite database and temporary PDF directory. They cover job creation, request validation, PDF generation, job status, individual generation failure, eligibility decisions, individual and ZIP downloads, not-found behavior, search filters, and pagination.

## Learning and future scope

This project demonstrates request validation, REST API design, relational persistence, PDF generation, assessment eligibility rules, and resilient batch processing. Future improvements could add background workers, authentication, configurable certificate templates, cloud object storage, job cancellation, integration with a verified assessment system, and audit logging.
