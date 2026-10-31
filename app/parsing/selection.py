from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.common.schemas import PageRecord


@dataclass(frozen=True)
class Rule:
    rule_id: str
    label: str
    pattern: re.Pattern[str]
    weight: int


@dataclass(frozen=True)
class PageSelection:
    mode: str
    selected_pages: list[int]
    excluded_pages: list[int]
    reasons: dict[int, list[str]]
    hits: dict[int, list[str]]


def load_rules(path: Path) -> tuple[list[Rule], int, int]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = [
        Rule(
            rule_id=item["id"],
            label=item["label"],
            pattern=re.compile(item["regex"], re.IGNORECASE | re.MULTILINE),
            weight=int(item.get("weight", 1)),
        )
        for item in payload["patterns"]
    ]
    return rules, int(payload.get("neighbor_radius", 1)), int(payload.get("low_text_threshold", 80))


def select_pages(
    pages: list[PageRecord],
    rules: list[Rule],
    *,
    neighbor_radius: int = 1,
    process_all_pages: bool = False,
) -> PageSelection:
    page_numbers = {page.page_number for page in pages}
    if process_all_pages:
        selected = sorted(page_numbers)
        return PageSelection(
            mode="all_pages",
            selected_pages=selected,
            excluded_pages=[],
            reasons={number: ["PROCESS_ALL_PAGES"] for number in selected},
            hits={},
        )

    direct: set[int] = set()
    reasons: dict[int, list[str]] = {}
    hits: dict[int, list[str]] = {}

    for page in pages:
        if page.low_text:
            direct.add(page.page_number)
            reasons.setdefault(page.page_number, []).append("LOW_TEXT_VISION_PATH")

        for rule in rules:
            matches = [
                match.group(0)[:120] for match in rule.pattern.finditer(page.normalized_text)
            ]
            if matches:
                direct.add(page.page_number)
                reasons.setdefault(page.page_number, []).append(f"REGEX:{rule.rule_id}")
                hits.setdefault(page.page_number, []).extend(matches[:5])

    if not direct:
        selected = sorted(page_numbers)
        return PageSelection(
            mode="fallback_all_pages",
            selected_pages=selected,
            excluded_pages=[],
            reasons={number: ["NO_REGEX_MATCH_FALLBACK"] for number in selected},
            hits=hits,
        )

    selected_set = set(direct)
    for page_number in sorted(direct):
        for neighbor in range(page_number - neighbor_radius, page_number + neighbor_radius + 1):
            if neighbor in page_numbers and neighbor not in selected_set:
                selected_set.add(neighbor)
                reasons.setdefault(neighbor, []).append(f"NEIGHBOR_OF:{page_number}")

    selected = sorted(selected_set)
    return PageSelection(
        mode="regex_plus_neighbors",
        selected_pages=selected,
        excluded_pages=sorted(page_numbers - selected_set),
        reasons=reasons,
        hits=hits,
    )
