#!/usr/bin/env python3
"""Retype checks. A line is set only when the press glyphs match the source."""

from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vector_retype import (
    FAMILIES,
    RETYPE_BUDGET_S,
    _align_words,
    _apply_source_marks,
    _ink_mask,
    _retarget_marks,
    _classify_mark,
    _exact,
    _fill_text_hole,
    _complete_groups,
    _ink_cmyk,
    _render_layout,
    _stroke_ok,
    _style_blocks,
    _match_word_widths,
    choose_font,
    fonts_embedded,
    retype_rejected,
)

ART = os.environ.get("QUICK_PRINT_ARTIFACTS", "/opt/cursor/artifacts")
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts", "v2")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _font(name: str, size: int):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def _stamp_face(text: str, key: str, size: int, width: int, height: int) -> np.ndarray:
    """A line drawn by the press rasterizer, in the source's dark green."""
    mask, _baseline, _chars = _render_layout(key, text, float(size), 0.0)
    canvas = np.full((height, width, 3), (220, 236, 244), np.uint8)
    if mask is None:
        return canvas
    copy_h = min(mask.shape[0], height - 4)
    copy_w = min(mask.shape[1], width - 8)
    ink = mask[:copy_h, :copy_w] > 0
    view = canvas[2:2 + copy_h, 4:4 + copy_w]
    view[ink] = (8, 28, 12)
    return canvas


def _draw_line(text: str, face: str, size: int = 28, width: int = 640, height: int = 72):
    image = Image.new("RGB", (width, height), (244, 236, 220))
    draw = ImageDraw.Draw(image)
    draw.text((16, 12), text, font=_font(face, size), fill=(28, 26, 24))
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _job(plate: np.ndarray, text: str, y: int, height: int, score: float = 0.97):
    h, w = plate.shape[:2]
    block = {
        "text": text,
        "score": score,
        "bbox": [8 / w, y / h, (w - 16) / w, height / h],
    }
    box = {
        "text": text,
        "rect": (4, y, w - 8, height),
        "core": (8, y + 4, w - 20, height - 8),
    }
    line = {"text": text, "mode": "raster", "font": "", "reason": "The trace did not match the ink.", "match": None, "pt": 0}
    placed = {"map": lambda x, y: (float(x), float(y)), "ppi": 400}
    return block, box, line, placed


def test_align_words() -> None:
    check("align-words", _align_words([("SKIN", 0.99), ("CONDITIONS", 0.98)], ["SKIN", "CONDITIONS"]))
    check("align-pieces", _align_words([("CON", 0.95), ("DITIONS", 0.96)], ["CONDITIONS"]))
    check("align-comma", not _align_words([("infections", 0.99)], ["infections,"]))
    check("align-changed", not _align_words([("PREVENTIC", 0.99)], ["PREVENTION"]))
    check("align-case", not _align_words([("Skin", 0.99)], ["SKIN"]))
    check("align-extra", not _align_words([("SKIN", 0.99), ("X", 0.99)], ["SKIN"]))


def test_exact() -> None:
    check("exact-case", _exact("Hello", "Hello") and not _exact("Hello", "hello"))
    check("exact-punct", _exact("health,", "health,") and not _exact("health,", "health"))
    check("exact-space-punct", _exact("health ,", "health,"))
    check("exact-accent", _exact("café", "café") and not _exact("café", "cafe"))
    check("exact-apostrophe", not _exact("body's", "body\u2019s"))
    check("exact-quote", not _exact("\u201cHi\u201d", '"Hi"'))
    check("exact-dash", not _exact("one - two", "one \u2014 two"))


