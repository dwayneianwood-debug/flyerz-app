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


def test_a_line_is_not_half_traced() -> None:
    """One raster box on a line, or in a stacked paragraph, puts the rest back."""
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
    check("paragraph-follows", "Next line" not in texts, str(texts))
    check("distant-stays", "Far heading" in texts, str(texts))
    check("mixed-note", any("half traced" in str(line.get("reason")) for line in raster_lines))

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
        kept = line is not None and line.get("mode") == "vector" and float(line.get("match") or 0) >= 0.86
        put_back = line is not None and line.get("mode") == "raster"
        check("card-body-" + text[:24], kept or put_back, str(line))
        if kept:
            print(f"IOU {text} {line.get('match')}")
    bad = _vector_reads_match(gate)
    check("card-back-letters", not bad, str(bad)[:400])
    check("card-back-count", int(result.get("vector_lines") or 0) >= 40, str(result.get("vector_lines")))
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
        "light": result.get("light"),
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


def test_medella_coverage_and_gate_speed() -> None:
    """Two threads. The render is read back once per side, and a mismatch stays raster.

    Card sides stay within 15s. Flyer sides stay within 30s. The letter check
    itself may use up to 6s.
    """
    phrases = {
        "flyer_front": ("fatigue or low energy", "A small drop of blood", "imbalances"),
        "flyer_back": ("chronic inflammation", "Identifies triggers"),
    }
    card = _trace_side("card_front", 90, 50)
    check("card-front-count", int(card.get("vector_lines") or 0) >= 9, str(card.get("vector_lines")))
    timings = card.get("timings") or {}
    check("card-front-gate", float(timings.get("gate_s") or 99) < 6.0, str(timings))
    check("card-front-total", float(timings.get("total_s") or 99) <= 15.0, str(timings))
    check("card-front-letters", not _vector_reads_match(card.get("textGate")), str(_vector_reads_match(card.get("textGate")))[:400])
    back = _trace_side("card_back", 90, 50)
    check("card-back-still", int(back.get("vector_lines") or 0) >= 40, str(back.get("vector_lines")))
    check("card-back-gate", float((back.get("timings") or {}).get("gate_s") or 99) < 6.0, str(back.get("timings")))
    check("card-back-total", float((back.get("timings") or {}).get("total_s") or 99) <= 15.0, str(back.get("timings")))
    check("card-back-letters", not _vector_reads_match(back.get("textGate")), str(_vector_reads_match(back.get("textGate")))[:400])
    front = _trace_side("flyer_front", 148, 210)
    front_t = front.get("timings") or {}
    check("flyer-front-gate", float(front_t.get("gate_s") or 99) < 8.0, str(front_t))
    check("flyer-front-total", float(front_t.get("total_s") or 99) <= 30.0, str(front_t))
    check("flyer-front-letters", not _vector_reads_match(front.get("textGate")), str(_vector_reads_match(front.get("textGate")))[:500])
    flyer_back = _trace_side("flyer_back", 148, 210)
    back_t = flyer_back.get("timings") or {}
    check("flyer-back-gate", float(back_t.get("gate_s") or 99) < 8.0, str(back_t))
    check("flyer-back-total", float(back_t.get("total_s") or 99) <= 30.0, str(back_t))
    check("flyer-back-letters", not _vector_reads_match(flyer_back.get("textGate")), str(_vector_reads_match(flyer_back.get("textGate")))[:500])
    gates = {"flyer_front": front.get("textGate") or [], "flyer_back": flyer_back.get("textGate") or []}
    for side, wanted in phrases.items():
        blob = "\n".join(str(row.get("text") or "") for row in gates[side])
        for phrase in wanted:
            check("letters-" + phrase[:24], phrase in blob, blob[:240])
            hits = [row for row in gates[side] if phrase in str(row.get("text") or "")]
            wrong = [
                row for row in hits
                if row.get("mode") == "vector" and not _reads_match(str(row.get("text") or ""), str(row.get("render") or ""))
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
    test_glyphs_reject_a_changed_letter()
    test_a_line_is_not_half_traced()
    test_paths_and_local_plate()
    test_card_back_body_is_traced()
    test_medella_coverage_and_gate_speed()
    print("ALL PASS")


if __name__ == "__main__":
    main()
