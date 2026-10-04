#!/usr/bin/env python3
"""Retype a line the tracer left as a picture.

Only a line the trace rejected is considered. It becomes live type when all
of these hold:

- the page read is confident
- one face is chosen for the whole style block
- the stroke width of that face, measured on the distance-transform core,
  is within 10% of the source at the same scale
- every line in the block fits that face; if one fails, the block stays
  the picture
- every word overlaps the source, including on the press page

Anything unsure stays in the picture. Icons and neighbouring lines are not
painted. The face is embedded in the press PDF.
"""

from __future__ import annotations

import os
import re
import sys
import time

import cv2
import numpy as np


def _dbg(message: str) -> None:
    if os.environ.get("VECTOR_RETYPE_DEBUG"):
        sys.stderr.write("[retype] " + message + "\n")

CONFIDENCE_FLOOR = 0.90
WORD_SCORE_FLOOR = 0.80
FAMILY_FLOOR = 0.12
OVERLAP_FLOOR = 0.34
WORD_OVERLAP_FLOOR = 0.40
CONTRAST_FLOOR = 40.0
MIN_LETTERS = 3
# A slow 4-core laptop has to finish retype inside this, including the press read-back.
RETYPE_BUDGET_S = 3.0
BODY_FACES = ("crimson", "crimson-semibold", "crimson-bold", "libre", "poppins-regular")
CAPS_FACES = ("crimson-bold", "crimson-semibold", "cinzel-700", "montserrat", "cinzel-600")

# One face is not used for every line. Each family is scored, then the
# weights inside the winning family.
FAMILIES = {
    "serif": ("crimson", "crimson-semibold", "crimson-bold", "libre", "cormorant", "eb-semibold"),
    "italic": ("crimson-italic", "eb-italic"),
    "sans": ("poppins-regular", "inter", "poppins", "montserrat"),
    "display": ("cinzel-400", "cinzel-600", "cinzel-700", "marcellus", "cormorant-sc"),
    "script": ("parisienne", "greatvibes", "allura", "pinyon"),
}
REPS = {
    "serif": "crimson",
    "italic": "eb-italic",
    "sans": "poppins-regular",
    "display": "cinzel-600",
    "script": "parisienne",
}
SKIP_REASON = (
    "icon was left",
    "single small glyph",
    "logo lettering",
    "circle stayed",
    "short mark",
    "too small to trace",
)


def retype_enabled() -> bool:
    raw = os.environ.get("VECTOR_RETYPE", "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def retype_rejected(
    plate: np.ndarray,
    pristine: np.ndarray,
    blocks: list,
    raster_lines: list,
    raster_boxes: list,
    drawn: list,
    placed: dict,
    source_shape,
    text_gate: list | None = None,
) -> list:
    """Set rejected lines as type. Paints the plate. Returns PDF text items."""
    if not retype_enabled() or plate is None or pristine is None:
        return []
    started = time.perf_counter()
    deadline = started + RETYPE_BUDGET_S
    candidates = _candidates(blocks, raster_lines, raster_boxes, drawn, placed, source_shape, pristine.shape)
    _dbg(f"candidates {len(candidates)} of {len(raster_boxes or [])} raster boxes")
    if not candidates:
        return []
    prepared = _prepare(pristine, candidates)
    _dbg(f"prepared {len(prepared)} of {len(candidates)}")
    if not prepared:
        return []
    fitted = []
    for index, group in enumerate(_style_blocks(prepared)):
        if time.perf_counter() > deadline:
            _dbg("budget")
            break
        placed = _fit_group(pristine, group, deadline)
        for item in placed:
            item["group_id"] = index
            item["group_size"] = len(group)
        _dbg(f"group {len(placed)}/{len(group)} {group[0]['text'][:40]!r}")
        fitted.extend(placed)
    _dbg(f"fitted {len(fitted)}")
    if not fitted:
        return []
    confirmed = _press_keep(fitted, pristine.shape)
    confirmed = _complete_groups(confirmed)
    _dbg(f"confirmed {len(confirmed)} of {len(fitted)} in {time.perf_counter() - started:.2f}s")
    if not confirmed:
        return []
    confirmed = _paint(plate, confirmed)
    confirmed = _complete_groups(confirmed)
    _dbg(f"painted {len(confirmed)}")
    if not confirmed:
        return []
    _mark(raster_lines, text_gate, confirmed)
    return confirmed


