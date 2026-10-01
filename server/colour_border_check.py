#!/usr/bin/env python3
"""Quality checks for the colour-border bleed. Does not alter other strategies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from colour_border import (  # noqa: E402
    PRESETS,
    apply_colour_border_bgr,
    cmyk_to_bgr,
    sample_edge_cmyk,
    stamp_cmyk_bleed,
)
from smart_bleed import BLEED_STRATEGY_MIRROR, auto_resolve_safe_zone  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def test_border_copies_trim() -> None:
    art = np.zeros((40, 60, 3), dtype=np.uint8)
    art[:, :] = (10, 20, 30)
    art[5, 7] = (1, 2, 3)
    out = apply_colour_border_bgr(art, 8, (0, 100, 100, 0))
    check("size", out.shape == (56, 76, 3), str(out.shape))
    check("trim-identical", np.array_equal(out[8:48, 8:68], art))
    border = cmyk_to_bgr((0, 100, 100, 0))
    check("border-colour", tuple(int(v) for v in out[0, 0]) == border, str(tuple(int(v) for v in out[0, 0])))
    check("corner", tuple(int(v) for v in out[-1, -1]) == border)


def test_edge_sample_uses_one_pixel() -> None:
    art = np.zeros((20, 30, 3), dtype=np.uint8)
    art[:, :] = (0, 0, 255)  # interior red in BGR would be (0,0,255) if we only set edge
    art[0, :, :] = (255, 0, 0)  # blue edge top in BGR
    art[-1, :, :] = (255, 0, 0)
    art[:, 0, :] = (255, 0, 0)
    art[:, -1, :] = (255, 0, 0)
    cmyk = sample_edge_cmyk(art)
    # Pure blue BGR (255,0,0) → C100 M100 Y0 K0
    check("edge-c", abs(cmyk[0] - 100) < 1, str(cmyk))
    check("edge-m", abs(cmyk[1] - 100) < 1, str(cmyk))
    check("edge-y", cmyk[2] < 1, str(cmyk))


def test_other_strategy_unchanged() -> None:
    rng = np.random.default_rng(1)
    art = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)
    mirror, _ = auto_resolve_safe_zone(art.copy(), 6, BLEED_STRATEGY_MIRROR, 300.0)
    mirror_again, _ = auto_resolve_safe_zone(art.copy(), 6, "mirror", 300.0)
    check("mirror-stable", mirror.shape == mirror_again.shape and np.array_equal(mirror, mirror_again), str(mirror.shape))
    solid, _ = auto_resolve_safe_zone(art.copy(), 6, "colourBorder", 300.0, border_cmyk=(0, 0, 100, 0))
    check("colour-canvas", solid.shape[0] == art.shape[0] + 12 and solid.shape[1] == art.shape[1] + 12, str(solid.shape))
    # Trim block is the post-safe-zone artwork. With no safe-zone shrink it matches the source.
    check("colour-trim", np.array_equal(solid[6:-6, 6:-6], art))


def test_stamp_leaves_trim() -> None:
    import pikepdf

    h, w = 80, 100
    bleed = 10
    raw = np.zeros((h, w, 4), dtype=np.uint8)
    raw[:, :] = (10, 20, 30, 40)
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 160))
    image = pikepdf.Stream(pdf, raw.tobytes())
    image.Type = pikepdf.Name("/XObject")
    image.Subtype = pikepdf.Name("/Image")
    image.Width = w
    image.Height = h
    image.ColorSpace = pikepdf.Name("/DeviceCMYK")
    image.BitsPerComponent = 8
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
    page.Contents = pikepdf.Stream(pdf, b"q 200 0 0 160 0 0 cm /Im0 Do Q")
    path = tempfile.mktemp(suffix=".pdf")
    pdf.save(path)
    # trim 40mm, bleed 5mm → fraction 5/50 = 0.1 → 10px on width 100, 8px on height 80
    stamp_cmyk_bleed(path, (0, 100, 100, 0), 40, 30, 5)
    out = pikepdf.open(path)
    obj = out.pages[0].Resources.XObject["/Im0"]
    arr = np.frombuffer(obj.read_bytes(), dtype=np.uint8).reshape(h, w, 4)
    check("stamp-border", tuple(int(v) for v in arr[0, 0]) == (0, 255, 255, 0), str(tuple(arr[0, 0])))
    check("stamp-trim", tuple(int(v) for v in arr[h // 2, w // 2]) == (10, 20, 30, 40), str(tuple(arr[h // 2, w // 2])))
    out.close()
    os.remove(path)


def _compile(src: str, dest: str, cmyk: tuple[float, float, float, float]) -> None:
    status = tempfile.mktemp(suffix=".json")
    result = tempfile.mktemp(suffix=".json")
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "compile_press_pdf.py"),
        "--input", src,
        "--output", dest,
        "--strategy", "colourBorder",
        "--color-space", "cmyk",
        "--trim-w", "40",
        "--trim-h", "30",
        "--status-file", status,
        "--result-file", result,
        "--border-c", str(cmyk[0]),
        "--border-m", str(cmyk[1]),
        "--border-y", str(cmyk[2]),
        "--border-k", str(cmyk[3]),
        "--border-label", "Red",
        "--base-name", "colour-border-check",
    ]
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(__file__)), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        check("compile", False, tail)
    check("compile-exists", os.path.exists(dest) and os.path.getsize(dest) > 1000, str(os.path.getsize(dest) if os.path.exists(dest) else 0))


def _sample_pdf(path: str) -> None:
    import fitz
    import pikepdf

    doc = fitz.open(path)
    page = doc[0]
    images = page.get_images(full=True)
    drawings = page.get_drawings()
    check("single-layer", len(images) == 1 and len(drawings) == 0, f"images={len(images)} drawings={len(drawings)}")
    # 40+10 mm by 30+10 mm at 72 pt/in
    expect_w = (50 / 25.4) * 72
    expect_h = (40 / 25.4) * 72
    check(
        "mediabox",
        abs(page.rect.width - expect_w) < 1.5 and abs(page.rect.height - expect_h) < 1.5,
        f"{page.rect.width:.2f}x{page.rect.height:.2f} vs {expect_w:.2f}x{expect_h:.2f}",
    )
    pdf = pikepdf.open(path)
    obj = None
    for _name, candidate in pdf.pages[0].Resources.XObject.items():
        if str(candidate.get("/Subtype")) == "/Image":
            obj = candidate
            break
    check("cmyk-image", obj is not None)
    raw = obj.read_bytes()
    w, h = int(obj.Width), int(obj.Height)
    channels = 4 if len(raw) == w * h * 4 else 3
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, channels)
    bleed_x = max(1, int(round(w * 5 / 50)))
    bleed_y = max(1, int(round(h * 5 / 40)))
    border = arr[1, 1]
    trim = arr[h // 2, w // 2]
    if channels == 4:
        check("bleed-ink", tuple(int(v) for v in border) == (0, 255, 255, 0), str(tuple(int(v) for v in border)))
    else:
        check("bleed-rgb", int(border[0]) > 200 and int(border[1]) < 40 and int(border[2]) < 40, str(tuple(int(v) for v in border)))
    check("trim-not-border", not np.array_equal(border, trim), f"border={tuple(int(v) for v in border)} trim={tuple(int(v) for v in trim)}")
    # Trim block should be the green artwork, not scaled away to a sliver.
    inner = arr[bleed_y:-bleed_y, bleed_x:-bleed_x]
    check("trim-area", inner.shape[0] > h * 0.5 and inner.shape[1] > w * 0.5, str(inner.shape))
    doc.close()
    pdf.close()


def test_compile_jpg_and_pdf() -> None:
    folder = tempfile.mkdtemp(prefix="colour-border-")
    jpg = os.path.join(folder, "art.jpg")
    Image.new("RGB", (240, 180), (0, 180, 40)).save(jpg, quality=95)
    pdf_src = os.path.join(folder, "art.pdf")
    import fitz

    src = fitz.open()
    page = src.new_page(width=200, height=150)
    page.draw_rect(fitz.Rect(0, 0, 200, 150), color=(0, 0.7, 0.1), fill=(0, 0.7, 0.1))
    page.insert_text((20, 80), "TRIM TEXT", fontsize=18, fontname="helv", color=(0, 0, 0))
    src.save(pdf_src)
    src.close()

    jpg_out = os.path.join(folder, "jpg-press.pdf")
    pdf_out = os.path.join(folder, "pdf-press.pdf")
    _compile(jpg, jpg_out, (0, 100, 100, 0))
    _sample_pdf(jpg_out)
    print("OK  compiled-jpg")
    _compile(pdf_src, pdf_out, (100, 0, 0, 0))
    # cyan border for the pdf input
    import pikepdf

    pdf = pikepdf.open(pdf_out)
    obj = next(iter(pdf.pages[0].Resources.XObject.values()))
    raw = obj.read_bytes()
    w, h = int(obj.Width), int(obj.Height)
    channels = 4 if len(raw) == w * h * 4 else 3
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, channels)
    border = tuple(int(v) for v in arr[0, 0])
    if channels == 4:
        check("pdf-cyan-border", border == (255, 0, 0, 0), str(border))
    check("pdf-single-page", len(pdf.pages) == 1)
    pdf.close()
    print("OK  compiled-pdf", folder)


def test_presets_match_client() -> None:
    client = os.path.join(os.path.dirname(__file__), "..", "client", "src", "lib", "colour-border.ts")
    text = open(client, encoding="utf-8").read()
    for preset in PRESETS:
        needle = f'id: "{preset["id"]}"'
        check(f"preset-{preset['id']}", needle in text and f"c: {preset['c']}" in text)


if __name__ == "__main__":
    test_border_copies_trim()
    test_edge_sample_uses_one_pixel()
    test_other_strategy_unchanged()
    test_stamp_leaves_trim()
    test_presets_match_client()
    test_compile_jpg_and_pdf()
    print("all colour border checks passed")