def test_retype_keeps_neighbour_and_embeds() -> None:
    line = _draw_line("Skin conditions respond", "CrimsonText-Regular.ttf", 26, 520, 64)
    neighbour = _draw_line("BY IDENTIFYING", "Cinzel-600.ttf", 22, 520, 56)
    plate = np.full((140, 520, 3), (244, 236, 220), np.uint8)
    plate[8:72] = line
    plate[78:134] = neighbour
    block, box, record, placed = _job(plate, "Skin conditions respond", 8, 64)
    other = {
        "text": "BY IDENTIFYING",
        "score": 0.99,
        "bbox": [8 / 520, 82 / 140, 500 / 520, 48 / 140],
    }
    # The neighbour is not a rejected box, so it must not be painted or retyped.
    before = plate[78:134].copy()
    gate = [{"text": "Skin conditions respond", "mode": "raster", "render": "Skin conditions respond", "ok": True}]
    items = retype_rejected(
        plate, plate.copy(), [block, other], [record], [box], [], placed, plate.shape, gate,
    )
    check("retyped-one", len(items) == 1, str(len(items)))
    if not items:
        return
    check("retyped-font-family", items[0]["font"] in FAMILIES["serif"] or items[0]["font"] in FAMILIES["italic"], items[0]["font"])
    check("retyped-text", items[0]["text"] == "Skin conditions respond")
    check("gate-marked", gate[0].get("mode") == "vector" and gate[0].get("retyped") is True and gate[0].get("font"))
    delta = np.abs(plate[78:134].astype(np.int16) - before.astype(np.int16))
    check("neighbour-untouched", int(delta.max()) == 0, str(int(delta.max())))
    # The source letters are gone. The paper is what is left until the PDF draws the type.
    gray = cv2.cvtColor(plate[12:68, 16:500], cv2.COLOR_BGR2GRAY)
    check("letters-painted-out", float(gray.mean()) > 200, f"{float(gray.mean()):.1f}")

    import pymupdf as fitz

    from vector_retype import paint_retyped

    folder = tempfile.mkdtemp(prefix="retype-pdf-")
    path = os.path.join(folder, "line.pdf")
    doc = fitz.open()
    page = doc.new_page(width=520, height=140)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    paint_retyped(page, items, 1.0, 1.0)
    doc.subset_fonts()
    doc.save(path)
    doc.close()
    opened = fitz.open(path)
    text = opened[0].get_text("text")
    opened.close()
    check("pdf-live-text", "Skin conditions respond" in text, text[:180])
    check("pdf-font-embedded", fonts_embedded(path))


def test_disagreement_stays() -> None:
    plate = _draw_line("Skin conditions respond", "CrimsonText-Regular.ttf", 26, 520, 64)
    block, box, record, placed = _job(plate, "Skin conditions respondX", 0, 64)
    before = plate.copy()
    items = retype_rejected(plate, before, [block], [record], [box], [], placed, plate.shape, None)
    check("mismatch-kept", items == [] and record["mode"] == "raster", str(record))
    check("mismatch-pixels", int(np.abs(plate.astype(np.int16) - before.astype(np.int16)).max()) == 0)


def test_low_confidence_stays() -> None:
    plate = _draw_line("Guides you forward", "LibreBaskerville-Regular.ttf", 24, 480, 60)
    block, box, record, placed = _job(plate, "Guides you forward", 0, 60, score=0.42)
    items = retype_rejected(plate, plate.copy(), [block], [record], [box], [], placed, plate.shape, None)
    check("low-score-kept", items == [], str(len(items)))


def test_one_face_per_block_and_the_paint() -> None:
    """A column keeps one serif. Caps stay a heavy serif. The old letters are gone."""
    check("budget", RETYPE_BUDGET_S <= 3.0, str(RETYPE_BUDGET_S))
    body = "Supports natural balance"
    body2 = "Guides you through care"
    caps = "SKIN CONDITIONS"
    plate = np.full((240, 640, 3), (220, 236, 244), np.uint8)
    plate[8:72] = _stamp_face(body, "crimson", 26, 640, 64)
    plate[80:144] = _stamp_face(body2, "crimson", 26, 640, 64)
    plate[160:224] = _stamp_face(caps, "crimson-bold", 22, 640, 64)
    blocks = []
    boxes = []
    records = []
    gate = []
    for text, y, height in ((body, 8, 64), (body2, 80, 64), (caps, 160, 64)):
        block, box, record, _placed = _job(plate, text, y, height)
        blocks.append(block)
        boxes.append(box)
        records.append(record)
        gate.append({"text": text, "mode": "raster", "render": text, "ok": True})
    _block, _box, _record, placed = _job(plate, body, 8, 64)
    before = plate.copy()
    items = retype_rejected(plate, before, blocks, records, boxes, [], placed, plate.shape, gate)
    fonts = {item["text"]: item["font"] for item in items}
    check("block-both-body", body in fonts and body2 in fonts, str(fonts))
    check("block-one-face", fonts.get(body) == fonts.get(body2) and fonts.get(body) in FAMILIES["serif"], str(fonts))
    check(
        "caps-not-sans",
        fonts.get(caps, "crimson-bold") in ("crimson-bold", "crimson-semibold"),
        str(fonts),
    )
    gray = cv2.cvtColor(plate[16:68, 20:600], cv2.COLOR_BGR2GRAY)
    check("no-ghost", float(np.percentile(gray, 5)) > 175, f"{float(np.percentile(gray, 5)):.1f}")


