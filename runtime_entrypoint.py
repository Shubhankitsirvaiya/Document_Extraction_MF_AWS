"""Amazon Bedrock AgentCore Runtime entry point.

The invocation path is intentionally short: it validates the dispatch token, registers
an AgentCore asynchronous task, starts a background worker, and immediately returns
``ACCEPTED``.  The worker owns the long PDF/model work while AgentCore's ``/ping``
endpoint remains responsive.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError

from app.agent.graph import Pipeline
from app.agent.state import ExtractionState
from app.common.config import load_settings
from app.common.logging import configure_logging
from app.common.schemas import InputValidationReport, OutputValidationReport
from app.common.status import JobStatus, is_terminal

app = BedrockAgentCoreApp()
configure_logging()
logger = logging.getLogger("agentcore-runtime")
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("MAX_ACTIVE_JOBS", "1")))

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")


def _table():
    """Resolve the jobs table lazily so unit tests can replace environment values."""

    return dynamodb.Table(os.environ["JOBS_TABLE_NAME"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _invoke_lambda(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mandatory validator synchronously and reject Lambda-level failures."""

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = json.loads(response["Payload"].read() or b"{}")
    if response.get("FunctionError"):
        raise RuntimeError(f"Validator Lambda failed: {body}")
    return body


def _conditional_update(
    job_id: str,
    attempt_id: str,
    *,
    update_expression: str,
    values: dict[str, Any],
    names: dict[str, str] | None = None,
) -> bool:
    """Fence every runtime write by ``attempt_id`` so a stale worker cannot win."""

    expression_names = {"#status": "status", **(names or {})}
    expression_values = {":attempt": attempt_id, **values}
    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ConditionExpression="attempt_id=:attempt AND attribute_exists(job_id)",
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.warning("stale_attempt_write_blocked", extra={"job_id": job_id})
            return False
        raise


def _claim_attempt(job_id: str, attempt_id: str) -> tuple[str, dict[str, Any]]:
    """Move exactly the dispatched attempt into processing or report idempotent state."""

    table = _table()
    item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item:
        return "MISSING", {}
    if is_terminal(item["status"]):
        return "TERMINAL", item
    if item.get("attempt_id") != attempt_id:
        return "STALE", item
    if item["status"] != JobStatus.DISPATCHING:
        return "ALREADY_RUNNING", item

    now = int(time.time())
    try:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status=:validating, stage=:validating, progress=:progress, "
                "heartbeat_at=:heartbeat, deadline_at=:deadline, updated_at=:updated"
            ),
            ConditionExpression="#status=:dispatching AND attempt_id=:attempt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":validating": JobStatus.VALIDATING_INPUT,
                ":dispatching": JobStatus.DISPATCHING,
                ":attempt": attempt_id,
                ":progress": 1,
                ":heartbeat": now,
                ":deadline": now + int(os.environ.get("MAX_JOB_SECONDS", "1200")),
                ":updated": _now_iso(),
            },
        )
        return "CLAIMED", item
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item", {})
            return "ALREADY_RUNNING", latest
        raise


def _heartbeat(job_id: str, attempt_id: str, stop: threading.Event) -> None:
    """Refresh the application heartbeat independently from model/PDF work."""

    interval = int(os.environ.get("HEARTBEAT_SECONDS", "30"))
    while not stop.wait(interval):
        if not _conditional_update(
            job_id,
            attempt_id,
            update_expression="SET heartbeat_at=:heartbeat, updated_at=:updated",
            values={":heartbeat": int(time.time()), ":updated": _now_iso()},
        ):
            return


