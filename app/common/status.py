from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    UPLOADING = "UPLOADING"
    QUEUED = "QUEUED"
    DISPATCHING = "DISPATCHING"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    PARSING = "PARSING"
    SELECTING_PAGES = "SELECTING_PAGES"
    EXTRACTING = "EXTRACTING"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_WARNINGS,
    JobStatus.REJECTED,
    JobStatus.FAILED,
}


def is_terminal(status: JobStatus | str) -> bool:
    return JobStatus(status) in TERMINAL_STATUSES
