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
    drop_solid_blobs,
    _reads_match,
    glyphs_agree,
    harmonise_pending,
    _fill_from_paper,
    _glyph_structure_fails,
    _letter_erase_mask,
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
    _components,
    _edge_clipped,
    _glyph_iou,
    _novel_white_block,
    _readings_for_boxes,
    _ssim_luma,
    _texts_equal,
    _vector_glyphs_disagree,
    _trace_fill,
    _expand_rect,
    _topology_fails,
    _hole_count,
    _min_glyph_iou,
    _paint_halo_fails,
    _ghost_double_fails,
    _double_edge_fails,
    _short_faint_fails,
    _small_glyph_fails,
    _small_reads_differ,
    ABOVE_FRAC,
    BELOW_FRAC,
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
    traced = [
        row for row in (low.get("lines") or [])
        if row.get("mode") == "vector" and not row.get("retyped")
    ]
    retyped = [row for row in (low.get("lines") or []) if row.get("retyped")]
    check("iou-gate", low.get("ok") is True and not traced, str(low.get("lines")))
    if retyped:
        check("iou-retype", retyped[0].get("text") == "only" and bool(retyped[0].get("font")), str(retyped))
    else:
        check("iou-raster", low["lines"] and low["lines"][0]["mode"] == "raster", str(low.get("lines")))
    doc = fitz.open(missed)
    missed_drawings = doc[0].get_drawings()
    missed_text = doc[0].get_text("text") or ""
    doc.close()
    if retyped:
        check("iou-retype-live", "only" in missed_text, missed_text[:80])
    else:
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


def test_solid_logo_is_not_traced() -> None:
    """A tall solid mark in a text line is the flame beside CHURCH, not a letter."""
    mask = np.zeros((90, 420), np.uint8)
    for x in range(8, 280, 26):
        mask[30:58, x:x + 14] = 255
    mask[4:86, 340:400] = 255
    cleaned = drop_solid_blobs(mask)
    check("logo-dropped", int(cleaned[:, 340:400].max()) == 0, str(int(cleaned[:, 340:400].sum())))
    check("letters-kept", int(cleaned[:, :280].sum()) == int(mask[:, :280].sum()))
    even = np.zeros((40, 200), np.uint8)
    for x in range(4, 180, 20):
        even[8:32, x:x + 12] = 255
    check("even-letters-kept", np.array_equal(drop_solid_blobs(even), even))

    crop = np.full((90, 420, 3), 28, np.uint8)
    crop[mask > 0] = (250, 250, 250)
    reason = shape_gate(crop, mask, mask, (0, 0, 420, 90))
    check("solid-block-rejected", "solid block" in reason, reason)
    letters = mask.copy()
    letters[:, 320:] = 0
    check("letters-pass-shape", shape_gate(crop, letters, letters, (0, 0, 320, 90)) == "")


def test_letter_welded_to_a_logo_is_kept() -> None:
    """The H touching the flame stays. The flame does not."""
    mask = np.zeros((90, 420), np.uint8)
    gray = np.full((90, 420), 30, np.uint8)
    for x in range(8, 280, 26):
        mask[30:58, x:x + 14] = 255
        gray[30:58, x:x + 14] = 245
    mask[4:86, 340:400] = 255
    gray[4:86, 340:400] = 140
    mask[30:58, 328:342] = 255
    gray[30:58, 328:342] = 245
    cleaned = drop_solid_blobs(mask, gray)
    check("flame-stays-out", int(cleaned[4:22, 360:398].max()) == 0, str(int(cleaned[4:22, 360:398].sum())))
    check("welded-letter-kept", int(cleaned[34:54, 328:340].max()) == 255)


def _paint_t(mask: np.ndarray, x: int, y: int, spur: bool = False) -> None:
    mask[y:y + 6, x:x + 18] = 255
    mask[y:y + 28, x + 7:x + 11] = 255
    if spur:
        mask[y - 6:y + 1, x + 8:x + 10] = 255
        mask[y + 4:y + 16, x + 16:x + 22] = 255


def test_a_bad_glyph_is_rebuilt_from_its_sibling() -> None:
    """A spurred T becomes the clean T. A word that cannot be repaired is all raster."""
    mask = np.zeros((52, 240), np.uint8)
    _paint_t(mask, 6, 12)
    mask[12:40, 40:46] = 255
    mask[12:40, 56:62] = 255
    mask[24:30, 40:62] = 255
    mask[12:40, 78:96] = 255
    mask[12:18, 78:94] = 255
    mask[24:30, 78:92] = 255
    mask[34:40, 78:94] = 255
    _paint_t(mask, 120, 8, spur=True)
    item = {"text": "THET", "mask": mask, "clear": np.zeros_like(mask)}
    harmonise_pending([item])
    parts = _components(item["mask"], 20)
    check("sibling-count", len(parts) == 4, str(len(parts)))
    if len(parts) == 4:
        check("sibling-t", _glyph_iou(parts[0]["pixels"], parts[3]["pixels"]) >= 0.9, f"{_glyph_iou(parts[0]['pixels'], parts[3]['pixels']):.3f}")
        check("sibling-height", abs(parts[0]["h"] - parts[3]["h"]) <= 2, f"{parts[0]['h']} {parts[3]['h']}")
    check("spur-cleared", int(item["clear"].max()) > 0)

    broken = np.zeros((80, 280), np.uint8)
    for x in (4, 18, 32, 46, 100, 114, 142):
        broken[20:44, x:x + 12] = 255
    broken[2:70, 128:140] = 255
    word = {"text": "ABCDEFGH", "mask": broken, "clear": np.zeros_like(broken)}
    harmonise_pending([word])
    check("broken-line-raster", int(word["mask"].max()) == 0, "a line with one broken word stayed partly traced")


def test_vector_line_flags_a_soft_glyph() -> None:
    """A blurred letter beside solid ones fails. Matching letters, and small type, do not."""
    solid = np.full((48, 230, 3), 24, np.uint8)
    for x in range(8, 190, 36):
        solid[8:36, x:x + 18] = 250
    check("even-glyphs", _vector_glyphs_disagree(solid, "AAAAA") is False)
    mixed = solid.copy()
    soft = np.full_like(solid, 24)
    soft[8:36, 188:206] = 250
    soft = cv2.GaussianBlur(soft, (0, 0), 2.8)
    mixed[:, 180:] = soft[:, 180:]
    check("soft-glyph", _vector_glyphs_disagree(mixed, "AAAAAA") is True)
    tiny = np.full((16, 90, 3), 24, np.uint8)
    for x in range(2, 80, 16):
        tiny[4:12, x:x + 8] = 250
    check("small-type-skipped", _vector_glyphs_disagree(tiny, "AAAAA") is False)


def test_gate_is_exact_and_sees_a_white_block() -> None:
    """CHURC is not CHURCH. A new white rectangle is a failed box. A halo is not."""
    full = "GREATER HARVEST FAMILY CHURCH"
    check("exact-church", _texts_equal(full, "GREATER  HARVEST\tFAMILY CHURCH"))
    check("exact-rejects-churc", _texts_equal(full, "GREATER HARVEST FAMILY CHURC") is False)
    check("exact-rejects-substring", _texts_equal("CHURCH", full) is False)
    check("exact-rejects-blank", _texts_equal(full, "") is False and _texts_equal("", full) is False)

    source = np.full((70, 280, 3), 36, np.uint8)
    source[20:50, 16:36] = 248
    source[20:50, 48:78] = 248
    render = source.copy()
    render[8:62, 200:258] = 255
    check("white-block", _novel_white_block(source, render) is True)
    check("white-clean", _novel_white_block(source, source.copy()) is False)
    halo = source.copy()
    halo[18:52, 14:38] = 255
    check("white-halo-ok", _novel_white_block(source, halo) is False, "a fatter letter is not a block")
    check("ssim-same", _ssim_luma(source, source.copy()) >= 0.99)
    check("ssim-block", _ssim_luma(source, render) < 0.99)
    clipped = source.copy()
    clipped[18:52, 0:8] = 250
    check("edge-clipped", _edge_clipped(source, clipped) is True)
    check("edge-clean", _edge_clipped(source, source.copy()) is False)
    split = _readings_for_boxes(
        ["OCT", "OCT"],
        [(0, 0, 20, 10), (12, 0, 32, 10)],
        [{"text": "OCT OCT", "bbox": [0, 0, 1, 1]}],
        32, 10,
    )
    check("oct-split", split == ["OCT", "OCT"], str(split))
    swapped = _readings_for_boxes(
        ["9AM"],
        [(0, 0, 30, 12)],
        [{"text": "8AM", "bbox": [0, 0, 1, 1]}],
        30, 12,
    )
    check("nine-not-eight", swapped == ["8AM"] and swapped[0] != "9AM", str(swapped))


