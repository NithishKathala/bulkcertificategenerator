"""API request and response schemas."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RecipientInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    score_percent: float = Field(ge=0, le=100)

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
    recipients: list[RecipientInput] = Field(min_length=1, max_length=1000)

    @field_validator("course_name")
    @classmethod
    def course_name_must_not_be_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Course name cannot be blank")
        return cleaned


class CertificateResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    certificate_id: str = Field(validation_alias="id")
    recipient_name: str
    recipient_email: str | None
    score_percent: float
    passed: bool
    status: Literal["SUCCESS", "NOT_PASSED", "FAILED"]
    error: str | None = Field(default=None, validation_alias="error_message")
    download_url: str | None = None


class JobOut(BaseModel):
    id: str
    course_name: str
    completion_date: date
    pass_mark: float
    status: Literal["COMPLETED", "COMPLETED_WITH_ERRORS"]
    total: int
    passed: int
    not_passed: int
    succeeded: int
    failed: int
    created_at: datetime
    results: list[CertificateResultOut]
