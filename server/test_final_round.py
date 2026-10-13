"""Final round on c85521b. Text is CMYK, the card names each edge, a
pixels-as-points page is A4, and extra bleed is trimmed to 5 mm.

Bleed generation is not under test here. These checks only cover the four
laptop findings.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VECTOR_REBUILD", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "ian_2026-10-13")
CROP_DIR = "/opt/cursor/artifacts/bleed-crops"
MM = 72.0 / 25.4


def _px(mm: float, dpi: float = 300.0) -> int:
    return max(1, int(round(float(mm) / 25.4 * float(dpi))))


def _save_edges(rgb: np.ndarray, bleed_px: int, stem: str) -> None:
    from PIL import Image

    os.makedirs(CROP_DIR, exist_ok=True)
    height, width = rgb.shape[:2]
    band = max(8, bleed_px + 24)
    crops = {
        "top": rgb[:band, :],
        "bottom": rgb[max(0, height - band):, :],
        "left": rgb[:, :band],
        "right": rgb[:, max(0, width - band):],
    }
    for side, crop in crops.items():
        Image.fromarray(np.ascontiguousarray(crop[:, :, :3])).save(
            os.path.join(CROP_DIR, f"{stem}-{side}.png")
        )


class FinalRoundTest(unittest.TestCase):
    def test_bleed_sentence_names_each_edge(self):
        from press_ready_engine import bleed_method_sentence

        edges = [
            {"side": "top", "kind": "flat", "method": "replicate", "note": "Top: continued the cleaned edge colour."},
            {"side": "bottom", "kind": "flat", "method": "replicate", "note": "Bottom: continued the cleaned edge colour."},
            {"side": "left", "kind": "photo", "method": "mirror", "note": "Left: mirrored the cleaned edge."},
            {"side": "right", "kind": "photo", "method": "mirror", "note": "Right: mirrored the cleaned edge."},
        ]
        self.assertEqual(
            bleed_method_sentence(edges),
            "Bleed: top/bottom flat colour, left/right mirrored photo",
        )
        trimmed = [
            {"side": side, "kind": "flat", "method": "trimmed", "note": f"{side.capitalize()}: extra bleed was trimmed to 5 mm."}
            for side in ("top", "bottom", "left", "right")
        ]
        self.assertEqual(
            bleed_method_sentence(trimmed),
            "Bleed: top/bottom/left/right trimmed to 5 mm",
        )

    def test_gate_names_the_rgb_part(self):
        import pymupdf as fitz
        from PIL import Image

        from green_gate import _colour

        folder = tempfile.mkdtemp(prefix="gate-rgb-")
        cmyk = os.path.join(folder, "plate.jpg")
        Image.new("CMYK", (120, 160), (10, 20, 30, 40)).save(cmyk, "JPEG", quality=90)
        rgb = os.path.join(folder, "rgb.png")
        Image.new("RGB", (120, 160), (180, 40, 40)).save(rgb)

        text_only = os.path.join(folder, "text.pdf")
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.insert_image(page.rect, filename=cmyk)
        page.insert_text((20, 40), "Karseell", fontsize=18, fontname="helv", color=(0.160784, 0.152941, 0.129412))
        doc.save(text_only)
        doc.close()
        _cmyk_item, _ink = _colour(text_only)
        self.assertFalse(_cmyk_item["passed"])
        self.assertIn("The text/vector colour is RGB.", _cmyk_item["detail"])
        self.assertNotIn("The press picture is not CMYK.", _cmyk_item["detail"])

        both = os.path.join(folder, "both.pdf")
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.insert_image(page.rect, filename=rgb)
        page.insert_text((20, 40), "Both", fontsize=18, fontname="helv", color=(0.2, 0.2, 0.2))
        doc.save(both)
        doc.close()
        both_item, _ink = _colour(both)
        self.assertIn("The press picture is not CMYK.", both_item["detail"])
        self.assertIn("The text/vector colour is RGB.", both_item["detail"])

        picture = os.path.join(folder, "picture.pdf")
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.insert_image(page.rect, filename=rgb)
        doc.save(picture)
        doc.close()
        picture_item, _ink = _colour(picture)
        self.assertEqual(picture_item["detail"], "The press picture is not CMYK.")

    def test_stamped_text_is_cmyk_and_black_stays_k(self):
        import pymupdf as fitz
        from PIL import Image

        from green_gate import _art_operators, _colour
        from press_ready_engine import compile_vector_press

        folder = tempfile.mkdtemp(prefix="stamp-cmyk-")
        photo = os.path.join(folder, "photo.png")
        Image.new("RGB", (900, 640), (20, 90, 160)).save(photo)
        src = os.path.join(folder, "src.pdf")
        trim_w, trim_h = 148.0, 105.0
        bleed = 2.5
        page_w = (trim_w + 2 * bleed) * MM
        page_h = (trim_h + 2 * bleed) * MM
        doc = fitz.open()
        page = doc.new_page(width=page_w, height=page_h)
        page.insert_image(page.rect, filename=photo)
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.isfile(font):
            page.insert_font(fontname="DEJAVU", fontfile=font)
            face = "DEJAVU"
        else:
            face = "helv"
        page.insert_text((page_w * 0.2, page_h * 0.45), "BLACK", fontsize=22, fontname=face, color=(0, 0, 0))
        page.insert_text((page_w * 0.2, page_h * 0.62), "GREY", fontsize=18, fontname=face, color=(0.160784, 0.152941, 0.129412))
        rect = page.rect
        page.set_mediabox(rect)
        page.set_cropbox(rect)
        page.set_trimbox(rect)
        page.set_bleedbox(rect)
        doc.save(src)
        doc.close()
        out = os.path.join(folder, "press.pdf")
        built = compile_vector_press(src, out, trim_w, trim_h, 5.0)
        self.assertTrue(built.get("used"))
        raw = _art_operators(out)
        self.assertIsNone(re.search(r"\brg\b|\bRG\b|DeviceRGB", raw), raw[:500])
        self.assertRegex(raw, r"0(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?\s+1(?:\.0+)?\s+k\b")
        item, _ink = _colour(out)
        self.assertTrue(item["passed"], item.get("detail"))
        self.assertIn("Bleed:", str((built.get("report") or {}).get("bleedNote") or ""))

        karseell = os.path.join(FIXTURE, "karseell_A5_double_sided_3mm_trimbox.pdf")
        if os.path.isfile(karseell):
            k_out = os.path.join(folder, "karseell.pdf")
            k_built = compile_vector_press(karseell, k_out, 148.0, 210.0, 5.0)
            self.assertTrue(k_built.get("used"))
            k_raw = _art_operators(k_out)
            self.assertIsNone(re.search(r"\brg\b|\bRG\b|DeviceRGB", k_raw))
            k_item, _ink = _colour(k_out)
            self.assertNotIn("The press picture is not CMYK.", str(k_item.get("detail") or ""))

    def test_pixels_as_points_match_a4_even_as_millimetres(self):
        import pymupdf as fitz

        from press_ready_engine import compile_vector_press
        from quick_print import make_print_ready

        src = os.path.join(FIXTURE, "e6e73dae_A4_300px_as_points_wrong_size.pdf")
        self.assertTrue(os.path.isfile(src))
        doc = fitz.open(src)
        try:
            false_w = float(doc[0].rect.width) * 25.4 / 72.0
            false_h = float(doc[0].rect.height) * 25.4 / 72.0
        finally:
            doc.close()
        self.assertGreater(false_w, 800)
        self.assertGreater(false_h, 1100)

        folder = tempfile.mkdtemp(prefix="a4-px-")
        started = time.perf_counter()
        out = os.path.join(folder, "press.pdf")
        built = compile_vector_press(src, out, false_w, false_h, 5.0)
        elapsed = time.perf_counter() - started
        self.assertTrue(built.get("used"))
        self.assertLess(elapsed, 45.0, f"compile took {elapsed:.1f}s")
        press = fitz.open(out)
        try:
            media_w = float(press[0].rect.width) * 25.4 / 72.0
            media_h = float(press[0].rect.height) * 25.4 / 72.0
        finally:
            press.close()
        self.assertAlmostEqual(media_w, 220.0, delta=1.5)
        self.assertAlmostEqual(media_h, 307.0, delta=1.5)
        report = built.get("report") or {}
        self.assertTrue(report.get("pixelsAsPoints"))
        self.assertIn("A4", str(report.get("sizeNote") or ""))
        pix = fitz.open(out)
        try:
            page = pix[0]
            zoom = 72.0 / max(page.rect.width, page.rect.height) * 2
            shot = page.get_pixmap(matrix=fitz.Matrix(max(zoom, 0.2), max(zoom, 0.2)), alpha=False)
            rgb = np.frombuffer(shot.samples, dtype=np.uint8).reshape(shot.h, shot.w, shot.n)
        finally:
            pix.close()
        _save_edges(rgb, max(4, int(round(5.0 / 25.4 * 72.0 * max(zoom, 0.2)))), "e6e73dae-final")

        card_started = time.perf_counter()
        result = make_print_ready(
            src, os.path.join(folder, "card"), false_w, false_h, "custom", "Custom", filename="pixels.pdf",
        )
        card_elapsed = time.perf_counter() - card_started
        self.assertLess(card_elapsed, 90.0, f"quick print took {card_elapsed:.1f}s")
        self.assertAlmostEqual(float(result.get("trimW") or 0), 210.0, delta=0.6)
        self.assertAlmostEqual(float(result.get("trimH") or 0), 297.0, delta=0.6)
        text = " ".join(str(line) for line in (result.get("decisions") or []))
        self.assertIn("matched to A4", text)
        self.assertNotIn("874.9", text)
        self.assertNotIn("1237.5", text)
        print(f"[TIME] e6e73dae compile {elapsed:.1f}s card {card_elapsed:.1f}s", flush=True)

    def test_overbleed_is_trimmed_to_5mm_and_artwork_stays(self):
        import pymupdf as fitz

        from press_ready_engine import compile_raster_canvas, compile_vector_press
        from quick_print import make_print_ready

        for bleed in (8, 10, 15):
            src = os.path.join(FIXTURE, f"overbleed_a5_{bleed}mm.pdf")
            self.assertTrue(os.path.isfile(src), src)
            folder = tempfile.mkdtemp(prefix=f"over-{bleed}-")
            out = os.path.join(folder, "press.pdf")
            built = compile_vector_press(src, out, 148.0, 210.0, 5.0)
            report = built.get("report") or {}
            notes = " ".join(str(edge.get("note") or "") for edge in (report.get("edges") or []))
            sentence = str(report.get("bleedNote") or "")
            self.assertIn("trimmed to 5 mm", notes)
            self.assertIn("trimmed to 5 mm", sentence)
            self.assertTrue(sentence.startswith("Bleed:"))
            press = fitz.open(out)
            source = fitz.open(src)
            try:
                page = press[0]
                media_w = float(page.rect.width) * 25.4 / 72.0
                media_h = float(page.rect.height) * 25.4 / 72.0
                self.assertAlmostEqual(media_w, 158.0, delta=1.2)
                self.assertAlmostEqual(media_h, 220.0, delta=1.2)
                self.assertIn("TRIM-MARK", page.get_text("text"))
                same = _trim_mean_delta(source[0], page)
            finally:
                press.close()
                source.close()
            self.assertLess(same, 18.0, f"{bleed} mm trim shifted by {same:.1f}")
            _crop_pdf(out, f"overbleed-{bleed}mm")

        raster = os.path.join(FIXTURE, "overbleed_a5_20mm.png")
        self.assertTrue(os.path.isfile(raster))
        from PIL import Image

        with Image.open(raster) as image:
            image.load()
            dpi = image.info.get("dpi") or (300, 300)
            source_rgb = np.asarray(image.convert("RGB"))
        self.assertGreater(float(dpi[0]), 250)
        canvas, report = compile_raster_canvas(source_rgb[:, :, ::-1], 148.0, 210.0, 5.0, float(dpi[0]))
        view = np.ascontiguousarray(canvas[:, :, :3])
        notes = " ".join(str(edge.get("note") or "") for edge in (report.get("edges") or []))
        sentence = str(report.get("bleedNote") or "")
        self.assertIn("trimmed to 5 mm", notes)
        self.assertIn("trimmed to 5 mm", sentence)
        self.assertAlmostEqual(view.shape[1], _px(158.0), delta=2)
        self.assertAlmostEqual(view.shape[0], _px(220.0), delta=2)
        extra = _px(20.0)
        bleed_px = _px(5.0)
        before = source_rgb[extra:-extra, extra:-extra]
        after = view[bleed_px:-bleed_px, bleed_px:-bleed_px][:, :, ::-1]
        self.assertEqual(before.shape[:2], after.shape[:2])
        delta = float(np.abs(before.astype(np.int16) - after.astype(np.int16)).mean())
        self.assertLess(delta, 1.0, f"raster trim moved by {delta:.2f}")
        _save_edges(view[:, :, ::-1], bleed_px, "overbleed-20mm")

        folder = tempfile.mkdtemp(prefix="over-card-")
        pdf_card = make_print_ready(
            os.path.join(FIXTURE, "overbleed_a5_8mm.pdf"),
            os.path.join(folder, "pdf"),
            148.0, 210.0, "a5", "A5", filename="over.pdf",
        )
        pdf_lines = " ".join(str(line) for line in (pdf_card.get("decisions") or []))
        self.assertIn("Bleed:", pdf_lines)
        self.assertIn("trimmed to 5 mm", pdf_lines)
        raster_card = make_print_ready(
            raster, os.path.join(folder, "raster"), 148.0, 210.0, "a5", "A5", filename="over.png",
        )
        raster_lines = " ".join(str(line) for line in (raster_card.get("decisions") or []))
        engine_note = str(((raster_card.get("pressEngine") or {}).get("bleedNote")) or "")
        self.assertIn("Bleed:", raster_lines + " " + engine_note)
        self.assertIn("trimmed to 5 mm", raster_lines + " " + engine_note)


def _trim_mean_delta(source, press) -> float:
    import pymupdf as fitz

    src_trim = source.trimbox if source.trimbox.width > 2 else source.rect
    press_trim = press.trimbox if press.trimbox.width > 2 else press.rect
    src_pix = source.get_pixmap(matrix=fitz.Matrix(0.6, 0.6), clip=src_trim, alpha=False)
    press_pix = press.get_pixmap(matrix=fitz.Matrix(0.6, 0.6), clip=press_trim, alpha=False)
    src = np.frombuffer(src_pix.samples, dtype=np.uint8).reshape(src_pix.h, src_pix.w, src_pix.n)[:, :, :3]
    out = np.frombuffer(press_pix.samples, dtype=np.uint8).reshape(press_pix.h, press_pix.w, press_pix.n)[:, :, :3]
    if src.shape != out.shape:
        import cv2

        out = cv2.resize(out, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.abs(src.astype(np.int16) - out.astype(np.int16)).mean())


def _crop_pdf(path: str, stem: str) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        zoom = 1.1
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        bleed_px = max(4, int(round(5.0 / 25.4 * 72.0 * zoom)))
        _save_edges(rgb, bleed_px, stem)
    finally:
        doc.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