def test_families_differ() -> None:
    serif = _draw_line("natural balance", "LibreBaskerville-Regular.ttf", 32, 420, 70)
    sans_mask, _baseline, _chars = _render_layout("montserrat", "NATURAL BALANCE", 32, 0.0)
    serif_mask = _mask(serif)
    serif_key, serif_score = choose_font(serif_mask, "natural balance")
    sans_key, sans_score = choose_font(sans_mask, "NATURAL BALANCE")
    check("serif-chosen", serif_key in FAMILIES["serif"] or serif_key in FAMILIES["italic"], f"{serif_key} {serif_score:.2f}")
    check("sans-or-display", sans_key in FAMILIES["sans"] or sans_key in FAMILIES["display"], f"{sans_key} {sans_score:.2f}")
    check("not-one-face", serif_key != sans_key, f"{serif_key} vs {sans_key}")


def _mask(bgr: np.ndarray) -> np.ndarray:
    from vector_retype import _ink_mask

    mask = _ink_mask(bgr)
    if mask is not None:
        return mask
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return ((gray < 160).astype(np.uint8)) * 255


def test_source_glyph_and_core_and_paper() -> None:
    curly = (np.array([
        [0, 1, 1, 1],
        [0, 0, 1, 1],
        [0, 0, 1, 0],
        [0, 1, 0, 0],
    ], np.uint8) * 255)
    straight = (np.array([
        [0, 1, 0],
        [0, 1, 0],
        [0, 1, 0],
        [0, 1, 0],
        [0, 1, 0],
    ], np.uint8) * 255)
    check("mark-curly", _classify_mark(curly) == "curly", _classify_mark(curly))
    check("mark-straight", _classify_mark(straight) == "straight", _classify_mark(straight))
    mask = np.zeros((36, 120), np.uint8)
    mask[8:28, 8:18] = 255
    mask[8:28, 24:36] = 255
    mask[8:28, 42:54] = 255
    mask[8:28, 70:82] = 255
    mask[4:8, 58:62] = curly
    item = {"text": "body's", "mask": mask, "words": ["body's"]}
    _apply_source_marks(item)
    check("source-apostrophe", item["text"] == "body\u2019s", item["text"])
    card = cv2.imread(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "medella", "card_back.png"))
    boosts = _ink_mask(card[312:345, 158:476])
    rewritten = _retarget_marks(boosts, "Boosts your body's natural defences")
    check("card-apostrophe", "body\u2019s" in rewritten and "'" not in rewritten, rewritten)
    split = np.zeros((28, 80), np.uint8)
    split[6:22, 8:16] = 255
    split[6:22, 22:34] = 255
    split[4:7, 40:42] = 255
    split[8:11, 43:45] = 255
    split[6:22, 52:64] = 255
    joined = _retarget_marks(split, "bo's")
    check("split-apostrophe", joined == "bo\u2019s", joined)

    plate = np.full((40, 80, 3), (236, 232, 220), np.uint8)
    plate[12:28, 16:64] = (150, 155, 145)
    plate[16:24, 24:56] = (8, 18, 6)
    ink = np.zeros((40, 80), np.uint8)
    ink[12:28, 16:64] = 255
    cmyk, rgb = _ink_cmyk(plate, (0, 0), ink)
    check("core-not-grey", rgb[0] < 40 and rgb[1] < 50 and rgb[2] < 40, str(rgb))
    check("core-rgb-green", rgb[1] > rgb[0] + 4, str(rgb))
    check("core-not-k", cmyk != (0.0, 0.0, 0.0, 1.0), str(cmyk))

    paper = np.random.default_rng(2).integers(228, 242, (70, 140, 3), dtype=np.uint8)
    hole = np.zeros((70, 140), np.uint8)
    hole[24:42, 18:110] = 255
    dirty = paper.copy()
    dirty[hole > 0] = (150, 150, 148)
    filled, ok = _fill_text_hole(dirty, hole)
    check("paper-kept", ok, "fill rejected")
    if ok:
        grey = cv2.cvtColor(filled, cv2.COLOR_BGR2GRAY)
        ring = np.zeros(hole.shape, np.uint8)
        ring[16:50, 8:124] = 255
        ring[hole > 0] = 0
        diff = abs(float(grey[hole > 0].mean()) - float(grey[ring > 0].mean()))
        check("paper-mean", diff <= 3.0, f"{diff:.2f}")
    blocked = np.full((12, 12, 3), 180, np.uint8)
    _filled, closed = _fill_text_hole(blocked, np.full((12, 12), 255, np.uint8))
    check("paper-no-ring", closed is False)

    source = np.zeros((24, 80), np.uint8)
    source[4:20, 8:40] = 255
    rendered = np.zeros_like(source)
    rendered[4:20, 9:40] = 255
    wide = _match_word_widths(source, rendered, "Word", [("W", 10), ("o", 18), ("r", 26), ("d", 32)], 16.0, "crimson")
    check("width-within", wide is not None and wide[2][0]["scale"] <= 1.05, str(None if wide is None else wide[2][0]["scale"]))
    narrow = np.zeros_like(source)
    narrow[4:20, 14:28] = 255
    too_far = _match_word_widths(source, narrow, "Word", [("W", 14), ("o", 18), ("r", 22), ("d", 26)], 16.0, "crimson")
    check("width-capped", too_far is None)
    # One short word with a noisy span does not throw away a line that otherwise fits.
    both = np.zeros((24, 180), np.uint8)
    both[4:20, 8:78] = 255
    both[4:20, 100:112] = 255
    drawn = np.zeros_like(both)
    drawn[4:20, 8:76] = 255
    drawn[4:20, 96:124] = 255
    kept = _match_word_widths(
        both, drawn, "Longer it",
        [("L", 10), ("o", 22), ("n", 34), ("g", 46), ("e", 58), ("r", 68), (" ", 80), ("i", 100), ("t", 108)],
        16.0, "crimson",
    )
    check("width-short-word", kept is not None and kept[2][0]["scale"] <= 1.05, str(None if kept is None else [run["scale"] for run in kept[2]]))


