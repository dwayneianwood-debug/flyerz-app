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
    SANS_KEYS,
    SCRIPT_KEYS,
    _is_phone,
    _rescue_kind,
    _script_allowed,
    _scripty,
    _strip_side_icons,
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


def test_list_item_is_not_script() -> None:
    ink = np.zeros((36, 280), np.uint8)
    cv2.rectangle(ink, (4, 6), (270, 30), 255, -1)
    check("detox-not-script", _script_allowed("Detox and wellness support", ink) is False)
    check("phone-shape", _is_phone("073 703 0766"))
    check("name-rescue", _rescue_kind("Chandré") == "name")
    check("phone-rescue", _rescue_kind("073 703 0766") == "phone")


def test_tick_leaves_the_mask() -> None:
    ink = np.zeros((40, 220), np.uint8)
    cv2.line(ink, (6, 22), (14, 32), 255, 2)
    cv2.line(ink, (14, 32), (26, 8), 255, 2)
    cv2.rectangle(ink, (78, 8), (200, 32), 255, -1)
    stripped, removed = _strip_side_icons(ink)
    check("tick-gap-removed", bool(removed) and int(stripped[:, :40].max()) == 0, str(removed))
    check("tick-words-kept", int(stripped[:, 80:].max()) > 0)


def test_serif_page_is_consistent() -> None:
    """Caps, body, a list line, a name and a phone share the approved serif set."""
    crimson = font_path("crimson")
    cinzel = font_path("cinzel-600")
    image = Image.new("RGB", (980, 1400), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    body = ImageFont.truetype(crimson, 34)
    caps = ImageFont.truetype(cinzel, 42)
    script = ImageFont.truetype(font_path("parisienne"), 78)
    green = (36, 58, 40)
    draw.text((70, 40), "SEE BEYOND", font=caps, fill=green)
    draw.text((70, 160), "Early detection of imbalances", font=body, fill=green)
    draw.text((70, 230), "Supports natural healing daily", font=body, fill=green)
    draw.text((70, 300), "Detox and wellness support", font=body, fill=green)
    draw.text((70, 430), "Chandré", font=script, fill=green)
    draw.text((70, 560), "073 703 0766", font=body, fill=green)
    draw.text((80, 980), "Medella", font=ImageFont.truetype(font_path("parisienne"), 150), fill=green)
    blocks = [
        _block("SEE BEYOND", 0.06, 0.025, 0.42, 0.045, "#243a28"),
        _block("Early detection of imbalances", 0.06, 0.11, 0.62, 0.04, "#243a28"),
        _block("Supports natural healing daily", 0.06, 0.16, 0.62, 0.04, "#243a28"),
        _block("Detox and wellness support", 0.06, 0.21, 0.55, 0.04, "#243a28"),
        _block("Chandré", 0.06, 0.30, 0.28, 0.07, "#243a28"),
        _block("073 703 0766", 0.06, 0.39, 0.40, 0.04, "#243a28"),
        _block("Medella", 0.06, 0.68, 0.55, 0.14, "#243a28"),
    ]
    blocks[4]["score"] = 0.62
    blocks[5]["score"] = 0.4
    out = tempfile.mkdtemp(prefix="vector-serif-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(image_bgr(image), 148, 210, pdf, blocks=blocks, reocr=lambda _path: " ".join(
        line["text"] for line in blocks if line["text"] != "Medella"
    ))
    check("serif-job", result.get("ok") is True, str(result.get("reason")))
    vector = [line for line in result.get("lines") or [] if line.get("mode") == "vector"]
    fonts = {line.get("font") for line in vector}
    check("serif-no-sans", fonts.isdisjoint(SANS_KEYS), str(sorted(fonts)))
    body_fonts = {line.get("font") for line in vector if "detection" in line["text"] or "Supports" in line["text"] or "Detox" in line["text"]}
    check("serif-one-body", body_fonts == {"crimson"}, str(body_fonts))
    detox = next(line for line in vector if line["text"].startswith("Detox"))
    check("detox-not-script-face", detox.get("font") not in SCRIPT_KEYS, str(detox))
    caps_line = next(line for line in vector if line["text"] == "SEE BEYOND")
    check("caps-are-cinzel", caps_line.get("font") == "cinzel-600", str(caps_line))
    phone = next(line for line in result.get("lines") or [] if "073" in line["text"])
    name = next(line for line in result.get("lines") or [] if "hand" in line["text"].lower() or "Chand" in line["text"])
    check("phone-is-vector", phone.get("mode") == "vector" and phone.get("font") == "crimson", str(phone))
    check("name-is-vector", name.get("mode") == "vector" and name.get("font") not in SANS_KEYS, str(name))
    logo = next(line for line in result.get("lines") or [] if line["text"] == "Medella")
    check("logo-stays-raster", logo.get("mode") == "raster", str(logo))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = doc[0].get_text("text") or ""
    doc.close()
    check("logo-not-retyped", "Medella" not in text, text[:240])


