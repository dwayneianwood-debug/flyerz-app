#!/usr/bin/env python3
"""Checks for trace mode. The original ink is traced; fonts are opt-in."""

from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import vector_trace
from vector_text_v2 import font_substitution_enabled, rebuild_fitted
from vector_trace import (
    choke_ink,
    glyphs_agree,
    ink_colour,
    is_lettering,
    mask_iou,
    rasterise_paths,
    refine_ink,
    segment_ink,
    shape_gate,
    sharpen_background,
    trace_fitted,
    trace_mask,
    _trace_fill,
)

SANS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "LiberationSans-Bold.ttf")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _word(text: str, fill, paper, size: int = 72) -> np.ndarray:
    image = Image.new("RGB", (520, 180), paper)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(SANS, size)
    draw.text((36, 40), text, font=font, fill=fill)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _block(text: str, x: float, y: float, w: float, h: float) -> dict:
    return {"text": text, "bbox": [x, y, w, h], "score": 0.99}


def _k_lines(path: str) -> list[str]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        raw = doc[0].read_contents().decode("latin1", errors="replace")
    finally:
        doc.close()
    return [line.strip() for line in raw.splitlines() if " k " in f" {line} "]


def test_segment_dark_light_and_fills() -> None:
    dark = _word("only", (24, 18, 14), (245, 236, 220))
    mask, colour = segment_ink(dark)
    check("dark-mask", mask is not None and 0.02 < float((mask > 0).mean()) < 0.45, "mask")
    check("dark-core", colour is not None and float(colour.mean()) < 80, str(colour))
    light = _word("AND", (246, 240, 228), (18, 72, 42))
    mask, colour = segment_ink(light)
    check("light-mask", mask is not None and float((mask > 0).mean()) < 0.4, "mask")
    check("light-core", colour is not None and float(colour.mean()) > 180, str(colour))
    flat = np.full((80, 200, 3), 200, np.uint8)
    check("flat-skipped", segment_ink(flat)[0] is None)
    solid = np.full((90, 220, 3), 230, np.uint8)
    solid[12:78, 16:200] = (12, 12, 12)
    check("fill-skipped", segment_ink(solid)[0] is None)
    check("icon-text", is_lettering("♡") is False and is_lettering("Medella") and is_lettering("2"))


def test_trace_matches_the_ink() -> None:
    dark = _word("only", (20, 16, 12), (245, 236, 220))
    mask, _colour = segment_ink(dark)
    cleaned, colour = refine_ink(dark, mask)
    paths = trace_mask(cleaned)
    painted = rasterise_paths(paths, cleaned.shape[1], cleaned.shape[0])
    score = mask_iou(cleaned, painted)
    check("word-iou", score >= 0.9, f"{score:.3f}")
    check("word-black", _trace_fill(colour) == (0.0, 0.0, 0.0, 1.0), str(_trace_fill(colour)))
    # A dark green script stays coloured. Near-black snaps to solid K.
    green = np.array([16.0, 43.0, 13.0], np.float32)
    check("green-kept", _trace_fill(green) != (0.0, 0.0, 0.0, 1.0), str(_trace_fill(green)))
    near = np.array([7.0, 15.0, 3.0], np.float32)
    check("near-black", _trace_fill(near) == (0.0, 0.0, 0.0, 1.0))


