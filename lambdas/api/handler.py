from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from app.common.schemas import CreateJobRequest
from app.common.status import JobStatus, is_terminal

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")


def _table():
    return dynamodb.Table(os.environ["JOBS_TABLE_NAME"])


def _owner(event: dict[str, Any]) -> str:
    context = event.get("requestContext", {})
    authorizer = context.get("authorizer", {})
    iam = authorizer.get("iam", {}) if isinstance(authorizer, dict) else {}
    identity = context.get("identity", {})
    owner = (
        iam.get("userArn")
        or iam.get("callerId")
        or identity.get("userArn")
        or authorizer.get("principalId")
    )
    if not owner:
        raise PermissionError("Authenticated principal was not present in request context")
    return str(owner)


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, default=_json_default),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(type(value).__name__)


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    return json.loads(raw) if isinstance(raw, str) else raw


def _get_owned_job(job_id: str, owner_id: str) -> dict[str, Any]:
    item = _table().get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item or item.get("owner_id") != owner_id:
        raise KeyError("Job not found")
    return item


def create_job(event: dict[str, Any], owner_id: str) -> dict[str, Any]:
    request = CreateJobRequest.model_validate(_body(event))
    max_bytes = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    if request.size_bytes > max_bytes:
        return _response(400, {"error": "DECLARED_FILE_TOO_LARGE"})

    job_id = str(uuid4())
    correlation_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    input_key = f"jobs/{job_id}/input/document.pdf"
    ttl = int(time.time()) + int(os.environ.get("ARTIFACT_TTL_SECONDS", "604800"))
    item = {
        "job_id": job_id,
        "owner_id": owner_id,
        "status": JobStatus.UPLOADING,
        "stage": JobStatus.UPLOADING,
        "created_at": now,
        "updated_at": now,
        "expires_at": ttl,
        "input_key": input_key,
        "original_filename": PurePosixPath(request.original_filename).name,
        "declared_size_bytes": request.size_bytes,
        "process_all_pages": request.process_all_pages,
        "progress": 0,
        "warnings_count": 0,
        "model_calls": 0,
        "correlation_id": correlation_id,
        "completion_event_state": "NOT_READY",
        "enqueue_attempts": 0,
    }
    _table().put_item(Item=item, ConditionExpression="attribute_not_exists(job_id)")
    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["ARTIFACT_BUCKET_NAME"],
            "Key": input_key,
            "ContentType": "application/pdf",
        },
        ExpiresIn=900,
    )
    return _response(
        201, {"job_id": job_id, "status": JobStatus.UPLOADING, "upload_url": upload_url}
    )


def submit_job(job_id: str, owner_id: str) -> dict[str, Any]:
    item = _get_owned_job(job_id, owner_id)
    if is_terminal(item["status"]):
        return _response(409, {"error": "TERMINAL_JOB_REQUIRES_NEW_JOB_ID"})
    if item["status"] != JobStatus.UPLOADING:
        return _response(202, {"job_id": job_id, "status": item["status"]})

    bucket = os.environ["ARTIFACT_BUCKET_NAME"]
    try:
        metadata = s3.head_object(Bucket=bucket, Key=item["input_key"])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            return _response(409, {"error": "UPLOAD_NOT_FOUND"})
        raise
    if metadata["ContentLength"] > int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))):
        return _response(400, {"error": "ACTUAL_FILE_TOO_LARGE"})

    now = datetime.now(UTC).isoformat()
    _table().update_item(
        Key={"job_id": job_id},
        UpdateExpression=(
            "SET #status=:queued, stage=:queued, updated_at=:now, "
            "enqueue_state=:pending, enqueue_attempts=if_not_exists(enqueue_attempts,:zero)+:one"
        ),
        ConditionExpression="#status=:uploading AND owner_id=:owner",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":queued": JobStatus.QUEUED,
            ":uploading": JobStatus.UPLOADING,
            ":owner": owner_id,
            ":pending": "PENDING",
            ":now": now,
            ":zero": 0,
            ":one": 1,
        },
    )
    sqs.send_message(
        QueueUrl=os.environ["INTAKE_QUEUE_URL"],
        MessageBody=json.dumps({"job_id": job_id, "correlation_id": item["correlation_id"]}),
    )
    _table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET enqueue_state=:sent, enqueue_sent_at=:now",
        ExpressionAttributeValues={":sent": "SENT", ":now": now},
    )
    return _response(202, {"job_id": job_id, "status": JobStatus.QUEUED})


def get_job(job_id: str, owner_id: str) -> dict[str, Any]:
    item = _get_owned_job(job_id, owner_id)
    response = {
        key: value
        for key, value in item.items()
        if key not in {"owner_id", "dispatch_lease_expires_at", "processing_lease_expires_at"}
    }
    if item.get("result_key") and is_terminal(item["status"]):
        response["result_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["ARTIFACT_BUCKET_NAME"], "Key": item["result_key"]},
            ExpiresIn=300,
        )
        result = json.loads(
            s3.get_object(Bucket=os.environ["ARTIFACT_BUCKET_NAME"], Key=item["result_key"])[
                "Body"
            ].read()
        )
        evidence_pages = sorted(
            {
                evidence["page_number"]
                for restriction in result.get("restrictions", [])
                for evidence in restriction.get("evidence", [])
            }
        )
        attempt_id = item.get("attempt_id")
        response["evidence_image_urls"] = {
            str(page_number): s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": os.environ["ARTIFACT_BUCKET_NAME"],
                    "Key": (
                        f"jobs/{job_id}/attempts/{attempt_id}/artifacts/pages/"
                        f"page-{page_number:03d}.png"
                    ),
                },
                ExpiresIn=300,
            )
            for page_number in evidence_pages
        }
    return _response(200, response)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    try:
        owner_id = _owner(event)
        route = event.get("routeKey", "")
        job_id = (event.get("pathParameters") or {}).get("job_id")
        if route == "POST /jobs":
            return create_job(event, owner_id)
        if route == "POST /jobs/{job_id}/submit" and job_id:
            return submit_job(job_id, owner_id)
        if route == "GET /jobs/{job_id}" and job_id:
            return get_job(job_id, owner_id)
        return _response(404, {"error": "ROUTE_NOT_FOUND"})
    except PermissionError as exc:
        return _response(403, {"error": str(exc)})
    except KeyError:
        return _response(404, {"error": "JOB_NOT_FOUND"})
    except Exception as exc:
        return _response(500, {"error": type(exc).__name__, "message": str(exc)})
