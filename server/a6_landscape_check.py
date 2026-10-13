#!/usr/bin/env python3
"""A6 landscape Canva-style PDF: both pages, existing bleed kept, amber not red."""

from __future__ import annotations

import os
import tempfile
import urllib.error
import urllib.request

import cv2
import numpy as np

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ok  {name}")
        return
    FAILURES.append(name)
    print(f"FAIL  {name} {detail}")


def test_size_match() -> None:
    from press_ready_engine import inferred_side_bleed
    from quick_print import _match_product

    bleed = inferred_side_bleed(152.9, 110.1, 148, 105)
    check(
        "size-is-existing-bleed",
        bool(bleed) and bleed["kind"] == "existing" and 2.2 <= bleed["left"] <= 2.7 and 2.2 <= bleed["top"] <= 2.8,
        str(bleed),
    )
    exact = inferred_side_bleed(148, 210, 148, 210)
    check("exact-trim", bool(exact) and exact["kind"] == "trim", str(exact))
    product, rotated = _match_product(152.9, 110.1)
    check(
        "auto-a6-landscape",
        bool(product) and product.get("id") == "a6-landscape" and rotated is False,
        str(product) + f" rotated={rotated}",
    )
    portrait, turned = _match_product(105, 148)
    check("portrait-a6", bool(portrait) and portrait.get("id") == "a6" and turned is False, str(portrait))


def test_glyph_not_em_box() -> None:
    import pymupdf as fitz

    from green_gate import _glyph_words, _pdf_text_edge

    trial = fitz.open()
    page = trial.new_page(width=500, height=400)
    page.insert_text((40, 200), "LOCATION:", fontsize=72, fontname="helv", color=(0, 0, 0))
    words = page.get_text("words") or []
    glyphs = _glyph_words(page)
    check("glyph-found", bool(words) and bool(glyphs), str(glyphs))
    if not words or not glyphs:
        trial.close()
        return
    word = fitz.Rect(words[0][:4])
    glyph = glyphs[0][0]
    gap = word.y1 - glyph.y1
    check("em-box-taller", gap > 1.0, f"gap={gap:.2f}")
    trim_bottom = glyph.y1 + (1.4 * 72.0 / 25.4)
    page.set_mediabox(page.rect)
    page.set_cropbox(page.rect)
    page.set_bleedbox(page.rect)
    page.set_trimbox(fitz.Rect(20, 20, 480, trim_bottom))
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "glyph.pdf")
    trial.save(path)
    trial.close()
    over, near = _pdf_text_edge(path, 148, 105)
    check("glyph-not-over", "LOCATION:" not in over and not any("LOCATION" in item for item in over), str(over))
    check("glyph-near", any("LOCATION" in item for item in near), str(near))
    check("word-box-would-cross", word.y1 > trim_bottom + 0.4, f"word {word.y1:.1f} trim {trim_bottom:.1f}")


def test_smask_ignored() -> None:
    import pymupdf as fitz

    from green_gate import _colour

    from PIL import Image

    folder = tempfile.mkdtemp()
    cmyk = Image.new("CMYK", (16, 16), (0, 80, 80, 0))
    grey = Image.new("L", (16, 16), 180)
    cmyk_path = os.path.join(folder, "cmyk.tif")
    grey_path = os.path.join(folder, "mask.png")
    cmyk.save(cmyk_path, format="TIFF")
    grey.save(grey_path, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=120, height=120)
    with open(grey_path, "rb") as handle:
        mask_bytes = handle.read()
    page.insert_image(fitz.Rect(20, 20, 60, 60), filename=cmyk_path, mask=mask_bytes)
    path = os.path.join(folder, "mask.pdf")
    doc.save(path)
    full = page.get_images(full=True)
    doc.close()
    masks = [int(item[1]) for item in full if len(item) > 1 and int(item[1] or 0)]
    check("smask-present", bool(masks), str(full))
    cmyk, _ink = _colour(path)
    check("smask-not-cmyk-fail", cmyk["passed"] is True, str(cmyk))


