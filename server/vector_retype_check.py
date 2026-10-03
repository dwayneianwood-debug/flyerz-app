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
    _exact,
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
    plate = np.full((240, 640, 3), (244, 236, 220), np.uint8)
    plate[8:72] = _draw_line(body, "CrimsonText-Regular.ttf", 26, 640, 64)
    plate[80:144] = _draw_line(body2, "CrimsonText-Regular.ttf", 26, 640, 64)
    plate[160:224] = _draw_line(caps, "CrimsonText-Bold.ttf", 26, 640, 64)
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
    check("caps-not-sans", fonts.get(caps) in ("crimson-bold", "crimson-semibold"), str(fonts))
    gray = cv2.cvtColor(plate[16:68, 20:600], cv2.COLOR_BGR2GRAY)
    check("no-ghost", float(np.percentile(gray, 5)) > 175, f"{float(np.percentile(gray, 5)):.1f}")


def test_families_differ() -> None:
    serif = _draw_line("natural balance", "LibreBaskerville-Regular.ttf", 32, 420, 70)
    sans = _draw_line("NATURAL BALANCE", "Montserrat-700.ttf", 28, 460, 70)
    serif_mask = _mask(serif)
    sans_mask = _mask(sans)
    serif_key, serif_score = choose_font(serif_mask, "natural balance")
    sans_key, sans_score = choose_font(sans_mask, "NATURAL BALANCE")
    check("serif-chosen", serif_key in FAMILIES["serif"] or serif_key in FAMILIES["italic"], f"{serif_key} {serif_score:.2f}")
    check("sans-or-display", sans_key in FAMILIES["sans"] or sans_key in FAMILIES["display"], f"{sans_key} {sans_score:.2f}")
    check("not-one-face", serif_key != sans_key, f"{serif_key} vs {sans_key}")


def _mask(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return ((gray < 180).astype(np.uint8)) * 255


def main() -> None:
    test_exact()
    test_align_words()
    test_low_confidence_stays()
    test_disagreement_stays()
    test_families_differ()
    test_one_face_per_block_and_the_paint()
    test_retype_keeps_neighbour_and_embeds()
    print("RETYPE CHECKS PASSED")


if __name__ == "__main__":
    main()
