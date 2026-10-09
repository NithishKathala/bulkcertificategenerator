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
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("CERTIFICATE_OUTPUT_DIR", str(tmp_path / "generated"))

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def payload():
    return {
        "course_name": "Python Fundamentals",
        "completion_date": "2026-10-09",
        "recipients": [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com"},
        ],
    }


def test_create_bulk_job_generates_certificates_and_status(client):
    response = client.post("/api/jobs/", json=payload())
    assert response.status_code == 201
    job = response.json()
    assert job["total"] == 2
    assert job["succeeded"] == 2
    assert job["failed"] == 0
    assert job["status"] == "COMPLETED"
    assert all(result["status"] == "SUCCESS" for result in job["results"])

    status = client.get(f"/api/jobs/{job['id']}")
    assert status.status_code == 200
    assert status.json()["succeeded"] == 2


def test_input_validation_rejects_empty_recipient_list(client):
    invalid = payload()
    invalid["recipients"] = []
    response = client.post("/api/jobs/", json=invalid)
    assert response.status_code == 422


def test_generation_failure_is_isolated_to_one_recipient(client, monkeypatch):
    real_generate = routes.generate_certificate

    def fail_for_ravi(**kwargs):
        if kwargs["recipient_name"] == "Ravi Kumar":
            raise RuntimeError("simulated renderer failure")
        return real_generate(**kwargs)

    monkeypatch.setattr(routes, "generate_certificate", fail_for_ravi)
    response = client.post("/api/jobs/", json=payload())
    assert response.status_code == 201
    job = response.json()
    assert job["succeeded"] == 1
    assert job["failed"] == 1
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert [result["status"] for result in job["results"]] == ["SUCCESS", "FAILED"]


def test_download_generated_certificate(client):
    created = client.post("/api/jobs/", json=payload()).json()
    certificate_id = created["results"][0]["certificate_id"]
    downloaded = client.get(f"/api/certificates/{certificate_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.content.startswith(b"%PDF")


def test_unknown_job_and_certificate_return_404(client):
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/certificates/missing/download").status_code == 404


def test_failed_certificate_cannot_be_downloaded(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(routes, "generate_certificate", fail)
    created = client.post("/api/jobs/", json=payload()).json()
    certificate_id = created["results"][0]["certificate_id"]
    response = client.get(f"/api/certificates/{certificate_id}/download")
    assert response.status_code == 409
