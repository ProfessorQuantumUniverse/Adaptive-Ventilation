"""Render the brand assets in ``custom_components/adaptive_ventilation/brand/``.

The icon is generated rather than hand-drawn so it can be re-rendered at any
size without an editor, and so the @2x variants can never drift out of sync
with the ones they double.

The mark is three airflow strokes curling to the right - the same idea as the
sidebar icon (``mdi:weather-windy``), but drawn with uneven lengths, round caps
and a soft shadow, and optically centred, so it reads as a product logo rather
than as a glyph that happened to be pasted onto a square.

    python scripts/make_brand_assets.py

Needs Pillow, which is deliberately *not* a runtime dependency of the
integration - this only ever runs by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BRAND = Path(__file__).resolve().parents[1] / "custom_components" / "adaptive_ventilation" / "brand"

#: Supersampling factor. Pillow has no anti-aliased drawing, so draw big and
#: shrink; 6x is indistinguishable from a vector renderer at these sizes.
SS = 6
#: Canvas the shape constants below are expressed in, before the mark is
#: cropped to its own bounding box and re-centred.
SKETCH = 256

#: Deep evening blue to a fresh morning cyan.
GRADIENT_FROM = (10, 60, 140)
GRADIENT_TO = (0, 173, 205)
#: Corner radius of the app icon, as a fraction of its edge.
CORNER_RATIO = 0.225
#: How much of the icon edge the mark spans.
MARK_RATIO = 0.66

#: (x_start, y, x_end, curl radius, curl sweep in degrees). Deliberately
#: uneven - three identical strokes read as a barcode, not as moving air. The
#: sweep stops short of a full loop so every curl stays open, and the rows are
#: 60 apart with curls no taller than 42 so nothing ever collides: at this size
#: two strokes that touch turn into one grey blob.
STROKES: tuple[tuple[float, float, float, float, float], ...] = (
    (42.0, 68.0, 130.0, 19.0, 215.0),
    (28.0, 128.0, 158.0, 21.0, 220.0),
    (52.0, 188.0, 118.0, 18.0, 210.0),
)
STROKE_WIDTH = 16.0


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _diagonal_gradient(width: int, height: int) -> Image.Image:
    """A linear gradient running from the top-left corner to the bottom-right."""
    span = width + height - 2
    ramp = [_lerp(GRADIENT_FROM, GRADIENT_TO, i / span) for i in range(span + 1)]
    gradient = Image.new("RGB", (width, height))
    pixels = gradient.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ramp[x + y]
    return gradient


def _stroke_path(
    x_start: float, y: float, x_end: float, radius: float, sweep_degrees: float
) -> list[tuple[float, float]]:
    """A horizontal stroke that curls up and over to the right.

    The line runs from ``x_start`` to ``x_end`` at height ``y``; from there the
    path follows a circle of ``radius`` sitting directly above the end point,
    starting at its lowest point and sweeping clockwise on screen.
    """
    points: list[tuple[float, float]] = [(x_start, y), (x_end, y)]
    centre_x, centre_y = x_end, y - radius
    steps = max(12, round(sweep_degrees / 3))
    for step in range(steps + 1):
        angle = math.radians(90.0 - sweep_degrees * step / steps)
        points.append((centre_x + radius * math.cos(angle), centre_y + radius * math.sin(angle)))
    return points


def _resample(points: Sequence[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    """Walk a polyline and return points at most ``spacing`` apart."""
    dense: list[tuple[float, float]] = [points[0]]
    for (x0, y0), (x1, y1) in pairwise(points):
        distance = math.hypot(x1 - x0, y1 - y0)
        for step in range(1, max(1, math.ceil(distance / spacing)) + 1):
            t = step / max(1, math.ceil(distance / spacing))
            dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return dense


def _mark_shape() -> Image.Image:
    """The mark as an alpha mask, cropped tight to its own ink.

    Drawn by stamping a disc along the path rather than with ``draw.line``:
    a thick polyline with this many joints comes out visibly scalloped along
    the outside of every curl, and Pillow has no round caps either way.
    """
    canvas = Image.new("L", (SKETCH * SS, SKETCH * SS), 0)
    draw = ImageDraw.Draw(canvas)
    radius = STROKE_WIDTH * SS / 2.0

    for stroke in STROKES:
        coarse = [(x * SS, y * SS) for x, y in _stroke_path(*stroke)]
        for x, y in _resample(coarse, spacing=1.0):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)

    return canvas.crop(canvas.getbbox() or (0, 0, 1, 1))


def _mark_mask(size: int) -> Image.Image:
    """The mark scaled to ``MARK_RATIO`` of ``size`` and centred in that square."""
    shape = _mark_shape()
    extent = size * MARK_RATIO
    scale = min(extent / shape.width, extent / shape.height)
    scaled = shape.resize(
        (max(1, round(shape.width * scale)), max(1, round(shape.height * scale))), Image.LANCZOS
    )
    mask = Image.new("L", (size, size), 0)
    mask.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
    return mask


def _tint(mask: Image.Image, colour: tuple[int, int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", mask.size, (*colour[:3], 0))
    layer.putalpha(mask.point(lambda value: value * colour[3] // 255))
    return layer


def render_icon(size: int) -> Image.Image:
    """The rounded-square app icon: gradient, sheen, white mark, soft shadow."""
    icon = _diagonal_gradient(size, size).convert("RGBA")

    # A very soft light from the top-left keeps the flat gradient from reading
    # as a swatch. A blurred wedge is enough; a radial gradient is not worth it.
    sheen = Image.new("L", (size, size), 0)
    ImageDraw.Draw(sheen).polygon([(0, 0), (size * 0.9, 0), (0, size * 0.9)], fill=30)
    sheen = sheen.filter(ImageFilter.GaussianBlur(size / 5))
    icon = Image.composite(Image.new("RGBA", (size, size), (255, 255, 255, 255)), icon, sheen)

    mask = _mark_mask(size)
    shadow = _tint(mask, (3, 26, 60, 130)).filter(ImageFilter.GaussianBlur(size / 110))
    icon.alpha_composite(shadow, (0, max(1, round(size / 150))))
    icon.alpha_composite(_tint(mask, (255, 255, 255, 255)))

    rounded = Image.new("L", (size, size), 0)
    ImageDraw.Draw(rounded).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=round(size * CORNER_RATIO), fill=255
    )
    icon.putalpha(rounded)
    return icon


def render_logo(width: int, height: int) -> Image.Image:
    """The mark on transparent, gradient filled, centred in a wide canvas."""
    box = round(height * 0.92)
    mark = _diagonal_gradient(box, box).convert("RGBA")
    mark.putalpha(_mark_mask(box))

    logo = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    logo.alpha_composite(mark, ((width - box) // 2, (height - box) // 2))
    return logo


def main() -> None:
    outputs = {
        "icon.png": render_icon(256),
        "icon@2x.png": render_icon(512),
        "logo.png": render_logo(512, 256),
        "logo@2x.png": render_logo(1024, 512),
    }
    for name, image in outputs.items():
        (BRAND / name).parent.mkdir(parents=True, exist_ok=True)
        image.save(BRAND / name, "PNG", optimize=True)
        print(f"brand/{name}: {image.width}x{image.height}")


if __name__ == "__main__":
    main()
