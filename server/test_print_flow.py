"""Notes, checklist labels, and the one-pass bleed change for the print-ready flow."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from customer_message import customer_message  # noqa: E402
from intake_check import assess_intake, severity_for  # noqa: E402
from job_progress import write_progress  # noqa: E402
from page_zoom import adjusted_zoom  # noqa: E402
from quick_print import extra_bleed_lines, incoming_bleed_line, press_saved_for_rebleed  # noqa: E402
from bleed_reapply import bleed_sentence  # noqa: E402


class PrintFlowTests(unittest.TestCase):
    def test_over_bleed_has_one_trim_sentence(self):
        line, story = incoming_bleed_line({
            "kind": "existing",
            "left": 10.0,
            "right": 10.0,
            "top": 10.0,
            "bottom": 10.0,
        })
        self.assertEqual(story, "over")
        self.assertIn("trimmed to 5 mm", line)
        self.assertNotIn("shortfall", line)
        kept, extra = extra_bleed_lines(True, True, True, True)
        self.assertTrue(kept)
        self.assertEqual(extra, [])
        kept_trim, extra_trim = extra_bleed_lines(False, True, True, True, trimmed=True)
        self.assertEqual(extra_trim, [])
        joined = line + " " + " ".join(extra)
        self.assertNotIn("already had 5 mm", joined)
        self.assertNotIn("shortfall is added", joined)

    def test_no_trim_box_overbleed_card_has_one_note(self):
        import pymupdf as fitz

        src = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "ian_2026-10-13", "overbleed_a5_10mm.pdf")
        folder = tempfile.mkdtemp(prefix="over-note-")
        opened = os.path.join(folder, "no-trim.pdf")
        doc = fitz.open(src)
        page = doc[0]
        page.set_trimbox(page.mediabox)
        doc.save(opened)
        doc.close()
        from quick_print import make_print_ready

        result = make_print_ready(opened, os.path.join(folder, "out"), 148, 210, "a5", "A5", filename="no-trim.pdf")
        text = "\n".join(str(line) for line in (result.get("decisions") or []))
        self.assertIn("trimmed to 5 mm", text)
        self.assertNotIn("shortfall is added", text)
        self.assertNotIn("already had 5 mm", text)
        self.assertIn("Bleed:", str(result.get("bleedNote") or ""))

    def test_partial_bleed_still_names_the_shortfall(self):
        line, story = incoming_bleed_line({
            "kind": "existing",
            "left": 2.5,
            "right": 2.5,
            "top": 2.5,
            "bottom": 2.5,
        })
        self.assertEqual(story, "partial")
        self.assertIn("shortfall is added", line)
        kept, extra = extra_bleed_lines(False, True, True, True)
        self.assertTrue(kept)
        self.assertTrue(any("already had 5 mm" in row for row in extra))

    def test_pixels_as_points_zoom_is_about_a_quarter(self):
        zoom = adjusted_zoom(2480, 3508, 1.0)
        self.assertLess(zoom, 0.3)
        self.assertGreater(zoom, 0.2)

    def test_small_text_is_amber(self):
        self.assertEqual(severity_for("warning"), "amber")
        self.assertEqual(severity_for("failed"), "red")
        office = assess_intake("notes.docx", 210, 297)
        self.assertEqual(office["items"][0]["severity"], "red")
        self.assertIn("PowerPoint", office["items"][0]["detail"])

    def test_customer_message_names_the_work_and_the_concerns(self):
        plain = customer_message(["Bleed: top/bottom flat colour, left/right mirrored photo", "Converted to CMYK."], [])
        self.assertTrue(plain.startswith("Your artwork has successfully been made print ready."))
        self.assertIn("Bleed:", plain)
        worried = customer_message(
            ["Added 5 mm bleed.", "Converted to CMYK.", "Brought content in 2 mm."],
            ["Small text: we strongly recommend larger text.", "Low resolution: accepted and continued."],
        )
        self.assertTrue(worried.startswith(
            "Your artwork has successfully been made print ready, but to ensure you are 100% happy we would recommend checking the points below."
        ))
        self.assertIn("Brought content in 2 mm", worried)
        settled = customer_message(
            ["Bleed: top/bottom/left/right trimmed to 5 mm.", "The press file was converted to CMYK."],
            [
                "Bleed Detection: There is no bleed past the trim.",
                "CMYK: The file is RGB. It still needs a CMYK conversion.",
                "Small text: we strongly recommend larger text.",
            ],
        )
        self.assertNotIn("no bleed", settled.lower())
        self.assertNotIn("still needs a CMYK", settled)
        self.assertIn("strongly recommend larger text", settled)
        self.assertIn("Please have a look at this: Small text", worried)
        self.assertIn("Low resolution", worried)
        repeated = customer_message(
            ["Bleed: top/bottom flat colour, left/right mirrored photo", "The press file was converted to CMYK."],
            [
                "3. Resolution / DPI Validation: About 72 ppi.",
                "3b. AI Resolution Enhancement: Upscale was not applied in this check.",
                "Resolution / DPI Validation: About 72 ppi.",
                "AI Resolution Enhancement: Upscale was not applied in this check.",
                "Small text: we strongly recommend larger text.",
            ],
        )
        self.assertEqual(repeated.lower().count("please have a look at this: resolution:"), 1)
        self.assertEqual(repeated.lower().count("ai resolution enhancement"), 1)
        self.assertNotIn("3b.", repeated)
        self.assertNotIn("3. Resolution", repeated)
        self.assertIn("Bleed: top/bottom flat colour, left/right mirrored photo", repeated)
        self.assertIn("strongly recommend larger text", repeated)

    def test_each_fix_has_its_own_percent(self):
        folder = tempfile.mkdtemp(prefix="flow-progress-")
        path = os.path.join(folder, "1.json")
        write_progress(path, "press")
        import json
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = {row["id"]: row["percent"] for row in payload["fixes"]}
        self.assertEqual(rows["checking"], 100)
        self.assertEqual(rows["press"], 78)
        self.assertEqual(rows["proof"], 0)

    def test_reapply_uses_the_chosen_method_without_a_new_job(self):
        import numpy as np

        from bleed_reapply import reapply_bleed
        from press_ready_engine import write_cmyk_pdf

        folder = tempfile.mkdtemp(prefix="rebleed-")
        bleed = max(1, int(round(5.0 / 25.4 * 300.0)))
        trim_w = max(1, int(round(40.0 / 25.4 * 300.0)))
        trim_h = max(1, int(round(30.0 / 25.4 * 300.0)))
        art = np.zeros((trim_h + 2 * bleed, trim_w + 2 * bleed, 3), dtype=np.uint8)
        art[:, :] = (20, 20, 20)
        yy, xx = np.mgrid[0:trim_h, 0:trim_w]
        art[bleed:bleed + trim_h, bleed:bleed + trim_w, 0] = (xx * 3) % 251
        art[bleed:bleed + trim_h, bleed:bleed + trim_w, 1] = (yy * 5) % 251
        art[bleed:bleed + trim_h, bleed:bleed + trim_w, 2] = 90
        press = os.path.join(folder, "press.pdf")
        write_cmyk_pdf(art, press, 40, 30, 5.0)
        before, before_media, before_cs = _trim_snapshot(press)
        changed = reapply_bleed(press, "mirror", 40, 30, folder)
        self.assertTrue(changed["ok"])
        self.assertIn("mirrored", changed["bleedNote"])
        self.assertGreater(os.path.getsize(changed["pressPath"]), 1000)
        after, after_media, after_cs = _trim_snapshot(changed["pressPath"])
        self.assertEqual(before.shape, after.shape)
        self.assertEqual(before_media, after_media)
        delta = float(np.abs(before.astype(np.int16) - after.astype(np.int16)).mean())
        print(f"[TRIM] change-bleed mean delta {delta:.4f} media {after_media} colour {before_cs} -> {after_cs}", flush=True)
        self.assertLess(delta, 1.0, f"trim mean delta {delta:.3f}")
        if any("CMYK" in space.upper() for space in before_cs):
            self.assertTrue(any("CMYK" in space.upper() for space in after_cs), after_cs)
        self.assertFalse(any("RGB" in space.upper() for space in after_cs), after_cs)
        import pymupdf as fitz

        from green_gate import _space_and_tac

        plate = fitz.open(press)
        try:
            xref = int(plate[0].get_images()[0][0])
            info = plate.extract_image(xref)
            space, tac = _space_and_tac(plate, xref)
        finally:
            plate.close()
        self.assertEqual(space, int(info.get("colorspace") or 0))
        self.assertGreater(tac, 1.0)
        self.assertLess(tac, 400.0)

    def test_raster_and_press_edges_name_each_side(self):
        import cv2

        from press_ready_engine import bleed_method_sentence, compile_raster_canvas
        from quick_print import _record_bleed_note

        root = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
        names = [
            os.path.join(root, "ian_2026-10-13", "rosanna_olive_flyer.jpg"),
            os.path.join(root, "ian_2026-10-13", "urbanflex_gym_flyer.jpeg"),
            os.path.join(root, "catch_fire", "src.jpg"),
        ]
        for path in names:
            image = cv2.imread(path, cv2.IMREAD_COLOR)
            name = os.path.basename(path)
            self.assertIsNotNone(image, name)
            _canvas, report = compile_raster_canvas(image, 148.0, 210.0, 5.0, None)
            sentence = str(report.get("bleedNote") or "")
            self.assertTrue(sentence.startswith("Bleed:"), f"{name}: {sentence}")
            for side in ("top", "bottom", "left", "right"):
                self.assertIn(side, sentence, name)
        self.assertTrue(press_saved_for_rebleed(True, "red"))
        self.assertFalse(press_saved_for_rebleed(False, "red"))
        decisions = []
        engine = {"edges": report.get("edges") or [], "headline": "Ready for press"}
        _record_bleed_note(decisions, engine)
        self.assertTrue(str(engine.get("bleedNote") or "").startswith("Bleed:"))
        message = customer_message(decisions, ["3. Resolution / DPI Validation: About 90 ppi."], engine["bleedNote"])
        self.assertIn(engine["bleedNote"], message)
        self.assertIn("Please have a look at this: Resolution:", message)
        self.assertNotIn("3. Resolution", message)
        built = bleed_method_sentence([
            {"side": "top", "method": "replicate", "kind": "", "note": "Top: continued the cleaned edge colour."},
            {"side": "bottom", "method": "replicate", "kind": "", "note": "Bottom: continued the cleaned edge colour."},
            {"side": "left", "method": "mirror", "kind": "photo", "note": "Left: mirrored the cleaned edge."},
            {"side": "right", "method": "mirror", "kind": "photo", "note": "Right: mirrored the cleaned edge."},
        ])
        self.assertIn("top/bottom", built)
        self.assertIn("left/right mirrored photo", built)
        from press_ready_engine import apply_edge_meta

        painted = {}
        apply_edge_meta(painted, {"edges": [
            {"side": "top", "method": "replicate", "kind": "", "note": "Top: continued the cleaned edge colour."},
            {"side": "bottom", "method": "replicate", "kind": "", "note": "Bottom: continued the cleaned edge colour."},
            {"side": "left", "method": "mirror", "kind": "photo", "note": "Left: mirrored the cleaned edge."},
            {"side": "right", "method": "mirror", "kind": "photo", "note": "Right: mirrored the cleaned edge."},
        ]})
        self.assertIn("top/bottom", str(painted.get("bleedNote") or ""))
        self.assertIn("left/right mirrored photo", str(painted.get("bleedNote") or ""))

    def test_change_bleed_sentence_names_one_method(self):
        self.assertEqual(bleed_sentence("mirror"), "Bleed: top/bottom/left/right mirrored.")
        self.assertIn("pixel-drift", bleed_sentence("stretch"))


def _trim_snapshot(path: str):
    import pymupdf as fitz
    import numpy as np

    doc = fitz.open(path)
    try:
        page = doc[0]
        trim = page.trimbox if page.trimbox.width > 2 else page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=trim, alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        media = (round(float(page.rect.width), 2), round(float(page.rect.height), 2))
        spaces = []
        for item in page.get_images(full=True) or []:
            spaces.append(str(item[5]) if len(item) > 5 else "")
        return rgb, media, tuple(spaces)
    finally:
        doc.close()


if __name__ == "__main__":
    unittest.main()