def test_choke_is_only_the_ring() -> None:
    plate = np.full((80, 160, 3), 240, np.uint8)
    cv2.putText(plate, "No", (20, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (15, 15, 15), 2, cv2.LINE_AA)
    mask, _colour = segment_ink(plate)
    before = plate.copy()
    choke_ink(plate, mask, 1)
    changed = np.any(plate != before, axis=2)
    eroded = cv2.erode((mask > 0).astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
    ring = (mask > 0) & (eroded == 0)
    core = eroded > 0
    outside = mask == 0
    check("choke-core", not bool(changed[core].any()), str(int(changed[core].sum())))
    check("choke-outside", not bool(changed[outside].any()), str(int(changed[outside].sum())))
    check("choke-ring", bool(changed[ring].any()) and int(changed.sum()) <= int(ring.sum()), str(int(changed.sum())))


def test_default_trace_and_font_flag() -> None:
    os.environ.pop("VECTOR_FONTS", None)
    os.environ.pop("VECTOR_REBUILD", None)
    check("fonts-off", font_substitution_enabled() is False)
    page = _word("only", (20, 16, 12), (245, 236, 220), 64)
    # The word sits in the middle of the cream page.
    canvas = np.full((360, 640, 3), (220, 236, 245), np.uint8)
    canvas[80:260, 40:560] = page
    out = tempfile.mkdtemp(prefix="trace-default-")
    pdf = os.path.join(out, "press.pdf")
    result = trace_fitted(canvas, 90, 50, pdf, blocks=[_block("only", 40 / 640, 80 / 360, 520 / 640, 180 / 360)])
    check("trace-ok", result.get("ok") is True and result.get("mode") == "trace", str(result.get("reason")))
    check("trace-vector", int(result.get("vector_lines") or 0) == 1, str(result.get("lines")))
    check("trace-iou", float(result["lines"][0].get("match") or 0) >= 0.9, str(result["lines"]))
    check("trace-time", float(result.get("elapsed_s") or 99) < 20, str(result.get("timings")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    fonts = doc[0].get_fonts()
    drawings = doc[0].get_drawings()
    doc.close()
    check("trace-no-font", fonts == [], str(fonts)[:160])
    check("trace-drawing", len(drawings) >= 1, str(len(drawings)))
    check("trace-k", any(line.startswith("0 0 0 1 k") for line in _k_lines(pdf)), str(_k_lines(pdf)[:4]))

    # A box that fails the IoU gate is left as pixels and is not choked into a path.
    saved = vector_trace.IOU_FLOOR
    vector_trace.IOU_FLOOR = 1.01
    try:
        missed = os.path.join(out, "miss.pdf")
        low = trace_fitted(canvas, 90, 50, missed, blocks=[_block("only", 40 / 640, 80 / 360, 520 / 640, 180 / 360)])
    finally:
        vector_trace.IOU_FLOOR = saved
    check("iou-gate", low.get("ok") is True and int(low.get("vector_lines") or 0) == 0, str(low.get("lines")))
    check("iou-raster", low["lines"] and low["lines"][0]["mode"] == "raster", str(low.get("lines")))
    doc = fitz.open(missed)
    missed_drawings = doc[0].get_drawings()
    doc.close()
    check("iou-no-path", missed_drawings == [], str(len(missed_drawings)))

    heart = canvas.copy()
    heart[20:70, 560:630] = (40, 40, 220)
    mixed = trace_fitted(heart, 90, 50, os.path.join(out, "mix.pdf"), blocks=[
        _block("♡", 560 / 640, 20 / 360, 70 / 640, 50 / 360),
        _block("only", 40 / 640, 80 / 360, 520 / 640, 180 / 360),
    ])
    modes = {line["text"]: line["mode"] for line in mixed.get("lines") or []}
    check("icon-raster", modes.get("♡") == "raster" and modes.get("only") == "vector", str(modes))

    os.environ["VECTOR_FONTS"] = "1"
    try:
        check("fonts-on", font_substitution_enabled() is True)
        poster = Image.new("RGB", (900, 400), (24, 72, 140))
        drawn = ImageDraw.Draw(poster)
        face = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")
        drawn.text((80, 150), "SATURDAY", font=ImageFont.truetype(face, 64), fill=(255, 244, 210))
        poster_bgr = cv2.cvtColor(np.array(poster), cv2.COLOR_RGB2BGR)
        font_pdf = os.path.join(out, "font.pdf")
        fonted = rebuild_fitted(
            poster_bgr, 148, 80, font_pdf,
            blocks=[_block("SATURDAY", 80 / 900, 140 / 400, 560 / 900, 110 / 400)],
        )
    finally:
        os.environ.pop("VECTOR_FONTS", None)
    check("font-opt-in", fonted.get("mode") != "trace", str(fonted.get("mode")) + " " + str(fonted.get("reason")))
    check("font-built", fonted.get("ok") is True, str(fonted.get("reason")) + " " + str(fonted.get("lines"))[:240])
    doc = fitz.open(font_pdf)
    embedded = doc[0].get_fonts()
    doc.close()
    check("font-embedded", bool(embedded), str(embedded)[:160])

    os.environ["VECTOR_REBUILD"] = "0"
    try:
        off = rebuild_fitted(canvas, 90, 50, os.path.join(out, "off.pdf"))
    finally:
        os.environ.pop("VECTOR_REBUILD", None)
    check("rebuild-off", off.get("ok") is False and "switched off" in str(off.get("reason")), str(off.get("reason")))


def test_rebuild_defaults_to_trace() -> None:
    os.environ.pop("VECTOR_FONTS", None)
    page = _word("ONLY", (18, 16, 14), (248, 244, 236), 70)
    canvas = np.full((320, 600, 3), (236, 244, 248), np.uint8)
    canvas[60:240, 30:550] = page
    pdf = os.path.join(tempfile.mkdtemp(prefix="trace-rebuild-"), "press.pdf")
    result = rebuild_fitted(canvas, 90, 50, pdf, blocks=[_block("ONLY", 30 / 600, 60 / 320, 520 / 600, 180 / 320)])
    check("default-mode", result.get("ok") is True and result.get("mode") == "trace", str(result.get("reason")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    fonts = doc[0].get_fonts()
    doc.close()
    check("default-no-font", fonts == [], str(fonts)[:120])
    # Colour sampled from the stroke, not the paper.
    colour = ink_colour(page, segment_ink(page)[0])
    check("sample-dark", colour is not None and float(np.mean(colour)) < 60, str(colour))


def test_closed_counter_stays_raster() -> None:
    """A 9 whose counter is filled, or a speck outside the line, is not a vector."""
    crop = np.full((96, 96, 3), 235, np.uint8)
    ring = np.zeros((96, 96), np.uint8)
    # Thick ring: filling the counter still covers the stroke, so this is a hole change.
    cv2.circle(ring, (48, 48), 30, 255, 18)
    crop[ring > 0] = (16, 18, 20)
    filled = np.zeros((96, 96), np.uint8)
    cv2.circle(filled, (48, 48), 30, 255, -1)
    inner = (12, 12, 84, 84)
    closed = shape_gate(crop, ring, filled, inner)
    check("counter-closed", "counter" in closed, closed)
    kept = shape_gate(crop, ring, ring, inner)
    check("counter-open", kept == "", kept)
    fragment = ring.copy()
    fragment[2:16, 2:22] = 255
    outside = shape_gate(crop, ring, fragment, inner)
    check("fragment-outside", "fragment" in outside or "background" in outside, outside)


def test_glyphs_reject_a_changed_letter() -> None:
    mask = np.zeros((40, 80), np.uint8)
    mask[8:32, 6:18] = 255
    mask[8:32, 28:40] = 255
    same = mask.copy()
    check("glyphs-same", glyphs_agree(mask, same, min_area=12))
    missing = mask.copy()
    missing[:, 28:40] = 0
    check("glyphs-missing", glyphs_agree(mask, missing, min_area=12) is False)
    extra = mask.copy()
    extra[8:32, 55:70] = 255
    check("glyphs-extra", glyphs_agree(mask, extra, min_area=12) is False)


def test_card_back_body_is_traced() -> None:
    """Small card-back lines clear the 4× score. A changed letter still stays raster."""
    os.environ.pop("VECTOR_FONTS", None)
    src = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "medella", "card_back.png")
    bgr = cv2.imread(src)
    pdf = os.path.join(tempfile.mkdtemp(prefix="card-back-trace-"), "press.pdf")
    result = trace_fitted(bgr, 90, 50, pdf)
    check("card-back-trace", result.get("ok") is True and result.get("mode") == "trace", str(result.get("reason")))
    lines = {line["text"]: line for line in result.get("lines") or []}
    wanted = (
        "Helps monitor glucose control",
        "Detects low iron, B12, folate and",
        "strong bones and joints.",
        "your immune response.",
        "INFECTIONS & INFLAMMATION",
    )
    for text in wanted:
        line = lines.get(text)
        check(
            "card-body-" + text[:24],
            line is not None and line.get("mode") == "vector" and float(line.get("match") or 0) >= 0.86,
            str(line),
        )
        print(f"IOU {text} {line.get('match')}")
    check("card-back-count", int(result.get("vector_lines") or 0) == 63, str(result.get("vector_lines")))
    rasters = [line.get("text") for line in result.get("lines") or [] if line.get("mode") == "raster"]
    check("card-back-icons", set(rasters) <= {"+", "中", "♡"}, str(rasters))
    timings = result.get("timings") or {}
    joined = " ".join(result.get("decisions") or [])
    check("card-back-timing", "Timing:" in joined and "colour" in joined, joined[-240:])
    check("card-back-time", float(result.get("elapsed_s") or 999) < 120, str(timings))
    print("TIMING", timings)


def test_paths_and_local_plate() -> None:
    """Windows-safe files, and Lanczos unless Real-ESRGAN is asked for."""
    from PIL import ImageCms

    from host_paths import bundled_sans, esrgan_enabled, find_potrace, icc_file

    check("sans-bundled", os.path.isfile(SANS) and SANS == bundled_sans(True), SANS)
    cmyk = icc_file("default_cmyk.icc")
    srgb = icc_file("srgb.icc")
    check("icc-bundled", "assets" in cmyk.replace("\\", "/") and "assets" in srgb.replace("\\", "/"), cmyk)
    ImageCms.getOpenProfile(cmyk)
    ImageCms.getOpenProfile(srgb)
    folder = tempfile.mkdtemp(prefix="potrace-path-")
    binary = os.path.join(folder, "potrace")
    with open(binary, "w", encoding="utf-8") as handle:
        handle.write("")
    saved_potrace = os.environ.get("POTRACE_PATH")
    saved_esrgan = os.environ.get("VECTOR_ESRGAN")
    saved_skip = os.environ.get("VECTOR_SKIP_ESRGAN")
    try:
        os.environ["POTRACE_PATH"] = folder
        check("potrace-dir", find_potrace() == binary, find_potrace())
        os.environ.pop("VECTOR_ESRGAN", None)
        os.environ.pop("VECTOR_SKIP_ESRGAN", None)
        check("esrgan-off", esrgan_enabled() is False)
        os.environ["VECTOR_ESRGAN"] = "1"
        check("esrgan-on", esrgan_enabled() is True)
        os.environ["VECTOR_SKIP_ESRGAN"] = "1"
        check("esrgan-skip", esrgan_enabled() is False)
    finally:
        if saved_potrace is None:
            os.environ.pop("POTRACE_PATH", None)
        else:
            os.environ["POTRACE_PATH"] = saved_potrace
        if saved_esrgan is None:
            os.environ.pop("VECTOR_ESRGAN", None)
        else:
            os.environ["VECTOR_ESRGAN"] = saved_esrgan
        if saved_skip is None:
            os.environ.pop("VECTOR_SKIP_ESRGAN", None)
        else:
            os.environ["VECTOR_SKIP_ESRGAN"] = saved_skip

    plate = np.zeros((48, 96, 3), np.uint8)
    plate[:, :40] = 30
    plate[:, 40:] = 220
    ink = np.zeros((48, 96), np.uint8)
    ink[8:24, 8:28] = 255
    plate[8:24, 8:28] = (12, 18, 9)
    out = sharpen_background(plate, ink)
    check("sharpen-ink", np.array_equal(out[8:24, 8:28], plate[8:24, 8:28]))
    check("sharpen-paper", int(np.abs(out.astype(np.int16) - plate.astype(np.int16)).sum()) > 0)


def main() -> None:
    test_segment_dark_light_and_fills()
    test_trace_matches_the_ink()
    test_choke_is_only_the_ring()
    test_default_trace_and_font_flag()
    test_rebuild_defaults_to_trace()
    test_closed_counter_stays_raster()
    test_glyphs_reject_a_changed_letter()
    test_paths_and_local_plate()
    test_card_back_body_is_traced()
    print("ALL PASS")


if __name__ == "__main__":
    main()
