#!/usr/bin/env python3
"""Vector text rebuild v2.

Quick mode uses this for AI raster artwork. The picture is enlarged to at least
400 pixels per inch, text is removed with a tight Telea mask, and each OCR line
is set again as embedded vector type fitted to its original width. Icons stay
in the picture. A line that cannot be read, or a page that fails the check,
falls back to the enlarged picture and an amber flag.

The press page is trim plus 5 mm bleed. The trim box sits 5 mm inside the
bleed box. Fonts are subset. Open-licence faces live in server/fonts/v2
(SIL OFL, see OFL.txt).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BLEED_MM = 5.0
MIN_PPI = 400
MM_TO_PT = 72.0 / 25.4
DETECT_LONG_EDGE = 1280
QA_LONG_EDGE = 1100
MATCH_FLOOR = 0.18
SCRIPT_OCR_FLOOR = 0.75
RECALL_FLOOR = 0.60
OCR_CACHE = os.environ.get("VECTOR_OCR_CACHE", "/tmp/flyerz-ocr-cache")

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "v2")

# key, filename, role. Roles pick the shortlist and the tracking limit.
FONTS = (
    ("cinzel-500", "Cinzel-500.ttf", "spaced"),
    ("cinzel-600", "Cinzel-600.ttf", "spaced"),
    ("cinzel-700", "Cinzel-700.ttf", "spaced"),
    ("eb-semibold", "EBGaramond-SemiBold.ttf", "serif"),
    ("crimson", "CrimsonText-Regular.ttf", "body"),
    ("eb-italic", "EBGaramond-Italic.ttf", "tagline"),
    ("parisienne", "Parisienne-Regular.ttf", "script"),
    ("greatvibes", "GreatVibes-Regular.ttf", "script"),
    ("allura", "Allura-Regular.ttf", "script"),
    ("pinyon", "PinyonScript-Regular.ttf", "script"),
    ("montserrat", "Montserrat-700.ttf", "sans"),
    ("poppins", "Poppins-Bold.ttf", "sans"),
    ("poppins-regular", "Poppins-Regular.ttf", "sans"),
    ("inter", "Inter-500.ttf", "sans"),
    ("marcellus", "Marcellus-Regular.ttf", "display"),
    ("cormorant-sc", "CormorantSC-Medium.ttf", "spaced"),
    ("cormorant", "CormorantGaramond-600.ttf", "serif"),
    ("libre", "LibreBaskerville-Regular.ttf", "body"),
)

# Approved faces. A serif page never leaves this set. Sans is only for a page
# whose lettering has no serif feet and clearly matches a sans face.
SANS_KEYS = {"montserrat", "poppins", "poppins-regular", "inter"}
ROLE_FONT = {
    "caps": "cinzel-600",
    "head": "eb-semibold",
    "body": "crimson",
    "tagline": "eb-italic",
    "script": "parisienne",
    "phone": "crimson",
    "sans-caps": "montserrat",
    "sans-body": "poppins-regular",
}
SCRIPT_KEYS = ("parisienne", "greatvibes", "allura", "pinyon")
THICKEN = {"crimson", "libre"}

_FONT_FILES = {key: os.path.join(FONT_DIR, name) for key, name, _role in FONTS}
_FONT_ROLE = {key: role for key, _name, role in FONTS}
_FITZ_FONTS: dict = {}
_PIL_FONTS: dict = {}


def font_path(key: str) -> str:
    return _FONT_FILES[key]


def rebuild_fitted(
    bgr: np.ndarray,
    trim_w_mm: float,
    trim_h_mm: float,
    output_pdf: str,
    bleed_mm: float = BLEED_MM,
    progress: Optional[Callable[[str, str], None]] = None,
    blocks: Optional[list] = None,
    reocr: Optional[Callable[[str], str]] = None,
    recall_floor: float = RECALL_FLOOR,
) -> dict:
    """Build a vector-text press PDF. Never raises. ok False means fall back."""
    started = time.perf_counter()
    try:
        return _rebuild(
            bgr, float(trim_w_mm), float(trim_h_mm), output_pdf,
            float(bleed_mm), progress, blocks, reocr, float(recall_floor), started,
        )
    except Exception as exc:
        return _fail(started, f"Vector type failed ({str(exc)[:160]}). The original lettering was kept.")


def _fail(started: float, reason: str, **extra) -> dict:
    payload = {
        "ok": False,
        "amber": True,
        "reason": reason,
        "decisions": [reason],
        "elapsed_s": round(time.perf_counter() - started, 3),
        "lines": [],
        "qa": {},
        "provider": extra.get("provider") or "",
    }
    payload.update(extra)
    return payload


def _note(progress, stage: str, note: str) -> None:
    if progress:
        try:
            progress(stage, note)
        except Exception:
            pass


def _rebuild(bgr, trim_w, trim_h, output_pdf, bleed_mm, progress, blocks, reocr, recall_floor, started) -> dict:
    _note(progress, "reading", "Reading the lettering.")
    ocr_started = time.perf_counter()
    if blocks is None:
        blocks = read_blocks(bgr)
    ocr_s = time.perf_counter() - ocr_started

    from ai_rebuild import keep_word_blocks

    kept, dropped = keep_word_blocks(blocks or [])
    rescued, dropped = _rescue_lines(bgr, dropped)
    kept = list(kept) + rescued
    raster_lines = [_line_record(block, "raster", "The read was too uncertain to set as type.") for block in dropped]
    style = _page_style(bgr, kept)
    ordinary = []
    for block in kept:
        if _is_wordmark(block, kept):
            record = _line_record(block, "raster", "Large logo lettering stayed in the picture.")
            record["kept_on_purpose"] = True
            raster_lines.append(record)
            continue
        ordinary.append(block)
    chosen = []
    for block in ordinary:
        decision = _choose_font(bgr, block, style)
        if decision.get("mode") == "vector":
            chosen.append(decision)
        else:
            raster_lines.append(decision)
    chosen = _harmonise(chosen, style)

    if not chosen:
        return _fail(
            started,
            "No line was confident enough to set as type, so the original lettering was kept.",
            lines=raster_lines,
            timings={"ocr_s": round(ocr_s, 3)},
        )

    _note(progress, "enlarging", "Enlarging the picture and setting the type.")
    enlarge_started = time.perf_counter()
    trim_px_w, trim_px_h = _trim_pixels(trim_w, trim_h, MIN_PPI)
    sharp, provider = _enlarge(bgr, trim_px_w, trim_px_h)
    colour_source, scale, off_x, off_y = _cover(bgr, trim_px_w, trim_px_h, cv2.INTER_CUBIC)
    if sharp.shape[0] != trim_px_h or sharp.shape[1] != trim_px_w:
        sharp, scale, off_x, off_y = _cover(sharp, trim_px_w, trim_px_h, cv2.INTER_LANCZOS4)
        colour_source, scale, off_x, off_y = _cover(bgr, trim_px_w, trim_px_h, cv2.INTER_CUBIC)
    restored = _restore_colour(sharp, colour_source)
    bleed_px = max(1, int(round(bleed_mm / 25.4 * MIN_PPI)))
    from quick_print import _extend_edges

    media = _extend_edges(restored, bleed_px, bleed_px, bleed_px, bleed_px)
    enlarge_s = time.perf_counter() - enlarge_started

    for line in chosen:
        line["media_box"] = _map_rect(line["rect"], scale, off_x, off_y, bleed_px, media.shape)

    chosen = _drop_overlaps(chosen, raster_lines)
    if not chosen:
        return _fail(started, "The lines overlapped, so the original lettering was kept.", provider=provider)

    paint_started = time.perf_counter()
    painted, bullets, blotches = _remove_text(media, chosen)
    for line in blotches:
        raster_lines.append(line)
    chosen = [line for line in chosen if not line.get("blotch")]
    paint_s = time.perf_counter() - paint_started
    if not chosen:
        return _fail(
            started,
            "Removing the old lettering marked the picture, so the original lettering was kept.",
            provider=provider,
            lines=raster_lines,
        )

    type_started = time.perf_counter()
    qa = _write_pdf(painted, chosen, bullets, output_pdf, trim_w, trim_h, bleed_mm)
    type_s = time.perf_counter() - type_started
    if not qa.get("wrote"):
        return _fail(started, qa.get("reason") or "The vector press file could not be written.", provider=provider)

    qa_started = time.perf_counter()
    proof_text = reocr(output_pdf) if reocr else _reocr_pdf(output_pdf)
    expected = " ".join(line["text"] for line in chosen)
    recall = _recall(expected, proof_text)
    qa["recall"] = round(recall, 3)
    qa["reocr"] = (proof_text or "")[:400]
    qa_s = time.perf_counter() - qa_started
    structural = qa.get("cmyk") and qa.get("boxes") and qa.get("fonts") and qa.get("ppi")
    if (not structural) or recall < recall_floor:
        _discard(output_pdf)
        reason = "The vector check failed, so the original lettering was kept."
        if not qa.get("cmyk"):
            reason = "The press file was not CMYK, so the original lettering was kept."
        elif not qa.get("boxes"):
            reason = "The trim box was wrong, so the original lettering was kept."
        elif not qa.get("fonts"):
            reason = "The fonts were not embedded, so the original lettering was kept."
        elif not qa.get("ppi"):
            reason = "The picture was under 400 PPI, so the original lettering was kept."
        return _fail(started, reason, provider=provider, qa=qa, lines=chosen + raster_lines)

    uncertain = [line for line in raster_lines if not line.get("kept_on_purpose")]
    amber = bool(uncertain)
    reason = ""
    if amber:
        reason = "Some lettering stayed in the picture because the read was uncertain. Glance at it before printing."
    decisions = [
        f"The lettering was set as vector type ({len(chosen)} lines).",
        f"The picture was enlarged with {provider} and the original colours were put back.",
        "The press file is CMYK at 400 PPI or more, with the trim 5 mm inside the bleed.",
    ]
    if raster_lines:
        decisions.append(reason)
    timings = {
        "ocr_s": round(ocr_s, 3),
        "enlarge_s": round(enlarge_s, 3),
        "paint_s": round(paint_s, 3),
        "typeset_s": round(type_s, 3),
        "qa_s": round(qa_s, 3),
        "total_s": round(time.perf_counter() - started, 3),
    }
    return {
        "ok": True,
        "amber": amber,
        "reason": reason,
        "decisions": decisions,
        "elapsed_s": timings["total_s"],
        "timings": timings,
        "lines": [_public_line(line) for line in chosen] + [_public_line(line) for line in raster_lines],
        "qa": qa,
        "provider": provider,
        "pdf": output_pdf,
        "vector_lines": len(chosen),
        "raster_lines": len(raster_lines),
    }


def _public_line(line: dict) -> dict:
    return {
        "text": line.get("text") or "",
        "mode": line.get("mode") or "",
        "font": line.get("font") or "",
        "role": line.get("role") or "",
        "match": line.get("match"),
        "reason": line.get("reason") or "",
    }


def _line_record(block: dict, mode: str, reason: str) -> dict:
    return {
        "text": str(block.get("text") or ""),
        "mode": mode,
        "font": "",
        "match": None,
        "reason": reason,
        "score": block.get("score"),
        "bbox": block.get("bbox"),
    }


def read_blocks(bgr: np.ndarray) -> list:
    """One OCR pass on a downscaled copy. Results are cached by picture hash."""
    from ai_rebuild import _block, _space_blocks
    from ocr_reader import local_rows

    small, back = _downscale(bgr, DETECT_LONG_EDGE)
    key = hashlib.sha256(small.tobytes()).hexdigest()[:32]
    os.makedirs(OCR_CACHE, exist_ok=True)
    cache_path = os.path.join(OCR_CACHE, key + ".json")
    rows = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                rows = json.load(handle)
        except Exception:
            rows = None
    if not isinstance(rows, list):
        raw = local_rows(small) or []
        rows = []
        for item in raw:
            box = item[0]
            text = str(item[1] or "")
            score = float(item[2] if len(item) > 2 else 0)
            points = [[float(point[0]), float(point[1])] for point in box]
            rows.append({"box": points, "text": text, "score": score})
        try:
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump(rows, handle)
        except Exception:
            pass
    height, width = bgr.shape[:2]
    blocks = []
    for index, item in enumerate(rows):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        points = item.get("box") or []
        if len(points) < 4:
            continue
        xs = [float(point[0]) * back for point in points]
        ys = [float(point[1]) * back for point in points]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        blocks.append(_block(
            index, text, x0, y0, max(2.0, x1 - x0), max(2.0, y1 - y0),
            width, height, bgr, float(item.get("score") or 0),
        ))
    return _repair_short(bgr, _refine_blocks(bgr, _space_blocks(blocks, bgr)))


def _repair_short(bgr: np.ndarray, blocks: list) -> list:
    """A second read of short lines. Page OCR misreads script such as The Surface."""
    from ocr_reader import local_rows

    indexes = [
        index for index, block in enumerate(blocks)
        if 1 <= len(str(block.get("text") or "").split()) <= 3
    ]
    if not indexes:
        return blocks
    reads = _reread_many(bgr, [blocks[index] for index in indexes], local_rows)
    for index, (new_text, new_score) in zip(indexes, reads):
        block = blocks[index]
        old_text = str(block.get("text") or "")
        try:
            old_score = float(block.get("score") or 0)
        except (TypeError, ValueError):
            old_score = 0.0
        old_core = re.sub(r"[^A-Za-z0-9]", "", old_text)
        new_core = re.sub(r"[^A-Za-z0-9]", "", new_text or "")
        if (
            new_text
            and new_score >= 0.97
            and new_score >= old_score
            and len(new_text.split()) >= len(old_text.split())
            and len(new_core) >= max(4, int(len(old_core) * 0.8))
            and " ".join(new_text.split()).upper() != " ".join(old_text.split()).upper()
        ):
            block["text"] = new_text
            block["score"] = round(float(new_score), 3)
    return blocks


def _refine_blocks(bgr: np.ndarray, blocks: list) -> list:
    """One more OCR pass over the line crops, batched onto a single strip.

    The page-level read is fast and sometimes joins two words. A crop that
    reads more cleanly replaces that line.
    """
    from ocr_reader import local_rows

    if not blocks:
        return blocks
    pad = 10
    prepared = []
    for block in blocks:
        rect = _rect(block, bgr.shape[1], bgr.shape[0])
        crop = _safe_crop(bgr, rect)
        if crop is None or crop.shape[0] < 6 or crop.shape[1] < 6:
            prepared.append(None)
            continue
        scale = 72.0 / float(crop.shape[0])
        resized = cv2.resize(
            crop,
            (max(24, int(round(crop.shape[1] * scale))), 72),
            interpolation=cv2.INTER_CUBIC,
        )
        prepared.append(resized)
    usable = [crop for crop in prepared if crop is not None]
    if not usable:
        return blocks
    width = min(1400, max(crop.shape[1] for crop in usable) + pad * 2)
    height = pad + sum(crop.shape[0] + pad for crop in usable)
    canvas = np.full((height, width, 3), 255, np.uint8)
    spans = []
    cursor = pad
    for crop in prepared:
        if crop is None:
            spans.append(None)
            continue
        if crop.shape[1] > width - pad * 2:
            crop = cv2.resize(crop, (width - pad * 2, crop.shape[0]), interpolation=cv2.INTER_AREA)
        x = max(pad, (width - crop.shape[1]) // 2)
        canvas[cursor:cursor + crop.shape[0], x:x + crop.shape[1]] = crop
        spans.append((cursor, cursor + crop.shape[0]))
        cursor += crop.shape[0] + pad
    try:
        rows = local_rows(canvas) or []
    except Exception:
        return blocks
    grouped: list[list] = [[] for _ in spans]
    for item in rows:
        box = item[0]
        if box is None or len(box) == 0:
            continue
        center = sum(float(point[1]) for point in box) / float(len(box))
        for index, span in enumerate(spans):
            if span and span[0] - 2 <= center <= span[1] + 2:
                grouped[index].append(item)
                break
    for block, found in zip(blocks, grouped):
        if not found:
            continue
        found.sort(key=lambda item: min(float(point[0]) for point in item[0]))
        new_text = " ".join(str(item[1] or "").strip() for item in found if str(item[1] or "").strip())
        scores = [float(item[2] if len(item) > 2 else 0) for item in found]
        new_score = min(scores) if scores else 0.0
        old_text = str(block.get("text") or "")
        try:
            old_score = float(block.get("score") or 0)
        except (TypeError, ValueError):
            old_score = 0.0
        old_core = re.sub(r"[^A-Za-z0-9]", "", old_text)
        new_core = re.sub(r"[^A-Za-z0-9]", "", new_text)
        old_spaced = " ".join(old_text.split()).upper()
        new_spaced = " ".join(new_text.split()).upper()
        if (
            new_text
            and new_score >= 0.9
            and new_score >= old_score
            and len(new_core) >= max(4, int(len(old_core) * 0.8))
            and new_spaced != old_spaced
            and (len(new_text.split()) >= len(old_text.split()) or new_score >= 0.99)
        ):
            block["text"] = new_text
            block["score"] = round(new_score, 3)
    return blocks


def _downscale(bgr: np.ndarray, long_edge: int) -> tuple[np.ndarray, float]:
    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest <= long_edge:
        return bgr, 1.0
    scale = long_edge / float(longest)
    small = cv2.resize(bgr, (max(1, int(round(width * scale))), max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA)
    back = width / float(small.shape[1])
    return small, back


def _letters(text: str) -> list:
    return [ch for ch in str(text or "") if ch.isalpha()]


def _upper_ratio(text: str) -> float:
    letters = _letters(text)
    if not letters:
        return 0.0
    return sum(ch.isupper() for ch in letters) / float(len(letters))


def _is_phone(text: str) -> bool:
    raw = " ".join(str(text or "").split())
    digits = re.sub(r"\D", "", raw)
    return 7 <= len(digits) <= 15 and len(_letters(raw)) <= 2


def _rescue_kind(text: str) -> str:
    """Lines the word filter drops, but a second read can still set."""
    raw = " ".join(str(text or "").split())
    if not raw or len(re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", raw)) <= 1:
        return ""
    if _is_phone(raw):
        return "phone"
    words = raw.split()
    letters = _letters(raw)
    if 1 <= len(words) <= 3 and 2 <= len(letters) <= 24 and raw[0].isupper() and _upper_ratio(raw) < 0.85:
        return "name"
    if len(letters) >= 8 and _upper_ratio(raw) >= 0.65:
        return "letters"
    return ""


def _rescue_lines(bgr: np.ndarray, dropped: list) -> tuple[list, list]:
    """Second chance for a name, a phone number, and a caps line the filter skipped."""
    from ocr_reader import local_rows

    rescued = []
    still = []
    pending = []
    for block in dropped or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        kind = _rescue_kind(text)
        if not kind:
            still.append(block)
            continue
        pending.append((block, kind, text))
    if not pending:
        return rescued, still
    reads = _reread_many(bgr, [block for block, _kind, _text in pending], local_rows)
    for (block, kind, text), found in zip(pending, reads):
        new_text, new_score = found
        if kind == "phone":
            chosen = text
            if new_text and _is_phone(new_text) and new_score >= 0.5:
                chosen = new_text
            block = dict(block)
            block["text"] = chosen
            block["score"] = max(float(block.get("score") or 0), new_score, 0.8)
            block["rescued"] = "phone"
            rescued.append(block)
            continue
        if kind == "name":
            block = dict(block)
            if new_text and new_score >= 0.55 and 1 <= len(new_text.split()) <= 4:
                block["text"] = new_text
                block["score"] = new_score
            block["score"] = max(float(block.get("score") or 0), 0.72)
            block["rescued"] = "name"
            rescued.append(block)
            continue
        if new_text and new_score >= 0.88 and len(_letters(new_text)) >= 6:
            block = dict(block)
            block["text"] = new_text
            block["score"] = new_score
            block["rescued"] = "letters"
            rescued.append(block)
            continue
        try:
            old_score = float(block.get("score") or 0)
        except (TypeError, ValueError):
            old_score = 0.0
        if old_score >= 0.9 and len(_letters(text)) >= 8:
            block = dict(block)
            block["score"] = old_score
            block["rescued"] = "letters"
            rescued.append(block)
            continue
        still.append(block)
    return rescued, still


def _reread_many(bgr: np.ndarray, blocks: list, local_rows) -> list:
    pad = 8
    prepared = []
    for block in blocks:
        rect = _rect(block, bgr.shape[1], bgr.shape[0])
        crop = _safe_crop(bgr, rect)
        if crop is None or crop.shape[0] < 6:
            prepared.append(None)
            continue
        scale = 96.0 / float(crop.shape[0])
        prepared.append(cv2.resize(
            crop,
            (max(24, int(round(crop.shape[1] * scale))), 96),
            interpolation=cv2.INTER_CUBIC,
        ))
    if not any(crop is not None for crop in prepared):
        return [("", 0.0) for _ in blocks]
    width = min(1600, max(crop.shape[1] for crop in prepared if crop is not None) + pad * 2)
    height = pad + sum((crop.shape[0] + pad) if crop is not None else pad for crop in prepared)
    canvas = np.full((height, width, 3), 255, np.uint8)
    spans = []
    cursor = pad
    for crop in prepared:
        if crop is None:
            spans.append(None)
            continue
        if crop.shape[1] > width - pad * 2:
            crop = cv2.resize(crop, (width - pad * 2, crop.shape[0]), interpolation=cv2.INTER_AREA)
        x = max(pad, (width - crop.shape[1]) // 2)
        canvas[cursor:cursor + crop.shape[0], x:x + crop.shape[1]] = crop
        spans.append((cursor, cursor + crop.shape[0]))
        cursor += crop.shape[0] + pad
    try:
        rows = local_rows(canvas) or []
    except Exception:
        return [("", 0.0) for _ in blocks]
    grouped = [[] for _ in spans]
    for item in rows:
        box = item[0]
        if box is None or len(box) == 0:
            continue
        center = sum(float(point[1]) for point in box) / float(len(box))
        for index, span in enumerate(spans):
            if span and span[0] - 2 <= center <= span[1] + 2:
                grouped[index].append(item)
                break
    found = []
    for items in grouped:
        if not items:
            found.append(("", 0.0))
            continue
        items.sort(key=lambda item: min(float(point[0]) for point in item[0]))
        text = " ".join(str(item[1] or "").strip() for item in items if str(item[1] or "").strip())
        score = min(float(item[2] if len(item) > 2 else 0) for item in items)
        found.append((text, score))
    return found


def _page_style(bgr: np.ndarray, blocks: list) -> str:
    """Serif unless the lettering has no feet and a sans face is clearly closer."""
    feet = []
    sans_wins = 0
    checked = 0
    for block in blocks or []:
        text = str(block.get("text") or "").strip()
        if len(_letters(text)) < 6 or _is_phone(text):
            continue
        rect = _rect(block, bgr.shape[1], bgr.shape[0])
        crop = _safe_crop(bgr, rect)
        ink = _ink_mask(crop, block.get("color_hex") or "") if crop is not None else None
        if ink is None:
            continue
        feet.append(_serif_feet(ink))
        serif_key = "cinzel-600" if _upper_ratio(text) >= 0.72 else "crimson"
        sans_key = "montserrat" if serif_key == "cinzel-600" else "poppins-regular"
        serif = _score_key(ink, text, serif_key)
        sans = _score_key(ink, text, sans_key)
        checked += 1
        if sans > serif + 0.12:
            sans_wins += 1
        if checked >= 8:
            break
    if not feet:
        return "serif"
    median_feet = float(np.median(feet))
    if median_feet < 0.92 and checked and sans_wins >= max(2, int(checked * 0.6)):
        return "sans"
    return "serif"


def _serif_feet(ink: np.ndarray) -> float:
    """Wider ink at the top and baseline than in the stem means a serif."""
    source = _tight_binary(ink)
    if source is None:
        return 1.0
    height = source.shape[0]
    if height < 8:
        return 1.0
    row = (source > 40).sum(axis=1).astype(np.float32)
    mid = float(row[int(height * 0.35):int(height * 0.65)].mean() or 0)
    if mid < 1:
        return 1.0
    top = float(row[:max(2, int(height * 0.18))].mean())
    bot = float(row[int(height * 0.82):].mean())
    return (top + bot) / 2.0 / mid


def _is_wordmark(block: dict, blocks: list) -> bool:
    """A single large word, such as the script logo, stays in the picture."""
    text = " ".join(str(block.get("text") or "").split())
    if len(text.split()) != 1 or len(_letters(text)) < 4:
        return False
    box = block.get("bbox") or [0, 0, 0, 0]
    if len(box) < 4:
        return False
    height = float(box[3])
    heights = []
    for other in blocks or []:
        other_box = other.get("bbox") or [0, 0, 0, 0]
        if len(other_box) >= 4 and float(other_box[3]) > 0:
            heights.append(float(other_box[3]))
    if not heights:
        return height >= 0.06
    heights.sort()
    median = heights[len(heights) // 2]
    # The script logo is in a class of its own. A large tagline is not a logo.
    return height >= 0.082 and height >= median * 2.6


def _script_allowed(text: str, ink: np.ndarray) -> bool:
    words = str(text or "").split()
    if not words or len(words) > 3 or _is_phone(text):
        return False
    if len(words) >= 3 and any(word.lower() in {"and", "with", "for", "your"} for word in words):
        return False
    return _scripty(ink, text)


def _tagline_hint(text: str) -> bool:
    raw = " ".join(str(text or "").split())
    if not raw or len(raw.split()) > 12:
        return False
    low = raw.lower().rstrip(".,;:")
    cues = (
        "see your health",
        "live your best",
        "because you deserve",
        "empowering you",
        "invest in your health",
        "when you know better",
        "your health journey",
    )
    return any(low.startswith(cue) for cue in cues)


def _choose_font(bgr: np.ndarray, block: dict, style: str = "serif") -> dict:
    text = str(block.get("text") or "").strip()
    rect = _rect(block, bgr.shape[1], bgr.shape[0])
    crop = _safe_crop(bgr, rect)
    rescued = str(block.get("rescued") or "")
    record = {
        "text": text,
        "mode": "raster",
        "font": "",
        "role": "",
        "match": 0.0,
        "reason": "",
        "score": block.get("score"),
        "bbox": block.get("bbox"),
        "rect": rect,
        "color": block.get("color_hex") or "#222222",
    }
    try:
        ocr_score = float(block.get("score") or 0)
    except (TypeError, ValueError):
        ocr_score = 0.0
    ink = _ink_mask(crop, record["color"]) if crop is not None else None
    if ink is None:
        record["reason"] = "The letters could not be separated from the picture."
        return record
    if _is_phone(text) or rescued == "phone":
        key = "poppins-regular" if style == "sans" else "crimson"
        record.update(mode="vector", font=key, role="phone", match=0.5)
        return record
    if _scripty(ink, text) and ocr_score < SCRIPT_OCR_FLOOR and rescued != "name":
        record["reason"] = "Script lettering was hard to read, so it stayed in the picture."
        return record
    role, key, score = _assign_role(text, ink, style, rescued)
    record["match"] = round(float(score), 3)
    record["role"] = role
    confident = ocr_score >= 0.8 or rescued in ("name", "phone", "letters")
    if role == "script" and ocr_score < SCRIPT_OCR_FLOOR and rescued != "name":
        record["reason"] = "Script lettering was hard to read, so it stayed in the picture."
        return record
    script_ok = role == "script" and score >= 0.12 and (confident or ocr_score >= SCRIPT_OCR_FLOOR)
    plain_ok = role != "script" and confident and score > -0.05
    if score < MATCH_FLOOR and rescued not in ("name", "phone", "letters") and not script_ok and not plain_ok:
        record["reason"] = "No font matched this line closely enough."
        return record
    record["mode"] = "vector"
    record["font"] = key
    return record


def _assign_role(text: str, ink: np.ndarray, style: str, rescued: str) -> tuple[str, str, float]:
    if style == "sans" and _upper_ratio(text) >= 0.72 and len(_letters(text)) >= 3:
        score = _score_key(ink, text, "montserrat")
        return "sans-caps", "montserrat", score
    if style == "sans":
        score = _score_key(ink, text, "poppins-regular")
        return "sans-body", "poppins-regular", score
    if _upper_ratio(text) >= 0.72 and len(_letters(text)) >= 3:
        # Every caps line on a serif page uses one Cinzel. A low score still sets it
        # when the read itself is confident.
        score = _score_key(ink, text, "cinzel-600")
        return "caps", "cinzel-600", score
    words = text.split()
    if len(words) <= 2 and _upper_ratio(text) < 0.72 and not _is_phone(text):
        body = _score_key(ink, text, "crimson")
        best_key, best = _best_script(ink, text)
        # A real script word beats the body serif. A list item does not.
        if best >= body + 0.07 and best >= 0.12:
            return "script", best_key, best
    if _script_allowed(text, ink) or (rescued == "name" and _scripty(ink, text)):
        body = _score_key(ink, text, "crimson")
        best_key, best = _best_script(ink, text)
        if best >= body + 0.07 and best >= 0.12:
            return "script", best_key, best
    if _tagline_hint(text) or (rescued == "name" and not _scripty(ink, text)):
        italic = _score_key(ink, text, "eb-italic")
        body = _score_key(ink, text, "crimson")
        if italic + 0.02 >= body:
            return "tagline", "eb-italic", italic
        return "body", "crimson", body
    # Body copy stays one weight. Short list lines are not promoted to a bold head.
    score = _score_key(ink, text, "crimson")
    return "body", "crimson", score


def _best_script(ink: np.ndarray, text: str) -> tuple[str, float]:
    scores = {key: _score_key(ink, text, key) for key in SCRIPT_KEYS}
    best_key = max(scores, key=lambda key: scores[key])
    # Parisienne is the house script. Another face has to win clearly.
    if best_key != "parisienne" and scores[best_key] < scores.get("parisienne", 0) + 0.08:
        best_key = "parisienne"
    return best_key, scores[best_key]


def _score_key(ink: np.ndarray, text: str, key: str) -> float:
    path = _FONT_FILES.get(key)
    if not path or not os.path.exists(path):
        return 0.0
    return _score_font(ink, text, path, _FONT_ROLE.get(key, "body"))


def _harmonise(lines: list, style: str) -> list:
    """One face per role and size, and one weight inside a paragraph."""
    if style == "serif":
        for line in lines:
            role = line.get("role") or "body"
            if role == "script":
                continue
            if line.get("font") in SANS_KEYS or role in ROLE_FONT:
                line["font"] = ROLE_FONT.get(role, "crimson")
    scripts = [line for line in lines if line.get("role") == "script"]
    if len(scripts) >= 2:
        winner = max(
            set(item.get("font") or "parisienne" for item in scripts),
            key=lambda font: sum(1 for item in scripts if item.get("font") == font),
        )
        for item in scripts:
            item["font"] = winner
    bands: dict = {}
    for line in lines:
        height = max(8, int(line["rect"][3]))
        band = int(round(height / 6.0))
        bands.setdefault((line.get("role") or "", band), []).append(line)
    for group in bands.values():
        if len(group) < 2:
            continue
        role = group[0].get("role") or ""
        if role == "script":
            continue
        winner = ROLE_FONT.get(role, group[0].get("font") or "crimson")
        for item in group:
            item["font"] = winner
    _normalise_paragraphs(lines, style)
    return lines


def _normalise_paragraphs(lines: list, style: str) -> None:
    ordered = sorted(lines, key=lambda line: (line["rect"][1], line["rect"][0]))
    seen = set()
    for index, line in enumerate(ordered):
        if id(line) in seen or line.get("role") in ("script", "caps", "phone", "sans-caps"):
            seen.add(id(line))
            continue
        block = [line]
        seen.add(id(line))
        _x, y, _w, height = line["rect"]
        bottom = y + height
        left = line["rect"][0]
        for other in ordered[index + 1:]:
            if other.get("role") in ("script", "caps", "phone", "sans-caps"):
                break
            ox, oy, _ow, oh = other["rect"]
            if abs(ox - left) > max(14, height * 0.9):
                continue
            if oy - bottom > max(height, oh) * 0.9:
                break
            block.append(other)
            seen.add(id(other))
            bottom = oy + oh
            height = oh
        if len(block) < 2:
            continue
        short = all(len(str(item.get("text") or "").split()) <= 8 for item in block)
        font = "poppins-regular" if style == "sans" else "crimson"
        if style != "sans" and all(item.get("role") == "head" for item in block):
            font = "eb-semibold"
        elif style != "sans" and short and any(item.get("role") == "tagline" for item in block):
            font = "eb-italic"
        elif style != "sans" and any(item.get("role") == "tagline" for item in block):
            for item in block:
                if item.get("role") != "tagline":
                    item["font"] = "crimson"
                    item["role"] = "body"
            continue
        for item in block:
            item["font"] = font
            if font == "crimson":
                item["role"] = "body"
            elif font == "eb-semibold":
                item["role"] = "head"
            elif font == "eb-italic":
                item["role"] = "tagline"


def _rect(block: dict, width: int, height: int) -> tuple[int, int, int, int]:
    box = block.get("bbox") or [0, 0, 0.1, 0.05]
    x = int(round(float(box[0]) * width))
    y = int(round(float(box[1]) * height))
    bw = max(2, int(round(float(box[2]) * width)))
    bh = max(2, int(round(float(box[3]) * height)))
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    bw = max(2, min(bw, width - x))
    bh = max(2, min(bh, height - y))
    return x, y, bw, bh


def _safe_crop(bgr: np.ndarray, rect: tuple[int, int, int, int]) -> Optional[np.ndarray]:
    x, y, bw, bh = rect
    crop = bgr[y:y + bh, x:x + bw]
    if crop.size == 0:
        return None
    return crop


def _ink_mask(crop: np.ndarray, colour_hex: str = "") -> Optional[np.ndarray]:
    if crop is None or crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return None
    mask = _near_colour(crop, colour_hex)
    if mask is None:
        border = np.concatenate([
            crop[0, :, :], crop[-1, :, :], crop[:, 0, :], crop[:, -1, :],
        ]).astype(np.float32)
        bg = np.median(border, axis=0)
        dist = np.linalg.norm(crop.astype(np.float32) - bg.reshape(1, 1, 3), axis=2)
        cut = max(16.0, float(np.percentile(dist, 72)))
        mask = (dist >= cut).astype(np.uint8) * 255
        if float(mask.mean()) > 180:
            cut = float(np.percentile(dist, 85))
            mask = (dist >= cut).astype(np.uint8) * 255
    count = int(np.count_nonzero(mask))
    if count < 12 or count > mask.size * 0.8:
        return None
    ys, xs = np.where(mask > 0)
    mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if mask.shape[0] < 3 or mask.shape[1] < 3:
        return None
    return mask


def _near_colour(crop: np.ndarray, colour_hex: str) -> Optional[np.ndarray]:
    raw = str(colour_hex or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        red, green, blue = (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return None
    colour = np.array([blue, green, red], np.float32)
    dist = np.linalg.norm(crop.astype(np.float32) - colour.reshape(1, 1, 3), axis=2)
    mask = (dist <= 52.0).astype(np.uint8) * 255
    if int(np.count_nonzero(mask)) < 12:
        return None
    return mask


def _scripty(ink: np.ndarray, text: str) -> bool:
    chars = max(1, sum(1 for ch in text if ch.isalnum()))
    binary = (ink > 0).astype(np.uint8)
    count, _labels, _stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    components = max(0, count - 1)
    if chars >= 4 and components <= max(2, int(chars * 0.45)):
        return True
    ys, xs = np.where(binary > 0)
    if len(xs) < 30:
        return False
    height = ink.shape[0]
    top = xs[ys < np.percentile(ys, 30)]
    bottom = xs[ys > np.percentile(ys, 70)]
    if top.size == 0 or bottom.size == 0:
        return False
    slant = abs(float(top.mean()) - float(bottom.mean())) / float(max(1, height))
    return slant > 0.22 and chars >= 4


def _score_font(ink: np.ndarray, text: str, path: str, role: str) -> float:
    """Compare letter shapes, serif bands, and the spacing rhythm."""
    source = _tight_binary(ink)
    rendered = _render_ink(text, path, max(int(ink.shape[1]), 80), max(int(ink.shape[0]), 24), role)
    rendered = _tight_binary(rendered) if rendered is not None else None
    if source is None or rendered is None:
        return 0.0
    stretch = _ncc(_fit_canvas(source, 280, 48), _fit_canvas(rendered, 280, 48))
    rhythm = _rhythm(source, rendered)
    return 0.65 * stretch + 0.35 * rhythm


def _tight_binary(mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if mask is None or mask.size == 0:
        return None
    ys, xs = np.where(mask > 40)
    if len(xs) < 8:
        return None
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _fit_canvas(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    return cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)


def _rhythm(source: np.ndarray, rendered: np.ndarray) -> float:
    def profile(mask: np.ndarray) -> np.ndarray:
        height, width = mask.shape[:2]
        scaled = cv2.resize(mask, (max(8, int(round(width * (48.0 / max(1, height))))), 48), interpolation=cv2.INTER_AREA)
        row = (scaled > 40).mean(axis=0).astype(np.float32).reshape(1, -1)
        return cv2.resize(row, (160, 1), interpolation=cv2.INTER_AREA).ravel()

    return _ncc(profile(source), profile(rendered))


def _render_ink(text: str, path: str, width: int, height: int, role: str) -> Optional[np.ndarray]:
    if width < 4 or height < 4 or not text:
        return None
    track_em = {"spaced": 0.55, "script": 0.08, "tagline": 0.16}.get(role, 0.28)
    size = max(8, int(height * 0.92))
    font = _pil_font(path, size)
    if font is None:
        return None
    ink_h = _pil_ink_height(font, text)
    if ink_h > 0:
        size = max(8, int(size * (height * 0.96) / ink_h))
        font = _pil_font(path, size)
        if font is None:
            return None
    widths = [font.getlength(ch) for ch in text]
    natural = float(sum(widths))
    gaps = max(1, len(text) - 1)
    track = 0.0
    if natural > width and natural > 0:
        size = max(8, int(size * width / natural))
        font = _pil_font(path, size)
        if font is None:
            return None
        widths = [font.getlength(ch) for ch in text]
        natural = float(sum(widths))
    extra = width - natural
    if extra > 0:
        track = min(extra / gaps, track_em * size)
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    ink_h = _pil_ink_height(font, text) or size
    y = max(0, int((height - ink_h) / 2))
    x = 0.0
    for index, ch in enumerate(text):
        draw.text((x, y), ch, font=font, fill=255)
        x += widths[index] if index < len(widths) else font.getlength(ch)
        if index < len(text) - 1:
            x += track
    return np.array(image)


def _pil_font(path: str, size: int):
    key = (path, int(size))
    if key in _PIL_FONTS:
        return _PIL_FONTS[key]
    try:
        font = ImageFont.truetype(path, int(size))
    except Exception:
        return None
    if len(_PIL_FONTS) > 80:
        _PIL_FONTS.clear()
    _PIL_FONTS[key] = font
    return font


def _pil_ink_height(font, text: str) -> int:
    probe = "Hg" if any(ch.islower() for ch in text) else "H"
    try:
        box = font.getbbox(probe)
    except Exception:
        return 0
    if not box:
        return 0
    return max(1, int(box[3] - box[1]))


def _ncc(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(np.float32).ravel()
    b = right.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum())) + 1e-6
    return float((a * b).sum() / denom)


def _trim_pixels(trim_w: float, trim_h: float, ppi: int) -> tuple[int, int]:
    return (
        max(32, int(round(trim_w / 25.4 * ppi))),
        max(32, int(round(trim_h / 25.4 * ppi))),
    )


def _cover(bgr: np.ndarray, dst_w: int, dst_h: int, interp: int):
    src_h, src_w = bgr.shape[:2]
    scale = max(dst_w / float(src_w), dst_h / float(src_h))
    nw = max(dst_w, int(round(src_w * scale)))
    nh = max(dst_h, int(round(src_h * scale)))
    resized = cv2.resize(bgr, (nw, nh), interpolation=interp)
    off_x = max(0, (nw - dst_w) // 2)
    off_y = max(0, (nh - dst_h) // 2)
    crop = resized[off_y:off_y + dst_h, off_x:off_x + dst_w]
    if crop.shape[0] != dst_h or crop.shape[1] != dst_w:
        crop = cv2.resize(resized, (dst_w, dst_h), interpolation=interp)
        off_x, off_y = 0, 0
        scale = dst_w / float(src_w)
    return crop, scale, off_x, off_y


def _enlarge(bgr: np.ndarray, dst_w: int, dst_h: int) -> tuple[np.ndarray, str]:
    """Real-ESRGAN when a token is set. Otherwise one Lanczos cover resize."""
    token = ""
    try:
        from ai_enhancements import _get_replicate_token

        token = (_get_replicate_token() or "").strip()
    except Exception:
        token = ""
    if token and os.environ.get("VECTOR_SKIP_ESRGAN") != "1":
        enhanced = _try_esrgan(bgr, dst_w, dst_h)
        if enhanced is not None:
            fitted, _scale, _x, _y = _cover(enhanced, dst_w, dst_h, cv2.INTER_LANCZOS4)
            return fitted, "Real-ESRGAN"
    fitted, _scale, _x, _y = _cover(bgr, dst_w, dst_h, cv2.INTER_LANCZOS4)
    return fitted, "Lanczos"


def _try_esrgan(bgr: np.ndarray, dst_w: int, dst_h: int) -> Optional[np.ndarray]:
    import tempfile

    try:
        from ai_upscale import apply_ai_upscale
    except Exception:
        return None
    folder = tempfile.mkdtemp(prefix="vector-esrgan-")
    src = os.path.join(folder, "src.png")
    try:
        cv2.imwrite(src, bgr)
        # The upscaler's own plan is 300 PPI. We only borrow its pixels, then
        # fit them to the 400 PPI trim.
        result = apply_ai_upscale(src, {
            "trim_w_mm": 148,
            "trim_h_mm": 210,
            "bleed_mm": 0,
            "output_path": os.path.join(folder, "up.png"),
        })
    except Exception:
        return None
    path = str((result or {}).get("enhanced_path") or "")
    if not path or not os.path.exists(path) or result.get("used_original"):
        return None
    if str(result.get("provider") or "") not in ("replicate", "stub"):
        return None
    loaded = cv2.imread(path, cv2.IMREAD_COLOR)
    if loaded is None:
        return None
    del dst_w, dst_h
    return loaded


def _restore_colour(sharp: np.ndarray, original: np.ndarray) -> np.ndarray:
    if sharp.shape[:2] != original.shape[:2]:
        original = cv2.resize(original, (sharp.shape[1], sharp.shape[0]), interpolation=cv2.INTER_CUBIC)
    sharp_lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB)
    orig_lab = cv2.cvtColor(original, cv2.COLOR_BGR2LAB)
    sharp_lab[:, :, 1] = orig_lab[:, :, 1]
    sharp_lab[:, :, 2] = orig_lab[:, :, 2]
    return cv2.cvtColor(sharp_lab, cv2.COLOR_LAB2BGR)


def _map_rect(rect, scale, off_x, off_y, bleed, shape) -> tuple[int, int, int, int]:
    x, y, bw, bh = rect
    height, width = shape[:2]
    mx = int(round(x * scale - off_x)) + int(bleed)
    my = int(round(y * scale - off_y)) + int(bleed)
    mw = max(2, int(round(bw * scale)))
    mh = max(2, int(round(bh * scale)))
    mx = max(0, min(mx, width - 1))
    my = max(0, min(my, height - 1))
    mw = max(2, min(mw, width - mx))
    mh = max(2, min(mh, height - my))
    return mx, my, mw, mh


def _drop_overlaps(lines: list, raster_lines: list) -> list:
    kept = []
    ordered = sorted(lines, key=lambda line: float(line.get("match") or 0), reverse=True)
    for line in ordered:
        box = line.get("media_box")
        if box is None:
            raster_lines.append({**line, "mode": "raster", "reason": "The line had no position."})
            continue
        if any(_iou(box, other.get("media_box")) > 0.22 for other in kept):
            raster_lines.append({**line, "mode": "raster", "reason": "This line overlapped another line."})
            continue
        kept.append(line)
    return kept


def _iou(a, b) -> float:
    if not a or not b:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / float(union)


def _remove_text(image: np.ndarray, lines: list) -> tuple[np.ndarray, list, list]:
    painted = image.copy()
    before = image.copy()
    mask = np.zeros(image.shape[:2], np.uint8)
    bullets = []
    badges = []
    ticks = []
    for line in lines:
        rect = line["media_box"]
        ink = _tight_ink(painted, rect, line.get("color") or "")
        if ink is None:
            line["blotch"] = True
            line["mode"] = "raster"
            line["reason"] = "The letters could not be separated from the picture."
            continue
        x, y, bw, bh = rect
        ink, icon_boxes = _strip_side_icons(ink)
        for ix, iy, iw, ih in icon_boxes:
            ticks.append((x + ix, y + iy, iw, ih))
        mask[y:y + bh, x:x + bw] = np.maximum(mask[y:y + bh, x:x + bw], ink)
        mark = _mark_near(before, rect)
        if not mark:
            continue
        if mark[0] == "badge":
            badges.append(mark)
            line["text"] = re.sub(r"^\d{1,2}\s+", "", str(line.get("text") or ""))
            continue
        if any(abs(mark[1] - old[1]) < 6 and abs(mark[2] - old[2]) < 6 for old in bullets):
            continue
        cx, cy, radius, colour = mark[1], mark[2], mark[3], mark[4]
        cv2.circle(mask, (int(cx), int(cy)), int(radius) + 1, 255, -1)
        bullets.append((cx, cy, radius, colour))
    if int(mask.max()) == 0:
        for line in lines:
            line["blotch"] = True
            line["mode"] = "raster"
            line["reason"] = "The letters could not be separated from the picture."
        return painted, [], [line for line in lines if line.get("blotch")]
    heights = [int(line["media_box"][3]) for line in lines if line.get("media_box")]
    radius = 3
    if heights:
        radius = int(np.clip(float(np.median(heights)) * 0.045, 3, 6))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    mask = cv2.dilate(mask, kernel, iterations=1)
    _protect_marks(mask, badges, ticks, radius + 3)
    painted = cv2.inpaint(painted, mask, radius, cv2.INPAINT_TELEA)
    _patch_specks(painted, mask)
    blotches = []
    for line in lines:
        if line.get("blotch"):
            blotches.append(line)
            continue
        if _blotch(before, painted, mask, line["media_box"]):
            _restore_mask(painted, before, mask, line["media_box"])
            line["blotch"] = True
            line["mode"] = "raster"
            line["font"] = ""
            line["reason"] = "Removing this line marked the background, so it stayed in the picture."
            blotches.append(line)
    return painted, bullets, blotches


def _tight_ink(image: np.ndarray, rect: tuple[int, int, int, int], colour_hex: str = "") -> Optional[np.ndarray]:
    x, y, bw, bh = rect
    crop = image[y:y + bh, x:x + bw]
    if crop.size == 0:
        return None
    coloured = _near_colour(crop, colour_hex)
    if coloured is not None and 0.01 < float(coloured.mean()) / 255.0 < 0.75:
        return coloured
    pad = 2
    y0, y1 = max(0, y - pad), min(image.shape[0], y + bh + pad)
    x0, x1 = max(0, x - pad), min(image.shape[1], x + bw + pad)
    ring = image[y0:y1, x0:x1]
    border = np.concatenate([
        ring[0, :, :], ring[-1, :, :], ring[:, 0, :], ring[:, -1, :],
    ]).astype(np.float32)
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(crop.astype(np.float32) - bg.reshape(1, 1, 3), axis=2)
    cut = max(18.0, float(np.percentile(dist, 70)))
    ink = (dist >= cut).astype(np.uint8) * 255
    fraction = float(ink.mean()) / 255.0
    if fraction > 0.62:
        cut = float(np.percentile(dist, 86))
        ink = (dist >= cut).astype(np.uint8) * 255
        fraction = float(ink.mean()) / 255.0
    if fraction < 0.01 or fraction > 0.75:
        return None
    return ink


def _strip_side_icons(ink: np.ndarray) -> tuple[np.ndarray, list]:
    """Drop a tick or icon that sits in a gap to the left of the words."""
    binary = (ink > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 2:
        return ink, []
    height, width = ink.shape[:2]
    boxes = []
    for index in range(1, count):
        left = int(stats[index, cv2.CC_STAT_LEFT])
        top = int(stats[index, cv2.CC_STAT_TOP])
        bw = int(stats[index, cv2.CC_STAT_WIDTH])
        bh = int(stats[index, cv2.CC_STAT_HEIGHT])
        boxes.append((index, left, top, bw, bh))
    boxes.sort(key=lambda item: item[1])
    for pos in range(len(boxes) - 1):
        _index, left, _top, bw, _bh = boxes[pos]
        right_edge = left + bw
        next_left = boxes[pos + 1][1]
        gap = next_left - right_edge
        if gap >= max(4, int(width * 0.035)) and right_edge < width * 0.42:
            removed = []
            for index, ileft, itop, ibw, ibh in boxes:
                if ileft + ibw <= right_edge + 1:
                    ink[labels == index] = 0
                    removed.append((ileft, itop, ibw, ibh))
            return ink, removed
    return ink, []


def _protect_marks(mask: np.ndarray, badges: list, ticks: list, pad: int) -> None:
    """Filled badges and ticks stay as drawn. Inpaint must not enter them."""
    for mark in badges:
        cx, cy, radius = int(mark[1]), int(mark[2]), int(round(mark[3])) + pad
        cv2.circle(mask, (cx, cy), max(2, radius), 0, -1)
    height, width = mask.shape[:2]
    for x, y, bw, bh in ticks:
        x0 = max(0, int(x) - pad)
        y0 = max(0, int(y) - pad)
        x1 = min(width, int(x + bw) + pad)
        y1 = min(height, int(y + bh) + pad)
        mask[y0:y1, x0:x1] = 0


def _mark_near(image: np.ndarray, rect: tuple[int, int, int, int]):
    """A round mark left of a line. A filled badge that holds a digit is left alone."""
    found = _bullet_near(image, rect)
    if not found:
        return None
    cx, cy, radius, colour = found
    if _badge_has_mark(image, cx, cy, radius):
        return ("badge", cx, cy, radius)
    return ("bullet", cx, cy, radius, colour)


def _badge_has_mark(image: np.ndarray, cx: float, cy: float, radius: float) -> bool:
    """True when the disk is not one flat colour, so a digit sits inside it."""
    radius = float(radius)
    if radius < 4:
        return False
    x0 = max(0, int(cx - radius))
    y0 = max(0, int(cy - radius))
    x1 = min(image.shape[1], int(cx + radius + 1))
    y1 = min(image.shape[0], int(cy + radius + 1))
    roi = image[y0:y1, x0:x1]
    if roi.size == 0:
        return False
    yy, xx = np.ogrid[:roi.shape[0], :roi.shape[1]]
    dist = np.sqrt((xx - (cx - x0)) ** 2 + (yy - (cy - y0)) ** 2)
    inner = dist <= radius * 0.62
    if int(inner.sum()) < 12:
        return False
    pixels = roi[inner].astype(np.float32)
    lum = pixels.mean(axis=1)
    spread = float(lum.max() - lum.min())
    median = float(np.median(lum))
    bright = float((lum > median + 28).mean())
    dark = float((lum < median - 28).mean())
    return spread > 36 and (bright > 0.05 or dark > 0.05)


def _bullet_near(image: np.ndarray, rect: tuple[int, int, int, int]):
    x, y, _bw, bh = rect
    look = int(max(8, bh * 1.35))
    x0 = max(0, x - look)
    x1 = min(image.shape[1], x + max(2, bh // 5))
    y0 = max(0, y)
    y1 = min(image.shape[0], y + bh)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if float(binary.mean()) > 180:
        binary = cv2.bitwise_not(binary)
    count, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    best = None
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        bw = int(stats[index, cv2.CC_STAT_WIDTH])
        bh_ = int(stats[index, cv2.CC_STAT_HEIGHT])
        if bw < 3 or bh_ < 3:
            continue
        aspect = bw / float(bh_)
        fill = area / float(bw * bh_)
        if not (0.72 <= aspect <= 1.35 and fill >= 0.55):
            continue
        if not (bh * bh * 0.04 <= area <= bh * bh * 0.85):
            continue
        if best is None or area > best[0]:
            best = (area, index, bw, bh_)
    if best is None:
        return None
    _area, index, bw, bh_ = best
    pixels = roi[labels == index]
    colour = np.median(pixels, axis=0)
    cx = x0 + float(cents[index][0])
    cy = y0 + float(cents[index][1])
    radius = max(bw, bh_) / 2.0
    return cx, cy, radius, colour


def _patch_specks(image: np.ndarray, mask: np.ndarray) -> None:
    blurred = cv2.medianBlur(image, 5)
    diff = np.max(np.abs(image.astype(np.int16) - blurred.astype(np.int16)), axis=2)
    specks = ((mask > 0) & (diff > 36)).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(specks, 8)
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) <= 28:
            image[labels == index] = blurred[labels == index]


def _blotch(before: np.ndarray, after: np.ndarray, mask: np.ndarray, rect) -> bool:
    """A smear is paint that no longer matches the colour that was behind the letters."""
    x, y, bw, bh = rect
    local_mask = mask[y:y + bh, x:x + bw]
    if int(local_mask.max()) == 0:
        return False
    inside = after[y:y + bh, x:x + bw][local_mask > 0].astype(np.float32)
    ground = before[y:y + bh, x:x + bw][local_mask == 0].astype(np.float32)
    if ground.size < 30:
        pad = max(3, min(bw, bh) // 8)
        y0, y1 = max(0, y - pad), min(before.shape[0], y + bh + pad)
        x0, x1 = max(0, x - pad), min(before.shape[1], x + bw + pad)
        near = mask[y0:y1, x0:x1] == 0
        ground = before[y0:y1, x0:x1][near].astype(np.float32)
    if inside.size == 0 or ground.size == 0:
        return False
    delta = float(np.linalg.norm(inside.mean(axis=0) - ground.mean(axis=0)))
    return delta > 36.0


def _restore_mask(image, before, mask, rect) -> None:
    x, y, bw, bh = rect
    local = mask[y:y + bh, x:x + bw] > 0
    image[y:y + bh, x:x + bw][local] = before[y:y + bh, x:x + bw][local]


def _write_pdf(image, lines, bullets, output_pdf, trim_w, trim_h, bleed_mm) -> dict:
    import pymupdf as fitz

    qa = {"wrote": False, "cmyk": False, "boxes": False, "fonts": False, "ppi": False, "reason": ""}
    os.makedirs(os.path.dirname(output_pdf) or ".", exist_ok=True)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    cmyk = Image.fromarray(rgb).convert("CMYK")
    buffer = io.BytesIO()
    cmyk.save(buffer, format="TIFF", dpi=(MIN_PPI, MIN_PPI))
    width_pt = (trim_w + 2 * bleed_mm) * MM_TO_PT
    height_pt = (trim_h + 2 * bleed_mm) * MM_TO_PT
    doc = fitz.open()
    try:
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, stream=buffer.getvalue())
        img_h, img_w = image.shape[:2]
        sx = page.rect.width / float(img_w)
        sy = page.rect.height / float(img_h)
        for cx, cy, radius, colour in bullets:
            rgb_colour = (float(colour[2]) / 255.0, float(colour[1]) / 255.0, float(colour[0]) / 255.0)
            page.draw_circle(
                fitz.Point(cx * sx, cy * sy),
                max(0.4, radius * (sx + sy) / 2.0),
                color=rgb_colour,
                fill=rgb_colour,
                width=0,
            )
        used = set()
        for line in lines:
            if line.get("mode") != "vector":
                continue
            _draw_line(page, line, sx, sy)
            used.add(line.get("font") or "")
        _set_boxes(page, trim_w, trim_h, bleed_mm)
        try:
            doc.subset_fonts()
            qa["subset"] = True
        except Exception:
            qa["subset"] = False
        doc.save(output_pdf, deflate=True, garbage=4)
        qa["wrote"] = True
    finally:
        doc.close()
    _inspect(output_pdf, trim_w, trim_h, bleed_mm, qa)
    if not qa["subset"]:
        qa["fonts"] = False
        qa["reason"] = "The fonts could not be subset."
    return qa


def _fitz_font(key: str):
    import pymupdf as fitz

    if key in _FITZ_FONTS:
        return _FITZ_FONTS[key]
    path = _FONT_FILES.get(key)
    if not path or not os.path.exists(path):
        return None
    font = fitz.Font(fontfile=path)
    _FITZ_FONTS[key] = font
    return font


def _draw_line(page, line: dict, sx: float, sy: float) -> None:
    import pymupdf as fitz

    font = _fitz_font(line.get("font") or "")
    if font is None:
        return
    x, y, bw, bh = line["media_box"]
    box_w = bw * sx
    box_h = bh * sy
    text = str(line.get("text") or "")
    if not text or box_w < 1 or box_h < 1:
        return
    role = _FONT_ROLE.get(line.get("font") or "", "body")
    size, tracking = _fit_width(font, text, box_w, box_h, role)
    asc = size * (0.70 if role != "script" else 0.62)
    desc = size * 0.22 if any(ch in "gjpqy" for ch in text) else 0.0
    ink = asc + desc
    top_pad = max(0.0, (box_h - ink) / 2.0)
    baseline = y * sy + top_pad + asc
    colour = _cmyk(_hex_rgb(line.get("color") or "#222222"))
    writer = fitz.TextWriter(page.rect)
    cursor = x * sx
    for index, ch in enumerate(text):
        writer.append((cursor, baseline), ch, font=font, fontsize=size)
        cursor += font.text_length(ch, fontsize=size)
        if index < len(text) - 1:
            cursor += tracking
    writer.write_text(page, color=colour, render_mode=0)
    if line.get("font") in THICKEN:
        writer_b = fitz.TextWriter(page.rect)
        cursor = x * sx + 0.18
        for index, ch in enumerate(text):
            writer_b.append((cursor, baseline), ch, font=font, fontsize=size)
            cursor += font.text_length(ch, fontsize=size)
            if index < len(text) - 1:
                cursor += tracking
        writer_b.write_text(page, color=colour, render_mode=0)


def _fit_width(font, text: str, box_w: float, box_h: float, role: str) -> tuple[float, float]:
    upper = sum(ch.isupper() for ch in text if ch.isalpha()) / float(max(1, sum(ch.isalpha() for ch in text)))
    has_desc = any(ch in "gjpqy" for ch in text)
    if role == "script":
        ratio = 0.78
    elif upper > 0.85 and not has_desc:
        ratio = 0.70
    elif has_desc:
        ratio = 0.92
    else:
        ratio = 0.80
    size = max(4.0, box_h / ratio)
    natural = font.text_length(text, fontsize=size)
    if natural > box_w and natural > 0:
        size *= box_w / natural
        natural = font.text_length(text, fontsize=size)
    gaps = max(1, len(text) - 1)
    tracking = 0.0
    if natural < box_w:
        cap = {"spaced": 0.62, "script": 0.08, "tagline": 0.18}.get(role, 0.30) * size
        tracking = min((box_w - natural) / gaps, cap)
    return size, tracking


def fit_line(font_key: str, text: str, box_w: float, box_h: float) -> tuple[float, float, float]:
    """Return fontsize, tracking and the resulting advance width. For tests."""
    font = _fitz_font(font_key)
    if font is None:
        return 0.0, 0.0, 0.0
    role = _FONT_ROLE.get(font_key, "body")
    size, tracking = _fit_width(font, text, box_w, box_h, role)
    gaps = max(0, len(text) - 1)
    width = font.text_length(text, fontsize=size) + tracking * gaps
    return size, tracking, width


def _hex_rgb(value: str) -> tuple[float, float, float]:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6:
        return 0.1, 0.1, 0.1
    try:
        return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.1, 0.1, 0.1


def _cmyk(rgb: tuple[float, float, float]) -> tuple[float, float, float, float]:
    red, green, blue = rgb
    if max(red, green, blue) < 0.12 and max(red, green, blue) - min(red, green, blue) < 0.06:
        return (0.0, 0.0, 0.0, 1.0)
    black = 1.0 - max(red, green, blue)
    if black >= 0.999:
        return (0.0, 0.0, 0.0, 1.0)
    cyan = (1.0 - red - black) / (1.0 - black)
    magenta = (1.0 - green - black) / (1.0 - black)
    yellow = (1.0 - blue - black) / (1.0 - black)
    return (cyan, magenta, yellow, black)


def _set_boxes(page, trim_w: float, trim_h: float, bleed_mm: float) -> None:
    import pymupdf as fitz

    bleed_pt = bleed_mm * MM_TO_PT
    media = page.rect
    page.set_mediabox(media)
    page.set_cropbox(media)
    page.set_bleedbox(media)
    page.set_trimbox(fitz.Rect(
        bleed_pt,
        bleed_pt,
        bleed_pt + trim_w * MM_TO_PT,
        bleed_pt + trim_h * MM_TO_PT,
    ))


def _inspect(path: str, trim_w: float, trim_h: float, bleed_mm: float, qa: dict) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        media = page.mediabox
        trim = page.trimbox
        bleed = page.bleedbox
        inset_x = trim.x0 * 25.4 / 72.0
        inset_y = trim.y0 * 25.4 / 72.0
        qa["boxes"] = (
            abs(inset_x - bleed_mm) < 0.45
            and abs(inset_y - bleed_mm) < 0.45
            and abs(trim.width * 25.4 / 72.0 - trim_w) < 0.6
            and abs(trim.height * 25.4 / 72.0 - trim_h) < 0.6
            and abs(bleed.width - media.width) < 1.5
            and abs(bleed.height - media.height) < 1.5
        )
        images = page.get_images()
        qa["image_count"] = len(images)
        if images:
            info = doc.extract_image(images[0][0])
            qa["cmyk"] = info.get("colorspace") == 4 or "CMYK" in str(info.get("cs-name", ""))
            width_in = media.width / 72.0
            height_in = media.height / 72.0
            ppi_x = info.get("width", 0) / width_in if width_in else 0
            ppi_y = info.get("height", 0) / height_in if height_in else 0
            qa["ppi_x"] = round(ppi_x, 1)
            qa["ppi_y"] = round(ppi_y, 1)
            qa["ppi"] = ppi_x >= MIN_PPI - 1 and ppi_y >= MIN_PPI - 1
        fonts = page.get_fonts()
        qa["font_names"] = [item[3] for item in fonts]
        qa["fonts"] = bool(fonts) and all("+" in str(item[3]) for item in fonts)
    finally:
        doc.close()


def _reocr_pdf(path: str) -> str:
    import pymupdf as fitz
    from ocr_reader import local_rows

    doc = fitz.open(path)
    try:
        page = doc[0]
        longest = max(page.rect.width, page.rect.height, 1)
        scale = QA_LONG_EDGE / float(longest)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    finally:
        doc.close()
    rows = local_rows(bgr) or []
    return "\n".join(str(item[1] or "") for item in rows)


def _recall(expected: str, found: str) -> float:
    words = [word for word in re.findall(r"[A-Z0-9]+", expected.upper()) if len(word) >= 2]
    if not words:
        return 1.0
    raw = found.upper()
    blob = re.sub(r"[^A-Z0-9]", "", raw)
    hits = 0
    for word in words:
        if len(word) >= 4 and word in blob:
            hits += 1
            continue
        if re.search(rf"\b{re.escape(word)}\b", raw):
            hits += 1
            continue
        if len(word) >= 5 and any(_edit(word, other) <= 1 for other in re.findall(r"[A-Z0-9]{4,}", raw)):
            hits += 1
    return hits / float(len(words))


def _edit(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > 1:
        return 9
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right))
    if len(left) > len(right):
        left, right = right, left
    skips = 0
    i = 0
    for ch in right:
        if i < len(left) and left[i] == ch:
            i += 1
        else:
            skips += 1
    return skips if i == len(left) else 9


def _discard(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
