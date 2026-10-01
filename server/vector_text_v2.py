#!/usr/bin/env python3
"""Vector text rebuild v2.

Quick mode uses this for AI raster artwork. Lettering is erased on the original
picture, that clean plate is enlarged to at least 400 pixels per inch, and each
OCR line is set again as embedded vector type. The mask and the type share one
scale and one offset. Icons stay in the picture. A line that cannot be read, or
a page that fails the check, falls back to the enlarged picture and an amber flag.

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
from PIL import Image, ImageCms, ImageDraw, ImageFont

BLEED_MM = 5.0
MIN_PPI = 400
MM_TO_PT = 72.0 / 25.4
DETECT_LONG_EDGE = 1280
QA_LONG_EDGE = 1100
MATCH_FLOOR = 0.18
SCRIPT_OCR_FLOOR = 0.75
RECALL_FLOOR = 0.60
# A caps line under this size may stay as pixels. Larger headings are set in
# Cinzel, using Regular and a paper-coloured stroke when SemiBold is too heavy.
CAPS_INK_FLOOR_PT = 8.0
OCR_CACHE = os.environ.get("VECTOR_OCR_CACHE", "/tmp/flyerz-ocr-cache")

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "v2")

# key, filename, role. Roles pick the shortlist and the tracking limit.
FONTS = (
    ("cinzel-400", "Cinzel-400.ttf", "spaced"),
    ("cinzel-500", "Cinzel-500.ttf", "spaced"),
    ("cinzel-600", "Cinzel-600.ttf", "spaced"),
    ("cinzel-700", "Cinzel-700.ttf", "spaced"),
    ("eb-semibold", "EBGaramond-SemiBold.ttf", "serif"),
    ("crimson", "CrimsonText-Regular.ttf", "body"),
    ("crimson-italic", "CrimsonText-Italic.ttf", "body"),
    ("crimson-semibold", "CrimsonText-SemiBold.ttf", "body"),
    ("crimson-bold", "CrimsonText-Bold.ttf", "body"),
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
# Stroke as a fraction of the font size. PyMuPDF's own stroke is 5%, which is too heavy.
# Body copy matches the reference 1.3% stroke. Small script matches 1.2%.
BODY_STROKE = 0.013
SCRIPT_STROKE = 0.012

_FONT_FILES = {key: os.path.join(FONT_DIR, name) for key, name, _role in FONTS}
_FONT_ROLE = {key: role for key, _name, role in FONTS}
_FITZ_FONTS: dict = {}
_PIL_FONTS: dict = {}


def font_path(key: str) -> str:
    return _FONT_FILES[key]


def vector_rebuild_enabled() -> bool:
    """Vector type can be switched off. The press job then keeps the upscaled picture."""
    raw = os.environ.get("VECTOR_REBUILD", "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def font_substitution_enabled() -> bool:
    """Font substitution is opt-in. Tracing the original ink is the default."""
    raw = os.environ.get("VECTOR_FONTS", "0").strip().lower()
    return raw in ("1", "on", "true", "yes")


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
    """Build a vector press PDF. Never raises. ok False means fall back.

    The default traces the original ink. Set VECTOR_FONTS=1 to set new type instead.
    """
    started = time.perf_counter()
    if not vector_rebuild_enabled():
        return _fail(started, "Vector type is switched off, so the original lettering was kept.")
    try:
        if not font_substitution_enabled():
            from vector_trace import trace_fitted

            return trace_fitted(
                bgr, float(trim_w_mm), float(trim_h_mm), output_pdf,
                float(bleed_mm), progress, blocks,
            )
        return _rebuild(
            bgr, float(trim_w_mm), float(trim_h_mm), output_pdf,
            float(bleed_mm), progress, blocks, reocr, float(recall_floor), started,
        )
    except Exception as exc:
        return _fail(started, f"Vector type failed ({str(exc)[:160]}). The original lettering was kept.")


def _fail(started: float, reason: str, **extra) -> dict:
    lines = extra.get("lines")
    if isinstance(lines, list):
        extra["lines"] = [_jsonable_line(line) for line in lines]
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


def _jsonable_line(line: dict) -> dict:
    """Drop pixel masks. A failed job is still written out as JSON."""
    if not isinstance(line, dict):
        return line
    return {key: value for key, value in line.items() if not isinstance(value, np.ndarray)}


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
    kept, dupes = _dedupe_blocks(kept)
    raster_lines = [_line_record(block, "raster", "The read was too uncertain to set as type.") for block in dropped]
    for block in dupes:
        raster_lines.append(_line_record(
            block, "raster", "This box repeated another line, so it was not set again.",
        ))
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
        decision = _choose_font(bgr, block, style, trim_h)
        if decision.get("mode") == "vector":
            chosen.append(decision)
        else:
            raster_lines.append(decision)
    chosen = _harmonise(chosen, style)
    _share_block_weight(chosen)
    from vector_plate import find_badges

    page_badges = find_badges(bgr)
    kept_chosen = []
    for line in chosen:
        if _inside_badge(line.get("rect"), page_badges) or _near_badge(line.get("rect"), page_badges):
            record = dict(line)
            record.update(mode="raster", font="", kept_on_purpose=True)
            record["reason"] = "Lettering inside a circle stayed in the picture."
            raster_lines.append(record)
            continue
        if _compact_mark(line.get("text") or ""):
            record = dict(line)
            record.update(mode="raster", font="", kept_on_purpose=True)
            record["reason"] = "A short mark stayed in the picture."
            raster_lines.append(record)
            continue
        kept_chosen.append(line)
    chosen = kept_chosen

    if not chosen:
        return _fail(
            started,
            "No line was confident enough to set as type, so the original lettering was kept.",
            lines=raster_lines,
            timings={"ocr_s": round(ocr_s, 3)},
        )

    for line in chosen:
        line["media_box"] = line["rect"]
    chosen = _drop_overlaps(chosen, raster_lines)
    for line in chosen:
        line.pop("media_box", None)
    if not chosen:
        return _fail(started, "The lines overlapped, so the original lettering was kept.")

    _note(progress, "removing", "Removing the old lettering.")
    paint_started = time.perf_counter()
    marks = _source_marks(bgr, chosen)
    for badge in page_badges:
        marks.append({"kind": "badge", "cx": badge["cx"], "cy": badge["cy"], "radius": badge["radius"]})
    guide = _guide_boxes(bgr, blocks, chosen)
    from vector_plate import erase_text, map_rect, place_plate

    clean, chosen, skipped = erase_text(bgr, chosen, marks)
    raster_lines.extend(skipped)
    chosen = _drop_residual(clean, bgr, chosen, raster_lines, marks)
    paint_s = time.perf_counter() - paint_started
    if not chosen:
        return _fail(
            started,
            "Removing the old lettering marked the picture, so the original lettering was kept.",
            lines=raster_lines,
            timings={"ocr_s": round(ocr_s, 3), "paint_s": round(paint_s, 3)},
        )

    _note(progress, "enlarging", "Enlarging the picture and setting the type.")
    enlarge_started = time.perf_counter()
    placed = place_plate(clean, guide, trim_w, trim_h, bleed_mm, MIN_PPI)
    provider = placed["provider"]
    enlarge_s = time.perf_counter() - enlarge_started
    for line in chosen:
        line["media_box"] = map_rect(line["ink"], placed, placed["image"].shape)
        if line.get("ink_hex"):
            line["color"] = line["ink_hex"]
    bullets = _map_bullets(marks, placed)

    type_started = time.perf_counter()
    qa = _write_pdf(placed["image"], chosen, bullets, output_pdf, trim_w, trim_h, bleed_mm)
    type_s = time.perf_counter() - type_started
    if not qa.get("wrote"):
        return _fail(started, qa.get("reason") or "The vector press file could not be written.", provider=provider)

    spacing_raster = _restore_spacing_failures(
        bgr, clean, chosen, raster_lines, output_pdf, placed, check_tokens=(reocr is None),
    )
    if spacing_raster:
        if not chosen:
            return _fail(
                started,
                "The spacing could not be matched, so the original lettering was kept.",
                lines=raster_lines,
                timings={"ocr_s": round(ocr_s, 3), "paint_s": round(paint_s, 3)},
            )
        rewrite = time.perf_counter()
        placed = place_plate(clean, guide, trim_w, trim_h, bleed_mm, MIN_PPI)
        provider = placed["provider"]
        for line in chosen:
            line["media_box"] = map_rect(line["ink"], placed, placed["image"].shape)
        bullets = _map_bullets(marks, placed)
        qa = _write_pdf(placed["image"], chosen, bullets, output_pdf, trim_w, trim_h, bleed_mm)
        type_s += time.perf_counter() - rewrite
        if not qa.get("wrote"):
            return _fail(started, qa.get("reason") or "The vector press file could not be written.", provider=provider)
        if reocr is None and _repeat_failures(bgr, chosen, output_pdf, placed):
            _discard(output_pdf)
            return _fail(
                started,
                "Repeated words were still on the page, so the original lettering was kept.",
                lines=raster_lines,
                provider=provider,
            )
    coverage_raster = _restore_coverage_failures(
        bgr, clean, chosen, raster_lines, output_pdf, placed,
    )
    if coverage_raster:
        if not chosen:
            _discard(output_pdf)
            return _fail(
                started,
                "Original ink was still under the type, so the original lettering was kept.",
                lines=raster_lines,
                timings={"ocr_s": round(ocr_s, 3), "paint_s": round(paint_s, 3)},
            )
        rewrite = time.perf_counter()
        placed = place_plate(clean, guide, trim_w, trim_h, bleed_mm, MIN_PPI)
        provider = placed["provider"]
        for line in chosen:
            line["media_box"] = map_rect(line["ink"], placed, placed["image"].shape)
        bullets = _map_bullets(marks, placed)
        qa = _write_pdf(placed["image"], chosen, bullets, output_pdf, trim_w, trim_h, bleed_mm)
        type_s += time.perf_counter() - rewrite
        if not qa.get("wrote"):
            return _fail(started, qa.get("reason") or "The vector press file could not be written.", provider=provider)
        if reocr is None and _repeat_failures(bgr, chosen, output_pdf, placed):
            _discard(output_pdf)
            return _fail(
                started,
                "Repeated words were still on the page, so the original lettering was kept.",
                lines=raster_lines,
                provider=provider,
            )
    qa["spacing_raster"] = len(spacing_raster)
    qa["coverage_raster"] = len(coverage_raster)

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
        "The old lettering was removed from the original picture before it was enlarged.",
        f"The picture was enlarged with {provider} and the original colours were put back.",
        "The press file is CMYK at 400 PPI or more, with the trim 5 mm inside the bleed.",
    ]
    if raster_lines:
        decisions.append(reason)
    _stamp_points(list(chosen) + list(raster_lines), bgr, trim_h)
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


def gaps_hold(original: list, rebuild: list, slack: float = 0.30) -> bool:
    """Each word-gap fraction stays within ``slack`` of the original."""
    if not original:
        return True
    if len(original) != len(list(rebuild or [])):
        return False
    low, high = 1.0 - slack, 1.0 + slack
    for src, out in zip(original, rebuild):
        src = float(src)
        if src <= 0.004:
            continue
        ratio = float(out) / src
        if ratio < low or ratio > high:
            return False
    return True


def gap_ems_hold(original: dict, rebuild: dict, slack: float = 0.30) -> bool:
    """Each word gap, as a fraction of the line, stays within ``slack``.

    Both sides have to measure the same kind of gap. A crop that did not
    resolve the gaps is left for the token check.
    """
    if (original or {}).get("grain") != (rebuild or {}).get("grain"):
        return True
    src = list((original or {}).get("gaps") or [])
    out = list((rebuild or {}).get("gaps") or [])
    if not src or not out or len(src) != len(out):
        return True
    # A tight line moves by a pixel when it is redrawn. That is not a lost word space.
    kept_src = []
    kept_out = []
    for src_gap, out_gap in zip(src, out):
        if abs(float(src_gap) - float(out_gap)) <= 0.012:
            continue
        kept_src.append(src_gap)
        kept_out.append(out_gap)
    if not kept_src:
        return True
    return gaps_hold(kept_src, kept_out, slack)


def _word_tokens(text: str) -> list:
    return re.findall(r"[A-Za-z0-9]+|&", str(text or ""))


def repeats_hold(source: str, read: str) -> bool:
    """A repeated neighbouring word that the source does not have is a double draw.

    'A A', '& &', 'only only', and a doubled glyph such as 'DD' all fail.
    """
    src = _word_tokens(source)
    out = _word_tokens(read)
    if not out:
        return True
    src_pairs = {(left.lower(), right.lower()) for left, right in zip(src, src[1:])}
    src_set = {token.lower() for token in src}
    for left, right in zip(out, out[1:]):
        pair = (left.lower(), right.lower())
        # A repeated source word ('A A', 'only only', '& &') is a double draw.
        # A repeated token the source never had ('0 0' from a script misread) is not.
        if pair[0] == pair[1] and pair not in src_pairs and pair[0] in src_set:
            return False
    for token in out:
        low = token.lower()
        if len(low) < 2 or len(low) % 2:
            continue
        half = low[:len(low) // 2]
        if low == half * 2 and low not in src_set and half in src_set:
            return False
    return True


def tokens_hold(source: str, read: str, score: float = 1.0) -> bool:
    """Same letters with a different word count means the spaces were lost or invented.

    An empty read, or a read that changed the letters, is not a spacing failure.
    """
    found = " ".join(str(read or "").split())
    if not found or float(score or 0) < 0.5:
        return True
    source_words = re.findall(r"[A-Za-z0-9]+", str(source or ""))
    read_words = re.findall(r"[A-Za-z0-9]+", found)
    if not source_words or not read_words:
        return True
    source_core = re.sub(r"[^A-Za-z0-9]", "", str(source or "")).upper()
    read_core = re.sub(r"[^A-Za-z0-9]", "", found).upper()
    if source_core != read_core:
        return True
    return len(source_words) == len(read_words)


def _line_window(line: dict):
    """The OCR rectangle and the ink rectangle, as one pixel window.

    A thin ink slice must not hide the rest of the original letters.
    """
    parts = []
    rect = line.get("rect")
    if rect and len(rect) >= 4:
        x, y, bw, bh = [float(v) for v in rect[:4]]
        if bw > 1 and bh > 1:
            parts.append((x, y, x + bw, y + bh))
    ink = line.get("ink")
    if ink and len(ink) >= 4:
        x0, y0, x1, y1 = [float(v) for v in ink[:4]]
        if x1 > x0 and y1 > y0:
            parts.append((x0, y0, x1, y1))
    if not parts:
        return None
    x0 = int(np.floor(min(part[0] for part in parts)))
    y0 = int(np.floor(min(part[1] for part in parts)))
    x1 = int(np.ceil(max(part[2] for part in parts)))
    y1 = int(np.ceil(max(part[3] for part in parts)))
    if rect and len(rect) >= 4:
        height = max(1, int(round(float(rect[3]))))
    else:
        height = max(1, y1 - y0)
    return x0, y0, x1, y1, height


def _qa_box(line: dict, placed: dict | None) -> tuple:
    """Plate-pixel box for the final-page read. The OCR rect is included."""
    parts = []
    media = line.get("media_box")
    if media and len(media) >= 4:
        x, y, bw, bh = [float(v) for v in media[:4]]
        parts.append((x, y, x + bw, y + bh))
    rect = line.get("rect")
    mapper = placed.get("map") if placed else None
    if rect and mapper and len(rect) >= 4:
        x, y, bw, bh = [float(v) for v in rect[:4]]
        ax, ay = mapper(x, y)
        bx, by = mapper(x + bw, y + bh)
        parts.append((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
    if not parts:
        return 0.0, 0.0, 8.0, 8.0
    x0 = min(part[0] for part in parts)
    y0 = min(part[1] for part in parts)
    x1 = max(part[2] for part in parts)
    y1 = max(part[3] for part in parts)
    return x0, y0, max(2.0, x1 - x0), max(2.0, y1 - y0)


def _paste_original(clean: np.ndarray, source: np.ndarray, line: dict) -> None:
    """Put back every pixel this line's erase changed. A fallback is never half-cleared."""
    halo = line.get("cleared_halo")
    if isinstance(halo, np.ndarray) and halo.shape[:2] == clean.shape[:2] and int(halo.max()) > 0:
        clean[halo > 0] = source[halo > 0]
        return
    window = _line_window(line)
    if window is None:
        return
    x0, y0, x1, y1, _height = window
    pad = 14
    height, width = clean.shape[:2]
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(width, x1 + pad)
    y1 = min(height, y1 + pad)
    if x1 > x0 and y1 > y0:
        clean[y0:y1, x0:x1] = source[y0:y1, x0:x1]


