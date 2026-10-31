from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import boto3
from botocore.exceptions import ClientError

from app.common.status import JobStatus, is_terminal

dynamodb = boto3.resource("dynamodb")
agentcore = boto3.client("bedrock-agentcore")


def _table():
    return dynamodb.Table(os.environ["JOBS_TABLE_NAME"])


def _dispatch_one(record: dict[str, Any]) -> bool:
    body = json.loads(record["body"])
    job_id = body["job_id"]
    table = _table()
    item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item or is_terminal(item["status"]):
        return True
    if item["status"] in {
        JobStatus.VALIDATING_INPUT,
        JobStatus.PARSING,
        JobStatus.SELECTING_PAGES,
        JobStatus.EXTRACTING,
        JobStatus.VALIDATING_OUTPUT,
    }:
        # A previous delivery may have invoked AgentCore successfully and then lost
        # its SQS acknowledgement. The active fenced attempt proves it is safe to ack.
        return True
    if item["status"] not in {JobStatus.QUEUED, JobStatus.DISPATCHING}:
        return False
    if item["status"] == JobStatus.DISPATCHING and int(
        item.get("dispatch_lease_expires_at", 0)
    ) > int(time.time()):
        return False

    attempt_id = str(uuid4())
    runtime_session_id = str(uuid5(NAMESPACE_URL, f"mf-prospectus:{job_id}:{attempt_id}"))
    lease_expires = int(time.time()) + 120
    try:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status=:dispatching, stage=:dispatching, attempt_id=:attempt, "
                "runtime_session_id=:session, dispatch_lease_expires_at=:lease"
            ),
            ConditionExpression=(
                "#status=:queued OR (#status=:dispatching AND dispatch_lease_expires_at<:now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":dispatching": JobStatus.DISPATCHING,
                ":queued": JobStatus.QUEUED,
                ":attempt": attempt_id,
                ":session": runtime_session_id,
                ":lease": lease_expires,
                ":now": int(time.time()),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

    payload = json.dumps(
        {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "correlation_id": item["correlation_id"],
        }
    ).encode("utf-8")
    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENTCORE_RUNTIME_ARN"],
        runtimeSessionId=runtime_session_id,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=payload,
    )
    response_body = json.loads(response["response"].read())
    return response_body.get("status") in {"ACCEPTED", "ALREADY_RUNNING", "TERMINAL"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    failures = []
    for record in event.get("Records", []):
        try:
            if not _dispatch_one(record):
                failures.append({"itemIdentifier": record["messageId"]})
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
