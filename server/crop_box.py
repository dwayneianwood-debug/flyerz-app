"""Full-page crop_box for the No Crop Needed route (prevents Document Closed)."""

from __future__ import annotations

import unittest
from typing import Any, Optional

FULL_PAGE_CROP_NORMALIZED = {
    "cropX": 0.0,
    "cropY": 0.0,
    "cropWidth": 1.0,
    "cropHeight": 1.0,
}

FULL_PAGE_CROP_LIST = [0.0, 0.0, 1.0, 1.0]


def crop_box_missing(crop_box: Any) -> bool:
    if crop_box is None or crop_box is False:
        return True
    if isinstance(crop_box, (list, tuple)):
        if len(crop_box) < 4:
            return True
        try:
            return float(crop_box[2]) <= 0 or float(crop_box[3]) <= 0
        except (TypeError, ValueError):
            return True
    if isinstance(crop_box, dict):
        try:
            w = float(crop_box.get("cropWidth") or 0)
            h = float(crop_box.get("cropHeight") or 0)
        except (TypeError, ValueError):
            return True
        return crop_box.get("cropX") is None or crop_box.get("cropY") is None or w <= 0 or h <= 0
    return True


def is_no_crop_route(opts: Optional[dict] = None, page_state: Optional[dict] = None) -> bool:
    state = page_state or {}
    data = opts or {}
    return bool(
        data.get("isNoCrop")
        or data.get("is_no_crop")
        or data.get("preserveBleed")
        or state.get("is_no_crop")
        or state.get("isNoCrop")
        or state.get("preserveBleed")
    )


def ensure_full_page_crop_box(
    crop_box: Any = None,
    page_state: Optional[dict] = None,
    *,
    width: Any = None,
    height: Any = None,
) -> Any:
    """Populate full-page crop_box when No Crop / missing so compile never sees None."""
    if not crop_box_missing(crop_box):
        return crop_box
    state = page_state or {}
    dims = state.get("full_page_dimensions")
    if isinstance(dims, (list, tuple)) and len(dims) >= 4:
        try:
            if float(dims[2]) > 0 and float(dims[3]) > 0:
                return list(dims[:4])
        except (TypeError, ValueError):
            pass
    try:
        w = float(width) if width is not None else 0
        h = float(height) if height is not None else 0
    except (TypeError, ValueError):
        w, h = 0, 0
    if w > 0 and h > 0:
        return [0, 0, int(w), int(h)]
    return list(FULL_PAGE_CROP_LIST)


def ensure_full_page_crop_dict(opts: Optional[dict] = None, *, force: bool = False) -> dict:
    """Inject cropX/Y/Width/Height into bleed options when crop_box is missing.

    Default: only for the No Crop Needed route (preserveBleed / isNoCrop).
    force=True: fill any missing crop (compile/precompile Document Closed safety).
    """
    result = dict(opts or {})
    has_crop = not crop_box_missing({
        "cropX": result.get("cropX"),
        "cropY": result.get("cropY"),
        "cropWidth": result.get("cropWidth"),
        "cropHeight": result.get("cropHeight"),
    })
    if has_crop:
        if is_no_crop_route(result):
            result["isNoCrop"] = True
        return result
    if not force and not is_no_crop_route(result):
        return result
    result.update(FULL_PAGE_CROP_NORMALIZED)
    if is_no_crop_route(result):
        result["isNoCrop"] = True
    return result


class TestFullPageCropBox(unittest.TestCase):
    def test_missing_none_is_missing(self):
        self.assertTrue(crop_box_missing(None))
        self.assertTrue(crop_box_missing([]))
        self.assertTrue(crop_box_missing({"cropX": 0, "cropY": 0, "cropWidth": 0, "cropHeight": 1}))

    def test_no_crop_route_populates_normalized_full_page(self):
        out = ensure_full_page_crop_dict({"preserveBleed": True})
        self.assertEqual(out["cropX"], 0.0)
        self.assertEqual(out["cropY"], 0.0)
        self.assertEqual(out["cropWidth"], 1.0)
        self.assertEqual(out["cropHeight"], 1.0)
        self.assertTrue(out["isNoCrop"])

    def test_existing_manual_crop_is_preserved(self):
        src = {"cropX": 0.1, "cropY": 0.2, "cropWidth": 0.5, "cropHeight": 0.6}
        out = ensure_full_page_crop_dict(src)
        self.assertEqual(out["cropX"], 0.1)
        self.assertEqual(out["cropWidth"], 0.5)
        self.assertNotIn("isNoCrop", out)

    def test_plain_missing_crop_not_injected_without_no_crop_flag(self):
        out = ensure_full_page_crop_dict({"targetWidth": 148})
        self.assertNotIn("cropWidth", out)

    def test_force_fills_missing_crop_for_compile_safety(self):
        out = ensure_full_page_crop_dict({"targetWidth": 148}, force=True)
        self.assertEqual(out["cropWidth"], 1.0)
        self.assertEqual(out["cropHeight"], 1.0)

    def test_job_page_without_is_no_crop_still_gets_full_page_fallback(self):
        box = ensure_full_page_crop_box(None, {"page": "/job/208", "jobId": 208})
        self.assertEqual(box, [0.0, 0.0, 1.0, 1.0])

    def test_full_page_dimensions_from_page_state(self):
        box = ensure_full_page_crop_box(
            None,
            {"is_no_crop": True, "full_page_dimensions": [0, 0, 595, 842]},
        )
        self.assertEqual(box, [0, 0, 595, 842])

    def test_pixel_width_height_fallback(self):
        box = ensure_full_page_crop_box(None, width=1200, height=1800)
        self.assertEqual(box, [0, 0, 1200, 1800])


if __name__ == "__main__":
    unittest.main()
