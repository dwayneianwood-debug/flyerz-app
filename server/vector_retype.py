#!/usr/bin/env python3
"""Retype a line the tracer left as a picture.

Only a line the trace rejected is considered. It becomes live type when all
of these hold:

- the page read is confident
- a second read of each word on the upscaled picture matches that line
  letter for letter, including case, accents and punctuation
- a bundled open-licence face is close to the ink
- the fitted render overlaps the source ink, and a read of that render
  matches the same letters

Anything unsure stays in the picture. Icons and neighbouring lines are not
painted. The face is embedded in the press PDF.
"""

from __future__ import annotations

import os
import re
import sys

import cv2
import numpy as np


def _dbg(message: str) -> None:
    if os.environ.get("VECTOR_RETYPE_DEBUG"):
        sys.stderr.write("[retype] " + message + "\n")

CONFIDENCE_FLOOR = 0.90
WORD_SCORE_FLOOR = 0.80
FAMILY_FLOOR = 0.12
OVERLAP_FLOOR = 0.34
CONTRAST_FLOOR = 40.0
MIN_LETTERS = 3

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
    candidates = _candidates(blocks, raster_lines, raster_boxes, drawn, placed, source_shape, pristine.shape)
    _dbg(f"candidates {len(candidates)} of {len(raster_boxes or [])} raster boxes")
    if not candidates:
        return []
    agreed = _agree_words(pristine, candidates)
    _dbg(f"agreed {len(agreed)} of {len(candidates)}")
    if not agreed:
        return []
    fitted = []
    for item in agreed:
        choice = _fit_line(pristine, item)
        if choice is None:
            _dbg(f"fit-reject {item['text'][:60]!r}")
        else:
            fitted.append(choice)
    _dbg(f"fitted {len(fitted)}")
    if not fitted:
        return []
    confirmed = _confirm_render(fitted)
    _dbg(f"confirmed {len(confirmed)} of {len(fitted)}")
    if not confirmed:
        return []
    _paint(plate, confirmed)
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
    """One word. A space inside the read is a letter the recogniser split."""
    return _exact(str(read or "").replace(" ", ""), str(expected or "").replace(" ", ""))


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