def test_merged_or_split_glyphs_fail() -> None:
    """A joined pair and a gap inside a letter are not the source word."""
    source = np.zeros((40, 120), np.uint8)
    source[8:32, 6:18] = 255
    source[8:32, 28:44] = 255
    source[8:32, 54:70] = 255
    check("glyphs-structure-same", _glyph_structure_fails(source, source.copy()) is False)
    merged = source.copy()
    merged[8:32, 18:28] = 255
    check("glyphs-merged", _glyph_structure_fails(source, merged) is True)
    split = source.copy()
    split[8:32, 34:38] = 0
    check("glyphs-split", _glyph_structure_fails(source, split) is True)
    check("case-fatigue", _texts_equal("fatigue or low energy", "fatigue or low energy"))
    check("case-fatigue-rejects-oi", _texts_equal("fatigue or low energy", "fatigue oı loe energy") is False)
    check("case-drop", _texts_equal("A small drop of blood", "a small drop of blood") is False)
    check("case-imbalances", _texts_equal("imbalances", "imbaiances") is False)
    check("case-inflammation", _texts_equal("chronic inflammation", "chronic inflam mation") is False)
    check("case-identifies", _texts_equal("Identifies triggers", "identifies triggers") is False)

    plate = np.full((70, 90, 3), (230, 226, 220), np.uint8)
    cv2.rectangle(plate, (18, 14), (52, 50), (12, 16, 10), -1)
    cv2.rectangle(plate, (30, 26), (40, 38), (230, 226, 220), -1)
    mask = np.zeros((70, 90), np.uint8)
    mask[14:51, 18:53] = 255
    mask[26:39, 30:41] = 0
    erase = _letter_erase_mask(mask)
    check("erase-keeps-counter", int(erase[30:36, 33:38].max()) == 0)
    check("erase-covers-stroke", int(erase[16:24, 20:28].max()) == 255)
    check("erase-covers-fringe", int(erase[10:14, 28:40].max()) == 255)
    filled = _fill_from_paper(plate, erase)
    check("paint-out-stroke", int(filled[18:24, 20:26].max()) > 180, str(int(filled[18, 22].max())))
    check("paint-out-counter", int(np.max(np.abs(filled[30:36, 33:38].astype(int) - plate[30:36, 33:38].astype(int)))) == 0)


