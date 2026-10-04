#!/usr/bin/env python3
"""Timings and retype crops for a laptop run.

Prints each job's light, checklist, and stage times. Writes a source/press
crop of every retyped line, plus a before/after photo crop of the flyer and
the poster, at 600 DPI.
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "server"))

import cv2
import numpy as np
from PIL import Image

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
OUT = os.environ.get("LAPTOP_SELFCHECK_OUT", os.path.join(ROOT, "artifacts", "laptop_selfcheck"))


def _jobs() -> list[tuple[str, str, float, float]]:
    medella = os.path.join(FIXTURES, "medella")
    return [
        ("card_front", os.path.join(medella, "card_front.png"), 90, 50),
        ("card_back", os.path.join(medella, "card_back.png"), 90, 50),
        ("flyer_front", os.path.join(medella, "flyer_front.png"), 148, 210),
        ("flyer_back", os.path.join(medella, "flyer_back.png"), 148, 210),
        ("poster", os.path.join(FIXTURES, "catch_fire", "src.jpg"), 148, 210),
        ("existing_bleed", _bled_pdf(), 148, 210),
    ]


def _bled_pdf() -> str:
    import pymupdf as fitz

    mm = 72.0 / 25.4
    bleed = 5 * mm
    trim_w = 148 * mm
    trim_h = 210 * mm
    path = os.path.join(tempfile.mkdtemp(prefix="laptop-bleed-"), "already.pdf")
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
    return path


def _predicted(timings: dict) -> float:
    total = float(timings.get("total_s") or 0)
    spawns = int(timings.get("spawns") or 0)
    return total + 0.08 * spawns


def _traced_image(path: str, trim_w: float, trim_h: float):
    from quick_print import _bottom_bar_row, _extend_to_product, _read_image, _rotate_to_product

    image = _read_image(path)
    if image is None:
        return None
    image, _turned = _rotate_to_product(image, trim_w, trim_h, [])
    picture = image
    extended, flag, _delta = _extend_to_product(image, trim_w, trim_h, path, [])
    if flag and _bottom_bar_row(picture) is not None:
        return extended
    return picture


def _crop_pair(name: str, result: dict, src: str, trim_w: float, trim_h: float, folder: str) -> int:
    import pymupdf as fitz

    lines = [
        line for line in ((result.get("vectorText") or {}).get("lines") or [])
        if line.get("retyped") and line.get("plateRect")
    ]
    if not lines or not result.get("pressPath"):
        return 0
    placement = (result.get("vectorText") or {}).get("placement") or {}
    art = placement.get("artBox") or [0, 0, 1, 1]
    paste_x, paste_y, art_w, art_h = [int(v) for v in art]
    traced = _traced_image(src, trim_w, trim_h)
    if traced is None:
        return 0
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    ppi = float(placement.get("ppi") or 400)
    # Land on the embedded pixels. 600/72 is one pixel off and that blurs the type.
    from green_gate import _press_matrix

    pix = page.get_pixmap(matrix=_press_matrix(page), alpha=False, colorspace=fitz.csRGB)
    press = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    doc.close()
    plate = placement.get("plate") or [press.shape[0], press.shape[1]]
    scale_x = press.shape[1] / float(plate[1] or press.shape[1])
    scale_y = press.shape[0] / float(plate[0] or press.shape[0])
    pad = int(round(3.0 / 25.4 * ppi * scale_x))
    written = 0
    for index, line in enumerate(lines):
        x, y, bw, bh = [int(v) for v in line["plateRect"]]
        x0 = max(0, int(round(x * scale_x)) - pad)
        y0 = max(0, int(round(y * scale_y)) - pad)
        x1 = min(press.shape[1], int(round((x + bw) * scale_x)) + pad)
        y1 = min(press.shape[0], int(round((y + bh) * scale_y)) + pad)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        after = press[y0:y1, x0:x1]
        fx0 = int(round((x0 / scale_x - paste_x) / max(1, art_w) * traced.shape[1]))
        fy0 = int(round((y0 / scale_y - paste_y) / max(1, art_h) * traced.shape[0]))
        fx1 = int(round((x1 / scale_x - paste_x) / max(1, art_w) * traced.shape[1]))
        fy1 = int(round((y1 / scale_y - paste_y) / max(1, art_h) * traced.shape[0]))
        fx0 = max(0, min(fx0, traced.shape[1] - 1))
        fy0 = max(0, min(fy0, traced.shape[0] - 1))
        fx1 = max(fx0 + 1, min(fx1, traced.shape[1]))
        fy1 = max(fy0 + 1, min(fy1, traced.shape[0]))
        before = cv2.cvtColor(traced[fy0:fy1, fx0:fx1], cv2.COLOR_BGR2RGB)
        before = cv2.resize(before, (after.shape[1], after.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        gap = np.full((after.shape[0], 8, 3), 255, np.uint8)
        pair = np.concatenate([before, gap, after], axis=1)
        slug = "".join(ch if ch.isalnum() else "_" for ch in str(line.get("text") or ""))[:40]
        dest = os.path.join(folder, f"{name}_{index:02d}_{slug}.png")
        Image.fromarray(pair).save(dest, dpi=(600, 600))
        written += 1
    return written


def _column_pairs(name: str, result: dict, src: str, trim_w: float, trim_h: float, folder: str) -> int:
    """Full-height source|press crops of the left, middle and right of the page."""
    import pymupdf as fitz

    if not result.get("pressPath"):
        return 0
    placement = (result.get("vectorText") or {}).get("placement") or {}
    art = placement.get("artBox") or [0, 0, 1, 1]
    paste_x, paste_y, art_w, art_h = [int(v) for v in art]
    traced = _traced_image(src, trim_w, trim_h)
    if traced is None:
        return 0
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    ppi = float(placement.get("ppi") or 600)
    from green_gate import _press_matrix

    pix = page.get_pixmap(matrix=_press_matrix(page), alpha=False, colorspace=fitz.csRGB)
    press = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    doc.close()
    plate = placement.get("plate") or [press.shape[0], press.shape[1]]
    scale_x = press.shape[1] / float(plate[1] or press.shape[1])
    scale_y = press.shape[0] / float(plate[0] or press.shape[0])
    ax0 = int(round(paste_x * scale_x))
    ay0 = int(round(paste_y * scale_y))
    ax1 = int(round((paste_x + art_w) * scale_x))
    ay1 = int(round((paste_y + art_h) * scale_y))
    ax0, ay0 = max(0, ax0), max(0, ay0)
    ax1, ay1 = min(press.shape[1], ax1), min(press.shape[0], ay1)
    if ax1 - ax0 < 30 or ay1 - ay0 < 30:
        return 0
    # Gutters are the quiet vertical bands between columns of type.
    gray = cv2.cvtColor(press[ay0:ay1, ax0:ax1], cv2.COLOR_RGB2GRAY)
    dark = (gray < 140).sum(axis=0).astype(np.float32)
    smooth = cv2.GaussianBlur(dark.reshape(1, -1), (1, 31), 0).ravel()
    span = smooth.shape[0]
    cuts = [0]
    for start, end in ((int(span * 0.2), int(span * 0.45)), (int(span * 0.55), int(span * 0.82))):
        if end <= start:
            continue
        cuts.append(start + int(np.argmin(smooth[start:end])))
    cuts.append(span)
    written = 0
    labels = ("left", "middle", "right")
    for index in range(min(3, len(cuts) - 1)):
        x0 = ax0 + cuts[index]
        x1 = ax0 + cuts[index + 1]
        after = press[ay0:ay1, x0:x1]
        fx0 = int(round((x0 / scale_x - paste_x) / max(1, art_w) * traced.shape[1]))
        fx1 = int(round((x1 / scale_x - paste_x) / max(1, art_w) * traced.shape[1]))
        fy0 = int(round((ay0 / scale_y - paste_y) / max(1, art_h) * traced.shape[0]))
        fy1 = int(round((ay1 / scale_y - paste_y) / max(1, art_h) * traced.shape[0]))
        fx0 = max(0, min(fx0, traced.shape[1] - 1))
        fy0 = max(0, min(fy0, traced.shape[0] - 1))
        fx1 = max(fx0 + 1, min(fx1, traced.shape[1]))
        fy1 = max(fy0 + 1, min(fy1, traced.shape[0]))
        before = cv2.cvtColor(traced[fy0:fy1, fx0:fx1], cv2.COLOR_BGR2RGB)
        before = cv2.resize(before, (after.shape[1], after.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        gap = np.full((after.shape[0], 12, 3), 255, np.uint8)
        pair = np.concatenate([before, gap, after], axis=1)
        dest = os.path.join(folder, f"{name}_column_{labels[index]}.png")
        Image.fromarray(pair).save(dest, dpi=(600, 600))
        written += 1
    return written


def _sharpness(folder: str) -> None:
    """Before and after the press-picture sharpen, a photo window at 600 DPI."""
    from ai_upscale import photo_unsharp
    from quick_print import _extend_to_product, _read_image, _rotate_to_product
    from vector_plate import place_plate

    samples = [
        ("flyer", os.path.join(FIXTURES, "medella", "flyer_front.png"), 148, 210, (0.08, 0.43, 0.11, 0.12)),
        ("poster", os.path.join(FIXTURES, "catch_fire", "src.jpg"), 148, 210, (0.28, 0.42, 0.22, 0.18)),
    ]
    for name, path, trim_w, trim_h, box in samples:
        image = _read_image(path)
        image, _turned = _rotate_to_product(image, trim_w, trim_h, [])
        image, _flag, _delta = _extend_to_product(image, trim_w, trim_h, path, [])
        placed = place_plate(image, [], trim_w, trim_h, 5)
        plate = placed["image"]
        sharp = photo_unsharp(plate)
        height, width = image.shape[:2]
        x, y, bw, bh = box
        ax = int(x * width)
        ay = int(y * height)
        aw = int(bw * width)
        ah = int(bh * height)
        mapper = placed["map"]
        x0, y0 = mapper(ax, ay)
        x1, y1 = mapper(ax + aw, ay + ah)
        left, top = int(min(x0, x1)), int(min(y0, y1))
        right, bottom = int(max(x0, x1)), int(max(y0, y1))
        left = max(0, left)
        top = max(0, top)
        right = min(plate.shape[1], right)
        bottom = min(plate.shape[0], bottom)
        before = cv2.cvtColor(plate[top:bottom, left:right], cv2.COLOR_BGR2RGB)
        after = cv2.cvtColor(sharp[top:bottom, left:right], cv2.COLOR_BGR2RGB)
        scale = 600.0 / 400.0
        for label, crop in (("before", before), ("after", after)):
            shown = cv2.resize(
                crop,
                (max(1, int(round(crop.shape[1] * scale))), max(1, int(round(crop.shape[0] * scale)))),
                interpolation=cv2.INTER_LANCZOS4,
            )
            Image.fromarray(shown).save(os.path.join(folder, f"sharp_{name}_{label}_600.png"), dpi=(600, 600))


def main() -> None:
    from quick_print import make_print_ready

    os.makedirs(OUT, exist_ok=True)
    report = []
    for name, src, trim_w, trim_h in _jobs():
        out = tempfile.mkdtemp(prefix=f"laptop-{name}-")
        result = make_print_ready(src, out, trim_w, trim_h, name, name, filename=os.path.basename(src))
        timings = (result.get("vectorText") or {}).get("timings") or {}
        items = result.get("checklist") or []
        failed = [item.get("id") for item in items if not item.get("passed")]
        crops = _crop_pair(name, result, src, trim_w, trim_h, OUT)
        _column_pairs(name, result, src, trim_w, trim_h, OUT)
        line = (
            f"{name}: light={result.get('light')} failed={failed or '-'} "
            f"retype_s={timings.get('retype_s', 0)} total_s={timings.get('total_s', 0)} "
            f"predicted_s={_predicted(timings):.2f} crops={crops}"
        )
        print(line)
        for item in items:
            mark = "Pass" if item.get("passed") else "Fail"
            print(f"  {mark} {item.get('label')}" + ("" if item.get("passed") else f" — {item.get('detail')}"))
        report.append(line)
    _sharpness(OUT)
    path = os.path.join(OUT, "report.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(report) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