def rank_fonts(mask: np.ndarray, text: str) -> list[tuple[str, float]]:
    """Closest bundled faces, best first. Empty when none of the families is close."""
    from vector_text_v2 import _score_key

    if mask is None or not text:
        return []
    scores = {name: _score_key(mask, text, key) for name, key in REPS.items()}
    order = sorted(scores, key=lambda name: scores[name], reverse=True)
    winner = order[0]
    best = float(scores[winner])
    if best < FAMILY_FLOOR:
        return []
    second = float(scores[order[1]]) if len(order) > 1 else 0.0
    if winner == "script" and best < second + 0.04:
        winner = "serif" if scores["serif"] >= scores["sans"] else "sans"
    if winner == "italic" and best < scores["serif"] + 0.02:
        winner = "serif"
    caps = len(_letters(text)) >= 3 and sum(1 for ch in _letters(text) if ch.isupper()) / max(1, len(_letters(text))) >= 0.72
    if winner != "display" and caps and scores["display"] + 0.02 >= scores[winner]:
        winner = "display"
    # The closest family, then the runner-up. A script score can beat a serif
    # on a short line; the overlap check keeps only a face that sits on the ink.
    chosen = [winner]
    for name in order[1:]:
        if float(scores[name]) < FAMILY_FLOOR:
            continue
        if name not in chosen:
            chosen.append(name)
        if len(chosen) >= 2:
            break
    ranked = []
    for name in chosen:
        family = FAMILIES[name]
        ranked.extend((key, _score_key(mask, text, key)) for key in family)
    ranked.sort(key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < FAMILY_FLOOR:
        return []
    ordered = []
    seen = set()
    for key, score in _weight_order(mask, text, ranked[:8]):
        if key in seen or float(score) < FAMILY_FLOOR:
            continue
        seen.add(key)
        ordered.append((key, float(score)))
        if len(ordered) >= 4:
            break
    return ordered


def choose_font(mask: np.ndarray, text: str) -> tuple[str, float]:
    """Closest bundled face. Empty when none of the families is close."""
    ranked = rank_fonts(mask, text)
    if not ranked:
        return "", 0.0
    return ranked[0]


def _weight_order(mask: np.ndarray, text: str, ranked: list) -> list[tuple[str, float]]:
    """Among close shape scores, keep the weight whose stroke matches the ink."""
    from vector_text_v2 import _FONT_ROLE, _rel_stroke, _render_ink, font_path

    best_score = float(ranked[0][1])
    pool = [(key, float(score)) for key, score in ranked if float(score) >= best_score - 0.06]
    if len(pool) == 1:
        return pool
    source = _rel_stroke(mask)
    weighted = []
    for key, score in pool:
        rendered = _render_ink(
            text, font_path(key), max(int(mask.shape[1]), 80), max(int(mask.shape[0]), 24),
            _FONT_ROLE.get(key, "body"),
        )
        err = 1.0 if rendered is None else abs(_rel_stroke(rendered) - source)
        weighted.append((err, -score, key, score))
    weighted.sort()
    rest = [key for key, _score in ranked if key not in {item[2] for item in weighted}]
    ordered = [(key, score) for _err, _neg, key, score in weighted]
    for key in rest:
        ordered.append((key, dict(ranked)[key]))
    return ordered


def _fit_line(plate: np.ndarray, item: dict) -> dict | None:
    mask = item["mask"]
    ranked = rank_fonts(mask, item["text"])
    if not ranked:
        _dbg(f"no-font {item['text'][:50]!r}")
        return None
    best = None
    for key, score in ranked:
        placed = _place_font(plate, item, key, score)
        if placed is None:
            continue
        if best is None or placed["overlap"] > best["overlap"]:
            best = placed
        if placed["overlap"] >= OVERLAP_FLOOR + 0.08:
            break
    if best is None:
        _dbg(f"fit-reject {item['text'][:60]!r}")
    return best


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


def _place_font(plate: np.ndarray, item: dict, key: str, score: float) -> dict | None:
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
    if abs(render_h - ink_h) / float(ink_h) > 0.12:
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
    used_chars = [(ch, shift_x + px) for ch, px in chars]
    used_shift_y = shift_y
    snapped = _snap_glyphs(mask, rendered, item["text"], chars)
    if snapped is not None and snapped["overlap"] > overlap + 0.02:
        placed_mask = snapped["mask"]
        overlap = float(snapped["overlap"])
        used_chars = snapped["chars"]
        used_shift_y = int(snapped["shift_y"])
        _dbg(f"snap {overlap:.2f} {key} {item['text'][:40]!r}")
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
    origin_x, origin_y = item["mask_origin"]
    plate_chars = [(ch, origin_x + px) for ch, px in used_chars]
    colour = _ink_cmyk(plate, item["mask_origin"], mask)
    return {
        "text": item["text"],
        "font": key,
        "match": round(float(score), 3),
        "overlap": round(float(overlap), 3),
        "fill": colour,
        "size_px": float(size),
        "baseline_px": float(origin_y + used_shift_y + baseline),
        "chars": plate_chars,
        "mask_origin": item["mask_origin"],
        "mask": mask,
        "core": item["core"],
        "rect": item["rect"],
        "protect": list(item.get("protect") or []),
        "render_mask": placed_mask,
    }


def _fit_metrics(key: str, text: str, ink_h: int, ink_w: int, size_hint: float | None = None) -> tuple[float, float]:
    import pymupdf as fitz

    from vector_text_v2 import _FONT_ROLE, font_path

    font = fitz.Font(fontfile=font_path(key))

    em = max(0.4, float(font.ascender) - float(font.descender))
    if size_hint is None:
        size = max(4.0, float(ink_h) / (em * 0.72))
    else:
        size = max(4.0, float(size_hint))
    natural = _text_width(font, text, size, 0.0)
    track = 0.0
    gaps = max(1, len(text) - 1)
    role = _FONT_ROLE.get(key, "body")
    # Display headings are tracked much wider than body copy. The overlap
    # check still rejects a face that only fills the width by stretching.
    cap = {"spaced": 0.62, "display": 0.45, "script": 0.18, "tagline": 0.30}.get(role, 0.22)
    if natural < ink_w * 0.98:
        track = min((ink_w - natural) / gaps, cap * size)
    elif natural > ink_w * 1.04 and natural > 0:
        size *= (ink_w / natural)
        track = 0.0
    return float(size), float(track)


def _text_width(font, text: str, size: float, track: float) -> float:
    total = 0.0
    for index, ch in enumerate(text):
        total += font.text_length(ch, fontsize=size)
        if index < len(text) - 1:
            total += track
    return total


def _render_layout(key: str, text: str, size: float, track: float):
    import pymupdf as fitz

    from vector_text_v2 import font_path

    path = font_path(key)
    if not os.path.exists(path):
        return None, 0.0, []
    font = fitz.Font(fontfile=path)
    width = _text_width(font, text, size, track)
    page_w = max(8, int(math_ceil(width)) + 6)
    page_h = max(8, int(math_ceil(size * 2.4)) + 4)
    doc = fitz.open()
    try:
        page = doc.new_page(width=page_w, height=page_h)
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
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    finally:
        doc.close()
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


def _ink_cmyk(plate: np.ndarray, origin, mask: np.ndarray):
    from vector_text_v2 import _cmyk

    ox, oy = int(origin[0]), int(origin[1])
    ys, xs = np.where(mask > 0)
    if xs.size < 4:
        return (0.0, 0.0, 0.0, 1.0)
    sample_y = np.clip(oy + ys, 0, plate.shape[0] - 1)
    sample_x = np.clip(ox + xs, 0, plate.shape[1] - 1)
    if sample_y.size > 800:
        step = int(sample_y.size / 800)
        sample_y = sample_y[::step]
        sample_x = sample_x[::step]
    pixels = plate[sample_y, sample_x].astype(np.float32)
    blue, green, red = [float(v) / 255.0 for v in np.median(pixels, axis=0)[:3]]
    return _cmyk((red, green, blue))


def _confirm_render(fitted: list) -> list:
    crops = []
    for item in fitted:
        mask = item["render_mask"]
        ys, xs = np.where(mask > 0)
        if xs.size < 8:
            crops.append(None)
            continue
        pad = 3
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(mask.shape[0], int(ys.max()) + 1 + pad)
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(mask.shape[1], int(xs.max()) + 1 + pad)
        crop = np.full((y1 - y0, x1 - x0, 3), 255, np.uint8)
        ink = mask[y0:y1, x0:x1] > 0
        crop[ink] = (0, 0, 0)
        crops.append(crop)
    reads = _read_crops(crops, words=False)
    kept = []
    for item, (text, score) in zip(fitted, reads):
        if score < WORD_SCORE_FLOOR or not _exact(text, item["text"]):
            _dbg(f"render-read {item['text'][:60]!r} -> {text[:60]!r} {score:.2f}")
            continue
        _dbg(f"accept {item['font']} overlap={item['overlap']} {item['text'][:70]!r}")
        kept.append(item)
    return kept


def _paint(plate: np.ndarray, items: list) -> None:
    from vector_trace import _fill_from_paper, _letter_erase_mask

    erase = np.zeros(plate.shape[:2], np.uint8)
    for item in items:
        mask = _letter_erase_mask(item["mask"])
        mask = _clip_protect(mask, item["mask_origin"], _protect_from(item, items))
        ox, oy = item["mask_origin"]
        height, width = mask.shape[:2]
        y1 = min(plate.shape[0], oy + height)
        x1 = min(plate.shape[1], ox + width)
        if y1 <= oy or x1 <= ox:
            continue
        view = erase[oy:y1, ox:x1]
        view[:] = np.maximum(view, mask[:y1 - oy, :x1 - ox])
    if int(erase.max()) == 0:
        return
    filled = _fill_from_paper(plate, erase)
    plate[:] = filled


def _protect_from(item: dict, items: list) -> list:
    protect = list(item.get("protect") or [])
    for other in items:
        if other is item:
            continue
        protect.append(other["core"])
    return protect


def _mark(raster_lines: list, text_gate, items: list) -> None:
    used_lines = set()
    used_gate = set()
    for item in items:
        for index, line in enumerate(raster_lines or []):
            if index in used_lines:
                continue
            if str(line.get("text") or "").strip() != item["text"]:
                continue
            if line.get("mode") == "vector":
                continue
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
            if str(row.get("text") or "").strip() != item["text"]:
                continue
            if row.get("mode") == "vector" and not row.get("retyped"):
                continue
            row["mode"] = "vector"
            row["render"] = item["text"]
            row["font"] = item["font"]
            row["ok"] = True
            row["glyphFail"] = False
            row["retyped"] = True
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
        writer = fitz.TextWriter(page.rect)
        baseline = float(item["baseline_px"]) * float(sy)
        for ch, px in item.get("chars") or []:
            writer.append((float(px) * float(sx), baseline), ch, font=font, fontsize=size)
        colour = tuple(float(channel) for channel in item.get("fill") or (0, 0, 0, 1))
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