def test_descenders_and_counters() -> None:
    """A word box grows past the x-height, and a clipped tail or an open e fails."""
    rect = (20, 100, 80, 40)
    grown = _expand_rect(rect, 400, 500, ())
    check("expand-above", grown[1] <= 100 - int(40 * ABOVE_FRAC), str(grown))
    check("expand-below", grown[1] + grown[3] >= 140 + int(40 * BELOW_FRAC), str(grown))
    below = (20, 152, 80, 40)
    clamped = _expand_rect(rect, 400, 500, (rect, below))
    check("clamp-below", clamped[1] + clamped[3] <= 147, str(clamped))
    check("clamp-still-grows", clamped[1] + clamped[3] > 140, str(clamped))
    beside = (110, 100, 60, 40)
    same = _expand_rect(rect, 400, 500, (rect, beside))
    check("same-line-full-below", same[1] + same[3] >= 140 + int(40 * BELOW_FRAC), str(same))
    # Word boxes overlap. The descender still gets the full 35%, up to the next line's center.
    over = (375, 978, 196, 25)
    nxt = (385, 1000, 175, 25)
    opened = _expand_rect(over, 1024, 1536, (over, nxt))
    want_down = max(int(round(25 * 0.14)), int(round(25 * BELOW_FRAC)))
    check("overlap-grows-below", opened[1] + opened[3] >= 1003 + want_down, str(opened))
    heading = (10, 20, 200, 80)
    tall = _expand_rect(heading, 800, 600, ())
    check("heading-not-35", tall[1] + tall[3] < 100 + int(80 * BELOW_FRAC), str(tall))
    check("heading-still-padded", tall[1] + tall[3] > 100, str(tall))

    paper = (245, 242, 236)
    crop = np.full((70, 50, 3), paper, np.uint8)
    crop[12:58, 8:14] = (18, 16, 14)
    crop[12:18, 8:40] = (18, 16, 14)
    crop[12:36, 34:40] = (18, 16, 14)
    crop[52:58, 8:40] = (18, 16, 14)
    crop[32:36, 8:38] = (150, 146, 140)
    mask, _colour = segment_ink(crop)
    check("thin-bar-mask", mask is not None)
    holes = 0
    if mask is not None:
        count, labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
        for index in range(1, count):
            if int(stats[index, cv2.CC_STAT_AREA]) < 20:
                continue
            holes = max(holes, _hole_count(labels == index))
    check("thin-bar-hole", holes >= 1, str(holes))

    # A gold N on navy keeps the paper channel. Growing that channel shut
    # draws the doubled stem.
    navy = (28, 22, 16)
    gold = (90, 180, 215)
    en = np.full((70, 48, 3), navy, np.uint8)
    en[16:52, 8:14] = gold
    en[16:52, 30:36] = gold
    for step in range(16):
        en[16 + step, 12 + step:16 + step] = gold
    gap_mask, _gap_colour = segment_ink(en)
    check("n-gap-mask", gap_mask is not None)
    if gap_mask is not None:
        channel = gap_mask[28:40, 16:28]
        check("n-gap-stays-open", int(np.count_nonzero(channel == 0)) >= 12, str(int(np.count_nonzero(channel == 0))))
    else:
        check("n-gap-stays-open", False, "no mask")

    def _block_letter(canvas, x, y, colour):
        canvas[y:y + 28, x:x + 6] = colour
        canvas[y:y + 28, x + 16:x + 22] = colour
        canvas[y:y + 6, x:x + 22] = colour

    clean = np.full((60, 80, 3), (236, 232, 226), np.uint8)
    _block_letter(clean, 8, 16, (20, 18, 16))
    _block_letter(clean, 44, 16, (20, 18, 16))
    same = clean.copy()
    check("glyph-iou-match", _min_glyph_iou(clean, same) >= 0.85, f"{_min_glyph_iou(clean, same):.3f}")
    outline = clean.copy()
    ink = np.any(clean.astype(int) < 80, axis=2)
    fringe = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    outline[fringe & ~ink] = (20, 18, 16)
    check("glyph-iou-outline", _min_glyph_iou(clean, outline) >= 0.85, f"{_min_glyph_iou(clean, outline):.3f}")
    barred = clean.copy()
    barred[20:44, 17:21] = (20, 18, 16)
    barred_score = _min_glyph_iou(clean, barred)
    check("glyph-iou-extra-bar", barred_score < 0.85, f"{barred_score:.3f}")
    # The vector's hard edge sits one pixel inside the picture's soft edge.
    inset = np.full_like(clean, (236, 232, 226))
    eroded = cv2.erode(ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    inset[eroded] = (20, 18, 16)
    inset_score = _min_glyph_iou(clean, inset)
    check("glyph-iou-inset", inset_score >= 0.85, f"{inset_score:.3f}")
    # A missing crossbar is a gap, not that one-pixel inset.
    gap = clean.copy()
    gap[16:22, 8:22] = (236, 232, 226)
    gap_score = _min_glyph_iou(clean, gap)
    check("glyph-iou-missing-bar", gap_score < 0.85, f"{gap_score:.3f}")
    # A short bar beside a digit is not a letter. It must not pull the line down.
    mixed = np.full((90, 80, 3), (236, 232, 226), np.uint8)
    _block_letter(mixed, 8, 16, (20, 18, 16))
    _block_letter(mixed, 44, 16, (20, 18, 16))
    mixed[72:78, 8:14] = (20, 18, 16)
    check(
        "glyph-iou-short-bar",
        _min_glyph_iou(mixed, mixed.copy()) >= 0.85,
        f"{_min_glyph_iou(mixed, mixed.copy()):.3f}",
    )
    # Under 12px, one pixel is a fifth of the stroke, so that edge is not judged.
    tiny = np.full((28, 50, 3), (236, 232, 226), np.uint8)
    tiny[8:18, 4:7] = (20, 18, 16)
    tiny[8:11, 4:14] = (20, 18, 16)
    tiny[8:18, 28:31] = (20, 18, 16)
    tiny[8:11, 28:38] = (20, 18, 16)
    thick = tiny.copy()
    thick[8:18, 7:9] = (20, 18, 16)
    tiny_score = _min_glyph_iou(tiny, thick)
    check("glyph-iou-tiny-edge", tiny_score >= 0.85, f"{tiny_score:.3f}")
    # Two pixels clear of the stem is a bar beside the letter, not the hard edge.
    beside = clean.copy()
    beside[20:44, 68:71] = (20, 18, 16)
    beside_score = _min_glyph_iou(clean, beside)
    check("glyph-iou-side-bar", beside_score < 0.85, f"{beside_score:.3f}")

    photo = np.random.default_rng(1).integers(70, 160, (52, 90), np.uint8)
    halo_src = cv2.cvtColor(photo, cv2.COLOR_GRAY2BGR)
    halo_src[10:42, 24:36] = (248, 248, 248)
    halo_src[10:18, 24:62] = (248, 248, 248)
    halo_ren = halo_src.copy()
    halo_ren[8:44, 20:40] = (96, 96, 96)
    halo_ren[8:20, 20:66] = (96, 96, 96)
    halo_ren[12:40, 26:34] = (248, 248, 248)
    halo_ren[12:18, 26:58] = (248, 248, 248)
    check("halo-on-photo", _paint_halo_fails(halo_src, halo_ren) is True)
    navy = np.full((52, 90, 3), (28, 22, 16), np.uint8)
    gold = navy.copy()
    gold[10:42, 24:36] = (90, 180, 215)
    gold[10:18, 24:62] = (90, 180, 215)
    check("halo-on-navy", _paint_halo_fails(gold, gold.copy()) is False)
    check("halo-same-photo", _paint_halo_fails(halo_src, halo_src.copy()) is False)

    small = np.full((26, 180, 3), (236, 232, 226), np.uint8)
    for x in range(8, 160, 16):
        small[6:18, x:x + 3] = (30, 28, 26)
        small[6:9, x:x + 8] = (30, 28, 26)
    doubled = small.copy()
    shifted = np.full_like(small, (236, 232, 226))
    shifted[:, 4:] = small[:, :-4]
    ink = np.any(shifted.astype(int) < 80, axis=2)
    doubled[ink] = (30, 28, 26)
    check("ghost-double", _ghost_double_fails(small, doubled) is True)
    check("ghost-clean", _ghost_double_fails(small, small.copy()) is False)
    bold = small.copy()
    stem = np.any(small.astype(int) < 80, axis=2).astype(np.uint8)
    bold[cv2.dilate(stem, np.ones((3, 3), np.uint8)) > 0] = (30, 28, 26)
    check("ghost-one-pixel", _ghost_double_fails(small, bold) is False)
    # The card's small type doubles closer than three pixels. The picture's
    # stroke stays, and a second stroke sits just outside it. Headings are taller.
    body = np.full((22, 180, 3), (236, 232, 226), np.uint8)
    for x in range(8, 160, 16):
        body[7:15, x:x + 2] = (30, 28, 26)
        body[7:9, x:x + 7] = (30, 28, 26)
    near = body.copy()
    near_shift = np.full_like(body, (236, 232, 226))
    near_shift[:, 2:] = body[:, :-2]
    near_ink = np.any(near_shift.astype(int) < 80, axis=2)
    near[near_ink] = (30, 28, 26)
    check("double-edge-copy", _double_edge_fails(body, near) is True)
    body_bold = body.copy()
    body_stem = np.any(body.astype(int) < 80, axis=2)
    fringe = cv2.distanceTransform((~body_stem).astype(np.uint8), cv2.DIST_L2, 3)
    # Inside 1.2px is the hard edge. The check starts outside that.
    body_bold[(fringe > 0) & (fringe < 1.15)] = (30, 28, 26)
    check("double-edge-one-pixel", _double_edge_fails(body, body_bold) is False)
    heading = np.full((40, 180, 3), (236, 232, 226), np.uint8)
    for x in range(8, 160, 28):
        heading[6:28, x:x + 4] = (30, 28, 26)
        heading[6:12, x:x + 14] = (30, 28, 26)
    heading_near = heading.copy()
    heading_shift = np.full_like(heading, (236, 232, 226))
    heading_shift[:, 2:] = heading[:, :-2]
    heading_ink = np.any(heading_shift.astype(int) < 80, axis=2)
    heading_near[heading_ink] = (30, 28, 26)
    check("double-edge-heading", _double_edge_fails(heading, heading_near) is False)
    navy_line = np.full((26, 180, 3), (28, 22, 16), np.uint8)
    for x in range(8, 160, 16):
        navy_line[6:18, x:x + 3] = (90, 180, 215)
        navy_line[6:9, x:x + 8] = (90, 180, 215)
    navy_stem = np.max(navy_line.astype(int), axis=2) > 80
    navy_fringe = navy_line.copy()
    navy_fringe[cv2.dilate(navy_stem.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = (120, 200, 230)
    navy_fringe[navy_stem] = navy_line[navy_stem]
    check("double-edge-on-navy", _double_edge_fails(navy_line, navy_fringe) is False)
    faint_line = np.full((22, 160, 3), (236, 232, 226), np.uint8)
    for x in range(6, 150, 14):
        faint_line[7:15, x:x + 2] = (40, 36, 30)
        faint_line[7:9, x:x + 7] = (40, 36, 30)
    check("short-faint-footer", _short_faint_fails(faint_line, 0.89) is True)
    check("short-faint-sharp", _short_faint_fails(faint_line, 0.96) is False)
    tall_line = np.full((48, 180, 3), (236, 232, 226), np.uint8)
    for x in range(8, 160, 28):
        tall_line[8:36, x:x + 6] = (30, 28, 26)
        tall_line[8:14, x:x + 16] = (30, 28, 26)
    check("short-faint-tall", _short_faint_fails(tall_line, 0.89) is False)
    # A short e is under the 12px overlap skip. Losing its bar, or breaking a
    # stroke into two pieces, still has to send the line back. A harder edge
    # that keeps the counter does not.
    def _draw_e(image, x, y, bar=True):
        image[y:y + 12, x:x + 2] = (20, 18, 16)
        image[y:y + 2, x:x + 10] = (20, 18, 16)
        image[y:y + 7, x + 8:x + 10] = (20, 18, 16)
        image[y + 10:y + 12, x:x + 10] = (20, 18, 16)
        if bar:
            image[y + 5:y + 7, x:x + 10] = (20, 18, 16)

    short = np.full((28, 80, 3), (236, 232, 226), np.uint8)
    _draw_e(short, 8, 8, True)
    _draw_e(short, 28, 8, True)
    check("small-e-kept", _small_glyph_fails(short, short.copy()) is False)
    opened = short.copy()
    opened[13:15, 8:36] = (236, 232, 226)
    check("small-e-lost-bar", _small_glyph_fails(short, opened) is True)
    bold_e = short.copy()
    e_ink = np.any(short.astype(int) < 80, axis=2)
    # One pixel outside the stroke, the counter left open.
    fringe = cv2.distanceTransform((~e_ink).astype(np.uint8), cv2.DIST_L2, 3)
    bold_e[(fringe > 0) & (fringe < 1.15) & (fringe > 0)] = (20, 18, 16)
    # The counter sits at rows 10:13. Keep it paper so the harder edge is not a filled bowl.
    bold_e[10:13, 12:16] = (236, 232, 226)
    bold_e[10:13, 32:36] = (236, 232, 226)
    check("small-e-hard-edge", _small_glyph_fails(short, bold_e) is False)
    broken_r = np.full((28, 90, 3), (236, 232, 226), np.uint8)
    broken_r[8:20, 8:10] = (20, 18, 16)
    broken_r[8:20, 20:22] = (20, 18, 16)
    broken_r[8:20, 52:54] = (20, 18, 16)
    broken_r[8:11, 52:60] = (20, 18, 16)
    split_r = broken_r.copy()
    split_r[8:11, 54:60] = (236, 232, 226)
    split_r[14:17, 56:62] = (20, 18, 16)
    check("small-r-split", _small_glyph_fails(broken_r, split_r) is True)
    check("small-r-kept", _small_glyph_fails(broken_r, broken_r.copy()) is False)
    # The empty corner of a wide letter is not a broken stroke. A neighbour
    # that clips that corner, about one pixel off the stroke, stays out.
    corner = np.full((32, 90, 3), (236, 232, 226), np.uint8)
    corner[8:22, 8:12] = (20, 18, 16)
    corner[8:12, 8:32] = (20, 18, 16)
    corner[8:22, 48:52] = (20, 18, 16)
    corner[8:12, 48:70] = (20, 18, 16)
    clipped = corner.copy()
    clipped[13:17, 18:26] = (20, 18, 16)
    check("small-corner-neighbour", _small_glyph_fails(corner, clipped) is False)
    tall_e = np.full((48, 80, 3), (236, 232, 226), np.uint8)
    tall_e[8:36, 8:12] = (20, 18, 16)
    tall_e[8:36, 24:28] = (20, 18, 16)
    tall_e[8:14, 8:28] = (20, 18, 16)
    tall_e[20:26, 8:28] = (20, 18, 16)
    tall_open = tall_e.copy()
    tall_open[20:26, 8:28] = (236, 232, 226)
    check("small-skip-tall", _small_glyph_fails(tall_e, tall_open) is False)
    check("small-read-mismatch", _small_reads_differ([("e", 0.99, "c", 0.80)]) == [0])
    check("small-read-same", _small_reads_differ([("e", 0.99, "e", 0.91)]) == [])
    check("small-read-unsure", _small_reads_differ([("K", 0.40, "N", 0.40)]) == [])
    check("small-read-blank", _small_reads_differ([("e", 0.90, "", 0.0)]) == [])
    check("small-read-stem", _small_reads_differ([("I", 0.99, "1", 0.95)]) == [])
    check("small-read-case", _small_reads_differ([("e", 0.99, "E", 0.92)]) == [])

    wide = np.full((1000, 1700, 3), (230, 226, 220), np.uint8)
    wide[400:460, 200:260] = (16, 14, 12)
    erase = np.zeros(wide.shape[:2], np.uint8)
    erase[400:460, 200:260] = 255
    filled_wide = _fill_from_paper(wide, erase)
    patch = filled_wide[420:440, 220:240]
    check("fast-fill-paper", int(patch.min()) > 180, str(int(patch.min())))

    src = np.full((80, 40, 3), paper, np.uint8)
    src[15:40, 8:28] = (16, 14, 12)
    src[40:70, 14:22] = (16, 14, 12)
    clipped = np.zeros((80, 40), np.uint8)
    clipped[15:40, 8:28] = 255
    check("clipped-tail-fails", _topology_fails(src, clipped, 400) is True)
    whole = clipped.copy()
    whole[40:70, 14:22] = 255
    check("full-tail-passes", _topology_fails(src, whole, 400) is False)

    letter = np.full((60, 40, 3), paper, np.uint8)
    letter[8:52, 6:12] = (16, 14, 12)
    letter[8:14, 6:32] = (16, 14, 12)
    letter[8:34, 26:32] = (16, 14, 12)
    letter[28:34, 6:30] = (16, 14, 12)
    letter[46:52, 6:32] = (16, 14, 12)
    closed = np.zeros((60, 40), np.uint8)
    closed[letter[:, :, 0] < 40] = 255
    opened = closed.copy()
    opened[28:34, 12:30] = 0
    check("open-e-fails", _topology_fails(letter, opened, 400) is True)
    check("closed-e-passes", _topology_fails(letter, closed, 400) is False)

    from vector_trace import ink_touching

    split = np.zeros((70, 40), np.uint8)
    split[12:40, 8:28] = 255
    split[48:66, 14:22] = 255
    kept = ink_touching(split, (4, 8, 36, 44))
    check("tail-kept", kept is not None and int(kept[48:66, 14:22].max()) == 255)
    # Eight pixels of paper is the next line, not a broken join.
    check("paper-gap-not-bridged", kept is not None and int(kept[40:48, 14:22].max()) == 0)
    crack = np.zeros((70, 40), np.uint8)
    crack[12:40, 8:28] = 255
    crack[42:60, 14:22] = 255
    joined = ink_touching(crack, (4, 8, 36, 44))
    check("tail-joined", joined is not None and int(joined[40:42, 14:22].max()) == 255)

    # The tail is its own stroke. The short body must not be read as clipped.
    sibling = clipped.copy()
    sibling[42:70, 14:22] = 255
    check("sibling-tail-passes", _topology_fails(src, sibling, 400) is False)
    # A mark a gap below the word is the next line, not a descender.
    speck = np.full((90, 120, 3), paper, np.uint8)
    speck[20:50, 8:100] = (16, 14, 12)
    speck[68:80, 40:58] = (16, 14, 12)
    body_only = np.zeros((90, 120), np.uint8)
    body_only[20:50, 8:100] = 255
    check("distant-mark-passes", _topology_fails(speck, body_only, 400) is False)
    fringe = np.full((80, 40, 3), paper, np.uint8)
    fringe[15:40, 8:28] = (16, 14, 12)
    fringe[40:42, 18:19] = (16, 14, 12)
    check("fringe-passes", _topology_fails(fringe, clipped, 400) is False)
    # A gray gap between strokes is not a counter. An e's bowl is paper.
    gap = np.full((60, 50, 3), paper, np.uint8)
    gap[8:52, 8:14] = (16, 14, 12)
    gap[8:14, 8:40] = (16, 14, 12)
    gap[8:52, 34:40] = (16, 14, 12)
    gap[46:52, 8:40] = (16, 14, 12)
    gap[16:44, 16:32] = (170, 166, 160)
    filled = np.zeros((60, 50), np.uint8)
    filled[8:52, 8:40] = 255
    check("gray-gap-passes", _topology_fails(gap, filled, 400) is False)


def test_a_traced_line_does_not_enter_the_next_line() -> None:
    """A stroke on the next line's paper is new ink. The letter's own edge is not."""
    from vector_trace import _new_ink_in_band

    source = np.full((40, 80, 3), (236, 232, 226), np.uint8)
    source[18:36, 8:70] = (24, 22, 20)
    # The path covers the letters, plus a 3px stem dropped through the gap above them.
    painted = np.zeros((50, 80), np.uint8)
    painted[18:36, 8:70] = 255
    painted[0:28, 20:23] = 255
    area = _new_ink_in_band(painted, (0, 0), source, (0, 0, 80, 40))
    check("stem-on-the-next-line", area >= 12, str(area))
    flush = np.zeros((40, 80), np.uint8)
    flush[16:36, 6:72] = 255
    edge = _new_ink_in_band(flush, (0, 0), source, (0, 0, 80, 40))
    check("edge-on-the-letters", edge < 12, str(edge))
    # A wide lip of the curve in the gap is not a stem dropped onto the letters.
    lip = np.zeros((40, 80), np.uint8)
    lip[18:36, 8:70] = 255
    lip[12:18, 8:40] = 255
    lip_area = _new_ink_in_band(lip, (0, 0), source, (0, 0, 80, 40))
    check("curve-lip-stays", lip_area < 12, str(lip_area))


def test_a_wide_swash_is_not_traced() -> None:
    """A flourish wider than the letters stays in the picture. A descender does not."""
    from vector_trace import _strip_flourish

    letters = np.zeros((80, 200), np.uint8)
    for x in (10, 40, 70, 100):
        letters[10:40, x:x + 18] = 255
    descender = letters.copy()
    descender[40:58, 44:52] = 255
    kept = _strip_flourish(descender)
    check("descender-stays", int(kept[50, 46]) == 255 and int(kept[20, 16]) == 255)
    swash = letters.copy()
    for x in range(15, 150):
        yy = 52 + int(8 + 7 * np.sin(x / 6.0))
        swash[yy:yy + 2, x:x + 2] = 255
    stripped = _strip_flourish(swash)
    check("swash-not-traced", int(stripped[52:, :].max()) == 0, str(int(stripped[52:].max())))
    check("swash-letters-stay", int(stripped[20, 16]) == 255 and int(stripped[20, 46]) == 255)


def test_swash_bars_are_not_exempt() -> None:
    """A change under the letters, outside the traced stroke, is a leak on every side."""
    from vector_trace import _source_leaks

    ink = np.zeros((70, 80), np.uint8)
    ink[8:28, 8:60] = 255
    plate = np.full((70, 80, 3), 230, np.uint8)
    plate[8:28, 8:60] = (20, 24, 18)
    plate[40:58, 12:68] = (30, 36, 28)
    pristine = plate.copy()
    plate[40:58, 12:68:4] = 250
    item = {"_ink": ink, "origin": (0, 0)}
    leaks = _source_leaks(plate, pristine, [item], (0, 0, 80, 70))
    check("swash-bars-leak", int(leaks[44:54, 12:68].sum()) > 20, str(int(leaks.sum())))
    stroke = pristine.copy()
    stroke[12:24, 12:56] = (80, 80, 80)
    quiet = _source_leaks(stroke, pristine, [item], (0, 0, 80, 70))
    check("stroke-fringe-is-the-only-exemption", int(quiet.sum()) == 0, str(int(quiet.sum())))


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


def test_paint_follows_the_glyph_not_the_box() -> None:
    """A neighbour's ascender is not this line's ink, and a rectangular paint is put back."""
    from vector_trace import _keep_line_glyphs, _repair_source, _source_leaks

    mask = np.zeros((90, 100), np.uint8)
    mask[14:32, 8:48] = 255
    mask[32:46, 18:26] = 255
    mask[42:54, 60:70] = 255
    cores = [
        (4, 10, 80, 24),
        (4, 50, 80, 24),
    ]
    owned = _keep_line_glyphs(mask, 0, 0, 0, cores)
    other = _keep_line_glyphs(mask, 0, 0, 1, cores)
    check("owns-its-descender", owned is not None and int(owned[40, 22]) == 255)
    check("spares-neighbour-ascender", owned is not None and int(owned[48, 64]) == 0)
    check("neighbour-keeps-ascender", other is not None and int(other[48, 64]) == 255)
    check("neighbour-spares-descender", other is not None and int(other[40, 22]) == 0)
    erase = _letter_erase_mask(owned)
    check("erase-not-a-rectangle", int(erase[4, 4]) == 0 and int(erase[20, 20]) == 255)

    plate = np.full((100, 120, 3), 240, np.uint8)
    plate[14:46, 8:48] = (20, 18, 16)
    plate[42:54, 60:70] = (20, 18, 16)
    pristine = plate.copy()
    plate[0:90, 0:100] = (250, 250, 250)
    item = {
        "text": "g",
        "_ink": owned,
        "origin": (0, 0),
        "rect": (0, 0, 100, 90),
        "core": cores[0],
        "paths": [[]],
        "fill": (0, 0, 0, 1),
        "iou": 0.9,
    }
    kept, ok = _repair_source(plate, pristine, [item], [], [], (0, 0, 120, 100))
    leaks = _source_leaks(plate, pristine, kept, (0, 0, 120, 100))
    check("source-guard-repairs", bool(ok) and not bool(leaks.any()), f"ok={ok} leaks={int(leaks.sum())}")
    check("neighbour-ink-restored", np.array_equal(plate[48, 64], pristine[48, 64]), str(plate[48, 64]))
    from vector_trace import _hide_foreign_ink
    paper = (248, 246, 242)
    src = np.full((70, 50, 3), paper, np.uint8)
    src[12:40, 10:22] = (16, 14, 12)
    src[12:18, 10:36] = (16, 14, 12)
    src[34:40, 10:36] = (16, 14, 12)
    src[18:34, 30:36] = (16, 14, 12)
    src[52:66, 14:28] = (16, 14, 12)
    owned = np.zeros((70, 50), np.uint8)
    owned[:40][src[:40, :, 0] < 40] = 255
    cleaned = _hide_foreign_ink(src, owned)
    check("foreign-stroke-cleared", int(cleaned[58, 20, 0]) > 200)
    check("own-counter-stays", int(cleaned[26, 24, 0]) > 200)
    check("own-stroke-stays", int(cleaned[15, 14, 0]) < 40)


def test_light_on_dark_stays_on_its_own_line() -> None:
    """A streak under light type is not the next line, and gray type on navy still counts.

    The same bridge is cut for dark type. The footer strings are the letters
    this was eating: the address, the footer church name, and the school line.
    """
    from vector_trace import _display_ink, _keep_line_glyphs

    mask = np.zeros((90, 80), np.uint8)
    mask[12:36, 8:28] = 255
    mask[12:36, 40:60] = 255
    mask[36:52, 16:19] = 255
    mask[36:48, 48:51] = 255
    mask[52:76, 6:28] = 255
    mask[52:76, 40:62] = 255
    cores = [(4, 10, 70, 28), (4, 50, 70, 28)]
    top = _keep_line_glyphs(mask, 0, 0, 0, cores)
    bottom = _keep_line_glyphs(mask, 0, 0, 1, cores)
    check("footer-bridge-stays-off-the-church-line", top is not None and int(top[64, 16]) == 0)
    check("footer-drip-stays-off-the-church-line", top is not None and int(top[44, 49]) == 0)
    check("footer-church-letter-stays", top is not None and int(top[20, 16]) == 255 and int(top[20, 48]) == 255)
    check(
        "footer-address-keeps-its-own-letter",
        bottom is not None and int(bottom[64, 16]) == 255 and int(bottom[20, 16]) == 0,
    )
    navy = np.full((64, 90, 3), (32, 24, 48), np.uint8)
    cv2.putText(navy, "T", (12, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (250, 250, 250), 2, cv2.LINE_AA)
    light_mask, light_colour = segment_ink(navy)
    check("light-t-separated", light_mask is not None and light_colour is not None and float(light_colour.mean()) > 180)
    if light_mask is not None:
        ys, xs = np.where(light_mask > 0)
        y0, y1 = int(ys.min()), int(ys.max())
        width = max(1, int(xs.max() - xs.min()))
        cap = light_mask[y0:y0 + max(2, (y1 - y0) // 5)] > 0
        check("light-t-bar", int(cap.any(axis=0).sum()) >= int(0.65 * width), f"{int(cap.any(axis=0).sum())} of {width}")
    cream = np.full((64, 90, 3), (236, 232, 224), np.uint8)
    cv2.putText(cream, "T", (12, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 16, 14), 2, cv2.LINE_AA)
    dark_mask, dark_colour = segment_ink(cream)
    check("dark-t-separated", dark_mask is not None and dark_colour is not None and float(dark_colour.mean()) < 80)
    # The footer weld is not a uniform 3px gap. A run of 3px rows is followed
    # by one wider shoulder, and that shoulder used to keep both letters.
    shoulder = np.zeros((80, 40), np.uint8)
    shoulder[4:30, 4:32] = 255
    shoulder[30:31, 14:19] = 255
    shoulder[31:40, 16:19] = 255
    shoulder[40:41, 12:23] = 255
    shoulder[41:68, 6:32] = 255
    bands = [(0, 2, 40, 32), (0, 38, 40, 36)]
    upper = _keep_line_glyphs(shoulder, 0, 0, 0, bands)
    lower = _keep_line_glyphs(shoulder, 0, 0, 1, bands)
    check(
        "shoulder-bridge-leaves-the-upper-letter",
        upper is not None and int(upper[16, 16]) == 255 and int(upper[50, 16]) == 0,
    )
    check(
        "shoulder-bridge-keeps-the-lower-letter",
        lower is not None and int(lower[50, 16]) == 255 and int(lower[16, 16]) == 0,
    )
    gray = np.full((36, 140), 27, np.uint8)
    gray[10:26, 8:120] = 172
    shown = _display_ink(gray)
    check(
        "school-line-gray-counts",
        shown is not None and int((shown > 0).sum()) > 400,
        str(None if shown is None else int((shown > 0).sum())),
    )


def test_a_line_is_not_half_traced() -> None:
    """One raster box pulls its own line. A paragraph drops only when two lines fall back."""
    from vector_trace import _keep_uniform

    drawn = [
        {"text": "Early", "rect": (10, 40, 80, 20), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 40), "iou": 0.95},
        {"text": "detection", "rect": (100, 40, 110, 20), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (100, 40), "iou": 0.95},
        {"text": "Next line", "rect": (10, 66, 140, 18), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 66), "iou": 0.95},
        {"text": "Far heading", "rect": (10, 200, 120, 24), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 200), "iou": 0.95},
    ]
    raster_lines = []
    raster_boxes = [{"text": "issues", "rect": (220, 40, 70, 20), "anchor": True}]
    kept = _keep_uniform(drawn, raster_lines, raster_boxes)
    texts = [item["text"] for item in kept]
    check("line-all-raster", "Early" not in texts and "detection" not in texts, str(texts))
    check("one-fallback-keeps-neighbours", "Next line" in texts, str(texts))
    check("distant-stays", "Far heading" in texts, str(texts))
    check("mixed-note", any("half traced" in str(line.get("reason")) for line in raster_lines))

    column = [
        {"text": "Alpha", "rect": (10, 10, 120, 20), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 10), "iou": 0.95},
        {"text": "Delta", "rect": (10, 84, 120, 20), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 84), "iou": 0.95},
    ]
    two = [
        {"text": "Beta", "rect": (10, 36, 120, 20), "anchor": "paragraph"},
        {"text": "Gamma", "rect": (10, 60, 120, 20), "anchor": "paragraph"},
    ]
    cleared = [item["text"] for item in _keep_uniform(column, [], two)]
    check("two-fallbacks-clear-the-paragraph", cleared == [], str(cleared))
    # Ink that was never separated is not a rejected trace. Two such lines
    # blank only themselves, so the traced neighbours stay vector.
    separated = [
        {"text": "Beta", "rect": (10, 36, 120, 20), "anchor": "line"},
        {"text": "Gamma", "rect": (10, 60, 120, 20), "anchor": "line"},
    ]
    kept_separated = [item["text"] for item in _keep_uniform(column, [], separated)]
    check("two-unseparated-keep-neighbours", kept_separated == ["Alpha", "Delta"], str(kept_separated))

    # A short title must not be blanked by the much taller word sitting under it.
    title = [
        {"text": "CHURCH", "rect": (80, 20, 160, 30), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (80, 20), "iou": 0.95},
    ]
    hero = [{"text": "CATCH", "rect": (10, 40, 300, 140), "anchor": "paragraph"}]
    kept_title = _keep_uniform(title, [], hero)
    check("title-not-pulled", [item["text"] for item in kept_title] == ["CHURCH"], str(kept_title))

    # A box whose ink could not be separated only pulls its own line.
    column = [
        {"text": "9", "rect": (10, 10, 40, 36), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 10), "iou": 0.95},
        {"text": "6PM", "rect": (10, 52, 36, 22), "paths": [[]], "fill": (0, 0, 0, 1), "origin": (10, 52), "iou": 0.95},
    ]
    unread = [{"text": "11AM", "rect": (56, 52, 40, 22), "anchor": "line"}]
    kept_column = _keep_uniform(column, [], unread)
    column_texts = [item["text"] for item in kept_column]
    check("unread-keeps-the-row-above", column_texts == ["9"], str(column_texts))
    from vector_trace import _same_line
    check("tall-word-not-the-column", _same_line((281, 332, 566, 227), (1008, 380, 294, 50)) is False)
    check("words-on-one-line", _same_line((10, 40, 80, 20), (100, 40, 110, 20)) is True)
    # Footer words are short, with a bullet's worth of gap. They are one line,
    # so one rejected trace puts the whole footer back in the picture.
    detect = (579, 779, 67, 18)
    balance = (689, 780, 75, 16)
    heal = (807, 779, 47, 17)
    live = (891, 780, 105, 16)
    check(
        "footer-words-one-line",
        _same_line(detect, balance) and _same_line(balance, heal) and _same_line(heal, live),
    )
    footer = [
        {"text": "DETECT", "rect": detect, "paths": [[]], "fill": (0, 0, 0, 1), "origin": detect[:2], "iou": 0.95},
        {"text": "BALANCE", "rect": balance, "paths": [[]], "fill": (0, 0, 0, 1), "origin": balance[:2], "iou": 0.95},
        {"text": "LIVE BETTER", "rect": live, "paths": [[]], "fill": (0, 0, 0, 1), "origin": live[:2], "iou": 0.95},
    ]
    dropped = [item["text"] for item in _keep_uniform(footer, [], [{"text": "HEAL", "rect": heal, "anchor": "paragraph"}])]
    check("footer-raster-together", dropped == [], str(dropped))


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
    gate = result.get("text_gate") or []
    for text in wanted:
        line = lines.get(text)
        kept = line is not None and line.get("mode") == "vector" and not line.get("retyped") and float(line.get("match") or 0) >= 0.86
        retyped_ok = line is not None and line.get("retyped") and line.get("mode") == "vector" and bool(line.get("font"))
        put_back = line is not None and line.get("mode") == "raster"
        check("card-body-" + text[:24], kept or put_back or retyped_ok, str(line))
        if kept:
            print(f"IOU {text} {line.get('match')}")
    bad = _vector_reads_match(gate)
    check("card-back-letters", not bad, str(bad)[:400])
    # Broken small type goes back to the picture, so the old floor of 40
    # counted damaged letters. The lines that still trace are intact.
    check("card-back-count", int(result.get("vector_lines") or 0) >= 6, str(result.get("vector_lines")))
    check("card-back-source", result.get("source_guard") is True, str(result.get("source_guard")))
    rasters = [line.get("text") for line in result.get("lines") or [] if line.get("mode") == "raster"]
    check("card-back-icons", {"+", "中", "♡"} <= set(rasters), str(rasters))
    timings = result.get("timings") or {}
    joined = " ".join(result.get("decisions") or [])
    check("card-back-timing", "Timing:" in joined and "colour" in joined, joined[-240:])
    check("card-back-time", float(result.get("elapsed_s") or 999) < 120, str(timings))
    print("TIMING", timings)


