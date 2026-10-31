from __future__ import annotations

from app.common.schemas import BoundingBox


def normalized_to_pixels(box: BoundingBox, width: int, height: int) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Displayed image dimensions must be positive")
    return (
        round(box.x_min * width),
        round(box.y_min * height),
        round(box.x_max * width),
        round(box.y_max * height),
    )


def suspicious_page_sized_box(box: BoundingBox, threshold: float = 0.90) -> bool:
    area = (box.x_max - box.x_min) * (box.y_max - box.y_min)
    return area >= threshold