def _run_job(payload: dict[str, Any], task_id: str) -> None:
    """Download, extract, validate, persist, and publish one attempt's terminal state."""

    job_id = payload["job_id"]
    attempt_id = payload["attempt_id"]
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        args=(job_id, attempt_id, heartbeat_stop),
        daemon=True,
        name=f"heartbeat-{job_id}",
    )
    heartbeat_thread.start()

    try:
        item = _table().get_item(Key={"job_id": job_id}, ConsistentRead=True)["Item"]
        bucket = os.environ["ARTIFACT_BUCKET_NAME"]
        settings = load_settings(environ=os.environ, overrides={"app_mode": "aws"})
        prefix = f"jobs/{job_id}/attempts/{attempt_id}"

        with tempfile.TemporaryDirectory(prefix="mf-agent-") as temporary_directory:
            workdir = Path(temporary_directory)
            pdf_path = workdir / "document.pdf"
            artifact_dir = workdir / "artifacts"
            artifact_dir.mkdir()
            s3.download_file(bucket, item["input_key"], str(pdf_path))

            def progress(stage: str, percent: int) -> None:
                _conditional_update(
                    job_id,
                    attempt_id,
                    update_expression=(
                        "SET #status=:stage, stage=:stage, progress=:progress, "
                        "heartbeat_at=:heartbeat, updated_at=:updated"
                    ),
                    values={
                        ":stage": stage,
                        ":progress": percent,
                        ":heartbeat": int(time.time()),
                        ":updated": _now_iso(),
                    },
                )

            def validate_input(pdf_bytes: bytes, state: ExtractionState) -> InputValidationReport:
                del pdf_bytes
                response = _invoke_lambda(
                    os.environ["INPUT_VALIDATOR_NAME"],
                    {
                        "bucket_name": bucket,
                        "input_key": item["input_key"],
                        "job_id": state["job_id"],
                        "attempt_id": state["attempt_id"],
                    },
                )
                return InputValidationReport.model_validate(response["report"])

            def validate_output(result, pages, state: ExtractionState) -> OutputValidationReport:
                candidate_key = f"{prefix}/candidate.json"
                pages_key = f"{prefix}/pages.json"
                s3.upload_file(state["candidate_path"], bucket, candidate_key)
                s3.put_object(
                    Bucket=bucket,
                    Key=pages_key,
                    Body=json.dumps([page.model_dump(mode="json") for page in pages]).encode(),
                    ContentType="application/json",
                )
                response = _invoke_lambda(
                    os.environ["OUTPUT_VALIDATOR_NAME"],
                    {
                        "bucket_name": bucket,
                        "candidate_key": candidate_key,
                        "pages_key": pages_key,
                        "job_id": state["job_id"],
                        "attempt_id": state["attempt_id"],
                        "input_sha256": result.document.sha256,
                    },
                )
                return OutputValidationReport.model_validate(response)

            pipeline = Pipeline(
                settings,
                progress=progress,
                input_validator=validate_input,
                output_validator=validate_output,
            )
            graph = pipeline.build()
            final_state = graph.invoke(
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "pdf_path": str(pdf_path),
                    "bucket_name": bucket,
                    "input_key": item["input_key"],
                    "artifact_dir": str(artifact_dir),
                    "process_all_pages": bool(item.get("process_all_pages", False)),
                    "status": JobStatus.VALIDATING_INPUT,
                    "stage": JobStatus.VALIDATING_INPUT,
                    "progress": 0,
                    "warnings": [],
                    "errors": [],
                    "model_calls": 0,
                }
            )

            # Persist rendered page images and validation artifacts so the review UI
            # can display the exact source page used by each evidence record.
            for artifact_path in artifact_dir.rglob("*"):
                if artifact_path.is_file():
                    relative_key = artifact_path.relative_to(artifact_dir).as_posix()
                    s3.upload_file(
                        str(artifact_path),
                        bucket,
                        f"{prefix}/artifacts/{relative_key}",
                    )

            result_key = f"{prefix}/result.json"
            s3.upload_file(final_state["result_path"], bucket, result_key)
            terminal_status = JobStatus(final_state["status"])
            _conditional_update(
                job_id,
                attempt_id,
                update_expression=(
                    "SET #status=:terminal, stage=:terminal, progress=:complete, "
                    "result_key=:result, warnings_count=:warnings, model_calls=:calls, "
                    "completion_event_state=:pending, heartbeat_at=:heartbeat, updated_at=:updated"
                ),
                values={
                    ":terminal": terminal_status,
                    ":complete": 100,
                    ":result": result_key,
                    ":warnings": len(final_state.get("warnings", [])),
                    ":calls": final_state.get("model_calls", 0),
                    ":pending": "PENDING",
                    ":heartbeat": int(time.time()),
                    ":updated": _now_iso(),
                },
            )
    except Exception as exc:
        logger.exception("job_failed", extra={"job_id": job_id, "attempt_id": attempt_id})
        _conditional_update(
            job_id,
            attempt_id,
            update_expression=(
                "SET #status=:failed, stage=:failed, progress=:complete, error_code=:code, "
                "error_message=:message, completion_event_state=:pending, updated_at=:updated"
            ),
            values={
                ":failed": JobStatus.FAILED,
                ":complete": 100,
                ":code": type(exc).__name__,
                ":message": str(exc)[:1000],
                ":pending": "PENDING",
                ":updated": _now_iso(),
            },
        )
    finally:
        heartbeat_stop.set()
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept an idempotent dispatch and return before the long job starts."""

    job_id = str(payload.get("job_id", ""))
    attempt_id = str(payload.get("attempt_id", ""))
    if not job_id or not attempt_id:
        return {"status": "REJECTED", "reason": "job_id and attempt_id are required"}

    claim, _ = _claim_attempt(job_id, attempt_id)
    if claim in {"TERMINAL", "ALREADY_RUNNING"}:
        return {"status": claim, "job_id": job_id, "attempt_id": attempt_id}
    if claim != "CLAIMED":
        return {"status": "REJECTED", "reason": claim}

    task_id = app.add_async_task(f"prospectus-{job_id}")
    executor.submit(_run_job, payload, task_id)
    return {"status": "ACCEPTED", "job_id": job_id, "attempt_id": attempt_id}


if __name__ == "__main__":
    app.run()
