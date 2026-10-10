"""HTTP routes for bulk generation, status, and downloads."""

from datetime import date
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.certificates import generate_certificate, output_directory
from app.database import SessionLocal, get_db
from app.models import CertificateResult, GenerationJob, JobEvent
from app.schemas import (
    CertificateSearchPage,
    CertificateVerification,
    JobCreate,
    JobEventPage,
    JobOut,
)

router = APIRouter(prefix="/api", tags=["certificates"])


def _record_job_event(
    db: Session,
    job_id: str,
    event_type: str,
    certificate_id: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        JobEvent(
            id=str(uuid4()),
            job_id=job_id,
            event_type=event_type,
            certificate_id=certificate_id,
            detail=detail,
        )
    )


def _finish_cancelled_job(db: Session, job: GenerationJob) -> None:
    for result in job.results:
        if result.status == "PROCESSING":
            result.status = "CANCELLED"
            result.error_message = "Job cancelled before this certificate was generated."
            _record_job_event(db, job.id, "CERTIFICATE_CANCELLED", result.id)
    job.status = "CANCELLED"
    _record_job_event(db, job.id, "JOB_CANCELLED")
    db.commit()


@router.get("/certificates/search", response_model=CertificateSearchPage)
def search_certificates(
    q: str | None = Query(default=None, max_length=200, description="Search candidate name, email, or course"),
    course: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(PROCESSING|SUCCESS|NOT_PASSED|FAILED|CANCELLED)$"),
    passed: bool | None = None,
    score_min: float | None = Query(default=None, ge=0, le=100),
    score_max: float | None = Query(default=None, ge=0, le=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if score_min is not None and score_max is not None and score_min > score_max:
        raise HTTPException(status_code=422, detail="score_min must be less than or equal to score_max")

    query = db.query(CertificateResult, GenerationJob).join(
        GenerationJob, CertificateResult.job_id == GenerationJob.id
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                CertificateResult.recipient_name.ilike(pattern),
                CertificateResult.recipient_email.ilike(pattern),
                GenerationJob.course_name.ilike(pattern),
            )
        )
    if course and course.strip():
        query = query.filter(GenerationJob.course_name.ilike(f"%{course.strip()}%"))
    if status:
        query = query.filter(CertificateResult.status == status)
    has_score = CertificateResult.score_provided.is_(True)
    passed_expression = has_score & (CertificateResult.score_percent >= GenerationJob.pass_mark)
    if passed is True:
        query = query.filter(passed_expression)
    elif passed is False:
        query = query.filter(has_score, CertificateResult.score_percent < GenerationJob.pass_mark)
    if score_min is not None:
        query = query.filter(has_score, CertificateResult.score_percent >= score_min)
    if score_max is not None:
        query = query.filter(has_score, CertificateResult.score_percent <= score_max)

    total = query.count()
    rows = (
        query.order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc(), CertificateResult.position)
        .offset(offset)
        .limit(limit)
        .all()
    )
    results = [
        {
            "certificate_id": result.id,
            "job_id": job.id,
            "course_name": job.course_name,
            "completion_date": date.fromisoformat(job.completion_date),
            "recipient_name": result.recipient_name,
            "recipient_email": result.recipient_email,
            "score_percent": result.score_percent if result.score_provided else None,
            "pass_mark": job.pass_mark,
            "passed": (result.score_percent >= job.pass_mark) if result.score_provided else None,
            "certificate_type": (
                "COMPLETION"
                if result.score_provided and result.score_percent >= job.pass_mark
                else "PARTICIPATION"
            ) if result.status == "SUCCESS" else None,
            "status": result.status,
            "download_url": f"/api/certificates/{result.id}/download" if result.status == "SUCCESS" else None,
        }
        for result, job in rows
    ]
    return {"total": total, "offset": offset, "limit": limit, "results": results}


def _job_response(job: GenerationJob) -> dict:
    results = []
    for result in job.results:
        results.append(
            {
                "id": result.id,
                "recipient_name": result.recipient_name,
                "recipient_email": result.recipient_email,
                "score_percent": result.score_percent if result.score_provided else None,
                "passed": (result.score_percent >= job.pass_mark) if result.score_provided else None,
                "certificate_type": (
                    "COMPLETION"
                    if result.score_provided and result.score_percent >= job.pass_mark
                    else "PARTICIPATION"
                ) if result.status == "SUCCESS" else None,
                "status": result.status,
                "error_message": result.error_message,
                "download_url": f"/api/certificates/{result.id}/download" if result.status == "SUCCESS" else None,
            }
        )
    return {
        "id": job.id,
        "course_name": job.course_name,
        "completion_date": date.fromisoformat(job.completion_date),
        "pass_mark": job.pass_mark,
        "require_pass": job.require_pass,
        "status": job.status,
        "total": job.total,
        "passed": sum(result.score_provided and result.score_percent >= job.pass_mark for result in job.results),
        "not_passed": sum(result.score_provided and result.score_percent < job.pass_mark for result in job.results),
        "unscored": sum(not result.score_provided for result in job.results),
        "succeeded": job.succeeded,
        "failed": job.failed,
        "processed": sum(result.status != "PROCESSING" for result in job.results),
        "progress_percent": round(
            sum(result.status != "PROCESSING" for result in job.results) / job.total * 100
        ) if job.total else 100,
        "created_at": job.created_at,
        "results": results,
    }


