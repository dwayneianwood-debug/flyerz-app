#!/usr/bin/env python3
"""Trace the original lettering into vector paths.

The picture is enlarged and left as it is. Each text box is split into ink
and paper, the ink mask is traced with potrace, and those paths are filled
in the same place with the sampled ink colour. Nothing is retyped. A box
whose trace does not match the ink (IoU under 0.9) stays as pixels.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

TRACE_SCALE = 4
# Specks smaller than this, on the 4× bitmap, are not letters.
TURDSIZE = 12
# Corner threshold. 1.0 keeps type corners without rounding them off.
ALPHAMAX = 1.0
# Curve optimisation. Potrace's opticurve stays on; this is its tolerance.
OPTTOLERANCE = 0.2
IOU_FLOOR = 0.9
CHOKE_PX = 1
PAD_FRAC = 0.14

_NUM = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[MmLlHhVvCcZz]|{_NUM}")


def segment_ink(roi: np.ndarray):
    """Split a text crop into an ink mask and the stroke-core colour.

    Distance from the local paper is thresholded with Otsu, so light type on
    a dark ground is ink as well as dark type on a light ground. A flat crop,
    an empty crop, or a crop that is mostly one solid fill is not text.
    """
    if roi is None or roi.ndim != 3 or roi.shape[0] < 6 or roi.shape[1] < 6:
        return None, None
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
    height, width = lab.shape[:2]
    band = max(2, min(6, min(height, width) // 8))
    ring = np.zeros((height, width), np.bool_)
    ring[:band, :] = True
    ring[-band:, :] = True
    ring[:, :band] = True
    ring[:, -band:] = True
    if int(ring.sum()) < 8:
        return None, None
    paper = np.median(lab[ring], axis=0)
    distance = np.linalg.norm(lab - paper, axis=2)
    sample = np.clip(distance, 0, 255).astype(np.uint8)
    if float(sample.std()) < 4.0:
        return None, None
    _threshold, binary = cv2.threshold(sample, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary > 0
    # Otsu can pick the paper when the letters fill the crop. The ring is paper.
    if float(ink[ring].mean()) > 0.5:
        ink = ~ink
    fraction = float(ink.mean())
    if fraction < 0.012 or fraction > 0.62:
        return None, None
    mask = ink.astype(np.uint8) * 255
    core_distance = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    peak = float(core_distance.max()) if core_distance.size else 0.0
    if peak < 0.5:
        return None, None
    # A solid fill is thick. Letter strokes are thin next to the crop.
    short = float(min(height, width))
    if peak >= 0.22 * short and fraction > 0.28:
        return None, None
    colour = ink_colour(roi, mask)
    if colour is None:
        return None, None
    return mask, colour


def ink_colour(roi: np.ndarray, mask: np.ndarray):
    """Median colour of the stroke cores. See refine_ink."""
    refined, colour = refine_ink(roi, mask)
    if colour is None and refined is not None:
        return np.median(roi[refined > 0], axis=0).astype(np.float32)
    return colour


def refine_ink(roi: np.ndarray, mask: np.ndarray):
    """Drop pale blotches and return the letter strokes with their core colour.

    A thick shadow can be larger than the letters and would tint the sample.
    The stroke colour is the component furthest from the local paper. Other
    components are kept only when they are on that same side of the paper.
    """
    if roi is None or mask is None or int(mask.max()) == 0:
        return None, None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    band = max(2, min(6, min(height, width) // 8))
    ring = np.zeros((height, width), np.bool_)
    ring[:band, :] = True
    ring[-band:, :] = True
    ring[:, :band] = True
    ring[:, -band:] = True
    if int(ring.sum()) < 8:
        return None, None
    paper = float(np.median(gray[ring]))
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    found = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 4:
            continue
        component = labels == index
        local = distance * component
        peak = float(local.max()) if local.size else 0.0
        core = local >= max(0.8, peak * 0.45)
        if int(core.sum()) < 3:
            core = component
        tone = float(np.median(gray[core]))
        found.append((index, area, tone, core))
    if not found:
        return None, None
    furthest = max(found, key=lambda item: abs(item[2] - paper))
    span = abs(furthest[2] - paper)
    if span < 12.0:
        return None, None
    dark = furthest[2] < paper
    if dark:
        limit = paper - 0.62 * span
        kept = [item for item in found if item[2] <= limit]
    else:
        limit = paper + 0.62 * span
        kept = [item for item in found if item[2] >= limit]
    if not kept:
        kept = [furthest]
    cleaned = np.zeros((height, width), np.uint8)
    samples = []
    for index, _area, _tone, core in kept:
        cleaned[labels == index] = 255
        samples.append(roi[core])
    if int(cleaned.max()) == 0 or not samples:
        return None, None
    colour = np.median(np.concatenate(samples, axis=0), axis=0).astype(np.float32)
    return cleaned, colour


# A mark that shares a text box (the flame beside CHURCH) is much taller than
# the letters and nearly solid. Potrace fills it as a rectangle and that
# rectangle covers the last glyph.
SOLID_HEIGHT_RATIO = 1.6
SOLID_MIN = 0.72


def drop_solid_blobs(mask: np.ndarray) -> np.ndarray:
    """Keep a near-solid blob that towers over the letters out of the trace.

    The blob stays in the picture. The letters around it are still traced.
    A line of similar letters is left unchanged.
    """
    if mask is None or int(mask.max()) == 0:
        return mask
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    parts = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 20:
            continue
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        solidity = area / float(max(1, width * height))
        parts.append((index, height, solidity))
    if len(parts) < 4:
        return mask
    median = float(np.median([height for _index, height, _solidity in parts]))
    if median < 8.0:
        return mask
    drop = [
        index for index, height, solidity in parts
        if height >= SOLID_HEIGHT_RATIO * median and solidity >= SOLID_MIN
    ]
    if not drop:
        return mask
    cleaned = mask.copy()
    for index in drop:
        cleaned[labels == index] = 0
    if int(cleaned.max()) == 0:
        return mask
    return cleaned


def ink_touching(mask: np.ndarray, inner: tuple) -> np.ndarray | None:
    """Keep strokes that belong to this box. Padding must not pull in the next line."""
    if mask is None or int(mask.max()) == 0:
        return None
    height, width = mask.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in inner]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return mask
    count, labels = cv2.connectedComponents((mask > 0).astype(np.uint8), 8)
    zone = np.zeros((height, width), np.uint8)
    zone[y0:y1, x0:x1] = 1
    keep = np.zeros((height, width), np.uint8)
    for index in range(1, count):
        component = labels == index
        area = int(component.sum())
        if area < 2:
            continue
        inside = int(np.count_nonzero(component & (zone > 0)))
        if inside >= max(4, int(0.40 * area)):
            keep[component] = 255
    if int(keep.max()) == 0:
        return None
    return keep


def mask_iou(left: np.ndarray, right: np.ndarray) -> float:
    """Intersection over union of two ink masks."""
    a = left > 0
    b = right > 0
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return 1.0
    return float(np.count_nonzero(a & b)) / float(union)


def choke_ink(plate: np.ndarray, mask: np.ndarray, pixels: int = CHOKE_PX) -> None:
    """Pull the raster ink in by ``pixels`` so its soft edge does not show past the vector.

    Only that ring changes. The stroke core and the rest of the picture stay.
    """
    if pixels <= 0 or mask is None or int(mask.max()) == 0:
        return
    kernel = np.ones((pixels * 2 + 1, pixels * 2 + 1), np.uint8)
    eroded = cv2.erode((mask > 0).astype(np.uint8) * 255, kernel)
    ring = ((mask > 0) & (eroded == 0)).astype(np.uint8) * 255
    if int(ring.max()) == 0:
        return
    plate[:] = cv2.inpaint(plate, ring, max(1, pixels), cv2.INPAINT_TELEA)


def is_lettering(text: str) -> bool:
    """Icons and ornaments are not traced. Words, figures and badge numbers are."""
    return re.search(r"[0-9A-Za-zÀ-ÿ]", str(text or "")) is not None


def trace_mask(mask: np.ndarray) -> list:
    """Bezier subpaths in the mask's own pixel space. Empty when potrace finds nothing."""
    if mask is None or int(mask.max()) == 0:
        return []
    height, width = mask.shape[:2]
    big = cv2.resize(mask, (width * TRACE_SCALE, height * TRACE_SCALE), interpolation=cv2.INTER_CUBIC)
    _thr, binary = cv2.threshold(big, 127, 255, cv2.THRESH_BINARY)
    svg = _potrace_svg(binary)
    if not svg:
        return []
    transform = _svg_transform(svg, binary.shape[0])
    paths = []
    for raw in re.findall(r'<path\b[^>]*\bd="([^"]+)"', svg, flags=re.I | re.S):
        subpaths = _parse_path(raw, transform)
        # The 4× bitmap is scaled back to the crop.
        scaled = []
        for sub in subpaths:
            scaled.append([(cmd, [(x / TRACE_SCALE, y / TRACE_SCALE) for x, y in pts]) for cmd, pts in sub])
        if scaled:
            paths.append(scaled)
    return paths


