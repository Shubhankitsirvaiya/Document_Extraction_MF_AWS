from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.agent.graph import run_pipeline
from app.common.config import Settings
from app.common.status import JobStatus


@pytest.mark.integration
def test_local_mock_pipeline_produces_valid_result(make_pdf, tmp_path):
    pdf = make_pdf(
        [
            "Alpha Income Fund Investment Restrictions\n"
            "The Alpha Income Fund shall not borrow more than 10% of NAV."
        ]
    )
    artifact_dir = tmp_path / "artifacts"
    job_id = str(uuid4())
    attempt_id = str(uuid4())

    state = run_pipeline(
        pdf,
        artifact_dir,
        Settings(app_mode="local_mock", low_text_threshold=0),
        job_id=job_id,
        attempt_id=attempt_id,
    )

    assert state["status"] in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}
    result = json.loads((artifact_dir / "result.json").read_text(encoding="utf-8"))
    assert result["deployment_mode"] == "local_mock"
    assert result["usage"]["model_id"] == "LOCAL_MOCK_NO_MODEL"
    assert result["restrictions"]
    assert result["restrictions"][0]["evidence"][0]["bbox_source"] == "MOCK"
