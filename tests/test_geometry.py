from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.schemas import BoundingBox
from app.parsing.geometry import normalized_to_pixels, suspicious_page_sized_box


def test_normalized_box_converts_to_display_pixels():
    box = BoundingBox(x_min=0.1, y_min=0.2, x_max=0.9, y_max=0.8)
    assert normalized_to_pixels(box, 1000, 500) == (100, 100, 900, 400)
    assert not suspicious_page_sized_box(box)


def test_invalid_box_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        BoundingBox(x_min=0.8, y_min=0.2, x_max=0.1, y_max=0.8)
