from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.model import BedrockVisionExtractor, MockVisionExtractor, VisionExtractor
from app.agent.state import ExtractionState
from app.common.config import ROOT_DIR, Settings
from app.common.schemas import (
    CoverageInfo,
    DocumentInfo,
    ExtractionResult,
    InputValidationReport,
    OutputValidationReport,
    PageRecord,
    RestrictionRecord,
    UsageInfo,
)
from app.common.status import JobStatus
from app.parsing.pdf import parse_and_render_pdf
from app.parsing.selection import load_rules, select_pages
from app.validation.input import validate_input_document
from app.validation.output import validate_output_candidate

ProgressCallback = Callable[[str, int], None]
InputValidatorCallback = Callable[[bytes, ExtractionState], InputValidationReport]
OutputValidatorCallback = Callable[
    [ExtractionResult, list[PageRecord], ExtractionState], OutputValidationReport
]


def _noop_progress(stage: str, progress: int) -> None:
    del stage, progress


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        extractor: VisionExtractor | None = None,
        progress: ProgressCallback | None = None,
        input_validator: InputValidatorCallback | None = None,
        output_validator: OutputValidatorCallback | None = None,
    ) -> None:
        self.settings = settings
        self.extractor = extractor or (
            MockVisionExtractor()
            if settings.app_mode == "local_mock"
            else BedrockVisionExtractor(settings)
        )
        self.progress = progress or _noop_progress
        self.input_validator = input_validator
        self.output_validator = output_validator
        self.deadline = time.monotonic() + settings.max_job_seconds

    def validate_input(self, state: ExtractionState) -> dict[str, Any]:
        self.progress(JobStatus.VALIDATING_INPUT, 5)
        pdf_bytes = Path(state["pdf_path"]).read_bytes()
        report = (
            self.input_validator(pdf_bytes, state)
            if self.input_validator
            else validate_input_document(pdf_bytes, self.settings)
        )
        return {
            "stage": JobStatus.VALIDATING_INPUT,
            "progress": 10,
            "status": JobStatus.PARSING if report.ok else JobStatus.REJECTED,
            "input_report": report.model_dump(mode="json"),
            "errors": report.errors,
            "warnings": report.warnings,
        }

    @staticmethod
    def route_after_input(state: ExtractionState) -> str:
        return "parse" if state.get("status") != JobStatus.REJECTED else "finalize_rejected"

    def parse(self, state: ExtractionState) -> dict[str, Any]:
        self.progress(JobStatus.PARSING, 15)
        artifact_dir = Path(state["artifact_dir"])
        report, pages = parse_and_render_pdf(Path(state["pdf_path"]), artifact_dir, self.settings)
        if not report.ok:
            return {"status": JobStatus.REJECTED, "errors": report.errors}
        return {
            "status": JobStatus.SELECTING_PAGES,
            "stage": JobStatus.PARSING,
            "progress": 25,
            "pages": [page.model_dump(mode="json") for page in pages],
        }

    def select(self, state: ExtractionState) -> dict[str, Any]:
        self.progress(JobStatus.SELECTING_PAGES, 30)
        pages = [PageRecord.model_validate(item) for item in state["pages"]]
        rules, neighbor_radius, _ = load_rules(ROOT_DIR / "config" / "page_selection_rules.yaml")
        selection = select_pages(
            pages,
            rules,
            neighbor_radius=neighbor_radius,
            process_all_pages=state.get("process_all_pages", False),
        )
        return {
            "status": JobStatus.EXTRACTING,
            "stage": JobStatus.SELECTING_PAGES,
            "progress": 35,
            "selection": asdict(selection),
        }

    def extract(self, state: ExtractionState) -> dict[str, Any]:
        pages = [PageRecord.model_validate(item) for item in state["pages"]]
        page_map = {page.page_number: page for page in pages}
        selected = state["selection"]["selected_pages"]
        restrictions: list[RestrictionRecord] = []
        warnings = list(state.get("warnings", []))
        calls = 0
        input_tokens = 0
        output_tokens = 0
        batch_size = self.settings.model_page_batch_size
        total_batches = max((len(selected) + batch_size - 1) // batch_size, 1)

        for batch_index in range(0, len(selected), batch_size):
            if calls >= self.settings.max_model_calls:
                warnings.append("MODEL_CALL_CIRCUIT_BREAKER")
                break
            if time.monotonic() >= self.deadline:
                warnings.append("JOB_DEADLINE_REACHED")
                break
            batch_numbers = selected[batch_index : batch_index + batch_size]
            batch_pages = [page_map[number] for number in batch_numbers]
            try:
                extracted = self.extractor.extract_batch(batch_pages, deadline=self.deadline)
                restrictions.extend(extracted.restrictions)
                calls += extracted.calls
                input_tokens += extracted.input_tokens or 0
                output_tokens += extracted.output_tokens or 0
                warnings.extend(extracted.warnings or [])
            except Exception as exc:
                warnings.append(
                    f"BATCH_FAILED:{','.join(map(str, batch_numbers))}:{type(exc).__name__}"
                )
                state["selection"].setdefault("failed_pages", []).extend(batch_numbers)
            completed_batch = batch_index // batch_size + 1
            progress = 35 + round(35 * completed_batch / total_batches)
            self.progress(JobStatus.EXTRACTING, progress)

        return {
            "stage": JobStatus.EXTRACTING,
            "progress": 70,
            "restrictions": [item.model_dump(mode="json") for item in restrictions],
            "warnings": sorted(set(warnings)),
            "model_calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    @staticmethod
    def merge(state: ExtractionState) -> dict[str, Any]:
        unique: dict[tuple[str, int, str], dict[str, Any]] = {}
        for item in state.get("restrictions", []):
            evidence = item.get("evidence", [{}])[0]
            key = (
                item.get("fund_name", ""),
                int(evidence.get("page_number", 0)),
                " ".join(item.get("restriction_text", "").casefold().split()),
            )
            unique.setdefault(key, item)
        return {"restrictions": list(unique.values()), "progress": 75}

    def persist_candidate(self, state: ExtractionState) -> dict[str, Any]:
        report = state["input_report"]
        selection = state["selection"]
        failed_pages = selection.get("failed_pages", [])
        result = ExtractionResult(
            job_id=state["job_id"],
            attempt_id=state["attempt_id"],
            deployment_mode=self.settings.app_mode,
            document=DocumentInfo(sha256=report["sha256"], page_count=report["page_count"]),
            coverage=CoverageInfo(
                mode=selection["mode"],
                selected_pages=selection["selected_pages"],
                failed_pages=failed_pages,
                selection_reasons={
                    str(key): value for key, value in selection.get("reasons", {}).items()
                },
            ),
            restrictions=[RestrictionRecord.model_validate(item) for item in state["restrictions"]],
            warnings=state.get("warnings", []),
            usage=UsageInfo(
                model_id=(self.settings.bedrock_model_id or "LOCAL_MOCK_NO_MODEL"),
                calls=state.get("model_calls", 0),
                input_tokens=state.get("input_tokens") or None,
                output_tokens=state.get("output_tokens") or None,
            ),
        )
        candidate_path = Path(state["artifact_dir"]) / "candidate.json"
        candidate_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return {"candidate_path": str(candidate_path), "progress": 82}

    def validate_output(self, state: ExtractionState) -> dict[str, Any]:
        self.progress(JobStatus.VALIDATING_OUTPUT, 85)
        result = ExtractionResult.model_validate_json(Path(state["candidate_path"]).read_text())
        pages = [PageRecord.model_validate(item) for item in state["pages"]]
        report = (
            self.output_validator(result, pages, state)
            if self.output_validator
            else validate_output_candidate(
                result,
                expected_job_id=state["job_id"],
                expected_attempt_id=state["attempt_id"],
                expected_sha256=state["input_report"]["sha256"],
                pages=pages,
                allow_mock_boxes=self.settings.app_mode == "local_mock",
            )
        )
        report_path = Path(state["artifact_dir"]) / "output-validation.json"
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        if not report.ok:
            return {
                "status": JobStatus.FAILED,
                "errors": report.errors,
                "warnings": report.warnings,
                "progress": 90,
            }
        warnings = sorted(set(state.get("warnings", []) + report.warnings))
        status = JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED
        return {"status": status, "warnings": warnings, "progress": 92}

    @staticmethod
    def route_after_output(state: ExtractionState) -> str:
        return "finalize" if state.get("status") != JobStatus.FAILED else "finalize_failed"

    def finalize(self, state: ExtractionState) -> dict[str, Any]:
        candidate = Path(state["candidate_path"])
        result_path = Path(state["artifact_dir"]) / "result.json"
        result_path.write_bytes(candidate.read_bytes())
        self.progress(str(state["status"]), 100)
        return {"stage": str(state["status"]), "progress": 100, "result_path": str(result_path)}

    def finalize_rejected(self, state: ExtractionState) -> dict[str, Any]:
        path = Path(state["artifact_dir"]) / "rejection.json"
        path.write_text(
            json.dumps({"status": JobStatus.REJECTED, "errors": state.get("errors", [])}, indent=2),
            encoding="utf-8",
        )
        self.progress(JobStatus.REJECTED, 100)
        return {"stage": JobStatus.REJECTED, "progress": 100, "result_path": str(path)}

    def finalize_failed(self, state: ExtractionState) -> dict[str, Any]:
        path = Path(state["artifact_dir"]) / "failure.json"
        path.write_text(
            json.dumps(
                {
                    "status": JobStatus.FAILED,
                    "errors": state.get("errors", []),
                    "warnings": state.get("warnings", []),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.progress(JobStatus.FAILED, 100)
        return {"stage": JobStatus.FAILED, "progress": 100, "result_path": str(path)}

    def build(self):
        builder = StateGraph(ExtractionState)
        builder.add_node("validate_input", self.validate_input)
        builder.add_node("parse", self.parse)
        builder.add_node("select_pages", self.select)
        builder.add_node("extract_batches", self.extract)
        builder.add_node("merge", self.merge)
        builder.add_node("persist_candidate", self.persist_candidate)
        builder.add_node("validate_output", self.validate_output)
        builder.add_node("finalize", self.finalize)
        builder.add_node("finalize_rejected", self.finalize_rejected)
        builder.add_node("finalize_failed", self.finalize_failed)
        builder.add_edge(START, "validate_input")
        builder.add_conditional_edges(
            "validate_input",
            self.route_after_input,
            {"parse": "parse", "finalize_rejected": "finalize_rejected"},
        )
        builder.add_edge("parse", "select_pages")
        builder.add_edge("select_pages", "extract_batches")
        builder.add_edge("extract_batches", "merge")
        builder.add_edge("merge", "persist_candidate")
        builder.add_edge("persist_candidate", "validate_output")
        builder.add_conditional_edges(
            "validate_output",
            self.route_after_output,
            {"finalize": "finalize", "finalize_failed": "finalize_failed"},
        )
        builder.add_edge("finalize", END)
        builder.add_edge("finalize_rejected", END)
        builder.add_edge("finalize_failed", END)
        return builder.compile()


def run_pipeline(
    pdf_path: Path,
    artifact_dir: Path,
    settings: Settings,
    *,
    job_id: str,
    attempt_id: str,
    process_all_pages: bool = False,
    progress: ProgressCallback | None = None,
) -> ExtractionState:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pipeline = Pipeline(settings, progress=progress)
    graph = pipeline.build()
    initial: ExtractionState = {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "pdf_path": str(pdf_path),
        "artifact_dir": str(artifact_dir),
        "process_all_pages": process_all_pages,
        "status": JobStatus.VALIDATING_INPUT,
        "stage": JobStatus.VALIDATING_INPUT,
        "progress": 0,
        "warnings": [],
        "errors": [],
        "model_calls": 0,
    }
    return graph.invoke(initial)
