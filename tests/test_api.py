"""API tests for required generation behavior."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app import routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(routes, "SessionLocal", TestingSession)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("CERTIFICATE_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.delenv("CERTIFICATE_API_KEY", raising=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # The app has no lifespan hooks; avoiding the context manager also keeps
    # Windows' long-lived AnyIO portal from interfering with local test runs.
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def payload():
    return {
        "course_name": "Python Fundamentals",
        "completion_date": "2026-10-09",
        "recipients": [
            {"name": "Asha Rao", "email": "asha@example.com", "score_percent": 85},
            {"name": "Ravi Kumar", "email": "ravi@example.com", "score_percent": 72},
        ],
    }


def submit_job(client, data=None):
    response = client.post("/api/jobs/", json=data or payload())
    job_id = response.json()["id"]
    return response, client.get(f"/api/jobs/{job_id}")


def completed_job(client, data=None):
    _, response = submit_job(client, data)
    return response.json()


def test_create_bulk_job_generates_certificates_and_status(client):
    accepted, response = submit_job(client)
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "PROCESSING"
    assert accepted.json()["processed"] == 0
    job = response.json()
    assert job["total"] == 2
    assert job["succeeded"] == 2
    assert job["failed"] == 0
    assert job["status"] == "COMPLETED"
    assert all(result["status"] == "SUCCESS" for result in job["results"])
    assert all(result["certificate_type"] == "COMPLETION" for result in job["results"])

    status = client.get(f"/api/jobs/{job['id']}")
    assert status.status_code == 200
    assert status.json()["succeeded"] == 2


def test_idempotency_key_returns_existing_job_for_repeated_request(client):
    headers = {"Idempotency-Key": "event-registration-batch-2026-10-10"}
    first = client.post("/api/jobs/", json=payload(), headers=headers)
    second = client.post("/api/jobs/", json=payload(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert client.get("/api/certificates/search", params={"limit": 10}).json()["total"] == 2


def test_idempotency_key_rejects_different_request_body(client):
    headers = {"Idempotency-Key": "same-key"}
    client.post("/api/jobs/", json=payload(), headers=headers)
    changed = payload()
    changed["course_name"] = "Different Course"

    response = client.post("/api/jobs/", json=changed, headers=headers)

    assert response.status_code == 409
    assert "different request body" in response.json()["detail"]


def test_configured_api_key_protects_private_routes_and_allows_public_verification(
    client, monkeypatch
):
    monkeypatch.setenv("CERTIFICATE_API_KEY", "test-secret-key")
    assert client.get("/api/certificates/search").status_code == 401
    assert client.get("/health").status_code == 200

    assert client.post("/api/jobs/", json=payload(), headers={"X-API-Key": "wrong"}).status_code == 401
    accepted = client.post("/api/jobs/", json=payload(), headers={"X-API-Key": "test-secret-key"})
    assert accepted.status_code == 202
    job = accepted.json()

    assert client.get(f"/api/jobs/{job['id']}").status_code == 401
    authorized_job = client.get(
        f"/api/jobs/{job['id']}", headers={"X-API-Key": "test-secret-key"}
    )
    assert authorized_job.status_code == 200
    certificate_id = job["results"][0]["certificate_id"]
    assert client.get(f"/api/certificates/{certificate_id}/download").status_code == 401
    assert client.get(f"/api/certificates/{certificate_id}/verify").json()["valid"] is True


def test_job_exposes_live_progress_while_worker_is_running(client, monkeypatch):
    real_worker = routes.process_generation_job
    monkeypatch.setattr(routes, "process_generation_job", lambda job_id: None)

    accepted = client.post("/api/jobs/", json=payload())

    assert accepted.status_code == 202
    snapshot = accepted.json()
    assert snapshot["status"] == "PROCESSING"
    assert snapshot["processed"] == 0
    assert snapshot["progress_percent"] == 0
    assert all(row["status"] == "PROCESSING" for row in snapshot["results"])

    real_worker(snapshot["id"])
    completed = client.get(f"/api/jobs/{snapshot['id']}").json()
    assert completed["status"] == "COMPLETED"
    assert completed["processed"] == 2
    assert completed["progress_percent"] == 100


def test_job_audit_history_records_creation_generation_and_completion(client):
    job = completed_job(client)

    response = client.get(f"/api/jobs/{job['id']}/events")

    assert response.status_code == 200
    events = response.json()
    event_types = [event["event_type"] for event in events["events"]]
    assert events["total"] == 4
    assert event_types[0] == "JOB_CREATED"
    assert event_types.count("CERTIFICATE_GENERATED") == 2
    assert event_types[-1] == "COMPLETED"


def test_processing_job_can_be_cancelled_between_recipients(client, monkeypatch):
    real_worker = routes.process_generation_job
    monkeypatch.setattr(routes, "process_generation_job", lambda job_id: None)
    accepted = client.post("/api/jobs/", json=payload())
    job_id = accepted.json()["id"]

    cancellation = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancellation.status_code == 202
    assert cancellation.json()["status"] == "CANCELLING"

    real_worker(job_id)
    cancelled_job = client.get(f"/api/jobs/{job_id}").json()
    assert cancelled_job["status"] == "CANCELLED"
    assert cancelled_job["processed"] == cancelled_job["total"]
    assert all(result["status"] == "CANCELLED" for result in cancelled_job["results"])
    events = client.get(f"/api/jobs/{job_id}/events").json()
    assert "JOB_CANCELLATION_REQUESTED" in [event["event_type"] for event in events["events"]]
    assert "JOB_CANCELLED" in [event["event_type"] for event in events["events"]]


def test_input_validation_rejects_empty_recipient_list(client):
    invalid = payload()
    invalid["recipients"] = []
    response = client.post("/api/jobs/", json=invalid)
    assert response.status_code == 422


def test_input_validation_rejects_duplicate_recipient_emails(client):
    data = payload()
    data["recipients"][1]["email"] = "ASHA@example.com"

    response = client.post("/api/jobs/", json=data)

    assert response.status_code == 422
    assert "repeats the email address" in response.text


def test_generation_failure_is_isolated_to_one_recipient(client, monkeypatch):
    real_generate = routes.generate_certificate

    def fail_for_ravi(**kwargs):
        if kwargs["recipient_name"] == "Ravi Kumar":
            raise RuntimeError("simulated renderer failure")
        return real_generate(**kwargs)

    monkeypatch.setattr(routes, "generate_certificate", fail_for_ravi)
    _, response = submit_job(client)
    job = response.json()
    assert job["succeeded"] == 1
    assert job["failed"] == 1
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert [result["status"] for result in job["results"]] == ["SUCCESS", "FAILED"]


def test_retry_failed_generation_reuses_result_and_completes_job(client, monkeypatch):
    real_generate = routes.generate_certificate
    attempts = {"Asha Rao": 0}

    def fail_once(**kwargs):
        if kwargs["recipient_name"] == "Asha Rao" and attempts["Asha Rao"] == 0:
            attempts["Asha Rao"] += 1
            raise RuntimeError("temporary renderer failure")
        return real_generate(**kwargs)

    monkeypatch.setattr(routes, "generate_certificate", fail_once)
    original = completed_job(client)
    failed_id = original["results"][0]["certificate_id"]
    assert original["status"] == "COMPLETED_WITH_ERRORS"
    assert original["failed"] == 1

    accepted = client.post(f"/api/jobs/{original['id']}/retry-failed")
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "PROCESSING"
    retried = client.get(f"/api/jobs/{original['id']}").json()

    assert retried["status"] == "COMPLETED"
    assert retried["total"] == original["total"]
    assert retried["succeeded"] == 2
    assert retried["failed"] == 0
    assert retried["results"][0]["certificate_id"] == failed_id
    assert retried["results"][0]["status"] == "SUCCESS"


def test_retry_rejects_job_without_retryable_failures(client, monkeypatch):
    completed = completed_job(client)
    assert client.post(f"/api/jobs/{completed['id']}/retry-failed").status_code == 409

    monkeypatch.setattr(routes, "process_generation_job", lambda job_id: None)
    in_progress = client.post("/api/jobs/", json=payload()).json()
    response = client.post(f"/api/jobs/{in_progress['id']}/retry-failed")
    assert response.status_code == 409


def test_download_generated_certificate(client):
    created = completed_job(client)
    certificate_id = created["results"][0]["certificate_id"]
    downloaded = client.get(f"/api/certificates/{certificate_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.content.startswith(b"%PDF")


def test_certificate_verification_returns_issued_identity(client):
    created = completed_job(client)
    certificate_id = created["results"][0]["certificate_id"]

    response = client.get(f"/api/certificates/{certificate_id}/verify")

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "certificate_id": certificate_id,
        "recipient_name": "Asha Rao",
        "course_name": "Python Fundamentals",
        "completion_date": "2026-10-09",
        "message": "This certificate is valid.",
    }


def test_non_passing_recipient_does_not_have_a_valid_certificate(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"][0]["score_percent"] = 40
    created = completed_job(client, data)
    certificate_id = created["results"][0]["certificate_id"]

    response = client.get(f"/api/certificates/{certificate_id}/verify")

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["recipient_name"] is None


def test_unknown_job_and_certificate_return_404(client):
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/certificates/missing/download").status_code == 404
    assert client.get("/api/certificates/missing/verify").status_code == 404


def test_failed_certificate_cannot_be_downloaded(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(routes, "generate_certificate", fail)
    created = completed_job(client)
    certificate_id = created["results"][0]["certificate_id"]
    response = client.get(f"/api/certificates/{certificate_id}/download")
    assert response.status_code == 409


def test_candidate_below_pass_mark_does_not_receive_certificate(client):
    data = payload()
    data["require_pass"] = True
    data["pass_mark"] = 60
    data["recipients"][1]["score_percent"] = 59
    accepted, response = submit_job(client, data)
    assert accepted.status_code == 202
    job = response.json()
    assert job["passed"] == 1
    assert job["not_passed"] == 1
    assert job["succeeded"] == 1
    failed_candidate = job["results"][1]
    assert failed_candidate["status"] == "NOT_PASSED"
    assert failed_candidate["passed"] is False
    assert failed_candidate["download_url"] is None


def test_zip_contains_only_successful_certificates(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"][1]["score_percent"] = 40
    job = completed_job(client, data)
    response = client.get(f"/api/jobs/{job['id']}/certificates.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    from io import BytesIO
    from zipfile import ZipFile

    with ZipFile(BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 1
        assert archive.namelist()[0] == f"{job['results'][0]['certificate_id']}.pdf"


def test_score_outside_range_is_rejected(client):
    data = payload()
    data["recipients"][0]["score_percent"] = 101
    assert client.post("/api/jobs/", json=data).status_code == 422


def test_zip_for_job_without_passed_candidates_returns_conflict(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"] = [{"name": "Ravi Kumar", "score_percent": 20}]
    job = completed_job(client, data)
    response = client.get(f"/api/jobs/{job['id']}/certificates.zip")
    assert response.status_code == 409


def test_search_by_candidate_name_and_course(client):
    created = completed_job(client)
    response = client.get("/api/certificates/search", params={"q": "asha", "course": "Python"})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    assert page["results"][0]["recipient_name"] == "Asha Rao"
    assert page["results"][0]["job_id"] == created["id"]
    assert page["results"][0]["download_url"].endswith("/download")


def test_search_filters_not_passed_candidates(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"][1]["score_percent"] = 40
    completed_job(client, data)
    response = client.get(
        "/api/certificates/search", params={"passed": "false", "status": "NOT_PASSED"}
    )
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    assert page["results"][0]["recipient_name"] == "Ravi Kumar"
    assert page["results"][0]["passed"] is False


def test_default_generation_issues_certificate_below_pass_mark(client):
    data = payload()
    data["recipients"] = [{"name": "Ravi Kumar", "score_percent": 20}]

    accepted, response = submit_job(client, data)

    assert accepted.status_code == 202
    assert accepted.json()["unscored"] == 0
    job = response.json()
    assert job["require_pass"] is False
    assert job["passed"] == 0
    assert job["not_passed"] == 1
    assert job["succeeded"] == 1
    assert job["results"][0]["status"] == "SUCCESS"
    assert job["results"][0]["certificate_type"] == "PARTICIPATION"
    assert job["results"][0]["download_url"]
    downloaded = client.get(job["results"][0]["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")


def test_score_is_optional_and_unscored_candidate_gets_participation_certificate(client):
    data = payload()
    data["recipients"] = [{"name": "Taylor Morgan"}]

    accepted, response = submit_job(client, data)

    assert accepted.status_code == 202
    job = response.json()
    result = job["results"][0]
    assert job["unscored"] == 1
    assert job["passed"] == 0
    assert job["not_passed"] == 0
    assert result["score_percent"] is None
    assert result["passed"] is None
    assert result["certificate_type"] == "PARTICIPATION"
    assert result["status"] == "SUCCESS"


def test_pass_gated_request_requires_a_score_for_each_candidate(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"] = [{"name": "Taylor Morgan"}]

    response = client.post("/api/jobs/", json=data)

    assert response.status_code == 422
    assert "needs a score when require_pass is enabled" in response.text


def test_default_generation_assigns_each_certificate_type_by_pass_mark(client, monkeypatch):
    generated_types = {}
    real_generate = routes.generate_certificate

    def capture_type(**kwargs):
        generated_types[kwargs["recipient_name"]] = kwargs["certificate_type"]
        return real_generate(**kwargs)

    monkeypatch.setattr(routes, "generate_certificate", capture_type)
    data = payload()
    data["recipients"][1]["score_percent"] = 40

    job = completed_job(client, data)

    assert generated_types == {"Asha Rao": "completion", "Ravi Kumar": "participation"}
    assert [result["certificate_type"] for result in job["results"]] == [
        "COMPLETION",
        "PARTICIPATION",
    ]


def test_pass_mark_gate_is_explicit_and_reported(client):
    data = payload()
    data["require_pass"] = True
    data["recipients"][1]["score_percent"] = 20

    job = completed_job(client, data)

    assert job["require_pass"] is True
    assert job["results"][1]["status"] == "NOT_PASSED"
    assert job["results"][1]["download_url"] is None


def test_search_paginates_results(client):
    completed_job(client)
    first_page = client.get("/api/certificates/search", params={"limit": 1, "offset": 0}).json()
    second_page = client.get("/api/certificates/search", params={"limit": 1, "offset": 1}).json()
    assert first_page["total"] == 2
    assert len(first_page["results"]) == 1
    assert len(second_page["results"]) == 1
    assert first_page["results"][0]["certificate_id"] != second_page["results"][0]["certificate_id"]


def test_search_rejects_reversed_score_range(client):
    response = client.get("/api/certificates/search", params={"score_min": 80, "score_max": 20})
    assert response.status_code == 422