def test_badge_digit_stays() -> None:
    image = Image.new("RGB", (900, 420), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 140, 200, 240), fill=(36, 92, 58))
    draw.text((136, 162), "1", font=ImageFont.truetype(SANS, 48), fill=(255, 255, 255))
    draw.text((230, 168), "A small drop of blood", font=ImageFont.truetype(font_path("crimson"), 32), fill=(36, 58, 40))
    draw.line((70, 310, 86, 332), fill=(36, 92, 58), width=4)
    draw.line((86, 332, 112, 292), fill=(36, 92, 58), width=4)
    draw.text((140, 300), "Looks at the cause", font=ImageFont.truetype(font_path("crimson"), 32), fill=(36, 58, 40))
    blocks = [
        _block("A small drop of blood", 0.24, 0.36, 0.55, 0.22, "#243a28"),
        _block("Looks at the cause", 0.06, 0.66, 0.55, 0.18, "#243a28"),
    ]
    out = tempfile.mkdtemp(prefix="vector-badge-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        image_bgr(image), 148, 80, pdf, blocks=blocks,
        reocr=lambda _path: "A small drop of blood Looks at the cause",
    )
    check("badge-job", result.get("ok") is True, str(result.get("reason")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    doc.close()
    white = (rgb[:, :, 0] > 210) & (rgb[:, :, 1] > 210) & (rgb[:, :, 2] > 210)
    green = (rgb[:, :, 1] > 70) & (rgb[:, :, 1] > rgb[:, :, 0] + 15) & (rgb[:, :, 1] > rgb[:, :, 2] + 10)
    # The digit is white pixels enclosed by the green disk.
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(green.astype(np.uint8), 8)
    held = False
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 80:
            continue
        ys, xs = np.where(labels == index)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        if int(white[y0:y1 + 1, x0:x1 + 1].sum()) > 8:
            held = True
    check("badge-digit-survives", held, f"white {int(white.sum())} green {int(green.sum())}")


def test_shared_scale_keeps_the_whole_picture() -> None:
    """The side stripe stays in the trim. The gap beside it is the continued edge, not a crop."""
    from quick_print import make_print_ready

    image = Image.new("RGB", (1024, 1536), (236, 228, 214))
    draw = ImageDraw.Draw(image)
    draw.rectangle((984, 0, 1023, 1535), fill=(210, 40, 40))
    draw.rectangle((960, 0, 983, 1535), fill=(30, 50, 190))
    draw.text((180, 680), "MARKET DAY", font=ImageFont.truetype(SANS, 72), fill=(30, 50, 30))
    folder = tempfile.mkdtemp(prefix="vector-place-")
    src = os.path.join(folder, "src.png")
    image.save(src)
    out = os.path.join(folder, "out")
    result = make_print_ready(src, out, 148, 210, "a5", "A5", filename="src.png")
    check("place-press", bool(result.get("pressPath")), str(result.get("reasons"))[:300])
    joined = " ".join(result.get("decisions") or [])
    check("place-one-scale", "one scale" in joined, joined[:400])
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    inset = int(round(float(page.trimbox.x0) / page.rect.width * pix.w))
    doc.close()
    trim = rgb[:, inset:pix.w - inset]
    blue_at = None
    red_at = None
    for col in range(trim.shape[1] - 1, max(0, trim.shape[1] - 180), -1):
        probe = np.median(trim[:, col], axis=0)
        if blue_at is None and float(probe[2]) > float(probe[0]) + 15:
            blue_at = col
        if red_at is None and float(probe[0]) > float(probe[2]) + 25 and float(probe[0]) > 90:
            red_at = col
    check(
        "place-keeps-both-stripes",
        blue_at is not None and red_at is not None and red_at > blue_at,
        f"blue {blue_at} red {red_at}",
    )


def test_placement_matches_the_reference_fit() -> None:
    """The general fit reproduces the approved Medella placement. No side is hard-coded."""
    from vector_plate import fit_placement

    scale, off_x, off_y = fit_placement(1024, 1536, 148, 210, 5, [(51, 43, 936, 1531)])
    check("flyer-front-scale", abs(scale - 205 / 1536) < 1e-6, f"{scale}")
    check(
        "flyer-front-origin",
        abs(off_y - 7.5) < 1e-6 and abs(off_x - (158 - 1024 * 205 / 1536) / 2) < 1e-4,
        f"{off_x},{off_y}",
    )
    scale, off_x, off_y = fit_placement(1054, 1492, 148, 210, 5, [(63, 53, 1027, 1432)])
    check("flyer-back-scale", abs(scale - 148 / 1054) < 1e-9, f"{scale}")
    check("flyer-back-origin", abs(off_x - 5) < 1e-6, f"{off_x},{off_y}")
    scale, off_x, off_y = fit_placement(1586, 992, 90, 50, 5, [(221, 167, 1478, 865)])
    check("card-front-scale", abs(scale - 50 / 992) < 1e-9, f"{scale}")
    check("card-front-origin", abs(off_y - 5) < 1e-6, f"{off_x},{off_y}")


def test_erase_clears_the_original() -> None:
    image = Image.new("RGB", (420, 180), (240, 236, 228))
    draw = ImageDraw.Draw(image)
    draw.text((36, 48), "Through", font=ImageFont.truetype(font_path("crimson"), 52), fill=(20, 30, 20))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    line = {"text": "Through", "rect": (24, 36, 320, 100), "font": "crimson", "role": "body", "mode": "vector"}
    from vector_plate import erase_text

    clean, kept, skipped = erase_text(bgr, [line], [])
    check("erase-kept", len(kept) == 1 and not skipped, str([item.get("reason") for item in skipped]))
    crop = clean[48:130, 30:300]
    dark = int(((crop[:, :, 0] < 90) & (crop[:, :, 1] < 90) & (crop[:, :, 2] < 90)).sum())
    check("erase-no-ghost", dark < 40, f"dark pixels {dark}")


def test_numeral_mask_spares_the_circle() -> None:
    image = Image.new("RGB", (240, 180), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 30, 130, 120), fill=(36, 92, 58))
    draw.text((72, 48), "1", font=ImageFont.truetype(font_path("crimson"), 42), fill=(255, 255, 255))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    green_before = (bgr[:, :, 1] > 70) & (bgr[:, :, 1] > bgr[:, :, 2] + 10) & (bgr[:, :, 1] > bgr[:, :, 0] + 10)
    line = {"text": "1", "rect": (64, 44, 48, 56), "mode": "vector"}
    from vector_plate import erase_text

    clean, _kept, _skipped = erase_text(bgr, [line], [])
    green_after = (clean[:, :, 1] > 70) & (clean[:, :, 1] > clean[:, :, 2] + 10) & (clean[:, :, 1] > clean[:, :, 0] + 10)
    kept_green = int((green_before & green_after).sum())
    check("circle-survives", kept_green > int(green_before.sum()) * 0.72, f"kept {kept_green} of {int(green_before.sum())}")


def image_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


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
    test_list_item_is_not_script()
    test_tick_leaves_the_mask()
    test_serif_page_is_consistent()
    test_badge_digit_stays()
    test_shared_scale_keeps_the_whole_picture()
    test_placement_matches_the_reference_fit()
    test_erase_clears_the_original()
    test_numeral_mask_spares_the_circle()
    test_real_ocr_is_quick()
    print("ALL PASS")


if __name__ == "__main__":
    main()
