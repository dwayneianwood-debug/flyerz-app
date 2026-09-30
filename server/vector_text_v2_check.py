#!/usr/bin/env python3
"""Checks for vector text rebuild v2. Engines are called, not rewritten."""

from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vector_text_v2 import (
    BLEED_MM,
    FONTS,
    MIN_PPI,
    _scripty,
    fit_line,
    font_path,
    rebuild_fitted,
)

ART = os.environ.get("QUICK_PRINT_ARTIFACTS", "/opt/cursor/artifacts")
SANS = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def test_faces_and_leashes() -> None:
    for key, _name, _role in FONTS:
        path = font_path(key)
        check(f"font-{key}", os.path.exists(path) and os.path.getsize(path) > 1000, path)
    check("bleed-5", BLEED_MM == 5.0)
    check("ppi-400", MIN_PPI >= 400)
    root = os.path.dirname(__file__)
    leash = open(os.path.join(root, "smart_bleed.py"), encoding="utf-8", errors="ignore").read()
    check("leash-buffer", "BufferSpace=50000000" in leash or "-dBufferSpace=50000000" in leash or "50000000" in leash)
    check("leash-threads", "NumRenderingThreads=1" in leash or "-dNumRenderingThreads=1" in leash)


def test_width_fit() -> None:
    _size, _track, width = fit_line("cinzel-600", "MEDELLA", 180.0, 28.0)
    check("cinzel-fits-width", abs(width - 180.0) / 180.0 < 0.08, f"{width:.2f}")
    _size, _track, body = fit_line("crimson", "Live blood analysis", 220.0, 16.0)
    check("crimson-fits-width", abs(body - 220.0) / 220.0 < 0.12, f"{body:.2f}")


def test_script_stays_raster() -> None:
    ink = np.zeros((40, 180), np.uint8)
    cv2.line(ink, (8, 28), (170, 12), 255, 6)
    check("connected-ink-is-script", _scripty(ink, "Medella"))
    image = Image.new("RGB", (640, 400), (236, 228, 210))
    draw = ImageDraw.Draw(image)
    draw.line((40, 180, 420, 120), fill=(40, 70, 40), width=18)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    block = {
        "id": "t1",
        "text": "Medella",
        "bbox": [0.05, 0.28, 0.7, 0.22],
        "color_hex": "#284628",
        "bold": False,
        "score": 0.61,
    }
    out = tempfile.mkdtemp(prefix="vector-script-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        bgr, 90, 50, pdf, blocks=[block], reocr=lambda _path: "Medella",
    )
    check("script-not-set", result.get("ok") is False and not os.path.exists(pdf), str(result.get("reason")))
    check("script-amber", result.get("amber") is True)


