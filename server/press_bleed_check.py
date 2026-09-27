"""Cover-crop warning and bleed-preview JSON for every normal bleed style."""

import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from bleed_size import clamp_bleed_mm
from cover_crop_notice import cover_trim_amounts, recommend_whole_artwork, render_cover_preview
from compile_press_pdf import press_target_media_rect


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        raise SystemExit(f"FAIL {name}: {detail}")
    print(f"OK {name}")


def test_bleed_sizes() -> None:
    check("default", clamp_bleed_mm(None) == 5.0)
    check("ten", clamp_bleed_mm(10) == 10.0)
    check("custom", clamp_bleed_mm(7.2) == 7.2)
    check("too-big", clamp_bleed_mm(40) == 5.0)
    rect = press_target_media_rect(100, 80, 10)
    expected_w = (100 + 20) * 72.0 / 25.4
    check("mediabox-10", abs(rect.width - expected_w) < 0.02, str(rect.width))


def test_cover_warning() -> None:
    wide = np.full((200, 400, 3), 255, np.uint8)
    wide[:, :80] = (0, 0, 0)
    amounts = cover_trim_amounts(400, 200, 100, 100)
    check("crops-sides", amounts["cropped"] and amounts["left"] > 1 and amounts["top"] == 0, str(amounts))
    folder = tempfile.mkdtemp()
    dest = os.path.join(folder, "notice.png")
    notice = render_cover_preview(wide, 100, 100, dest)
    check("warns-content", notice["contentInTrim"] and "cut off" in notice["warning"], notice["warning"])
    check("states-mm", "mm off the left" in notice["summary"], notice["summary"])
    preview = cv2.imread(dest)
    check("shaded", preview is not None and int(preview[0, 0, 2]) > 80, str(None if preview is None else preview[0, 0]))

    rng = np.random.default_rng(1)
    photo = rng.integers(0, 255, (180, 180, 3), dtype=np.uint8)
    whole = recommend_whole_artwork(photo)
    check("extend-photo", whole["method"] == "extend", whole["method"])
    flat = np.full((180, 220, 3), (240, 240, 240), np.uint8)
    border = recommend_whole_artwork(flat)
    check("border-flat", border["method"] == "border", border["method"])


def _run_preview(src: str, kind: str) -> dict:
    folder = tempfile.mkdtemp()
    dest = os.path.join(folder, "preview.png")
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "bleed_preview.py"), src, dest, kind, "10", "90", "50"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    check(f"preview-exit-{kind}", proc.returncode == 0, proc.stderr[-400:])
    parsed = json.loads(proc.stdout)
    check(f"preview-json-{kind}", parsed.get("success") is True and parsed["pages"][0]["bleed_mm"] == 10, proc.stdout[:200])
    image = cv2.imread(parsed["pages"][0]["previewPath"])
    red = int(np.count_nonzero(image[:, :, 2] > 200)) if image is not None else 0
    check(f"cut-line-{kind}", red > 20, str(red))
    return parsed


def test_press_boxes() -> None:
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "art.png")
    cv2.imwrite(src, np.full((200, 300, 3), (30, 80, 180), np.uint8))
    out = os.path.join(folder, "press.pdf")
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "compile_press_pdf.py"),
            "--input", src,
            "--output", out,
            "--strategy", "mirror",
            "--color-space", "rgb",
            "--trim-w", "90",
            "--trim-h", "50",
            "--bleed-mm", "10",
            "--status-file", os.path.join(folder, "status.json"),
            "--result-file", os.path.join(folder, "result.json"),
            "--base-name", "bleed-check",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    check("compile-10", proc.returncode == 0, proc.stderr[-500:])
    import pymupdf
    doc = pymupdf.open(out)
    page = doc[0]
    media_w = page.mediabox.width
    trim_w = page.trimbox.width
    doc.close()
    expect_media = (90 + 20) * 72.0 / 25.4
    expect_trim = 90 * 72.0 / 25.4
    check("boxes-10", abs(media_w - expect_media) < 0.2 and abs(trim_w - expect_trim) < 0.2, f"{media_w} {trim_w}")


def test_preview_styles() -> None:
    folder = tempfile.mkdtemp()
    art = np.full((240, 320, 3), (40, 90, 180), np.uint8)
    cv2.rectangle(art, (40, 40), (280, 200), (20, 20, 220), -1)
    png = os.path.join(folder, "art.png")
    cv2.imwrite(png, art)
    _run_preview(png, "png")

    import pymupdf
    pdf_path = os.path.join(folder, "art.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=120)
    page.insert_image(page.rect, filename=png)
    doc.save(pdf_path)
    doc.close()
    _run_preview(pdf_path, "pdf")

    from smart_bleed import auto_resolve_safe_zone
    styles = ["mirror", "stretch", "replicate", "bgExtract", "upscale", "ai_outpaint", "colourBorder"]
    for style in styles:
        out, _meta = auto_resolve_safe_zone(art.copy(), target_bleed_px=12, bleed_strategy=style, dpi=72)
        variant = os.path.join(folder, f"{style}.png")
        cv2.imwrite(variant, out)
        _run_preview(variant, "png")
        print(f"OK style-{style}")


if __name__ == "__main__":
    test_bleed_sizes()
    test_cover_warning()
    test_press_boxes()
    test_preview_styles()
    print("ALL OK")
