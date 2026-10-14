"""School smear, customer-message dedupe, card keylines, and Corel crop marks."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from customer_message import customer_message  # noqa: E402
from intake_check import assess_intake  # noqa: E402
from press_ready_engine import _vector_page_bleed  # noqa: E402
from quick_print import (  # noqa: E402
    detect_edge_frame,
    make_print_ready,
    pixels_as_points_trim,
    remove_edge_frame,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14b")
SCHOOL = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14", "school_readiness.jpg")
PROGRAMME = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-14", "school_programme.jpg")


def _trim_rgb(path: str, zoom: float = 2.0):
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        trim = page.trimbox if page.trimbox.width > 2 else page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=trim, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        return rgb
    finally:
        doc.close()


class Round14bTest(unittest.TestCase):
    def test_customer_message_keeps_one_line_per_point(self):
        message = customer_message(
            [
                "Bleed is 5 mm on every side.",
                "The picture was a different shape from the product. The whole picture was kept and the gap was filled.",
                "Your picture is a different shape from A4, so we extended the background at the sides",
                "A thin border near the cut was removed. The background was continued into the bleed.",
            ],
            [
                "Minimum text size: The smallest text is about 6.8 pt.",
                "E5. Minimum text size: The smallest text is about 6.8 pt.",
                "Safe Zone: Text sits close to the trim.",
                "7. Safe Zone: Text sits close to the trim.",
                "Resolution / DPI Validation: About 124 ppi.",
                "3. Resolution / DPI Validation: About 124 ppi.",
                "Ordered product vs file size: Before 1024×1536 px – product 210×297 mm.",
                "E1. Ordered product vs file size: Page 1 aspect differs by 6%.",
                "Border near the cut: A thin border sits on the cut and can print unevenly.",
                "Layout Balance Analysis: Layout weight is off centre.",
                "8. Layout Balance Analysis: Layout weight is off centre.",
                "Rasterised or soft text: Raster text is present at about 300 ppi.",
                "E4. Rasterised or soft text: Raster text is present at about 300 ppi.",
                "Composition Centre Analysis: Visual centre is off.",
                "9. Composition Centre Analysis: Visual centre is off.",
            ],
            "Bleed: top/bottom flat colour, left/right continued colour",
        )
        self.assertEqual(message.count("Minimum text size"), 1)
        self.assertNotIn("E5.", message)
        self.assertEqual(message.count("Safe Zone"), 1)
        self.assertNotIn("7. Safe Zone", message)
        self.assertEqual(message.count("Resolution:"), 1)
        self.assertNotIn("3. Resolution", message)
        self.assertNotIn("E1.", message)
        self.assertNotIn("Ordered product vs file size", message)
        self.assertEqual(message.count("different shape"), 1)
        self.assertIn("extended the background at the sides", message)
        self.assertIn("thin border near the cut was removed", message)
        self.assertNotIn("Border near the cut: A thin border", message)
        self.assertEqual(message.count("Layout Balance"), 1)
        self.assertNotIn("8. Layout", message)
        self.assertEqual(message.count("Rasterised or soft text"), 1)
        self.assertNotIn("E4.", message)

    def test_card_keyline_is_flagged_and_removed(self):
        import cv2

        path = os.path.join(NEW, "a5dedcfa.jpg")
        report = assess_intake(path, 90, 50)
        border = [item for item in report["items"] if item["id"] == "border-near-cut"]
        self.assertEqual(len(border), 1)
        self.assertEqual(border[0]["severity"], "amber")
        bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        self.assertIsNotNone(detect_edge_frame(bgr))
        cleaned, found = remove_edge_frame(bgr)
        self.assertIsNotNone(found)
        self.assertEqual(cleaned.shape, bgr.shape)
        # The rule sat on the outer pixels. It is now the background colour.
        edge = cleaned[1, 1]
        inner = cleaned[found["top"] + 4, found["left"] + 4]
        self.assertLess(float(np.abs(edge.astype(np.int16) - inner.astype(np.int16)).mean()), 8.0)
        other = os.path.join(NEW, "be09ef80.jpg")
        self.assertIsNotNone(detect_edge_frame(cv2.imread(other, cv2.IMREAD_COLOR)))
        school = cv2.imread(SCHOOL, cv2.IMREAD_COLOR)
        self.assertIsNone(detect_edge_frame(school))
        folder = tempfile.mkdtemp(prefix="card-14b-")
        made = make_print_ready(
            path, folder, 90, 50, "card-90x50", "Business card 90 × 50",
            filename="a5dedcfa.jpg",
            options={"skipUpscale": True, "concerns": ["Border near the cut: A thin border sits on the cut and can print unevenly."]},
        )
        self.assertIn("thin border near the cut was removed", made.get("customerMessage") or "")
        press = made.get("pressPath") or ""
        self.assertTrue(os.path.isfile(press), made.get("customerMessage"))
        rgb = _trim_rgb(press, 4.0)
        dark = rgb.max(axis=2) < 40
        self.assertLess(float(dark[:, :8].mean()), 0.05)
        self.assertLess(float(dark[:, -8:].mean()), 0.05)

    def test_corel_trim_keeps_the_art_and_drops_the_marks(self):
        import pymupdf as fitz

        samples = (
            ("3b6b2fc9.pdf", 90.0, 50.0),
            ("89d8ed50.pdf", 105.0, 148.0),
            ("a0304881.pdf", 105.0, 148.0),
            ("c494fc76.pdf", 105.0, 148.0),
        )
        for name, trim_w, trim_h in samples:
            doc = fitz.open(os.path.join(NEW, name))
            try:
                self.assertEqual(doc.page_count, 2)
                for page in doc:
                    existing, mode = _vector_page_bleed(page, trim_w, trim_h)
                    self.assertEqual(mode, "boxes", name)
                    for side in ("left", "right", "top", "bottom"):
                        self.assertGreaterEqual(existing[side], 5.0, f"{name} {side}")
                    self.assertLess(max(existing.values()) - min(existing.values()), 0.4, name)
            finally:
                doc.close()

        path = os.path.join(NEW, "89d8ed50.pdf")
        doc = fitz.open(path)
        try:
            page = doc[0]
            trim = page.trimbox
            bleed = 5.0 * 72.0 / 25.4
            clip = fitz.Rect(trim.x0 - bleed, trim.y0 - bleed, trim.x1 + bleed, trim.y1 + bleed)
            out = fitz.open()
            placed = out.new_page(width=clip.width, height=clip.height)
            placed.show_pdf_page(placed.rect, doc, 0, clip=clip)
            pix = placed.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
            black = rgb.max(axis=2) < 40
            ring = np.concatenate([
                black[:6].ravel(), black[-6:].ravel(), black[:, :6].ravel(), black[:, -6:].ravel(),
            ])
            self.assertLess(float(ring.mean()), 0.02)
            ink = rgb.min(axis=2) < 250
            ys = np.where(ink)[0]
            self.assertLess(int(ys.min()), int(0.05 * rgb.shape[0]))
            self.assertGreater(int(ys.max()), int(0.90 * rgb.shape[0]))
            out.close()
        finally:
            doc.close()

    def test_school_picture_is_not_mirrored_into_the_cut(self):
        folder = tempfile.mkdtemp(prefix="school-14b-")
        concerns = [
            "Resolution / DPI Validation: About 124 ppi.",
            "Safe Zone: The picture reaches the cut.",
            "Layout Balance Analysis: Layout weight is off centre.",
            "Composition Centre Analysis: Visual centre is off.",
            "Ordered product vs file size: Page 1 aspect differs by 6%.",
            "E1. Ordered product vs file size: Page 1 aspect differs by 6%.",
            "Minimum text size: The smallest text is about 6.8 pt.",
            "E5. Minimum text size: The smallest text is about 6.8 pt.",
            "Smart Downscale Radar: Safe zone violations.",
            "Trim Tolerance Simulation: Trim drift risk is HIGH.",
        ]
        result = make_print_ready(
            SCHOOL, folder, 210, 297, "a4", "A4",
            filename="049b45e8.jpg",
            options={"skipUpscale": True, "concerns": concerns},
        )
        message = result.get("customerMessage") or ""
        self.assertIn("extended the background at the sides", message)
        self.assertNotIn("E1.", message)
        self.assertEqual(message.count("Minimum text size"), 1)
        self.assertNotIn("E5.", message)
        press = result.get("pressPath") or ""
        self.assertTrue(os.path.isfile(press))
        rgb = _trim_rgb(press, 3.0)
        ppm = (72.0 * 3.0) / 25.4
        gap = rgb[:, : int(6.0 * ppm)]
        art = rgb[:, int(6.0 * ppm): int(12.0 * ppm)][:, ::-1]
        if gap.shape == art.shape:
            corr = float(np.corrcoef(gap.reshape(-1).astype(float), art.reshape(-1).astype(float))[0, 1])
            self.assertLess(corr, 0.8, corr)
        # The gap is a continuation, so it is smoother than the photograph beside it.
        self.assertLess(float(gap.std()), float(rgb[:, int(20 * ppm): int(40 * ppm)].std()))

    def test_programme_message_names_the_shape(self):
        folder = tempfile.mkdtemp(prefix="programme-14b-")
        concerns = [f"E1. Ordered product vs file size: Page 1 aspect differs."] + [
            f"Check {index}: Detail {index}." for index in range(8)
        ]
        result = make_print_ready(
            PROGRAMME, folder, 210, 297, "a4", "A4",
            filename="8222b654.jpg",
            options={"skipUpscale": True, "concerns": concerns},
        )
        message = result.get("customerMessage") or ""
        self.assertIn("extended the background at the sides", message)
        self.assertNotIn("E1.", message)

    def test_picture_black_is_k_only(self):
        import pikepdf

        from press_ready_engine import write_cmyk_pdf

        folder = tempfile.mkdtemp(prefix="k-black-")
        path = os.path.join(folder, "press.pdf")
        canvas = np.zeros((80, 80, 3), np.uint8)
        canvas[:] = (255, 255, 255)
        canvas[20:60, 20:60] = (0, 0, 0)
        write_cmyk_pdf(canvas, path, 40.0, 40.0, 5.0)
        pdf = pikepdf.open(path)
        try:
            image = list(pdf.pages[0]["/Resources"]["/XObject"].values())[0]
            raw = image.read_bytes()
            width, height = int(image.Width), int(image.Height)
            channels = raw.__len__() // (width * height)
            self.assertGreaterEqual(channels, 4)
            samples = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, channels)
        finally:
            pdf.close()
        k = samples[:, :, 3].astype(np.int16)
        total = samples[:, :, :4].astype(np.int16).sum(axis=-1)
        black = k >= 180
        self.assertGreater(int(black.sum()), 20)
        # Rich black stays under the 300 TAC limit, and the K channel is actually used.
        self.assertLess(float(total[black].max()) / 2.55, 300.0)

    def test_a2_over_bleed_keeps_its_trim_box(self):
        """An A2 page with 10 mm extra bleed is the same point size as A6 pixels."""
        import pymupdf as fitz

        folder = tempfile.mkdtemp(prefix="a2-trim-")
        path = os.path.join(folder, "a2-over.pdf")
        trim_w, trim_h, extra = 420.0, 594.0, 10.0
        mm = 72.0 / 25.4
        w_pt = (trim_w + 2.0 * extra) * mm
        h_pt = (trim_h + 2.0 * extra) * mm
        doc = fitz.open()
        page = doc.new_page(width=w_pt, height=h_pt)
        page.draw_rect(page.rect, color=None, fill=(0.2, 0.4, 0.3))
        inset = extra * mm
        page.set_trimbox(fitz.Rect(inset, inset, w_pt - inset, h_pt - inset))
        doc.save(path)
        doc.close()
        self.assertIsNone(pixels_as_points_trim(path))
        doc = fitz.open(path)
        try:
            existing, mode = _vector_page_bleed(doc[0], trim_w, trim_h)
        finally:
            doc.close()
        self.assertEqual(mode, "boxes")
        for side in ("left", "right", "top", "bottom"):
            self.assertGreater(existing[side], 9.0, side)
            self.assertLess(existing[side], 11.0, side)


if __name__ == "__main__":
    unittest.main()
