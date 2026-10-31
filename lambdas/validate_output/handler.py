from __future__ import annotations

import json
from typing import Any

import boto3

from app.common.schemas import ExtractionResult, PageRecord
from app.validation.output import validate_output_candidate

s3 = boto3.client("s3")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    bucket = event["bucket_name"]
    candidate = ExtractionResult.model_validate_json(
        s3.get_object(Bucket=bucket, Key=event["candidate_key"])["Body"].read()
    )
    pages = [
        PageRecord.model_validate(item)
        for item in json.loads(s3.get_object(Bucket=bucket, Key=event["pages_key"])["Body"].read())
    ]
    report = validate_output_candidate(
        candidate,
        expected_job_id=event["job_id"],
        expected_attempt_id=event["attempt_id"],
        expected_sha256=event["input_sha256"],
        pages=pages,
        allow_mock_boxes=False,
    )
    report_key = f"jobs/{event['job_id']}/attempts/{event['attempt_id']}/validation/output.json"
    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=report.model_dump_json(indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    payload = report.model_dump(mode="json")
    payload["validation_artifact_key"] = report_key
    return payload
