"""SQLAlchemy models for generation jobs and individual outcomes."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_name: Mapped[str] = mapped_column(String(200))
    completion_date: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING")
    total: Mapped[int] = mapped_column(Integer)
    pass_mark: Mapped[float] = mapped_column(Float, default=60.0)
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
    recipient_name: Mapped[str] = mapped_column(String(200))
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    score_percent: Mapped[float] = mapped_column(Float, default=100.0)
    status: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped[GenerationJob] = relationship(back_populates="results")