def _bars(height: int, thick: int) -> np.ndarray:
    mask = np.zeros((height, 100), np.uint8)
    for index in range(5):
        x = 8 + index * 18
        mask[2:height - 2, x:x + thick] = 255
    return mask


def test_stroke_and_groups() -> None:
    thin = _bars(28, 2)
    thick = _bars(28, 5)
    check("stroke-same", _stroke_ok(thin, thin))
    check("stroke-bold", _stroke_ok(thin, thick) is False)

    def item(text, x, y, height):
        return {"text": text, "core": (x, y, 80, height), "ink_box": (0, 0, 70, height)}

    groups = _style_blocks([
        item("IMMUNE SYSTEM", 12, 10, 16),
        item("Boosts your body", 12, 36, 18),
        item("and helps you fight", 12, 60, 18),
        item("HEART HEALTH", 12, 110, 16),
        item("Other column line", 420, 36, 18),
    ])
    texts = [sorted(row["text"] for row in group) for group in groups]
    check(
        "headings-together",
        any(set(group) == {"IMMUNE SYSTEM", "HEART HEALTH"} for group in texts),
        str(texts),
    )
    check(
        "body-together",
        any(set(group) == {"Boosts your body", "and helps you fight"} for group in texts),
        str(texts),
    )
    check(
        "other-column",
        any(group == ["Other column line"] for group in texts),
        str(texts),
    )
    partial = _complete_groups([
        {"group_id": 1, "group_size": 2, "text": "Boosts"},
    ])
    whole = _complete_groups([
        {"group_id": 1, "group_size": 2, "text": "Boosts"},
        {"group_id": 1, "group_size": 2, "text": "and helps"},
    ])
    check("group-partial", partial == [])
    check("group-whole", len(whole) == 2)
    regular = "Supports natural balance"
    heavy = "Guides you through care"
    plate = np.full((160, 640, 3), (220, 236, 244), np.uint8)
    plate[8:72] = _stamp_face(regular, "crimson", 26, 640, 64)
    plate[80:144] = _stamp_face(heavy, "crimson-bold", 26, 640, 64)
    blocks, boxes, records, gate = [], [], [], []
    for text, y in ((regular, 8), (heavy, 80)):
        block, box, record, _placed = _job(plate, text, y, 64)
        blocks.append(block)
        boxes.append(box)
        records.append(record)
        gate.append({"text": text, "mode": "raster", "render": text, "ok": True})
    _block, _box, _record, placed = _job(plate, regular, 8, 64)
    mixed = retype_rejected(plate, plate.copy(), blocks, records, boxes, [], placed, plate.shape, gate)
    check("mixed-weight-stays", mixed == [], str([item["text"] for item in mixed]))


def main() -> None:
    test_exact()
    test_source_glyph_and_core_and_paper()
    test_align_words()
    test_low_confidence_stays()
    test_disagreement_stays()
    test_families_differ()
    test_one_face_per_block_and_the_paint()
    test_stroke_and_groups()
    test_retype_keeps_neighbour_and_embeds()
    print("RETYPE CHECKS PASSED")


if __name__ == "__main__":
    main()