def test_overlap_and_qa_gate() -> None:
    image = _poster()
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    both = [
        _block("MARKET DAY", 0.12, 0.50, 0.62, 0.08, "#fff4d2"),
        _block("MARKET DAY", 0.14, 0.52, 0.60, 0.08, "#fff4d2"),
    ]
    out = tempfile.mkdtemp(prefix="vector-overlap-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(bgr, 148, 210, pdf, blocks=both, reocr=lambda _path: "MARKET DAY")
    modes = [line.get("mode") for line in result.get("lines") or []]
    check("overlap-one-vector", result.get("ok") is True and modes.count("vector") == 1, str(modes))
    check("overlap-amber", result.get("amber") is True, str(result.get("reason")))

    failed = rebuild_fitted(
        bgr, 148, 210, os.path.join(out, "bad.pdf"),
        blocks=[_block("MARKET DAY", 0.12, 0.50, 0.62, 0.08, "#fff4d2")],
        reocr=lambda _path: "",
    )
    check("qa-recall-fallback", failed.get("ok") is False and not os.path.exists(os.path.join(out, "bad.pdf")), str(failed.get("reason")))


def test_bullet_cmyk_and_boxes() -> None:
    image = _poster()
    draw = ImageDraw.Draw(image)
    draw.ellipse((78, 768, 108, 798), fill=(255, 244, 210))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    out = tempfile.mkdtemp(prefix="vector-bullet-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        bgr, 148, 210, pdf,
        blocks=[_block("SATURDAY", 0.14, 0.62, 0.46, 0.06, "#ffffff")],
        reocr=lambda _path: "SATURDAY",
    )
    check("bullet-job", result.get("ok") is True, str(result.get("reason")) + " " + str(result.get("qa")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    page = doc[0]
    text = page.get_text("text") or ""
    drawings = page.get_drawings()
    images = page.get_images()
    info = doc.extract_image(images[0][0])
    inset = float(page.trimbox.x0) * 25.4 / 72.0
    fonts = page.get_fonts()
    doc.close()
    check("bullet-word", "SATURDAY" in text, text[:120])
    check("bullet-dot", any(item.get("type") == "c" or item.get("items") for item in drawings), str(len(drawings)))
    check("bullet-cmyk", info.get("colorspace") == 4, str(info.get("colorspace")))
    check("bullet-ppi", float(result["qa"].get("ppi_x") or 0) >= 399, str(result.get("qa")))
    check("bullet-inset", abs(inset - 5) < 0.45, f"{inset:.2f}")
    check("bullet-subset", bool(fonts) and all("+" in str(item[3]) for item in fonts), str(fonts)[:180])
    check("bullet-one-image", len(images) == 1, str(len(images)))


def test_refine_repairs_a_joined_word() -> None:
    from vector_text_v2 import _refine_blocks

    image = Image.new("RGB", (900, 200), (20, 40, 30))
    draw = ImageDraw.Draw(image)
    draw.text((40, 70), "LIVE BLOOD ANALYSIS", font=ImageFont.truetype(SANS, 42), fill=(255, 255, 255))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    block = {
        "text": "LIVE BLOODA NALYSIS",
        "bbox": [0.03, 0.28, 0.75, 0.42],
        "score": 0.72,
        "color_hex": "#ffffff",
    }
    refined = _refine_blocks(bgr, [block])
    text = str(refined[0]["text"]).upper()
    check("refine-blood", "BLOOD ANALYSIS" in text and "BLOODA" not in text, text)


def test_real_ocr_is_quick() -> None:
    import time

    image = _poster()
    path = os.path.join(tempfile.mkdtemp(prefix="vector-ocr-"), "src.png")
    image.save(path)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    out = tempfile.mkdtemp(prefix="vector-real-")
    pdf = os.path.join(out, "press.pdf")
    started = time.perf_counter()
    result = rebuild_fitted(bgr, 148, 210, pdf)
    elapsed = time.perf_counter() - started
    print(f"TIME real-ocr {elapsed:.1f}s {result.get('timings')}")
    check("real-ocr-time", elapsed < 90, f"{elapsed:.1f}s")
    check("real-ocr-ok", result.get("ok") is True, str(result.get("reason")) + " " + str((result.get("qa") or {}).get("reocr")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = (doc[0].get_text("text") or "").upper()
    doc.close()
    check("real-ocr-vector", "MARKET" in text, text[:200])
    os.makedirs(ART, exist_ok=True)
    _render(pdf, os.path.join(ART, "vector-v2-synthetic.png"))


def _poster() -> Image.Image:
    image = Image.new("RGB", (900, 1200), (24, 72, 140))
    draw = ImageDraw.Draw(image)
    draw.ellipse((560, 80, 820, 340), fill=(240, 120, 20))
    draw.rounded_rectangle((80, 520, 820, 1040), radius=28, fill=(12, 28, 70))
    font = ImageFont.truetype(SANS, 72)
    small = ImageFont.truetype(SANS, 48)
    draw.text((120, 620), "MARKET DAY", font=font, fill=(255, 244, 210))
    draw.text((120, 760), "SATURDAY", font=small, fill=(255, 255, 255))
    return image


def _block(text: str, x: float, y: float, w: float, h: float, colour: str) -> dict:
    return {
        "id": text,
        "text": text,
        "bbox": [x, y, w, h],
        "color_hex": colour,
        "bold": True,
        "score": 0.98,
    }


def _render(path: str, dest: str) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.1, 1.1), alpha=False)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    pix.save(dest)
    doc.close()


def main() -> None:
    test_faces_and_leashes()
    test_width_fit()
    test_script_stays_raster()
    test_overlap_and_qa_gate()
    test_bullet_cmyk_and_boxes()
    test_refine_repairs_a_joined_word()
    test_real_ocr_is_quick()
    print("ALL PASS")


if __name__ == "__main__":
    main()