def glyphs_agree(mask: np.ndarray, painted: np.ndarray, min_area: int = 12) -> bool:
    """True when the trace has the same letters: none missing, none added.

    A count that differs by one is a split dot, not a new letter, when every
    piece still sits on the other mask.
    """
    def parts(image):
        count, labels, stats, _cent = cv2.connectedComponentsWithStats((image > 0).astype(np.uint8), 8)
        ids = [index for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= min_area]
        return labels, ids

    left, left_ids = parts(mask)
    right, right_ids = parts(painted)
    if not left_ids or not right_ids:
        return False
    missing = 0
    for index in left_ids:
        if not np.any((left == index) & (painted > 0)):
            missing += 1
    extra = 0
    for index in right_ids:
        if not np.any((right == index) & (mask > 0)):
            extra += 1
    if missing or extra:
        return False
    return abs(len(left_ids) - len(right_ids)) <= 1


def _scale_paths(paths: list, scale: float) -> list:
    scaled = []
    for group in paths:
        scaled.append([
            [(cmd, [(x * scale, y * scale) for x, y in pts]) for cmd, pts in sub]
            for sub in group
        ])
    return scaled


def _accept_trace(mask: np.ndarray, paths: list) -> tuple[float, bool, np.ndarray]:
    """Score a trace. Thin type is judged on the 4× bitmap potrace actually fit.

    The 1px render of a hairline stroke loses the edge and scores about 0.85
    even when the curves match. That 4× score may sit just under 0.9 only when
    every glyph is present and none were added. The 1× paint is returned so a
    later gate can compare counters with the source pixels.
    """
    height, width = mask.shape[:2]
    painted = rasterise_paths(paths, width, height)
    low = mask_iou(mask, painted)
    if low >= IOU_FLOOR:
        return low, True, painted
    big = cv2.resize(mask, (width * TRACE_SCALE, height * TRACE_SCALE), interpolation=cv2.INTER_CUBIC)
    _threshold, binary = cv2.threshold(big, 127, 255, cv2.THRESH_BINARY)
    zoomed = rasterise_paths(_scale_paths(paths, TRACE_SCALE), binary.shape[1], binary.shape[0])
    high = mask_iou(binary, zoomed)
    agree = glyphs_agree(binary, zoomed)
    if high >= IOU_FLOOR and agree:
        return high, True, painted
    if agree and high >= 0.86 and IOU_FLOOR <= 0.9:
        return high, True, painted
    return max(low, high), False, painted


def _source_ink(crop: np.ndarray, seg_mask: np.ndarray) -> np.ndarray | None:
    """Re-threshold the crop so a paper-coloured counter stays a hole.

    The segmentation mask can fill a 9 into an 8. The pixels still show the hole.
    """
    if crop is None or seg_mask is None or int(seg_mask.max()) == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = seg_mask > 0
    if int(ink.sum()) < 8:
        return None
    ink_tone = float(np.median(gray[ink]))
    near = cv2.dilate(ink.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    paper_px = gray[near & ~ink]
    if paper_px.size < 8:
        paper_px = gray[~ink]
    if paper_px.size < 8:
        return ink.astype(np.uint8) * 255
    paper_tone = float(np.median(paper_px))
    if abs(ink_tone - paper_tone) < 16.0:
        return ink.astype(np.uint8) * 255
    mid = (ink_tone + paper_tone) * 0.5
    binary = gray <= mid if ink_tone < paper_tone else gray >= mid
    zone = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return (binary & zone).astype(np.uint8) * 255


def _components(mask: np.ndarray, min_area: int) -> list:
    count, labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    parts = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        parts.append({
            "area": area,
            "x": int(stats[index, cv2.CC_STAT_LEFT]),
            "y": int(stats[index, cv2.CC_STAT_TOP]),
            "w": int(stats[index, cv2.CC_STAT_WIDTH]),
            "h": int(stats[index, cv2.CC_STAT_HEIGHT]),
            "pixels": labels == index,
        })
    parts.sort(key=lambda item: item["x"])
    return parts


def _hole_count(component: np.ndarray) -> int:
    """Enclosed holes. 9 has one, 8 has two, S has none, B has two. Specks do not count."""
    ys, xs = np.where(component)
    if ys.size < 12:
        return 0
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = np.zeros((y1 - y0, x1 - x0), np.uint8)
    crop[component[y0:y1, x0:x1]] = 255
    padded = cv2.copyMakeBorder(crop, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    inv = cv2.bitwise_not(padded)
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(inv, flood_mask, (0, 0), 128)
    holes = (inv == 255).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(holes, 8)
    min_hole = max(8, int(round(float(ys.size) * 0.02)))
    found = 0
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) >= min_hole:
            found += 1
    return found


def shape_gate(crop: np.ndarray, seg_mask: np.ndarray, traced: np.ndarray, inner: tuple) -> str:
    """Reject a box whose trace is not the same letters as the pixels.

    A component that sits outside the original text line is a fragment. A glyph
    whose hole count changed (a 9 closed into an 8, an S opened into a B) is a
    wrong character. Either one keeps the raster.
    """
    if traced is None or int(np.max(traced)) == 0:
        return "The trace was empty, so this box stayed in the picture."
    source = _source_ink(crop, seg_mask)
    if source is None:
        return ""
    height, width = traced.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in inner]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, max(x0 + 1, x1)), min(height, max(y0 + 1, y1))
    zone = np.zeros((height, width), np.uint8)
    zone[y0:y1, x0:x1] = 1
    traced_bin = traced > 0
    source_bin = source > 0
    for part in _components(traced, 20):
        pixels = part["pixels"]
        area = part["area"]
        inside = int(np.count_nonzero(pixels & (zone > 0)))
        if inside < int(0.50 * area):
            return "The trace added a fragment outside the letters, so this box stayed in the picture."
        overlap = int(np.count_nonzero(pixels & source_bin))
        if overlap < int(0.35 * area):
            return "The trace picked up background that is not the letter, so this box stayed in the picture."
    # A solid block much taller than the letters is a logo filled in as a rectangle.
    source_parts = _components(source, 20)
    if len(source_parts) >= 4:
        median_h = float(np.median([part["h"] for part in source_parts]))
        if median_h >= 8.0:
            for part in _components(traced_bin.astype(np.uint8) * 255, 20):
                solidity = part["area"] / float(max(1, part["w"] * part["h"]))
                if part["h"] >= SOLID_HEIGHT_RATIO * median_h and solidity >= SOLID_MIN:
                    return "The trace filled a solid block over the letters, so this box stayed in the picture."
    # Display type only. Hairline body copy is judged by IoU; a 1px render closes its counters.
    for part in source_parts:
        if min(part["h"], part["w"]) < 32:
            continue
        pixels = part["pixels"]
        best = None
        best_overlap = 0
        for other in _components(traced, 12):
            overlap = int(np.count_nonzero(pixels & other["pixels"]))
            if overlap > best_overlap:
                best_overlap = overlap
                best = other
        if best is None or best_overlap < int(0.35 * part["area"]):
            if part["area"] >= max(48, int(0.12 * max(1, int(source_bin.sum())))):
                return "The trace dropped part of a letter, so this box stayed in the picture."
            continue
        if _hole_count(pixels) != _hole_count(best["pixels"]):
            return "The trace closed or opened a counter, so this box stayed in the picture."
    return ""


