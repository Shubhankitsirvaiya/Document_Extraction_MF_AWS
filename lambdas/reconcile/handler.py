from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.common.status import JobStatus, is_terminal

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
events = boto3.client("events")


def _table():
    return dynamodb.Table(os.environ["JOBS_TABLE_NAME"])


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del event, context
    table = _table()
    now = int(time.time())
    processed = 0
    last_key = None
    while True:
        kwargs: dict[str, Any] = {
            "ProjectionExpression": (
                "job_id,#status,correlation_id,enqueue_state,enqueue_attempts,created_at,"
                "dispatch_lease_expires_at,heartbeat_at,deadline_at,attempt_id,completion_event_state"
            ),
            "ExpressionAttributeNames": {"#status": "status"},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            processed += 1
            status = JobStatus(item["status"])
            if status == JobStatus.QUEUED and item.get("enqueue_state") != "SENT":
                attempts = int(item.get("enqueue_attempts", 0))
                if attempts < 3:
                    sqs.send_message(
                        QueueUrl=os.environ["INTAKE_QUEUE_URL"],
                        MessageBody=json.dumps(
                            {"job_id": item["job_id"], "correlation_id": item["correlation_id"]}
                        ),
                    )
                    table.update_item(
                        Key={"job_id": item["job_id"]},
                        UpdateExpression="SET enqueue_state=:sent ADD enqueue_attempts :one",
                        ExpressionAttributeValues={":sent": "SENT", ":one": 1},
                    )
            elif (
                status == JobStatus.DISPATCHING
                and int(item.get("dispatch_lease_expires_at", 0)) < now
            ):
                table.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET #status=:queued, stage=:queued, enqueue_state=:pending",
                    ConditionExpression="#status=:dispatching AND dispatch_lease_expires_at<:now",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":queued": JobStatus.QUEUED,
                        ":pending": "PENDING",
                        ":dispatching": JobStatus.DISPATCHING,
                        ":now": now,
                    },
                )
            elif status in {
                JobStatus.VALIDATING_INPUT,
                JobStatus.PARSING,
                JobStatus.SELECTING_PAGES,
                JobStatus.EXTRACTING,
                JobStatus.VALIDATING_OUTPUT,
            }:
                heartbeat = int(item.get("heartbeat_at", 0))
                deadline = int(item.get("deadline_at", 0))
                if (heartbeat and heartbeat < now - 120) or (deadline and deadline < now):
                    try:
                        table.update_item(
                            Key={"job_id": item["job_id"]},
                            UpdateExpression=(
                                "SET #status=:failed, stage=:failed, error_code=:code, "
                                "error_message=:message, updated_at=:updated"
                            ),
                            ConditionExpression="#status=:expected AND attempt_id=:attempt",
                            ExpressionAttributeNames={"#status": "status"},
                            ExpressionAttributeValues={
                                ":failed": JobStatus.FAILED,
                                ":expected": status,
                                ":attempt": item.get("attempt_id"),
                                ":code": "STALE_PROCESSING_ATTEMPT",
                                ":message": "Heartbeat or application deadline expired",
                                ":updated": datetime.now(UTC).isoformat(),
                            },
                        )
                    except ClientError as exc:
                        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                            raise
            if is_terminal(status) and item.get("completion_event_state") == "PENDING":
                events.put_events(
                    Entries=[
                        {
                            "Source": "mf.prospectus",
                            "DetailType": "ProspectusExtractionCompleted",
                            "EventBusName": os.environ["COMPLETION_BUS_NAME"],
                            "Detail": json.dumps({"job_id": item["job_id"], "status": status}),
                        }
                    ]
                )
                table.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET completion_event_state=:sent",
                    ExpressionAttributeValues={":sent": "SENT"},
                )
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
    return {"processed": processed}
