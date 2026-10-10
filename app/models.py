"""SQLAlchemy models for generation jobs and individual outcomes."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (Index("uq_generation_jobs_idempotency_key", "idempotency_key", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_name: Mapped[str] = mapped_column(String(200), index=True)
    completion_date: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING")
    total: Mapped[int] = mapped_column(Integer)
    pass_mark: Mapped[float] = mapped_column(Float, default=60.0)
    require_pass: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    results: Mapped[list["CertificateResult"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="CertificateResult.position"
    )


class CertificateResult(Base):
    __tablename__ = "certificate_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    recipient_name: Mapped[str] = mapped_column(String(200), index=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    score_percent: Mapped[float] = mapped_column(Float, default=100.0)
    score_provided: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped[GenerationJob] = relationship(back_populates="results")


class JobEvent(Base):
    """Append-only history of meaningful job and recipient transitions."""

    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    certificate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
