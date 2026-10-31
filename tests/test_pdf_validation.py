from __future__ import annotations

import fitz

from app.common.config import Settings
from app.parsing.pdf import validate_pdf_bytes


def test_one_and_twenty_page_documents_are_accepted(make_pdf):
    settings = Settings(low_text_threshold=0)
    one = make_pdf(["Investment restrictions apply."], filename="one.pdf")
    twenty = make_pdf([f"Page {number}" for number in range(1, 21)], filename="twenty.pdf")

    assert validate_pdf_bytes(one.read_bytes(), settings).ok
    report = validate_pdf_bytes(twenty.read_bytes(), settings)
    assert report.ok
    assert report.page_count == 20


def test_twenty_one_pages_are_rejected(make_pdf):
    path = make_pdf([f"Page {number}" for number in range(1, 22)])
    report = validate_pdf_bytes(path.read_bytes(), Settings())
    assert not report.ok
    assert "PAGE_LIMIT_EXCEEDED" in report.errors


def test_non_pdf_and_corrupt_pdf_are_rejected():
    settings = Settings()
    assert validate_pdf_bytes(b"hello", settings).errors == ["NOT_A_PDF"]
    corrupt = validate_pdf_bytes(b"%PDF-1.7\nnot really a pdf", settings)
    assert "CORRUPT_OR_UNREADABLE_PDF" in corrupt.errors


def test_password_protected_pdf_is_rejected(tmp_path):
    source = fitz.open()
    source.new_page().insert_text((72, 72), "Restricted")
    path = tmp_path / "encrypted.pdf"
    source.save(
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    source.close()

    report = validate_pdf_bytes(path.read_bytes(), Settings())
    assert not report.ok
    assert "ENCRYPTED_OR_PASSWORD_PROTECTED" in report.errors
