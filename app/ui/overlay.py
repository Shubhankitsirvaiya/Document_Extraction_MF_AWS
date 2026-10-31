from __future__ import annotations

from PIL import Image, ImageDraw

from app.common.schemas import BoundingBox
from app.parsing.geometry import normalized_to_pixels


def draw_boxes(image: Image.Image, boxes: list[BoundingBox]) -> Image.Image:
    rendered = image.convert("RGBA")
    overlay = Image.new("RGBA", rendered.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for box in boxes:
        pixels = normalized_to_pixels(box, rendered.width, rendered.height)
        draw.rectangle(pixels, fill=(255, 153, 0, 55), outline=(255, 90, 0, 230), width=4)
    return Image.alpha_composite(rendered, overlay)
