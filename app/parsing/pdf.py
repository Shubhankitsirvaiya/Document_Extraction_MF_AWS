from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import fitz

from app.common.config import Settings
from app.common.schemas import InputValidationReport, PageRecord


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_pdf_bytes(
    pdf_bytes: bytes,
    settings: Settings,
    *,
    max_page_dimension: float = 20_000,
    max_render_megapixels: float = 20.0,
    max_parse_seconds: float = 30.0,
) -> InputValidationReport:
    """Validate actual PDF content before any model invocation."""

    errors: list[str] = []
    warnings: list[str] = []
    pages: list[PageRecord] = []
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    if not pdf_bytes:
        return InputValidationReport(ok=False, errors=["EMPTY_FILE"], sha256=sha256)
    if len(pdf_bytes) > settings.max_upload_bytes:
        return InputValidationReport(ok=False, errors=["FILE_TOO_LARGE"], sha256=sha256)
    if not pdf_bytes.startswith(b"%PDF-"):
        return InputValidationReport(ok=False, errors=["NOT_A_PDF"], sha256=sha256)

    started = time.monotonic()
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return InputValidationReport(ok=False, errors=["CORRUPT_OR_UNREADABLE_PDF"], sha256=sha256)

    try:
        if document.needs_pass:
            errors.append("ENCRYPTED_OR_PASSWORD_PROTECTED")
        page_count = document.page_count
        if page_count < 1:
            errors.append("ZERO_PAGE_PDF")
        if page_count > settings.max_pdf_pages:
            errors.append("PAGE_LIMIT_EXCEEDED")

        if not errors:
            for page_index in range(page_count):
                if time.monotonic() - started > max_parse_seconds:
                    errors.append("PDF_PARSE_TIMEOUT")
                    break

                page = document.load_page(page_index)
                rect = page.rect
                if rect.width > max_page_dimension or rect.height > max_page_dimension:
                    errors.append(f"PAGE_DIMENSION_EXCEEDED:{page_index + 1}")
                    continue

                render_width = max(int(rect.width * 2), 1)
                render_height = max(int(rect.height * 2), 1)
                megapixels = render_width * render_height / 1_000_000
                if megapixels > max_render_megapixels:
                    errors.append(f"RENDER_MEGAPIXEL_LIMIT:{page_index + 1}")
                    continue

                raw_text = page.get_text("text") or ""
                normalized = normalize_text(raw_text)
                low_text = len(normalized) < settings.low_text_threshold
                if low_text:
                    warnings.append(f"LOW_TEXT_PAGE:{page_index + 1}")

                pages.append(
                    PageRecord(
                        page_number=page_index + 1,
                        printed_page_label=page.get_label() or None,
                        raw_text=raw_text,
                        normalized_text=normalized,
                        width=rect.width,
                        height=rect.height,
                        rotation=page.rotation,
                        text_character_count=len(normalized),
                        low_text=low_text,
                        render_width=render_width,
                        render_height=render_height,
                        transform={
                            "scale": 2.0,
                            "rotation": page.rotation,
                            "origin": "top-left-upright-raster",
                        },
                    )
                )
    finally:
        document.close()

    return InputValidationReport(
        ok=not errors,
        errors=errors,
        warnings=sorted(set(warnings)),
        sha256=sha256,
        page_count=len(pages) if not errors else (page_count if "page_count" in locals() else None),
        pages=pages,
    )


def parse_and_render_pdf(
    pdf_path: Path,
    output_dir: Path,
    settings: Settings,
    *,
    scale: float = 2.0,
) -> tuple[InputValidationReport, list[PageRecord]]:
    """Validate, parse, render upright pages, and persist pages.json."""

    pdf_bytes = pdf_path.read_bytes()
    report = validate_pdf_bytes(pdf_bytes, settings)
    if not report.ok:
        return report, []

    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    rendered_pages: list[PageRecord] = []
    try:
        for page_record in report.pages:
            page = document.load_page(page_record.page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image_path = pages_dir / f"page-{page_record.page_number:03d}.png"
            pixmap.save(str(image_path))
            rendered_pages.append(
                page_record.model_copy(
                    update={
                        "image_artifact": str(image_path),
                        "render_width": pixmap.width,
                        "render_height": pixmap.height,
                        "transform": {
                            "scale": scale,
                            "rotation": page.rotation,
                            "origin": "top-left-upright-raster",
                        },
                    }
                )
            )
    finally:
        document.close()

    (output_dir / "pages.json").write_text(
        json.dumps([page.model_dump(mode="json") for page in rendered_pages], indent=2),
        encoding="utf-8",
    )
    return report, rendered_pages
