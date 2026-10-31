from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.agent.graph import run_pipeline
from app.common.config import Settings
from app.common.status import JobStatus, is_terminal


class LocalJobService:
    """Small persistent local job service used only in clearly labeled mock mode."""

    def __init__(self, settings: Settings, root: Path) -> None:
        self.settings = settings
        self.root = root
        self.jobs_dir = root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=settings.max_active_jobs)
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def create_job(self, pdf_bytes: bytes, filename: str, process_all_pages: bool) -> dict:
        job_id = str(uuid4())
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        input_path = job_dir / "document.pdf"
        input_path.write_bytes(pdf_bytes)
        now = datetime.now(UTC)
        record = {
            "job_id": job_id,
            "owner_id": self.settings.owner_id,
            "status": JobStatus.UPLOADING,
            "stage": JobStatus.UPLOADING,
            "progress": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": int(
                (now + timedelta(days=self.settings.artifact_retention_days)).timestamp()
            ),
            "original_filename": Path(filename).name,
            "input_path": str(input_path),
            "process_all_pages": process_all_pages,
            "deployment_mode": "local_mock",
            "warnings": [],
            "errors": [],
        }
        self._write(record)
        return record

    def submit(self, job_id: str) -> dict:
        with self._lock:
            record = self.get(job_id)
            if is_terminal(record["status"]) or job_id in self._running:
                return record
            if record["status"] != JobStatus.UPLOADING:
                return record
            record.update(status=JobStatus.QUEUED, stage=JobStatus.QUEUED, updated_at=_now())
            self._write(record)
            self._running.add(job_id)
            self._executor.submit(self._run, job_id)
            return record

    def _run(self, job_id: str) -> None:
        attempt_id = str(uuid4())
        try:
            record = self.get(job_id)
            record.update(
                status=JobStatus.VALIDATING_INPUT,
                stage=JobStatus.VALIDATING_INPUT,
                attempt_id=attempt_id,
                updated_at=_now(),
            )
            self._write(record)

            def progress(stage: str, percentage: int) -> None:
                current = self.get(job_id)
                if is_terminal(current["status"]):
                    return
                current.update(
                    status=stage,
                    stage=stage,
                    progress=percentage,
                    updated_at=_now(),
                )
                self._write(current)

            final_state = run_pipeline(
                Path(record["input_path"]),
                self.jobs_dir / job_id / "artifacts",
                self.settings,
                job_id=job_id,
                attempt_id=attempt_id,
                process_all_pages=record["process_all_pages"],
                progress=progress,
            )
            record = self.get(job_id)
            record.update(
                status=final_state["status"],
                stage=final_state["stage"],
                progress=100,
                result_path=final_state.get("result_path"),
                warnings=final_state.get("warnings", []),
                errors=final_state.get("errors", []),
                model_calls=final_state.get("model_calls", 0),
                updated_at=_now(),
            )
            self._write(record)
        except Exception as exc:
            record = self.get(job_id)
            record.update(
                status=JobStatus.FAILED,
                stage=JobStatus.FAILED,
                progress=100,
                error_code=type(exc).__name__,
                error_message=str(exc),
                updated_at=_now(),
            )
            self._write(record)
        finally:
            with self._lock:
                self._running.discard(job_id)

    def get(self, job_id: str) -> dict:
        path = self.jobs_dir / job_id / "job.json"
        if not path.exists():
            raise KeyError(f"Unknown local job: {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[dict]:
        records = []
        for path in self.jobs_dir.glob("*/job.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def read_result(self, job_id: str) -> dict | None:
        record = self.get(job_id)
        result_path = record.get("result_path")
        if not result_path or not Path(result_path).exists():
            return None
        return json.loads(Path(result_path).read_text(encoding="utf-8"))

    def _write(self, record: dict) -> None:
        path = self.jobs_dir / record["job_id"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        temp.replace(path)

    def purge_job(self, job_id: str) -> None:
        record = self.get(job_id)
        if not is_terminal(record["status"]):
            raise RuntimeError("Only terminal local jobs can be purged")
        shutil.rmtree(self.jobs_dir / job_id)


def _now() -> str:
    return datetime.now(UTC).isoformat()