def process_generation_job(job_id: str) -> None:
    """Generate each PDF in a separate session and commit recipient progress."""
    db = SessionLocal()
    try:
        job = (
            db.query(GenerationJob)
            .options(selectinload(GenerationJob.results))
            .filter_by(id=job_id)
            .first()
        )
        if job is None:
            return

        for result in job.results:
            db.refresh(job, attribute_names=["status"])
            if job.status == "CANCELLING":
                _finish_cancelled_job(db, job)
                return
            if result.status != "PROCESSING":
                continue
            if job.require_pass and result.score_provided and result.score_percent < job.pass_mark:
                result.status = "NOT_PASSED"
                result.error_message = "Candidate did not meet the pass mark; no certificate was generated."
                _record_job_event(db, job_id, "CERTIFICATE_NOT_ISSUED", result.id, "Below pass mark")
                db.commit()
                continue

            try:
                path = generate_certificate(
                    recipient_name=result.recipient_name,
                    course_name=job.course_name,
                    completion_date=job.completion_date,
                    job_id=job_id,
                    certificate_id=result.id,
                    certificate_type=(
                        "completion"
                        if result.score_provided and result.score_percent >= job.pass_mark
                        else "participation"
                    ),
                )
                result.status = "SUCCESS"
                result.filename = str(path.relative_to(output_directory()))
                job.succeeded += 1
                _record_job_event(db, job_id, "CERTIFICATE_GENERATED", result.id)
            except Exception:
                result.status = "FAILED"
                result.error_message = "Certificate generation failed. Please retry this recipient."
                job.failed += 1
                _record_job_event(db, job_id, "GENERATION_FAILED", result.id, result.error_message)
            db.commit()

        db.refresh(job, attribute_names=["status"])
        if job.status == "CANCELLING":
            _finish_cancelled_job(db, job)
            return
        job.status = (
            "COMPLETED_WITH_ERRORS"
            if job.failed or any(result.status == "CANCELLED" for result in job.results)
            else "COMPLETED"
        )
        _record_job_event(db, job_id, job.status)
        db.commit()
    finally:
        db.close()


@router.post("/jobs/", response_model=JobOut, status_code=202)
def create_job(
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="Idempotency-Key cannot be blank")
        request_body = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(request_body.encode("utf-8")).hexdigest()
        existing = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with a different request body",
                )
            response.status_code = 202 if existing.status == "PROCESSING" else 200
            return _job_response(existing)
    else:
        request_hash = None

    job_id = str(uuid4())
    job = GenerationJob(
        id=job_id,
        course_name=payload.course_name,
        completion_date=payload.completion_date.isoformat(),
        pass_mark=payload.pass_mark,
        require_pass=payload.require_pass,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status="PROCESSING",
        total=len(payload.recipients),
        succeeded=0,
        failed=0,
    )
    db.add(job)
    db.flush()
    _record_job_event(db, job_id, "JOB_CREATED", detail=f"Recipients: {len(payload.recipients)}")

    for position, recipient in enumerate(payload.recipients):
        certificate_id = str(uuid4())
        result = CertificateResult(
            id=certificate_id,
            job_id=job_id,
            position=position,
            recipient_name=recipient.name,
            recipient_email=str(recipient.email) if recipient.email else None,
            score_percent=recipient.score_percent if recipient.score_percent is not None else 0.0,
            score_provided=recipient.score_percent is not None,
            status="PROCESSING",
        )
        db.add(result)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key is not None:
            existing = (
                db.query(GenerationJob)
                .options(selectinload(GenerationJob.results))
                .filter_by(idempotency_key=idempotency_key)
                .first()
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used with a different request body",
                    )
                response.status_code = 202 if existing.status == "PROCESSING" else 200
                return _job_response(existing)
        raise
    db.refresh(job)
    job = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(id=job_id).one()
    response_payload = _job_response(job)
    db.expunge_all()
    db.close()
    background_tasks.add_task(process_generation_job, job_id)
    return response_payload


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return _job_response(job)


