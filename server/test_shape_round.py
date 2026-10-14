"""Shape fit, bleed note, customer wording, and the small-text check for this round."""

from __future__ import annotations

import json
import os
import sys
import time
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from customer_message import customer_message  # noqa: E402
from extra_checks import check_e5  # noqa: E402
from press_ready_engine import compile_raster_canvas  # noqa: E402
from quick_print import (  # noqa: E402
    _record_bleed_note,
    aspect_fit,
    decide_light,
    shape_extension_note,
)
from vector_plate import _enlarge, place_plate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHOOL = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14", "school_readiness.jpg")
CANVA = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14", "0547d0ea_canva.pdf")


class ShapeRoundTest(unittest.TestCase):
    def test_cover_within_2mm_and_contain_past_it(self):
        near = aspect_fit(1000, 1400, 210, 297)
        self.assertEqual(near["mode"], "cover")
        self.assertLessEqual(max(near["cropX"], near["cropY"]), 2.05)
        school = aspect_fit(1024, 1536, 210, 297)
        self.assertEqual(school["mode"], "contain")
        self.assertAlmostEqual(school["gapX"], 6.0, delta=0.15)
        self.assertAlmostEqual(school["gapY"], 0.0, delta=0.15)
        self.assertEqual(
            shape_extension_note("A4", school["gapX"], school["gapY"]),
            "Your picture is a different shape from A4, so we extended the background at the sides",
        )
        self.assertIn("top and bottom", shape_extension_note("A5", 0.2, 4.0))
        matched = aspect_fit(2100, 2970, 210, 297)
        self.assertNotEqual(matched["mode"], "contain")

    def test_school_picture_is_not_smeared_inside_the_trim(self):
        image = cv2.imread(SCHOOL, cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        canvas, report = compile_raster_canvas(image, 210.0, 297.0, 5.0, None)
        self.assertEqual((report.get("shape") or {}).get("mode"), "contain")
        self.assertTrue(str(report.get("bleedNote") or "").startswith("Bleed:"))
        for side in ("top", "bottom", "left", "right"):
            self.assertIn(side, report["bleedNote"])
        box = report.get("artBox") or []
        self.assertEqual(len(box), 4)
        x0, y0, width, height = [int(v) for v in box]
        bleed = int(round(5.0 / 25.4 * 300.0))
        placed = canvas[y0 + bleed:y0 + bleed + height, x0 + bleed:x0 + bleed + width]
        scale = min(width / image.shape[1], height / image.shape[0])
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        resized = cv2.resize(image, (width, height), interpolation=interp)
        delta = float(np.abs(placed.astype(np.int16) - resized.astype(np.int16)).mean())
        self.assertLess(delta, 0.05, f"artwork inside the trim moved ({delta})")
        # The old feather reached about 6 mm inside the cut. That band is the art edge.
        band = int(round(6.0 / 25.4 * 300.0))
        edge = placed[:, :band]
        fresh = resized[:, :band]
        self.assertLess(float(np.abs(edge.astype(np.int16) - fresh.astype(np.int16)).mean()), 0.05)

    def test_plate_keeps_art_and_names_bleed(self):
        rng = np.random.default_rng(4)
        picture = rng.integers(0, 255, (300, 200, 3), dtype=np.uint8)
        placed = place_plate(picture, [], 210.0, 297.0, 5.0, ppi=300)
        self.assertEqual(placed["shape"]["mode"], "contain")
        x0, y0, width, height = placed["art_box"]
        art = placed["image"][y0:y0 + height, x0:x0 + width]
        enlarged, _provider = _enlarge(picture, width, height)
        delta = float(np.abs(art.astype(np.int16) - enlarged.astype(np.int16)).mean())
        self.assertLess(delta, 0.05)
        decisions = ["Bleed is 5 mm on every side."]
        engine = {"edges": placed["edges"], "shape": placed["shape"], "headline": "Vector type"}
        _record_bleed_note(decisions, engine, placed["edges"])
        note = str(engine.get("bleedNote") or "")
        self.assertTrue(note.startswith("Bleed:"), note)
        for side in ("top", "bottom", "left", "right"):
            self.assertIn(side, note)
        info = decide_light({
            "compiled": True,
            "enginePassed": True,
            "shapeNote": shape_extension_note("A4", placed["shape"]["gapX"], placed["shape"]["gapY"]),
            "upscale": 1,
        })
        self.assertEqual(info["light"], "amber")
        self.assertIn("different shape from A4", " ".join(info["reasons"]))
        message = customer_message(decisions, [
            "Resolution / DPI Validation: About 176 ppi.",
            "The lowest picture is about 176 ppi (school_readiness.jpg (176 ppi)). It needs about 300 ppi.",
            "This picture is about 185 DPI at this size.",
            "Ordered product vs file size: Before 1024×1536 px – product 210×297 mm.",
            "E1. Ordered product vs file size: Page 1 aspect differs by 5%. The gap is filled.",
            "3b. AI Resolution Enhancement: Upscale was not applied.",
        ], note)
        self.assertIn(note, message)
        self.assertEqual(message.count("Ordered product vs file size"), 1)
        self.assertEqual(message.count("Resolution:"), 1)
        self.assertNotIn("lowest picture", message.lower())
        self.assertNotIn("185 DPI", message)
        self.assertIn("AI Resolution Enhancement:", message)
        self.assertIn("×", message)
        self.assertIn("–", message)
        self.assertNotIn("\ufffd", message)
        raw = json.dumps({"customerMessage": message}, ensure_ascii=False).encode("utf-8")
        parts = [raw[index:index + 3] for index in range(0, len(raw), 3)]
        restored = b"".join(parts).decode("utf-8")
        self.assertEqual(restored, raw.decode("utf-8"))
        self.assertIn("×", restored)
        self.assertIn("–", restored)

    def test_canva_small_text_check_is_one_render(self):
        started = time.perf_counter()
        row = check_e5(CANVA)
        elapsed = time.perf_counter() - started
        self.assertIn("5.2", str(row.get("detail") or ""))
        self.assertLess(elapsed, 8.0, f"check_e5 took {elapsed:.2f}s")

    def test_one_picture_xref_does_not_decode_the_plate(self):
        import pymupdf as fitz

        from image_info_cache import install_image_info_cache

        install_image_info_cache()
        folder = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14")
        path = os.path.join(folder, "school_readiness.jpg")
        image = cv2.imread(path)
        self.assertIsNotNone(image)
        doc = fitz.open()
        page = doc.new_page(width=200, height=300)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        page.insert_image(page.rect, stream=encoded.tobytes())
        calls = {"n": 0}
        real = fitz.Pixmap

        def counting(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        fitz.Pixmap = counting
        try:
            info = page.get_image_info(xrefs=True)
            again = page.get_image_info(xrefs=True)
        finally:
            fitz.Pixmap = real
            doc.close()
        self.assertEqual(calls["n"], 0)
        self.assertEqual(len(info), 1)
        self.assertEqual(int(info[0]["xref"]), int(again[0]["xref"]))
        self.assertGreater(int(info[0]["width"]), 8)
        self.assertEqual(int(info[0]["width"]), int(again[0]["width"]))

    def test_split_trace_matches_one_stack(self):
        from vector_trace import _potrace_chunk, _potrace_placed, mask_iou, rasterise_paths

        bitmap = np.zeros((96, 96), np.uint8)
        cv2.circle(bitmap, (48, 48), 28, 255, 6)
        cv2.circle(bitmap, (48, 48), 10, 255, -1)
        bitmaps = [bitmap.copy() for _ in range(4)]
        stacked = [[] for _ in range(4)]
        _potrace_chunk(bitmaps, [0, 1, 2, 3], stacked)
        alone_results = [[] for _ in range(4)]
        for index in range(4):
            _potrace_chunk(bitmaps, [index], alone_results)
        placed = _potrace_placed(bitmaps)
        for index in range(4):
            self.assertTrue(placed[index])
            left = rasterise_paths(stacked[index], 96, 96)
            right = rasterise_paths(alone_results[index], 96, 96)
            self.assertGreaterEqual(mask_iou(left, right), 0.98)

    def test_region_scores_stay_on_the_picture(self):
        from vector_trace import _judge_many

        mask = np.zeros((16, 16), np.uint8)
        item = {"mask": mask, "crop": np.zeros((16, 16, 3), np.uint8), "inner": (0, 0, 16, 16), "colour": (0, 0, 0)}
        scored = _judge_many([(item, []) for _ in range(3)], 300.0)
        self.assertEqual(len(scored), 3)
        self.assertTrue(all(row.get("raster") for row in scored))

    def test_glyph_overlap_count_matches_the_full_mask(self):
        from vector_trace import _components, _glyph_structure_fails, _shared_ink

        mask = np.zeros((48, 120), np.uint8)
        mask[12:36, 8:28] = 255
        mask[12:36, 70:96] = 255
        parts = _components(mask, 8)
        self.assertEqual(len(parts), 2)
        self.assertEqual(_shared_ink(parts[0], parts[1]), int(np.count_nonzero(parts[0]["pixels"] & parts[1]["pixels"])))
        self.assertFalse(_glyph_structure_fails(mask, mask))


if __name__ == "__main__":
    unittest.main()
