#!/usr/bin/env python3
"""End-to-end checks for sales quick mode. Engines are called, not rewritten."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from quick_print import BLEED_MM, decide_light, make_print_ready, too_small

ART = os.environ.get("QUICK_PRINT_ARTIFACTS", "/opt/cursor/artifacts")
FONT = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _font(size: int):
    if os.path.exists(FONT):
        return ImageFont.truetype(FONT, size)
    return ImageFont.load_default()


def _docx(path: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>Please print this</w:t></w:r></w:p></w:body></w:document>",
        )


def _flyer(path: str) -> None:
    image = Image.new("RGB", (1024, 1024), (24, 72, 140))
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, 80, 940, 400), fill=(240, 120, 20))
    draw.rounded_rectangle((120, 460, 900, 900), radius=36, fill=(12, 28, 70), outline=(210, 170, 90), width=8)
    draw.text((180, 560), "MARKET DAY", font=_font(72), fill=(255, 236, 180))
    draw.text((180, 700), "SATURDAY", font=_font(54), fill=(255, 255, 255))
    image.save(path, format="PNG")


def _wide(path: str) -> None:
    image = Image.new("RGB", (1800, 700), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 120, 700), fill=(220, 20, 20))
    draw.rectangle((1680, 0, 1800, 700), fill=(20, 40, 220))
    draw.rectangle((800, 250, 1000, 450), fill=(20, 180, 60))
    image.save(path, format="PNG")


def _bled_pdf(path: str) -> None:
    import pymupdf as fitz

    mm = 72.0 / 25.4
    bleed = 5 * mm
    trim_w = 148 * mm
    trim_h = 210 * mm
    doc = fitz.open()
    page = doc.new_page(width=trim_w + 2 * bleed, height=trim_h + 2 * bleed)
    page.draw_rect(page.rect, color=None, fill=(0.75, 0.08, 0.08))
    page.draw_rect(fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h), color=None, fill=(0.1, 0.55, 0.2))
    page.insert_text((bleed + 36, bleed + 80), "ALREADY BLEED", fontsize=28, fontname="helv", color=(1, 1, 1))
    trim = fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    doc.save(path)
    doc.close()


def _render(path: str, dest: str) -> np.ndarray:
    import cv2
    import pymupdf as fitz

    doc = fitz.open(path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cv2.imwrite(dest, bgr)
    doc.close()
    return bgr


def _run(src: str, name: str, product: str = "a5") -> dict:
    out = tempfile.mkdtemp(prefix=f"quick-{name}-")
    return make_print_ready(src, out, 148, 210, product, "A5", filename=os.path.basename(src))


def test_rules() -> None:
    office = decide_light({"kind": "office"})
    check("word-rule", office["light"] == "red" and "Word" in office["clientMessage"])
    small = decide_light({"tooSmall": True, "tooSmallMessage": "too small please"})
    check("small-rule", small["light"] == "red" and "too small" in small["clientMessage"])
    green = decide_light({"compiled": True, "enginePassed": True, "upscale": 1.2, "aspectDelta": 0.01})
    check("green-rule", green["light"] == "green" and green["reasons"] == [])
    amber = decide_light({"compiled": True, "enginePassed": True, "aspectExtended": True, "aspectDelta": 0.4, "upscale": 1})
    check("amber-rule", amber["light"] == "amber" and any("extended" in line for line in amber["reasons"]))
    quiet = decide_light({"compiled": True, "enginePassed": True, "aspectExtended": True, "aspectDelta": 0.03, "upscale": 1.4})
    check("small-aspect-stays-green", quiet["light"] == "green", str(quiet))
    check("a5-80px-too-small", too_small(80, 80, 148, 210))
    check("a5-1024-not-too-small", not too_small(1024, 1024, 148, 210))
    check("bleed-constant", BLEED_MM == 5)


def test_word(root: str) -> None:
    path = os.path.join(root, "brief.docx")
    _docx(path)
    result = _run(path, "word")
    check("word-red", result["light"] == "red", result["light"])
    check("word-message", "Word" in result["clientMessage"] and "PDF" in result["clientMessage"], result["clientMessage"])
    check("word-no-press", not result.get("pressPath"))


def test_corrupt(root: str) -> None:
    path = os.path.join(root, "broken.jpg")
    with open(path, "wb") as handle:
        handle.write(b"this is not a jpeg file at all, it is just text")
    result = _run(path, "corrupt")
    check("corrupt-red", result["light"] == "red" and not result.get("pressPath"), result["light"])


def test_too_small(root: str) -> None:
    path = os.path.join(root, "tiny.png")
    Image.new("RGB", (80, 80), (200, 20, 20)).save(path)
    result = _run(path, "tiny")
    check("tiny-red", result["light"] == "red" and "small" in result["clientMessage"].lower(), result["clientMessage"])
    check("tiny-no-press", not result.get("pressPath"))


def test_existing(root: str) -> None:
    path = os.path.join(root, "already.pdf")
    _bled_pdf(path)
    result = _run(path, "existing")
    check("existing-green", result["light"] == "green", f"{result['light']} {result.get('reasons')}")
    media = float(result.get("mediaWidthMm") or 0)
    check("existing-bleed-not-doubled", 156 <= media <= 161, str(result.get("mediaWidthMm")))
    check("existing-not-20mm", abs(media - 168) > 4, str(media))
    check("existing-kept", result.get("existingBleedKept") is True or any("not added again" in line for line in result["decisions"]), str(result["decisions"]))
    press = result["pressPath"]
    import pymupdf as fitz
    doc = fitz.open(press)
    text = doc[0].get_text("text")
    doc.close()
    check("existing-text-kept", "ALREADY" in text, text[:120])
    _render(press, os.path.join(ART, "quick-print-existing-bleed.png"))


def test_wide(root: str) -> None:
    path = os.path.join(root, "banner.png")
    _wide(path)
    result = _run(path, "wide")
    check("wide-amber", result["light"] == "amber", f"{result['light']} {result.get('reasons')}")
    check("wide-reason", any("extended" in line.lower() for line in result["reasons"]), str(result["reasons"]))
    check("wide-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000))
    check("wide-bleed", abs(float(result.get("mediaWidthMm") or 0) - 158) < 3, str(result.get("mediaWidthMm")))
    rendered = _render(result["pressPath"], os.path.join(ART, "quick-print-wrong-aspect.png"))
    check("wide-portrait", rendered.shape[0] > rendered.shape[1], str(rendered.shape))
    rgb = rendered[:, :, ::-1].astype(np.int16)
    red = (rgb[:, :, 0] > 140) & (rgb[:, :, 0] > rgb[:, :, 1] + 40) & (rgb[:, :, 0] > rgb[:, :, 2] + 40)
    blue = (rgb[:, :, 2] > 70) & (rgb[:, :, 2] > rgb[:, :, 0] + 15) & (rgb[:, :, 2] > rgb[:, :, 1] + 10)
    green = (rgb[:, :, 1] > 90) & (rgb[:, :, 1] > rgb[:, :, 0] + 30) & (rgb[:, :, 1] > rgb[:, :, 2] + 20)
    check("wide-keeps-both-ends", int(red.sum()) > 30 and int(blue.sum()) > 30, f"red {int(red.sum())} blue {int(blue.sum())}")
    import cv2
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(green.astype(np.uint8), 8)
    big = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_AREA] > 40]
    check("wide-has-centre", len(big) == 1, f"{len(big)} green marks")
    blob = big[0]
    ratio = float(blob[cv2.CC_STAT_WIDTH]) / float(max(1, blob[cv2.CC_STAT_HEIGHT]))
    check(
        "wide-not-stretched",
        0.8 <= ratio <= 1.25,
        f"{int(blob[cv2.CC_STAT_WIDTH])}x{int(blob[cv2.CC_STAT_HEIGHT])} ratio {ratio:.2f}",
    )
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    images = doc[0].get_images()
    doc.close()
    check("wide-one-image", len(images) == 1, str(len(images)))


def test_flyer(root: str) -> None:
    path = os.path.join(root, "ai-flyer.png")
    _flyer(path)
    result = _run(path, "flyer")
    check("flyer-light", result["light"] in ("green", "amber"), f"{result['light']} {result.get('reasons')}")
    check("flyer-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000))
    media_w = float(result.get("mediaWidthMm") or 0)
    media_h = float(result.get("mediaHeightMm") or 0)
    trim_w = float(result.get("trimWidthMm") or 0)
    trim_h = float(result.get("trimHeightMm") or 0)
    check("flyer-trim", abs(trim_w - 148) < 2 and abs(trim_h - 210) < 2, f"{trim_w}x{trim_h}")
    check("flyer-bleed-5", abs(media_w - 158) < 3 and abs(media_h - 220) < 3, f"{media_w}x{media_h}")
    check("flyer-not-doubled", abs(media_w - 168) > 4, str(media_w))
    rendered = _render(result["pressPath"], os.path.join(ART, "quick-print-ai-flyer.png"))
    check("flyer-not-blank", float(rendered.std()) > 12, str(rendered.std()))
    if result.get("proofPng") and os.path.exists(result["proofPng"]):
        shutil.copyfile(result["proofPng"], os.path.join(ART, "quick-print-ai-flyer-proof.png"))
    joined = " ".join(result.get("decisions") or [])
    check("flyer-records-decisions", "5 mm" in joined and "AI Rebuild" in joined, joined[:400])


def main() -> None:
    test_rules()
    root = tempfile.mkdtemp(prefix="quick-print-src-")
    try:
        test_word(root)
        test_corrupt(root)
        test_too_small(root)
        test_existing(root)
        test_wide(root)
        test_flyer(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("ALL QUICK PRINT CHECKS PASSED")


if __name__ == "__main__":
    main()