@router.get("/jobs/{job_id}/events", response_model=JobEventPage)
def get_job_events(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if not db.query(GenerationJob.id).filter_by(id=job_id).first():
        raise HTTPException(status_code=404, detail="Generation job not found")
    query = db.query(JobEvent).filter_by(job_id=job_id)
    total = query.count()
    events = query.order_by(JobEvent.created_at, JobEvent.id).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "events": events}


@router.post("/jobs/{job_id}/retry-failed", response_model=JobOut, status_code=202)
def retry_failed_results(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Requeue failed recipient results in an existing, inactive job."""
    job = (
        db.query(GenerationJob)
        .options(selectinload(GenerationJob.results))
        .filter_by(id=job_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.status == "PROCESSING":
        raise HTTPException(status_code=409, detail="This job is already processing")

    failed_results = [result for result in job.results if result.status == "FAILED"]
    if not failed_results:
        raise HTTPException(status_code=409, detail="This job has no failed certificates to retry")

    # Claim the completed job conditionally so concurrent retries cannot both
    # enqueue the same failed recipients.
    claimed = (
        db.query(GenerationJob)
        .filter(GenerationJob.id == job_id, GenerationJob.status != "PROCESSING")
        .update({GenerationJob.status: "PROCESSING"}, synchronize_session=False)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=409, detail="This job is already processing")

    for result in failed_results:
        result.status = "PROCESSING"
        result.error_message = None
    job.status = "PROCESSING"
    job.failed = 0
    _record_job_event(db, job_id, "JOB_RETRY_REQUESTED", detail=f"Recipients: {len(failed_results)}")
    db.commit()

    job = (
        db.query(GenerationJob)
        .options(selectinload(GenerationJob.results))
        .filter_by(id=job_id)
        .one()
    )
    response_payload = _job_response(job)
    db.expunge_all()
    db.close()
    background_tasks.add_task(process_generation_job, job_id)
    return response_payload


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, status_code=202)
def cancel_generation_job(job_id: str, db: Session = Depends(get_db)):
    job = (
        db.query(GenerationJob)
        .options(selectinload(GenerationJob.results))
        .filter_by(id=job_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.status == "CANCELLING":
        return _job_response(job)
    if job.status != "PROCESSING":
        raise HTTPException(status_code=409, detail="Only a processing job can be cancelled")

    claimed = (
        db.query(GenerationJob)
        .filter(GenerationJob.id == job_id, GenerationJob.status == "PROCESSING")
        .update({GenerationJob.status: "CANCELLING"}, synchronize_session=False)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=409, detail="The job is no longer processing")
    job.status = "CANCELLING"
    _record_job_event(db, job_id, "JOB_CANCELLATION_REQUESTED")
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    result = db.query(CertificateResult).filter_by(id=certificate_id).first()
    if result is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if result.status != "SUCCESS" or not result.filename:
        raise HTTPException(status_code=409, detail="This certificate was not generated successfully")
    path = (output_directory() / result.filename).resolve()
    if not path.is_relative_to(output_directory()):
        raise HTTPException(status_code=400, detail="Invalid certificate path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is unavailable")
    return FileResponse(path, media_type="application/pdf", filename=f"certificate-{result.id}.pdf")


@router.get("/certificates/{certificate_id}/verify", response_model=CertificateVerification)
def verify_certificate(certificate_id: str, db: Session = Depends(get_db)):
    record = (
        db.query(CertificateResult, GenerationJob)
        .join(GenerationJob, CertificateResult.job_id == GenerationJob.id)
        .filter(CertificateResult.id == certificate_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Certificate not found")

    result, job = record
    if result.status != "SUCCESS" or not result.filename:
        return {
            "valid": False,
            "certificate_id": result.id,
            "message": "No valid certificate was issued for this record.",
        }

    root = output_directory()
    path = (root / result.filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return {
            "valid": False,
            "certificate_id": result.id,
            "message": "The certificate record exists, but its PDF is unavailable.",
        }

    return {
        "valid": True,
        "certificate_id": result.id,
        "recipient_name": result.recipient_name,
        "course_name": job.course_name,
        "completion_date": date.fromisoformat(job.completion_date),
        "message": "This certificate is valid.",
    }


@router.get("/jobs/{job_id}/certificates.zip")
def download_job_certificates(job_id: str, db: Session = Depends(get_db)):
    job = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    successful = [result for result in job.results if result.status == "SUCCESS" and result.filename]
    if not successful:
        raise HTTPException(status_code=409, detail="This job has no successful certificates to download")

    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as zip_file:
        for result in successful:
            path = (output_directory() / result.filename).resolve()
            if not path.is_relative_to(output_directory()):
                raise HTTPException(status_code=400, detail="Invalid certificate path")
            if not path.is_file():
                raise HTTPException(status_code=404, detail=f"Certificate file for {result.recipient_name} is unavailable")
            zip_file.write(path, arcname=f"{result.id}.pdf")

    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates-{job.id}.zip"'},
    )
