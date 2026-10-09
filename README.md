# Bulk Certificate Generator API

A Python API that accepts one request for many recipients, creates a personalized PDF certificate for each recipient, records each outcome, and provides job status and certificate downloads. It uses FastAPI, SQLAlchemy, SQLite by default, and ReportLab.

## Features

- Bulk certificate generation with one fixed PDF design.
- Request validation and a maximum batch size of 1,000 recipients.
- A relational record for each job and each recipient result.
- Per-recipient failure isolation and job success/failure counts.
- Job status and individual PDF download endpoints.
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

## API

### Submit a bulk generation request

`POST /api/jobs/`

```json
{
  "course_name": "Python Fundamentals",
  "completion_date": "2026-10-09",
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "Ravi Kumar", "email": "ravi@example.com"}
  ]
}
```

Example with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/ \
  -H "Content-Type: application/json" \
  -d '{"course_name":"Python Fundamentals","completion_date":"2026-10-09","recipients":[{"name":"Asha Rao","email":"asha@example.com"}]}'
```

The response includes the job ID, counts, and each recipient outcome. Successful results include a `download_url`; failed results include a safe error message.

### Check job status and results

`GET /api/jobs/{job_id}` returns the job summary and its recipient results. Status is `COMPLETED` if all certificates succeeded, or `COMPLETED_WITH_ERRORS` if one or more failed. Processing is synchronous in this version, so the POST response is the completed result; the status endpoint remains available for later lookup.

### Retrieve a certificate

`GET /api/certificates/{certificate_id}/download` returns the PDF for a successful result. A missing ID returns `404`; a known failed result returns `409`.

## Architecture and design decisions

- `app/routes.py` handles HTTP requests, validation through Pydantic schemas, database persistence, and per-recipient error isolation.
- `app/models.py` stores generation jobs and ordered recipient outcomes in relational tables.
- `app/certificates.py` renders the fixed landscape PDF using ReportLab.
- Generated PDFs are stored under `generated/{job_id}/`; set `CERTIFICATE_OUTPUT_DIR` to change the storage directory.
- Synchronous processing keeps the deadline-sized implementation simple and easy to run. A background queue such as Celery or RQ would be a suitable next step for larger batches or long-running rendering.
- SQLite keeps local setup simple; `DATABASE_URL` supports configuring another SQLAlchemy-compatible relational database.

## Tests

```bash
pytest
```

The tests use an isolated in-memory SQLite database and temporary PDF directory. They cover job creation, request validation, PDF generation, job status, individual generation failure, download retrieval, and not-found behavior.

## Learning and future scope

This project demonstrates request validation, REST API design, relational persistence, PDF generation, and resilient batch processing. Future improvements could add background workers, authentication, configurable certificate templates, cloud object storage, job cancellation, and audit logging.