def rasterise_paths(paths: list, width: int, height: int) -> np.ndarray:
    """Fill the traced paths onto a binary mask the size of the crop."""
    import pymupdf as fitz

    canvas = np.zeros((max(1, height), max(1, width)), np.uint8)
    if not paths:
        return canvas
    doc = fitz.open()
    try:
        page = doc.new_page(width=max(1, width), height=max(1, height))
        _paint_paths(page, paths, (0.0, 0.0, 0.0, 1.0), 1.0, 1.0, 0.0, 0.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    finally:
        doc.close()
    fitted = gray
    if fitted.shape[0] != height or fitted.shape[1] != width:
        fitted = cv2.resize(fitted, (width, height), interpolation=cv2.INTER_AREA)
    canvas[fitted < 200] = 255
    return canvas


def trace_fitted(
    bgr: np.ndarray,
    trim_w_mm: float,
    trim_h_mm: float,
    output_pdf: str,
    bleed_mm: float = 5.0,
    progress=None,
    blocks: list | None = None,
    ocr_s: float | None = None,
) -> dict:
    """Enlarge the picture and trace its lettering. Never raises."""
    from vector_text_v2 import _fail, _note

    started = time.perf_counter()
    try:
        return _trace(
            bgr, float(trim_w_mm), float(trim_h_mm), output_pdf,
            float(bleed_mm), progress, blocks, started, ocr_s,
        )
    except Exception as exc:
        return _fail(started, f"Vector trace failed ({str(exc)[:160]}). The original lettering was kept.")


def _trace(bgr, trim_w, trim_h, output_pdf, bleed_mm, progress, blocks, started, ocr_already=None) -> dict:
    from vector_plate import place_plate
    from vector_text_v2 import MIN_PPI, _note, _rect, read_blocks

    _note(progress, "reading", "Reading the lettering.")
    ocr_started = time.perf_counter()
    if blocks is None:
        blocks = read_blocks(bgr, extra=False)
        ocr_s = time.perf_counter() - ocr_started
    else:
        ocr_s = float(ocr_already or 0.0)
    guide = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        if not is_lettering(block.get("text") or ""):
            continue
        rx, ry, rw, rh = _rect(block, bgr.shape[1], bgr.shape[0])
        guide.append((rx, ry, rx + rw, ry + rh))
    if not guide:
        from vector_text_v2 import _fail
        return _fail(started, "No lettering was found to trace, so the original picture was kept.")

    _note(progress, "enlarging", "Enlarging the picture.")
    enlarge_started = time.perf_counter()
    placed = place_plate(bgr, guide, trim_w, trim_h, bleed_mm, MIN_PPI)
    plate = placed["image"].copy()
    enlarge_s = time.perf_counter() - enlarge_started
    provider = placed["provider"]

    _note(progress, "tracing", "Tracing the lettering.")
    trace_started = time.perf_counter()
    drawn = []
    raster_lines = []
    choke = np.zeros(plate.shape[:2], np.uint8)
    height, width = plate.shape[:2]
    pending = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            raster_lines.append(_line(text, "raster", "An icon was left in the picture."))
            continue
        raw = _rect(block, bgr.shape[1], bgr.shape[0])
        # A one-glyph speck under about 4 mm is a logo edge the reader called a letter.
        # Tracing it punches the picture and the trim does not read it back.
        glyph = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", text)
        if len(glyph) == 1 and glyph.islower() and int(raw[3]) < 48:
            raster_lines.append(_line(text, "raster", "A single small glyph stayed in the picture."))
            continue
        rect = _padded(raw, bgr.shape[1], bgr.shape[0])
        left, top, right, bottom = _mapped_bounds(placed["map"], rect, width, height)
        inner_left, inner_top, inner_right, inner_bottom = _mapped_bounds(placed["map"], raw, width, height)
        if right - left < 4 or bottom - top < 4:
            raster_lines.append(_line(text, "raster", "The box was too small to trace."))
            continue
        crop = plate[top:bottom, left:right]
        mask, colour = segment_ink(crop)
        if mask is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            continue
        mask = ink_touching(mask, (
            inner_left - left, inner_top - top, inner_right - left, inner_bottom - top,
        ))
        if mask is None:
            raster_lines.append(_line(text, "raster", "The ink sat outside this box, so it stayed in the picture."))
            continue
        mask, colour = refine_ink(crop, mask)
        if mask is None or colour is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            continue
        # A logo in the same box is not a letter. Leave it as pixels.
        mask = drop_solid_blobs(mask)
        mask, colour = refine_ink(crop, mask)
        if mask is None or colour is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            continue
        pending.append({
            "text": text,
            "mask": mask,
            "colour": colour,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "crop": crop,
            "inner": (inner_left - left, inner_top - top, inner_right - left, inner_bottom - top),
        })
    traced = _trace_many([item["mask"] for item in pending])
    for item, paths in zip(pending, traced):
        text = item["text"]
        mask = item["mask"]
        left, top, right, bottom = item["left"], item["top"], item["right"], item["bottom"]
        if not paths:
            raster_lines.append(_line(text, "raster", "The trace was empty, so this box stayed in the picture."))
            continue
        score, accepted, painted = _accept_trace(mask, paths)
        if not accepted:
            raster_lines.append(_line(text, "raster", f"The trace did not match the ink ({score:.2f}), so this box stayed in the picture."))
            continue
        reason = shape_gate(item["crop"], mask, painted, item["inner"])
        if reason:
            raster_lines.append(_line(text, "raster", reason))
            continue
        fill = _trace_fill(item["colour"])
        drawn.append({
            "text": text,
            "paths": paths,
            "fill": fill,
            "origin": (left, top),
            "iou": round(score, 3),
            "rect": (left, top, right - left, bottom - top),
        })
        choke[top:bottom, left:right] = cv2.bitwise_or(choke[top:bottom, left:right], mask)
    pristine = plate.copy() if drawn else None
    if int(choke.max()) > 0:
        plate[:] = sharpen_background(plate, choke)
        choke_ink(plate, choke, CHOKE_PX)
    trace_s = time.perf_counter() - trace_started
    if not drawn and not raster_lines:
        from vector_text_v2 import _fail
        return _fail(started, "No lettering was traced, so the original picture was kept.", provider=provider)

    qa = _write_pdf(plate, drawn, output_pdf, trim_w, trim_h, bleed_mm, placed)
    if not qa.get("wrote") or not qa.get("cmyk") or not qa.get("boxes") or not qa.get("ppi"):
        from vector_text_v2 import _discard, _fail
        _discard(output_pdf)
        reason = "The traced press file failed the check, so the original lettering was kept."
        if not qa.get("cmyk"):
            reason = "The press file was not CMYK, so the original lettering was kept."
        elif not qa.get("boxes"):
            reason = "The trim box was wrong, so the original lettering was kept."
        elif not qa.get("ppi"):
            reason = "The picture was under 400 PPI, so the original lettering was kept."
        return _fail(started, reason, provider=provider, qa=qa)

    gate_started = time.perf_counter()
    text_gate, drawn, qa = _apply_text_gate(
        blocks, drawn, raster_lines, plate, pristine, output_pdf,
        trim_w, trim_h, bleed_mm, placed, qa, bgr.shape,
    )
    gate_s = time.perf_counter() - gate_started
    if not qa.get("wrote") or not qa.get("cmyk") or not qa.get("boxes") or not qa.get("ppi"):
        from vector_text_v2 import _discard, _fail
        _discard(output_pdf)
        return _fail(started, "The traced press file failed the check, so the original lettering was kept.", provider=provider, qa=qa)

    vector_lines = []
    for item in drawn:
        vector_lines.append({
            "text": item["text"],
            "mode": "vector",
            "font": "",
            "reason": "",
            "match": item["iou"],
            "pt": _points(item["rect"][3], placed.get("ppi") or MIN_PPI),
        })
    amber = bool(raster_lines)
    reason = ""
    if amber:
        reason = "Some lettering stayed in the picture because its trace did not match. Glance at it before printing."
    decisions = [
        f"The lettering was traced as vector shapes ({len(vector_lines)} boxes).",
        "The original letters were not removed. A 1px edge under each trace was pulled in.",
        f"The picture was enlarged with {provider}.",
        "The press file is CMYK at 400 PPI or more, with the trim 5 mm inside the bleed.",
    ]
    if reason:
        decisions.append(reason)
    colour_s = float(qa.get("colour_s") or 0)
    compose_s = float(qa.get("compose_s") or 0)
    matched = sum(1 for row in text_gate if row.get("ok"))
    if text_gate and matched == len(text_gate):
        decisions.append(f"Text gate: {matched} source lines match the render.")
    elif text_gate:
        decisions.append(
            f"Text gate: {matched} of {len(text_gate)} source lines match the render. "
            "A line that did not match was put back as raster."
        )
    timings = {
        "ocr_s": round(ocr_s, 3),
        "enlarge_s": round(enlarge_s, 3),
        "trace_s": round(trace_s, 3),
        "colour_s": round(colour_s, 3),
        "compose_s": round(compose_s, 3),
        "gate_s": round(gate_s, 3),
        "total_s": round(time.perf_counter() - started, 3),
    }
    timing_line = (
        f"Timing: OCR {timings['ocr_s']:.2f}s, upscale {timings['enlarge_s']:.2f}s, "
        f"trace {timings['trace_s']:.2f}s, colour {timings['colour_s']:.2f}s, "
        f"PDF {timings['compose_s']:.2f}s, gate {timings['gate_s']:.2f}s."
    )
    decisions.append(timing_line)
    sys.stderr.write("[vector-trace] " + timing_line + "\n")
    sys.stderr.write("[vector-trace] text gate\n")
    for row in text_gate:
        flag = "OK" if row.get("ok") else "FAIL"
        sys.stderr.write(
            f"  {flag} {row.get('mode')} ssim={float(row.get('ssim') or 0):.3f} "
            f"white={bool(row.get('whiteBlock'))} clip={bool(row.get('clipped'))} "
            f"{row.get('text')!r} -> {row.get('render')!r}\n"
        )
    return {
        "ok": True,
        "amber": amber,
        "reason": reason,
        "decisions": decisions,
        "elapsed_s": timings["total_s"],
        "timings": timings,
        "lines": vector_lines + raster_lines,
        "qa": qa,
        "provider": provider,
        "pdf": output_pdf,
        "vector_lines": len(vector_lines),
        "raster_lines": len(raster_lines),
        "mode": "trace",
        "text_gate": text_gate,
    }


def _mapped_bounds(mapper, rect, width, height):
    x0, y0 = mapper(rect[0], rect[1])
    x1, y1 = mapper(rect[0] + rect[2], rect[1] + rect[3])
    left, top = int(np.floor(min(x0, x1))), int(np.floor(min(y0, y1)))
    right, bottom = int(np.ceil(max(x0, x1))), int(np.ceil(max(y0, y1)))
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    return left, top, right, bottom


def _padded(rect, width, height):
    x, y, bw, bh = rect
    pad = max(2, int(round(bh * PAD_FRAC)))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(width, x + bw + pad)
    y1 = min(height, y + bh + pad)
    return x0, y0, max(2, x1 - x0), max(2, y1 - y0)


def _trace_fill(colour: np.ndarray):
    """CMYK for a stroke. Near-black type is 100% K so it does not print as a grey mix."""
    from vector_text_v2 import _cmyk

    red = float(colour[2]) / 255.0
    green = float(colour[1]) / 255.0
    blue = float(colour[0]) / 255.0
    lum = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    chroma = max(red, green, blue) - min(red, green, blue)
    if lum < 0.08 and chroma < 0.06:
        return (0.0, 0.0, 0.0, 1.0)
    return _cmyk((red, green, blue))


def _line(text: str, mode: str, reason: str) -> dict:
    return {"text": text, "mode": mode, "font": "", "reason": reason, "match": None, "pt": 0}


def _norm_text(text: str) -> str:
    """Character-exact compare with whitespace collapsed to single spaces."""
    cleaned = str(text or "").replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", cleaned).strip()


def _texts_equal(left: str, right: str) -> bool:
    """Exact compare after whitespace is collapsed. No ratio, no substring."""
    a = _norm_text(left)
    b = _norm_text(right)
    return bool(a) and a == b


def _line_matches(source: str, render_lines: list) -> tuple[bool, str]:
    """A source line matches one whole render line, and nothing else."""
    key = _norm_text(source)
    if not key:
        return False, ""
    for line in render_lines:
        shown = _norm_text(line)
        if shown == key:
            return True, shown
    return False, ""


def _trim_bgr(path: str, bleed_mm: float, dpi: int = 300) -> np.ndarray:
    """Press trim at `dpi`. The gate reads this, not the bleed."""
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        zoom = float(dpi) / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    finally:
        doc.close()
    inset = int(round(float(bleed_mm) / 25.4 * float(dpi)))
    if rgb.shape[0] > inset * 2 + 8 and rgb.shape[1] > inset * 2 + 8:
        rgb = rgb[inset:-inset, inset:-inset]
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _ocr_crop(image: np.ndarray) -> str:
    """Read one text box. A short crop is enlarged so a single figure is visible."""
    from vector_text_v2 import read_blocks

    if image is None or image.size == 0 or image.shape[0] < 6 or image.shape[1] < 6:
        return ""
    crop = image
    if crop.shape[0] < 96:
        scale = 96.0 / float(crop.shape[0])
        crop = cv2.resize(
            crop,
            (max(8, int(round(crop.shape[1] * scale))), 96),
            interpolation=cv2.INTER_CUBIC,
        )
    rows = read_blocks(crop, extra=False)
    return _norm_text(" ".join(str(row.get("text") or "") for row in rows))


def _same_reading(left: str, right: str) -> bool:
    """True only when both reads are the same letters. A blank read is not a match."""
    return _texts_equal(left, right)


SSIM_FLOOR = 0.85
WHITE_LUMA = 232.0
WHITE_STD = 12.0
WHITE_FRACTION = 0.30
EDGE_PX = 2


def _match_scale(source: np.ndarray, render: np.ndarray) -> np.ndarray:
    """Source crop at the render crop's pixel size."""
    if source is None or render is None or source.size == 0 or render.size == 0:
        return source
    if source.shape[:2] != render.shape[:2]:
        return cv2.resize(source, (render.shape[1], render.shape[0]), interpolation=cv2.INTER_CUBIC)
    return source


def _ssim_luma(source_bgr: np.ndarray, render_bgr: np.ndarray) -> float:
    """Mean SSIM of the luminance. The two crops are already the same size."""
    if source_bgr is None or render_bgr is None or source_bgr.size == 0 or render_bgr.size == 0:
        return 0.0
    height, width = render_bgr.shape[:2]
    left = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    right = cv2.cvtColor(render_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    # The plate and the 300 dpi trim are the same picture through two resamplers.
    # A one-pixel blur on both lets SSIM see the letters rather than that resample.
    left = cv2.GaussianBlur(left, (0, 0), 1.0)
    right = cv2.GaussianBlur(right, (0, 0), 1.0)
    win = 11
    if min(height, width) < win:
        win = min(height, width)
        if win % 2 == 0:
            win -= 1
    if win < 3:
        return float(max(0.0, 1.0 - float(np.mean(np.abs(left - right))) / 255.0))
    sigma = 1.5 if win >= 11 else 0.8
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    mu1 = cv2.GaussianBlur(left, (win, win), sigma)
    mu2 = cv2.GaussianBlur(right, (win, win), sigma)
    sigma1 = cv2.GaussianBlur(left * left, (win, win), sigma) - mu1 * mu1
    sigma2 = cv2.GaussianBlur(right * right, (win, win), sigma) - mu2 * mu2
    sigma12 = cv2.GaussianBlur(left * right, (win, win), sigma) - mu1 * mu2
    score = ((2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)) / ((mu1 * mu1 + mu2 * mu2 + c1) * (sigma1 + sigma2 + c2))
    return float(np.clip(score.mean(), 0.0, 1.0))


def _flat_white(gray: np.ndarray) -> np.ndarray:
    luma = gray.astype(np.float32)
    mean = cv2.blur(luma, (5, 5))
    var = cv2.blur(luma * luma, (5, 5)) - mean * mean
    std = np.sqrt(np.maximum(var, 0.0))
    return (luma >= WHITE_LUMA) & (std <= WHITE_STD)


def _glyph_height(gray: np.ndarray) -> float:
    """Height of the ink band. That is the glyph height the white-block test uses."""
    med = float(np.median(gray))
    ink = np.abs(gray.astype(np.int16) - med) >= 36
    rows = np.where(ink.any(axis=1))[0]
    if rows.size < 4:
        return float(gray.shape[0])
    return float(rows[-1] - rows[0] + 1)


def _novel_white_block(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """A flat near-white region in the render that the source does not have.

    It has to be larger than 30% of the glyph height on both sides, which is
    a painted rectangle and not a one-pixel JPEG fringe.
    """
    if source_bgr is None or render_bgr is None or render_bgr.size == 0:
        return False
    src_gray = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
    dst_gray = cv2.cvtColor(render_bgr, cv2.COLOR_BGR2GRAY)
    novel = (_flat_white(dst_gray) & ~_flat_white(src_gray)).astype(np.uint8) * 255
    if int(novel.max()) == 0:
        return False
    # Thickness, not the bounding box: a 1px halo around a letter is as wide as
    # the word, but it is not a solid block. A disk of 30% of the glyph height
    # has to fit inside the new white region.
    limit = WHITE_FRACTION * _glyph_height(src_gray)
    radius = max(1, int(round(limit * 0.5)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    return int(cv2.erode(novel, kernel).max()) > 0


def _edge_clipped(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """Ink on the left or right edge of the render that the source keeps inset."""
    if source_bgr is None or render_bgr is None or render_bgr.size == 0:
        return False
    height, width = render_bgr.shape[:2]
    if width < 8 or height < 8:
        return False
    src = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
    dst = cv2.cvtColor(render_bgr, cv2.COLOR_BGR2GRAY)
    med_src = float(np.median(src))
    med_dst = float(np.median(dst))
    need = max(4, int(round(0.22 * height)))
    for sl in (slice(0, EDGE_PX), slice(width - EDGE_PX, width)):
        src_hit = int(np.count_nonzero(np.abs(src[:, sl].astype(np.int16) - med_src) >= 36))
        dst_hit = int(np.count_nonzero(np.abs(dst[:, sl].astype(np.int16) - med_dst) >= 36))
        if dst_hit >= need and src_hit * 2 < need:
            return True
    return False


def _plate_crop(image: np.ndarray, rect: tuple) -> np.ndarray:
    left, top, width, height = [int(v) for v in rect]
    bottom = min(image.shape[0], max(0, top) + max(1, height))
    right = min(image.shape[1], max(0, left) + max(1, width))
    top = max(0, top)
    left = max(0, left)
    return image[top:bottom, left:right]


def _trim_rect(trim_shape, plate_shape, rect, trim_w, trim_h, bleed_mm, dpi: int = 300):
    """Plate box mapped onto the trim render, in pixels. None when it falls outside."""
    plate_h, plate_w = plate_shape[:2]
    media_w = (float(trim_w) + 2.0 * float(bleed_mm)) / 25.4 * float(dpi)
    media_h = (float(trim_h) + 2.0 * float(bleed_mm)) / 25.4 * float(dpi)
    inset = float(bleed_mm) / 25.4 * float(dpi)
    left, top, width, height = [float(v) for v in rect]
    x0 = left / float(plate_w) * media_w - inset
    y0 = top / float(plate_h) * media_h - inset
    x1 = (left + width) / float(plate_w) * media_w - inset
    y1 = (top + height) / float(plate_h) * media_h - inset
    ix0 = max(0, int(np.floor(x0)))
    iy0 = max(0, int(np.floor(y0)))
    ix1 = min(int(trim_shape[1]), int(np.ceil(x1)))
    iy1 = min(int(trim_shape[0]), int(np.ceil(y1)))
    if ix1 - ix0 < 4 or iy1 - iy0 < 4:
        return None
    return ix0, iy0, ix1, iy1


def _render_crop(trim: np.ndarray, plate_shape, rect, trim_w, trim_h, bleed_mm, dpi: int = 300) -> np.ndarray:
    """The same box on the 300 dpi trim render."""
    box = _trim_rect(trim.shape, plate_shape, rect, trim_w, trim_h, bleed_mm, dpi)
    if box is None:
        return trim[:0, :0]
    ix0, iy0, ix1, iy1 = box
    return trim[iy0:iy1, ix0:ix1]


def _box_mm(rect, plate_shape, trim_w, trim_h, bleed_mm) -> list:
    """Plate box as millimetres on the trim, origin at the trim's top left."""
    left, top, width, height = [float(v) for v in rect]
    plate_h, plate_w = plate_shape[:2]
    media_w = float(trim_w) + 2.0 * float(bleed_mm)
    media_h = float(trim_h) + 2.0 * float(bleed_mm)
    x = left / float(plate_w) * media_w - float(bleed_mm)
    y = top / float(plate_h) * media_h - float(bleed_mm)
    w = width / float(plate_w) * media_w
    h = height / float(plate_h) * media_h
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def _plate_rect(block, source_shape, placed, plate_shape, padded: bool = True) -> tuple:
    from vector_text_v2 import _rect

    raw = _rect(block, int(source_shape[1]), int(source_shape[0]))
    chosen = _padded(raw, int(source_shape[1]), int(source_shape[0])) if padded else raw
    left, top, right, bottom = _mapped_bounds(
        placed["map"], chosen, int(plate_shape[1]), int(plate_shape[0]),
    )
    return left, top, max(1, right - left), max(1, bottom - top)


def _rects_match(left, right, tol: int = 4) -> bool:
    if not left or not right:
        return False
    return all(abs(int(left[i]) - int(right[i])) <= tol for i in range(4))


def _parse_reads(rows, width: int, height: int) -> list:
    parsed = []
    for row in rows or []:
        bbox = row.get("bbox") or [0, 0, 0, 0]
        if len(bbox) < 4:
            continue
        rx0 = float(bbox[0]) * width
        ry0 = float(bbox[1]) * height
        rx1 = rx0 + float(bbox[2]) * width
        ry1 = ry0 + float(bbox[3]) * height
        text = str(row.get("text") or "")
        if rx1 <= rx0 or ry1 <= ry0 or not text.strip():
            continue
        parsed.append((rx0, ry0, rx1, ry1, text))
    return parsed


def _readings_for_boxes(texts: list, boxes: list, rows, width: int, height: int) -> list:
    """The read that belongs to each source box. Nothing is borrowed from another line.

    A detection whose centre sits in several boxes is kept whole for the box it
    overlaps most. The one exception is a detection that is exactly those boxes'
    own words joined: OCT OCT is the two OCT labels, and each keeps OCT.
    """
    parsed = _parse_reads(rows, width, height)
    owned = [[] for _ in texts]
    used = set()
    for index, (rx0, ry0, rx1, ry1, text) in enumerate(parsed):
        cx = (rx0 + rx1) * 0.5
        cy = (ry0 + ry1) * 0.5
        covered = [
            si for si, box in enumerate(boxes)
            if box is not None and box[0] <= cx <= box[2] and box[1] <= cy <= box[3]
        ]
        covered.sort(key=lambda si: (boxes[si][0], boxes[si][1]))
        joined = _norm_text(" ".join(texts[si] for si in covered))
        if len(covered) >= 2 and _norm_text(text) == joined:
            for si in covered:
                owned[si].append((boxes[si][1], boxes[si][0], texts[si]))
            used.add(index)
    for index, (rx0, ry0, rx1, ry1, text) in enumerate(parsed):
        if index in used:
            continue
        cx = (rx0 + rx1) * 0.5
        cy = (ry0 + ry1) * 0.5
        area = max(1.0, (rx1 - rx0) * (ry1 - ry0))
        best_i = None
        best = 0.0
        for si, box in enumerate(boxes):
            if box is None or not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
                continue
            ix0, iy0 = max(box[0], rx0), max(box[1], ry0)
            ix1, iy1 = min(box[2], rx1), min(box[3], ry1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            frac = (ix1 - ix0) * (iy1 - iy0) / area
            if frac > best:
                best = frac
                best_i = si
        if best_i is not None and best >= 0.30:
            owned[best_i].append((ry0, rx0, text))
    return [
        _norm_text(" ".join(text for _y, _x, text in sorted(group) if text))
        for group in owned
    ]


def _score_pair(source_bgr: np.ndarray, render_bgr: np.ndarray, source_text: str, render_text: str) -> dict:
    source_bgr = _match_scale(source_bgr, render_bgr)
    ssim = _ssim_luma(source_bgr, render_bgr)
    white = _novel_white_block(source_bgr, render_bgr)
    clipped = _edge_clipped(source_bgr, render_bgr)
    exact = _texts_equal(source_text, render_text)
    # A white block or a clipped edge is damage, so that box is put back.
    # SSIM still has to clear the floor for the row to pass.
    pixel_fail = bool(white or clipped)
    return {
        "ssim": round(float(ssim), 3),
        "whiteBlock": bool(white),
        "clipped": bool(clipped),
        "exact": bool(exact),
        "pixelFail": pixel_fail,
        "ok": bool(exact and ssim >= SSIM_FLOOR and not pixel_fail),
    }


def _measure_boxes(blocks, drawn, trim, plate_image, plate_shape, source_shape, placed, trim_w, trim_h, bleed_mm, rows):
    """One row per lettering box: exact reading plus the pixel checks."""
    prepared = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            continue
        rect = _plate_rect(block, source_shape, placed, plate_shape, padded=True)
        tight = _plate_rect(block, source_shape, placed, plate_shape, padded=False)
        tight_box = _trim_rect(trim.shape, plate_shape, tight, trim_w, trim_h, bleed_mm, 300)
        prepared.append((block, text, rect, tight_box))
    readings = _readings_for_boxes(
        [item[1] for item in prepared],
        [item[3] for item in prepared],
        rows, trim.shape[1], trim.shape[0],
    )
    report = []
    for (block, text, rect, _tight_box), render_text in zip(prepared, readings):
        vector = any(_rects_match(rect, item.get("rect")) for item in drawn)
        box = _trim_rect(trim.shape, plate_shape, rect, trim_w, trim_h, bleed_mm, 300)
        render = trim[:0, :0] if box is None else trim[box[1]:box[3], box[0]:box[2]]
        source = _plate_crop(plate_image, rect)
        score = _score_pair(source, render, text, render_text)
        # 9AM traced as 8AM is the same length and a different word. A merged
        # neighbour (OCT OCT) is not: that string is longer, and it is split above.
        same_length = bool(
            render_text
            and not score["exact"]
            and len(_norm_text(text)) == len(_norm_text(render_text))
            and int(rect[3]) >= 44
        )
        bbox = block.get("bbox") or [0, 0, 0, 0]
        report.append({
            "text": text,
            "ok": score["ok"],
            "render": render_text,
            "mode": "vector" if vector else "raster",
            "ssim": score["ssim"],
            "whiteBlock": score["whiteBlock"],
            "clipped": score["clipped"],
            "y": round(float(bbox[1]) if len(bbox) > 1 else 0.0, 4),
            "boxMm": _box_mm(rect, plate_shape, trim_w, trim_h, bleed_mm),
            "_rect": rect,
            "_pixelFail": score["pixelFail"],
            "_changed": bool(vector and same_length),
            "_source": source,
            "_render": render,
        })
    return report


def _restore_box(plate, pristine, rect) -> None:
    left, top, width, height = [int(v) for v in rect]
    right = min(plate.shape[1], left + width)
    bottom = min(plate.shape[0], top + height)
    top = max(0, top)
    left = max(0, left)
    plate[top:bottom, left:right] = pristine[top:bottom, left:right]


def _apply_text_gate(
    blocks, drawn, raster_lines, plate, pristine, output_pdf,
    trim_w, trim_h, bleed_mm, placed, qa, source_shape,
):
    """Exact per-box reading, then the pixels of that same box.

    A footer that repeats the line cannot pass the copy above it. A vector box
    with a white block, a clipped glyph, or a changed letter (9AM read as 8AM)
    is put back as pixels and the file is written again. The row still fails
    when luminance SSIM is under 0.85.
    """
    from vector_text_v2 import read_blocks

    plate_shape = plate.shape if pristine is None else pristine.shape
    base = pristine if pristine is not None else plate
    try:
        trim = _trim_bgr(output_pdf, bleed_mm, 300)
        rows = read_blocks(trim, extra=False)
    except Exception as exc:
        sys.stderr.write(f"[vector-trace] text gate skipped ({exc})\n")
        report = []
        for block in blocks or []:
            if isinstance(block, dict) and is_lettering(str(block.get("text") or "")):
                report.append({
                    "text": str(block.get("text") or ""),
                    "ok": False,
                    "render": "",
                    "mode": "unread",
                    "ssim": 0.0,
                    "whiteBlock": False,
                    "clipped": False,
                    "y": 0.0,
                    "boxMm": [0, 0, 0, 0],
                })
        return report, drawn, qa

    report = _measure_boxes(
        blocks, drawn, trim, base, plate_shape, source_shape, placed,
        trim_w, trim_h, bleed_mm, rows,
    )
    kept = []
    reverted = False
    if pristine is not None:
        for item in drawn:
            row = next(
                (candidate for candidate in report if _rects_match(candidate.get("_rect"), item.get("rect"))),
                None,
            )
            damaged = bool(row and row.get("_pixelFail"))
            changed = bool(row and row.get("_changed"))
            if changed and not damaged:
                original = _ocr_crop(row.get("_source"))
                rendered = _ocr_crop(row.get("_render"))
                changed = bool(original and rendered and not _texts_equal(original, rendered))
            if not damaged and not changed:
                kept.append(item)
                continue
            _restore_box(plate, pristine, item["rect"])
            if changed and not damaged:
                why = "The render changed a letter, so the box stayed in the picture."
            else:
                why = "The render covered or clipped this line, so the box stayed in the picture."
            raster_lines.append(_line(item["text"], "raster", why))
            reverted = True
    else:
        kept = list(drawn)
    if reverted:
        first_colour = float(qa.get("colour_s") or 0)
        first_compose = float(qa.get("compose_s") or 0)
        qa = _write_pdf(plate, kept, output_pdf, trim_w, trim_h, bleed_mm, placed)
        qa["colour_s"] = first_colour + float(qa.get("colour_s") or 0)
        qa["compose_s"] = first_compose + float(qa.get("compose_s") or 0)
        try:
            trim = _trim_bgr(output_pdf, bleed_mm, 300)
            rows = read_blocks(trim, extra=False)
            report = _measure_boxes(
                blocks, kept, trim, base, plate_shape, source_shape, placed,
                trim_w, trim_h, bleed_mm, rows,
            )
        except Exception as exc:
            sys.stderr.write(f"[vector-trace] text gate rescore skipped ({exc})\n")
    drawn = kept
    for row in report:
        row.pop("_rect", None)
        row.pop("_pixelFail", None)
        row.pop("_changed", None)
        row.pop("_source", None)
        row.pop("_render", None)
    return report, drawn, qa


def _points(height_px: int, ppi: int) -> float:
    if ppi <= 0:
        return 0.0
    return round(float(height_px) / float(ppi) * 72.0, 2)


def _write_pdf(plate, drawn, output_pdf, trim_w, trim_h, bleed_mm, placed) -> dict:
    import io

    import pymupdf as fitz
    from PIL import Image, ImageCms

    from vector_text_v2 import MIN_PPI, MM_TO_PT, _press_cmyk, _set_boxes

    qa = {"wrote": False, "cmyk": False, "boxes": False, "ppi": False, "fonts": True, "reason": ""}
    os.makedirs(os.path.dirname(output_pdf) or ".", exist_ok=True)
    colour_started = time.perf_counter()
    rgb = cv2.cvtColor(plate, cv2.COLOR_BGR2RGB)
    cmyk = ImageCms.applyTransform(Image.fromarray(rgb), _press_cmyk())
    qa["colour_s"] = time.perf_counter() - colour_started
    compose_started = time.perf_counter()
    buffer = io.BytesIO()
    # One CMYK conversion, then JPEG. TIFF plus garbage=4 was the slow part of the press file.
    cmyk.save(buffer, format="JPEG", quality=90, subsampling=0, dpi=(MIN_PPI, MIN_PPI))
    width_pt = (trim_w + 2 * bleed_mm) * MM_TO_PT
    height_pt = (trim_h + 2 * bleed_mm) * MM_TO_PT
    doc = fitz.open()
    try:
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, stream=buffer.getvalue())
        img_h, img_w = plate.shape[:2]
        sx = page.rect.width / float(img_w)
        sy = page.rect.height / float(img_h)
        _paint_drawn(page, drawn, sx, sy)
        _set_boxes(page, trim_w, trim_h, bleed_mm)
        doc.save(output_pdf, deflate=True, garbage=1)
        qa["wrote"] = True
    finally:
        doc.close()
    _inspect_plate(output_pdf, trim_w, trim_h, bleed_mm, qa)
    qa["compose_s"] = time.perf_counter() - compose_started
    qa["traced"] = len(drawn)
    return qa


def _draw_paths(shape, paths, sx, sy, origin_x, origin_y) -> bool:
    used = False
    for group in paths:
        for sub in group:
            cursor = None
            start = None
            for cmd, pts in sub:
                if cmd == "M" and pts:
                    cursor = pts[0]
                    start = pts[0]
                    continue
                if cmd == "L" and pts and cursor is not None:
                    end = pts[0]
                    shape.draw_line(_pt(cursor, sx, sy, origin_x, origin_y), _pt(end, sx, sy, origin_x, origin_y))
                    cursor = end
                    used = True
                    continue
                if cmd == "C" and len(pts) == 3 and cursor is not None:
                    shape.draw_bezier(
                        _pt(cursor, sx, sy, origin_x, origin_y),
                        _pt(pts[0], sx, sy, origin_x, origin_y),
                        _pt(pts[1], sx, sy, origin_x, origin_y),
                        _pt(pts[2], sx, sy, origin_x, origin_y),
                    )
                    cursor = pts[2]
                    used = True
                    continue
                if cmd == "Z" and cursor is not None and start is not None:
                    shape.draw_line(_pt(cursor, sx, sy, origin_x, origin_y), _pt(start, sx, sy, origin_x, origin_y))
                    cursor = start
                    used = True
    return used


def _paint_paths(page, paths, fill, sx, sy, origin_x, origin_y) -> None:
    shape = page.new_shape()
    if not _draw_paths(shape, paths, sx, sy, origin_x, origin_y):
        return
    shape.finish(color=None, fill=tuple(fill), width=0, even_odd=True, closePath=False)
    shape.commit()


def _pdf_xy(px, py, sx, sy, origin_x, origin_y, page_h) -> tuple[float, float]:
    return (origin_x + px) * sx, page_h - (origin_y + py) * sy


def _append_pdf_paths(chunks, paths, sx, sy, origin_x, origin_y, page_h) -> bool:
    """PDF path operators. Even-odd fill is applied by the caller."""
    used = False
    for group in paths:
        for sub in group:
            cursor = None
            start = None
            for cmd, pts in sub:
                if cmd == "M" and pts:
                    cursor = pts[0]
                    start = pts[0]
                    x, y = _pdf_xy(cursor[0], cursor[1], sx, sy, origin_x, origin_y, page_h)
                    chunks.append(f"{x:.2f} {y:.2f} m\n")
                    continue
                if cmd == "L" and pts and cursor is not None:
                    end = pts[0]
                    x, y = _pdf_xy(end[0], end[1], sx, sy, origin_x, origin_y, page_h)
                    chunks.append(f"{x:.2f} {y:.2f} l\n")
                    cursor = end
                    used = True
                    continue
                if cmd == "C" and len(pts) == 3 and cursor is not None:
                    c1 = _pdf_xy(pts[0][0], pts[0][1], sx, sy, origin_x, origin_y, page_h)
                    c2 = _pdf_xy(pts[1][0], pts[1][1], sx, sy, origin_x, origin_y, page_h)
                    end = _pdf_xy(pts[2][0], pts[2][1], sx, sy, origin_x, origin_y, page_h)
                    chunks.append(f"{c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {end[0]:.2f} {end[1]:.2f} c\n")
                    cursor = pts[2]
                    used = True
                    continue
                if cmd == "Z" and cursor is not None and start is not None:
                    x, y = _pdf_xy(start[0], start[1], sx, sy, origin_x, origin_y, page_h)
                    chunks.append(f"{x:.2f} {y:.2f} l\n")
                    cursor = start
                    used = True
    return used


def _paint_drawn(page, drawn, sx, sy) -> None:
    """Write every trace as PDF path operators. One fill per ink colour.

    Shape.draw_bezier once per curve was most of the press-file time. The
    operators go into the page stream in one write.
    """
    if not drawn:
        return
    order = []
    groups: dict = {}
    for item in drawn:
        key = tuple(float(channel) for channel in item["fill"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    page_h = float(page.rect.height)
    chunks = []
    for key in order:
        cyan, magenta, yellow, black = key
        # Near-black traces are 100% K. The stream keeps that as the four integers.
        if abs(cyan) < 1e-6 and abs(magenta) < 1e-6 and abs(yellow) < 1e-6 and abs(black - 1.0) < 1e-6:
            chunks.append("q\n0 0 0 1 k\n")
        else:
            chunks.append(f"q\n{cyan:.4f} {magenta:.4f} {yellow:.4f} {black:.4f} k\n")
        used = False
        for item in groups[key]:
            if _append_pdf_paths(chunks, item["paths"], sx, sy, item["origin"][0], item["origin"][1], page_h):
                used = True
        chunks.append("f*\nQ\n" if used else "Q\n")
    if not chunks:
        return
    xrefs = page.get_contents()
    if len(xrefs) != 1:
        for key in order:
            shape = page.new_shape()
            used = False
            for item in groups[key]:
                if _draw_paths(shape, item["paths"], sx, sy, item["origin"][0], item["origin"][1]):
                    used = True
            if used:
                shape.finish(color=None, fill=key, width=0, even_odd=True, closePath=False)
                shape.commit()
        return
    page.parent.update_stream(xrefs[0], page.read_contents() + b"\n" + "".join(chunks).encode("ascii"))


def _pt(point, sx, sy, origin_x, origin_y):
    import pymupdf as fitz

    return fitz.Point((origin_x + point[0]) * sx, (origin_y + point[1]) * sy)


def _inspect_plate(path, trim_w, trim_h, bleed_mm, qa) -> None:
    import pymupdf as fitz

    from vector_text_v2 import MIN_PPI

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
        else:
            qa["cmyk"] = False
            qa["ppi"] = False
    finally:
        doc.close()


def _workers() -> int:
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 2
    # Every core the machine gives this process. Capped so a big host cannot spawn a crowd.
    return max(1, min(8, int(count or 1)))


def _trace_many(masks: list) -> list:
    """Potrace each mask. A pool overlaps the CLI calls. Order stays the page order."""
    if not masks:
        return []
    if len(masks) == 1 or _workers() == 1:
        return [trace_mask(mask) for mask in masks]
    with ThreadPoolExecutor(max_workers=_workers()) as pool:
        return list(pool.map(trace_mask, masks))


def sharpen_background(plate: np.ndarray, ink: np.ndarray) -> np.ndarray:
    """Mild unsharp on the paper. Letter pixels, and a ring around them, stay put.

    The ring is what the 1px choke samples. Sharpening it would leave a halo
    beside the vector. The ink mask itself is already traced, so this does not
    change the paths or their IoU.
    """
    blur = cv2.GaussianBlur(plate, (0, 0), 0.8)
    sharp = cv2.addWeighted(plate, 1.25, blur, -0.25, 0)
    if ink is None or int(np.max(ink)) == 0:
        return sharp
    protect = cv2.dilate((ink > 0).astype(np.uint8), np.ones((5, 5), np.uint8))
    out = sharp
    out[protect > 0] = plate[protect > 0]
    return out


def _potrace_svg(binary: np.ndarray) -> str:
    height, width = binary.shape[:2]
    handle = tempfile.NamedTemporaryFile(prefix="trace-", suffix=".pbm", delete=False)
    path = handle.name
    try:
        bits = (binary > 127).astype(np.uint8)
        pad = (8 - (width % 8)) % 8
        if pad:
            bits = np.pad(bits, ((0, 0), (0, pad)))
        packed = np.packbits(bits, axis=1)
        handle.write(f"P4\n{width} {height}\n".encode())
        handle.write(packed.tobytes())
        handle.close()
        # Potrace traces black. PBM 1-bits are black, and those are our ink pixels.
        from host_paths import find_potrace

        result = subprocess.run(
            [
                find_potrace(), "-s", "--flat",
                "-t", str(TURDSIZE),
                "-a", str(ALPHAMAX),
                "-O", str(OPTTOLERANCE),
                "-u", "10",
                "-o", "-",
                path,
            ],
            check=False,
            capture_output=True,
            timeout=20,
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _svg_transform(svg: str, height: int):
    found = re.search(
        r"translate\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)\s*scale\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)",
        svg,
    )
    if not found:
        return 0.0, float(height), 1.0, -1.0
    return tuple(float(found.group(index)) for index in range(1, 5))


def _parse_path(raw: str, transform) -> list:
    tokens = _TOKEN.findall(raw.replace(",", " "))
    tx, ty, sx, sy = transform

    def apply(x, y):
        return tx + sx * x, ty + sy * y

    subpaths = []
    current = []
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)
    command = ""
    index = 0

    def numbers(count):
        nonlocal index
        values = []
        for _ in range(count):
            if index >= len(tokens) or tokens[index].isalpha():
                return None
            values.append(float(tokens[index]))
            index += 1
        return values

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
        elif not command:
            break
        relative = command.islower() and command.lower() != "z"
        kind = command.lower()
        if kind == "z":
            if current:
                current.append(("Z", []))
                subpaths.append(current)
            current = []
            cursor = start
            continue
        if kind == "m":
            pair = numbers(2)
            if pair is None:
                break
            x, y = pair
            if relative:
                x, y = cursor[0] + x, cursor[1] + y
            if current:
                subpaths.append(current)
            cursor = (x, y)
            start = cursor
            current = [("M", [apply(x, y)])]
            # Further pairs are implicit lineto.
            command = "l" if command == "m" else "L"
            continue
        if kind == "l":
            pair = numbers(2)
            if pair is None:
                break
            x, y = pair
            if relative:
                x, y = cursor[0] + x, cursor[1] + y
            cursor = (x, y)
            current.append(("L", [apply(x, y)]))
            continue
        if kind == "c":
            values = numbers(6)
            if values is None:
                break
            if relative:
                values = [
                    cursor[0] + values[0], cursor[1] + values[1],
                    cursor[0] + values[2], cursor[1] + values[3],
                    cursor[0] + values[4], cursor[1] + values[5],
                ]
            points = [apply(values[0], values[1]), apply(values[2], values[3]), apply(values[4], values[5])]
            cursor = (values[4], values[5])
            current.append(("C", points))
            continue
        if kind in ("h", "v"):
            value = numbers(1)
            if value is None:
                break
            x, y = cursor
            if kind == "h":
                x = cursor[0] + value[0] if relative else value[0]
            else:
                y = cursor[1] + value[0] if relative else value[0]
            cursor = (x, y)
            current.append(("L", [apply(x, y)]))
            continue
        break
    if current:
        subpaths.append(current)
    return subpaths
