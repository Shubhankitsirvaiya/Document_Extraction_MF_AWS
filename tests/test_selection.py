from __future__ import annotations

import re

from app.common.schemas import PageRecord
from app.parsing.selection import Rule, select_pages


def page(number: int, text: str, *, low_text: bool = False) -> PageRecord:
    return PageRecord(
        page_number=number,
        raw_text=text,
        normalized_text=text,
        width=612,
        height=792,
        rotation=0,
        text_character_count=len(text),
        low_text=low_text,
    )


RULES = [Rule("borrowing", "Borrowing", re.compile("borrow", re.I), 1)]


def test_regex_hit_includes_neighbors():
    selection = select_pages(
        [page(1, "Introduction"), page(2, "The fund may not borrow."), page(3, "Appendix")],
        RULES,
        neighbor_radius=1,
    )
    assert selection.mode == "regex_plus_neighbors"
    assert selection.selected_pages == [1, 2, 3]
    assert selection.reasons[2] == ["REGEX:borrowing"]


def test_no_match_falls_back_to_all_pages():
    selection = select_pages([page(1, "Overview"), page(2, "Fees")], RULES)
    assert selection.mode == "fallback_all_pages"
    assert selection.selected_pages == [1, 2]


def test_low_text_page_takes_vision_path_and_process_all_is_explicit():
    low_text = select_pages(
        [page(1, "", low_text=True), page(2, "Overview")], RULES, neighbor_radius=0
    )
    assert low_text.selected_pages == [1]
    assert low_text.reasons[1] == ["LOW_TEXT_VISION_PATH"]

    all_pages = select_pages([page(1, "Overview"), page(2, "Fees")], RULES, process_all_pages=True)
    assert all_pages.mode == "all_pages"
    assert all_pages.selected_pages == [1, 2]
