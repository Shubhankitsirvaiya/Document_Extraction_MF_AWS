from __future__ import annotations

from pathlib import Path

import fitz
import pytest


@pytest.fixture
def make_pdf(tmp_path: Path):
    """Return a helper that creates small, deterministic PDFs without cloud calls."""

    def _make(page_texts: list[str], *, filename: str = "document.pdf") -> Path:
        document = fitz.open()
        for text in page_texts:
            page = document.new_page(width=612, height=792)
            page.insert_textbox(fitz.Rect(60, 60, 552, 732), text, fontsize=11)
        path = tmp_path / filename
        document.save(path)
        document.close()
        return path

    return _make