INK_EXCESS_LIMIT = 0.15


def excess_holds(ratio: float, limit: float = INK_EXCESS_LIMIT) -> bool:
    """True when composited ink is not more than about 15% above the vector ink."""
    return float(ratio) <= float(limit) + 1.0e-6


def ink_excess(composite: np.ndarray, vector: np.ndarray) -> float:
    """Extra ink beside the vector glyphs, as a fraction of the vector ink.

    The vector render is the type on white. Ink on the composite that sits
    outside a 1-pixel halo of those glyphs is leftover picture ink.
    """
    if composite is None or vector is None or composite.size == 0 or vector.size == 0:
        return 0.0
    if composite.shape[:2] != vector.shape[:2]:
        vector = cv2.resize(vector, (composite.shape[1], composite.shape[0]), interpolation=cv2.INTER_AREA)
    if composite.ndim != 3 or vector.ndim != 3:
        return 0.0
    distance = np.linalg.norm(vector.astype(np.float32) - 255.0, axis=2)
    vec_ink = distance > 12.0
    count = int(vec_ink.sum())
    if count < 20:
        return 0.0
    covered = cv2.dilate(vec_ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    rad = max(3, int(round(composite.shape[0] * 0.45)))
    if rad % 2 == 0:
        rad += 1
    rad = min(rad, 31)
    band = cv2.dilate(vec_ink.astype(np.uint8), np.ones((rad, rad), np.uint8)) > 0
    ring = band & ~covered
    if int(ring.sum()) < 15:
        return 0.0
    paper = np.median(composite[ring].astype(np.float32), axis=0)
    comp_dist = np.linalg.norm(composite.astype(np.float32) - paper, axis=2)
    extra = band & (comp_dist > 18.0) & ~covered
    # A rule or the next panel often sits on the crop edge. That is not leftover letters.
    height, width = extra.shape[:2]
    margin = max(2, int(round(min(height, width) * 0.08)))
    if margin * 2 < height and margin * 2 < width:
        extra = extra.copy()
        extra[:margin, :] = False
        extra[-margin:, :] = False
        extra[:, :margin] = False
        extra[:, -margin:] = False
    if int(extra.sum()) == 0:
        return 0.0
    # Keep a component only when it is tall enough to be a letter, not a 2px rule.
    ys, xs = np.where(vec_ink)
    glyph_h = max(4, int(ys.max() - ys.min()) + 1) if ys.size else height
    count_cc, labels, stats, _cent = cv2.connectedComponentsWithStats(extra.astype(np.uint8), 8)
    kept = np.zeros(extra.shape, np.uint8)
    for index in range(1, count_cc):
        comp_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        # A 1–2px rule is a panel edge. A leftover letter is taller than that.
        if comp_h <= 2 or area < 6:
            continue
        if comp_h < max(4, int(glyph_h * 0.18)):
            continue
        kept[labels == index] = 255
    return float(int((kept > 0).sum())) / float(count)


def _restore_spacing_failures(source, clean, chosen, raster_lines, pdf_path, placed, check_tokens: bool) -> list:
    """Re-read every vector line. A bad word count or word gap is put back as pixels."""
    try:
        failed = _spacing_failures(source, chosen, pdf_path, placed, check_tokens)
    except Exception:
        return []
    if not failed:
        return []
    for line in failed:
        _paste_original(clean, source, line)
        line["mode"] = "raster"
        line["font"] = ""
        line["reason"] = "The spacing did not match the original, so this line stayed in the picture."
        raster_lines.append(line)
    chosen[:] = [line for line in chosen if line not in failed]
    return failed


def _restore_coverage_failures(source, clean, chosen, raster_lines, pdf_path, placed) -> list:
    """A line whose composite carries extra ink falls back onto its original pixels."""
    failed = _coverage_failures(chosen, pdf_path, placed)
    if not failed:
        return []
    for line in failed:
        _paste_original(clean, source, line)
        line["mode"] = "raster"
        line["font"] = ""
        line["reason"] = "Ink was still under this line, so it stayed in the picture."
        raster_lines.append(line)
    chosen[:] = [line for line in chosen if line not in failed]
    return failed


def _coverage_failures(lines, pdf_path, placed) -> list:
    comp, vec = _render_ink_pair(pdf_path)
    if comp is None or vec is None:
        return []
    img_h, img_w = placed["image"].shape[:2]
    render_h, render_w = comp.shape[:2]
    if img_w < 1 or img_h < 1:
        return []
    sx = render_w / float(img_w)
    sy = render_h / float(img_h)
    failed = []
    for line in lines:
        if line.get("mode") != "vector":
            continue
        box = line.get("media_box")
        if not box or len(box) < 4:
            continue
        x, y, bw, bh = [float(v) for v in box[:4]]
        pad_x = max(2.0, bh * 0.25)
        pad_y = max(2.0, bh * 0.35)
        x0 = max(0, int(np.floor((x - pad_x) * sx)))
        y0 = max(0, int(np.floor((y - pad_y) * sy)))
        x1 = min(render_w, int(np.ceil((x + bw + pad_x) * sx)))
        y1 = min(render_h, int(np.ceil((y + bh + pad_y) * sy)))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        ratio = ink_excess(comp[y0:y1, x0:x1], vec[y0:y1, x0:x1])
        line["ink_excess"] = round(ratio, 3)
        if not excess_holds(ratio):
            failed.append(line)
    return failed


def _render_ink_pair(pdf_path: str, dpi: int = 120):
    """The press page, and the same page with the picture removed so only the type remains."""
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        comp = _pixmap_bgr(page, dpi)
        for info in list(page.get_images() or []):
            page.delete_image(info[0])
        vec = _pixmap_bgr(page, dpi)
    finally:
        doc.close()
    if comp.shape[:2] != vec.shape[:2]:
        vec = cv2.resize(vec, (comp.shape[1], comp.shape[0]), interpolation=cv2.INTER_AREA)
    return comp, vec


def _pixmap_bgr(page, dpi: int) -> np.ndarray:
    import pymupdf as fitz

    pix = page.get_pixmap(dpi=int(dpi), alpha=False, colorspace=fitz.csRGB)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def _spacing_failures(source, lines, pdf_path, placed, check_tokens: bool) -> list:
    import pymupdf as fitz

    from ai_rebuild import measure_rhythm

    vector = [line for line in lines if line.get("mode") == "vector" and (line.get("media_box") or line.get("rect"))]
    if not vector:
        return []
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        pix = page.get_pixmap(dpi=144, alpha=False, colorspace=fitz.csRGB)
        frame = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        page_w = float(page.rect.width) or 1.0
        page_h = float(page.rect.height) or 1.0
        scale_x = pix.width / page_w
        scale_y = pix.height / page_h
    finally:
        doc.close()
    img_h, img_w = placed["image"].shape[:2]
    sx = page_w / float(img_w)
    sy = page_h / float(img_h)
    crops = []
    originals = []
    for line in vector:
        # Word-gap QA stays on the ink box. The wider OCR rect is only for the
        # token read, so a letter left beside the line is seen without moving the gap.
        media = line.get("media_box")
        if media and len(media) >= 4:
            x, y, bw, bh = [float(v) for v in media[:4]]
        else:
            x, y, bw, bh = _qa_box(line, placed)
        frame_h = max(4.0, bh * sy * scale_y)
        x0 = max(0, int((x * sx) * scale_x) - int(frame_h * 0.55))
        y0 = max(0, int((y * sy) * scale_y) - int(frame_h * 0.35))
        x1 = min(frame.shape[1], int(((x + bw) * sx) * scale_x) + int(frame_h * 0.45))
        y1 = min(frame.shape[0], int(((y + bh) * sy) * scale_y) + int(frame_h * 0.35))
        crop = frame[y0:y1, x0:x1] if x1 - x0 >= 4 and y1 - y0 >= 4 else None
        crops.append(crop)
        bbox = line.get("bbox") or [0, 0, 1, 0.1]
        originals.append(measure_rhythm(source, bbox, line.get("text") or ""))
    reads = [(" ", 0.0)] * len(vector)
    if check_tokens:
        reads = _ocr_crops(frame, vector, placed, img_w, img_h, sx, sy, scale_x, scale_y)
    failed = []
    for line, crop, original, read in zip(vector, crops, originals, reads):
        text = str(line.get("text") or "")
        rebuild = {"measured": False, "gaps": [], "span": 0, "line_h": 0}
        if crop is not None and crop.size:
            rebuild = measure_rhythm(crop, [0, 0, 1, 1], text)
        if not gap_ems_hold(original, rebuild):
            line["spacing_why"] = "gap src=%s out=%s" % (
                [round(v, 3) for v in (original.get("gaps") or [])],
                [round(v, 3) for v in (rebuild.get("gaps") or [])],
            )
            failed.append(line)
            continue
        if check_tokens and not repeats_hold(text, read[0]):
            line["repeat_fail"] = True
            line["spacing_why"] = f"repeat:{read[0][:60]}"
            failed.append(line)
            continue
        if check_tokens and not tokens_hold(text, read[0], read[1]):
            line["spacing_why"] = f"tokens:{read[0][:40]}"
            failed.append(line)
    return failed


def _repeat_failures(source, lines, pdf_path, placed) -> list:
    """Lines whose final crop still shows a repeated word."""
    try:
        failed = _spacing_failures(source, lines, pdf_path, placed, True)
    except Exception:
        return []
    return [line for line in failed if line.get("repeat_fail")]


def _ocr_crops(frame, lines, placed, img_w, img_h, sx, sy, scale_x, scale_y) -> list:
    from ocr_reader import local_rows

    height, width = frame.shape[:2]
    blocks = []
    for line in lines:
        # Stay on this line. A wide OCR rect pulls in the line above and
        # turns its first word into a false 'A A'. A modest pad still sees a
        # leftover letter sitting on the same line.
        media = line.get("media_box")
        if media and len(media) >= 4:
            x, y, bw, bh = [float(v) for v in media[:4]]
        else:
            x, y, bw, bh = _qa_box(line, placed)
        bh_px = max(2.0, bh * sy * scale_y)
        x0 = max(0, (x * sx) * scale_x - bh_px * 0.85)
        y0 = max(0, (y * sy) * scale_y - bh_px * 0.45)
        bw_px = max(2.0, bw * sx * scale_x + bh_px * 1.7)
        bh_box = bh_px * 1.9
        blocks.append({
            "text": line.get("text") or "",
            "bbox": [x0 / width, y0 / height, bw_px / width, bh_box / height],
        })
    try:
        return _reread_many(frame, blocks, local_rows)
    except Exception:
        return [("", 0.0) for _ in lines]


def _public_line(line: dict) -> dict:
    payload = {
        "text": line.get("text") or "",
        "mode": line.get("mode") or "",
        "font": line.get("font") or "",
        "role": line.get("role") or "",
        "match": line.get("match"),
        "reason": line.get("reason") or "",
        "pt": line.get("pt") or 0,
    }
    if line.get("knockout"):
        payload["knockout"] = line.get("knockout")
    return payload


def _stamp_points(lines: list, bgr: np.ndarray, trim_h_mm: float) -> None:
    for line in lines:
        if line.get("pt"):
            continue
        line["pt"] = round(_line_points(bgr, line, trim_h_mm), 2)


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
        block = _block(
            index, text, x0, y0, max(2.0, x1 - x0), max(2.0, y1 - y0),
            width, height, bgr, float(item.get("score") or 0),
        )
        block["quad"] = [[float(point[0]) * back, float(point[1]) * back] for point in points[:4]]
        blocks.append(block)
    return _repair_short(bgr, _refine_blocks(bgr, _space_blocks(blocks, bgr)))


def _prefer_same_line(old_text: str, new_text: str) -> str:
    """Keep the sentence that matches the line. A tall box can include the line above."""
    new_text = " ".join(str(new_text or "").split())
    old_text = " ".join(str(old_text or "").split())
    if not new_text or not old_text or new_text.lower() == old_text.lower():
        return new_text
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", new_text) if part.strip()]
    if len(parts) < 2:
        return new_text
    best = new_text
    best_ratio = _line_ratio(old_text, new_text)
    for part in parts:
        ratio = _line_ratio(old_text, part)
        if ratio > best_ratio:
            best = part
            best_ratio = ratio
    return best


def _close_reread(old_text: str, new_text: str, old_score: float = 1.0) -> bool:
    """A second read may restore a letter. It may not swap a confident line."""
    old_core = re.sub(r"[^A-Za-z0-9]", "", old_text).upper()
    new_core = re.sub(r"[^A-Za-z0-9]", "", new_text).upper()
    if not old_core or not new_core:
        return False
    if old_core == new_core or _line_ratio(old_text, new_text) >= 0.72 or _edit(old_core, new_core) <= 1:
        return True
    # A weak page read, such as "Supts noral", can take a confident crop read.
    try:
        return float(old_score) < 0.8
    except (TypeError, ValueError):
        return False


def _line_ratio(left: str, right: str) -> float:
    left = re.sub(r"[^a-z0-9]", "", left.lower())
    right = re.sub(r"[^a-z0-9]", "", right.lower())
    if not left or not right:
        return 0.0
    # Shared prefix length, so a restored letter still counts as the same line.
    shared = 0
    for a, b in zip(left, right):
        if a != b:
            break
        shared += 1
    return shared / float(max(len(left), len(right)))


def _repair_short(bgr: np.ndarray, blocks: list) -> list:
    """A second read of short lines. Page OCR misreads script such as The Surface."""
    from ocr_reader import local_rows

    indexes = [
        index for index, block in enumerate(blocks)
        if 1 <= len(str(block.get("text") or "").split()) <= 8
    ]
    if not indexes:
        return blocks
    reads = _reread_many(bgr, [blocks[index] for index in indexes], local_rows)
    for index, (new_text, new_score) in zip(indexes, reads):
        block = blocks[index]
        old_text = str(block.get("text") or "")
        if not new_text:
            solo = _reread_many(bgr, [block], local_rows)
            new_text, new_score = solo[0]
        new_text = _prefer_same_line(old_text, new_text)
        try:
            old_score = float(block.get("score") or 0)
        except (TypeError, ValueError):
            old_score = 0.0
        old_core = re.sub(r"[^A-Za-z0-9]", "", old_text)
        new_core = re.sub(r"[^A-Za-z0-9]", "", new_text or "")
        from ai_rebuild import keeps_grouping

        if (
            new_text
            and new_score >= 0.97
            and new_score >= old_score
            and len(new_text.split()) >= len(old_text.split())
            and len(new_core) >= len(old_core)
            and len(new_core) >= max(4, int(len(old_core) * 0.8))
            and " ".join(new_text.split()).upper() != " ".join(old_text.split()).upper()
            and _close_reread(old_text, new_text, old_score)
            and keeps_grouping(old_text, new_text)
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
        from ai_rebuild import keeps_grouping

        if (
            new_text
            and new_score >= 0.9
            and new_score >= old_score
            and len(new_core) >= max(4, int(len(old_core) * 0.8))
            and new_spaced != old_spaced
            and (len(new_text.split()) >= len(old_text.split()) or new_score >= 0.99)
            and _close_reread(old_text, new_text, old_score)
            and keeps_grouping(old_text, new_text)
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


def _is_numeral(text: str) -> bool:
    raw = str(text or "").strip()
    return raw.isdigit() and len(raw) <= 2


def _compact_mark(text: str) -> bool:
    """One or two glyphs, not a word. Icons misread as 4, 99, or + stay as pixels."""
    raw = " ".join(str(text or "").split())
    if not raw or " " in raw or _is_phone(raw):
        return False
    core = re.sub(r"[^0-9A-Za-z]", "", raw)
    if core:
        return len(core) <= 2
    return len(raw) <= 2


def _near_badge(rect, badges: list) -> bool:
    """A short box whose centre sits on a circular icon, even if the box is a little wide."""
    if not rect or not badges:
        return False
    x, y, bw, bh = [float(v) for v in rect[:4]]
    if bw <= 0 or bh <= 0 or max(bw, bh) > min(bw, bh) * 3.2:
        return False
    cx = x + bw / 2.0
    cy = y + bh / 2.0
    for badge in badges:
        radius = float(badge.get("radius") or 0)
        if radius <= 0:
            continue
        dx = cx - float(badge.get("cx") or 0)
        dy = cy - float(badge.get("cy") or 0)
        if dx * dx + dy * dy <= (radius * 1.35) ** 2 and max(bw, bh) <= radius * 2.4:
            return True
    return False


def _bbox_tokens_fit(short: str, long: str) -> bool:
    """Every word of the shorter line appears, in order, in the longer line."""
    small = [token.lower() for token in _word_tokens(short)]
    large = [token.lower() for token in _word_tokens(long)]
    if not small or not large or len(small) >= len(large):
        return False
    index = 0
    for token in large:
        if index < len(small) and token == small[index]:
            index += 1
    return index == len(small)


def _region_overlap(a, b) -> bool:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return False
    ax, ay, aw, ah = [float(v) for v in a[:4]]
    bx, by, bw, bh = [float(v) for v in b[:4]]
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    smaller = min(aw * ah, bw * bh)
    if smaller <= 0:
        return False
    if inter / smaller >= 0.45:
        return True
    xover = max(0.0, x1 - x0)
    ygap = max(0.0, y0 - (min(ay, by) + (ah if ay <= by else bh)))
    # The fragment sits on the same row and shares horizontal space.
    return xover >= 0.35 * min(aw, bw) and ygap <= 0.45 * max(ah, bh)


def _union_bbox(a, b) -> list:
    ax, ay, aw, ah = [float(v) for v in a[:4]]
    bx, by, bw, bh = [float(v) for v in b[:4]]
    x0, y0 = min(ax, bx), min(ay, by)
    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
    return [x0, y0, max(0.001, x1 - x0), max(0.001, y1 - y0)]


def _dedupe_blocks(blocks: list) -> tuple[list, list]:
    """One box per region. A fragment whose words sit inside a longer line is dropped.

    The longer box grows to cover the fragment, so those pixels are erased with it
    and are not drawn a second time.
    """
    ordered = sorted(
        list(blocks or []),
        key=lambda block: (
            -len(_word_tokens(block.get("text") or "")),
            -float(block.get("score") or 0),
        ),
    )
    kept = []
    dupes = []
    for block in ordered:
        text = str(block.get("text") or "")
        match = None
        for other in kept:
            if not _region_overlap(block.get("bbox"), other.get("bbox")):
                continue
            if _bbox_tokens_fit(text, other.get("text") or "") or text.strip().lower() == str(other.get("text") or "").strip().lower():
                match = other
                break
        if match is None:
            kept.append(block)
            continue
        match["bbox"] = _union_bbox(match.get("bbox"), block.get("bbox"))
        match.pop("quad", None)
        dupes.append(block)
    return kept, dupes


def _share_block_weight(lines: list) -> None:
    """One stroke for a paragraph, and one caps face for a stack. No per-line jumps."""
    ordered = sorted(
        [line for line in lines if line.get("rect")],
        key=lambda line: (line["rect"][1], line["rect"][0]),
    )
    seen = set()
    for index, line in enumerate(ordered):
        if id(line) in seen or line.get("role") in ("script", "caps", "phone", "sans-caps", "numeral"):
            seen.add(id(line))
            continue
        block = [line]
        seen.add(id(line))
        _x, y, _w, height = line["rect"]
        bottom = y + height
        left = line["rect"][0]
        for other in ordered[index + 1:]:
            if other.get("role") in ("script", "caps", "phone", "sans-caps", "numeral"):
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
        strokes = sorted(float(item.get("stroke") or 0) for item in block)
        median = strokes[len(strokes) // 2]
        for item in block:
            item["stroke"] = median
    caps = [line for line in lines if line.get("role") == "caps" and line.get("rect")]
    groups: list = []
    for line in sorted(caps, key=lambda item: (item["rect"][0], item["rect"][1])):
        x, _y, _w, height = line["rect"]
        placed = False
        for group in groups:
            gx, _gy, _gw, gh = group[0]["rect"]
            if abs(height - gh) > max(height, gh) * 0.35:
                continue
            if abs(x - gx) > max(18, height):
                continue
            group.append(line)
            placed = True
            break
        if not placed:
            groups.append([line])
    ranks = {"cinzel-400": 0, "cinzel-500": 1, "cinzel-600": 2, "cinzel-700": 3}
    names = {0: "cinzel-400", 1: "cinzel-500", 2: "cinzel-600", 3: "cinzel-700"}
    for group in groups:
        if len(group) < 2:
            continue
        values = sorted(ranks.get(item.get("font"), 1) for item in group)
        font = names[values[len(values) // 2]]
        knocks = sorted(float(item.get("knockout") or 0) for item in group)
        knock = knocks[len(knocks) // 2]
        for item in group:
            item["font"] = font
            item["face_lock"] = True
            item["knockout"] = knock
            if knock:
                item["stroke"] = 0.0


def _drop_residual(clean, source, chosen, raster_lines, marks) -> list:
    """After the erase and before any type is drawn, leftover ink sends the line back.

    A wider mask gets one chance. If dark pixels are still above the local
    paper, the line is put back exactly as it was and left as pixels.
    """
    from vector_plate import _badge_protect

    protect = _badge_protect(marks, clean.shape[0], clean.shape[1])
    pending = list(chosen)
    for round_index in range(4):
        nxt = []
        restored = False
        for line in pending:
            avoid = [other for other in pending if other is not line]
            dirty = residue_remains(clean, line, avoid, protect) or _ink_remains(clean, source, line)
            if not dirty:
                nxt.append(line)
                continue
            if round_index == 0 and _widen_erase(clean, source, line, avoid, protect):
                if not (residue_remains(clean, line, avoid, protect) or _ink_remains(clean, source, line)):
                    nxt.append(line)
                    continue
            _paste_original(clean, source, line)
            record = dict(line)
            record.update(mode="raster", font="")
            record["reason"] = "Original ink was still under this line, so it stayed in the picture."
            raster_lines.append(record)
            restored = True
        pending = nxt
        if not restored:
            break
    return pending


def residue_remains(clean: np.ndarray, line: dict, avoid=None, protect=None) -> bool:
    """True when this erased line still holds ink that is darker than the local paper."""
    pixels = _residue_pixels(clean, line, avoid, protect)
    return pixels is not None and int(pixels.max()) > 0


def _residue_pixels(clean: np.ndarray, line: dict, avoid=None, protect=None):
    """Residual ink inside the erased box: pixels that still differ from the local paper."""
    window = _line_window(line)
    if window is None:
        return None
    height_img, width_img = clean.shape[:2]
    x0, y0, x1, y1, height = window
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width_img, x1), min(height_img, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    k = int(round(max(height, y1 - y0) * 0.55))
    if k % 2 == 0:
        k += 1
    k = max(9, k)
    pad = max(4, k // 2 + 2)
    rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
    rx1, ry1 = min(width_img, x1 + pad), min(height_img, y1 + pad)
    roi = clean[ry0:ry1, rx0:rx1]
    rh, rw = roi.shape[:2]
    k = min(k, rh if rh % 2 else rh - 1, rw if rw % 2 else rw - 1)
    if k < 3:
        return None
    paper = cv2.medianBlur(roi, k)
    dist = np.linalg.norm(
        cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
        - cv2.cvtColor(paper, cv2.COLOR_BGR2LAB).astype(np.float32),
        axis=2,
    )
    odd = dist > 16.0
    iy0, iy1 = y0 - ry0, y1 - ry0
    ix0, ix1 = x0 - rx0, x1 - rx0
    gate = np.zeros((rh, rw), np.bool_)
    gate[iy0:iy1, ix0:ix1] = True
    odd &= gate
    ornament = line.get("kept_ornament")
    if isinstance(ornament, np.ndarray) and ornament.shape[:2] == clean.shape[:2]:
        grown = cv2.dilate(ornament, np.ones((9, 9), np.uint8))
        odd &= grown[ry0:ry1, rx0:rx1] == 0
    if protect is not None and int(protect.max()) > 0:
        odd &= protect[ry0:ry1, rx0:rx1] == 0
    if avoid:
        for other in avoid:
            if other is line:
                continue
            other_window = _line_window(other)
            if other_window is None:
                continue
            ox0, oy0, ox1, oy1, _other_h = other_window
            ax0, ay0 = max(0, ox0 - rx0), max(0, oy0 - ry0)
            ax1, ay1 = min(rw, ox1 - rx0), min(rh, oy1 - ry0)
            if ax1 > ax0 and ay1 > ay0:
                odd[ay0:ay1, ax0:ax1] = False
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(odd.astype(np.uint8), 8)
    keep = np.zeros((rh, rw), np.uint8)
    window_area = max(1, (y1 - y0) * (x1 - x0))
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        comp_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        comp_w = int(stats[index, cv2.CC_STAT_WIDTH])
        if area < 8 or comp_w < 2:
            continue
        fill = area / float(max(1, comp_h * comp_w))
        if fill > 0.85 and comp_w > (x1 - x0) * 0.7 and comp_h > (y1 - y0) * 0.7:
            continue
        if area > window_area * 0.55:
            continue
        if comp_h >= height * 0.22 or area >= max(12, int(window_area * 0.03)):
            keep[labels == index] = 255
    if int(keep.max()) == 0:
        return None
    full = np.zeros((height_img, width_img), np.uint8)
    full[ry0:ry1, rx0:rx1] = keep
    return full


def _widen_erase(clean, source, line, avoid, protect) -> bool:
    """Inpaint the leftover ink again, on a wider mask that stays inside this line."""
    pixels = _residue_pixels(clean, line, avoid, protect)
    if pixels is None or int(pixels.max()) == 0:
        return False
    wide = cv2.dilate(pixels, np.ones((5, 7), np.uint8))
    window = _line_window(line)
    if window is None:
        return False
    height, width = clean.shape[:2]
    x0, y0, x1, y1, _height = window
    pad = 6
    gate = np.zeros((height, width), np.uint8)
    gate[max(0, y0 - pad):min(height, y1 + pad), max(0, x0 - pad):min(width, x1 + pad)] = 255
    wide = cv2.bitwise_and(wide, gate)
    for other in avoid or []:
        other_window = _line_window(other)
        if other_window is None:
            continue
        ox0, oy0, ox1, oy1, _other_h = other_window
        wide[max(0, oy0):min(height, oy1), max(0, ox0):min(width, ox1)] = 0
    ornament = line.get("kept_ornament")
    if isinstance(ornament, np.ndarray) and int(ornament.max()) > 0:
        wide[cv2.dilate(ornament, np.ones((9, 9), np.uint8)) > 0] = 0
    if protect is not None and int(protect.max()) > 0:
        wide[protect > 0] = 0
    if int(wide.max()) == 0:
        return False
    clean[:] = cv2.inpaint(clean, wide, 5, cv2.INPAINT_TELEA)
    mask = line.get("cleared_mask")
    if isinstance(mask, np.ndarray):
        line["cleared_mask"] = cv2.bitwise_or(mask, wide)
    else:
        line["cleared_mask"] = wide
    _grow_halo(clean, source, line, wide)
    return True


def _grow_halo(clean, source, line, wide) -> None:
    delta = np.abs(clean.astype(np.int16) - source.astype(np.int16)).sum(axis=2) > 15
    near = cv2.dilate(wide, np.ones((15, 15), np.uint8)) > 0
    extra = np.zeros(clean.shape[:2], np.uint8)
    extra[delta & near] = 255
    extra = cv2.bitwise_or(extra, wide)
    halo = line.get("cleared_halo")
    if isinstance(halo, np.ndarray):
        line["cleared_halo"] = cv2.bitwise_or(halo, extra)
    else:
        line["cleared_halo"] = extra


def _ink_remains(clean: np.ndarray, source: np.ndarray, line: dict) -> bool:
    """True when a full letter of this line is still sitting in the cleaned picture."""
    box = line.get("ink") if line.get("ink") else None
    if box and len(box) >= 4:
        x0, y0, x1, y1 = [int(v) for v in box[:4]]
    else:
        rect = line.get("rect")
        if not rect:
            return False
        x, y, bw, bh = [int(v) for v in rect[:4]]
        x0, y0, x1, y1 = x, y, x + bw, y + bh
    height = max(1, y1 - y0)
    pad_x = int(round(height * 0.5))
    pad_y = int(round(height * 0.2))
    limit_h, limit_w = source.shape[:2]
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(limit_w, x1 + pad_x), min(limit_h, y1 + pad_y)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    src = source[y0:y1, x0:x1]
    out = clean[y0:y1, x0:x1]
    red, green, blue = _hex_rgb(line.get("ink_hex") or line.get("color") or "#222222")
    target = np.array([blue * 255.0, green * 255.0, red * 255.0], np.float32)
    text = np.linalg.norm(src.astype(np.float32) - target, axis=2) < 46
    if int(text.sum()) < 12:
        return False
    unchanged = np.abs(src.astype(np.int16) - out.astype(np.int16)).sum(axis=2) < 36
    left = (text & unchanged).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(left, 8)
    for index in range(1, count):
        comp_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        comp_w = int(stats[index, cv2.CC_STAT_WIDTH])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if comp_h >= height * 0.42 and comp_w >= 2 and area >= 8:
            return True
    return False


def _inside_badge(rect, badges: list) -> bool:
    """An OCR box that sits inside a filled circle. A long line beside one does not."""
    if not rect or not badges:
        return False
    x, y, bw, bh = [float(v) for v in rect[:4]]
    if bw <= 0 or bh <= 0:
        return False
    cx = x + bw / 2.0
    cy = y + bh / 2.0
    for badge in badges:
        radius = float(badge.get("radius") or 0)
        if radius <= 0:
            continue
        dx = cx - float(badge.get("cx") or 0)
        dy = cy - float(badge.get("cy") or 0)
        if max(bw, bh) <= radius * 1.7 and dx * dx + dy * dy <= (radius * 0.9) ** 2:
            return True
        corners = ((x, y), (x + bw, y), (x, y + bh), (x + bw, y + bh))
        limit = (radius * 1.05) ** 2
        if all((px - float(badge["cx"])) ** 2 + (py - float(badge["cy"])) ** 2 <= limit for px, py in corners):
            return True
    return False


def _is_email(text: str) -> bool:
    raw = " ".join(str(text or "").split())
    return re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", raw) is not None


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
        if _is_numeral(text):
            block = dict(block)
            block["rescued"] = "numeral"
            try:
                block["score"] = max(float(block.get("score") or 0), 0.8)
            except (TypeError, ValueError):
                block["score"] = 0.8
            rescued.append(block)
            continue
        kind = _rescue_kind(text)
        if not kind:
            still.append(block)
            continue
        pending.append((block, kind, text))
    if not pending:
        return rescued, still
    from ai_rebuild import keeps_grouping

    reads = _reread_many(bgr, [block for block, _kind, _text in pending], local_rows)
    for (block, kind, text), found in zip(pending, reads):
        new_text, new_score = found
        if kind == "phone":
            chosen = text
            if new_text and _is_phone(new_text) and new_score >= 0.5 and keeps_grouping(text, new_text):
                chosen = new_text
            block = dict(block)
            block["text"] = chosen
            block["score"] = max(float(block.get("score") or 0), new_score, 0.8)
            block["rescued"] = "phone"
            rescued.append(block)
            continue
        if kind == "name":
            block = dict(block)
            if (
                new_text
                and new_score >= 0.55
                and 1 <= len(new_text.split()) <= 4
                and keeps_grouping(text, new_text)
            ):
                block["text"] = new_text
                block["score"] = new_score
            block["score"] = max(float(block.get("score") or 0), 0.72)
            block["rescued"] = "name"
            rescued.append(block)
            continue
        if new_text and new_score >= 0.88 and len(_letters(new_text)) >= 6 and keeps_grouping(text, new_text):
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
    if not words or _is_phone(text) or _is_email(text):
        return False
    if _upper_ratio(text) >= 0.72 and len(_letters(text)) >= 3:
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


def _choose_font(bgr: np.ndarray, block: dict, style: str = "serif", trim_h_mm: float | None = None) -> dict:
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
        "quad": block.get("quad"),
        "pt": round(_line_points(bgr, block, trim_h_mm), 2),
        "knockout": 0.0,
    }
    ink = _ink_mask(crop, record["color"]) if crop is not None else None
    if _is_numeral(text) or rescued == "numeral":
        key = "poppins-regular" if style == "sans" else "crimson"
        record.update(mode="vector", font=key, role="numeral", match=0.5)
        return _decorate(record, bgr, block, ink, trim_h_mm)
    if _is_email(text):
        key = "poppins-regular" if style == "sans" else "crimson"
        record.update(mode="vector", font=key, role="phone", match=0.8)
        return _decorate(record, bgr, block, ink, trim_h_mm)
    if _is_phone(text) or rescued == "phone":
        key = "poppins-regular" if style == "sans" else "crimson"
        record.update(mode="vector", font=key, role="phone", match=0.5)
        return _decorate(record, bgr, block, ink, trim_h_mm)
    try:
        ocr_score = float(block.get("score") or 0)
    except (TypeError, ValueError):
        ocr_score = 0.0
    if ink is None:
        record["reason"] = "The letters could not be separated from the picture."
        return record
    connected, _slanted = _script_signals(ink, text)
    role, key, score = _assign_role(text, ink, style, rescued)
    record["match"] = round(float(score), 3)
    record["role"] = role
    if role == "unsure":
        # A confident read of a script tagline is set in Parisienne. A weak read stays ink.
        if _tagline_hint(text) and ocr_score >= 0.9:
            record.update(mode="vector", font="parisienne", role="script", match=0.12)
            return _decorate(record, bgr, block, ink, trim_h_mm)
        record["reason"] = "This looked like script, so it stayed in the picture."
        return record
    confident = ocr_score >= 0.8 or rescued in ("name", "phone", "letters")
    # A joined script line with a confident read is set in the script face.
    # A weak read of script stays in the picture. It is not set in a serif.
    script_sure = score >= 0.12 or (connected and ocr_score >= 0.9)
    if role == "script" and not script_sure and ocr_score < SCRIPT_OCR_FLOOR and rescued != "name":
        record["reason"] = "Script lettering was hard to read, so it stayed in the picture."
        return record
    script_ok = role == "script" and script_sure and (confident or ocr_score >= 0.55 or rescued == "name")
    # A confident read is not enough when the face does not look like the ink.
    # A script line that was not detected would otherwise be set in the body serif.
    plain_ok = role != "script" and confident and score > 0
    if score < MATCH_FLOOR and rescued not in ("name", "phone", "letters") and not script_ok and not plain_ok:
        record["reason"] = "No font matched this line closely enough."
        return record
    record["mode"] = "vector"
    record["font"] = key
    return _decorate(record, bgr, block, ink, trim_h_mm)


def _line_points(bgr: np.ndarray, block: dict, trim_h_mm: float | None) -> float:
    """The line's height on the trim, in points. Zero when the page size is unknown."""
    height = max(1, int(bgr.shape[0]))
    box = block.get("bbox") or [0, 0, 0.1, 0.05]
    try:
        pixels = max(1.0, float(box[3]) * height)
    except (TypeError, ValueError):
        return 0.0
    if not trim_h_mm:
        return 0.0
    return pixels * (float(trim_h_mm) / float(height)) * (72.0 / 25.4)


def _sample_bg(bgr: np.ndarray, block: dict) -> str:
    """The paper colour just outside this line, used to thin a face that is still too heavy."""
    height, width = bgr.shape[:2]
    x, y, bw, bh = _rect(block, width, height)
    pad = max(3, int(round(min(bw, bh) * 0.45)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(width, x + bw + pad), min(height, y + bh + pad)
    parts = []
    if y0 < y:
        parts.append(bgr[y0:y, x0:x1].reshape(-1, bgr.shape[2]))
    if y + bh < y1:
        parts.append(bgr[y + bh:y1, x0:x1].reshape(-1, bgr.shape[2]))
    if x0 < x:
        parts.append(bgr[y:y + bh, x0:x].reshape(-1, bgr.shape[2]))
    if x + bw < x1:
        parts.append(bgr[y:y + bh, x + bw:x1].reshape(-1, bgr.shape[2]))
    parts = [part for part in parts if part.size]
    if not parts:
        return "#f4f1e4"
    edge = np.concatenate(parts, axis=0)
    blue, green, red = [int(v) for v in np.median(edge, axis=0)[:3]]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _decorate(record: dict, bgr: np.ndarray, block: dict, ink: Optional[np.ndarray], trim_h_mm: float | None = None) -> dict:
    """Word gaps, letter-spacing, and a stroke matched to this line's own ink."""
    record["gaps"] = []
    record["track"] = 0.0
    record["stroke"] = 0.0
    record["pt"] = round(_line_points(bgr, block, trim_h_mm), 2)
    if record.get("mode") != "vector":
        return record
    from ai_rebuild import measure_rhythm

    rhythm = measure_rhythm(bgr, block.get("bbox") or [0, 0, 1, 0.1], record.get("text") or "")
    record["gaps"] = list(rhythm.get("gaps") or [])
    record["track"] = float(rhythm.get("track") or 0.0)
    key = str(record.get("font") or "")
    red, green, blue = _hex_rgb(record.get("color") or "#222222")
    light_ink = 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.72
    # Cinzel SemiBold is heavier than most dark caps. Step down to Medium, then
    # Regular. A heading that is still heavy is thinned with a paper stroke.
    # Light lettering on a dark ground is left to the face: its mask is the edge.
    if key == "cinzel-600" and ink is not None and record.get("role") == "caps" and not light_ink:
        key = _lighter_caps(record, bgr, block, ink, trim_h_mm) or ""
        if record.get("mode") != "vector" or not key:
            return record
    if light_ink or record.get("knockout"):
        record["stroke"] = 0.0
        return record
    if ink is not None and key:
        record["stroke"] = _match_stroke(ink, str(record.get("text") or ""), key)
    return record


def _coverage(mask: np.ndarray) -> float:
    """Share of the tight ink box that is actually ink."""
    if mask is None or mask.size == 0:
        return 0.0
    ys, xs = np.where(mask > 40)
    if len(xs) < 8:
        return 0.0
    tight = mask[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
    return float((tight > 40).mean())


def _rel_stroke(mask: np.ndarray) -> float:
    """Stroke width at the 80th percentile, as a fraction of the ink height.

    The median follows hairline serifs. The upper part of the stroke is the
    weight a reader sees.
    """
    if mask is None or mask.size == 0:
        return 0.0
    binary = (mask > 40).astype(np.uint8)
    ys, xs = np.where(binary > 0)
    if len(xs) < 8:
        return 0.0
    tight = binary[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    dist = cv2.distanceTransform(tight, cv2.DIST_L2, 3)
    vals = dist[dist >= 0.5]
    if vals.size < 8 or tight.shape[0] < 2:
        return 0.0
    return float(np.percentile(vals, 80)) * 2.0 / float(tight.shape[0])


def _match_stroke(ink: np.ndarray, text: str, key: str) -> float:
    """Extra stroke so this face matches the original ink. Zero when the face is already heavier."""
    path = _FONT_FILES.get(key)
    if not path or ink is None:
        return 0.0
    role = _FONT_ROLE.get(key, "body")
    rendered = _render_ink(text, path, max(int(ink.shape[1]), 80), max(int(ink.shape[0]), 24), role)
    if rendered is None:
        return 0.0
    ink_stroke = _rel_stroke(ink)
    face_stroke = _rel_stroke(rendered)
    extra = ink_stroke - face_stroke
    if extra <= 0.008:
        return 0.0
    if role == "script":
        # The outline may not push ink coverage more than 10% past the original.
        source_cover = _coverage(ink)
        face_cover = _coverage(rendered)
        if source_cover > 0 and face_cover >= source_cover * 1.10:
            return 0.0
        room = max(0.0, ink_stroke * 1.10 - face_stroke)
        factor = float(min(0.012, extra * 0.45, room))
        if source_cover > 0 and face_cover > 0 and factor > 0:
            # A 1px outline on a thin script adds a lot of area. Stop before +10%.
            grown = cv2.dilate((rendered > 40).astype(np.uint8), np.ones((3, 3), np.uint8))
            if _coverage(grown * 255) > source_cover * 1.10:
                return 0.0
        return factor
    ratio = {"spaced": 0.70, "tagline": 0.78}.get(role, 0.82)
    return float(min(0.014, extra * ratio))


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
    if _is_phone(text) or _is_email(text):
        return "phone", "crimson", _score_key(ink, text, "crimson")
    connected, slanted = _script_signals(ink, text)
    script_key, script_score = _best_script(ink, text)
    body = _score_key(ink, text, "crimson")
    italic = _score_key(ink, text, "eb-italic")
    best_serif = max(body, italic)
    # Joined or clearly script-shaped letters stay script. They are never
    # swapped for a serif or an italic.
    # Joined strokes or a real lean. The scorer alone has to win by a wide
    # margin, or a noisy mask turns a body line into script.
    if connected or slanted or (script_score >= 0.22 and script_score >= best_serif + 0.15):
        return "script", script_key, script_score
    if _tagline_hint(text) or (rescued == "name" and not connected):
        if script_score >= 0.10 and script_score + 0.03 >= best_serif:
            return "script", script_key, script_score
        # A clear italic match can stay italic. A weak match is not set in a serif:
        # the original script ink stays in the picture.
        if _tagline_hint(text) and best_serif < 0.12 and script_score < 0.12:
            return "unsure", "", max(script_score, italic)
        if italic >= 0.12 and italic + 0.02 >= body:
            return "tagline", "eb-italic", italic
        if italic + 0.02 >= body and not _tagline_hint(text):
            return "tagline", "eb-italic", italic
        return "body", "crimson", body
    # Body copy stays one weight. Short list lines are not promoted to a bold head.
    return "body", "crimson", body


def _best_script(ink: np.ndarray, text: str) -> tuple[str, float]:
    # Parisienne is the house script. A script line is not set in another face.
    return "parisienne", _score_key(ink, text, "parisienne")


def _score_key(ink: np.ndarray, text: str, key: str) -> float:
    path = _FONT_FILES.get(key)
    if not path or not os.path.exists(path):
        return 0.0
    return _score_font(ink, text, path, _FONT_ROLE.get(key, "body"))


def _harmonise(lines: list, style: str) -> list:
    """One face per role and size, and one weight inside a paragraph."""
    if style == "serif":
        for line in lines:
            if line.get("face_lock"):
                continue
            role = line.get("role") or "body"
            if role == "script":
                continue
            if line.get("font") in SANS_KEYS or role in ROLE_FONT:
                line["font"] = ROLE_FONT.get(role, "crimson")
    scripts = [line for line in lines if line.get("role") == "script"]
    if style == "serif":
        for item in scripts:
            item["font"] = "parisienne"
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
            if item.get("face_lock"):
                continue
            item["font"] = winner
    _normalise_paragraphs(lines, style)
    return lines


def _normalise_paragraphs(lines: list, style: str) -> None:
    ordered = sorted(lines, key=lambda line: (line["rect"][1], line["rect"][0]))
    seen = set()
    for index, line in enumerate(ordered):
        if id(line) in seen or line.get("role") in ("script", "caps", "phone", "sans-caps", "numeral"):
            seen.add(id(line))
            continue
        block = [line]
        seen.add(id(line))
        _x, y, _w, height = line["rect"]
        bottom = y + height
        left = line["rect"][0]
        for other in ordered[index + 1:]:
            if other.get("role") in ("script", "caps", "phone", "sans-caps", "numeral"):
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


def _script_signals(ink: np.ndarray, text: str) -> tuple[bool, bool]:
    """Joined strokes, or a consistent lean measured on each letter. A solid bar is neither."""
    chars = sum(1 for ch in str(text or "") if ch.isalnum())
    if ink is None or ink.size == 0 or chars < 4:
        return False, False
    binary = (ink > 0).astype(np.uint8)
    if float(binary.mean()) > 0.55:
        return False, False
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    components = max(0, count - 1)
    connected = components <= max(2, int(chars * 0.45))
    leans = []
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_HEIGHT]) < 8 or int(stats[index, cv2.CC_STAT_AREA]) < 10:
            continue
        ys, xs = np.where(labels == index)
        height = max(1, int(stats[index, cv2.CC_STAT_HEIGHT]))
        top = xs[ys <= np.percentile(ys, 30)]
        bottom = xs[ys >= np.percentile(ys, 70)]
        if top.size < 2 or bottom.size < 2:
            continue
        leans.append((float(top.mean()) - float(bottom.mean())) / float(height))
    slanted = len(leans) >= 3 and abs(float(np.median(leans))) > 0.18
    return connected, slanted


def _scripty(ink: np.ndarray, text: str) -> bool:
    connected, slanted = _script_signals(ink, text)
    return connected or slanted


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


def _guide_boxes(bgr, blocks, lines) -> list:
    width, height = bgr.shape[1], bgr.shape[0]
    boxes = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        x, y, bw, bh = _rect(block, width, height)
        boxes.append((x, y, x + bw, y + bh))
    for line in lines:
        ink = line.get("ink")
        if ink:
            boxes.append(tuple(ink[:4]))
            continue
        rect = line.get("rect")
        if rect:
            x, y, bw, bh = rect
            boxes.append((x, y, x + bw, y + bh))
    return boxes


def _source_marks(bgr, lines) -> list:
    """Badges stay painted. Plain bullets are erased here and redrawn as vectors."""
    marks = []
    for line in lines:
        found = _mark_near(bgr, line["rect"])
        if not found:
            continue
        if found[0] == "badge":
            marks.append({"kind": "badge", "cx": found[1], "cy": found[2], "radius": found[3]})
            stripped = re.sub(r"^\d{1,2}\s+", "", str(line.get("text") or ""))
            if stripped.strip():
                line["text"] = stripped
            continue
        if any(
            mark.get("kind") == "bullet"
            and abs(found[1] - mark["cx"]) < 6
            and abs(found[2] - mark["cy"]) < 6
            for mark in marks
        ):
            continue
        marks.append({
            "kind": "bullet",
            "cx": found[1],
            "cy": found[2],
            "radius": found[3],
            "color": found[4],
        })
    return marks


def _map_bullets(marks, placed) -> list:
    bullets = []
    scale = float(placed.get("px_per_src") or 1.0)
    for mark in marks:
        if mark.get("kind") != "bullet":
            continue
        cx, cy = placed["map"](mark["cx"], mark["cy"])
        bullets.append((cx, cy, float(mark["radius"]) * scale, mark["color"]))
    return bullets


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
    cmyk = ImageCms.applyTransform(Image.fromarray(rgb), _press_cmyk())
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
    gap_fracs = list(line.get("gaps") or [])
    track = float(line.get("track") or 0.0)
    size, gap_fracs, track = _resolve_fit(font, text, box_w, box_h, role, gap_fracs, track)
    asc = size * (0.70 if role != "script" else 0.62)
    desc = size * 0.22 if any(ch in "gjpqy" for ch in text) else 0.0
    ink = asc + desc
    top_pad = max(0.0, (box_h - ink) / 2.0)
    baseline = y * sy + top_pad + asc
    colour = _cmyk(_hex_rgb(line.get("color") or "#222222"))
    advances = _char_advances(font, text, size, box_w, role, gap_fracs, track)
    writer = fitz.TextWriter(page.rect)
    cursor = x * sx
    for index, ch in enumerate(text):
        writer.append((cursor, baseline), ch, font=font, fontsize=size)
        cursor += advances[index] if index < len(advances) else font.text_length(ch, fontsize=size)
    try:
        knock = max(0.0, float(line.get("knockout") or 0.0))
    except (TypeError, ValueError):
        knock = 0.0
    stroke = 0.0 if knock else _stroke_for(line, size, role)
    writer.write_text(page, color=colour, render_mode=2 if (stroke or knock) else 0)
    if knock:
        _apply_knockout(page, size, knock, str(line.get("bg") or "#f4f1e4"))
    elif stroke:
        _restroke(page, size, stroke)


def _resolve_fit(font, text, box_w, box_h, role, gap_fracs, track):
    """Fit the face to the line. Sidebearings are not extra letter-spacing.

    Measured gaps that are only the face's own sidebearings used to be added
    again. That spread 'Skin conditions' into 'S k i n' and shrank the face
    to about 3.5 pt. Open caps such as AND HEALTH keep the surplus.
    """
    fitted, _ignored = _fit_width(font, text, box_w, box_h, role)
    gaps = list(gap_fracs or [])
    track = _spare_track(float(track or 0.0), float(box_w), fitted, role)
    size = _size_for_gaps(font, text, fitted, box_w, gaps, track)
    floor = 0.78 if role == "spaced" else 0.88
    if fitted > 0 and size < fitted * floor:
        track = 0.0
        size = _size_for_gaps(font, text, fitted, box_w, gaps, 0.0)
        if size < fitted * floor:
            size = fitted
            gaps = []
    return size, gaps, track


def _spare_track(track: float, box_w: float, size: float, role: str) -> float:
    """Ignore a letter gap that is already inside the face. Keep open caps tracking."""
    track = max(0.0, float(track or 0.0))
    if track <= 0 or box_w <= 0 or size <= 0:
        return 0.0
    gap = track * float(box_w)
    if role == "spaced":
        surplus = gap - 0.05 * float(size)
        if surplus <= 0.04 * float(size):
            return 0.0
        return surplus / float(box_w)
    if gap < 0.20 * float(size):
        return 0.0
    return track


def _size_for_gaps(font, text: str, size: float, box_w: float, gap_fracs: list, track: float = 0.0) -> float:
    """Shrink the face so the measured word gaps and letter-spacing still fit."""
    spaces = [index for index, ch in enumerate(text) if ch == " "]
    boundaries = _letter_boundaries(text)
    space_total = 0.0
    if spaces and len(gap_fracs) == len(spaces) and box_w > 0:
        space_total = sum(max(0.0, float(box_w) * float(frac)) for frac in gap_fracs)
    track_total = len(boundaries) * float(box_w) * max(0.0, float(track or 0.0))
    if space_total + track_total <= 0 or box_w <= 0:
        return size
    room = float(box_w) - space_total - track_total
    if room < float(box_w) * 0.34:
        return size
    glyph = sum(float(font.text_length(ch, fontsize=size)) for ch in text if ch != " ")
    if glyph > room > 0:
        size = max(3.5, size * room / glyph)
    return size


def _letter_boundaries(text: str) -> list:
    return [
        index for index in range(len(text) - 1)
        if text[index] != " " and text[index + 1] != " "
    ]


def _char_advances(font, text: str, size: float, box_w: float, role: str, gap_fracs: list, track: float = 0.0) -> list:
    """Per-character advances. Word spaces and letter-spacing follow the original pixels.

    Measured gaps are a fraction of the ink, not of an empty margin past the last letter.
    """
    widths = [float(font.text_length(ch, fontsize=size)) for ch in text]
    spaces = [index for index, ch in enumerate(text) if ch == " "]
    boundaries = _letter_boundaries(text)
    measured = len(gap_fracs) == len(spaces) and len(spaces) > 0
    tracked = float(track or 0.0) > 0 and bool(boundaries) and box_w > 0
    extra = [0.0] * len(text)
    if measured or tracked:
        glyphs = [index for index, ch in enumerate(text) if ch != " "]
        glyph_total = sum(widths[index] for index in glyphs)
        gap_share = sum(float(frac) for frac in gap_fracs) if measured else 0.0
        track_share = len(boundaries) * float(track) if tracked else 0.0
        share = gap_share + track_share
        if glyph_total > 0 and 0 < share < 0.72:
            span = glyph_total / (1.0 - share)
        else:
            span = float(box_w)
        span = min(span, float(box_w))
        if measured:
            for index, frac in zip(spaces, gap_fracs):
                widths[index] = max(span * float(frac), size * 0.18)
        else:
            for index in spaces:
                widths[index] = max(widths[index], size * 0.32)
        if tracked:
            each = span * float(track)
            for index in boundaries:
                extra[index] = each
        total = sum(widths) + sum(extra)
        if total > box_w + 0.2 and total > 0:
            scale = float(box_w) / total
            widths = [width * scale for width in widths]
            extra = [value * scale for value in extra]
        return [widths[index] + extra[index] for index in range(len(text))]
    for index in spaces:
        widths[index] = max(widths[index], size * 0.32)
    leftover = box_w - sum(widths)
    if leftover > 1.0 and boundaries:
        cap = {"spaced": 0.12, "script": 0.0, "tagline": 0.06}.get(role, 0.0) * size
        share = min(leftover / float(len(boundaries)), cap)
        for index in boundaries:
            extra[index] = share
    return [widths[index] + extra[index] for index in range(len(text))]


def _stroke_of(line: dict) -> float:
    """The stroke measured for this line. Light ink stays at the file weight."""
    red, green, blue = _hex_rgb(line.get("color") or "#222222")
    if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.72:
        return 0.0
    try:
        return max(0.0, float(line.get("stroke") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _lighter_caps(record: dict, bgr: np.ndarray, block: dict, ink: np.ndarray, trim_h_mm: float | None) -> str:
    """The heaviest Cinzel that is not heavier than this ink.

    Comparison is at least 72 px tall, so hinting does not hide Regular from Medium.
    A heading that is still heavy stays in Cinzel Regular with a thin paper stroke.
    Only a line under about 8 pt falls back to the original pixels.
    """
    text = str(record.get("text") or "")
    native_h = max(int(ink.shape[0]), 8)
    native_w = max(int(ink.shape[1]), 24)
    scale = max(1.0, 72.0 / float(native_h))
    if native_w * scale > 2200:
        scale = 2200.0 / float(native_w)
    height = max(48, int(round(native_h * scale)))
    width = max(80, int(round(native_w * scale)))
    ink_stroke = _rel_stroke(ink)
    deltas = {}
    for key in ("cinzel-600", "cinzel-500", "cinzel-400"):
        rendered = _render_ink(text, _FONT_FILES.get(key) or "", width, height, "spaced")
        if rendered is None:
            continue
        deltas[key] = _rel_stroke(rendered) - ink_stroke
    # A fresh render reads slightly heavier than the scanned ink. More than
    # about one percent is a heavier cut, so the next lighter face is used.
    for key in ("cinzel-600", "cinzel-500", "cinzel-400"):
        if key in deltas and deltas[key] <= 0.01:
            record["font"] = key
            record["knockout"] = 0.0
            if key != "cinzel-600":
                record["face_lock"] = True
            return key
    pt = float(record.get("pt") or 0.0)
    if not pt:
        pt = _line_points(bgr, block, trim_h_mm)
        record["pt"] = round(pt, 2)
    # An unknown page size is treated as a heading, so a direct check does not
    # raster large type just because the trim was not passed in.
    if pt == 0 or pt >= CAPS_INK_FLOOR_PT:
        delta = float(deltas.get("cinzel-400", 0.08))
        bg = _sample_bg(bgr, block)
        record["font"] = "cinzel-400"
        record["face_lock"] = True
        record["bg"] = bg
        record["knockout"] = _paper_knock(delta, str(record.get("color") or "#222222"), bg)
        record["stroke"] = 0.0
        return "cinzel-400"
    record["mode"] = "raster"
    record["font"] = ""
    record["face_lock"] = True
    record["knockout"] = 0.0
    record["reason"] = (
        f"This caps line is {pt:.1f} pt and the lightest face was still heavier, so it stayed in the picture."
    )
    return ""


def _paper_knock(delta: float, text_hex: str, bg_hex: str) -> float:
    """A small stroke in the paper colour. Zero when the paper matches the ink."""
    text_l = _luminance(text_hex)
    paper_l = _luminance(bg_hex)
    if abs(text_l - paper_l) < 0.18:
        return 0.0
    return round(min(0.012, max(0.0, (float(delta) - 0.02) * 0.35)), 4)


def _luminance(value: str) -> float:
    red, green, blue = _hex_rgb(value)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _stroke_for(line: dict, size: float, role: str) -> float:
    """The stroke measured for this line. Script is not given a fixed outline.

    A fixed outline made small script blobby. The measured stroke is already
    capped so the face stays within about 10% of the original ink.
    """
    return _stroke_of(line)


def _restroke(page, size: float, factor: float) -> None:
    """Replace PyMuPDF's 5% stroke with the press weight on the stream just written."""
    doc = page.parent
    xrefs = page.get_contents()
    if not xrefs:
        return
    xref = xrefs[-1]
    raw = doc.xref_stream(xref)
    if not raw:
        return
    text = raw.decode("latin1")
    width = f"{float(size) * float(factor):.4g}"
    updated, count = re.subn(r"(?m)^([0-9]*\.?[0-9]+) w$", width + " w", text, count=1)
    if count:
        doc.update_stream(xref, updated.encode("latin1"))


def _apply_knockout(page, size: float, factor: float, bg_hex: str) -> None:
    """Replace the stroke on the text just written with a thin outline in the paper colour.

    The fill stays the ink colour. Render mode 2 paints that stroke over the
    glyph edge, so the face reads lighter without a second text object.
    """
    doc = page.parent
    xrefs = page.get_contents()
    if not xrefs:
        return
    xref = xrefs[-1]
    raw = doc.xref_stream(xref)
    if not raw:
        return
    text = raw.decode("latin1")
    cyan, magenta, yellow, black = _cmyk(_hex_rgb(bg_hex))
    stroke_colour = f"{cyan:.4g} {magenta:.4g} {yellow:.4g} {black:.4g} K"
    updated, count = re.subn(
        r"(?m)^([0-9]*\.?[0-9]+(?:\s+[0-9]*\.?[0-9]+){3}) K$",
        stroke_colour,
        text,
        count=1,
    )
    if not count:
        return
    width = f"{float(size) * float(factor):.4g}"
    updated, width_count = re.subn(r"(?m)^([0-9]*\.?[0-9]+) w$", width + " w", updated, count=1)
    if width_count:
        doc.update_stream(xref, updated.encode("latin1"))


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


_PRESS_CMYK = None


def _press_cmyk():
    """sRGB to the press CMYK profile. Relative colorimetric, black point compensation."""
    global _PRESS_CMYK
    if _PRESS_CMYK is not None:
        return _PRESS_CMYK
    profile = "/usr/share/color/icc/ghostscript/default_cmyk.icc"
    _PRESS_CMYK = ImageCms.buildTransform(
        ImageCms.createProfile("sRGB"),
        ImageCms.getOpenProfile(profile),
        "RGB",
        "CMYK",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        flags=ImageCms.Flags.BLACKPOINTCOMPENSATION,
    )
    return _PRESS_CMYK


def _cmyk(rgb: tuple[float, float, float]) -> tuple[float, float, float, float]:
    red, green, blue = rgb
    # Dark neutral type is solid black. A grey K prints light on the press.
    lum = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    # Only a neutral dark snaps to solid K. A dark green, such as a script name, keeps its colour.
    if max(red, green, blue) - min(red, green, blue) < 0.045 and lum < 0.35:
        return (0.0, 0.0, 0.0, 1.0)
    if max(red, green, blue) < 0.12 and max(red, green, blue) - min(red, green, blue) < 0.04:
        return (0.0, 0.0, 0.0, 1.0)
    if min(red, green, blue) >= 0.96:
        return (0.0, 0.0, 0.0, 0.0)
    pixel = Image.new("RGB", (1, 1), tuple(int(round(channel * 255)) for channel in rgb))
    converted = ImageCms.applyTransform(pixel, _press_cmyk()).getpixel((0, 0))
    return tuple(channel / 255.0 for channel in converted)


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
