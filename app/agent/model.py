from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.config import Config
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompts import SYSTEM_PROMPT, batch_prompt
from app.common.config import Settings
from app.common.schemas import (
    BoundingBox,
    EvidenceRecord,
    LocalizationStatus,
    PageRecord,
    RestrictionCategory,
    RestrictionRecord,
    Scope,
)


class BatchPayload(BaseModel):
    restrictions: list[RestrictionRecord] = Field(default_factory=list)


@dataclass
class BatchExtraction:
    restrictions: list[RestrictionRecord]
    calls: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    warnings: list[str] | None = None


class VisionExtractor(ABC):
    @abstractmethod
    def extract_batch(self, pages: list[PageRecord], *, deadline: float) -> BatchExtraction:
        raise NotImplementedError


class MockVisionExtractor(VisionExtractor):
    """Deterministic local extractor; it never claims to be a real LLM extraction."""

    _restriction_pattern = re.compile(
        r"(?i)(shall\s+not|may\s+not|must\s+not|prohibited|not\s+more\s+than|"
        r"maximum|minimum|borrow|concentrat|derivative|eligible\s+investment)"
    )

    def extract_batch(self, pages: list[PageRecord], *, deadline: float) -> BatchExtraction:
        restrictions: list[RestrictionRecord] = []
        for page in pages:
            sentences = re.split(r"(?<=[.;])\s+|\n+", page.normalized_text)
            for sentence_index, sentence in enumerate(sentences):
                sentence = sentence.strip()
                if len(sentence) < 15 or not self._restriction_pattern.search(sentence):
                    continue
                percentage = re.search(r"(\d+(?:\.\d+)?)\s*%", sentence)
                fund_match = re.search(r"(?i)([A-Z][A-Za-z ]+ Fund)", sentence)
                y_min = min(0.08 + sentence_index * 0.055, 0.82)
                restrictions.append(
                    RestrictionRecord(
                        restriction_id=f"mock-{uuid4().hex[:10]}",
                        fund_name=fund_match.group(1) if fund_match else "Unknown fund",
                        scope=Scope.FUND if fund_match else Scope.UNKNOWN,
                        category=self._category(sentence),
                        restriction_text=sentence,
                        operator="<=" if percentage else None,
                        threshold=float(percentage.group(1)) if percentage else None,
                        unit="PERCENT" if percentage else None,
                        denominator="NAV" if percentage and "NAV" in sentence.upper() else None,
                        evidence=[
                            EvidenceRecord(
                                page_number=page.page_number,
                                quote=sentence,
                                bboxes=[
                                    BoundingBox(
                                        x_min=0.08,
                                        y_min=y_min,
                                        x_max=0.92,
                                        y_max=min(y_min + 0.045, 0.98),
                                    )
                                ],
                                bbox_source="MOCK",
                                localization_status=LocalizationStatus.UNVERIFIED,
                                source_image_width=page.render_width or 1,
                                source_image_height=page.render_height or 1,
                            )
                        ],
                        needs_review=True,
                        review_reasons=[
                            "Local mock extraction; no Bedrock model was called",
                            "Mock box is for UI testing and is not an LLM localization",
                        ],
                    )
                )
        return BatchExtraction(restrictions=restrictions, calls=0, warnings=[])

    @staticmethod
    def _category(text: str) -> RestrictionCategory:
        lowered = text.casefold()
        if "borrow" in lowered:
            return RestrictionCategory.BORROWING
        if "derivative" in lowered or "swap" in lowered or "future" in lowered:
            return RestrictionCategory.DERIVATIVES
        if "concentrat" in lowered:
            return RestrictionCategory.CONCENTRATION
        if "eligible investment" in lowered:
            return RestrictionCategory.ELIGIBLE_INVESTMENTS
        return RestrictionCategory.OTHER


class BedrockVisionExtractor(VisionExtractor):
    """Explicitly configured Bedrock Converse adapter with one bounded repair call."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        if not settings.bedrock_model_id:
            raise ValueError("BEDROCK_MODEL_ID is required in AWS mode; no model is guessed")
        self.settings = settings
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            config=Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 2}),
        )

    def extract_batch(self, pages: list[PageRecord], *, deadline: float) -> BatchExtraction:
        if time.monotonic() >= deadline:
            raise TimeoutError("Job deadline reached before Bedrock call")
        content: list[dict[str, Any]] = []
        descriptions: list[str] = []
        for page in pages:
            if not page.image_artifact:
                raise ValueError(f"Page {page.page_number} has no rendered image")
            image_bytes = Path(page.image_artifact).read_bytes()
            content.append({"image": {"format": "png", "source": {"bytes": image_bytes}}})
            descriptions.append(
                json.dumps(
                    {
                        "page_number": page.page_number,
                        "render_width": page.render_width,
                        "render_height": page.render_height,
                        "text": page.normalized_text[:18_000],
                    },
                    ensure_ascii=False,
                )
            )
        content.append({"text": batch_prompt(descriptions)})
        response = self._converse(content)
        calls = 1
        try:
            payload = BatchPayload.model_validate_json(self._response_text(response))
        except (ValidationError, json.JSONDecodeError) as first_error:
            if time.monotonic() >= deadline:
                raise TimeoutError("Job deadline reached before repair call") from first_error
            repair_content = [
                {
                    "text": (
                        "Repair the following invalid extraction into the exact "
                        "requested JSON schema. "
                        "Do not add unsupported facts. Invalid content:\n"
                        + self._response_text(response)[:20_000]
                    )
                }
            ]
            response = self._converse(repair_content)
            calls += 1
            payload = BatchPayload.model_validate_json(self._response_text(response))
        usage = response.get("usage", {})
        return BatchExtraction(
            restrictions=payload.restrictions,
            calls=calls,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            warnings=[],
        )

    def _converse(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        return self.client.converse(
            modelId=self.settings.bedrock_model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": 4096},
        )

    @staticmethod
    def _response_text(response: dict[str, Any]) -> str:
        blocks = response["output"]["message"]["content"]
        text = "\n".join(block["text"] for block in blocks if "text" in block).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        return text
