"""HTTP routes for bulk generation, status, and downloads."""

from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_

from app.certificates import generate_certificate, output_directory
from app.database import get_db
from app.models import CertificateResult, GenerationJob
from app.schemas import CertificateSearchPage, JobCreate, JobOut

router = APIRouter(prefix="/api", tags=["certificates"])


@router.get("/certificates/search", response_model=CertificateSearchPage)
def search_certificates(
    q: str | None = Query(default=None, max_length=200, description="Search candidate name, email, or course"),
    course: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(SUCCESS|NOT_PASSED|FAILED)$"),
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
    passed_expression = CertificateResult.score_percent >= GenerationJob.pass_mark
    if passed is True:
        query = query.filter(passed_expression)
    elif passed is False:
        query = query.filter(CertificateResult.score_percent < GenerationJob.pass_mark)
    if score_min is not None:
        query = query.filter(CertificateResult.score_percent >= score_min)
    if score_max is not None:
        query = query.filter(CertificateResult.score_percent <= score_max)

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
            "score_percent": result.score_percent,
            "pass_mark": job.pass_mark,
            "passed": result.score_percent >= job.pass_mark,
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
                "score_percent": result.score_percent,
                "passed": result.score_percent >= job.pass_mark,
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
        "status": job.status,
        "total": job.total,
        "passed": sum(result.score_percent >= job.pass_mark for result in job.results),
        "not_passed": sum(result.score_percent < job.pass_mark for result in job.results),
        "succeeded": job.succeeded,
        "failed": job.failed,
        "created_at": job.created_at,
        "results": results,
    }


@router.post("/jobs/", response_model=JobOut, status_code=201)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    job_id = str(uuid4())
    job = GenerationJob(
        id=job_id,
        course_name=payload.course_name,
        completion_date=payload.completion_date.isoformat(),
        pass_mark=payload.pass_mark,
        status="PROCESSING",
        total=len(payload.recipients),
        succeeded=0,
        failed=0,
    )
    db.add(job)
    db.flush()

    for position, recipient in enumerate(payload.recipients):
        certificate_id = str(uuid4())
        result = CertificateResult(
            id=certificate_id,
            job_id=job_id,
            position=position,
            recipient_name=recipient.name,
            recipient_email=str(recipient.email) if recipient.email else None,
            score_percent=recipient.score_percent,
            status="NOT_PASSED" if recipient.score_percent < payload.pass_mark else "FAILED",
        )
        db.add(result)
        if recipient.score_percent < payload.pass_mark:
            result.error_message = "Candidate did not meet the pass mark; no certificate was generated."
            continue
        try:
            path = generate_certificate(
                recipient_name=recipient.name,
                course_name=payload.course_name,
                completion_date=payload.completion_date.isoformat(),
                score_percent=recipient.score_percent,
                pass_mark=payload.pass_mark,
                job_id=job_id,
                certificate_id=certificate_id,
            )
            result.status = "SUCCESS"
            result.filename = str(path.relative_to(output_directory()))
            job.succeeded += 1
        except Exception:
            result.status = "FAILED"
            result.error_message = "Certificate generation failed. Please retry this recipient."
            job.failed += 1

    job.status = "COMPLETED_WITH_ERRORS" if job.failed else "COMPLETED"
    db.commit()
    db.refresh(job)
    job = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(id=job_id).one()
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(GenerationJob).options(selectinload(GenerationJob.results)).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
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
