"""HTTP routes for bulk generation, status, and downloads."""

from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, selectinload

from app.certificates import generate_certificate, output_directory
from app.database import get_db
from app.models import CertificateResult, GenerationJob
from app.schemas import JobCreate, JobOut

router = APIRouter(prefix="/api", tags=["certificates"])


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