def _trace_side(name: str, trim_w: float, trim_h: float, workers: str = "2") -> dict:
    """The production path, at a fixed worker count. This is what the press file uses."""
    os.environ["VECTOR_TRACE_WORKERS"] = workers
    from quick_print import make_print_ready

    src = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "medella", f"{name}.png")
    out = tempfile.mkdtemp(prefix=f"medella-{name}-")
    result = make_print_ready(src, out, trim_w, trim_h, "proof", name, filename=f"{name}.png")
    built = result.get("vectorText") or {}
    return {
        "ok": bool(built.get("ok")),
        "vector_lines": built.get("vectorLines") or 0,
        "timings": built.get("timings") or {},
        "elapsed_s": built.get("elapsed_s"),
        "lines": built.get("lines") or [],
        "textGate": built.get("textGate") or [],
        "source_guard": built.get("sourceGuard"),
        "light": result.get("light"),
        "press": result.get("pressPath") or "",
    }


def _vector_reads_match(gate: list) -> list:
    """Vector rows whose painted letters are not the source letters."""
    bad = []
    for row in gate or []:
        if row.get("mode") != "vector":
            continue
        if row.get("glyphFail") or not _reads_match(str(row.get("text") or ""), str(row.get("render") or "")):
            bad.append(row)
    return bad


