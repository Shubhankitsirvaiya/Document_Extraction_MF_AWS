from __future__ import annotations

import json

import pandas as pd
import requests
import streamlit as st
from PIL import Image

from app.common.config import load_settings
from app.common.schemas import BoundingBox
from app.common.status import is_terminal
from app.ui.api_client import SignedApiClient
from app.ui.overlay import draw_boxes

settings = load_settings()
st.set_page_config(page_title="MF Prospectus Restriction Extractor", layout="wide")
st.title("MF Prospectus Restriction Extractor")
st.caption(f"Deployment mode: **{settings.app_mode}**")

if settings.app_mode != "aws":
    st.error("This deployed application supports APP_MODE=aws only.")
    st.stop()
st.warning("Submitting a job can incur AWS and Bedrock charges.")


@st.cache_resource
def aws_client() -> SignedApiClient:
    return SignedApiClient(settings)


upload_tab, jobs_tab, results_tab, evidence_tab, pipeline_tab, aws_tab = st.tabs(
    ["Upload", "Jobs", "Results", "Evidence", "Pipeline", "AWS architecture/demo"]
)

with upload_tab:
    st.subheader("Upload a prospectus")
    st.write(f"PDF only, 1–{settings.max_pdf_pages} pages, at most {settings.max_upload_mib} MiB.")
    uploaded = st.file_uploader("Prospectus PDF", type=["pdf"])
    process_all = st.toggle("Process all pages for recall evaluation", value=False)
    if st.button("Create and submit job", disabled=uploaded is None):
        payload = uploaded.getvalue()
        if len(payload) > settings.max_upload_bytes:
            st.error("The uploaded file exceeds the configured size limit.")
        else:
            created = aws_client().request(
                "POST",
                "/jobs",
                {
                    "original_filename": uploaded.name,
                    "content_type": "application/pdf",
                    "size_bytes": len(payload),
                    "process_all_pages": process_all,
                },
            )
            upload_response = requests.put(
                created["upload_url"],
                data=payload,
                headers={"content-type": "application/pdf"},
                timeout=60,
            )
            upload_response.raise_for_status()
            submitted = aws_client().request("POST", f"/jobs/{created['job_id']}/submit")
            st.session_state["job_id"] = created["job_id"]
            st.query_params["job_id"] = created["job_id"]
            st.success(f"Submitted AWS job {submitted['job_id']}")

with jobs_tab:
    st.info("The current AWS job ID is kept in this browser session and in the URL.")
    requested_job = st.text_input("Open an AWS job ID", value=st.session_state.get("job_id", ""))
    if st.button("Open job") and requested_job:
        st.session_state["job_id"] = requested_job.strip()
        st.query_params["job_id"] = requested_job.strip()

job_id = st.session_state.get("job_id") or st.query_params.get("job_id")
if job_id:
    record = aws_client().request("GET", f"/jobs/{job_id}")
    result = (
        requests.get(record["result_url"], timeout=30).json() if record.get("result_url") else None
    )
else:
    record = None
    result = None

with results_tab:
    if not result:
        st.info("Select a completed job to view results.")
    else:
        restrictions = result.get("restrictions", [])
        st.metric("Restrictions", len(restrictions))
        if restrictions:
            rows = [
                {
                    "restriction_id": item["restriction_id"],
                    "fund": item["fund_name"],
                    "scope": item["scope"],
                    "category": item["category"],
                    "restriction": item["restriction_text"],
                    "needs_review": item["needs_review"],
                }
                for item in restrictions
            ]
            st.dataframe(rows, use_container_width=True)
            st.download_button(
                "Download JSON",
                json.dumps(result, indent=2),
                file_name=f"{job_id}-result.json",
                mime="application/json",
            )
            csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV", csv_bytes, f"{job_id}-restrictions.csv", "text/csv")

with evidence_tab:
    if not result or not result.get("restrictions"):
        st.info("No evidence is available for the selected job.")
    else:
        restrictions = result["restrictions"]
        selected_id = st.selectbox(
            "Restriction",
            [item["restriction_id"] for item in restrictions],
        )
        restriction = next(item for item in restrictions if item["restriction_id"] == selected_id)
        evidence = restriction["evidence"][0]
        image_url = record["evidence_image_urls"][str(evidence["page_number"])]
        image_response = requests.get(image_url, timeout=30, stream=True)
        image_response.raise_for_status()
        image = Image.open(image_response.raw)
        boxes = [BoundingBox.model_validate(item) for item in evidence["bboxes"]]
        st.image(draw_boxes(image, boxes), caption=f"Physical page {evidence['page_number']}")
        st.markdown(f"**Exact quote:** {evidence['quote']}")
        st.warning("Localization is unverified until a human reviews the visual alignment.")

with pipeline_tab:
    if not record:
        st.info("Select a job to inspect its pipeline metadata.")
    else:
        st.json(
            {
                "job_id": record["job_id"],
                "status": record["status"],
                "stage": record["stage"],
                "progress": record["progress"],
                "deployment_mode": record.get("deployment_mode", settings.app_mode),
                "model_calls": record.get("model_calls", 0),
                "warnings": record.get("warnings", []),
                "errors": record.get("errors", []),
            }
        )

with aws_tab:
    st.json(
        {
            "mode": settings.app_mode,
            "region": settings.aws_region,
            "api": settings.api_base_url or "disabled/not deployed",
            "agentcore_runtime": settings.agentcore_runtime_arn or "disabled/not deployed",
            "ecs_ui_enabled": settings.enable_ecs_ui,
            "bedrock_model": settings.bedrock_model_id or "not configured",
        }
    )

if record and not is_terminal(record["status"]):
    st.caption("Job is still running. Refresh this page in a few seconds to read persisted status.")
