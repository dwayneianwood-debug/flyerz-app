"""Render zoom for a page whose point size is a 300 dpi pixel count.

2480 × 3508 pt is an A4 picture, not an 875 mm sheet. A zoom of 1 would
paint that whole picture. The same zoom on a real A4 page is about a
quarter of that, which is what the checks asked for.
"""

from __future__ import annotations

import json
import os

_PRODUCTS = None


def _products() -> list:
    global _PRODUCTS
    if _PRODUCTS is None:
        path = os.path.join(os.path.dirname(__file__), "..", "shared", "quick-print-products.json")
        with open(path, "r", encoding="utf-8") as handle:
            _PRODUCTS = json.load(handle)
    return _PRODUCTS


def pixels_as_points_scale(width_pt: float, height_pt: float) -> float:
    """How much to shrink a zoom so the picture matches the catalog page."""
    best = None
    for product in _products():
        expect_w = float(product["widthMm"]) / 25.4 * 300.0
        expect_h = float(product["heightMm"]) / 25.4 * 300.0
        if abs(float(width_pt) - expect_w) <= 8.0 and abs(float(height_pt) - expect_h) <= 8.0:
            err = abs(float(width_pt) - expect_w) + abs(float(height_pt) - expect_h)
            real_pt = float(product["widthMm"]) / 25.4 * 72.0
            scale = real_pt / max(float(width_pt), 1.0)
            if best is None or err < best[0]:
                best = (err, scale)
    return best[1] if best else 1.0


def adjusted_zoom(width_pt: float, height_pt: float, zoom: float) -> float:
    return float(zoom) * pixels_as_points_scale(width_pt, height_pt)
