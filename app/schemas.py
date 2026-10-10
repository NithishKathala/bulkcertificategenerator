"""API request and response schemas."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class RecipientInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    score_percent: float | None = Field(default=None, ge=0, le=100)

    @field_validator("name")
    @classmethod
    def name_must_not_be_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Recipient name cannot be blank")
        return cleaned


class JobCreate(BaseModel):
    course_name: str = Field(min_length=1, max_length=200)
    completion_date: date
    pass_mark: float = Field(default=60, ge=0, le=100)
    require_pass: bool = False
    recipients: list[RecipientInput] = Field(min_length=1, max_length=1000)

    @field_validator("course_name")
    @classmethod
    def course_name_must_not_be_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Course name cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def recipient_emails_must_be_unique(self) -> "JobCreate":
        seen_emails: dict[str, int] = {}
        for index, recipient in enumerate(self.recipients, start=1):
            if not recipient.email:
                continue
            email_key = str(recipient.email).casefold()
            if email_key in seen_emails:
                raise ValueError(
                    f"Recipient {index} repeats the email address from recipient {seen_emails[email_key]}"
                )
            seen_emails[email_key] = index
        if self.require_pass and any(recipient.score_percent is None for recipient in self.recipients):
            raise ValueError("Every recipient needs a score when require_pass is enabled")
        return self


class CertificateResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    certificate_id: str = Field(validation_alias="id")
    recipient_name: str
    recipient_email: str | None
    score_percent: float | None
    passed: bool | None
    certificate_type: Literal["COMPLETION", "PARTICIPATION"] | None = None
    status: Literal["PROCESSING", "SUCCESS", "NOT_PASSED", "FAILED", "CANCELLED"]
    error: str | None = Field(default=None, validation_alias="error_message")
    download_url: str | None = None


class JobOut(BaseModel):
    id: str
    course_name: str
    completion_date: date
    pass_mark: float
    require_pass: bool
    status: Literal["PROCESSING", "CANCELLING", "CANCELLED", "COMPLETED", "COMPLETED_WITH_ERRORS"]
    total: int
    processed: int
    progress_percent: int
    passed: int
    not_passed: int
    unscored: int
    succeeded: int
    failed: int
    created_at: datetime
    results: list[CertificateResultOut]


class JobEventOut(BaseModel):
    id: str
    job_id: str
    event_type: str
    certificate_id: str | None
    detail: str | None
    created_at: datetime


class JobEventPage(BaseModel):
    total: int
    offset: int
    limit: int
    events: list[JobEventOut]


class CertificateSearchItem(BaseModel):
    certificate_id: str
    job_id: str
    course_name: str
    completion_date: date
    recipient_name: str
    recipient_email: str | None
    score_percent: float | None
    pass_mark: float
    passed: bool | None
    certificate_type: Literal["COMPLETION", "PARTICIPATION"] | None = None
    status: Literal["PROCESSING", "SUCCESS", "NOT_PASSED", "FAILED", "CANCELLED"]
    download_url: str | None


class CertificateSearchPage(BaseModel):
    total: int
    offset: int
    limit: int
    results: list[CertificateSearchItem]


class CertificateVerification(BaseModel):
    valid: bool
    certificate_id: str
    recipient_name: str | None = None
    course_name: str | None = None
    completion_date: date | None = None
    message: str
