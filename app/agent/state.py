from __future__ import annotations

from typing import Any, TypedDict


class ExtractionState(TypedDict, total=False):
    job_id: str
    attempt_id: str
    pdf_path: str
    bucket_name: str
    input_key: str
    artifact_dir: str
    process_all_pages: bool
    status: str
    stage: str
    progress: int
    input_report: dict[str, Any]
    pages: list[dict[str, Any]]
    selection: dict[str, Any]
    restrictions: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    model_calls: int
    input_tokens: int
    output_tokens: int
    candidate_path: str
    result_path: str