def _exact(left: str, right: str) -> bool:
    """Letter-for-letter, including case, accents and punctuation."""
    def norm(text: str) -> str:
        cleaned = str(text or "").replace("\u00a0", " ").replace("\u2009", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        return cleaned

    a = norm(left)
    b = norm(right)
    return bool(a) and a == b


def _word_exact(read: str, expected: str) -> bool:
    """One word. A space inside the read is a letter the recogniser split.

    A straight quote is not a curly one. An en dash is not a hyphen.
    """
    return _exact(str(read or "").replace(" ", ""), str(expected or "").replace(" ", ""))


_SINGLE_QUOTES = {"'", "\u2018", "\u2019", "\u02bc"}
_DOUBLE_QUOTES = {'"', "\u201c", "\u201d"}
_DASH_CHARS = {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}
WIDTH_SCALE_MIN = 0.95
WIDTH_SCALE_MAX = 1.05
# Stroke width of the rendered face against the source, at the same scale.
STROKE_TOL = 0.10
# The filled hole has to match the paper ring. A visible grey box fails.
PAINT_MEAN_MAX = 3.0
PAINT_STD_MAX = 3.5


def _classify_mark(glyph: np.ndarray) -> str:
    """One ink blob: curly quote, straight quote, or a dash."""
    ys, xs = np.where(glyph > 0)
    if xs.size < 3:
        return "straight"
    tight = glyph[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1] > 0
    height, width = tight.shape
    if width >= max(4, height * 2) and height <= max(3, int(round(width * 0.45))):
        return "dash"
    # A straight quote is a one-pixel hairline. A thicker blob is curly,
    # even when the picture is only a couple of pixels tall.
    if width >= 2 and height >= 2 and height <= width * 3.2:
        return "curly"
    row_widths = [int(row.sum()) for row in tight]
    filled = [w for w in row_widths if w]
    if height >= 3 and width >= 2 and filled and max(filled) >= min(filled) + 1:
        return "curly"
    mid = max(1, height // 2)
    top = tight[:mid]
    bot = tight[mid:]

    def centre(rows: np.ndarray) -> float:
        ys, xs = np.where(rows)
        if xs.size == 0:
            return 0.0
        return float(xs.mean())

    if height >= 3 and width >= 2 and abs(centre(top) - centre(bot)) >= 0.45:
        return "curly"
    return "straight"


def _stem_below(binary: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """An i-dot has the stem under it. An apostrophe does not."""
    y0 = y + h
    y1 = min(binary.shape[0], y0 + max(3, h))
    if y1 <= y0:
        return False
    x0 = max(0, x - 1)
    x1 = min(binary.shape[1], x + w + 1)
    below = binary[y0:y1, x0:x1]
    if below.size == 0:
        return False
    return int(below.sum()) >= max(4, h)


def _dash_char(width: int, letter_w: float) -> str:
    if letter_w <= 1:
        return "-"
    ratio = float(width) / float(letter_w)
    if ratio >= 1.15:
        return "\u2014"
    if ratio >= 0.55:
        return "\u2013"
    return "-"


def _quote_char(kind: str, opening: bool, double: bool) -> str:
    if double:
        if kind == "curly":
            return "\u201c" if opening else "\u201d"
        return '"'
    if kind == "curly":
        return "\u2018" if opening else "\u2019"
    return "'"


def _replacement(ch: str, mark: tuple, typical: float, index: int, text: str) -> str:
    _x, _y, width, _h, kind = mark
    opening = index == 0 or (index > 0 and text[index - 1].isspace())
    if ch in _DASH_CHARS:
        return _dash_char(width, typical * 0.55)
    if ch in _DOUBLE_QUOTES:
        return _quote_char(kind, opening, True)
    if ch in _SINGLE_QUOTES:
        return _quote_char(kind, opening, False)
    return ch


def _retarget_marks(mask: np.ndarray, text: str) -> str:
    """Swap a quote or dash for the glyph the ink actually is."""
    binary = (mask > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    heights = []
    for index in range(1, count):
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if height >= 8 and area >= 12:
            heights.append(height)
    if not heights:
        return text
    typical = float(np.median(heights))
    tops, bots = [], []
    for index in range(1, count):
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if height >= typical * 0.75 and area >= 12:
            top = int(stats[index, cv2.CC_STAT_TOP])
            tops.append(top)
            bots.append(top + height)
    # The cap line, not the x-height. An apostrophe sits on the cap line.
    band_top = float(np.percentile(tops, 5)) if tops else 0.0
    band_bot = float(np.percentile(bots, 80)) if bots else float(mask.shape[0])
    midline = (band_top + band_bot) / 2.0
    marks = []
    for index in range(1, count):
        x, y, width, height, area = [int(stats[index, k]) for k in range(5)]
        if area < 3 or height > typical * 0.72 or width > typical * 0.95:
            continue
        centre_y = y + height / 2.0
        if centre_y < band_top - 4 or centre_y > band_bot + 2:
            continue
        glyph = binary[y:y + height, x:x + width]
        kind = _classify_mark(glyph)
        if kind == "dash":
            # A dash sits on the midline. A comma or a period sits on the baseline.
            if abs(centre_y - midline) > max(3.0, (band_bot - band_top) * 0.28):
                continue
        else:
            # A quote sits in the upper half. Baseline dots are not quotes.
            if centre_y > midline:
                continue
            if _stem_below(binary, x, y, width, height):
                continue
        marks.append((x, y, width, height, kind))
    marks.sort(key=lambda mark: mark[0])
    # A curly apostrophe often breaks into two blobs a pixel apart.
    merged = []
    for mark in marks:
        if merged:
            prev = merged[-1]
            gap = mark[0] - (prev[0] + prev[2])
            if 0 <= gap <= 2 and abs((mark[1] + mark[3] / 2.0) - (prev[1] + prev[3] / 2.0)) <= 5:
                kind = "curly" if "curly" in (prev[4], mark[4]) else prev[4]
                top = min(prev[1], mark[1])
                bottom = max(prev[1] + prev[3], mark[1] + mark[3])
                merged[-1] = (prev[0], top, mark[0] + mark[2] - prev[0], bottom - top, kind)
                continue
        merged.append(mark)
    marks = merged
    indexes = [
        index for index, ch in enumerate(text)
        if ch in _SINGLE_QUOTES or ch in _DOUBLE_QUOTES or ch in _DASH_CHARS
    ]
    if len(marks) != len(indexes) or not indexes:
        _dbg(
            f"marks {len(marks)} pun {len(indexes)} "
            f"{[(m[0], m[2], m[3], m[4]) for m in marks]} {text[:40]!r}"
        )
        return text
    chars = list(text)
    if len(marks) == len(indexes):
        for mark, index in zip(marks, indexes):
            chars[index] = _replacement(chars[index], mark, typical, index, text)
    return "".join(chars)


def _apply_source_marks(item: dict) -> None:
    """Use the quote, dash or apostrophe the source picture shows."""
    text = str(item.get("text") or "")
    mask = item.get("mask")
    if mask is None or not text:
        return
    if not any(ch in _SINGLE_QUOTES or ch in _DOUBLE_QUOTES or ch in _DASH_CHARS for ch in text):
        return
    rewritten = _retarget_marks(mask, text)
    if rewritten and rewritten != text:
        item["ocr_text"] = item.get("ocr_text") or text
        item["text"] = rewritten
        item["words"] = rewritten.split()


def _align_words(detections, words: list[str]) -> bool:
    """Detections spell the expected words and nothing else.

    A word may arrive in pieces. The pieces have to join to that word,
    including its punctuation and case. A dropped comma or a changed letter fails.
    """
    tokens = []
    for item in detections or []:
        text = str(item[0] if item else "")
        try:
            score = float(item[1] if len(item) > 1 else 0)
        except (TypeError, ValueError):
            score = 0.0
        for part in text.split():
            if part:
                tokens.append((part, score))
    if not tokens or not words:
        return False
    index = 0
    for word in words:
        if index >= len(tokens):
            return False
        acc = ""
        score = 1.0
        used = 0
        target = word.replace(" ", "")
        while index + used < len(tokens):
            part, part_score = tokens[index + used]
            compact = acc + part
            if target.startswith(compact):
                acc = compact
                score = min(score, part_score)
                used += 1
                if compact == target:
                    break
                continue
            break
        if used == 0 or not _word_exact(acc, word) or score < WORD_SCORE_FLOOR:
            return False
        index += used
    return index == len(tokens)


def _letters(text: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", str(text or ""))


def _candidates(blocks, raster_lines, raster_boxes, drawn, placed, source_shape, plate_shape) -> list:
    from vector_text_v2 import _rect
    from vector_trace import _mapped_bounds

    height, width = int(source_shape[0]), int(source_shape[1])
    plate_h, plate_w = int(plate_shape[0]), int(plate_shape[1])
    mapper = placed.get("map")
    if mapper is None:
        return []
    mapped = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        text = str(block.get("text") or "").strip()
        if len(_letters(text)) < MIN_LETTERS:
            continue
        try:
            score = float(block.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score < CONFIDENCE_FLOOR:
            _dbg(f"low-score {score:.2f} {text[:50]!r}")
            continue
        raw = _rect(block, width, height)
        left, top, right, bottom = _mapped_bounds(mapper, raw, plate_w, plate_h)
        mapped.append({
            "block": block,
            "text": text,
            "score": score,
            "core": (left, top, max(2, right - left), max(2, bottom - top)),
        })
    claimed = set()
    found = []
    reasons_used = set()
    for box in raster_boxes or []:
        core = box.get("core") or box.get("rect")
        if not core or len(core) < 4:
            continue
        best = None
        best_iou = 0.0
        for index, item in enumerate(mapped):
            if index in claimed:
                continue
            overlap = _rect_iou(item["core"], core)
            if overlap > best_iou:
                best_iou = overlap
                best = index
        if best is None or best_iou < 0.35:
            _dbg(f"no-block iou={best_iou:.2f} {str(box.get('text') or '')[:50]!r}")
            continue
        claimed.add(best)
        item = mapped[best]
        reason = _line_reason(raster_lines, item["text"], reasons_used)
        if any(phrase in reason.lower() for phrase in SKIP_REASON):
            continue
        rect = box.get("rect") or core
        found.append({
            "text": item["text"],
            "score": item["score"],
            "core": tuple(int(v) for v in item["core"]),
            "rect": tuple(int(v) for v in rect),
            "box": box,
        })
    if not found:
        return []
    others = [tuple(int(v) for v in (box.get("core") or box.get("rect"))) for box in raster_boxes or []]
    for item in drawn or []:
        rect = item.get("rect")
        if rect and len(rect) >= 4:
            others.append(tuple(int(v) for v in rect))
    for item in found:
        item["protect"] = [rect for rect in others if _rect_iou(rect, item["core"]) < 0.5]
    return found


def _line_reason(raster_lines, text: str, used: set) -> str:
    for index, line in enumerate(raster_lines or []):
        if index in used:
            continue
        if str(line.get("text") or "").strip() == text:
            used.add(index)
            return str(line.get("reason") or "")
    return ""


def _rect_iou(left, right) -> float:
    ax, ay, aw, ah = [float(v) for v in left[:4]]
    bx, by, bw, bh = [float(v) for v in right[:4]]
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _ink_mask(crop: np.ndarray) -> np.ndarray | None:
    if crop is None or crop.size == 0 or crop.shape[0] < 6 or crop.shape[1] < 6:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    border = np.concatenate([
        gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1],
    ]).astype(np.float32)
    paper = float(np.median(border))
    # A line of type is a few dark (or light) pixels in a field of paper.
    # A percentile of the whole crop is still the paper, so the extreme is used.
    darkest = float(np.min(gray))
    lightest = float(np.max(gray))
    if paper - darkest >= 28:
        cut = paper - max(18.0, (paper - darkest) * 0.38)
        mask = (gray <= cut).astype(np.uint8) * 255
    elif lightest - paper >= 28:
        cut = paper + max(18.0, (lightest - paper) * 0.38)
        mask = (gray >= cut).astype(np.uint8) * 255
    else:
        return None
    count = int(np.count_nonzero(mask))
    if count < 12 or count > mask.size * 0.72:
        return None
    # A 2×2 open cuts thin strokes and invents gaps inside a letter.
    count_labels, labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    keep = np.zeros(mask.shape, np.uint8)
    for index in range(1, count_labels):
        if int(stats[index, cv2.CC_STAT_AREA]) >= 4:
            keep[labels == index] = 255
    mask = keep
    if int(np.count_nonzero(mask)) < 12:
        return None
    contrast = abs(paper - float(np.median(gray[mask > 0])))
    if contrast < CONTRAST_FLOOR:
        return None
    return mask


def _clip_protect(mask: np.ndarray, origin, protect) -> np.ndarray:
    ox, oy = int(origin[0]), int(origin[1])
    height, width = mask.shape[:2]
    out = mask.copy()
    for rect in protect or []:
        x, y, bw, bh = [int(v) for v in rect[:4]]
        x0 = max(0, x - ox)
        y0 = max(0, y - oy)
        x1 = min(width, x + bw - ox)
        y1 = min(height, y + bh - oy)
        if x1 > x0 and y1 > y0:
            out[y0:y1, x0:x1] = 0
    return out


def _word_spans(mask: np.ndarray, words: list[str]) -> list[tuple[int, int]] | None:
    cols = (mask > 0).any(axis=0)
    runs = []
    index = 0
    width = int(cols.size)
    while index < width:
        if not cols[index]:
            index += 1
            continue
        end = index
        while end < width and cols[end]:
            end += 1
        if end - index >= 1:
            runs.append((index, end))
        index = end
    if not runs:
        return None
    if len(words) == 1:
        return [(runs[0][0], runs[-1][1])]
    gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
    need = len(words) - 1
    if len(gaps) < need:
        return None
    order = sorted(range(len(gaps)), key=lambda i: gaps[i], reverse=True)
    cuts = set(order[:need])
    letter = [gaps[i] for i in range(len(gaps)) if i not in cuts]
    word_gaps = [gaps[i] for i in cuts]
    if letter and min(word_gaps) < max(2.0, float(np.median(letter)) * 1.35):
        return None
    groups = [[runs[0]]]
    for gap_index, run in enumerate(runs[1:]):
        if gap_index in cuts:
            groups.append([run])
        else:
            groups[-1].append(run)
    if len(groups) != len(words):
        return None
    return [(group[0][0], group[-1][1]) for group in groups]


def _agree_words(plate: np.ndarray, candidates: list) -> list:
    prepared = []
    crops = []
    owners = []
    unsplittable = []
    for item in candidates:
        # The trace box includes the descender. Neighbour cores are clipped out
        # of the mask so the next line is not read or painted.
        x, y, bw, bh = item["rect"]
        x = max(0, int(x))
        y = max(0, int(y))
        bw = max(2, int(bw))
        bh = max(2, int(bh))
        crop = plate[y:y + bh, x:x + bw]
        mask = _ink_mask(crop)
        if mask is None:
            _dbg(f"no-ink {item['text'][:50]!r}")
            continue
        mask = _clip_protect(mask, (x, y), item.get("protect"))
        if int(np.count_nonzero(mask)) < 12:
            continue
        words = item["text"].split()
        if not words:
            continue
        ys, xs = np.where(mask > 0)
        item = dict(item)
        item["mask_origin"] = (x, y)
        item["mask"] = mask
        item["source_crop"] = np.ascontiguousarray(crop)
        item["ink_box"] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        item["words"] = words
        spans = _word_spans(mask, words)
        if spans is None:
            _dbg(f"no-split {item['text'][:50]!r}")
            unsplittable.append(item)
            continue
        prepared.append(item)
        for word, (x0, x1) in zip(words, spans):
            pad = 2
            y0 = max(0, int(ys.min()) - pad)
            y1 = min(mask.shape[0], int(ys.max()) + 1 + pad)
            xa = max(0, x0 - pad)
            xb = min(mask.shape[1], x1 + pad)
            word_crop = crop[y0:y1, xa:xb]
            if word_crop.size == 0:
                word_crop = None
            crops.append(word_crop)
            owners.append((len(prepared) - 1, word))
    if not prepared and not unsplittable:
        return []
    reads = _read_crops(crops) if crops else []
    grouped: list[list[tuple[str, float]]] = [[] for _ in prepared]
    for (owner, _word), read in zip(owners, reads):
        grouped[owner].append(read)
    agreed = []
    retry = []
    for item, parts in zip(prepared, grouped):
        if len(parts) != len(item["words"]):
            retry.append(item)
            continue
        if any(not text or score < WORD_SCORE_FLOOR for text, score in parts):
            _dbg(f"word-score {item['text'][:50]!r} {parts}")
            retry.append(item)
            continue
        joined = " ".join(text for text, _score in parts)
        if any(not _word_exact(text, word) for (text, _score), word in zip(parts, item["words"])):
            _dbg(f"word-mismatch {item['text'][:70]!r} -> {joined[:70]!r}")
            retry.append(item)
            continue
        agreed.append(item)
    if retry or unsplittable:
        agreed.extend(_agree_by_detector(retry + unsplittable))
    for item in agreed:
        _drop_side_marks(item)
    return agreed


def _drop_side_marks(item: dict) -> None:
    """A small mark in the left gap is not part of the words. A word is kept."""
    mask = item.get("mask")
    if mask is None:
        return
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 2:
        return
    height, width = mask.shape[:2]
    boxes = []
    for index in range(1, count):
        left = int(stats[index, cv2.CC_STAT_LEFT])
        top = int(stats[index, cv2.CC_STAT_TOP])
        bw = int(stats[index, cv2.CC_STAT_WIDTH])
        bh = int(stats[index, cv2.CC_STAT_HEIGHT])
        boxes.append((index, left, top, bw, bh))
    boxes.sort(key=lambda part: part[1])
    for pos in range(len(boxes) - 1):
        index, left, _top, bw, bh = boxes[pos]
        if bw > max(bh * 1.3, height * 0.7):
            continue
        right_edge = left + bw
        gap = boxes[pos + 1][1] - right_edge
        if gap < max(4, int(width * 0.03)) or right_edge > width * 0.28:
            continue
        mask[labels == index] = 0
        ys, xs = np.where(mask > 0)
        if xs.size < 12:
            return
        item["ink_box"] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return


def _agree_by_detector(items: list) -> list:
    """A second read of each upscaled line. Words must still match exactly."""
    views = []
    for item in items:
        crop = item.get("source_crop")
        if crop is None:
            mask = item["mask"]
            crop = np.full((mask.shape[0], mask.shape[1], 3), 255, np.uint8)
            crop[mask > 0] = (0, 0, 0)
        view = _blank_protected(crop, item.get("mask_origin") or (0, 0), item.get("protect"))
        height, width = view.shape[:2]
        scale = 48.0 / float(max(1, height)) if height < 48 else 1.0
        if width * scale > 700:
            scale = 700.0 / float(max(1, width))
        if scale > 1.05:
            view = cv2.resize(
                view,
                (max(12, int(round(width * scale))), max(12, int(round(height * scale)))),
                interpolation=cv2.INTER_CUBIC,
            )
        views.append(view)
    found = _detect_lines(views)
    agreed = []
    for item, detections in zip(items, found):
        if _align_words(detections, item["words"]):
            _dbg(f"line-agree {item['text'][:70]!r}")
            agreed.append(item)
        else:
            read = " ".join(str(part[0]) for part in detections)
            _dbg(f"line-reject {item['text'][:60]!r} -> {read[:60]!r}")
    return agreed


def _blank_protected(crop: np.ndarray, origin, protect) -> np.ndarray:
    """Paper over a neighbouring line, so its letters are not part of this read."""
    view = np.ascontiguousarray(crop)
    if view.ndim == 2:
        view = cv2.cvtColor(view, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
    paper = int(np.median(np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])))
    ox, oy = int(origin[0]), int(origin[1])
    height, width = view.shape[:2]
    for rect in protect or []:
        x, y, bw, bh = [int(v) for v in rect[:4]]
        x0 = max(0, x - ox)
        y0 = max(0, y - oy)
        x1 = min(width, x + bw - ox)
        y1 = min(height, y + bh - oy)
        if x1 > x0 and y1 > y0:
            view[y0:y1, x0:x1] = paper
    return view


def _detect_lines(views: list) -> list:
    """One detection list per line. Rows stay separate so two lines are not joined."""
    from ocr_reader import local_rows

    results = [[] for _ in views]
    pending = []
    for index, view in enumerate(views):
        if view is None or view.size == 0 or view.shape[0] < 4 or view.shape[1] < 4:
            continue
        border = cv2.copyMakeBorder(view, 8, 8, 10, 10, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        pending.append((index, border))
    step = 4
    for start in range(0, len(pending), step):
        batch = pending[start:start + step]
        gap = 28
        pad = 8
        width = min(736, max(image.shape[1] for _index, image in batch) + pad * 2)
        height = pad + sum(image.shape[0] + gap for _index, image in batch)
        canvas = np.full((height, max(64, width), 3), 255, np.uint8)
        cells = []
        cursor = pad
        for index, image in batch:
            x = pad
            if image.shape[1] > canvas.shape[1] - pad * 2:
                image = cv2.resize(
                    image,
                    (canvas.shape[1] - pad * 2, image.shape[0]),
                    interpolation=cv2.INTER_AREA,
                )
            canvas[cursor:cursor + image.shape[0], x:x + image.shape[1]] = image
            cells.append((index, x, cursor, image.shape[1], image.shape[0]))
            cursor += image.shape[0] + gap
        try:
            found = local_rows(canvas, fast=True) or []
        except Exception:
            found = []
        buckets: dict[int, list] = {index: [] for index, *_rest in cells}
        for item in found:
            box = item[0]
            text = str(item[1] or "").strip()
            score = float(item[2] if len(item) > 2 else 0)
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            area = max(1.0, (x1 - x0) * (y1 - y0))
            best = None
            best_frac = 0.0
            for index, x, y, bw, bh in cells:
                ix0, iy0 = max(x0, x), max(y0, y)
                ix1, iy1 = min(x1, x + bw), min(y1, y + bh)
                inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
                if inter / area > best_frac:
                    best_frac = inter / area
                    best = index
            if best is None or best_frac < 0.7 or not text:
                continue
            buckets[best].append((text, score, x0, x1))
        for index, parts in buckets.items():
            parts.sort(key=lambda part: part[2])
            results[index] = parts
    return results


def _read_crops(crops: list, words: bool = True) -> list[tuple[str, float]]:
    """One read per crop. Each crop sits on its own row so two words are not joined."""
    from ocr_reader import local_rows

    results = [("", 0.0)] * len(crops)
    pending = []
    for index, crop in enumerate(crops):
        if crop is None or getattr(crop, "size", 0) == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
            continue
        height = max(1, int(crop.shape[0]))
        scale = 56.0 / float(height)
        resized = cv2.resize(
            crop,
            (max(12, int(round(crop.shape[1] * scale))), 56),
            interpolation=cv2.INTER_CUBIC,
        )
        if resized.shape[1] > 640:
            resized = cv2.resize(resized, (640, 56), interpolation=cv2.INTER_AREA)
        border = cv2.copyMakeBorder(resized, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        pending.append((index, border))
    # Eight rows stay near the recogniser's long-side limit, so a word is not shrunk away.
    step = 8
    for start in range(0, len(pending), step):
        batch = pending[start:start + step]
        gap = 28
        pad = 8
        width = max(image.shape[1] for _index, image in batch) + pad * 2
        width = min(720, max(width, 64))
        height = pad + sum(image.shape[0] + gap for _index, image in batch)
        canvas = np.full((height, width, 3), 255, np.uint8)
        cells = []
        cursor = pad
        for index, image in batch:
            x = pad
            if image.shape[1] > width - pad * 2:
                image = cv2.resize(image, (width - pad * 2, image.shape[0]), interpolation=cv2.INTER_AREA)
            canvas[cursor:cursor + image.shape[0], x:x + image.shape[1]] = image
            cells.append((index, x, cursor, image.shape[1], image.shape[0]))
            cursor += image.shape[0] + gap
        try:
            found = local_rows(canvas, fast=True) or []
        except Exception:
            found = []
        buckets: dict[int, list] = {index: [] for index, *_rest in cells}
        for item in found:
            box = item[0]
            text = str(item[1] or "").strip()
            score = float(item[2] if len(item) > 2 else 0)
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            area = max(1.0, (x1 - x0) * (y1 - y0))
            best = None
            best_frac = 0.0
            for index, x, y, bw, bh in cells:
                ix0, iy0 = max(x0, x), max(y0, y)
                ix1, iy1 = min(x1, x + bw), min(y1, y + bh)
                inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
                frac = inter / area
                if frac > best_frac:
                    best_frac = frac
                    best = index
            if best is None or best_frac < 0.7:
                continue
            buckets[best].append((x0, x1, text, score))
        for index, parts in buckets.items():
            if not parts:
                continue
            parts.sort(key=lambda part: part[0])
            if words:
                # One word. A space is a letter the reader split.
                text = "".join(part[2] for part in parts).replace(" ", "")
            else:
                pieces = [parts[0][2]]
                for prev, part in zip(parts, parts[1:]):
                    pieces.append((" " if part[0] - prev[1] > 10 else "") + part[2])
                text = "".join(pieces).strip()
            results[index] = (text, min(part[3] for part in parts))
    return results


def _is_caps(text: str) -> bool:
    letters = _letters(text)
    if len(letters) < 3:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= 0.72


def _face_keys(text: str) -> tuple[str, ...]:
    return CAPS_FACES if _is_caps(text) else BODY_FACES


def rank_fonts(mask: np.ndarray, text: str) -> list[tuple[str, float]]:
    """Closest bundled faces, best first. Scored by the press renderer, not a preview."""
    item = _item_from_mask(mask, text)
    if item is None:
        return []
    ranked = []
    for key in _face_keys(text):
        score = _face_score(item, key)
        if score >= FAMILY_FLOOR:
            ranked.append((key, score))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:4]


def choose_font(mask: np.ndarray, text: str) -> tuple[str, float]:
    """Closest bundled face. Empty when none of the families is close."""
    ranked = rank_fonts(mask, text)
    if not ranked:
        return "", 0.0
    return ranked[0]


def _item_from_mask(mask: np.ndarray, text: str) -> dict | None:
    if mask is None or not str(text or "").strip():
        return None
    ys, xs = np.where(mask > 0)
    if xs.size < 12:
        return None
    words = str(text).split()
    if not words:
        return None
    return {
        "text": str(text),
        "mask": mask,
        "words": words,
        "ink_box": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
        "mask_origin": (0, 0),
        "core": (0, 0, int(mask.shape[1]), int(mask.shape[0])),
        "rect": (0, 0, int(mask.shape[1]), int(mask.shape[0])),
        "protect": [],
    }


def _ink_height(mask: np.ndarray) -> int:
    ys, _xs = np.where(mask > 0)
    if ys.size == 0:
        return 0
    return int(ys.max() - ys.min() + 1)


def _stroke_core_width(mask: np.ndarray) -> float:
    """Median stroke width from the distance-transform core, in pixels.

    The core is the medial axis of each letter, so hairline serifs do not
    set the weight. Both masks have to be at the same scale before this.
    """
    if mask is None or mask.size == 0:
        return 0.0
    binary = (mask > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    heights = []
    for index in range(1, count):
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if height >= 6 and area >= 8:
            heights.append(height)
    if not heights:
        return 0.0
    typical = float(np.median(heights))
    widths = []
    for index in range(1, count):
        x, y, width, height, area = [int(stats[index, k]) for k in range(5)]
        if height < typical * 0.70 or area < 8:
            continue
        glyph = binary[y:y + height, x:x + width]
        # A stem as wide as its box has no zero pixel beside it, and the
        # distance transform then overflows. One pixel of paper fixes that.
        glyph = cv2.copyMakeBorder(glyph, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
        dist = cv2.distanceTransform(glyph, cv2.DIST_L2, 3)
        local = cv2.dilate(dist, np.ones((3, 3), np.uint8))
        core = (dist + 1e-3 >= local) & (dist >= 0.6)
        if int(core.sum()) < 2:
            continue
        widths.append(float(np.median(dist[core]) * 2.0))
    if not widths:
        return 0.0
    return float(np.median(widths))


def _stroke_ratio(source: np.ndarray, rendered: np.ndarray) -> float:
    """Rendered stroke over source stroke, after both are brought to one height."""
    source_h = _ink_height(source)
    render_h = _ink_height(rendered)
    source_w = _stroke_core_width(source)
    render_w = _stroke_core_width(rendered)
    if min(source_h, render_h) < 4 or source_w <= 0.4 or render_w <= 0.4:
        return 0.0
    return (render_w / float(render_h)) / (source_w / float(source_h))


def _stroke_ok(source: np.ndarray, rendered: np.ndarray) -> bool:
    ratio = _stroke_ratio(source, rendered)
    return abs(ratio - 1.0) <= STROKE_TOL


def _face_score(item: dict, key: str) -> float:
    """Weight first, then overlap. Outside ±10% stroke is not a match."""
    placed = _place_font(np.zeros((1, 1, 3), np.uint8), item, key, 0.0)
    if placed is None:
        return 0.0
    ratio = _stroke_ratio(item["mask"], placed["render_mask"])
    if ratio <= 0.0 or abs(ratio - 1.0) > STROKE_TOL:
        _dbg(
            f"stroke {key} {ratio:.3f} {item.get('text', '')[:40]!r}"
        )
        return 0.0
    terminals = abs(_terminal_ratio(item["mask"]) - _terminal_ratio(placed["render_mask"]))
    score = 0.70 * (1.0 - min(1.0, abs(ratio - 1.0) / STROKE_TOL)) + 0.30 * float(placed["overlap"])
    if terminals > 0.45:
        score -= 0.12
    return max(0.0, score)


def _terminal_ratio(mask: np.ndarray) -> float:
    """How much wider the ends of a letter are than its middle. Serifs sit above 1."""
    binary = (mask > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    ratios = []
    for index in range(1, count):
        x, y, width, height, area = [int(stats[index, k]) for k in range(5)]
        if height < 8 or width < 2 or area < 10:
            continue
        glyph = binary[y:y + height, x:x + width] > 0
        band = max(2, height // 6)
        mid = glyph[height // 3: max(height // 3 + 1, 2 * height // 3)]

        def span(rows: np.ndarray) -> int:
            return int(rows.any(axis=0).sum()) if rows.size else 0

        middle = max(1, span(mid))
        ratios.append(max(span(glyph[:band]), span(glyph[-band:])) / middle)
    if not ratios:
        return 1.0
    return float(np.median(ratios))


def _pick_face(plate: np.ndarray, group: list, deadline: float) -> str:
    """One face for the block, from the line with the most letters."""
    sample = max(group, key=lambda item: len(_letters(item["text"])))
    best_key = ""
    best_score = 0.0
    second = 0.0
    for key in _face_keys(sample["text"]):
        if time.perf_counter() > deadline:
            break
        score = _face_score(sample, key)
        _dbg(f"face {score:.2f} {key} {sample['text'][:40]!r}")
        if score > best_score:
            second = best_score
            best_score = score
            best_key = key
        elif score > second:
            second = score
    if best_score < 0.42:
        _dbg(f"no-face {best_score:.2f} {sample['text'][:40]!r}")
        return ""
    return best_key


def _fit_group(plate: np.ndarray, group: list, deadline: float) -> list:
    """One face for every line in the group, or nothing.

    A heavier face is not used for the line that happens to fit it. If any
    line misses the stroke, the width, or the word overlap, the group stays
    the picture.
    """
    sample = max(group, key=lambda item: len(_letters(item["text"])))
    ranked = []
    for key in _face_keys(sample["text"]):
        if time.perf_counter() > deadline:
            return []
        score = _face_score(sample, key)
        _dbg(f"face {score:.2f} {key} {sample['text'][:40]!r}")
        if score >= 0.42:
            ranked.append((score, key))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    for score, key in ranked:
        if time.perf_counter() > deadline:
            return []
        placed = []
        failed = False
        for item in group:
            if time.perf_counter() > deadline:
                return []
            one = _place_font(plate, item, key, score)
            if one is None or not _words_clear(one):
                _dbg(f"fit-reject {key} {item['text'][:50]!r}")
                failed = True
                break
            one["font"] = key
            placed.append(one)
        if not failed and len(placed) == len(group):
            return placed
    return []


def _glyph_boxes(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """One box per letter. A dot or accent is part of the letter under it."""
    binary = (mask > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    boxes = []
    for index in range(1, count):
        x, y, width, height, area = [int(stats[index, k]) for k in range(5)]
        if area < 2 or height < 2 or width < 1:
            continue
        boxes.append((x, y, x + width, y + height))
    if not boxes:
        return []
    heights = sorted(box[3] - box[1] for box in boxes)
    typical = heights[len(heights) // 2]
    parent = list(range(len(boxes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, box in enumerate(boxes):
        if box[3] - box[1] >= typical * 0.55:
            continue
        center = (box[0] + box[2]) / 2.0
        for other_index, other in enumerate(boxes):
            if other_index == index or other[3] - other[1] < typical * 0.7:
                continue
            if other[0] - 2 <= center <= other[2] + 2:
                parent[find(index)] = find(other_index)
                break
    groups: dict[int, list] = {}
    for index, box in enumerate(boxes):
        groups.setdefault(find(index), []).append(box)
    merged = []
    for parts in groups.values():
        merged.append((
            min(part[0] for part in parts),
            min(part[1] for part in parts),
            max(part[2] for part in parts),
            max(part[3] for part in parts),
        ))
    merged.sort()
    return merged


def _snap_glyphs(mask: np.ndarray, rendered: np.ndarray, text: str, chars: list):
    """Sit each rendered letter on the source letter. None when the counts differ."""
    letters = [(index, ch) for index, ch in enumerate(text) if not ch.isspace()]
    source_boxes = _glyph_boxes(mask)
    render_boxes = _glyph_boxes(rendered)
    if len(source_boxes) != len(letters) or len(render_boxes) != len(letters) or not letters:
        return None
    shifts = []
    for source_box, render_box in zip(source_boxes, render_boxes):
        source_cy = (source_box[1] + source_box[3]) / 2.0
        render_cy = (render_box[1] + render_box[3]) / 2.0
        shifts.append(source_cy - render_cy)
    shift_y = int(round(float(np.median(shifts))))
    placed = np.zeros(mask.shape, np.uint8)
    pen_at = {}
    for (index, _ch), source_box, render_box in zip(letters, source_boxes, render_boxes):
        source_cx = (source_box[0] + source_box[2]) / 2.0
        render_cx = (render_box[0] + render_box[2]) / 2.0
        shift_x = int(round(source_cx - render_cx))
        x0, y0, x1, y1 = render_box
        glyph = rendered[y0:y1, x0:x1]
        dest_x = shift_x + x0
        dest_y = shift_y + y0
        src_y0 = max(0, dest_y)
        src_x0 = max(0, dest_x)
        cut_y = src_y0 - dest_y
        cut_x = src_x0 - dest_x
        copy_h = min(glyph.shape[0] - cut_y, placed.shape[0] - src_y0)
        copy_w = min(glyph.shape[1] - cut_x, placed.shape[1] - src_x0)
        if copy_h < 1 or copy_w < 1:
            return None
        patch = glyph[cut_y:cut_y + copy_h, cut_x:cut_x + copy_w]
        view = placed[src_y0:src_y0 + copy_h, src_x0:src_x0 + copy_w]
        view[:] = np.maximum(view, patch)
        cursor = chars[index][1] if index < len(chars) else 0.0
        pen_at[index] = float(shift_x) + float(cursor)
    snapped_chars = []
    previous = 0.0
    for index, ch in enumerate(text):
        if index in pen_at:
            previous = pen_at[index]
            snapped_chars.append((ch, previous))
        else:
            snapped_chars.append((ch, previous))
    return {
        "mask": placed,
        "overlap": _iou(mask, placed),
        "chars": snapped_chars,
        "shift_y": shift_y,
    }


def _best_shift(mask: np.ndarray, rendered: np.ndarray, shift_x: int, shift_y: int):
    """A one-pixel baseline error is most of the miss on an 11px letter."""
    best = (0.0, shift_x, shift_y)
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            placed = _stamp(mask.shape, rendered, shift_x + dx, shift_y + dy)
            score = _iou(mask, placed)
            if score > best[0]:
                best = (score, shift_x + dx, shift_y + dy)
    return best


def _stamp(shape, rendered: np.ndarray, shift_x: int, shift_y: int) -> np.ndarray:
    placed = np.zeros(shape, np.uint8)
    src_y0 = max(0, shift_y)
    src_x0 = max(0, shift_x)
    dst_y0 = max(0, -shift_y)
    dst_x0 = max(0, -shift_x)
    copy_h = min(rendered.shape[0] - dst_y0, placed.shape[0] - src_y0)
    copy_w = min(rendered.shape[1] - dst_x0, placed.shape[1] - src_x0)
    if copy_h < 4 or copy_w < 4:
        return placed
    placed[src_y0:src_y0 + copy_h, src_x0:src_x0 + copy_w] = rendered[dst_y0:dst_y0 + copy_h, dst_x0:dst_x0 + copy_w]
    return placed


def _match_word_widths(source: np.ndarray, rendered: np.ndarray, text: str, chars: list, size: float, key: str):
    """Scale each word by at most 5% so its ink matches the source word."""
    from vector_text_v2 import _fitz_font

    words = str(text or "").split()
    src = _word_spans(source, words)
    ren = _word_spans(rendered, words)
    if not src or not ren or len(src) != len(words):
        return None
    font = _fitz_font(key)
    if font is None:
        return None
    groups = _char_groups(text, chars)
    if len(groups) != len(words):
        return None
    placed = np.zeros_like(rendered)
    runs = []
    moved = list(chars)
    bad_letters = 0
    suspect_letters = 0
    total_letters = 0
    for (sx0, sx1), (rx0, rx1), indexes, word in zip(src, ren, groups, words):
        rw = max(1, rx1 - rx0)
        sw = max(1, sx1 - sx0)
        needed = sw / float(rw)
        # Never scale past 5%. Two pixels left over is the fringe, not another face.
        scale = float(min(WIDTH_SCALE_MAX, max(WIDTH_SCALE_MIN, needed)))
        residual = abs(sw - rw * scale)
        shortfall = residual / float(sw)
        letters = max(1, sum(1 for ch in word if not ch.isspace()))
        total_letters += letters
        # A span this far from the letters is a bad split, not a narrow face.
        if needed < 0.62 or needed > 1.55:
            scale = 1.0
            suspect_letters += letters
            _dbg(f"width-span {needed:.3f} {word!r} {sw} vs {rw}")
        # One short word often has a noisy span. Most of the line still has to fit.
        elif residual > 2.0 and shortfall > 0.015:
            bad_letters += letters
            _dbg(f"width-scale {needed:.3f} short {shortfall:.3f} {word!r} {sw} vs {rw}")
        word_img = rendered[:, rx0:rx1]
        new_w = max(1, int(round(rw * scale)))
        if new_w != word_img.shape[1]:
            scaled = cv2.resize(word_img, (new_w, word_img.shape[0]), interpolation=cv2.INTER_LINEAR)
            scaled = np.where(scaled > 127, np.uint8(255), np.uint8(0))
        else:
            scaled = word_img
        pivot = (sx0 + sx1) / 2.0
        dest = int(round(pivot - scaled.shape[1] / 2.0))
        x_from = max(0, dest)
        x_to = min(placed.shape[1], dest + scaled.shape[1])
        src_from = x_from - dest
        if x_to <= x_from:
            return None
        view = placed[:, x_from:x_to]
        view[:] = np.maximum(view, scaled[:, src_from:src_from + (x_to - x_from)])
        advance = _text_width(font, word, size, 0.0)
        runs.append({
            "text": word,
            "pivot": float(pivot),
            "left": float(pivot - advance / 2.0),
            "scale": scale,
        })
        cxs = [moved[index][1] for index in indexes]
        centre = (min(cxs) + max(cxs)) / 2.0 if cxs else pivot
        for index in indexes:
            ch, px = moved[index]
            moved[index] = (ch, pivot + (px - centre) * scale)
    if total_letters and suspect_letters / float(total_letters) > 0.50:
        _dbg(f"width-span-line {suspect_letters}/{total_letters} {text[:40]!r}")
        return None
    if total_letters and bad_letters / float(total_letters) > 0.45:
        _dbg(f"width-line {bad_letters}/{total_letters} {text[:40]!r}")
        return None
    return placed, moved, runs, _iou(source, placed)


def _place_font(plate: np.ndarray, item: dict, key: str, score: float) -> dict | None:
    if not item.get("_marks"):
        _apply_source_marks(item)
        item["_marks"] = True
    mask = item["mask"]
    x0, y0, x1, y1 = item["ink_box"]
    ink_h = max(4, y1 - y0)
    ink_w = max(4, x1 - x0)
    size, track = _fit_metrics(key, item["text"], ink_h, ink_w)
    rendered, baseline, chars = _render_layout(key, item["text"], size, track)
    if rendered is None:
        return None
    rys, rxs = np.where(rendered > 0)
    if rxs.size < 8:
        return None
    render_h = int(rxs.size and (rys.max() - rys.min() + 1))
    if render_h < 3:
        return None
    if render_h != ink_h:
        size = max(4.0, size * (ink_h / float(render_h)))
        size, track = _fit_metrics(key, item["text"], ink_h, ink_w, size_hint=size)
        rendered, baseline, chars = _render_layout(key, item["text"], size, track)
        if rendered is None:
            return None
        rys, rxs = np.where(rendered > 0)
        if rxs.size < 8:
            return None
    # Land the rendered ink on the source ink. Left edges and vertical centres.
    shift_x = int(x0) - int(rxs.min())
    shift_y = int((y0 + y1) / 2) - int((rys.min() + rys.max()) / 2)
    overlap, shift_x, shift_y = _best_shift(mask, rendered, shift_x, shift_y)
    placed_mask = _stamp(mask.shape, rendered, shift_x, shift_y)
    # One shift for the line, then each word may move a couple of pixels so
    # it sits on the source word. Letters inside a word are not moved apart.
    used_chars = [(ch, shift_x + px) for ch, px in chars]
    used_shift_y = shift_y
    sat = _sit_words(mask, placed_mask, item["text"], used_chars)
    if sat is not None:
        placed_mask, used_chars, overlap = sat
    matched = _match_word_widths(mask, placed_mask, item["text"], used_chars, float(size), key)
    if matched is None:
        _dbg(f"width {key} {item['text'][:50]!r}")
        return None
    placed_mask, used_chars, runs, overlap = matched
    if overlap < OVERLAP_FLOOR:
        rys2, rxs2 = np.where(placed_mask > 0)
        rh = int(rys2.max() - rys2.min() + 1) if rxs2.size else 0
        rw = int(rxs2.max() - rxs2.min() + 1) if rxs2.size else 0
        _dbg(
            f"overlap {overlap:.2f} {key} size={size:.1f} track={track:.2f} "
            f"ink={ink_w}x{ink_h} render={rw}x{rh} {item['text'][:50]!r}"
        )
        return None
    if not _words_sit(mask, placed_mask, len(item["words"])):
        _dbg(f"word-place {key} {item['text'][:50]!r}")
        return None
    if not _stroke_ok(mask, placed_mask):
        _dbg(f"stroke {key} {_stroke_ratio(mask, placed_mask):.3f} {item['text'][:50]!r}")
        return None
    origin_x, origin_y = item["mask_origin"]
    plate_chars = [(ch, origin_x + px) for ch, px in used_chars]
    for run in runs:
        run["left"] = float(origin_x) + float(run["left"])
        run["pivot"] = float(origin_x) + float(run["pivot"])
    colour, source_rgb = _ink_cmyk(plate, item["mask_origin"], mask)
    made = {
        "text": item["text"],
        "font": key,
        "match": round(float(score), 3),
        "overlap": round(float(overlap), 3),
        "fill": colour,
        "source_ink": source_rgb,
        "size_px": float(size),
        "baseline_px": float(origin_y + used_shift_y + baseline),
        "chars": plate_chars,
        "runs": runs,
        "mask_origin": item["mask_origin"],
        "mask": mask,
        "core": item["core"],
        "rect": item["rect"],
        "protect": list(item.get("protect") or []),
        "render_mask": placed_mask,
        "source_crop": item.get("source_crop"),
        "words": list(item.get("words") or []),
    }
    if item.get("ocr_text"):
        made["ocr_text"] = item["ocr_text"]
    return made


def _fit_metrics(key: str, text: str, ink_h: int, ink_w: int, size_hint: float | None = None) -> tuple[float, float]:
    from vector_text_v2 import _fitz_font

    font = _fitz_font(key)
    if font is None:
        return 12.0, 0.0
    em = max(0.4, float(font.ascender) - float(font.descender))
    if size_hint is None:
        size = max(4.0, float(ink_h) / (em * 0.72))
    else:
        size = max(4.0, float(size_hint))
    # Width is a horizontal scale of at most 5%, applied per word later.
    # Shrinking the size to hit the width also thins the stroke.
    return float(size), 0.0


def _text_width(font, text: str, size: float, track: float) -> float:
    total = 0.0
    for index, ch in enumerate(text):
        total += font.text_length(ch, fontsize=size)
        if index < len(text) - 1:
            total += track
    return total


def _scratch_doc():
    import pymupdf as fitz

    doc = getattr(_scratch_doc, "doc", None)
    if doc is None or doc.is_closed:
        doc = fitz.open()
        _scratch_doc.doc = doc
    return doc


def _render_layout(key: str, text: str, size: float, track: float):
    import pymupdf as fitz

    from vector_text_v2 import _fitz_font

    font = _fitz_font(key)
    if font is None:
        return None, 0.0, []
    width = _text_width(font, text, size, track)
    page_w = max(8, int(math_ceil(width)) + 6)
    page_h = max(8, int(math_ceil(size * 2.4)) + 4)
    doc = _scratch_doc()
    page = doc.new_page(width=page_w, height=page_h)
    try:
        writer = fitz.TextWriter(page.rect)
        baseline = size * (0.2 + max(0.0, float(font.ascender)))
        cursor = 2.0
        chars = []
        for index, ch in enumerate(text):
            chars.append((ch, cursor))
            writer.append((cursor, baseline), ch, font=font, fontsize=size)
            cursor += font.text_length(ch, fontsize=size)
            if index < len(text) - 1:
                cursor += track
        writer.write_text(page, color=(0, 0, 0))
        pix = page.get_pixmap(alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.delete_page(page.number)
    mask = (rgb[:, :, 0] < 200).astype(np.uint8) * 255
    return mask, float(baseline), chars


def math_ceil(value: float) -> int:
    import math
    return int(math.ceil(value))


def _iou(source: np.ndarray, rendered: np.ndarray) -> float:
    a = source > 0
    b = rendered > 0
    inter = int(np.count_nonzero(a & b))
    union = int(np.count_nonzero(a | b))
    if union < 8:
        return 0.0
    return inter / float(union)


def _words_sit(source: np.ndarray, rendered: np.ndarray, word_count: int) -> bool:
    """Word centres have to land on the source words. A missing word fails."""
    if word_count <= 1:
        return True
    labels = ["w"] * word_count
    left = _word_spans(source, labels)
    right = _word_spans(rendered, labels)
    if left is None or right is None:
        return False
    for (a0, a1), (b0, b1) in zip(left, right):
        ac = (a0 + a1) / 2.0
        bc = (b0 + b1) / 2.0
        limit = max(3.0, 0.45 * max(1, a1 - a0))
        if abs(ac - bc) > limit:
            return False
    return True


def _core_rgb(plate: np.ndarray, origin, mask: np.ndarray) -> np.ndarray | None:
    """Darkest 15% of the stroke, as RGB rows.

    The choice is the sum of the RGB channels. A grey conversion first would
    pull a dark green toward neutral black, and the median would be that black.
    """
    ox, oy = int(origin[0]), int(origin[1])
    binary = (mask > 0).astype(np.uint8)
    ys, xs = np.where(binary > 0)
    if xs.size < 4:
        return None
    sample_y = np.clip(oy + ys, 0, plate.shape[0] - 1)
    sample_x = np.clip(ox + xs, 0, plate.shape[1] - 1)
    bgr = plate[sample_y, sample_x][:, :3].astype(np.float32)
    if bgr.shape[0] > 4000:
        step = int(bgr.shape[0] / 4000)
        bgr = bgr[::step]
    rgb = bgr[:, ::-1]
    darkness = rgb.sum(axis=1)
    darkest = rgb[darkness <= np.percentile(darkness, 15)]
    if darkest.shape[0] < 4:
        darkest = rgb
    return darkest


def _ink_cmyk(plate: np.ndarray, origin, mask: np.ndarray):
    """Press CMYK of the RGB stroke core, plus that RGB for the gate.

    Near-black type is not snapped to solid K. A dark green has to stay green.
    """
    from PIL import Image, ImageCms

    from vector_text_v2 import _press_cmyk

    pixels = _core_rgb(plate, origin, mask)
    if pixels is None:
        return (0.0, 0.0, 0.0, 1.0), (0, 0, 0)
    med = np.median(pixels, axis=0)
    rgb = tuple(int(round(float(v))) for v in med[:3])
    rgb = tuple(max(0, min(255, channel)) for channel in rgb)
    pixel = Image.new("RGB", (1, 1), rgb)
    converted = ImageCms.applyTransform(pixel, _press_cmyk()).getpixel((0, 0))
    cmyk = tuple(float(channel) / 255.0 for channel in converted)
    return cmyk, rgb


def _sit_words(source: np.ndarray, rendered: np.ndarray, text: str, chars: list):
    """Nudge each word onto the source word. None when a word cannot sit."""
    words = str(text or "").split()
    source_spans = _word_spans(source, words)
    render_spans = _word_spans(rendered, words)
    if source_spans is None or render_spans is None:
        return None
    groups = _char_groups(text, chars)
    if len(groups) != len(words):
        return None
    placed = np.zeros_like(rendered)
    moved = list(chars)
    ious = []
    for (sx0, sx1), (rx0, rx1), indexes in zip(source_spans, render_spans, groups):
        dx = int(round(((sx0 + sx1) / 2.0) - ((rx0 + rx1) / 2.0)))
        limit = min(4, max(2, int(0.15 * max(1, sx1 - sx0))))
        if abs(dx) > limit:
            return None
        src_x = max(0, rx0 + dx)
        copy = rendered[:, rx0:rx1]
        dest_x = min(placed.shape[1], src_x)
        width = min(copy.shape[1], placed.shape[1] - dest_x)
        if width < 1:
            return None
        view = placed[:, dest_x:dest_x + width]
        view[:] = np.maximum(view, copy[:, :width])
        for index in indexes:
            ch, px = moved[index]
            moved[index] = (ch, px + dx)
        ious.append(_iou(source[:, sx0:sx1], placed[:, sx0:sx1]))
    # The press read-back is stricter. This only drops a word that missed the source.
    if not ious or min(ious) < 0.30:
        return None
    overlap = _iou(source, placed)
    return placed, moved, overlap


def _char_groups(text: str, chars: list) -> list[list[int]]:
    groups = []
    current: list[int] = []
    for index, ch in enumerate(text):
        if index >= len(chars):
            break
        if ch.isspace():
            if current:
                groups.append(current)
                current = []
            continue
        current.append(index)
    if current:
        groups.append(current)
    return groups


def _words_clear(item: dict) -> bool:
    """Each word overlaps the source, and letters do not land on each other."""
    ious = _word_ious(item["mask"], item["render_mask"], item.get("words") or [])
    # One weak word can still be checked on the press page, which keeps 0.40.
    if not ious or min(ious) < 0.30:
        _dbg(f"word-overlap {item.get('font')} {ious} {item['text'][:40]!r}")
        return False
    if _glyphs_collide(item["render_mask"]):
        _dbg(f"collide {item.get('font')} {item['text'][:40]!r}")
        return False
    return True


def _word_ious(source: np.ndarray, rendered: np.ndarray, words: list) -> list[float] | None:
    spans = _word_spans(source, words)
    if spans is None or rendered.shape[:2] != source.shape[:2]:
        return None
    ious = []
    for x0, x1 in spans:
        ious.append(_iou(source[:, x0:x1], rendered[:, x0:x1]))
    return ious


def _glyphs_collide(rendered: np.ndarray) -> bool:
    boxes = _glyph_boxes(rendered)
    if len(boxes) < 2:
        return False
    for left, right in zip(boxes, boxes[1:]):
        overlap = min(left[2], right[2]) - max(left[0], right[0])
        narrow = max(1, min(left[2] - left[0], right[2] - right[0]))
        if overlap > 0.45 * narrow:
            return True
    return False


def _press_keep(fitted: list, shape) -> list:
    """Draw the lines the way the PDF will, then compare each word to the source."""
    import pymupdf as fitz

    height, width = int(shape[0]), int(shape[1])
    if width < 8 or height < 8 or not fitted:
        return []
    doc = fitz.open()
    try:
        page = doc.new_page(width=width, height=height)
        paint_retyped(page, fitted, 1.0, 1.0)
        pix = page.get_pixmap(alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.close()
    if rgb.shape[0] != height or rgb.shape[1] != width:
        rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    kept = []
    for item in fitted:
        if _press_words(gray, item):
            _dbg(f"accept {item['font']} overlap={item['overlap']} {item['text'][:70]!r}")
            kept.append(item)
        else:
            _dbg(f"press-reject {item['font']} {item['text'][:60]!r}")
    return kept


def _press_words(gray: np.ndarray, item: dict) -> bool:
    mask = item["mask"]
    spans = _word_spans(mask, item.get("words") or [])
    if not spans:
        return False
    ox, oy = item["mask_origin"]
    for x0, x1 in spans:
        y_idx, x_idx = np.where(mask[:, x0:x1] > 0)
        if x_idx.size < 4:
            return False
        top = max(0, oy + int(y_idx.min()))
        bottom = min(gray.shape[0], oy + int(y_idx.max()) + 1)
        left = max(0, ox + x0)
        right = min(gray.shape[1], ox + x1)
        if bottom <= top or right <= left:
            return False
        press = (gray[top:bottom, left:right] < 200).astype(np.uint8) * 255
        source = mask[int(y_idx.min()):int(y_idx.max()) + 1, x0:x1]
        if press.shape != source.shape:
            press = cv2.resize(press, (source.shape[1], source.shape[0]), interpolation=cv2.INTER_NEAREST)
        score = _iou(source, press)
        if score < WORD_OVERLAP_FLOOR:
            _dbg(f"press-word {score:.3f}")
            return False
    return True


def _fill_text_hole(crop: np.ndarray, hole: np.ndarray) -> tuple[np.ndarray, bool]:
    """Inpaint a dilated letter hole from the paper outside it.

    The hole is shifted to the ring's mean and grain. If it still does not
    match that ring, the line stays in the picture.
    """
    hole_u8 = ((hole > 0).astype(np.uint8)) * 255
    if int(hole_u8.max()) == 0:
        return crop, True
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    outer = cv2.dilate(hole_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    ring = (outer > 0) & (hole_u8 == 0)
    if int(ring.sum()) < 24:
        return crop, False
    paper = float(np.median(gray[ring]))
    clean = ring & (np.abs(gray.astype(np.float32) - paper) <= 14.0)
    if int(clean.sum()) < 16:
        clean = ring
    filled = cv2.inpaint(crop, hole_u8, 3, cv2.INPAINT_TELEA)
    out = filled.astype(np.float32)
    ring_px = crop[clean].astype(np.float32)
    hole_sel = hole_u8 > 0
    target_mean = ring_px.mean(axis=0)
    out[hole_sel] += target_mean - out[hole_sel].mean(axis=0)
    target_std = ring_px.std(axis=0)
    current_std = out[hole_sel].std(axis=0)
    need = np.sqrt(np.maximum(0.0, target_std ** 2 - current_std ** 2))
    if float(np.max(need)) > 0.15:
        noise = np.random.default_rng(1).normal(0.0, 1.0, size=out[hole_sel].shape).astype(np.float32)
        out[hole_sel] = out[hole_sel] + noise * need
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)

    def luma(px: np.ndarray) -> np.ndarray:
        return 0.114 * px[:, 2] + 0.587 * px[:, 1] + 0.299 * px[:, 0]

    hole_px = out[hole_sel].astype(np.float32)
    mean_diff = abs(float(luma(hole_px).mean()) - float(luma(ring_px).mean()))
    std_diff = abs(float(luma(hole_px).std()) - float(luma(ring_px).std()))
    chan_diff = float(np.max(np.abs(hole_px.mean(axis=0) - ring_px.mean(axis=0))))
    if mean_diff > PAINT_MEAN_MAX or std_diff > PAINT_STD_MAX or chan_diff > 4.0:
        _dbg(f"paint-diff mean {mean_diff:.2f} std {std_diff:.2f} chan {chan_diff:.2f}")
        return crop, False
    result = crop.copy()
    result[hole_sel] = out[hole_sel]
    return result, True


def _paint_one(plate: np.ndarray, item: dict, others: list) -> bool:
    """Replace one line with matching paper. False keeps the picture."""
    from vector_trace import _letter_erase_mask

    mask = _cover_mask(item.get("source_crop"), item["mask"])
    mask = _letter_erase_mask(mask)
    mask = _clip_protect(mask, item["mask_origin"], _protect_from(item, others))
    if int(mask.max()) == 0:
        return False
    hole = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    ox, oy = item["mask_origin"]
    margin = 18
    y0 = max(0, oy - margin)
    x0 = max(0, ox - margin)
    y1 = min(plate.shape[0], oy + hole.shape[0] + margin)
    x1 = min(plate.shape[1], ox + hole.shape[1] + margin)
    if y1 <= y0 or x1 <= x0:
        return False
    crop = plate[y0:y1, x0:x1]
    local = np.zeros(crop.shape[:2], np.uint8)
    ly, lx = oy - y0, ox - x0
    hy = min(hole.shape[0], local.shape[0] - ly)
    hx = min(hole.shape[1], local.shape[1] - lx)
    if hy < 1 or hx < 1:
        return False
    local[ly:ly + hy, lx:lx + hx] = hole[:hy, :hx]
    filled, ok = _fill_text_hole(crop, local)
    if not ok:
        _dbg(f"paint-reject {item.get('text', '')[:50]!r}")
        return False
    plate[y0:y1, x0:x1] = filled
    return True


def _paint(plate: np.ndarray, items: list) -> list:
    """Paint a style group together. One hole that will not match puts the group back."""
    buckets: dict = {}
    order = []
    for item in items:
        gid = item.get("group_id")
        if gid not in buckets:
            order.append(gid)
            buckets[gid] = []
        buckets[gid].append(item)
    kept = []
    for gid in order:
        rows = buckets[gid]
        snapshot = plate.copy()
        good = True
        for item in rows:
            if not _paint_one(plate, item, items):
                good = False
                break
        need = int(rows[0].get("group_size") or len(rows))
        if not good or len(rows) != need:
            plate[:] = snapshot
            continue
        kept.extend(rows)
    return kept


def _complete_groups(items: list) -> list:
    """Drop a style group unless every line in it is still present."""
    buckets: dict = {}
    order = []
    for item in items:
        gid = item.get("group_id")
        if gid not in buckets:
            order.append(gid)
            buckets[gid] = []
        buckets[gid].append(item)
    kept = []
    for gid in order:
        rows = buckets[gid]
        need = int(rows[0].get("group_size") or len(rows))
        if len(rows) == need:
            kept.extend(rows)
        else:
            _dbg(f"group-drop {len(rows)}/{need} {rows[0].get('text', '')[:40]!r}")
    return kept


def _cover_mask(crop: np.ndarray | None, core: np.ndarray) -> np.ndarray:
    """The letter core plus the grey edge, so the original does not ghost through."""
    if crop is None or crop.ndim != 3 or crop.shape[:2] != core.shape[:2]:
        return core
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]]).astype(np.float32)
    paper = float(np.median(border))
    if paper - float(gray.min()) >= 28:
        soft = gray <= (paper - 8.0)
    elif float(gray.max()) - paper >= 28:
        soft = gray >= (paper + 8.0)
    else:
        return core
    near = cv2.dilate((core > 0).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    covered = np.zeros(core.shape, np.uint8)
    covered[(core > 0) | (soft & near)] = 255
    return covered


def _prepare(plate: np.ndarray, candidates: list) -> list:
    """Ink masks and word spans. A line that cannot be split stays a picture."""
    prepared = []
    for item in candidates:
        x, y, bw, bh = item["rect"]
        x = max(0, int(x))
        y = max(0, int(y))
        bw = max(2, int(bw))
        bh = max(2, int(bh))
        crop = plate[y:y + bh, x:x + bw]
        mask = _ink_mask(crop)
        if mask is None:
            continue
        mask = _clip_protect(mask, (x, y), item.get("protect"))
        if int(np.count_nonzero(mask)) < 12:
            continue
        words = item["text"].split()
        if not words:
            continue
        ys, xs = np.where(mask > 0)
        made = dict(item)
        made["mask_origin"] = (x, y)
        made["mask"] = mask
        made["source_crop"] = np.ascontiguousarray(crop)
        made["ink_box"] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        made["words"] = words
        if _word_spans(mask, words) is None:
            _dbg(f"no-split {item['text'][:50]!r}")
            continue
        _drop_side_marks(made)
        if _word_spans(made["mask"], words) is None:
            continue
        prepared.append(made)
    return prepared


def _style_blocks(items: list) -> list:
    """Headings in one column are one group. Body lines of one item are another.

    An item is the body under a heading, up to the next heading. The group is
    retyped together. One line that cannot match puts the whole group back.
    """
    if not items:
        return []
    pending = sorted(items, key=lambda item: (item["core"][0], item["core"][1]))
    columns: list[list] = []
    for item in pending:
        height = max(4, item["ink_box"][3] - item["ink_box"][1])
        placed = False
        for column in columns:
            anchor = column[0]
            limit = max(28, int(height * 1.2))
            if abs(int(item["core"][0]) - int(anchor["core"][0])) <= limit:
                column.append(item)
                placed = True
                break
        if not placed:
            columns.append([item])
    groups = []
    for column in columns:
        column.sort(key=lambda item: item["core"][1])
        headings = [item for item in column if _is_caps(item["text"])]
        if headings:
            groups.append(headings)
        bodies: list = []
        for item in column:
            if _is_caps(item["text"]):
                if bodies:
                    groups.extend(_size_clusters(bodies))
                    bodies = []
                continue
            bodies.append(item)
        if bodies:
            groups.extend(_size_clusters(bodies))
    return groups


def _size_clusters(items: list) -> list:
    """Body lines of one item that share a size. A caption is not the body."""
    pending = list(items)
    used = [False] * len(pending)
    groups = []
    for index, item in enumerate(pending):
        if used[index]:
            continue
        height = max(4, item["ink_box"][3] - item["ink_box"][1])
        group = [item]
        used[index] = True
        for other_index, other in enumerate(pending):
            if used[other_index]:
                continue
            other_h = max(4, other["ink_box"][3] - other["ink_box"][1])
            if abs(other_h - height) / float(height) <= 0.28:
                group.append(other)
                used[other_index] = True
        groups.append(group)
    return groups


def _protect_from(item: dict, items: list) -> list:
    protect = list(item.get("protect") or [])
    for other in items:
        if other is item:
            continue
        protect.append(other["core"])
    return protect


def _same_line(stored, item: dict) -> bool:
    text = str(stored or "").strip()
    if not text:
        return False
    if text == item["text"]:
        return True
    return text == str(item.get("ocr_text") or "")


def _mark(raster_lines: list, text_gate, items: list) -> None:
    used_lines = set()
    used_gate = set()
    for item in items:
        for index, line in enumerate(raster_lines or []):
            if index in used_lines:
                continue
            if not _same_line(line.get("text"), item):
                continue
            if line.get("mode") == "vector":
                continue
            line["text"] = item["text"]
            line["mode"] = "vector"
            line["font"] = item["font"]
            line["match"] = item["overlap"]
            line["reason"] = ""
            line["retyped"] = True
            used_lines.add(index)
            break
        if not text_gate:
            continue
        for index, row in enumerate(text_gate):
            if index in used_gate:
                continue
            if not _same_line(row.get("text"), item):
                continue
            if row.get("mode") == "vector" and not row.get("retyped"):
                continue
            row["text"] = item["text"]
            row["mode"] = "vector"
            row["render"] = item["text"]
            row["font"] = item["font"]
            row["ok"] = True
            row["glyphFail"] = False
            row["retyped"] = True
            if item.get("source_ink"):
                row["sourceInk"] = [int(v) for v in item["source_ink"]]
            used_gate.add(index)
            break


def paint_retyped(page, items: list, sx: float, sy: float) -> None:
    """Live type on the press page. The face is embedded by the writer."""
    import pymupdf as fitz

    from vector_text_v2 import _fitz_font

    if not items:
        return
    for item in items:
        font = _fitz_font(item.get("font") or "")
        if font is None:
            continue
        size = float(item["size_px"]) * float(sy)
        if size < 0.4:
            continue
        colour = tuple(float(channel) for channel in item.get("fill") or (0, 0, 0, 1))
        baseline = float(item["baseline_px"]) * float(sy)
        runs = item.get("runs") or []
        if runs:
            for run in runs:
                writer = fitz.TextWriter(page.rect)
                left = float(run["left"]) * float(sx)
                pivot = fitz.Point(float(run["pivot"]) * float(sx), baseline)
                writer.append((left, baseline), str(run.get("text") or ""), font=font, fontsize=size)
                scale = float(run.get("scale") or 1.0)
                morph = None
                if abs(scale - 1.0) > 0.001:
                    morph = (pivot, fitz.Matrix(scale, 1.0))
                writer.write_text(page, color=colour, morph=morph)
            continue
        writer = fitz.TextWriter(page.rect)
        for ch, px in item.get("chars") or []:
            writer.append((float(px) * float(sx), baseline), ch, font=font, fontsize=size)
        writer.write_text(page, color=colour)


def fonts_embedded(path: str) -> bool:
    """True when every face on the page is embedded. No faces is not a failure."""
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        fonts = doc[0].get_fonts() or []
    finally:
        doc.close()
    if not fonts:
        return True
    for font in fonts:
        ext = str(font[1] if len(font) > 1 else "")
        base = str(font[3] if len(font) > 3 else "")
        if ext.lower() in ("", "n/a") and "+" not in base:
            return False
    return True
