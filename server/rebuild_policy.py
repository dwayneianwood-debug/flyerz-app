"""Retyping is opt-in. A risky or damaged rebuild is refused, not printed."""

from __future__ import annotations

import cv2
import numpy as np

MAX_RETYPE_LINES = 15


def text_line_count(blocks: list) -> int:
    count = 0
    for block in blocks or []:
        text = str((block or {}).get("text") or "").strip()
        if not text:
            continue
        count += max(1, len([line for line in text.splitlines() if line.strip()]))
    return count


def _box_pixels(block: dict, width: int, height: int) -> tuple[int, int, int, int] | None:
    box = (block or {}).get("bbox") or []
    if len(box) < 4:
        return None
    x, y, bw, bh = [float(v) for v in box[:4]]
    if max(x, y, bw, bh) <= 1.2:
        x, y, bw, bh = x * width, y * height, bw * width, bh * height
    x0 = int(round(x))
    y0 = int(round(y))
    x1 = int(round(x + bw))
    y1 = int(round(y + bh))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _stroke_cv(bgr: np.ndarray, block: dict) -> float | None:
    height, width = bgr.shape[:2]
    pixels = _box_pixels(block, width, height)
    if pixels is None:
        return None
    x0, y0, x1, y1 = pixels
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    roi = bgr[y0:y1, x0:x1]
    if roi.size < 40:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    med = float(np.median(gray))
    ink = gray < med - 20 if med > 90 else gray > med + 20
    if float(ink.mean()) < 0.03 or float(ink.mean()) > 0.65:
        return None
    dist = cv2.distanceTransform(ink.astype(np.uint8) * 255, cv2.DIST_L2, 3)
    widths = dist[ink]
    if widths.size < 12:
        return None
    mean = float(widths.mean())
    if mean < 0.4:
        return None
    return float(widths.std() / mean)


def looks_decorative(bgr: np.ndarray, blocks: list) -> bool:
    """Script, serif, and drawn wordmarks have uneven stroke width. A plain sans word does not."""
    scores = []
    for block in (blocks or [])[:16]:
        score = _stroke_cv(bgr, block)
        if score is not None:
            scores.append(score)
    if not scores:
        return False
    return float(np.median(scores)) >= 0.72


def overlapping_or_overflowing(blocks: list) -> bool:
    rects = []
    for block in blocks or []:
        box = (block or {}).get("bbox") or []
        if len(box) < 4 or not str((block or {}).get("text") or "").strip():
            continue
        x, y, bw, bh = [float(v) for v in box[:4]]
        if max(abs(x), abs(y), abs(bw), abs(bh)) > 1.2:
            return True
        if x < -0.01 or y < -0.01 or x + bw > 1.01 or y + bh > 1.01:
            return True
        rects.append((x, y, x + bw, y + bh))
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            overlap = ix * iy
            area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
            if overlap / area > 0.12:
                return True
    return False


def retype_refusal(blocks: list, bgr: np.ndarray | None = None) -> list[str]:
    """Reasons to refuse a retype. Empty means a preview may be attempted."""
    reasons = []
    lines = text_line_count(blocks)
    if lines > MAX_RETYPE_LINES:
        reasons.append(
            f"This artwork has about {lines} lines of text. "
            f"Retyping is refused above {MAX_RETYPE_LINES} lines so the design is not damaged."
        )
    if bgr is not None and looks_decorative(bgr, blocks):
        reasons.append(
            "The lettering looks like script, serif, or a decorative font. "
            "Retyping would swap it for a plain font, so it was refused."
        )
    if overlapping_or_overflowing(blocks):
        reasons.append("The retyped lines would overlap or overflow the page. Retyping was refused.")
    return reasons


def design_damage_reason(original: np.ndarray, rendered: np.ndarray, blocks: list) -> str:
    """Compare a rebuild with the upscaled original. A bad result is discarded."""
    if original is None or rendered is None or original.size == 0 or rendered.size == 0:
        return "The rebuild could not be compared with the original, so it was discarded."
    if overlapping_or_overflowing(blocks):
        return "Retyped lines overlap or overflow, so the rebuild was discarded."
    height, width = original.shape[:2]
    view = rendered
    if rendered.shape[0] != height or rendered.shape[1] != width:
        view = cv2.resize(rendered, (width, height), interpolation=cv2.INTER_AREA)
    mask = np.zeros((height, width), np.uint8)
    for block in blocks or []:
        pixels = _box_pixels(block, width, height)
        if pixels is None:
            continue
        x0, y0, x1, y1 = pixels
        pad = 2
        cv2.rectangle(
            mask,
            (max(0, x0 - pad), max(0, y0 - pad)),
            (min(width - 1, x1 + pad), min(height - 1, y1 + pad)),
            255,
            -1,
        )
    outside = mask == 0
    if int(outside.sum()) > 100:
        delta = cv2.absdiff(original, view)
        outside_diff = float(delta[outside].mean())
        if outside_diff > 14:
            return (
                "The rebuild changed the picture outside the words, so it was discarded. "
                "The upscaled original will be used."
            )
    gray_o = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
    gray_r = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
    for block in blocks or []:
        pixels = _box_pixels(block, width, height)
        if pixels is None or not str((block or {}).get("text") or "").strip():
            continue
        x0, y0, x1, y1 = pixels
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        orig = gray_o[y0:y1, x0:x1]
        made = gray_r[y0:y1, x0:x1]
        orig_edge = float(cv2.Laplacian(orig, cv2.CV_64F).var())
        made_edge = float(cv2.Laplacian(made, cv2.CV_64F).var())
        if orig_edge > 60 and made_edge < orig_edge * 0.28:
            return "A line of lettering was lost in the rebuild, so it was discarded. The upscaled original will be used."
    return ""
