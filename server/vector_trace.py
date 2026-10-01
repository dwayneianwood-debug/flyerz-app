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
# A word box stops at the x-height. Descenders hang below it and ascenders
# above it, so the trace crop has to be taller than the box OCR returned.
ABOVE_FRAC = 0.25
BELOW_FRAC = 0.35

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
    otsu, binary = cv2.threshold(sample, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary > 0
    # Otsu can pick the paper when the letters fill the crop. The ring is paper.
    if float(ink[ring].mean()) > 0.5:
        ink = ~ink
    ink = _grow_thin_strokes(sample, ink, float(otsu))
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


def _grow_thin_strokes(sample: np.ndarray, ink: np.ndarray, otsu: float) -> np.ndarray:
    """Pull a light crossbar or a pale tail back onto the stroke it touches.

    Otsu keeps the stem and drops the thinner, lighter stroke, so an e is
    traced as a c. Only pixels next to the stem, and still well clear of the
    paper, are added. A counter sits on the paper and stays empty.
    """
    if ink is None or not bool(ink.any()):
        return ink
    if float(np.median(sample[ink])) < max(8.0, otsu * 0.75):
        return ink
    cut = max(6.0, otsu * 0.40)
    weak = sample >= cut
    count, labels = cv2.connectedComponents((weak | ink).astype(np.uint8), 8)
    grown = ink.copy()
    for label in np.unique(labels[ink]):
        if int(label) == 0:
            continue
        comp = labels == label
        core = int(np.count_nonzero(comp & ink))
        extra = int(np.count_nonzero(comp & ~ink))
        # A crossbar is a fraction of the letter. A fringe that doubles it is not.
        if extra > max(8, int(round(0.45 * core))):
            continue
        grown[comp] = True
    if float(grown.mean()) > 0.62:
        return ink
    return grown


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


def drop_solid_blobs(mask: np.ndarray, gray: np.ndarray | None = None) -> np.ndarray:
    """Keep a near-solid blob that towers over the letters out of the trace.

    A letter stuck to that blob, such as the H against the flame, stays in
    the mask when it sits in the letter band and is about one letter wide.
    The rest of the blob stays in the picture. A line of similar letters is
    left unchanged.
    """
    cleaned, _halo = split_solid_blobs(mask, gray)
    return cleaned


def split_solid_blobs(mask: np.ndarray, gray: np.ndarray | None = None):
    """Drop a tall solid blob. Return the letter mask and a halo to inpaint.

    The halo is only the letter that was cut off the blob, plus a few pixels,
    so the flame itself stays in the picture.
    """
    empty = np.zeros((1, 1), np.uint8) if mask is None else np.zeros(mask.shape[:2], np.uint8)
    if mask is None or int(mask.max()) == 0:
        return mask, empty
    if gray is not None and gray.shape[:2] != mask.shape[:2]:
        gray = None
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    parts = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 20:
            continue
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        top = int(stats[index, cv2.CC_STAT_TOP])
        solidity = area / float(max(1, width * height))
        parts.append((index, height, width, top, solidity))
    if len(parts) < 4:
        return mask, empty
    median = float(np.median([height for _index, height, _width, _top, _solidity in parts]))
    median_w = float(np.median([width for _index, _height, width, _top, _solidity in parts]))
    if median < 8.0 or median_w < 4.0:
        return mask, empty
    drop = [
        index for index, height, _width, _top, solidity in parts
        if height >= SOLID_HEIGHT_RATIO * median and solidity >= SOLID_MIN
    ]
    if not drop:
        return mask, empty
    band_parts = [item for item in parts if item[0] not in drop and item[1] <= 1.35 * median]
    if band_parts:
        y0 = int(np.percentile([item[3] for item in band_parts], 25)) - 1
        y1 = int(np.percentile([item[3] + item[1] for item in band_parts], 75)) + 1
    else:
        y0, y1 = 0, mask.shape[0]
    light = True
    if gray is not None and band_parts:
        kept = np.zeros(mask.shape[:2], np.bool_)
        for index, _height, _width, _top, _solidity in band_parts:
            kept |= labels == index
        if int(kept.sum()) > 20:
            light = float(np.median(gray[kept])) >= float(np.median(gray))
    cleaned = mask.copy()
    salvaged = np.zeros(mask.shape[:2], np.uint8)
    for index in drop:
        component = labels == index
        cleaned[component] = 0
        band = np.zeros(mask.shape[:2], np.uint8)
        y_lo = max(0, y0)
        y_hi = min(mask.shape[0], y1)
        if y_hi - y_lo < 4:
            continue
        band[y_lo:y_hi][component[y_lo:y_hi]] = 255
        sub_count, sub_labels, sub_stats, _sub = cv2.connectedComponentsWithStats(band, 8)
        kept_slice = False
        for sub in range(1, sub_count):
            area = int(sub_stats[sub, cv2.CC_STAT_AREA])
            width = int(sub_stats[sub, cv2.CC_STAT_WIDTH])
            height = int(sub_stats[sub, cv2.CC_STAT_HEIGHT])
            if not _letter_sized(area, width, height, median, median_w):
                continue
            cleaned[sub_labels == sub] = 255
            salvaged[sub_labels == sub] = 255
            kept_slice = True
        if kept_slice or gray is None:
            continue
        core = _bright_core(gray, component, y_lo, y_hi, median, median_w, light)
        # Only a core that matches another letter. A bright piece of the logo does not.
        if core is None or not _matches_letter(core, cleaned, median, median_w):
            continue
        cleaned[core > 0] = 255
        salvaged[core > 0] = 255
    if int(cleaned.max()) == 0:
        return mask, empty
    halo = _halo_of(salvaged, cleaned)
    return cleaned, halo


def _letter_sized(area: int, width: int, height: int, median_h: float, median_w: float) -> bool:
    if area < 20:
        return False
    if height < 0.65 * median_h or height > 1.35 * median_h:
        return False
    if width < 0.22 * median_w or width > 1.7 * median_w:
        return False
    return True


def _bright_core(gray, blob, y0, y1, median_h, median_w, light: bool):
    """The letter welded to a logo is the bright (or dark) core of that blob.

    The join is dimmer than the stroke, so a local threshold splits them.
    The core whose area is closest to a normal letter is the one kept.
    """
    band = np.zeros(blob.shape, np.bool_)
    band[y0:y1] = blob[y0:y1]
    if int(band.sum()) < 30:
        return None
    tones = gray[band]
    lo = float(np.percentile(tones, 15))
    hi = float(np.percentile(tones, 90))
    if hi - lo < 28.0:
        return None
    target = 0.62 * median_h * median_w
    best_score = -1e9
    best = None
    if light:
        levels = range(int(min(248, round(hi))), int(lo + 0.40 * (hi - lo)), -4)
        def selected(level):
            return gray >= level
    else:
        levels = range(int(max(8, round(lo))), int(hi - 0.40 * (hi - lo)), 4)
        def selected(level):
            return gray <= level
    for level in levels:
        binary = (selected(level) & band).astype(np.uint8) * 255
        sub_count, sub_labels, sub_stats, _sub = cv2.connectedComponentsWithStats(binary, 8)
        for sub in range(1, sub_count):
            area = int(sub_stats[sub, cv2.CC_STAT_AREA])
            width = int(sub_stats[sub, cv2.CC_STAT_WIDTH])
            height = int(sub_stats[sub, cv2.CC_STAT_HEIGHT])
            if not _letter_sized(area, width, height, median_h, median_w):
                continue
            solidity = area / float(max(1, width * height))
            if solidity < 0.35:
                continue
            score = -abs(area - target) / max(1.0, target) + 0.2 * solidity - 0.6 * _sparse_rows(sub_labels == sub)
            if score > best_score:
                best_score = score
                best = sub_labels == sub
    if best is None:
        return None
    out = np.zeros(gray.shape[:2], np.uint8)
    out[best] = 255
    return out


def _matches_letter(core: np.ndarray, cleaned: np.ndarray, median_h: float, median_w: float) -> bool:
    """True when the recovered core is the same shape as a letter already on the line."""
    pieces = _components(core, 20)
    others = [
        part for part in _components(cleaned, 20)
        if _letter_sized(part["area"], part["w"], part["h"], median_h, median_w)
    ]
    if not pieces or not others:
        return False
    for piece in pieces:
        for other in others:
            if _glyph_iou(piece["pixels"], other["pixels"]) >= 0.70:
                return True
    return False


def _sparse_rows(pixels) -> float:
    ys, xs = np.where(pixels)
    if ys.size < 8:
        return 1.0
    crop = pixels[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
    widths = crop.sum(axis=1).astype(np.float32)
    wide = float(widths.max())
    if wide < 1.0:
        return 1.0
    return float((widths < 0.25 * wide).mean())


def _halo_of(salvaged: np.ndarray, cleaned: np.ndarray) -> np.ndarray:
    """A few pixels around a recovered letter, without swallowing its neighbours."""
    if salvaged is None or int(salvaged.max()) == 0:
        return np.zeros(cleaned.shape[:2], np.uint8)
    halo = cv2.dilate(salvaged, np.ones((7, 7), np.uint8))
    other = (cleaned > 0) & (salvaged == 0)
    if int(other.sum()) > 0:
        near = cv2.dilate(other.astype(np.uint8), np.ones((3, 3), np.uint8))
        halo[near > 0] = 0
        halo[salvaged > 0] = 255
    return halo


# A repeated letter that does not match its sibling, and is taller or shorter
# than the line, is rebuilt from that sibling. Below this, body copy is left
# alone: its glyphs are too small for a shape compare to be meaningful.
GLYPH_MIN_H = 16.0
GLYPH_IOU_MIN = 0.72
# An ascender is about 1.6× the x-height. A logo welded into the line is about
# twice as tall, or nearly three letters wide. Only that second kind is severe.
SEVERE_HEIGHT_RATIO = 1.85
SEVERE_WIDTH_RATIO = 2.8


def harmonise_pending(pending: list) -> None:
    """Make every glyph in a display line match the other copies of that letter.

    A spur on one T is replaced with the clean T from the same line, or from
    another line of the same artwork. A word that still contains a broken
    glyph and has no sibling is left entirely as pixels.
    """
    prepared = []
    donors = {}
    for item in pending:
        rec = _line_glyphs(item)
        prepared.append(rec)
        if rec is None:
            continue
        halo = item.get("clear")
        for index, part in enumerate(rec["parts"]):
            # A letter cut off a logo is not a donor. Its counter can still hold the logo.
            if _halo_hit(part, halo):
                continue
            if not _glyph_clean(part, rec["median_h"], rec["median_top"]):
                continue
            donors.setdefault(rec["letters"][index], []).append((rec, part))
    for item, rec in zip(pending, prepared):
        if rec is None:
            continue
        halo = item.get("clear")
        replacements = {}
        for index, part in enumerate(rec["parts"]):
            welded = _halo_hit(part, halo)
            # A spurred letter is only a little taller than the line. A component
            # twice as tall is a different object and must not be rebuilt.
            # Only a copy that is taller or shorter than the other copies of
            # that same letter. A normal ascender is not rebuilt.
            siblings = [
                other for other_index, other in enumerate(rec["parts"])
                if other_index != index and rec["letters"][other_index] == rec["letters"][index]
            ]
            modest = False
            if siblings and 0.70 * rec["median_h"] <= part["h"] <= 1.35 * rec["median_h"]:
                sibling_h = float(np.median([other["h"] for other in siblings]))
                # A 2px body-copy wobble is not a different letter. The spurred T is.
                modest = abs(part["h"] - sibling_h) > max(4.0, 0.18 * sibling_h)
            if not welded and not modest:
                continue
            donor = _sibling_donor(donors.get(rec["letters"][index], []), rec, part)
            if donor is None:
                continue
            limit = 0.94 if welded else GLYPH_IOU_MIN
            if _glyph_iou(part["pixels"], donor["pixels"]) >= limit:
                continue
            replacements[index] = donor
        severe = []
        for index, part in enumerate(rec["parts"]):
            if index in replacements:
                continue
            tall = part["h"] > SEVERE_HEIGHT_RATIO * rec["median_h"]
            wide = part["w"] > SEVERE_WIDTH_RATIO * rec["median_w"]
            if tall or wide:
                severe.append(index)
        cleared = set()
        if severe:
            # One broken piece takes the whole line. A line is not half vector.
            cleared.update(range(len(rec["parts"])))
        if not replacements and not cleared:
            continue
        updated = np.zeros(item["mask"].shape[:2], np.uint8)
        clear = item.get("clear")
        if clear is None or clear.shape[:2] != item["mask"].shape[:2]:
            clear = np.zeros(item["mask"].shape[:2], np.uint8)
        else:
            clear = clear.copy()
        for index, part in enumerate(rec["parts"]):
            if index in cleared:
                continue
            if index in replacements:
                stamped = _place_glyph(
                    replacements[index]["pixels"], part, rec["median_h"], rec["median_top"], updated.shape,
                )
                updated[stamped] = 255
                clear[cv2.dilate(part["pixels"].astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = 255
                continue
            updated[part["pixels"]] = 255
        item["changed"] = True
        if int(updated.max()) == 0 or cleared:
            # The whole line was unusable. Leave the picture untouched.
            item["mask"] = np.zeros(item["mask"].shape[:2], np.uint8)
            item["clear"] = np.zeros(item["mask"].shape[:2], np.uint8)
            continue
        item["mask"] = updated
        item["clear"] = clear


def _line_glyphs(item: dict):
    parts = _components(item.get("mask"), 20)
    if len(parts) < 4:
        return None
    median_h = float(np.median([part["h"] for part in parts]))
    median_w = float(np.median([part["w"] for part in parts]))
    if median_h < GLYPH_MIN_H or median_w < 4.0:
        return None
    letters = [ch for ch in str(item.get("text") or "").upper() if ch.isalnum()]
    if len(letters) != len(parts):
        return None
    return {
        "parts": parts,
        "letters": letters,
        "median_h": median_h,
        "median_w": median_w,
        "median_top": float(np.median([part["y"] for part in parts])),
    }


def _glyph_clean(part: dict, median_h: float, median_top: float) -> bool:
    return (
        abs(part["h"] - median_h) <= max(2.0, 0.08 * median_h)
        and abs(part["y"] - median_top) <= 2.0
    )


def _halo_hit(part: dict, halo) -> bool:
    """True when this glyph is one that was cut out of a tall logo."""
    if halo is None or getattr(halo, "shape", None) is None:
        return False
    if halo.shape[:2] != part["pixels"].shape[:2] or int(halo.max()) == 0:
        return False
    overlap = int(np.count_nonzero(part["pixels"] & (halo > 0)))
    return overlap >= max(8, int(0.50 * part["area"]))


def _sibling_donor(candidates, rec, part):
    """The cleanest copy of this letter. The same line wins a tie, then the footer."""
    pool = []
    for owner, donor in candidates:
        if donor["pixels"] is part["pixels"]:
            continue
        pool.append((owner is rec, donor))
    if not pool:
        return None
    return min(
        pool,
        key=lambda item: (_sparse_rows(item[1]["pixels"]), 0 if item[0] else 1, abs(item[1]["h"] - rec["median_h"])),
    )[1]


def _word_groups(parts: list, median_w: float) -> list:
    groups = [[0]]
    for index in range(1, len(parts)):
        gap = parts[index]["x"] - (parts[index - 1]["x"] + parts[index - 1]["w"])
        if gap > 0.90 * median_w:
            groups.append([index])
        else:
            groups[-1].append(index)
    return groups


def _glyph_iou(left, right) -> float:
    """IoU after the two glyphs are scaled to the same box."""
    def tight(pixels):
        ys, xs = np.where(pixels)
        if ys.size == 0:
            return None
        return pixels[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]

    a = tight(left)
    b = tight(right)
    if a is None or b is None:
        return 0.0
    b = cv2.resize(b.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST)
    inter = np.count_nonzero(a & (b > 0))
    union = np.count_nonzero(a | (b > 0))
    if union == 0:
        return 1.0
    return float(inter) / float(union)


def _place_glyph(donor_pixels, target: dict, median_h: float, median_top: float, shape) -> np.ndarray:
    """Stamp a clean sibling on the line's cap-height, centred on the bad glyph."""
    ys, xs = np.where(donor_pixels)
    canvas = np.zeros(shape[:2], np.bool_)
    if ys.size == 0:
        return canvas
    tight = donor_pixels[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1].astype(np.uint8)
    new_h = max(1, int(round(median_h)))
    new_w = max(1, int(round(tight.shape[1] * (new_h / float(max(1, tight.shape[0]))))))
    scaled = cv2.resize(tight, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    left = int(round(target["x"] + target["w"] * 0.5 - new_w / 2.0))
    top = int(round(median_top))
    y0, x0 = max(0, top), max(0, left)
    y1, x1 = min(shape[0], top + new_h), min(shape[1], left + new_w)
    if y1 <= y0 or x1 <= x0:
        return canvas
    canvas[y0:y1, x0:x1] = scaled[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)] > 0
    return canvas


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
        # A descender is mostly below the word box. Keeping it only when 40% of
        # the stroke sits in that box drops the tail and leaves a floating speck.
        if inside >= max(4, int(round(0.08 * area))):
            keep[component] = 255
    # A tail that the threshold split off sits under a kept letter and outside
    # the word box. It is not the next line: it is short, and it shares the
    # letter's columns.
    kept_ids = [index for index in range(1, count) if int(keep[labels == index].max()) > 0]
    for index in range(1, count):
        if int(keep[labels == index].max()) > 0:
            continue
        component = labels == index
        ys, xs = np.where(component)
        if ys.size < 4:
            continue
        top, bottom = int(ys.min()), int(ys.max())
        left, right = int(xs.min()), int(xs.max())
        if top < y1:
            continue
        if bottom - top > max(8, int(round(0.55 * (y1 - y0)))):
            continue
        for kept in kept_ids:
            ky, kx = np.where(labels == kept)
            if ky.size == 0:
                continue
            k_bottom = int(ky.max())
            gap = top - k_bottom - 1
            if gap < 0 or gap > max(6, int(round(0.35 * (y1 - y0)))):
                continue
            k_left, k_right = int(kx.min()), int(kx.max())
            overlap = min(right, k_right) - max(left, k_left)
            if overlap >= max(2, int(round(0.35 * (right - left + 1)))):
                keep[component] = 255
                break
    if int(keep.max()) == 0:
        return None
    return _join_descenders(keep, (x0, y0, x1, y1))


def _join_descenders(mask: np.ndarray, inner: tuple) -> np.ndarray:
    """Bridge a thin gap between a letter and the tail under it.

    Upscale and the threshold open a one-pixel join, so the tail is traced as
    a floating speck. The bridge is two pixels wide, only where the tail
    already sits under that letter.
    """
    height, width = mask.shape[:2]
    _x0, _y0, _x1, y1 = [int(v) for v in inner]
    count, labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    if count < 3:
        return mask
    out = mask.copy()
    ids = [index for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= 8]
    for lower in ids:
        ly = int(stats[lower, cv2.CC_STAT_TOP])
        lh = int(stats[lower, cv2.CC_STAT_HEIGHT])
        lx = int(stats[lower, cv2.CC_STAT_LEFT])
        lw = int(stats[lower, cv2.CC_STAT_WIDTH])
        la = int(stats[lower, cv2.CC_STAT_AREA])
        if ly + lh < y1:
            continue
        for upper in ids:
            if upper == lower:
                continue
            uy = int(stats[upper, cv2.CC_STAT_TOP])
            uh = int(stats[upper, cv2.CC_STAT_HEIGHT])
            ux = int(stats[upper, cv2.CC_STAT_LEFT])
            uw = int(stats[upper, cv2.CC_STAT_WIDTH])
            if int(stats[upper, cv2.CC_STAT_AREA]) < la:
                continue
            gap = ly - (uy + uh)
            if gap < 1 or gap > max(4, int(round(0.30 * max(uh, 1)))):
                continue
            overlap = min(lx + lw, ux + uw) - max(lx, ux)
            if overlap < max(2, int(round(0.35 * lw))):
                continue
            mid = (max(lx, ux) + min(lx + lw, ux + uw)) // 2
            xa = max(0, mid - 1)
            xb = min(width, mid + 2)
            ya = max(0, uy + uh - 1)
            yb = min(height, ly + 1)
            out[ya:yb, xa:xb] = 255
    return out


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


def _letter_erase_mask(mask: np.ndarray) -> np.ndarray:
    """The stroke plus a 2px fringe outside it. A counter stays, so the hole is the paper."""
    ink = mask > 0
    if not bool(ink.any()):
        return np.zeros(mask.shape[:2], np.uint8)
    background = (~ink).astype(np.uint8)
    count, labels = cv2.connectedComponents(background, 4)
    border = np.zeros(count, np.bool_)
    border[labels[0, :]] = True
    border[labels[-1, :]] = True
    border[labels[:, 0]] = True
    border[labels[:, -1]] = True
    border[0] = False
    exterior = border[labels]
    fringe = cv2.dilate(ink.astype(np.uint8) * 255, np.ones((5, 5), np.uint8)) > 0
    erase = ink | (fringe & exterior)
    return erase.astype(np.uint8) * 255


def _strip_flourish(mask: np.ndarray) -> np.ndarray:
    """Leave a word-wide swash in the picture.

    The letter body is traced. A flourish wider than the letters, hanging below
    that body, is not erased and not traced: a vector of it turns into bars,
    and those bars sit inside the source-ink fringe so the page guard misses
    them. A normal descender is about one letter wide and stays in the mask.
    """
    if mask is None or int(mask.max()) == 0:
        return mask
    parts = _components(mask, 20)
    letterlike = [part for part in parts if part["h"] >= 8 and part["w"] <= 2.5 * max(1, part["h"])]
    baseline = None
    median_w = 0.0
    if len(letterlike) >= 2:
        median_w = float(np.median([part["w"] for part in letterlike]))
        baseline = int(np.median([part["y"] + part["h"] for part in letterlike])) + 2
    else:
        rows = (mask > 0).sum(axis=1)
        peak = int(rows.max()) if rows.size else 0
        if peak < 8:
            return mask
        heavy = rows >= 0.45 * peak
        y = int(np.argmax(heavy))
        while y + 1 < heavy.size and heavy[y + 1]:
            y += 1
        baseline = min(mask.shape[0], y + 3)
        upper = np.zeros(mask.shape, np.uint8)
        upper[:baseline] = mask[:baseline]
        upper_parts = _components(upper, 20)
        upper_letters = [part for part in upper_parts if part["w"] <= 2.5 * max(1, part["h"])]
        if len(upper_letters) < 2:
            return mask
        median_w = float(np.median([part["w"] for part in upper_letters]))
    if baseline is None or median_w <= 0 or baseline >= mask.shape[0] - 2:
        return mask
    if int((mask[baseline:] > 0).sum()) < 40:
        return mask
    tail = np.zeros(mask.shape, np.uint8)
    tail[baseline:] = mask[baseline:]
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats((tail > 0).astype(np.uint8), 8)
    wide = False
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 80:
            continue
        if int(stats[index, cv2.CC_STAT_WIDTH]) > 1.6 * median_w:
            wide = True
            break
    if not wide:
        return mask
    out = mask.copy()
    out[baseline:] = 0
    if int(out.max()) == 0 or int((out > 0).sum()) < 0.45 * int((mask > 0).sum()):
        return mask
    return out


def _fill_from_paper(plate: np.ndarray, erase: np.ndarray) -> np.ndarray:
    """Replace the raster letter with the paper around it. The vector is drawn on top."""
    if erase is None or int(erase.max()) == 0:
        return plate
    keep = (erase == 0).astype(np.float32)
    colour = plate.astype(np.float32) * keep[..., None]
    num = cv2.GaussianBlur(colour, (0, 0), 14.0)
    den = cv2.GaussianBlur(keep, (0, 0), 14.0)
    filled = num / np.maximum(den[..., None], 1e-3)
    weak = (erase > 0) & (den < 0.08)
    if bool(weak.any()):
        num_wide = cv2.GaussianBlur(colour, (0, 0), 36.0)
        den_wide = cv2.GaussianBlur(keep, (0, 0), 36.0)
        wide = num_wide / np.maximum(den_wide[..., None], 1e-3)
        filled[weak] = wide[weak]
    out = plate.copy()
    out[erase > 0] = np.clip(np.rint(filled[erase > 0]), 0, 255).astype(np.uint8)
    return out


def _glyph_structure_fails(source_mask: np.ndarray, painted: np.ndarray) -> bool:
    """True when the trace merged two letters, split one, or changed a letter's shape.

    A count that differs by one is an i-dot. A gap that is not in the source
    ('inflam mation') and a blob that covers two letters ('oı') are not.
    """
    if source_mask is None or painted is None:
        return False
    if source_mask.shape[:2] != painted.shape[:2]:
        return True

    def significant(mask: np.ndarray) -> list:
        parts = _components(mask, 8)
        if len(parts) < 2:
            return parts
        median = float(np.median([part["area"] for part in parts]))
        floor = max(12, int(round(0.12 * median)))
        return [part for part in parts if part["area"] >= floor]

    source = significant(source_mask)
    drawn = significant(painted)
    if not source or not drawn:
        return bool(source) or bool(drawn)
    # A thin stroke breaks into a few extra pieces. A whole extra word does not.
    count_limit = max(4, int(round(0.40 * max(len(source), len(drawn)))))
    if abs(len(source) - len(drawn)) > count_limit:
        if os.environ.get("GLYPH_DEBUG"):
            sys.stderr.write(f"[glyph] count {len(source)} vs {len(drawn)}\n")
        return True
    for part in drawn:
        covered = []
        for glyph in source:
            shared = int(np.count_nonzero(part["pixels"] & glyph["pixels"]))
            if shared >= 0.45 * glyph["area"] and shared >= 0.15 * part["area"]:
                covered.append(glyph)
        if len(covered) < 2:
            continue
        covered.sort(key=lambda glyph: glyph["x"])
        for left, right in zip(covered, covered[1:]):
            if right["x"] - (left["x"] + left["w"]) >= 2:
                if os.environ.get("GLYPH_DEBUG"):
                    sys.stderr.write(f"[glyph] merge gap {right['x'] - (left['x'] + left['w'])}\n")
                return True
    for glyph in source:
        hits = []
        for part in drawn:
            shared = int(np.count_nonzero(part["pixels"] & glyph["pixels"]))
            if shared >= 0.25 * part["area"] and shared >= 0.20 * glyph["area"]:
                hits.append((part, shared))
        if len(hits) < 2:
            continue
        hits.sort(key=lambda item: item[0]["x"])
        for (left, _left_shared), (right, _right_shared) in zip(hits, hits[1:]):
            gap = right["x"] - (left["x"] + left["w"])
            if gap >= 2:
                if os.environ.get("GLYPH_DEBUG"):
                    sys.stderr.write(f"[glyph] split gap {gap} h {glyph['h']}\n")
                return True
    if len(source) == len(drawn):
        ordered_source = sorted(source, key=lambda part: part["x"])
        ordered_drawn = sorted(drawn, key=lambda part: part["x"])
        median_w = float(np.median([part["w"] for part in ordered_source]))
        for index, (left, right) in enumerate(zip(ordered_source, ordered_source[1:])):
            source_gap = right["x"] - (left["x"] + left["w"])
            drawn_left = ordered_drawn[index]
            drawn_right = ordered_drawn[index + 1]
            drawn_gap = drawn_right["x"] - (drawn_left["x"] + drawn_left["w"])
            if drawn_gap > source_gap + max(6.0, 0.85 * median_w) and drawn_gap > max(4.0, 2.0 * max(source_gap, 1)):
                if os.environ.get("GLYPH_DEBUG"):
                    sys.stderr.write(f"[glyph] gap {source_gap:.1f} -> {drawn_gap:.1f}\n")
                return True
        for glyph in ordered_source:
            best = 0.0
            for part in ordered_drawn:
                shared = int(np.count_nonzero(glyph["pixels"] & part["pixels"]))
                if shared <= 0:
                    continue
                x0 = max(0, min(glyph["x"], part["x"]))
                y0 = max(0, min(glyph["y"], part["y"]))
                x1 = min(source_mask.shape[1], max(glyph["x"] + glyph["w"], part["x"] + part["w"]))
                y1 = min(source_mask.shape[0], max(glyph["y"] + glyph["h"], part["y"] + part["h"]))
                union = int(np.count_nonzero(glyph["pixels"][y0:y1, x0:x1] | part["pixels"][y0:y1, x0:x1]))
                if union:
                    best = max(best, shared / float(union))
            if best < 0.25:
                if os.environ.get("GLYPH_DEBUG"):
                    sys.stderr.write(f"[glyph] shape {best:.2f}\n")
                return True
    if _fork_closed(source, painted):
        return True
    return False


def _fork_closed(source_glyphs: list, painted: np.ndarray) -> bool:
    """A Y whose arms were joined into one blob no longer matches the source.

    The source glyph meets in one stem. An H or an M still has two feet, so a
    bridge there is left to the other shape checks.
    """
    for glyph in source_glyphs:
        if glyph["h"] < 8 or glyph["w"] < 5:
            continue
        y1 = glyph["y"] + max(3, int(round(glyph["h"] * 0.45)))
        src_top = glyph["pixels"][glyph["y"]:y1, glyph["x"]:glyph["x"] + glyph["w"]]
        if int(src_top.sum()) < 8 or _separated_runs(src_top) < 2:
            continue
        foot = max(3, int(round(glyph["h"] * 0.30)))
        src_foot = glyph["pixels"][glyph["y"] + glyph["h"] - foot:glyph["y"] + glyph["h"], glyph["x"]:glyph["x"] + glyph["w"]]
        if _separated_runs(src_foot) != 1:
            continue
        paint_top = painted[glyph["y"]:y1, glyph["x"]:glyph["x"] + glyph["w"]]
        if _separated_runs(paint_top) < 2:
            if os.environ.get("GLYPH_DEBUG"):
                sys.stderr.write(f"[glyph] fork closed x={glyph['x']} h={glyph['h']}\n")
            return True
    return False


def _separated_runs(binary: np.ndarray) -> int:
    """Ink pieces with a real gap between them. A one-pixel bridge does not split."""
    if binary is None or binary.size == 0 or int(np.count_nonzero(binary)) < 4:
        return 0
    mask = (binary > 0).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    return sum(1 for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= 4)


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


def _hole_labels(component: np.ndarray):
    """Binary hole mask and per-hole stats, in the component's own crop."""
    ys, xs = np.where(component)
    if ys.size < 12:
        return None
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = np.zeros((y1 - y0, x1 - x0), np.uint8)
    crop[component[y0:y1, x0:x1]] = 255
    padded = cv2.copyMakeBorder(crop, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    inv = cv2.bitwise_not(padded)
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(inv, flood_mask, (0, 0), 128)
    holes = (inv == 255).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(holes, 8)
    return y0, x0, count, labels, stats


def _hole_areas(component: np.ndarray, min_hole: int = 8) -> list:
    """Areas of enclosed holes. Specks smaller than ``min_hole`` are not counters."""
    packed = _hole_labels(component)
    if packed is None:
        return []
    _y0, _x0, count, _labels, stats = packed
    found = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area >= min_hole:
            found.append(area)
    return found


def _paper_hole_areas(component, gray, dark: bool, paper_tone: float, span: float, min_hole: int) -> list:
    """Holes whose interior is paper, not the gray fringe between strokes.

    A light threshold closes a script gap and invents a counter. A real counter
    (the bowl of an e or an a) is the paper colour.
    """
    packed = _hole_labels(component)
    if packed is None or span < 16.0:
        return []
    y0, x0, count, labels, stats = packed
    # Keep a counter that is clearly nearer the paper than the ink.
    limit = paper_tone - 0.28 * span if dark else paper_tone + 0.28 * span
    found = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < min_hole:
            continue
        hole = labels == index
        # labels include the 1px border, so shift back onto the gray crop.
        sub = hole[1:-1, 1:-1]
        if sub.shape[0] < 1 or sub.shape[1] < 1:
            continue
        abs_hole = np.zeros(gray.shape[:2], np.bool_)
        y1 = min(gray.shape[0], y0 + sub.shape[0])
        x1 = min(gray.shape[1], x0 + sub.shape[1])
        abs_hole[y0:y1, x0:x1] = sub[: y1 - y0, : x1 - x0]
        vals = gray[abs_hole]
        if vals.size == 0:
            continue
        med = float(np.median(vals))
        if dark and med < limit:
            continue
        if not dark and med > limit:
            continue
        found.append(area)
    return found


def _hole_count(component: np.ndarray) -> int:
    """Enclosed holes. 9 has one, 8 has two, S has none, B has two. Specks do not count."""
    ys, xs = np.where(component)
    if ys.size < 12:
        return 0
    min_hole = max(8, int(round(float(ys.size) * 0.02)))
    return len(_hole_areas(component, min_hole))


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
    traced_parts = _components(traced, 20)
    if traced_parts:
        median_area = float(np.median([part["area"] for part in traced_parts]))
    else:
        median_area = 80.0
    # A pad speck is not a wrong letter. A piece as big as a letter is.
    significant = max(36, int(round(0.12 * median_area)))
    for part in traced_parts:
        if part["area"] < significant:
            continue
        pixels = part["pixels"]
        area = part["area"]
        inside = int(np.count_nonzero(pixels & (zone > 0)))
        overlap = int(np.count_nonzero(pixels & source_bin))
        if inside < int(0.50 * area) and overlap < int(0.35 * area):
            return "The trace added a fragment outside the letters, so this box stayed in the picture."
        if overlap < int(0.18 * area):
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
            union = part["area"] + best["area"] - best_overlap
            # A 1px paint closes a hairline counter and still covers the stroke.
            # A 9 filled into an 8 does not: the hole is a real share of the glyph.
            if union > 0 and best_overlap / float(union) < 0.36:
                return "The trace closed or opened a counter, so this box stayed in the picture."
    return ""


def _hide_foreign_ink(crop: np.ndarray, owned: np.ndarray | None) -> np.ndarray:
    """Paper over another line's strokes. This line's counters stay as they are."""
    if crop is None or owned is None or crop.ndim != 3 or owned.shape[:2] != crop.shape[:2]:
        return crop
    ink = owned > 0
    if int(ink.sum()) < 8:
        return crop
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    paper_px = gray[~ink]
    if paper_px.size < 8:
        return crop
    ink_tone = float(np.median(gray[ink]))
    paper_tone = float(np.median(paper_px))
    span = abs(ink_tone - paper_tone)
    if span < 16.0:
        return crop
    if ink_tone < paper_tone:
        foreign_ink = gray <= paper_tone - 0.55 * span
    else:
        foreign_ink = gray >= paper_tone + 0.55 * span
    foreign_ink &= ~ink
    count, labels, stats, _cents = cv2.connectedComponentsWithStats(foreign_ink.astype(np.uint8), 8)
    drop = np.zeros(gray.shape, np.bool_)
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 8:
            continue
        drop[labels == index] = True
    if not bool(drop.any()):
        return crop
    out = crop.copy()
    tone = int(round(paper_tone))
    out[drop] = tone
    return out


def _topology_fails(crop: np.ndarray, painted: np.ndarray, ppi: float = 400.0) -> bool:
    """True when a traced glyph lost its tail or its counter.

    OCR reads a clipped g as the right word, so the text compare does not see
    it. The glyph's bottom has to match the source ink within 1px at 300 dpi,
    and the enclosed holes have to match the source glyph. One miss is enough.
    """
    if crop is None or painted is None or getattr(painted, "size", 0) == 0:
        return False
    if crop.ndim != 3 or crop.shape[0] < 6 or crop.shape[1] < 6:
        return False
    if int(np.max(painted)) == 0:
        return False
    if painted.shape[:2] != crop.shape[:2]:
        return True
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    paint = painted > 0
    ink_px = gray[paint]
    if ink_px.size < 8:
        return False
    ink_tone = float(np.median(ink_px))
    near = cv2.dilate(paint.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    paper_px = gray[near & ~paint]
    if paper_px.size < 8:
        return False
    paper_tone = float(np.median(paper_px))
    span = abs(ink_tone - paper_tone)
    if span < 16.0:
        return False
    dark = ink_tone < paper_tone
    # The counter check uses a lighter cut so a thin crossbar still closes the hole.
    # The tail check uses the solid stroke, so a pale fringe is not a longer letter.
    hole_cut = paper_tone - 0.42 * span if dark else paper_tone + 0.42 * span
    extent_cut = paper_tone - 0.62 * span if dark else paper_tone + 0.62 * span
    source_holes_ink = gray <= hole_cut if dark else gray >= hole_cut
    source_extent = gray <= extent_cut if dark else gray >= extent_cut
    tolerance = max(1, int(round(float(ppi) / 300.0)))
    # A sibling stroke covers the source. A one-pixel halo around it is fringe.
    cover = cv2.dilate(paint.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    # The join of a tail is a couple of pixels. A mark further away is the next line.
    gap_limit = max(4, int(round(2.0 * float(ppi) / 300.0)))
    parts = _components((paint.astype(np.uint8) * 255), 20)
    if len(parts) < 1:
        return False
    median_area = float(np.median([part["area"] for part in parts]))
    significant = max(36, int(round(0.12 * median_area)))
    height, width = gray.shape[:2]
    for part in parts:
        if part["area"] < significant:
            continue
        x0 = max(0, part["x"] - 1)
        x1 = min(width, part["x"] + part["w"] + 1)
        y0 = max(0, part["y"] - 1)
        y1 = min(height, part["y"] + part["h"] + 1)
        window = np.zeros((height, width), np.uint8)
        window[y0:y1, x0:x1] = source_holes_ink[y0:y1, x0:x1].astype(np.uint8)
        count, labels = cv2.connectedComponents(window, 8)
        body = np.zeros((height, width), np.bool_)
        for index in range(1, count):
            comp = labels == index
            if np.any(comp & part["pixels"]):
                body[comp] = True
        source_areas = _paper_hole_areas(body, gray, dark, paper_tone, span, 4)
        paint_areas = _hole_areas(part["pixels"], 4)
        # Substantial counters have to match. An e that lost its bar has none,
        # and a g that lost its loop has one instead of two. Specks do not count.
        substantial = max(12, int(round(0.08 * part["area"])))
        src_holes = sum(1 for area in source_areas if area >= substantial)
        paint_holes = sum(1 for area in paint_areas if area >= max(4, substantial // 3))
        if src_holes != paint_holes and src_holes > 0 and paint_holes < src_holes:
            eroded = cv2.erode(part["pixels"].astype(np.uint8), np.ones((3, 3), np.uint8))
            opened = _hole_areas(eroded > 0, 4) if int(eroded.max()) else []
            opened_n = sum(1 for area in opened if area >= max(4, substantial // 3))
            if opened_n < src_holes:
                if os.environ.get("TOPO_DEBUG"):
                    sys.stderr.write(
                        f"[topo] holes src={source_areas} paint={paint_areas} "
                        f"x={part['x']} w={part['w']} h={part['h']}\n"
                    )
                return True
        paint_bottom = part["y"] + part["h"] - 1
        y_end = min(height, paint_bottom + 1 + max(gap_limit + 2, int(round(0.50 * part["h"]))))
        x0 = max(0, part["x"])
        x1 = min(width, part["x"] + part["w"])
        if y_end <= paint_bottom + 1 or x1 <= x0:
            continue
        # Only ink the paint missed. A tail drawn as its own stroke is covered.
        gap = 0
        run_pixels = 0
        run_bottom = paint_bottom
        started = False
        for y in range(paint_bottom + 1, y_end):
            uncovered = source_extent[y, x0:x1] & ~cover[y, x0:x1]
            row_span = int(np.count_nonzero(uncovered))
            # The next line fills the column. A descender does not.
            if row_span > max(4, int(round(0.85 * max(1, part["w"])))):
                break
            if row_span <= 1:
                gap += 1
                if gap > gap_limit:
                    break
                continue
            gap = 0
            started = True
            run_pixels += row_span
            run_bottom = y
        # A one-pixel fringe is not a tail. A clipped g or y is.
        if started and run_bottom - paint_bottom > tolerance and run_pixels >= 8:
            if os.environ.get("TOPO_DEBUG"):
                sys.stderr.write(
                    f"[topo] tail delta={run_bottom - paint_bottom} tol={tolerance} "
                    f"px={run_pixels} x={part['x']} h={part['h']}\n"
                )
            return True
    return False


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


def _raster_box(text: str, left: int, top: int, right: int, bottom: int, core=None, scope: str = "paragraph") -> dict:
    """A box that stayed in the picture.

    Any fallback pulls the other boxes on its own line. A paragraph goes
    fully raster only when more than one of its lines falls back.
    """
    rect = (int(left), int(top), int(right - left), int(bottom - top))
    return {
        "text": text,
        "rect": rect,
        "core": core or rect,
        "anchor": scope,
    }


def _horizontal_gap(left, right) -> float:
    lx, _ly, lw, _lh = left
    rx, _ry, rw, _rh = right
    if lx + lw < rx:
        return float(rx - (lx + lw))
    if rx + rw < lx:
        return float(lx - (rx + rw))
    return 0.0


def _vertical_overlap(left, right) -> float:
    _lx, ly, _lw, lh = left
    _rx, ry, _rw, rh = right
    top = max(ly, ry)
    bottom = min(ly + lh, ry + rh)
    return float(bottom - top)


def _same_line(left, right) -> bool:
    overlap = _vertical_overlap(left, right)
    if overlap < 0.55 * min(left[3], right[3]):
        return False
    # The gap is judged against the shorter line. A tall word must not swallow
    # the column sitting beside it. Two short words with a bullet between them
    # are still one line.
    shorter = min(left[3], right[3])
    limit = 1.4 * shorter
    if max(left[3], right[3]) <= 28:
        limit = 3.2 * shorter
    return _horizontal_gap(left, right) < limit


def _same_paragraph(above, below) -> bool:
    """Two stacked lines in one column, spaced within 1.6 times the line height."""
    if max(above[3], below[3]) > 2.0 * max(1.0, min(above[3], below[3])):
        return False
    overlap = min(above[0] + above[2], below[0] + below[2]) - max(above[0], below[0])
    if overlap < 0.45 * min(above[2], below[2]):
        return False
    spacing = below[1] - above[1]
    if spacing < -0.35 * min(above[3], below[3]):
        return False
    return spacing <= 1.6 * max(above[3], below[3])


def _paragraph_roots(line_rects: dict) -> dict:
    """Lines in one column, spaced within 1.6×, share a paragraph id."""
    ids = list(line_rects)
    parent = {line_id: line_id for line_id in ids}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    ordered = sorted(ids, key=lambda line_id: (line_rects[line_id][1], line_rects[line_id][0]))
    for pos, line_id in enumerate(ordered):
        rect = line_rects[line_id]
        limit = rect[1] + max(rect[3], 1.0) * 6.0
        for other in ordered[pos + 1:]:
            below = line_rects[other]
            if below[1] > limit:
                break
            if _same_paragraph(rect, below):
                parent[find(other)] = find(line_id)
    return {line_id: find(line_id) for line_id in ids}


def _glyph_owner(cx: float, cy: float, cores: list) -> int | None:
    """The line this ink component belongs to.

    A centroid inside a line's own box belongs to that line. A centroid in the
    gap belongs to the nearer box edge: a descender stays with the line above,
    and an ascender stays with the line below. The other line never receives it.
    """
    containing = []
    for index, core in enumerate(cores):
        x, y, w, h = core
        if w <= 0 or h <= 0:
            continue
        if x <= cx <= x + w and y <= cy <= y + h:
            containing.append(index)
    if len(containing) == 1:
        return containing[0]
    if len(containing) > 1:
        def center_dist(index: int) -> float:
            x, y, w, h = cores[index]
            return abs(cy - (y + 0.5 * h))

        return min(containing, key=center_dist)
    best = None
    best_dist = 1e9
    for index, core in enumerate(cores):
        x, y, w, h = core
        if w <= 0 or h <= 0:
            continue
        if cx < x - 0.25 * h or cx > x + w + 0.25 * h:
            continue
        if cy < y:
            dist = y - cy
            limit = 0.40 * h
        elif cy > y + h:
            dist = cy - (y + h)
            limit = 0.50 * h
        else:
            if cx < x:
                dist = x - cx
            else:
                dist = cx - (x + w)
            limit = 0.25 * h
        if dist > limit or dist >= best_dist:
            continue
        best_dist = dist
        best = index
    return best


def _keep_line_glyphs(mask: np.ndarray, left: int, top: int, owner: int, cores: list) -> np.ndarray | None:
    """Ink components whose centroid sits in this line's baseline band."""
    if mask is None or int(np.max(mask)) == 0 or owner < 0:
        return None
    binary = (mask > 0).astype(np.uint8)
    count, labels, _stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    owned = np.zeros(mask.shape[:2], np.uint8)
    for index in range(1, count):
        if int(_stats[index, cv2.CC_STAT_AREA]) < 4:
            continue
        cx = float(left) + float(cents[index][0])
        cy = float(top) + float(cents[index][1])
        if _glyph_owner(cx, cy, cores) == owner:
            owned[labels == index] = 255
    if int(owned.max()) == 0:
        return None
    return owned


def _restore_glyphs(plate: np.ndarray, pristine: np.ndarray, item: dict) -> None:
    """Put this line's ink, and its 2px fringe, back from the clean upscale."""
    ink = item.get("_ink")
    origin = item.get("origin")
    if ink is None or origin is None or pristine is None:
        rect = item.get("rect")
        if rect is not None and pristine is not None:
            _restore_box(plate, pristine, rect)
        return
    fringe = _letter_erase_mask(ink)
    left, top = int(origin[0]), int(origin[1])
    height, width = fringe.shape[:2]
    y0, x0 = max(0, top), max(0, left)
    y1 = min(plate.shape[0], top + height)
    x1 = min(plate.shape[1], left + width)
    if y1 <= y0 or x1 <= x0:
        return
    piece = fringe[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)] > 0
    plate[y0:y1, x0:x1][piece] = pristine[y0:y1, x0:x1][piece]


def _stamp_fringe(canvas: np.ndarray, item: dict) -> None:
    ink = item.get("_ink")
    origin = item.get("origin")
    if ink is None or origin is None:
        return
    fringe = _letter_erase_mask(ink)
    left, top = int(origin[0]), int(origin[1])
    height, width = fringe.shape[:2]
    y0, x0 = max(0, top), max(0, left)
    y1 = min(canvas.shape[0], top + height)
    x1 = min(canvas.shape[1], left + width)
    if y1 <= y0 or x1 <= x0:
        return
    piece = fringe[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)]
    patch = canvas[y0:y1, x0:x1]
    np.maximum(patch, piece, out=patch)


def _source_leaks(plate: np.ndarray, pristine: np.ndarray, items: list, art_box: tuple) -> np.ndarray:
    """Pixels of the original art that paint-out changed outside the glyph fringe.

    The fringe is only the traced stroke plus 2px. It is the same rule on every
    side. Ink that was not traced, including a swash left in the picture, is
    not exempt: a change there is a leak.
    """
    allowed = np.zeros(plate.shape[:2], np.uint8)
    for item in items:
        _stamp_fringe(allowed, item)
    diff = np.max(np.abs(plate.astype(np.int16) - pristine.astype(np.int16)), axis=2)
    ax, ay, aw, ah = [int(v) for v in art_box]
    y0, x0 = max(0, ay), max(0, ax)
    y1 = min(plate.shape[0], ay + ah)
    x1 = min(plate.shape[1], ax + aw)
    bad = np.zeros(plate.shape[:2], np.bool_)
    if y1 > y0 and x1 > x0:
        bad[y0:y1, x0:x1] = (diff[y0:y1, x0:x1] > 8) & (allowed[y0:y1, x0:x1] == 0)
    return bad


def _repair_source(plate, pristine, drawn, raster_lines, raster_boxes, art_box) -> tuple:
    """A leak outside the glyph fringe is put back, and the line that caused it goes raster."""
    bad = _source_leaks(plate, pristine, drawn, art_box)
    if not bool(bad.any()):
        return drawn, True
    plate[bad] = pristine[bad]
    drop = []
    for item in drawn:
        ink = item.get("_ink")
        origin = item.get("origin")
        if ink is None or origin is None:
            continue
        fringe = _letter_erase_mask(ink)
        near = cv2.dilate((fringe > 0).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        left, top = int(origin[0]), int(origin[1])
        height, width = near.shape[:2]
        y0, x0 = max(0, top), max(0, left)
        y1 = min(bad.shape[0], top + height)
        x1 = min(bad.shape[1], left + width)
        if y1 <= y0 or x1 <= x0:
            continue
        window = bad[y0:y1, x0:x1]
        piece = near[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)]
        if int(np.count_nonzero(window & piece)) > 8:
            drop.append(item)
    if not drop:
        return drawn, not bool(_source_leaks(plate, pristine, drawn, art_box).any())
    kept = []
    for item in drawn:
        if item not in drop:
            kept.append(item)
            continue
        _restore_glyphs(plate, pristine, item)
        raster_lines.append(_line(
            item.get("text") or "",
            "raster",
            "The paint reached past this line, so the line stayed in the picture.",
        ))
        core = item.get("core") or item.get("rect")
        if core is not None:
            raster_boxes.append({
                "text": item.get("text") or "",
                "rect": core,
                "core": core,
                "anchor": "paragraph",
            })
    kept = _keep_uniform(kept, raster_lines, raster_boxes)
    for item in drawn:
        if item not in kept and item not in drop:
            _restore_glyphs(plate, pristine, item)
    still = _source_leaks(plate, pristine, kept, art_box)
    if bool(still.any()):
        plate[still] = pristine[still]
    return kept, not bool(_source_leaks(plate, pristine, kept, art_box).any())


def _keep_uniform(drawn: list, raster_lines: list, raster_boxes: list) -> list:
    """A line is all vector or all raster.

    One fallback in a paragraph leaves the other lines vector. Two or more
    fallbacks put every line of that paragraph back in the picture, so a
    column does not mix a bold vector line with a thin raster line. Icons
    do not count, because they are not in `raster_boxes`.
    """
    if not drawn or not raster_boxes:
        return drawn
    records = []
    for index, item in enumerate(drawn):
        records.append({
            "index": index,
            "mode": "vector",
            "rect": item.get("core") or item["rect"],
            "scope": "",
        })
    for box in raster_boxes:
        scope = box.get("anchor")
        if scope is True:
            scope = "paragraph"
        if not scope:
            continue
        records.append({
            "index": None,
            "mode": "raster",
            "rect": box.get("core") or box["rect"],
            "scope": scope,
        })
    parent = list(range(len(records)))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left, right):
        parent[find(left)] = find(right)

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if _same_line(records[i]["rect"], records[j]["rect"]):
                union(i, j)
    lines = {}
    for index, record in enumerate(records):
        lines.setdefault(find(index), []).append(index)
    line_ids = list(lines)
    line_rect = {}
    for line_id, members in lines.items():
        rects = [records[member]["rect"] for member in members]
        x0 = min(rect[0] for rect in rects)
        y0 = min(rect[1] for rect in rects)
        x1 = max(rect[0] + rect[2] for rect in rects)
        y1 = max(rect[1] + rect[3] for rect in rects)
        line_rect[line_id] = (x0, y0, x1 - x0, y1 - y0)
    roots = _paragraph_roots(line_rect)
    groups = {}
    for line_id in line_ids:
        groups.setdefault(roots[line_id], []).append(line_id)
    drop = set()
    for line_id, members in lines.items():
        if not any(records[member]["mode"] == "raster" for member in members):
            continue
        for member in members:
            if records[member]["mode"] == "vector" and records[member]["index"] is not None:
                drop.add(records[member]["index"])
    for grouped in groups.values():
        # A line whose ink was never separated only blanks its own line.
        # Two lines whose traces were rejected blank the paragraph.
        fallback_lines = 0
        for line_id in grouped:
            members = lines[line_id]
            if any(
                records[member]["mode"] == "raster" and records[member].get("scope") == "paragraph"
                for member in members
            ):
                fallback_lines += 1
        if fallback_lines <= 1:
            continue
        for line_id in grouped:
            for member in lines[line_id]:
                if records[member]["mode"] == "vector" and records[member]["index"] is not None:
                    drop.add(records[member]["index"])
    if not drop:
        return drawn
    kept = []
    for index, item in enumerate(drawn):
        if index not in drop:
            kept.append(item)
            continue
        raster_lines.append(_line(
            item["text"],
            "raster",
            "This line stayed in the picture so it would not be half traced.",
        ))
    return kept


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
    raster_boxes = []
    height, width = plate.shape[:2]
    pending = []
    letter_rects = _letter_rects(blocks, bgr.shape[1], bgr.shape[0])
    plate_ppi = float(placed.get("ppi") or MIN_PPI)
    plate_cores = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        if not is_lettering(str(block.get("text") or "")):
            continue
        raw = _rect(block, bgr.shape[1], bgr.shape[0])
        inner_left, inner_top, inner_right, inner_bottom = _mapped_bounds(placed["map"], raw, width, height)
        plate_cores.append((
            inner_left,
            inner_top,
            max(1, inner_right - inner_left),
            max(1, inner_bottom - inner_top),
        ))
    owner_cursor = 0
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            raster_lines.append(_line(text, "raster", "An icon was left in the picture."))
            continue
        owner = owner_cursor
        owner_cursor += 1
        raw = _rect(block, bgr.shape[1], bgr.shape[0])
        # A one-glyph speck under about 4 mm is a logo edge the reader called a letter.
        # Tracing it punches the picture and the trim does not read it back.
        glyph = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", text)
        if len(glyph) == 1 and glyph.islower() and int(raw[3]) < 48:
            raster_lines.append(_line(text, "raster", "A single small glyph stayed in the picture."))
            continue
        rect = _expand_rect(raw, bgr.shape[1], bgr.shape[0], letter_rects)
        left, top, right, bottom = _mapped_bounds(placed["map"], rect, width, height)
        inner_left, inner_top, inner_right, inner_bottom = _mapped_bounds(placed["map"], raw, width, height)
        core = (
            inner_left,
            inner_top,
            max(1, inner_right - inner_left),
            max(1, inner_bottom - inner_top),
        )
        if right - left < 4 or bottom - top < 4:
            raster_lines.append(_line(text, "raster", "The box was too small to trace."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        crop = plate[top:bottom, left:right]
        mask, colour = segment_ink(crop)
        if mask is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        mask = ink_touching(mask, (
            inner_left - left, inner_top - top, inner_right - left, inner_bottom - top,
        ))
        if mask is None:
            raster_lines.append(_line(text, "raster", "The ink sat outside this box, so it stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        mask, colour = refine_ink(crop, mask)
        if mask is None or colour is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        # A logo in the same box is not a letter. A letter touching it stays.
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mask, halo = split_solid_blobs(mask, gray)
        mask, colour = refine_ink(crop, mask)
        if mask is None or colour is None:
            raster_lines.append(_line(text, "raster", "The ink could not be separated from the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        # Only this line's ink. A neighbour's ascender in the expanded crop stays put.
        owned = _keep_line_glyphs(mask, left, top, owner, plate_cores)
        if owned is None:
            raster_lines.append(_line(text, "raster", "The ink sat outside this box, so it stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "line"))
            continue
        refined_mask, refined_colour = refine_ink(crop, owned)
        if refined_mask is not None and refined_colour is not None:
            mask, colour = refined_mask, refined_colour
        else:
            mask = owned
        # A word-wide swash stays in the picture. Tracing it, or painting it
        # out, leaves bars, and the source-ink fringe would hide those bars
        # from the page guard.
        mask = _strip_flourish(mask)
        pending.append({
            "text": text,
            "mask": mask,
            "colour": colour,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "core": core,
            "crop": crop,
            "inner": (inner_left - left, inner_top - top, inner_right - left, inner_bottom - top),
            "clear": halo,
        })
    harmonise_pending(pending)
    traced = _trace_many([item["mask"] for item in pending])
    for item, paths in zip(pending, traced):
        text = item["text"]
        mask = item["mask"]
        left, top, right, bottom = item["left"], item["top"], item["right"], item["bottom"]
        core = item.get("core")
        if not paths:
            raster_lines.append(_line(text, "raster", "The trace was empty, so this box stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph"))
            continue
        score, accepted, painted = _accept_trace(mask, paths)
        if not accepted:
            raster_lines.append(_line(text, "raster", f"The trace did not match the ink ({score:.2f}), so this box stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph"))
            continue
        check_crop = _hide_foreign_ink(item["crop"], mask)
        reason = shape_gate(check_crop, mask, painted, item["inner"])
        if reason:
            raster_lines.append(_line(text, "raster", reason))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph"))
            continue
        if _topology_fails(check_crop, painted, plate_ppi):
            if os.environ.get("TOPO_DEBUG"):
                sys.stderr.write(f"[topo] line {text!r}\n")
            raster_lines.append(_line(
                text, "raster",
                "A letter lost its tail or its counter, so this line stayed in the picture.",
            ))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph"))
            continue
        fill = _trace_fill(item["colour"])
        drawn.append({
            "text": text,
            "paths": paths,
            "fill": fill,
            "origin": (left, top),
            "iou": round(score, 3),
            "rect": (left, top, right - left, bottom - top),
            "core": core,
            "painted": painted,
            "colour": item["colour"],
            "changed": bool(item.get("changed")),
            "_mask": mask,
            "_clear": item.get("clear"),
        })
    drawn = _keep_uniform(drawn, raster_lines, raster_boxes)
    pristine = plate.copy()
    erase = np.zeros(plate.shape[:2], np.uint8)
    for item in drawn:
        mask = item.pop("_mask", None)
        item.pop("_clear", None)
        if mask is not None:
            item["_ink"] = mask
        # The source ink of this line, dilated 2px. Never the crop rectangle.
        _stamp_fringe(erase, item)
    if int(erase.max()) > 0:
        plate[:] = _fill_from_paper(plate, erase)
    art_box = placed.get("art_box") or (0, 0, plate.shape[1], plate.shape[0])
    drawn, source_guard = _repair_source(plate, pristine, drawn, raster_lines, raster_boxes, art_box)
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
    text_gate, drawn, qa, source_guard = _apply_text_gate(
        blocks, drawn, raster_lines, raster_boxes, plate, pristine, output_pdf,
        trim_w, trim_h, bleed_mm, placed, qa, bgr.shape, source_guard,
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
        "The original raster letter under each trace was painted out, so the vector edge is what prints.",
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
            f"glyph={bool(row.get('glyphFail'))} mismatch={bool(row.get('mismatch'))} "
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
        "source_guard": bool(source_guard),
    }


def _mapped_bounds(mapper, rect, width, height):
    x0, y0 = mapper(rect[0], rect[1])
    x1, y1 = mapper(rect[0] + rect[2], rect[1] + rect[3])
    left, top = int(np.floor(min(x0, x1))), int(np.floor(min(y0, y1)))
    right, bottom = int(np.ceil(max(x0, x1))), int(np.ceil(max(y0, y1)))
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    return left, top, right, bottom


def _expand_rect(rect, width, height, neighbours=()):
    """Grow a word box by 25% above and 35% below, without entering the next line.

    Horizontal padding stays the usual fraction of the line height. A neighbour
    on the same line does not clamp this one. A line above or below splits the
    gap, so a descender is traced and a neighbour's body is not.
    """
    x, y, bw, bh = [int(round(float(v))) for v in rect]
    bh = max(2, bh)
    bw = max(2, bw)
    pad_x = max(2, int(round(bh * PAD_FRAC)))
    # Body-copy boxes stop at the x-height, so they need the descender pad.
    # A tall heading already contains the tail; the same fraction pulls in the logo.
    if bh <= 40:
        up = max(pad_x, int(round(bh * ABOVE_FRAC)))
        down = max(pad_x, int(round(bh * BELOW_FRAC)))
    else:
        up = pad_x
        down = pad_x
    for other in neighbours or ():
        nx, ny, nw, nh = [int(round(float(v))) for v in other]
        if nw < 2 or nh < 2:
            continue
        if nx == x and ny == y and nw == bw and nh == bh:
            continue
        overlap = min(x + bw, nx + nw) - max(x, nx)
        if overlap <= 0 or overlap < 0.35 * min(bw, nw):
            continue
        v_overlap = min(y + bh, ny + nh) - max(y, ny)
        if v_overlap >= 0.55 * min(bh, nh):
            continue
        this_cy = y + bh * 0.5
        other_cy = ny + nh * 0.5
        if other_cy < this_cy:
            # Stop at their body. Overlapping word boxes still leave room for an ascender.
            up = min(up, max(0, y - int(round(other_cy))))
        elif ny >= y + bh:
            gap = ny - (y + bh)
            their_asc = int(round(nh * ABOVE_FRAC))
            usable = gap - their_asc
            if usable < int(round(bh * BELOW_FRAC)):
                usable = gap // 2
            down = min(down, max(0, usable))
        else:
            # The next word box starts inside this one. Its center is the body,
            # so the band above that center is this line's descender.
            down = min(down, max(0, int(round(other_cy)) - (y + bh)))
    x0 = max(0, x - pad_x)
    y0 = max(0, y - up)
    x1 = min(int(width), x + bw + pad_x)
    y1 = min(int(height), y + bh + down)
    return x0, y0, max(2, x1 - x0), max(2, y1 - y0)


def _padded(rect, width, height):
    return _expand_rect(rect, width, height, ())


def _letter_rects(blocks, width, height) -> list:
    from vector_text_v2 import _rect

    found = []
    for block in blocks or []:
        if not isinstance(block, dict) or not block.get("bbox"):
            continue
        if not is_lettering(block.get("text") or ""):
            continue
        found.append(_rect(block, int(width), int(height)))
    return found


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


def _reads_match(left: str, right: str) -> bool:
    """Case-sensitive letters and spaces. Punctuation is not a different word.

    'A small drop' is not 'a small drop'. 'inflam mation' is not
    'inflammation'. A comma read as a full stop is still the same line.
    """
    def key(text: str) -> str:
        cleaned = _norm_text(text)
        cleaned = re.sub(r"[^0-9A-Za-zÀ-ÿıİ ]+", "", cleaned)
        return re.sub(r" +", " ", cleaned).strip()

    a = key(left)
    b = key(right)
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
# Pixel checks run on this longest side. A full-page 300 dpi render is not required.
MODEST_EDGE = 96


def _numpy_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian. The gate's pixel checks stay on numpy."""
    sigma = float(sigma)
    values = np.asarray(image, np.float64)
    if sigma <= 0.05 or values.size == 0:
        return values
    radius = max(1, int(round(sigma * 3.0)))
    axis = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (axis / sigma) ** 2)
    kernel /= kernel.sum()

    def blur_last(plane):
        pad = radius
        padded = np.pad(plane, ((0, 0), (pad, pad)), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, kernel.size, axis=1)
        return np.tensordot(windows, kernel, axes=(2, 0))

    flat = values if values.ndim == 2 else values.reshape(-1, values.shape[-2], values.shape[-1])
    if values.ndim == 2:
        blurred = blur_last(values)
        blurred = blur_last(blurred.T).T
        return blurred
    # Colour is not used. Callers pass a grey plane.
    return values


def _box_mean(gray: np.ndarray, radius: int) -> np.ndarray:
    radius = int(radius)
    if radius < 1:
        return gray.astype(np.float64)
    padded = np.pad(gray.astype(np.float64), radius, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = integral.cumsum(0).cumsum(1)
    span = radius * 2 + 1
    total = (
        integral[span:, span:]
        - integral[:-span, span:]
        - integral[span:, :-span]
        + integral[:-span, :-span]
    )
    return total / float(span * span)


def _erode_square(binary: np.ndarray, radius: int) -> np.ndarray:
    radius = int(radius)
    if radius < 1:
        return binary
    span = radius * 2 + 1
    padded = np.pad(binary.astype(bool), radius, mode="constant", constant_values=False)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (span, span))
    return windows.all(axis=(-1, -2))


def _modest(image: np.ndarray, limit: int = MODEST_EDGE) -> np.ndarray:
    """Shrink a crop so the pixel checks stay cheap. Area-average, in numpy."""
    if image is None or image.size == 0:
        return image
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if long_edge <= limit or long_edge < 2:
        return image
    step = int(np.ceil(long_edge / float(limit)))
    height -= height % step
    width -= width % step
    if height < step or width < step:
        return image
    cropped = image[:height, :width]
    if cropped.ndim == 2:
        blocks = cropped.reshape(height // step, step, width // step, step)
        return blocks.mean(axis=(1, 3))
    blocks = cropped.reshape(height // step, step, width // step, step, cropped.shape[2])
    return blocks.mean(axis=(1, 3))


def _match_scale(source: np.ndarray, render: np.ndarray) -> np.ndarray:
    """Source crop at the render crop's pixel size."""
    if source is None or render is None or source.size == 0 or render.size == 0:
        return source
    if source.shape[:2] != render.shape[:2]:
        return cv2.resize(source, (render.shape[1], render.shape[0]), interpolation=cv2.INTER_CUBIC)
    return source


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.float64)
    plane = image.astype(np.float64)
    return plane[..., 0] * 0.114 + plane[..., 1] * 0.587 + plane[..., 2] * 0.299


def _ssim_luma(source_bgr: np.ndarray, render_bgr: np.ndarray) -> float:
    """Mean SSIM of the luminance. The two crops are already the same size."""
    if source_bgr is None or render_bgr is None or source_bgr.size == 0 or render_bgr.size == 0:
        return 0.0
    height, width = render_bgr.shape[:2]
    left = _gray(source_bgr)
    right = _gray(render_bgr)
    # The plate and the traced fill are the same picture through two samplers.
    # A one-pixel blur on both lets SSIM see the letters rather than that resample.
    left = _numpy_blur(left, 1.0)
    right = _numpy_blur(right, 1.0)
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
    mu1 = _numpy_blur(left, sigma)
    mu2 = _numpy_blur(right, sigma)
    sigma1 = _numpy_blur(left * left, sigma) - mu1 * mu1
    sigma2 = _numpy_blur(right * right, sigma) - mu2 * mu2
    sigma12 = _numpy_blur(left * right, sigma) - mu1 * mu2
    score = ((2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)) / ((mu1 * mu1 + mu2 * mu2 + c1) * (sigma1 + sigma2 + c2))
    return float(np.clip(score.mean(), 0.0, 1.0))


def _flat_white(gray: np.ndarray) -> np.ndarray:
    luma = gray.astype(np.float64)
    mean = _box_mean(luma, 2)
    var = _box_mean(luma * luma, 2) - mean * mean
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
    src_gray = _gray(source_bgr)
    dst_gray = _gray(render_bgr)
    novel = _flat_white(dst_gray) & ~_flat_white(src_gray)
    if not bool(novel.any()):
        return False
    # Thickness, not the bounding box: a 1px halo around a letter is as wide as
    # the word, but it is not a solid block. A square of 30% of the glyph height
    # has to fit inside the new white region.
    limit = WHITE_FRACTION * _glyph_height(src_gray)
    radius = max(1, int(round(limit * 0.5)))
    return bool(_erode_square(novel, radius).any())


def _edge_clipped(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """Ink on the left or right edge of the render that the source keeps inset."""
    if source_bgr is None or render_bgr is None or render_bgr.size == 0:
        return False
    height, width = render_bgr.shape[:2]
    if width < 8 or height < 8:
        return False
    src = _gray(source_bgr)
    dst = _gray(render_bgr)
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


def _plate_rect(block, source_shape, placed, plate_shape, padded: bool = True, neighbours=None) -> tuple:
    from vector_text_v2 import _rect

    raw = _rect(block, int(source_shape[1]), int(source_shape[0]))
    chosen = _expand_rect(raw, int(source_shape[1]), int(source_shape[0]), neighbours or ()) if padded else raw
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


def _vector_glyphs_disagree(render_bgr: np.ndarray, text: str) -> bool:
    """True when one vector glyph does not belong with the others in its line.

    A soft letter beside hard ones is still the photograph. A letter that is
    taller than the same character elsewhere in the line is a bad trace.
    Small body copy is not judged this way.
    """
    if render_bgr is None or getattr(render_bgr, "ndim", 0) != 3:
        return False
    if render_bgr.shape[0] < 8 or render_bgr.shape[1] < 8:
        return False
    gray = cv2.cvtColor(render_bgr, cv2.COLOR_BGR2GRAY)
    ink = _display_ink(gray)
    if ink is None:
        return False
    ink = _text_band(ink)
    parts = [part for part in _components(ink, 12) if part["h"] >= 8]
    if len(parts) < 4:
        return False
    median_h = float(np.median([part["h"] for part in parts]))
    if median_h < GLYPH_MIN_H:
        return False
    # A logo in the same crop is taller than the letters. It is not a glyph.
    sig = [part for part in parts if 0.62 * median_h <= part["h"] <= 1.35 * median_h]
    if len(sig) < 4:
        return False
    hard = [_glyph_hardness(gray, part) for part in sig]
    median_hard = float(np.median(hard))
    if median_hard >= 0.85 and any(value < 0.75 for value in hard):
        return True
    sharp = [_glyph_sharpness(gray, part) for part in sig]
    median_sharp = float(np.median(sharp))
    # Sharpness only confirms a soft stroke. Dark body copy is not judged on it.
    if median_hard >= 0.85 and median_sharp >= 40.0:
        for value, hardness in zip(sharp, hard):
            if value < 0.55 * median_sharp and hardness < 0.80:
                return True
    letters = [ch for ch in str(text or "").upper() if ch.isalnum()]
    if len(letters) != len(sig):
        return False
    groups = {}
    for index, ch in enumerate(letters):
        groups.setdefault(ch, []).append(sig[index])
    median_top = float(np.median([part["y"] for part in sig]))
    for glyphs in groups.values():
        if len(glyphs) < 2:
            continue
        donor = min(glyphs, key=lambda part: (abs(part["h"] - median_h), abs(part["y"] - median_top)))
        for glyph in glyphs:
            if glyph is donor:
                continue
            if abs(glyph["h"] - donor["h"]) <= max(2.0, 0.10 * median_h):
                continue
            if _glyph_iou(glyph["pixels"], donor["pixels"]) < 0.70:
                return True
    return False


def _text_band(ink: np.ndarray) -> np.ndarray:
    """Keep the row the letters sit on. A face or logo outside that row is not a glyph."""
    rows = (ink > 0).sum(axis=1)
    if int(rows.max()) < 4:
        return ink
    peak = int(np.argmax(rows))
    limit = max(3, int(rows[peak] * 0.25))
    y0 = peak
    while y0 > 0 and int(rows[y0 - 1]) >= limit:
        y0 -= 1
    y1 = peak + 1
    while y1 < len(rows) and int(rows[y1]) >= limit:
        y1 += 1
    y0 = max(0, y0 - 1)
    y1 = min(len(rows), y1 + 1)
    band = np.zeros_like(ink)
    band[y0:y1] = ink[y0:y1]
    return band


def _display_ink(gray: np.ndarray):
    """Ink mask for a rendered line. None when the crop is not a text line."""
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary > 0
    border = np.zeros(ink.shape, np.bool_)
    border[:2, :] = True
    border[-2:, :] = True
    border[:, :2] = True
    border[:, -2:] = True
    if int(border.sum()) > 0 and float(ink[border].mean()) > 0.5:
        ink = ~ink
    # A light letter is the solid core. Midtones belong to a logo or a blur
    # and would glue that logo onto the last glyph.
    if int(ink.sum()) > 20 and float(np.median(gray[ink])) >= 160.0:
        ink = ink & (gray >= 220)
    fraction = float(ink.mean())
    if fraction < 0.02 or fraction > 0.55:
        return None
    return ink.astype(np.uint8) * 255


def _glyph_hardness(gray: np.ndarray, part: dict) -> float:
    """Share of the stroke that is as solid as the ink, not a soft fringe."""
    values = gray[part["pixels"]]
    if values.size < 4:
        return 0.0
    if float(np.median(values)) >= 140.0:
        solid = float((values > 240).sum())
        body = float((values > 180).sum())
    else:
        solid = float((values < 20).sum())
        body = float((values < 80).sum())
    return solid / max(1.0, body)


def _glyph_sharpness(gray: np.ndarray, part: dict) -> float:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge = cv2.dilate(part["pixels"].astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    edge &= ~(cv2.erode(part["pixels"].astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    if int(edge.sum()) < 4:
        return 0.0
    return float(np.median(np.hypot(gx, gy)[edge]))


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
    neighbours = _letter_rects(blocks, int(source_shape[1]), int(source_shape[0]))
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            continue
        rect = _plate_rect(block, source_shape, placed, plate_shape, padded=True, neighbours=neighbours)
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
        # A vector line whose glyphs do not match each other is put back.
        # The check is not applied to raster, so the source pixels can still pass.
        glyph_fail = bool(vector and _vector_glyphs_disagree(render, text))
        if glyph_fail:
            score["pixelFail"] = True
            score["ok"] = False
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


def _paint_trace(plate_crop: np.ndarray, item: dict) -> np.ndarray:
    """The crop as the press file shows it: choked paper with the trace filled in."""
    render = np.array(plate_crop, copy=True)
    painted = item.get("painted")
    colour = item.get("colour")
    if painted is None or colour is None or painted.shape[:2] != render.shape[:2]:
        return render
    render[painted > 0] = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    return render


def _score_modest(source: np.ndarray, render: np.ndarray, source_text: str, render_text: str) -> dict:
    """SSIM, white-block and clip on a small numpy copy of one box."""
    source = _modest(source)
    render = _modest(render)
    if source is None or render is None or source.size == 0 or render.size == 0:
        return _score_pair(source, render, source_text, render_text)
    height = min(source.shape[0], render.shape[0])
    width = min(source.shape[1], render.shape[1])
    return _score_pair(source[:height, :width], render[:height, :width], source_text, render_text)


def _slice_inner(image: np.ndarray, rect, inner) -> np.ndarray:
    """Drop the pad. A neighbour in the pad is not this line."""
    if image is None or image.size == 0:
        return image
    x = max(0, int(inner[0]) - int(rect[0]))
    y = max(0, int(inner[1]) - int(rect[1]))
    return image[y:y + int(inner[3]), x:x + int(inner[2])]


def _ink_crop(image: np.ndarray) -> np.ndarray:
    """Trim a rendered box to the letters so the strip is tall enough to read."""
    if image is None or getattr(image, "size", 0) == 0 or image.ndim != 3:
        return image
    if image.shape[0] < 4 or image.shape[1] < 4:
        return image
    edges = np.concatenate([image[:2, :, :].reshape(-1, 3), image[-2:, :, :].reshape(-1, 3)], axis=0)
    paper = np.median(edges, axis=0)
    delta = np.max(np.abs(image.astype(np.int16) - paper.reshape(1, 1, 3)), axis=2)
    ys, xs = np.where(delta > 28)
    if ys.size < 12:
        return image
    pad = max(2, int(round(0.12 * (int(ys.max()) - int(ys.min()) + 1))))
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(image.shape[0], int(ys.max()) + 1 + pad)
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(image.shape[1], int(xs.max()) + 1 + pad)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return image
    return np.ascontiguousarray(image[y0:y1, x0:x1])


def _trim_strip(canvas: np.ndarray, rects: list) -> tuple[np.ndarray, list]:
    """Drop the unused margin. A word in a wide blank strip is invisible to OCR."""
    used = [rect for rect in rects if rect is not None]
    if not used:
        return canvas, rects
    x1 = min(canvas.shape[1], max(rect[2] for rect in used) + 16)
    y1 = min(canvas.shape[0], max(rect[3] for rect in used) + 16)
    return np.ascontiguousarray(canvas[:y1, :x1]), rects


def _slot_size(crop: np.ndarray, slot_h: int, max_w: int) -> tuple[int, int] | None:
    if crop is None or getattr(crop, "size", 0) == 0 or crop.shape[0] < 2 or crop.shape[1] < 2:
        return None
    slot_w = max(8, int(round(crop.shape[1] * slot_h / float(crop.shape[0]))))
    if slot_w > max_w:
        slot_w = max_w
        slot_h = max(16, int(round(crop.shape[0] * slot_w / float(crop.shape[1]))))
    return slot_w, slot_h


def _pack_render_strip(crops: list) -> tuple[np.ndarray, list]:
    """One strip of the rendered boxes. The long edge stays under 1280.

    Slots in one row are at least 48px apart. Closer than that, the reader
    joins two lines into one word. Columns stay inside the same limit.
    """
    max_edge = 1200
    hgap = 48
    vgap = 8
    margin = 12
    chosen = None
    for slot_h in (40, 34, 28):
        pitch = slot_h + vgap
        rows_fit = max(1, (max_edge - margin * 2) // pitch)
        count = max(1, len(crops))
        cols = max(1, int(np.ceil(count / float(rows_fit))))
        col_w = int((max_edge - margin * 2 - hgap * max(0, cols - 1)) / cols)
        if col_w < 80:
            continue
        wide = 0
        for crop in crops:
            if crop is None or getattr(crop, "size", 0) == 0 or crop.shape[0] < 2:
                continue
            natural = crop.shape[1] * float(slot_h) / float(crop.shape[0])
            if natural > col_w + 1:
                wide += 1
        height = margin * 2 + min(rows_fit, count) * pitch
        width = margin * 2 + cols * col_w + hgap * max(0, cols - 1)
        if height > max_edge or width > max_edge:
            continue
        # Prefer a layout that does not squash a line, then the taller letters.
        rank = (wide, -slot_h)
        if chosen is None or rank < chosen[0]:
            chosen = (rank, slot_h, rows_fit, cols, col_w, height, width)
        if wide == 0:
            break
    if chosen is None:
        return _pack_render_strip_column(crops, 28, max_edge)
    _rank, slot_h, rows_fit, cols, col_w, height, width = chosen
    sizes = [_slot_size(crop, slot_h, col_w) for crop in crops]
    canvas = np.full((height, width, 3), 255, np.uint8)
    rects = []
    for index, size in enumerate(sizes):
        if size is None:
            rects.append(None)
            continue
        col = index // rows_fit
        row = index % rows_fit
        slot_w, used_h = size
        x = margin + col * (col_w + hgap)
        y = margin + row * (slot_h + vgap) + max(0, (slot_h - used_h) // 2)
        resized = cv2.resize(crops[index], (slot_w, used_h), interpolation=cv2.INTER_CUBIC)
        canvas[y:y + used_h, x:x + slot_w] = resized
        rects.append((x, y, x + slot_w, y + used_h))
    return _trim_strip(canvas, rects)


def _pack_render_strip_column(crops: list, slot_h: int, max_edge: int) -> tuple[np.ndarray, list]:
    gap = 8
    margin = 12
    width = max_edge
    sizes = [_slot_size(crop, slot_h, max_edge - margin * 2) for crop in crops]
    height = margin * 2 + len(crops) * (slot_h + gap)
    canvas = np.full((height, width, 3), 255, np.uint8)
    rects = []
    for index, size in enumerate(sizes):
        if size is None:
            rects.append(None)
            continue
        slot_w, used_h = size
        x = margin
        y = margin + index * (slot_h + gap)
        resized = cv2.resize(crops[index], (slot_w, used_h), interpolation=cv2.INTER_CUBIC)
        canvas[y:y + used_h, x:x + slot_w] = resized
        rects.append((x, y, x + slot_w, y + used_h))
    return _trim_strip(canvas, rects)


def _slot_reading(parsed, rect) -> str:
    if rect is None:
        return ""
    x0, y0, x1, y1 = rect
    hits = []
    for rx0, ry0, rx1, ry1, text in parsed:
        cx = (rx0 + rx1) * 0.5
        cy = (ry0 + ry1) * 0.5
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        area = max(1.0, (rx1 - rx0) * (ry1 - ry0))
        overlap = max(0.0, min(x1, rx1) - max(x0, rx0)) * max(0.0, min(y1, ry1) - max(y0, ry0))
        # A detection that spills into the next slot is not this line.
        if overlap / area < 0.55:
            continue
        hits.append((rx0, text))
    hits.sort(key=lambda item: item[0])
    return _norm_text(" ".join(text for _x, text in hits))


def _reread_empty_slots(strip: np.ndarray, rects: list, readings: list, sources: list) -> None:
    """A skipped or mismatched slot is read on its own. A still-empty read is not a match."""
    from vector_text_v2 import read_blocks

    blanks = []
    for index, text in enumerate(readings):
        if rects[index] is None:
            continue
        source = sources[index] if index < len(sources) else ""
        if text == "" or not _reads_match(source, text):
            blanks.append(index)
    if not blanks:
        return
    pieces = []
    for index in blanks:
        x0, y0, x1, y1 = rects[index]
        slot = strip[y0:y1, x0:x1]
        if slot.size == 0:
            pieces.append(None)
            continue
        pieces.append(cv2.copyMakeBorder(slot, 18, 18, 18, 18, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    usable = [piece for piece in pieces if piece is not None]
    if not usable:
        return
    gap = 32
    width = max(piece.shape[1] for piece in usable)
    height = sum(piece.shape[0] for piece in usable) + gap * (len(usable) - 1)
    canvas = np.full((max(1, height), max(1, width), 3), 255, np.uint8)
    y = 0
    boxes = []
    for piece in pieces:
        if piece is None:
            boxes.append(None)
            continue
        canvas[y:y + piece.shape[0], 0:piece.shape[1]] = piece
        boxes.append((0, y, piece.shape[1], y + piece.shape[0]))
        y += piece.shape[0] + gap
    long_edge = max(canvas.shape[0], canvas.shape[1])
    if long_edge > 1280:
        scale = 1280.0 / float(long_edge)
        canvas = cv2.resize(
            canvas,
            (max(1, int(round(canvas.shape[1] * scale))), max(1, int(round(canvas.shape[0] * scale)))),
            interpolation=cv2.INTER_AREA,
        )
        boxes = [None if box is None else tuple(int(round(value * scale)) for value in box) for box in boxes]
    rows = read_blocks(canvas, extra=False)
    parsed = _parse_reads(rows, canvas.shape[1], canvas.shape[0])
    for index, box in zip(blanks, boxes):
        fresh = _slot_reading(parsed, box)
        if not fresh:
            continue
        source = sources[index] if index < len(sources) else ""
        if readings[index] == "" or _reads_match(source, fresh):
            readings[index] = fresh


def _read_render_strip(crops: list, sources: list) -> list:
    """One OCR pass, then a second pass for any slot the strip left blank."""
    from vector_text_v2 import read_blocks

    if not crops:
        return []
    crops = [_ink_crop(crop) for crop in crops]
    strip, rects = _pack_render_strip(crops)
    rows = read_blocks(strip, extra=False)
    parsed = _parse_reads(rows, strip.shape[1], strip.shape[0])
    readings = [_slot_reading(parsed, rect) for rect in rects]
    _reread_empty_slots(strip, rects, readings, sources)
    return readings


def _apply_text_gate(
    blocks, drawn, raster_lines, raster_boxes, plate, pristine, output_pdf,
    trim_w, trim_h, bleed_mm, placed, qa, source_shape, source_guard,
):
    """Case-sensitive render text against the source text already read.

    Pixel checks stay on the cropped box. The render is read once, from a
    strip of just those boxes. A mismatch, a merged glyph, or a split glyph
    puts the whole line back as raster.
    """
    plate_shape = plate.shape if pristine is None else pristine.shape
    base = pristine if pristine is not None else plate
    by_rect = list(drawn)
    report = []
    vector_slots = []
    letter_rects = _letter_rects(blocks, int(source_shape[1]), int(source_shape[0]))
    plate_ppi = float((placed or {}).get("ppi") or 400)
    # Rejected traces (empty, shape, topology, a later gate miss) count toward
    # the paragraph. Ink that was never separated does not: it only blanks its line.
    paragraph_cores = [
        box.get("core") or box.get("rect")
        for box in (raster_boxes or [])
        if box.get("anchor") is True or box.get("anchor") == "paragraph"
    ]
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if not is_lettering(text):
            continue
        rect = _plate_rect(block, source_shape, placed, plate_shape, padded=True, neighbours=letter_rects)
        inner = _plate_rect(block, source_shape, placed, plate_shape, padded=False)
        item = next((candidate for candidate in by_rect if _rects_match(candidate.get("rect"), rect)), None)
        vector = item is not None
        source = _slice_inner(_plate_crop(base, rect), rect, inner)
        render = _slice_inner(_plate_crop(plate, rect), rect, inner)
        glyph_fail = False
        if vector:
            render = _paint_trace(_plate_crop(plate, rect), item)
            render = _slice_inner(render, rect, inner)
            ink = _slice_inner(item.get("_ink"), rect, inner) if item.get("_ink") is not None else None
            painted = _slice_inner(item.get("painted"), rect, inner) if item.get("painted") is not None else None
            if ink is not None and painted is not None and ink.shape[:2] == painted.shape[:2]:
                glyph_fail = _glyph_structure_fails(ink, painted)
                if glyph_fail and os.environ.get("GLYPH_DEBUG"):
                    sys.stderr.write(f"[glyph] text {text!r}\n")
            else:
                glyph_fail = True
            full = item.get("painted")
            origin = _hide_foreign_ink(_plate_crop(base, rect), item.get("_ink"))
            if (
                full is not None
                and not glyph_fail
                and origin is not None
                and origin.shape[:2] == full.shape[:2]
                and _topology_fails(origin, full, plate_ppi)
            ):
                glyph_fail = True
        core = item.get("core") if item is not None and item.get("core") else inner
        bbox = block.get("bbox") or [0, 0, 0, 0]
        row = {
            "text": text,
            "ok": False,
            "render": "",
            "mode": "vector" if vector else "raster",
            "ssim": 0.0,
            "whiteBlock": False,
            "clipped": False,
            "glyphFail": bool(glyph_fail),
            "mismatch": False,
            "y": round(float(bbox[1]) if len(bbox) > 1 else 0.0, 4),
            "boxMm": _box_mm(rect, plate_shape, trim_w, trim_h, bleed_mm),
            "_rect": rect,
            "_core": core,
            "_vector": vector,
            "_source": source,
            "_render": render,
            "_revert": False,
            "_paragraph": (not vector) and any(
                _rects_match(core, other) for other in paragraph_cores
            ),
        }
        report.append(row)
        if vector:
            vector_slots.append(row)
    readings = _read_render_strip(
        [row["_render"] for row in vector_slots],
        [row["text"] for row in vector_slots],
    ) if vector_slots else []
    for row, reading in zip(vector_slots, readings):
        row["render"] = reading
        row["mismatch"] = not _reads_match(row["text"], reading)
    for row in report:
        if row["_vector"]:
            continue
        row["render"] = _norm_text(row["text"])
    for row in report:
        score = _score_modest(row["_source"], row["_render"], row["text"], row["render"] or row["text"])
        row["ssim"] = score["ssim"]
        row["whiteBlock"] = score["whiteBlock"]
        row["clipped"] = score["clipped"]
        row["_pixelFail"] = bool(score["pixelFail"])
        if not row["_vector"]:
            row["ok"] = bool(score["ok"])
    parent = list(range(len(report)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        parent[find(left)] = find(right)

    for i in range(len(report)):
        for j in range(i + 1, len(report)):
            if _same_line(report[i]["_core"], report[j]["_core"]):
                union(i, j)
    for index, row in enumerate(report):
        if not row["_vector"]:
            continue
        low_ssim = float(row["ssim"]) < SSIM_FLOOR
        if row["mismatch"] or row["glyphFail"] or row["_pixelFail"] or low_ssim:
            root = find(index)
            for other in range(len(report)):
                if find(other) == root and report[other]["_vector"]:
                    report[other]["_revert"] = True
    line_members = {}
    for index in range(len(report)):
        line_members.setdefault(find(index), []).append(index)
    line_rects = {}
    for line_id, members in line_members.items():
        rects = [report[member]["_core"] for member in members]
        x0 = min(rect[0] for rect in rects)
        y0 = min(rect[1] for rect in rects)
        x1 = max(rect[0] + rect[2] for rect in rects)
        y1 = max(rect[1] + rect[3] for rect in rects)
        line_rects[line_id] = (x0, y0, x1 - x0, y1 - y0)
    para_lines = {}
    if line_rects:
        for line_id, root in _paragraph_roots(line_rects).items():
            para_lines.setdefault(root, []).append(line_id)
    for grouped in para_lines.values():
        fallback = 0
        for line_id in grouped:
            members = line_members[line_id]
            if any(report[i]["_revert"] or report[i].get("_paragraph") for i in members):
                fallback += 1
        if fallback <= 1:
            continue
        for line_id in grouped:
            for index in line_members[line_id]:
                if report[index]["_vector"]:
                    report[index]["_revert"] = True
    kept = []
    reverted = False
    if pristine is not None:
        for item in drawn:
            row = next(
                (candidate for candidate in report if _rects_match(candidate.get("_rect"), item.get("rect"))),
                None,
            )
            if row is None or not row.get("_revert"):
                kept.append(item)
                continue
            _restore_glyphs(plate, pristine, item)
            if row.get("mismatch") or row.get("glyphFail"):
                why = "The render did not read back as this line, so the line stayed in the picture."
            elif row.get("_pixelFail"):
                why = "The render covered or clipped this line, so the line stayed in the picture."
            elif float(row.get("ssim") or 1) < SSIM_FLOOR:
                why = "The render did not match this line's picture, so the line stayed in the picture."
            else:
                why = "This line stayed in the picture so it would not be half traced."
            raster_lines.append(_line(item["text"], "raster", why))
            core = item.get("core") or item.get("rect")
            if core is not None:
                raster_boxes.append({
                    "text": item.get("text") or "",
                    "rect": core,
                    "core": core,
                    "anchor": "paragraph",
                })
            reverted = True
            row["mode"] = "raster"
            row["ok"] = False
    else:
        kept = list(drawn)
    for row in report:
        if row["_vector"] and not row["_revert"]:
            row["ok"] = bool(
                _reads_match(row["text"], row["render"])
                and not row["glyphFail"]
                and not row["_pixelFail"]
                and float(row["ssim"]) >= SSIM_FLOOR
            )
            row["mode"] = "vector"
    before = len(kept)
    if pristine is not None:
        art_box = (placed or {}).get("art_box") or (0, 0, plate.shape[1], plate.shape[0])
        kept, source_guard = _repair_source(plate, pristine, kept, raster_lines, raster_boxes, art_box)
        source_guard = bool(source_guard)
    else:
        source_guard = True
    alive = [item.get("rect") for item in kept]
    for row in report:
        if row.get("_vector") and not any(_rects_match(row.get("_rect"), rect) for rect in alive):
            row["mode"] = "raster"
            row["ok"] = False
    if reverted or len(kept) != before:
        first_colour = float(qa.get("colour_s") or 0)
        first_compose = float(qa.get("compose_s") or 0)
        qa = _write_pdf(plate, kept, output_pdf, trim_w, trim_h, bleed_mm, placed)
        qa["colour_s"] = first_colour + float(qa.get("colour_s") or 0)
        qa["compose_s"] = first_compose + float(qa.get("compose_s") or 0)
    for item in kept:
        item.pop("_ink", None)
        item.pop("core", None)
    for row in report:
        for key in ("_rect", "_core", "_vector", "_source", "_render", "_revert", "_pixelFail", "_paragraph"):
            row.pop(key, None)
    return report, kept, qa, source_guard


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
    raw = os.environ.get("VECTOR_TRACE_WORKERS", "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
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
