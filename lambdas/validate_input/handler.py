from __future__ import annotations

import os
from typing import Any

import boto3

from app.common.config import load_settings
from app.validation.input import validate_input_document

s3 = boto3.client("s3")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    bucket = event["bucket_name"]
    key = event["input_key"]
    job_id = event["job_id"]
    attempt_id = event["attempt_id"]
    pdf_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    settings = load_settings(environ=os.environ)
    report = validate_input_document(pdf_bytes, settings)
    report_key = f"jobs/{job_id}/attempts/{attempt_id}/validation/input.json"
    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=report.model_dump_json(indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return {"ok": report.ok, "report": report.model_dump(mode="json"), "report_key": report_key}
