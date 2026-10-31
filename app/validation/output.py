from __future__ import annotations

import re
from collections import Counter

from pydantic import ValidationError

from app.common.schemas import ExtractionResult, OutputValidationReport, PageRecord
from app.parsing.geometry import suspicious_page_sized_box


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_output_candidate(
    candidate: ExtractionResult | dict,
    *,
    expected_job_id: str,
    expected_attempt_id: str,
    expected_sha256: str,
    pages: list[PageRecord],
    allow_mock_boxes: bool,
) -> OutputValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        result = (
            candidate
            if isinstance(candidate, ExtractionResult)
            else ExtractionResult.model_validate(candidate)
        )
    except ValidationError as exc:
        return OutputValidationReport(ok=False, errors=[f"SCHEMA_ERROR:{exc}"])

    if str(result.job_id) != expected_job_id:
        errors.append("JOB_ID_MISMATCH")
    if str(result.attempt_id) != expected_attempt_id:
        errors.append("ATTEMPT_ID_MISMATCH")
    if result.document.sha256 != expected_sha256:
        errors.append("INPUT_HASH_MISMATCH")
    if result.document.page_count != len(pages):
        errors.append("PAGE_COUNT_MISMATCH")

    page_map = {page.page_number: page for page in pages}
    restriction_ids = [item.restriction_id for item in result.restrictions]
    duplicates = [key for key, count in Counter(restriction_ids).items() if count > 1]
    if duplicates:
        errors.append(f"DUPLICATE_RESTRICTION_IDS:{','.join(sorted(duplicates))}")

    for restriction in result.restrictions:
        if restriction.threshold is not None and not restriction.unit:
            errors.append(f"THRESHOLD_WITHOUT_UNIT:{restriction.restriction_id}")
        for evidence in restriction.evidence:
            page = page_map.get(evidence.page_number)
            if page is None:
                errors.append(f"UNKNOWN_EVIDENCE_PAGE:{restriction.restriction_id}")
                continue
            if evidence.bbox_source == "MOCK" and not allow_mock_boxes:
                errors.append(f"MOCK_BOX_IN_AWS_RESULT:{restriction.restriction_id}")
            if not evidence.bboxes:
                warnings.append(f"UNLOCALIZED_EVIDENCE:{restriction.restriction_id}")
            for box in evidence.bboxes:
                if suspicious_page_sized_box(box):
                    warnings.append(f"SUSPICIOUS_PAGE_SIZED_BOX:{restriction.restriction_id}")
            quote = _normalize_quote(evidence.quote)
            text = _normalize_quote(page.normalized_text)
            if quote and quote not in text:
                if page.low_text:
                    warnings.append(f"SCANNED_QUOTE_NOT_TEXT_VERIFIED:{restriction.restriction_id}")
                else:
                    errors.append(f"QUOTE_NOT_SUPPORTED:{restriction.restriction_id}")

    selected = set(result.coverage.selected_pages)
    known = set(page_map)
    if not selected.issubset(known):
        errors.append("UNKNOWN_SELECTED_PAGE")
    if result.coverage.failed_pages:
        warnings.append("PARTIAL_BATCH_FAILURE")

    return OutputValidationReport(ok=not errors, errors=errors, warnings=sorted(set(warnings)))