def _media_box(box_mm, shape, trim_w: float, trim_h: float, bleed: float = 5.0):
    height, width = shape[:2]
    media_w = float(trim_w) + 2.0 * bleed
    media_h = float(trim_h) + 2.0 * bleed
    x = (float(box_mm[0]) + bleed) / media_w * width
    y = (float(box_mm[1]) + bleed) / media_h * height
    w = float(box_mm[2]) / media_w * width
    h = float(box_mm[3]) / media_h * height
    return x, y, w, h


def _render_press(path: str, width: int, height: int) -> np.ndarray:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        zoom_x = float(width) / float(page.rect.width)
        zoom_y = float(height) / float(page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom_x, zoom_y), alpha=False, colorspace=fitz.csRGB)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.close()


def _ink_rows(gray: np.ndarray) -> np.ndarray:
    if gray.size == 0:
        return np.zeros(0, np.int32)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return (binary > 0).sum(axis=1)


def _check_medella_swash(card: dict) -> None:
    """The gap under Medella matches the upscale. The stripe detector runs on it.

    The press plate is built at PRESS_PPI. A 400 PPI plate is a second resample
    of that file, and the gap then differs even when the press picture is intact.
    """
    from vector_plate import PRESS_PPI, place_plate
    from vector_text_v2 import _rect, read_blocks
    from quick_print import vertical_streaks_dominate

    gate = card.get("textGate") or []
    medella = next((row for row in gate if str(row.get("text") or "").strip() == "Medella"), None)
    live = next((row for row in gate if "LIVE BLOOD" in str(row.get("text") or "")), None)
    check("medella-boxes", medella is not None and live is not None and bool(card.get("press")), str(medella)[:180])
    if medella is None or live is None or not card.get("press"):
        return
    src = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "medella", "card_front.png")
    bgr = cv2.imread(src)
    blocks = read_blocks(bgr, extra=False)
    guide = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        if not is_lettering(str(block.get("text") or "")):
            continue
        rx, ry, rw, rh = _rect(block, bgr.shape[1], bgr.shape[0])
        guide.append((rx, ry, rx + rw, ry + rh))
    placed = place_plate(bgr, guide, 90, 50, 5.0, PRESS_PPI)
    clean = placed["image"]
    render = _render_press(card["press"], clean.shape[1], clean.shape[0])
    if render.shape[0] != clean.shape[0] or render.shape[1] != clean.shape[1]:
        render = cv2.resize(render, (clean.shape[1], clean.shape[0]), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
    mx, my, mw, mh = _media_box(medella.get("boxMm") or [0, 0, 0, 0], clean.shape, 90, 50)
    lx, ly, lw, lh = _media_box(live.get("boxMm") or [0, 0, 0, 0], clean.shape, 90, 50)
    x0 = max(0, int(np.floor(mx)))
    x1 = min(clean.shape[1], int(np.ceil(mx + mw)))
    live_y0 = max(0, int(np.floor(ly)))
    live_y1 = min(gray.shape[0], int(np.ceil(ly + lh)))
    live_x0 = max(0, int(np.floor(lx)))
    live_x1 = min(gray.shape[1], int(np.ceil(lx + lw)))
    live_rows = _ink_rows(gray[live_y0:live_y1, live_x0:live_x1])
    strong = int(live_rows.max()) if live_rows.size else 0
    cap_off = 0
    for index, count in enumerate(live_rows):
        if strong and int(count) >= 0.35 * strong:
            cap_off = int(index)
            break
    cap = live_y0 + cap_off
    body_top = max(0, int(np.floor(my)))
    body = _ink_rows(gray[body_top:cap, x0:x1])
    peak = int(body.max()) if body.size else 0
    baseline = body_top
    if peak >= 8:
        # The letter body is the long heavy run. A one-row spike lower down
        # is the swash or the next line, not the baseline.
        heavy = body >= 0.55 * peak
        runs = []
        index = 0
        while index < heavy.size:
            if not heavy[index]:
                index += 1
                continue
            end = index
            while end < heavy.size and heavy[end]:
                end += 1
            runs.append((index, end))
            index = end
        if runs:
            start, end = max(runs, key=lambda run: run[1] - run[0])
            baseline = min(cap, body_top + end + 2)
    y0 = min(cap, baseline)
    y1 = cap
    # The traced stroke's 2px fringe, and the first pixels of the next line,
    # are allowed to differ. The gap between them is not.
    if y1 - y0 > 32:
        y0 += 4
        y1 -= 4
    detail = f"band {x0}:{x1},{y0}:{y1} cap {cap} baseline {baseline}"
    band_h = y1 - y0
    band_w = x1 - x0
    check("medella-band-size", band_h >= 24 and band_w >= 24, detail)
    if band_h < 8 or band_w < 8:
        return
    clean_rgb = cv2.cvtColor(clean[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
    rendered = render[y0:y1, x0:x1]
    delta = np.abs(clean_rgb.astype(np.int16) - rendered.astype(np.int16))
    mean = float(delta.mean())
    hot = float((delta.max(axis=2) > 12).mean())
    streaks = vertical_streaks_dominate(cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR))
    detail = f"{detail} mean {mean:.2f} hot {hot:.4f} streaks {streaks}"
    print("SWASH", detail)
    check("medella-swash-matches", mean <= 6.0 and hot <= 0.02, detail)
    check("medella-swash-not-striped", streaks is False, detail)


def _y_arms(binary: np.ndarray):
    """Arm areas of a Y. None when the glyph is not two arms over one stem."""
    if binary is None or binary.shape[0] < 8 or binary.shape[1] < 5:
        return None
    cut = max(3, int(round(binary.shape[0] * 0.45)))
    top = (binary[:cut] > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(top, 8)
    arms = [int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= 4]
    if len(arms) < 2:
        return None
    foot_h = max(3, int(round(binary.shape[0] * 0.30)))
    foot = (binary[-foot_h:] > 0).astype(np.uint8)
    fcount, _flabels, fstats, _fcent = cv2.connectedComponentsWithStats(foot, 8)
    feet = [index for index in range(1, fcount) if int(fstats[index, cv2.CC_STAT_AREA]) >= 4]
    if len(feet) != 1:
        return None
    return sorted(arms, reverse=True)[:2]


def _check_card_back_raster(side: dict) -> None:
    """A faint footer stays the picture unless the gate accepted it. A traced Y keeps both arms."""
    gate = side.get("textGate") or []
    for word in ("DETECT", "BALANCE", "HEAL", "LIVE BETTER"):
        rows = [row for row in gate if str(row.get("text") or "").strip() == word]
        # A faint footer stays in the picture unless the gate accepted the
        # trace, or a retype read back as the same word. At 600 PPI these
        # words clear that gate (the stroke matches). A rejected trace does not.
        row = rows[0] if len(rows) == 1 else {}
        reads = _reads_match(str(row.get("text") or ""), str(row.get("render") or ""))
        accepted = (
            row.get("mode") == "vector"
            and reads
            and (
                row.get("retyped")
                or (row.get("ok") is True and not row.get("glyphFail"))
            )
        )
        kept = len(rows) == 1 and (row.get("mode") == "raster" or accepted)
        check(
            "footer-" + word.lower().replace(" ", "-") + "-kept",
            kept,
            str(rows)[:300],
        )
    ident = [row for row in gate if "BY IDENTIFYING" in str(row.get("text") or "")]
    check("by-identifying-present", len(ident) == 1, str(len(ident)))
    if len(ident) != 1 or not side.get("press"):
        return
    if ident[0].get("mode") == "raster":
        check("by-identifying-y-matches", True)
    from vector_plate import PRESS_PPI, place_plate
    from vector_text_v2 import _rect, read_blocks

    src_path = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "medella", "card_back.png")
    bgr = cv2.imread(src_path)
    blocks = read_blocks(bgr, extra=False)
    guide = []
    raw = None
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            continue
        rx, ry, rw, rh = _rect(block, bgr.shape[1], bgr.shape[0])
        guide.append((rx, ry, rx + rw, ry + rh))
        if "BY IDENTIFYING" in text:
            raw = (rx, ry, rw, rh)
    check("by-identifying-box", raw is not None)
    if raw is None:
        return
    placed = place_plate(bgr, guide, 90, 50, 5.0, PRESS_PPI)
    clean = cv2.cvtColor(placed["image"], cv2.COLOR_BGR2GRAY)
    render = cv2.cvtColor(_render_press(side["press"], clean.shape[1], clean.shape[0]), cv2.COLOR_RGB2GRAY)
    if render.shape != clean.shape:
        render = cv2.resize(render, (clean.shape[1], clean.shape[0]), interpolation=cv2.INTER_AREA)
    from vector_trace import _mapped_bounds

    left, top, right, bottom = _mapped_bounds(placed["map"], raw, clean.shape[1], clean.shape[0])
    source = clean[top:bottom, left:right]
    painted = render[top:bottom, left:right]
    # The line above used to drop a stem onto these letters. New ink in the
    # gap above them is that stem. The letters' own hard edge is not.
    band = max(4, source.shape[0] // 3)
    src_ink = source[:band] < 160
    near = cv2.dilate(src_ink.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    stray = (painted[:band] < 150) & ~near
    check("by-identifying-no-new-stem", int(np.count_nonzero(stray)) < 12, str(int(np.count_nonzero(stray))))
    if ident[0].get("mode") == "raster":
        return
    _thr, source_bin = cv2.threshold(source, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _thr, paint_bin = cv2.threshold(painted, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(source_bin, 8)
    checked = 0
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_HEIGHT]) < 8 or int(stats[index, cv2.CC_STAT_AREA]) < 20:
            continue
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        src_glyph = source_bin[y:y + height, x:x + width]
        if _y_arms(src_glyph) is None:
            continue
        paint_glyph = paint_bin[y:y + height, x:x + width]
        paint_arms = _y_arms(paint_glyph)
        inter = int(np.count_nonzero((src_glyph > 0) & (paint_glyph > 0)))
        union = int(np.count_nonzero((src_glyph > 0) | (paint_glyph > 0)))
        iou = inter / float(union) if union else 0.0
        checked += 1
        check(
            "by-identifying-y-arms",
            paint_arms is not None and iou >= 0.85,
            f"iou {iou:.2f} arms {paint_arms}",
        )
    check("by-identifying-y-seen", checked >= 2, str(checked))


def _predicted(timings: dict) -> dict:
    """Add 80ms for each potrace start, which is what Windows pays."""
    timings = dict(timings or {})
    extra = 0.08 * int(timings.get("spawns") or 0)
    timings["trace_s"] = round(float(timings.get("trace_s") or 0) + extra, 3)
    timings["total_s"] = round(float(timings.get("total_s") or 0) + extra, 3)
    timings["spawn_s"] = round(extra, 3)
    return timings


def _check_allergies_column(gate: list) -> None:
    """One section, one weight. The heading and its description share a mode."""
    rows = gate or []
    heading = [row for row in rows if "ALLERGIES" in str(row.get("text") or "").upper()]
    body = [row for row in rows if "Identifies triggers" in str(row.get("text") or "")]
    check("allergies-present", bool(heading) and bool(body), str(rows)[:240])
    modes = {row.get("mode") for row in heading + body}
    retyped = [row for row in heading + body if row.get("retyped")]
    if retyped:
        check(
            "allergies-retype-reads",
            all(_reads_match(str(row.get("text") or ""), str(row.get("render") or "")) for row in retyped),
            str(retyped)[:400],
        )
    else:
        check("allergies-one-weight", modes == {"vector"} or modes == {"raster"}, str(heading + body)[:400])


def test_medella_coverage_and_gate_speed() -> None:
    """Two threads. Times include 80ms per potrace start, so they predict the laptop.

    Every side stays within 30s. The letter check stays within 4s.
    """
    phrases = {
        "flyer_front": (
            "fatigue or low energy",
            "A small drop of blood",
            "imbalances",
            "Through",
            "analysis",
            "Supports natural",
            "Guides you to make",
            "Supports long-term",
            "effectiveness",
            "Detox",
            "Anyone who wants to take",
            "your unique health story",
        ),
        "flyer_back": ("chronic inflammation", "Identifies triggers"),
    }
    aliases = {
        "Supports natural": ("Supports natural", "Supts noral"),
    }
    card = _trace_side("card_front", 90, 50)
    check("card-front-count", int(card.get("vector_lines") or 0) >= 9, str(card.get("vector_lines")))
    timings = _predicted(card.get("timings") or {})
    print("PREDICTED card_front", timings)
    check("card-front-gate", float(timings.get("gate_s") or 99) <= 4.0, str(timings))
    check("card-front-total", float(timings.get("total_s") or 99) <= 30.0, str(timings))
    check("card-front-letters", not _vector_reads_match(card.get("textGate")), str(_vector_reads_match(card.get("textGate")))[:400])
    check("card-front-source", card.get("source_guard") is True, str(card.get("source_guard")))
    _check_medella_swash(card)
    back = _trace_side("card_back", 90, 50)
    check("card-back-still", int(back.get("vector_lines") or 0) >= 6, str(back.get("vector_lines")))
    ident = [row for row in (back.get("textGate") or []) if "Identifies triggers" in str(row.get("text") or "")]
    ident_ok = len(ident) == 1 and (
        ident[0].get("mode") == "raster"
        or (
            ident[0].get("retyped")
            and ident[0].get("mode") == "vector"
            and _reads_match(str(ident[0].get("text") or ""), str(ident[0].get("render") or ""))
        )
    )
    check("identifies-triggers-kept", ident_ok, str(ident)[:240])
    back_pred = _predicted(back.get("timings") or {})
    print("PREDICTED card_back", back_pred)
    check("card-back-gate", float(back_pred.get("gate_s") or 99) <= 4.0, str(back_pred))
    check("card-back-total", float(back_pred.get("total_s") or 99) <= 30.0, str(back_pred))
    check("card-back-letters", not _vector_reads_match(back.get("textGate")), str(_vector_reads_match(back.get("textGate")))[:400])
    check("card-back-source-guard", back.get("source_guard") is True, str(back.get("source_guard")))
    _check_card_back_raster(back)
    front = _trace_side("flyer_front", 148, 210)
    front_t = _predicted(front.get("timings") or {})
    print("PREDICTED flyer_front", front_t)
    check("flyer-front-gate", float(front_t.get("gate_s") or 99) <= 4.0, str(front_t))
    check("flyer-front-total", float(front_t.get("total_s") or 99) <= 30.0, str(front_t))
    check("flyer-front-letters", not _vector_reads_match(front.get("textGate")), str(_vector_reads_match(front.get("textGate")))[:500])
    check("flyer-front-source", front.get("source_guard") is True, str(front.get("source_guard")))
    flyer_back = _trace_side("flyer_back", 148, 210)
    back_t = _predicted(flyer_back.get("timings") or {})
    print("PREDICTED flyer_back", back_t)
    check("flyer-back-gate", float(back_t.get("gate_s") or 99) <= 4.0, str(back_t))
    check("flyer-back-total", float(back_t.get("total_s") or 99) <= 30.0, str(back_t))
    _check_allergies_column(flyer_back.get("textGate") or [])
    check("flyer-back-letters", not _vector_reads_match(flyer_back.get("textGate")), str(_vector_reads_match(flyer_back.get("textGate")))[:500])
    check("flyer-back-source", flyer_back.get("source_guard") is True, str(flyer_back.get("source_guard")))
    gates = {"flyer_front": front.get("textGate") or [], "flyer_back": flyer_back.get("textGate") or []}
    for side, wanted in phrases.items():
        blob = "\n".join(str(row.get("text") or "") for row in gates[side])
        for phrase in wanted:
            keys = aliases.get(phrase, (phrase,))
            check("letters-" + phrase[:24], any(key in blob for key in keys), blob[:240])
            hits = [
                row for row in gates[side]
                if any(key in str(row.get("text") or "") for key in keys)
            ]
            wrong = [
                row for row in hits
                if row.get("mode") == "vector" and (
                    row.get("glyphFail")
                    or not _reads_match(str(row.get("text") or ""), str(row.get("render") or ""))
                )
            ]
            check("render-" + phrase[:24], bool(hits) and not wrong, str(hits)[:400])
    print("MEDELLA", {
        "card_front": card.get("vector_lines"),
        "card_back": back.get("vector_lines"),
        "flyer_front": front.get("vector_lines"),
        "flyer_back": flyer_back.get("vector_lines"),
        "timings": {
            "card_front": timings,
            "card_back": back.get("timings"),
            "flyer_front": front_t,
            "flyer_back": back_t,
        },
    })


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


def test_comma_decimal_matches_the_dot_trace() -> None:
    """Windows en-ZA prints potrace %f with a comma. The letters must stay the same size."""
    import re

    import vector_trace as vt

    mask = np.zeros((36, 48), np.uint8)
    mask[4:10, 6:40] = 255
    mask[4:32, 20:28] = 255
    mask[10:22, 22:26] = 0
    binary = vt._trace_binary(mask)
    svg = vt._potrace_svg(binary)
    check("potrace-svg", "scale(" in svg, svg[:180])
    comma = re.sub(r'(?<=\d)\.(?=\d)', ",", svg)
    check("comma-has-no-dot-scale", "scale(0," in comma or "scale(0." not in comma, comma[comma.find("scale"):comma.find("scale") + 40])
    dot_paths = vt._paths_from_svg(svg, binary.shape[0])
    comma_paths = vt._paths_from_svg(comma, binary.shape[0])
    painted = vt.rasterise_paths(dot_paths, binary.shape[1], binary.shape[0])
    other = vt.rasterise_paths(comma_paths, binary.shape[1], binary.shape[0])
    check("comma-same-ink", vt.mask_iou(painted, other) >= 0.99, f"{vt.mask_iou(painted, other):.3f}")
    env = __import__("host_paths", fromlist=["c_numeric_env"]).c_numeric_env({"LC_ALL": "en_ZA.UTF-8", "LC_NUMERIC": "en_ZA.UTF-8", "PATH": "x"})
    check("locale-drops-all", "LC_ALL" not in env and env.get("LC_NUMERIC") == "C", str(env))


def test_env_report_names_the_stack() -> None:
    """Startup can say which OCR and whether scipy is installed."""
    import os
    import re

    from env_report import describe

    line = describe()
    print("ENV", line)
    check("env-ocr", "ocr=rapidocr" in line and "PP-OCRv6_det_small.onnx" in line, line)
    check("env-numpy", "numpy=" in line and "opencv=" in line, line)
    check("env-scipy", "scipy=" in line and "skimage=" in line, line)
    root = os.path.dirname(__file__)
    bad = []
    for name in os.listdir(root):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(root, name), encoding="utf-8").read()
        if re.search(r"^\s*(?:import|from)\s+(?:scipy|skimage)\b", text, re.M):
            bad.append(name)
    check("no-scipy-import", not bad, str(bad))


def test_one_potrace_covers_the_page() -> None:
    """Two lines are one process, and a counter in the first line stays open."""
    import vector_trace as vt

    first = np.zeros((28, 60), np.uint8)
    first[6:22, 8:22] = 255
    first[8:20, 12:18] = 0
    second = np.zeros((28, 70), np.uint8)
    second[6:22, 30:52] = 255
    vt._spawn_count = 0
    many = vt._trace_many([first, second])
    check("one-spawn", vt._spawn_count == 1, str(vt._spawn_count))
    check("batch-has-both", len(many) == 2 and bool(many[0]) and bool(many[1]), str(len(item) for item in many))
    alone = vt.trace_mask(first)
    painted_many = vt.rasterise_paths(many[0], first.shape[1], first.shape[0])
    painted_one = vt.rasterise_paths(alone, first.shape[1], first.shape[0])
    check("batch-matches-one", vt.mask_iou(painted_many, painted_one) >= 0.95, f"{vt.mask_iou(painted_many, painted_one):.3f}")
    check("batch-keeps-counter", int(painted_many[14, 15]) == 0, str(int(painted_many[14, 15])))
    other = vt.rasterise_paths(many[1], second.shape[1], second.shape[0])
    check("batch-second-ink", int(other.max()) > 0)


def test_gate_image_keeps_a_word_whole() -> None:
    """A thin crop is not stretched until its short side is 736, and a page strip stays at that long side."""
    from ocr_reader import _gate_image

    thin = _gate_image(np.full((76, 490, 3), 255, np.uint8))
    check("gate-thin-long", max(thin.shape[:2]) <= 750, str(thin.shape))
    check("gate-thin-not-blown", min(thin.shape[:2]) < 400, str(thin.shape))
    card = _gate_image(np.full((452, 584, 3), 255, np.uint8))
    check("gate-card-readable", min(card.shape[:2]) >= 500 and max(card.shape[:2]) <= 750, str(card.shape))
    page = _gate_image(np.full((1135, 1172, 3), 255, np.uint8))
    check("gate-page-long", max(page.shape[:2]) <= 750, str(page.shape))


def test_column_style_follows_the_majority() -> None:
    """Same size and colour in one column follows whichever mode is in the majority."""
    from vector_trace import _keep_uniform

    ink = (20.0, 30.0, 40.0)
    other = (200.0, 210.0, 220.0)

    def item(text, rect, colour):
        return {
            "text": text,
            "rect": rect,
            "style": colour,
            "paths": [[]],
            "fill": (0, 0, 0, 1),
            "origin": rect[:2],
            "iou": 0.95,
        }

    rasters = [
        {"text": "Boosts immunity", "rect": (400, 80, 160, 18), "anchor": "line", "style": ink},
        {"text": "Supports the gut", "rect": (400, 100, 170, 18), "anchor": "line", "style": ink},
        {"text": "Calms the skin", "rect": (400, 120, 150, 18), "anchor": "line", "style": ink},
    ]
    drawn = [
        item("ALLERGIES & SENSITIVITIES", (400, 140, 180, 18), ink),
        item("Identifies triggers and", (400, 160, 170, 18), ink),
    ]
    kept = [row["text"] for row in _keep_uniform(drawn, [], rasters)]
    check("column-style-raster", kept == [], str(kept))
    gold = [
        item("Heading", (40, 40, 160, 28), other),
        item("Second", (40, 80, 150, 28), other),
    ]
    one = [{"text": "Third", "rect": (40, 120, 140, 28), "anchor": "line", "style": other}]
    stayed = [row["text"] for row in _keep_uniform(gold, [], one)]
    check("column-style-vector-stays", stayed == ["Heading", "Second"], str(stayed))
    heading = [item("INFECTIONS", (400, 40, 180, 36), ink)]
    kept_heading = [row["text"] for row in _keep_uniform(heading, [], rasters)]
    check("different-size-stays", kept_heading == ["INFECTIONS"], str(kept_heading))
    row_vector = [item("Identifies triggers and", (450, 200, 160, 18), ink)]
    row_rasters = [
        {"text": "Helps with bloating", "rect": (40, 200, 170, 18), "anchor": "line", "style": ink},
        {"text": "mood and anxiety", "rect": (240, 200, 170, 18), "anchor": "line", "style": ink},
    ]
    row_kept = [row["text"] for row in _keep_uniform(row_vector, [], row_rasters)]
    check("row-style-raster", row_kept == [], str(row_kept))
    far = [item("Other column", (980, 200, 160, 18), ink)]
    far_kept = [row["text"] for row in _keep_uniform(far, [], row_rasters)]
    check("distant-row-stays", far_kept == ["Other column"], str(far_kept))


def main() -> None:
    test_segment_dark_light_and_fills()
    test_trace_matches_the_ink()
    test_choke_is_only_the_ring()
    test_default_trace_and_font_flag()
    test_rebuild_defaults_to_trace()
    test_closed_counter_stays_raster()
    test_solid_logo_is_not_traced()
    test_letter_welded_to_a_logo_is_kept()
    test_a_bad_glyph_is_rebuilt_from_its_sibling()
    test_vector_line_flags_a_soft_glyph()
    test_gate_is_exact_and_sees_a_white_block()
    test_merged_or_split_glyphs_fail()
    test_descenders_and_counters()
    test_a_traced_line_does_not_enter_the_next_line()
    test_a_wide_swash_is_not_traced()
    test_swash_bars_are_not_exempt()
    test_glyphs_reject_a_changed_letter()
    test_paint_follows_the_glyph_not_the_box()
    test_light_on_dark_stays_on_its_own_line()
    test_a_line_is_not_half_traced()
    test_comma_decimal_matches_the_dot_trace()
    test_env_report_names_the_stack()
    test_one_potrace_covers_the_page()
    test_gate_image_keeps_a_word_whole()
    test_column_style_follows_the_majority()
    test_paths_and_local_plate()
    test_card_back_body_is_traced()
    test_medella_coverage_and_gate_speed()
    print("ALL PASS")


if __name__ == "__main__":
    main()
