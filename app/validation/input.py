from __future__ import annotations

from app.common.config import Settings
from app.common.schemas import InputValidationReport
from app.parsing.pdf import validate_pdf_bytes


def validate_input_document(pdf_bytes: bytes, settings: Settings) -> InputValidationReport:
    return validate_pdf_bytes(pdf_bytes, settings)
