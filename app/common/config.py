from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT_DIR / ".env"
DEFAULT_OUTPUT_FILE = ROOT_DIR / "generated" / "deployment_outputs.json"


class Settings(BaseModel):
    """Validated settings with explicit, testable source precedence.

    Precedence from lowest to highest is: class defaults, `.env`, generated deployment
    outputs, process environment, then explicit caller overrides.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    app_mode: str = "aws"
    aws_profile: str = "mf-dev"
    aws_region: str = "ap-south-1"
    expected_aws_account_id: str = ""
    project_name: str = "mf-prospectus"
    environment: str = "dev"
    owner_id: str = "local-owner"
    bedrock_model_id: str = ""
    max_pdf_pages: int = Field(default=20, ge=1, le=20)
    max_upload_mib: int = Field(default=20, ge=1, le=100)
    model_page_batch_size: int = Field(default=2, ge=1, le=2)
    max_model_calls: int = Field(default=12, ge=1, le=50)
    max_job_seconds: int = Field(default=1200, ge=60, le=7200)
    max_active_jobs: int = Field(default=1, ge=1, le=10)
    low_text_threshold: int = Field(default=80, ge=0, le=1000)
    page_neighbor_radius: int = Field(default=1, ge=0, le=2)
    artifact_retention_days: int = Field(default=7, ge=1, le=30)
    enable_secret_demo: bool = False
    enable_guardrail: bool = False
    budget_usd: float = Field(default=10.0, gt=0)
    budget_email: str = ""
    api_base_url: str = ""
    s3_bucket_name: str = ""
    dynamodb_table_name: str = ""
    sqs_queue_url: str = ""
    agentcore_runtime_arn: str = ""
    input_validator_arn: str = ""
    output_validator_arn: str = ""
    app_secret_arn: str = ""

    @field_validator("app_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        allowed = {"local_mock", "aws"}
        if value not in allowed:
            raise ValueError(f"APP_MODE must be one of {sorted(allowed)}")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mib * 1024 * 1024


def _normalized(values: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in values.items() if value not in (None, "")}


def _read_outputs(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Deployment output file must contain an object: {path}")
    return payload


def load_settings(
    *,
    env_file: Path | None = None,
    output_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Load settings without mutating `os.environ` or overwriting the user's `.env`."""

    env_path = env_file or DEFAULT_ENV_FILE
    output_path = output_file or DEFAULT_OUTPUT_FILE
    process_environment = dict(os.environ if environ is None else environ)

    merged: dict[str, Any] = {}
    if env_path.exists():
        merged.update(_normalized(dotenv_values(env_path)))
    merged.update(_normalized(_read_outputs(output_path)))
    merged.update(_normalized(process_environment))
    if overrides:
        merged.update(_normalized(overrides))
    return Settings.model_validate(merged)
