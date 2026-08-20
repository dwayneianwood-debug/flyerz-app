"""
Full-page crop_box helpers for the "No Crop Needed" route.

When the user skips drawing a crop, the backend MUST still populate crop_box
with full-page dimensions (normalized 0,0,1,1) so compile/bleed never see
None — that path previously hung the job UI ("its stuck") and could raise
Document Closed errors.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

FULL_PAGE_CROP_BOX = {
    "cropX": 0.0,
    "cropY": 0.0,
    "cropWidth": 1.0,
    "cropHeight": 1.0,
}

FULL_PAGE_CROP_LIST = [0.0, 0.0, 1.0, 1.0]

_EPS = 1e-6


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_full_page_crop_box(
    crop_x: Any = None,
    crop_y: Any = None,
    crop_w: Any = None,
    crop_h: Any = None,
) -> bool:
    """True when coords are the normalized full-page box (0,0,1,1)."""
    x = _as_float(crop_x)
    y = _as_float(crop_y)
    w = _as_float(crop_w)
    h = _as_float(crop_h)
    if None in (x, y, w, h):
        return False
    return (
        abs(x) < _EPS
        and abs(y) < _EPS
        and abs(w - 1.0) < _EPS
        and abs(h - 1.0) < _EPS
    )


def crop_coords_from_mapping(opts: Optional[Mapping[str, Any]]) -> Optional[tuple]:
    if not opts:
        return None
    x = opts.get("cropX", opts.get("crop_x"))
    y = opts.get("cropY", opts.get("crop_y"))
    w = opts.get("cropWidth", opts.get("crop_w"))
    h = opts.get("cropHeight", opts.get("crop_h"))
    fx, fy, fw, fh = _as_float(x), _as_float(y), _as_float(w), _as_float(h)
    if None in (fx, fy, fw, fh):
        return None
    return fx, fy, fw, fh


def has_user_manual_crop(opts: Optional[Mapping[str, Any]] = None, **coords: Any) -> bool:
    """True when a real (non-full-page) crop region was supplied."""
    if coords:
        x = coords.get("crop_x", coords.get("cropX"))
        y = coords.get("crop_y", coords.get("cropY"))
        w = coords.get("crop_w", coords.get("cropWidth"))
        h = coords.get("crop_h", coords.get("cropHeight"))
        fx, fy, fw, fh = _as_float(x), _as_float(y), _as_float(w), _as_float(h)
    else:
        parsed = crop_coords_from_mapping(opts)
        if not parsed:
            return False
        fx, fy, fw, fh = parsed
    if None in (fx, fy, fw, fh):
        return False
    if fw <= 0 or fh <= 0:
        return False
    return not is_full_page_crop_box(fx, fy, fw, fh)


def ensure_crop_box(opts: Optional[Mapping[str, Any]] = None) -> dict:
    """Return bleed-option dict with a valid crop_box (full-page when missing)."""
    result = dict(opts) if opts else {}
    parsed = crop_coords_from_mapping(result)
    if parsed and parsed[2] > 0 and parsed[3] > 0:
        result["cropX"], result["cropY"], result["cropWidth"], result["cropHeight"] = parsed
        return result
    result.update(FULL_PAGE_CROP_BOX)
    return result


def resolve_missing_crop_box(crop_box: Any, page_state: Optional[dict] = None) -> Any:
    """
    Populate full-page crop_box when the No Crop route left it empty.

    Used by Glitchy agent prompts AND as the canonical fallback whenever a
    job page reports crop_box=None (user said "its stuck").
    """
    if crop_box:
        return crop_box
    state = page_state or {}
    dims = state.get("full_page_dimensions")
    if dims:
        return dims
    if state.get("is_no_crop"):
        return list(FULL_PAGE_CROP_LIST)
    page = state.get("page") or ""
    if state.get("jobId") is not None or (isinstance(page, str) and "/job/" in page):
        return list(FULL_PAGE_CROP_LIST)
    return crop_box


if __name__ == "__main__":
    import unittest

    class TestCropBox(unittest.TestCase):
        def test_full_page_and_ensure(self):
            self.assertTrue(is_full_page_crop_box(0, 0, 1, 1))
            self.assertFalse(has_user_manual_crop(FULL_PAGE_CROP_BOX))
            self.assertTrue(has_user_manual_crop({"cropX": 0.2, "cropY": 0.2, "cropWidth": 0.5, "cropHeight": 0.5}))
            filled = ensure_crop_box(None)
            self.assertEqual(filled["cropWidth"], 1.0)
            self.assertEqual(
                resolve_missing_crop_box(None, {"page": "/job/208", "jobId": 208}),
                FULL_PAGE_CROP_LIST,
            )

    unittest.main()
