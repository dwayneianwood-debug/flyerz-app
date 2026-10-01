#!/usr/bin/env python3
"""Intake tests for Adobe Illustrator .ai and .eps files."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz

from artwork_types import input_kind
from illustrator_intake import (
    GS_BAND_BUFFER_SPACE,
    GS_BUFFER_SPACE,
    GS_MAX_BITMAP,
    GS_NUM_RENDERING_THREADS,
    ILLUSTRATOR_RESAVE_MESSAGE,
    classify_artwork,
    ghostscript_pdf_command,
    illustrator_audit_checks,
    prepare_illustrator_file,
)
from quick_check import run_quick_check


def _build_pdf(objects):
    header = (
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        b"%%Creator: Adobe Illustrator(R) 24.0\n"
        b"%%AI8_CreatorVersion: 24.0.0\n"
    )
    chunks = [header]
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in chunks))
        body = obj if isinstance(obj, bytes) else obj.encode("latin1")
        chunks.append(f"{index} 0 obj\n".encode("latin1") + body + b"\nendobj\n")
    xref_at = sum(len(part) for part in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n".encode("latin1"), b"0000000000 65535 f \n"]
    for off in offsets:
        xref.append(f"{off:010d} 00000 n \n".encode("latin1"))
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin1")
    return b"".join(chunks + xref + [trailer])


def _pdf_compatible_ai_bytes():
    """Two artboards: spot colour, non-embedded Helvetica, RGB image with a missing OPI link."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 8 0 R] /Count 2 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
            b"/Resources << "
            b"/ColorSpace << /CS0 [/Separation /PANTONE#20185#20C /DeviceCMYK 5 0 R] >> "
            b"/Font << /F1 6 0 R >> "
            b"/XObject << /Im0 7 0 R >> "
            b">> "
            b"/Contents 4 0 R >>"
        ),
        (
            b"<< /Length 118 >>\nstream\n"
            b"/CS0 cs\n1 scn\n0 0 200 100 re\nf\n"
            b"q\n40 0 0 40 10 10 cm\n/Im0 Do\nQ\n"
            b"BT\n/F1 18 Tf\n10 40 Td\n(Artboard One) Tj\nET\n"
            b"endstream"
        ),
        b"<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [0 0.9 0.8 0] /N 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            b"/OPI << /F (linked/missing-photo.tif) /ImageFileName (linked/missing-photo.tif) >> "
            b"/Length 3 >>\nstream\n"
            b"\xff\x00\x00\nendstream"
        ),
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 80] "
            b"/Resources << /ProcSet [/PDF] >> "
            b"/Contents 9 0 R >>"
        ),
        (
            b"<< /Length 44 >>\nstream\n"
            b"0 0 1 rg\n0 0 120 80 re\nf\n"
            b"endstream"
        ),
    ]
    return _build_pdf(objects)


EPS_ARTWORK = b"""%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Flyerz intake test
%%BoundingBox: 0 0 200 100
%%HiResBoundingBox: 0.000 0.000 200.000 100.000
%%LanguageLevel: 2
%%Pages: 1
%%EndComments
0 0 1 0 setcmykcolor
0 0 200 100 rectfill
0 0 0 1 setcmykcolor
/Helvetica findfont 18 scalefont setfont
10 40 moveto (EPS artwork) show
showpage
%%EOF
"""

BROKEN_AI = b"""%!PS-Adobe-3.0
%%Creator: Adobe Illustrator(R) 27.0
%%AI8_CreatorVersion: 27.0.0
%%BoundingBox: 0 0 612 792
%AI5_FileFormat 14
%%EOF
""" + os.urandom(400)


class IllustratorIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flyerz_ai_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_cli_stdout_is_only_json(self):
        path = self._write("campaign.ai", _pdf_compatible_ai_bytes())
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "illustrator_intake.py")
        proc = subprocess.run(
            [sys.executable, script, "prepare", path, ".ai"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["pageCount"], 2)

    def test_signature_not_extension(self):
        pdf_named_ai = self._write("flyer.ai", _pdf_compatible_ai_bytes())
        eps_named_pdf = self._write("notes.pdf", EPS_ARTWORK)
        self.assertEqual(classify_artwork(pdf_named_ai), "pdf")
        self.assertEqual(classify_artwork(eps_named_pdf), "postscript")
        self.assertEqual(classify_artwork(self._write("broken.ai", BROKEN_AI)), "unreadable")

    def test_pdf_compatible_ai_preserves_vectors_and_artboards(self):
        path = self._write("campaign.ai", _pdf_compatible_ai_bytes())
        with open(path, "rb") as raw_f:
            before = raw_f.read()
        result = prepare_illustrator_file(path, ".ai")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["kind"], "pdf")
        self.assertEqual(result["pageCount"], 2)
        with open(path, "rb") as raw_f:
            self.assertEqual(raw_f.read(), before)

        doc = fitz.open(path)
        try:
            self.assertIn("Artboard One", doc[0].get_text())
            self.assertGreater(len(doc[0].get_drawings()) + len(doc[0].get_text()), 0)
            self.assertAlmostEqual(doc[0].rect.width, 200, delta=1)
            self.assertAlmostEqual(doc[1].rect.width, 120, delta=1)
            fonts = doc[0].get_fonts(full=True)
            self.assertTrue(fonts)
            self.assertEqual(fonts[0][3], "Helvetica")
        finally:
            doc.close()

    def test_audit_flags_fonts_rgb_spots_bleed_and_missing_links(self):
        path = self._write("campaign.ai", _pdf_compatible_ai_bytes())
        prepared = prepare_illustrator_file(path, ".ai")
        self.assertTrue(prepared["success"])
        report = run_quick_check(path, "ai")
        self.assertNotIn("error", report)
        by_id = {check["id"]: check for check in report["checks"]}

        self.assertIn("illustrator_fonts", by_id)
        self.assertFalse(by_id["illustrator_fonts"]["passed"])
        self.assertIn("Helvetica", by_id["illustrator_fonts"]["message"])

        self.assertIn("cmyk", by_id)
        self.assertFalse(by_id["cmyk"]["passed"])
        self.assertIn("RGB", by_id["cmyk"]["message"])

        self.assertIn("illustrator_spots", by_id)
        self.assertFalse(by_id["illustrator_spots"]["passed"])
        self.assertIn("PANTONE", by_id["illustrator_spots"]["message"].upper())

        self.assertIn("bleed", by_id)
        self.assertFalse(by_id["bleed"]["passed"])
        self.assertIn("artboard", by_id["bleed"]["message"].lower())

        self.assertIn("illustrator_links", by_id)
        self.assertFalse(by_id["illustrator_links"]["passed"])
        self.assertIn("missing-photo.tif", by_id["illustrator_links"]["message"])

        self.assertEqual(report["pageCount"], 2)
        self.assertIn("illustrator_artboards", by_id)
        self.assertIn("page picker", by_id["illustrator_artboards"]["message"])

        self.assertTrue(by_id["resolution"]["passed"])
        self.assertEqual(by_id["resolution"]["message"], "Vector artwork, resolution independent")

    def test_vector_pdf_and_placed_image_dpi(self):
        self.assertEqual(input_kind(".ai"), "pdf")
        self.assertEqual(input_kind(".eps"), "pdf")
        self.assertEqual(input_kind(".pdf"), "pdf")
        self.assertEqual(input_kind(".png"), "image")
        self.assertEqual(input_kind(".exe"), "unsupported")

        vector_path = self._write("vector.pdf", b"")
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        page.insert_text((10, 50), "Vector only")
        doc.save(vector_path)
        doc.close()
        vector = run_quick_check(vector_path, "pdf")
        resolution = next(check for check in vector["checks"] if check["id"] == "resolution")
        self.assertTrue(resolution["passed"])
        self.assertEqual(resolution["message"], "Vector artwork, resolution independent")

        placed_path = self._write("placed.pdf", b"")
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
        pix.clear_with(255)
        page.insert_image(fitz.Rect(10, 10, 82, 82), pixmap=pix)
        doc.save(placed_path)
        doc.close()
        pix = None
        placed = run_quick_check(placed_path, "pdf")
        resolution = next(check for check in placed["checks"] if check["id"] == "resolution")
        self.assertFalse(resolution["passed"])
        self.assertEqual(resolution["message"], "Resolution too low: 10 DPI (minimum: 300 DPI)")

    def test_eps_converts_with_bounding_box_and_memory_leash(self):
        cmd = ghostscript_pdf_command("in.eps", "out.pdf", eps_crop=True)
        joined = " ".join(cmd)
        self.assertIn("-dEPSCrop", cmd)
        self.assertIn(f"-dNumRenderingThreads={GS_NUM_RENDERING_THREADS}", cmd)
        self.assertIn(f"-dBufferSpace={GS_BUFFER_SPACE}", cmd)
        self.assertIn(f"-dMaxBitmap={GS_MAX_BITMAP}", cmd)
        self.assertIn(f"-dBandBufferSpace={GS_BAND_BUFFER_SPACE}", cmd)
        self.assertIn("HWResolution [300 300]", joined)
        self.assertEqual(GS_BUFFER_SPACE, 50000000)
        self.assertEqual(GS_MAX_BITMAP, 50000000)
        self.assertEqual(GS_NUM_RENDERING_THREADS, 1)
        self.assertNotIn("-dEPSCrop", ghostscript_pdf_command("in.ai", "out.pdf", eps_crop=False))

        path = self._write("logo.eps", EPS_ARTWORK)
        result = prepare_illustrator_file(path, ".eps")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["kind"], "postscript")
        self.assertTrue(result["epsCrop"])
        self.assertTrue(result["preservedVectors"])

        doc = fitz.open(path)
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertIn("EPS artwork", doc[0].get_text())
            self.assertAlmostEqual(doc[0].rect.width, 200, delta=2)
            self.assertAlmostEqual(doc[0].rect.height, 100, delta=2)
            drawings = doc[0].get_drawings()
            self.assertTrue(drawings or doc[0].get_text())
        finally:
            doc.close()

    def test_postscript_ai_without_eps_crop_flag_on_extension(self):
        path = self._write("legacy.ai", EPS_ARTWORK.replace(b"EPSF-3.0", b" "))
        # Still valid PostScript, but the header no longer declares EPSF and the extension is .ai.
        result = prepare_illustrator_file(path, ".ai")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["kind"], "postscript")
        self.assertFalse(result["epsCrop"])
        doc = fitz.open(path)
        try:
            self.assertIn("EPS artwork", doc[0].get_text())
        finally:
            doc.close()

    def test_broken_ai_asks_customer_to_resave(self):
        path = self._write("native.ai", BROKEN_AI)
        result = prepare_illustrator_file(path, ".ai")
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "illustrator_resave")
        self.assertIn("Create PDF Compatible File", result["error"])
        self.assertIn("PDF/X-1a", result["error"])
        self.assertIn("File > Save As > Illustrator", result["error"])
        self.assertEqual(result["error"], ILLUSTRATOR_RESAVE_MESSAGE)

        garbage = self._write("empty.ai", b"\x00\x01not an illustrator file")
        result = prepare_illustrator_file(garbage, ".ai")
        self.assertFalse(result["success"])
        self.assertIn("Create PDF Compatible File", result["error"])

    def test_direct_audit_helper_lists_spot_name(self):
        path = self._write("one.ai", _pdf_compatible_ai_bytes())
        doc = fitz.open(path)
        try:
            with open(path, "rb") as raw_f:
                raw = raw_f.read()
            checks = illustrator_audit_checks(doc, raw, doc.page_count)
        finally:
            doc.close()
        spots = next(c for c in checks if c["id"] == "illustrator_spots")
        self.assertIn("PANTONE 185 C", spots["message"])


if __name__ == "__main__":
    unittest.main()
