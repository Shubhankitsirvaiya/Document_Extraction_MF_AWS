from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common.status import JobStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(StrEnum):
    FUND = "FUND"
    UMBRELLA = "UMBRELLA"
    SHARE_CLASS = "SHARE_CLASS"
    UNKNOWN = "UNKNOWN"


class RestrictionCategory(StrEnum):
    BORROWING = "BORROWING"
    LEVERAGE = "LEVERAGE"
    DERIVATIVES = "DERIVATIVES"
    CONCENTRATION = "CONCENTRATION"
    DIVERSIFICATION = "DIVERSIFICATION"
    ELIGIBLE_INVESTMENTS = "ELIGIBLE_INVESTMENTS"
    LIQUIDITY = "LIQUIDITY"
    OTHER = "OTHER"


class LocalizationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    REJECTED = "REJECTED"


class BoundingBox(StrictModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @model_validator(mode="after")
    def validate_geometry(self) -> BoundingBox:
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Bounding-box coordinates must be finite")
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("Bounding-box coordinates must be normalized to [0, 1]")
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("Bounding box must have positive width and height")
        return self

    def as_list(self) -> list[float]:
        return [self.x_min, self.y_min, self.x_max, self.y_max]


class EvidenceRecord(StrictModel):
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)
    bboxes: list[BoundingBox] = Field(default_factory=list)
    bbox_source: Literal["LLM", "MOCK"]
    localization_status: LocalizationStatus = LocalizationStatus.UNVERIFIED
    source_image_width: int = Field(gt=0)
    source_image_height: int = Field(gt=0)


class RestrictionRecord(StrictModel):
    restriction_id: str = Field(min_length=1)
    fund_name: str = Field(min_length=1)
    scope: Scope
    category: RestrictionCategory
    restriction_text: str = Field(min_length=1)
    operator: str | None = None
    threshold: float | None = None
    unit: str | None = None
    denominator: str | None = None
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(min_length=1)
    needs_review: bool = True
    review_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_review_reason_when_needed(self) -> RestrictionRecord:
        if self.needs_review and not self.review_reasons:
            raise ValueError("needs_review=true requires at least one review reason")
        return self


class DocumentInfo(StrictModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1, le=20)


class CoverageInfo(StrictModel):
    mode: Literal["regex_plus_neighbors", "all_pages", "fallback_all_pages"]
    selected_pages: list[int]
    failed_pages: list[int] = Field(default_factory=list)
    selection_reasons: dict[str, list[str]] = Field(default_factory=dict)


class UsageInfo(StrictModel):
    model_id: str
    calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ExtractionResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    attempt_id: UUID
    deployment_mode: Literal["local_mock", "aws"]
    document: DocumentInfo
    coverage: CoverageInfo
    restrictions: list[RestrictionRecord]
    warnings: list[str] = Field(default_factory=list)
    usage: UsageInfo


class PageRecord(StrictModel):
    page_number: int = Field(ge=1)
    printed_page_label: str | None = None
    raw_text: str
    normalized_text: str
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int
    text_character_count: int = Field(ge=0)
    low_text: bool
    image_artifact: str | None = None
    render_width: int | None = Field(default=None, gt=0)
    render_height: int | None = Field(default=None, gt=0)
    transform: dict[str, Any] = Field(default_factory=dict)


class InputValidationReport(StrictModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sha256: str | None = None
    page_count: int | None = None
    pages: list[PageRecord] = Field(default_factory=list)


class OutputValidationReport(StrictModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_artifact_key: str | None = None


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: UUID
    owner_id: str
    status: JobStatus
    stage: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: int
    input_key: str
    process_all_pages: bool = False
    progress: int = Field(default=0, ge=0, le=100)
    correlation_id: str
    attempt_id: UUID | None = None
    runtime_session_id: str | None = None
    result_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings_count: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)

    @field_validator("runtime_session_id")
    @classmethod
    def validate_runtime_session_id(cls, value: str | None) -> str | None:
        if value is not None and not 33 <= len(value) <= 256:
            raise ValueError("AgentCore runtime session IDs must contain 33–256 characters")
        return value


class CreateJobRequest(StrictModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["application/pdf"]
    size_bytes: int = Field(gt=0)
    process_all_pages: bool = False


class ReviewDecision(StrictModel):
    restriction_id: str
    decision: Literal["ACCEPT", "REJECT"]
    comment: str = Field(default="", max_length=2000)
