"""Bleed continuation and the press-file check, for every file customers send.

The table is the evidence: each file type, each upload path, and each print
size. A row fails when the outer 5 mm does not stay within ΔE 10 of the
artwork, when a real full bleed is repainted, or when a bad bleed is delivered
as a silent green.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("VECTOR_REBUILD", "0")

from bleed_builder import (  # noqa: E402
    BLEED_DELTA_E,
    check_page_array,
    check_press_pdf,
    fringe_depths,
    repair_light_fringe,
)
from press_ready_engine import compile_raster_canvas, compile_vector_press  # noqa: E402
from quick_print import OFFICE_MESSAGE, decide_light, make_print_ready  # noqa: E402

MM = 72.0 / 25.4
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS: list[dict] = []


def _products() -> list[dict]:
    with open(os.path.join(ROOT, "shared", "quick-print-products.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _rgb(width: int, height: int, kind: str) -> np.ndarray:
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    if kind == "photo":
        olive = np.array([76, 92, 38], np.float32)
        yellow = np.array([214, 176, 46], np.float32)
        image = olive * (1.0 - y) + yellow * y
        noise = np.random.default_rng(7).normal(0.0, 5.0, (height, width, 3)).astype(np.float32)
        image = image + noise
    elif kind == "gradient":
        left = np.array([48, 92, 64], np.float32)
        right = np.array([214, 168, 48], np.float32)
        image = left * (1.0 - x) + right * x
    elif kind == "flat":
        image = np.full((height, width, 3), (36, 110, 72), np.float32)
    elif kind == "dark":
        image = np.full((height, width, 3), (18, 24, 16), np.float32)
    elif kind == "light":
        image = np.full((height, width, 3), (228, 224, 214), np.float32)
    elif kind == "navy":
        image = np.full((height, width, 3), (24, 42, 92), np.float32)
    else:
        raise ValueError(kind)
    return np.clip(np.round(image), 0, 255).astype(np.uint8)


def _sliver(image: np.ndarray, pixels: int = 1) -> np.ndarray:
    out = np.array(image, copy=True)
    n = min(int(pixels), out.shape[0] // 5, out.shape[1] // 5)
    if n < 1:
        return out
    out[:, :n] = 255
    out[:, -n:] = 255
    out[:n, :] = 255
    out[-n:, :] = 255
    return out


def _worst(report: dict) -> tuple[bool, str]:
    sides = []
    for page in report.get("pages") or []:
        for side in page.get("sides") or []:
            sides.append(f"p{page.get('page')} {side.get('side')} ΔE {side.get('delta')}")
    if not sides and report.get("sides"):
        for side in report["sides"]:
            sides.append(f"{side.get('side')} ΔE {side.get('delta')}")
    detail = ", ".join(sides) if sides else (report.get("note") or "")
    return bool(report.get("ok")), detail[:240]


def _add(kind: str, path: str, size: str, ok: bool, detail: str) -> None:
    ROWS.append({
        "kind": kind,
        "path": path,
        "size": size,
        "result": "PASS" if ok else "FAIL",
        "detail": detail,
    })
    print(f"[{'PASS' if ok else 'FAIL'}] {kind} | {path} | {size} | {detail}", flush=True)


def _write_photo_pdf(
    path: str,
    page_w_mm: float,
    page_h_mm: float,
    kind: str,
    sliver_px: int = 0,
    ppi: float = 72.0,
    trim_inset_mm: float = 0.0,
    pages: list[tuple[str, int]] | None = None,
) -> None:
    import io

    import pymupdf as fitz
    from PIL import Image

    doc = fitz.open()
    specs = pages or [(kind, sliver_px)]
    for page_kind, page_sliver in specs:
        w_pt = float(page_w_mm) * MM
        h_pt = float(page_h_mm) * MM
        pw = max(8, int(round(w_pt * float(ppi) / 72.0)))
        ph = max(8, int(round(h_pt * float(ppi) / 72.0)))
        rgb = _rgb(pw, ph, page_kind)
        if page_sliver:
            rgb = _sliver(rgb, page_sliver)
        buf = io.BytesIO()
        Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=92)
        page = doc.new_page(width=w_pt, height=h_pt)
        page.insert_image(page.rect, stream=buf.getvalue())
        if trim_inset_mm > 0:
            inset = float(trim_inset_mm) * MM
            page.set_trimbox(fitz.Rect(inset, inset, w_pt - inset, h_pt - inset))
            page.set_cropbox(page.rect)
            page.set_bleedbox(page.rect)
    doc.save(path)
    doc.close()


def _write_vector_pdf(path: str, w_mm: float, h_mm: float, rgb=(36, 110, 72)) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=float(w_mm) * MM, height=float(h_mm) * MM)
    page.draw_rect(page.rect, color=None, fill=tuple(channel / 255.0 for channel in rgb))
    doc.save(path)
    doc.close()


def _compile_pdf(src: str, dest: str, trim_w: float, trim_h: float) -> dict:
    report = compile_vector_press(src, dest, trim_w, trim_h, 5)
    if not report.get("used"):
        reason = (report.get("report") or {}).get("reason") or "the press file was not built"
        raise RuntimeError(reason)
    return check_press_pdf(dest, trim_w, trim_h, 5)


def _raster_report(rgb: np.ndarray, trim_w: float, trim_h: float) -> dict:
    canvas, _report = compile_raster_canvas(np.ascontiguousarray(rgb[:, :, ::-1]), trim_w, trim_h, 5, None)
    out = np.ascontiguousarray(canvas[:, :, ::-1])
    height, width = out.shape[:2]
    bleed_px = max(1, int(round(5.0 / 25.4 * 300.0)))
    trim = (bleed_px, bleed_px, width - bleed_px, height - bleed_px)
    scored = check_page_array(out, trim, (0, 0, width, height), 300.0 / 72.0)
    return {"ok": scored["ok"], "pages": [{"page": 1, "ok": scored["ok"], "sides": scored["sides"]}]}


def _outer_mean(path: str) -> np.ndarray:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(72.0 / 72.0, 72.0 / 72.0), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
        band = max(2, int(round(2.0 * MM)))
        pieces = np.concatenate([
            rgb[:, :band].reshape(-1, 3),
            rgb[:, -band:].reshape(-1, 3),
            rgb[:band, :].reshape(-1, 3),
            rgb[-band:, :].reshape(-1, 3),
        ], axis=0)
        return np.median(pieces.astype(np.float32), axis=0)
    finally:
        doc.close()


class BleedQualityTest(unittest.TestCase):
    def test_pixel_drift_radius_stays_one_pixel(self):
        import smart_bleed

        self.assertEqual(smart_bleed.EDGE_SAMPLE_INSET, 1)

    def test_a_light_sliver_is_not_the_colour_that_is_continued(self):
        image = np.full((40, 50, 3), (76, 92, 38), np.uint8)
        image[:, -2:] = 255
        image[0, :] = 255
        repaired = repair_light_fringe(image)
        self.assertLess(float(repaired[:, -1].mean()), 120.0)
        self.assertGreater(float(repaired[5, -1, 1]), 70.0)
        pale = np.full((40, 50, 3), (230, 226, 216), np.uint8)
        self.assertEqual(int(fringe_depths(pale).max()), 0)
        self.assertLess(float(np.abs(repair_light_fringe(pale).astype(np.int16) - pale).max()), 1)

    def test_check_ignores_a_one_pixel_halo_and_flags_a_grey_band(self):
        height, width = 240, 320
        olive = np.full((height, width, 3), (76, 92, 38), np.uint8)
        trim = (40, 30, 280, 210)
        good = olive.copy()
        good[:, trim[2] - 1] = 255
        scored = check_page_array(good, trim, (0, 0, width, height), 2.0)
        self.assertTrue(scored["ok"], scored)
        bad = olive.copy()
        bad[:, trim[2]:] = (218, 215, 211)
        bad[:, :trim[0]] = (218, 215, 211)
        scored = check_page_array(bad, trim, (0, 0, width, height), 2.0)
        self.assertFalse(scored["ok"], scored)
        flags = {flag for side in scored["sides"] if not side["ok"] for flag in side["flags"]}
        self.assertTrue(flags & {"light-band", "mismatch", "seam"})

    def test_a_failed_check_is_amber_for_staff_and_the_file_stays(self):
        note = (
            "The bleed does not continue the artwork (right ΔE 40 (light-band,mismatch)). "
            "Check that edge before printing."
        )
        info = decide_light({
            "compiled": True,
            "enginePassed": True,
            "bleedQuality": {"ok": False, "note": note, "pages": []},
        })
        self.assertEqual(info["light"], "amber")
        self.assertEqual(info["clientMessage"], "")
        self.assertIn("does not continue the artwork", info["reasons"][0])
        clear = decide_light({
            "compiled": True,
            "enginePassed": True,
            "bleedQuality": {"ok": True, "note": "", "pages": []},
        })
        self.assertEqual(clear["light"], "green")

    def test_every_file_type_size_and_upload_path(self):
        ROWS.clear()
        folder = tempfile.mkdtemp(prefix="bleed-matrix-")
        try:
            self._matrix(folder)
        except Exception:
            _add("matrix", "setup", "", False, traceback.format_exc().splitlines()[-1][:240])
        _write_table()
        failed = [row for row in ROWS if row["result"] != "PASS"]
        self.assertFalse(failed, "\n".join(f"{row['kind']} | {row['path']} | {row['detail']}" for row in failed))

    def _matrix(self, folder: str) -> None:
        self._sizes(folder)
        self._pdf_kinds(folder)
        self._images(folder)
        self._paths(folder)
        self._refused(folder)

    def _sizes(self, folder: str) -> None:
        for product in _products():
            src = os.path.join(folder, f"{product['id']}.pdf")
            dest = os.path.join(folder, f"{product['id']}-press.pdf")
            try:
                _write_vector_pdf(src, product["widthMm"], product["heightMm"])
                report = _compile_pdf(src, dest, product["widthMm"], product["heightMm"])
                ok, detail = _worst(report)
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add("PDF flat colour", "press engine", product["label"], ok, detail)

    def _pdf_kinds(self, folder: str) -> None:
        cases = [
            ("PDF Canva", 147.9, 210.1, "photo", 1, 72, 0, 148, 210, "A5"),
            ("PDF Canva 0.3 mm short", 147.7, 210.0, "photo", 1, 72, 0, 148, 210, "A5"),
            ("PDF Canva 0.5 mm short", 147.5, 210.0, "photo", 1, 96, 0, 148, 210, "A5"),
            ("PDF Canva landscape", 209.9, 147.9, "photo", 1, 72, 0, 210, 148, "A5 landscape"),
            ("PDF InDesign partial 3 mm", 154.0, 216.0, "photo", 0, 120, 3.0, 148, 210, "A5"),
            ("PDF InDesign partial 2.5 mm", 153.0, 215.0, "gradient", 0, 120, 2.5, 148, 210, "A5"),
            ("PDF Illustrator", 148.0, 210.0, "flat", 1, 150, 0, 148, 210, "A5"),
            ("PDF Word-exported", 148.0, 210.0, "photo", 1, 150, 0, 148, 210, "A5"),
            ("PDF gradient edge", 105.0, 148.0, "gradient", 0, 100, 0, 105, 148, "A6"),
            ("PDF dark edge", 105.0, 148.0, "dark", 0, 100, 0, 105, 148, "A6"),
            ("PDF light edge", 105.0, 148.0, "light", 0, 100, 0, 105, 148, "A6"),
            ("PDF flat colour", 90.0, 50.0, "flat", 0, 150, 0, 90, 50, "card 90 × 50"),
        ]
        for name, pw, ph, kind, sliver, ppi, inset, tw, th, size in cases:
            src = os.path.join(folder, f"{name}.pdf".replace(" ", "-"))
            dest = src + ".press.pdf"
            try:
                _write_photo_pdf(src, pw, ph, kind, sliver, ppi, inset)
                report = _compile_pdf(src, dest, tw, th)
                ok, detail = _worst(report)
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add(name, "press engine", size, ok, detail)

        src = os.path.join(folder, "full-bleed.pdf")
        dest = src + ".press.pdf"
        try:
            _write_photo_pdf(src, 158.0, 220.0, "photo", 0, 100, 5.0)
            before = _outer_mean(src)
            report = _compile_pdf(src, dest, 148, 210)
            after = _outer_mean(dest)
            from bleed_builder import _delta_e

            shift = _delta_e(before, after)
            ok, detail = _worst(report)
            ok = ok and shift <= BLEED_DELTA_E
            detail = f"kept outer ΔE {shift:.2f}; {detail}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF existing full bleed", "press engine", "A5", ok, detail)

        src = os.path.join(folder, "multi.pdf")
        dest = src + ".press.pdf"
        try:
            _write_photo_pdf(
                src, 105.0, 148.0, "photo", 1, 100, 0,
                pages=[("photo", 1), ("gradient", 0), ("dark", 1)],
            )
            report = _compile_pdf(src, dest, 105, 148)
            ok, detail = _worst(report)
            ok = ok and len(report.get("pages") or []) == 3
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF multi-page", "press engine", "A6", ok, detail)

        src = os.path.join(folder, "double.pdf")
        dest = src + ".press.pdf"
        try:
            _write_photo_pdf(
                src, 105.0, 148.0, "photo", 1, 100, 0,
                pages=[("photo", 1), ("navy", 1)],
            )
            report = _compile_pdf(src, dest, 105, 148)
            ok, detail = _worst(report)
            ok = ok and len(report.get("pages") or []) == 2
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF double-sided", "press engine", "A6", ok, detail)

        ai = os.path.join(folder, "flyer.ai")
        dest = ai + ".press.pdf"
        try:
            _write_photo_pdf(ai, 147.9, 210.1, "photo", 1, 72, 0)
            report = _compile_pdf(ai, dest, 148, 210)
            ok, detail = _worst(report)
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("AI (PDF-compatible)", "press engine", "A5", ok, detail)

        eps = os.path.join(folder, "flat.eps")
        dest = eps + ".press.pdf"
        try:
            _write_eps(eps, 148, 210)
            report = _compile_pdf(eps, dest, 148, 210)
            ok, detail = _worst(report)
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("EPS", "press engine", "A5", ok, detail)

        src = os.path.join(folder, "white-ring.pdf")
        dest = src + ".press.pdf"
        try:
            _write_white_ring(src, 105, 148)
            report = compile_vector_press(src, dest, 105, 148, 5)
            self.assertTrue(report.get("used"))
            self.assertTrue(os.path.isfile(dest) and os.path.getsize(dest) > 1000)
            quality = check_press_pdf(dest, 105, 148, 5)
            info = decide_light({
                "compiled": True,
                "enginePassed": True,
                "bleedQuality": quality,
            })
            ok = (not quality["ok"]) and info["light"] == "amber" and info["clientMessage"] == ""
            detail = quality.get("note") or ""
            if info["light"] != "amber":
                detail = f"light {info['light']}; {detail}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF white bleed band", "press check", "A6", ok, detail)

    def _images(self, folder: str) -> None:
        specs = [
            ("JPG", "photo", True, "card.jpg"),
            ("PNG", "gradient", False, "art.png"),
            ("TIFF", "flat", True, "art.tif"),
            ("screenshot PNG", "photo", True, "screen.png"),
        ]
        for name, kind, sliver, filename in specs:
            width = max(32, int(round(90.0 / 25.4 * 72.0)))
            height = max(32, int(round(50.0 / 25.4 * 72.0)))
            rgb = _rgb(width, height, kind)
            if sliver:
                rgb = _sliver(rgb, 1)
            path = os.path.join(folder, filename)
            _save_image(path, rgb)
            try:
                loaded = _load_rgb(path)
                report = _raster_report(loaded, 90, 50)
                ok, detail = _worst(report)
            except Exception as exc:
                ok, detail = False, str(exc)[:240]
            _add(name, "image builder", "card 90 × 50", ok, detail)

        rgb = _sliver(_rgb(80, 60, "photo"), 2)
        try:
            from smart_bleed import pixel_drift_bleed_expand

            expanded = pixel_drift_bleed_expand(rgb[:, :, ::-1], 24, 24, 24, 24, 300.0)
            right = expanded[:, -8:, :3].astype(np.float32)
            ok = float(right.mean()) < 160.0 and float(right[:, :, 1].mean()) > 60.0
            detail = f"outer mean {float(right.mean()):.1f}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PNG sliver", "review pixel-drift", "raster", ok, detail)

    def _paths(self, folder: str) -> None:
        jpg = os.path.join(folder, "quick.jpg")
        rgb = _sliver(_rgb(max(32, int(round(90 / 25.4 * 150))), max(32, int(round(50 / 25.4 * 150))), "photo"), 1)
        _save_image(jpg, rgb)
        try:
            result = make_print_ready(jpg, os.path.join(folder, "qp-jpg"), 90, 50, "card-90x50", "Business card 90 × 50", filename="photo.jpg")
            quality = result.get("bleedQuality") or {}
            ok = bool(quality.get("ok")) and result.get("light") != "red" and os.path.isfile(result.get("pressPath") or "")
            detail = f"light {result.get('light')}; {quality.get('note') or 'bleed matches'}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("JPG", "quick-print", "card 90 × 50", ok, detail)

        pdf = os.path.join(folder, "quick-canva.pdf")
        try:
            _write_photo_pdf(pdf, 147.9, 210.1, "photo", 1, 72, 0)
            result = make_print_ready(pdf, os.path.join(folder, "qp-pdf"), 148, 210, "a5", "A5", filename="canva.pdf")
            quality = result.get("bleedQuality") or {}
            ok = bool(quality.get("ok")) and result.get("light") != "red" and os.path.isfile(result.get("pressPath") or "")
            detail = f"light {result.get('light')}; {quality.get('note') or 'bleed matches'}"
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PDF Canva", "quick-print", "A5", ok, detail)

        png = os.path.join(folder, "review.png")
        review_rgb = _sliver(_rgb(max(40, int(round(74 / 25.4 * 120))), max(40, int(round(105 / 25.4 * 120))), "flat"), 1)
        _save_image(png, review_rgb)
        try:
            result_path = os.path.join(folder, "compile-result.json")
            status_path = os.path.join(folder, "compile-status.json")
            press = os.path.join(folder, "compile-press.pdf")
            proc = subprocess.run(
                [
                    sys.executable,
                    os.path.join(os.path.dirname(__file__), "compile_press_pdf.py"),
                    "--input", png,
                    "--output", press,
                    "--trim-w", "74",
                    "--trim-h", "105",
                    "--status-file", status_path,
                    "--result-file", result_path,
                ],
                cwd=os.path.dirname(__file__),
                capture_output=True,
                text=True,
                timeout=180,
            )
            payload = json.loads(open(result_path, encoding="utf-8").read()) if os.path.isfile(result_path) else {}
            quality = payload.get("bleedQuality") or {}
            ok = proc.returncode == 0 and payload.get("success") and quality.get("ok") and os.path.isfile(press)
            detail = quality.get("note") or (proc.stderr[-240:] if not ok else "bleed matches")
        except Exception as exc:
            ok, detail = False, str(exc)[:240]
        _add("PNG", "review compile", "A7", ok, detail)

    def _refused(self, folder: str) -> None:
        psd = os.path.join(folder, "art.psd")
        try:
            _save_image(psd, _rgb(40, 30, "flat"), kind="psd")
        except Exception:
            open(psd, "wb").write(b"8BPS\x00\x01")
        result = make_print_ready(psd, os.path.join(folder, "qp-psd"), 90, 50, "card-90x50", "Business card", filename="art.psd")
        refused = result.get("light") == "red" and not os.path.isfile(result.get("pressPath") or "missing")
        _add("PSD", "quick-print", "card", refused, "refused, not a supported upload" if refused else str(result.get("light")))

        docx = os.path.join(folder, "brief.docx")
        open(docx, "wb").write(b"PK\x03\x04not-a-real-docx")
        result = make_print_ready(docx, os.path.join(folder, "qp-docx"), 148, 210, "a5", "A5", filename="brief.docx")
        message = " ".join(result.get("reasons") or []) + " " + str(result.get("clientMessage") or "")
        refused = result.get("light") == "red" and "export it as a PDF" in message and not os.path.isfile(result.get("pressPath") or "missing")
        _add("DOCX", "quick-print", "A5", refused, OFFICE_MESSAGE if refused else message[:180])

        pptx = os.path.join(folder, "slides.pptx")
        open(pptx, "wb").write(b"PK\x03\x04not-a-real-pptx")
        result = make_print_ready(pptx, os.path.join(folder, "qp-pptx"), 148, 210, "a5", "A5", filename="slides.pptx")
        message = " ".join(result.get("reasons") or []) + " " + str(result.get("clientMessage") or "")
        refused = result.get("light") == "red" and "export it as a PDF" in message and not os.path.isfile(result.get("pressPath") or "missing")
        _add("PPTX", "quick-print", "A5", refused, OFFICE_MESSAGE if refused else message[:180])


def _save_image(path: str, rgb: np.ndarray, kind: str = "") -> None:
    from PIL import Image

    image = Image.fromarray(rgb, "RGB")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        image.save(path, format="JPEG", quality=92, dpi=(72, 72))
    elif ext in (".tif", ".tiff"):
        image.save(path, format="TIFF", dpi=(72, 72))
    elif ext == ".psd" or kind == "psd":
        image.save(path, format="PSD")
    else:
        image.save(path, format="PNG", dpi=(72, 72))


def _load_rgb(path: str) -> np.ndarray:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    return np.asarray(image)


def _write_eps(path: str, w_mm: float, h_mm: float) -> None:
    w_pt = float(w_mm) * MM
    h_pt = float(h_mm) * MM
    body = (
        "%!PS-Adobe-3.0 EPSF-3.0\n"
        f"%%BoundingBox: 0 0 {int(round(w_pt))} {int(round(h_pt))}\n"
        "%%EndComments\n"
        "0.14 0.43 0.28 setrgbcolor\n"
        f"0 0 {w_pt:.2f} {h_pt:.2f} rectfill\n"
        "showpage\n"
        "%%EOF\n"
    )
    with open(path, "w", encoding="ascii") as handle:
        handle.write(body)


def _write_white_ring(path: str, trim_w: float, trim_h: float) -> None:
    """A finished-looking page whose 5 mm bleed is paper white. It must be flagged."""
    import pymupdf as fitz

    bleed = 5.0 * MM
    w_pt = float(trim_w) * MM + 2 * bleed
    h_pt = float(trim_h) * MM + 2 * bleed
    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(bleed, bleed, w_pt - bleed, h_pt - bleed), color=None, fill=(0.15, 0.36, 0.14))
    page.set_trimbox(fitz.Rect(bleed, bleed, w_pt - bleed, h_pt - bleed))
    page.set_cropbox(page.rect)
    page.set_bleedbox(page.rect)
    doc.save(path)
    doc.close()


def _write_table() -> None:
    lines = ["| File | Path | Size | Result | Detail |", "| --- | --- | --- | --- | --- |"]
    for row in ROWS:
        detail = row["detail"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['kind']} | {row['path']} | {row['size']} | {row['result']} | {detail} |")
    text = "\n".join(lines) + "\n"
    os.makedirs("/opt/cursor/artifacts", exist_ok=True)
    with open("/opt/cursor/artifacts/bleed-quality-table.txt", "w", encoding="utf-8") as handle:
        handle.write(text)
    print("\n" + text, flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