def test_replicate_note_stays_local() -> None:
    from press_ready_engine import replicate_note

    saved_token = os.environ.get("REPLICATE_API_TOKEN")
    saved_flag = os.environ.pop("VECTOR_ESRGAN", None)
    os.environ["REPLICATE_API_TOKEN"] = "test-token"
    calls = []
    old = urllib.request.urlopen

    def boom(req, timeout=None):
        calls.append(getattr(req, "full_url", "url"))
        raise urllib.error.URLError("blocked")

    urllib.request.urlopen = boom
    try:
        note = replicate_note()
    finally:
        urllib.request.urlopen = old
        if saved_token is None:
            os.environ.pop("REPLICATE_API_TOKEN", None)
        else:
            os.environ["REPLICATE_API_TOKEN"] = saved_token
        if saved_flag is not None:
            os.environ["VECTOR_ESRGAN"] = saved_flag
    check("no-replicate-http", not calls and "Real-ESRGAN is off" in note, note + str(calls))


def _canva_pdf(path: str) -> None:
    import pymupdf as fitz

    width, height = 433.5, 312.0
    trim_w = 148.0 * 72.0 / 25.4
    trim_h = 105.0 * 72.0 / 25.4
    inset_x = (width - trim_w) / 2.0
    inset_y = (height - trim_h) / 2.0
    doc = fitz.open()
    front = doc.new_page(width=width, height=height)
    front.draw_rect(front.rect, color=None, fill=(0.85, 0.08, 0.08))
    front.draw_rect(fitz.Rect(width / 2.0, 0, width, height), color=None, fill=(0.08, 0.18, 0.82))
    front.draw_rect(fitz.Rect(0, 0, width, 16), color=None, fill=(0.05, 0.65, 0.15))
    front.draw_rect(fitz.Rect(0, height - 16, width, height), color=None, fill=(0.95, 0.8, 0.05))
    trim_bottom = height - inset_y
    # Capitals sit on the baseline. 1.4 mm inside the trim is inside the 3 mm safe zone.
    baseline = trim_bottom - (1.4 * 72.0 / 25.4)
    font_file = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    front.insert_font(fontname="DEJAVU", fontfile=font_file)
    front.insert_text((inset_x + 24, baseline), "LOCATION:", fontsize=36, fontname="DEJAVU", color=(0, 0, 0))
    front.insert_text((inset_x + 30, inset_y + 40), "OPEN DAY", fontsize=18, fontname="DEJAVU", color=(1, 1, 1))
    try:
        from PIL import Image as PILImage

        swatch = PILImage.new("RGB", (20, 20), (180, 40, 40))
        shade = PILImage.new("L", (20, 20), 200)
        swatch_path = path + ".swatch.png"
        shade_path = path + ".shade.png"
        swatch.save(swatch_path)
        shade.save(shade_path)
        with open(shade_path, "rb") as handle:
            shade_bytes = handle.read()
        front.insert_image(
            fitz.Rect(inset_x + 20, inset_y + 70, inset_x + 50, inset_y + 100),
            filename=swatch_path,
            mask=shade_bytes,
        )
    except Exception as exc:
        print(f"  note smask insert skipped ({exc})")
    back = doc.new_page(width=width, height=height)
    back.draw_rect(back.rect, color=None, fill=(0.05, 0.75, 0.8))
    back.draw_rect(fitz.Rect(width / 2.0, 0, width, height), color=None, fill=(0.75, 0.1, 0.55))
    back.insert_font(fontname="DEJAVU", fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    back.insert_text((inset_x + 24, inset_y + 50), "BACK SIDE", fontsize=18, fontname="DEJAVU", color=(0, 0, 0))
    doc.save(path)
    doc.close()


def test_a6_job() -> None:
    import time

    import pymupdf as fitz

    from quick_print import make_print_ready

    folder = tempfile.mkdtemp(prefix="a6-canva-")
    src = os.path.join(folder, "canva-a6.pdf")
    _canva_pdf(src)
    saved_token = os.environ.get("REPLICATE_API_TOKEN")
    saved_flag = os.environ.pop("VECTOR_ESRGAN", None)
    os.environ["REPLICATE_API_TOKEN"] = "test-token"
    calls = []
    old = urllib.request.urlopen

    def boom(req, timeout=None):
        calls.append(getattr(req, "full_url", "url"))
        raise urllib.error.URLError("blocked")

    urllib.request.urlopen = boom
    started = time.perf_counter()
    try:
        result = make_print_ready(
            src, os.path.join(folder, "out"), 148, 210, "a5", "A5",
            filename="canva-a6.pdf", detect_size=True,
        )
    finally:
        urllib.request.urlopen = old
        if saved_token is None:
            os.environ.pop("REPLICATE_API_TOKEN", None)
        else:
            os.environ["REPLICATE_API_TOKEN"] = saved_token
        if saved_flag is not None:
            os.environ["VECTOR_ESRGAN"] = saved_flag
    elapsed = time.perf_counter() - started
    print(f"  time a6 {elapsed:.2f}s")
    reasons = result.get("reasons") or []
    check("a6-not-red", result.get("light") != "red", str(result.get("light")) + str(reasons))
    check("a6-amber", result.get("light") == "amber", str(result.get("light")) + str(reasons))
    check("a6-no-replicate", not calls, str(calls))
    joined = " ".join(str(item) for item in reasons).lower()
    check("a6-safe-reason", "3 mm" in joined or "location" in joined, joined)
    check("a6-not-cut", "cut line" not in joined, joined)
    check("a6-not-shape", "cannot be extended" not in joined and "shape" not in joined, joined)
    press = result.get("pressPath") or ""
    check("a6-press", bool(press) and os.path.exists(press), press)
    if not press or not os.path.exists(press):
        return
    doc = fitz.open(press)
    try:
        check("a6-pages", doc.page_count == 2, str(doc.page_count))
        media = doc[0].mediabox
        width_mm = media.width * 25.4 / 72.0
        height_mm = media.height * 25.4 / 72.0
        check("a6-size", abs(width_mm - 158) < 1.2 and abs(height_mm - 115) < 1.2, f"{width_mm:.2f}x{height_mm:.2f}")
        text = "\n".join((page.get_text("text") or "") for page in doc)
        check("a6-live-text", "LOCATION" in text and "BACK" in text, text[:240])
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.4, 0.4), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        left = rgb[rgb.shape[0] // 2, 2].astype(int)
        right = rgb[rgb.shape[0] // 2, -3].astype(int)
        check("a6-edges-differ", int(np.abs(left - right).sum()) > 40, f"{left.tolist()} vs {right.tolist()}")
        pix2 = doc[1].get_pixmap(matrix=fitz.Matrix(0.4, 0.4), alpha=False, colorspace=fitz.csRGB)
        rgb2 = np.frombuffer(pix2.samples, dtype=np.uint8).reshape(pix2.h, pix2.w, 3)
        left2 = rgb2[rgb2.shape[0] // 2, 2].astype(int)
        check("a6-page2-edge", int(np.abs(left.astype(int) - left2).sum()) > 40, f"{left.tolist()} vs {left2.tolist()}")
    finally:
        doc.close()
    print(f"  light {result.get('light')} reasons {reasons}")


def test_qr_readable_stays_passed() -> None:
    import pymupdf as fitz

    from smart_bleed import scan_and_fix_qr_codes

    encoder = cv2.QRCodeEncoder_create()
    qr = encoder.encode("https://flyerz.co.za/job")
    if qr is None or qr.size == 0:
        check("qr-encode", False, "encoder returned nothing")
        return
    soft = np.where(qr < 128, 40, 230).astype(np.uint8)
    big = cv2.resize(soft, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    colour = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
    folder = tempfile.mkdtemp()
    png = os.path.join(folder, "qr.png")
    cv2.imwrite(png, colour)
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_image(fitz.Rect(40, 40, 260, 260), filename=png)
    src = os.path.join(folder, "qr.pdf")
    out = os.path.join(folder, "qr-out.pdf")
    doc.save(src)
    doc.close()
    result = scan_and_fix_qr_codes(src, out)
    check("qr-found", int(result.get("qr_count") or 0) >= 1, str(result))
    check("qr-not-failed", result.get("status") != "failed" and int(result.get("qr_unreadable") or 0) == 0, str(result))


def main() -> None:
    test_size_match()
    test_glyph_not_em_box()
    test_smask_ignored()
    test_replicate_note_stays_local()
    test_qr_readable_stays_passed()
    test_a6_job()
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("a6 landscape checks passed")


if __name__ == "__main__":
    main()
