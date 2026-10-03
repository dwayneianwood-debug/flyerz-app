#!/usr/bin/env python3
"""Trace the original lettering into vector paths.

The picture is enlarged and left as it is. Each text box is split into ink
and paper, the ink mask is traced with potrace, and those paths are filled
in the same place with the sampled ink colour. A box whose trace does not
match the ink (IoU under 0.9) stays as pixels, then may be set as live type
when a second word-by-word read agrees and a bundled face matches the ink.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

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
# potrace %f is six digits. A decimal comma looks like 0,100000 and must not
# be read as two integers. Path data is integers, so this only touches %f.
_POTRACE_FLOAT = re.compile(r"(\d),(\d{6})(?!\d)")
# Potrace starts once per bitmap. The timing test adds the Windows cost of that start.
_spawn_count = 0


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
        # A pale crossbar is a small fraction of the letter. The channel
        # through an N, or a spur beside a T, is about half the stroke.
        if extra > max(8, int(round(0.32 * core))):
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
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        w = int(stats[index, cv2.CC_STAT_WIDTH])
        h = int(stats[index, cv2.CC_STAT_HEIGHT])
        window = labels[y:y + h, x:x + w]
        comp = window == index
        local = distance[y:y + h, x:x + w] * comp
        peak = float(local.max()) if local.size else 0.0
        core = local >= max(0.8, peak * 0.45)
        if int(core.sum()) < 3:
            core = comp
        tone = float(np.median(gray[y:y + h, x:x + w][core]))
        found.append((index, area, tone, x, y, w, h, core))
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
    for index, _area, _tone, x, y, w, h, core in kept:
        cleaned[y:y + h, x:x + w][labels[y:y + h, x:x + w] == index] = 255
        samples.append(roi[y:y + h, x:x + w][core])
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
    count, labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    keep = np.zeros((height, width), np.uint8)
    boxes = []
    kept_flag = np.zeros(count, np.bool_)
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 2:
            continue
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        w = int(stats[index, cv2.CC_STAT_WIDTH])
        h = int(stats[index, cv2.CC_STAT_HEIGHT])
        # Inclusive edges, matching a pixel walk of this component.
        right = x + w - 1
        bottom = y + h - 1
        ox0, oy0 = max(x, x0), max(y, y0)
        ox1, oy1 = min(x + w, x1), min(y + h, y1)
        inside = 0
        if ox1 > ox0 and oy1 > oy0:
            inside = int(np.count_nonzero(labels[oy0:oy1, ox0:ox1] == index))
        # A descender is mostly below the word box. Keeping it only when 40% of
        # the stroke sits in that box drops the tail and leaves a floating speck.
        if inside >= max(4, int(round(0.08 * area))):
            keep[y:y + h, x:x + w][labels[y:y + h, x:x + w] == index] = 255
            kept_flag[index] = True
        boxes.append((index, x, y, right, bottom, area))
    # A tail that the threshold split off sits under a kept letter and outside
    # the word box. It is not the next line: it is short, and it shares the
    # letter's columns.
    kept_ids = [item for item in boxes if kept_flag[item[0]]]
    for index, left, top, right, bottom, area in boxes:
        if kept_flag[index] or area < 4:
            continue
        if top < y1:
            continue
        if bottom - top > max(8, int(round(0.55 * (y1 - y0)))):
            continue
        for _kept, k_left, _k_top, k_right, k_bottom, _k_area in kept_ids:
            gap = top - k_bottom - 1
            if gap < 0 or gap > max(6, int(round(0.35 * (y1 - y0)))):
                continue
            overlap = min(right, k_right) - max(left, k_left)
            if overlap >= max(2, int(round(0.35 * (right - left + 1)))):
                y = int(stats[index, cv2.CC_STAT_TOP])
                x = int(stats[index, cv2.CC_STAT_LEFT])
                h = int(stats[index, cv2.CC_STAT_HEIGHT])
                w = int(stats[index, cv2.CC_STAT_WIDTH])
                keep[y:y + h, x:x + w][labels[y:y + h, x:x + w] == index] = 255
                break
    if int(keep.max()) == 0:
        return None
    return _join_descenders(keep, (x0, y0, x1, y1))


def _join_descenders(mask: np.ndarray, inner: tuple) -> np.ndarray:
    """Bridge a one-pixel crack between a letter and the tail under it.

    Upscale and the threshold open that crack, so the tail is traced as a
    floating speck. The bridge is two pixels wide, only where the tail already
    sits under that letter. A longer gap is paper, or the next line: filling
    it draws a stroke the picture does not have.
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
            # Two pixels is the opened join. Anything longer reaches past the ink.
            if gap < 1 or gap > 2:
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
    binary = _trace_binary(mask)
    if binary is None:
        return []
    return _scale_paths(_paths_from_svg(_potrace_svg(binary), binary.shape[0]), 1.0 / TRACE_SCALE)


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

    The letter body is the long heavy run of the mask. A flourish wider than
    the letters, hanging below that body, is not erased and not traced: a
    vector of it turns into bars, and those bars sit inside the source-ink
    fringe so the page guard misses them. A normal descender is about one
    letter wide and stays in the mask.
    """
    if mask is None or int(mask.max()) == 0:
        return mask
    rows = (mask > 0).sum(axis=1)
    peak = int(rows.max()) if rows.size else 0
    if peak < 8:
        return mask
    heavy = rows >= 0.45 * peak
    runs = []
    index = 0
    while index < heavy.size:
        if not heavy[index]:
            index += 1
            continue
        end = index
        while end < heavy.size and heavy[end]:
            end += 1
        runs.append((index, end))
        index = end
    if not runs:
        return mask
    _start, end = max(runs, key=lambda run: run[1] - run[0])
    baseline = min(mask.shape[0], end + 2)
    if baseline >= mask.shape[0] - 2 or int(rows[baseline:].sum()) < 40:
        return mask
    upper = np.zeros(mask.shape, np.uint8)
    upper[:baseline] = mask[:baseline]
    letterlike = [part for part in _components(upper, 20) if part["w"] <= 2.5 * max(1, part["h"])]
    if len(letterlike) < 2:
        return mask
    median_w = float(np.median([part["w"] for part in letterlike]))
    # Wider than the letters, and wide on the page. A descender is neither.
    limit = max(2.8 * median_w, 80.0)
    tail = np.zeros(mask.shape, np.uint8)
    tail[baseline:] = mask[baseline:]
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats((tail > 0).astype(np.uint8), 8)
    wide = False
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 80:
            continue
        if int(stats[index, cv2.CC_STAT_WIDTH]) > limit:
            wide = True
            break
    if not wide:
        return mask
    out = mask.copy()
    out[baseline:] = 0
    if int(out.max()) == 0 or int((out > 0).sum()) < 0.45 * int((mask > 0).sum()):
        return mask
    return out


def _fill_region(crop: np.ndarray, mask: np.ndarray, sigma: float, sigma_wide: float) -> np.ndarray:
    """Paper colour under `mask`. `mask` is 0 on paper and >0 on the letter."""
    keep = (mask == 0).astype(np.float32)
    colour = crop.astype(np.float32) * keep[..., None]
    num = cv2.GaussianBlur(colour, (0, 0), sigma)
    den = cv2.GaussianBlur(keep, (0, 0), sigma)
    filled = num / np.maximum(den[..., None], 1e-3)
    weak = (mask > 0) & (den < 0.08)
    if bool(weak.any()):
        num_wide = cv2.GaussianBlur(colour, (0, 0), sigma_wide)
        den_wide = cv2.GaussianBlur(keep, (0, 0), sigma_wide)
        wide = num_wide / np.maximum(den_wide[..., None], 1e-3)
        filled[weak] = wide[weak]
    return filled


def _fill_from_paper(plate: np.ndarray, erase: np.ndarray) -> np.ndarray:
    """Replace the raster letter with the paper around it. The vector is drawn on top.

    The blur only covers the letters, plus a margin the kernel can see.
    A page of a few megapixels is blurred at a quarter of the size with the
    same radius. That is the same paper colour, and it is the slow part of a
    poster or a flyer.
    """
    if erase is None or int(erase.max()) == 0:
        return plate
    ys, xs = np.where(erase > 0)
    if ys.size == 0:
        return plate
    # Sigma 36 still has weight out to about three radii.
    margin = 128
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(plate.shape[0], int(ys.max()) + 1 + margin)
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(plate.shape[1], int(xs.max()) + 1 + margin)
    crop = plate[y0:y1, x0:x1]
    mask = erase[y0:y1, x0:x1]
    area = int(crop.shape[0]) * int(crop.shape[1])
    if area >= 1_600_000:
        scale = 4
        small = (max(1, crop.shape[1] // scale), max(1, crop.shape[0] // scale))
        small_colour = cv2.resize(crop, small, interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize(mask, small, interpolation=cv2.INTER_AREA)
        # A pixel that is only partly the letter still has to be a hole,
        # or the letter colour leaks into the paper.
        hole = np.where(small_mask >= 8, np.uint8(255), np.uint8(0))
        filled_s = _fill_region(small_colour, hole, 14.0 / scale, 36.0 / scale)
        filled = cv2.resize(filled_s, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
    else:
        filled = _fill_region(crop, mask, 14.0, 36.0)
    out = plate.copy()
    view = out[y0:y1, x0:x1]
    write = mask > 0
    view[write] = np.clip(np.rint(filled[write]), 0, 255).astype(np.uint8)
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
    return False


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


def _raster_box(text: str, left: int, top: int, right: int, bottom: int, core=None, scope: str = "paragraph", style=None) -> dict:
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
        "style": _as_style(style),
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


def _as_style(value):
    """Three colour numbers, or nothing when this line has no ink sample."""
    if value is None:
        return None
    try:
        arr = np.asarray(value, np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if arr.size < 3:
        return None
    return (float(arr[0]), float(arr[1]), float(arr[2]))


def _sample_style(image: np.ndarray):
    """Median ink colour of a crop. The border is the paper."""
    if image is None or getattr(image, "size", 0) == 0 or getattr(image, "ndim", 0) != 3:
        return None
    if image.shape[0] < 4 or image.shape[1] < 4:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = np.concatenate([
        gray[:2, :].reshape(-1),
        gray[-2:, :].reshape(-1),
        gray[:, :2].reshape(-1),
        gray[:, -2:].reshape(-1),
    ])
    paper = float(np.median(edges))
    ink = np.abs(gray.astype(np.int16) - paper) > 28
    if int(ink.sum()) < 8:
        return None
    return _as_style(np.median(image[ink], axis=0))


def _same_text_style(left, right) -> bool:
    """Same font size and the same ink colour."""
    if left.get("colour") is None or right.get("colour") is None:
        return False
    h1 = float(left["rect"][3])
    h2 = float(right["rect"][3])
    if abs(h1 - h2) > max(4.0, 0.18 * max(h1, h2, 1.0)):
        return False
    return float(np.linalg.norm(
        np.asarray(left["colour"], np.float32) - np.asarray(right["colour"], np.float32)
    )) <= 40.0


def _same_column(above, below) -> bool:
    """Stacked lines of one column. A page-wide title does not swallow a narrow column."""
    ax, ay, aw, ah = [float(v) for v in above]
    bx, by, bw, bh = [float(v) for v in below]
    overlap = min(ax + aw, bx + bw) - max(ax, bx)
    if overlap <= 0:
        return False
    narrow = min(aw, bw)
    wide = max(aw, bw)
    if narrow < 0.55 * wide:
        return False
    if overlap < 0.45 * narrow:
        return False
    if ay + ah <= by:
        gap = by - (ay + ah)
    elif by + bh <= ay:
        gap = ay - (by + bh)
    else:
        gap = 0.0
    # A section is stacked tighter than a gap between topics. 14× chained the whole page.
    return gap <= 1.1 * max(ah, bh, 1.0)


def _same_band(above, below) -> bool:
    """Two lines of one row. A multi-column section shares a weight across the row."""
    ax, ay, aw, ah = [float(v) for v in above]
    bx, by, bw, bh = [float(v) for v in below]
    overlap = min(ay + ah, by + bh) - max(ay, by)
    short = min(ah, bh)
    if short <= 0 or overlap < 0.45 * short:
        return False
    if ax + aw <= bx:
        gap = bx - (ax + aw)
    elif bx + bw <= ax:
        gap = ax - (bx + bw)
    else:
        gap = 0.0
    return gap <= 8.0 * max(ah, bh, 1.0)


def _same_section(above, below) -> bool:
    return _same_column(above, below) or _same_band(above, below)


def _column_style_demote(entries: list) -> set:
    """Indexes to put back in the picture so one style in a column matches.

    Most siblings of the same size and colour are raster, so the traced ones
    stay in the picture too. Most siblings vector leaves a failed line in the
    picture and does not invent a trace for it. A line with no colour sample
    is not a sibling.
    """
    count = len(entries)
    if count < 2:
        return set()
    parent = list(range(count))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left, right):
        parent[find(left)] = find(right)

    for i in range(count):
        for j in range(i + 1, count):
            if not _same_text_style(entries[i], entries[j]):
                continue
            if not _same_section(entries[i]["rect"], entries[j]["rect"]):
                continue
            union(i, j)
    groups = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    demote = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        rasters = sum(1 for index in members if entries[index]["raster"])
        vectors = len(members) - rasters
        if rasters <= vectors:
            continue
        for index in members:
            if entries[index]["raster"]:
                continue
            for item in entries[index]["indexes"]:
                demote.add(item)
    return demote


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


def _split_stacked_ink(mask: np.ndarray) -> np.ndarray:
    """Cut a thin vertical streak that welds this line to the line above or below.

    Light type on a dark ground grows a 2–3px bridge across the gap, and the
    whole bridge becomes one component. The line below is then traced as a
    drip under this line, and painting it out erases the tops of those letters.
    A real stem or descender is wider than that bridge and stays. The same cut
    is used for dark type on a light ground.
    """
    if mask is None or int(np.max(mask)) == 0:
        return mask
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
    out = binary
    changed = False
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if area < 40 or height < 18:
            continue
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        window = labels[y:y + height, x:x + width]
        comp = window == index
        rows = comp.sum(axis=1).astype(np.int32)
        peak = int(rows.max()) if rows.size else 0
        if peak < 8:
            continue
        thin = max(3, int(round(0.16 * peak)))
        body = rows >= int(round(0.45 * peak))
        runs = []
        cursor = 0
        while cursor < height:
            if not body[cursor]:
                cursor += 1
                continue
            end = cursor
            while end < height and body[end]:
                end += 1
            runs.append((cursor, end))
            cursor = end
        if not runs:
            continue
        clear = np.zeros(height, np.bool_)
        first = runs[0][0]
        if first >= 3 and int(rows[:first].max()) <= thin:
            clear[:first] = True
        last = runs[-1][1]
        if last <= height - 3 and rows[last:].size and int(rows[last:].max()) <= thin:
            clear[last:] = True
        for (above_start, above_end), (below_start, below_end) in zip(runs, runs[1:]):
            if above_end - above_start < 4 or below_end - below_start < 4:
                continue
            # The streak is a few pixels wide. The next letter's shoulder is
            # wider, so only the thin run is cut. A one-row pinch is a letter.
            cursor = above_end
            while cursor < below_start:
                if int(rows[cursor]) > thin:
                    cursor += 1
                    continue
                end = cursor
                while end < below_start and int(rows[end]) <= thin:
                    end += 1
                if end - cursor >= 3:
                    clear[cursor:end] = True
                cursor = end
        if not bool(clear.any()):
            continue
        piece = out[y:y + height, x:x + width]
        for row in np.where(clear)[0]:
            piece[row, comp[row]] = 0
        changed = True
    if not changed:
        return mask
    return out * 255


def _keep_line_glyphs(mask: np.ndarray, left: int, top: int, owner: int, cores: list) -> np.ndarray | None:
    """Ink components whose centroid sits in this line's baseline band.

    A streak that only touches the gap is not an ascender of this line. It
    belongs to the line it actually touches, or it is a paint fragment and
    stays out of both traces.
    """
    if mask is None or int(np.max(mask)) == 0 or owner < 0 or owner >= len(cores):
        return None
    mask = _split_stacked_ink(mask)
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    owned = np.zeros(mask.shape[:2], np.uint8)
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 4:
            continue
        cx = float(left) + float(cents[index][0])
        cy = float(top) + float(cents[index][1])
        if _glyph_owner(cx, cy, cores) == owner:
            owned[labels == index] = 255
    if int(owned.max()) == 0:
        return None
    # A piece that never touches this line's own ink is the other line's edge.
    core_x, core_y, core_w, core_h = [int(v) for v in cores[owner]]
    band_top = max(0, core_y - int(top) - 2)
    band_bot = min(owned.shape[0], core_y + core_h - int(top) + 2)
    if band_bot <= band_top:
        return owned
    count, labels, stats, _cents = cv2.connectedComponentsWithStats((owned > 0).astype(np.uint8), 8)
    anchored = np.zeros(count, np.bool_)
    for index in range(1, count):
        y = int(stats[index, cv2.CC_STAT_TOP])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if y < band_bot and y + height > band_top:
            anchored[index] = True
    if not bool(anchored.any()):
        return owned
    near = cv2.dilate(anchored[labels].astype(np.uint8), np.ones((11, 11), np.uint8)) > 0
    for index in range(1, count):
        if anchored[index]:
            continue
        component = labels == index
        if bool((component & near).any()):
            continue
        owned[component] = 0
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
            "colour": _as_style(item.get("style")),
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
            "colour": _as_style(box.get("style")),
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
    style_rows = []
    for line_id, members in lines.items():
        colours = [records[member].get("colour") for member in members if records[member].get("colour") is not None]
        live = [
            records[member]["index"]
            for member in members
            if records[member]["mode"] == "vector"
            and records[member]["index"] is not None
            and records[member]["index"] not in drop
        ]
        style_rows.append({
            "rect": line_rect[line_id],
            "raster": not live,
            "colour": colours[0] if colours else None,
            "indexes": live,
        })
    drop.update(_column_style_demote(style_rows))
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


def _new_ink_in_band(painted: np.ndarray, origin, source_bgr: np.ndarray, band) -> int:
    """Largest blob of this line's paths that the source does not show inside ``band``.

    The band is another line's own box. A curve that sits on that line's ink
    is the shared edge. A stroke on the paper there is new, and it prints on
    top of the letters.
    """
    if painted is None or source_bgr is None or origin is None or band is None:
        return 0
    if getattr(painted, "size", 0) == 0 or source_bgr.ndim != 3:
        return 0
    left, top = int(origin[0]), int(origin[1])
    nx, ny, nw, nh = [int(v) for v in band]
    x0 = max(left, nx)
    y0 = max(top, ny)
    x1 = min(left + painted.shape[1], nx + nw)
    y1 = min(top + painted.shape[0], ny + nh)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0
    path = painted[y0 - top:y1 - top, x0 - left:x1 - left] > 0
    if not bool(path.any()):
        return 0
    found = _paper_ink(cv2.cvtColor(source_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
    if found is None:
        stray = path
    else:
        # Two pixels of curve past the soft edge is still that letter.
        # A stem dropped onto the next line is not.
        near = cv2.dilate(found[0].astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        stray = path & ~near
    if not bool(stray.any()):
        return 0
    _count, _labels, stats, _cent = cv2.connectedComponentsWithStats(stray.astype(np.uint8), 8)
    largest = 0
    for index in range(1, _count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        # The curve's lower edge can sit in the next box as a wide lip on the
        # paper gap. A stroke dropped onto that line is taller than it is wide.
        if area < 12 or height < 8 or height <= width:
            continue
        largest = max(largest, area)
    return largest


def _flag_neighbour_ink(report, drawn, source_bgr) -> None:
    """Send back a traced line whose paths add ink inside another line's box."""
    if source_bgr is None or not report:
        return
    bands = [row.get("_core") for row in report if row.get("_core") is not None]
    for row in report:
        if not row.get("_vector") or row.get("_revert"):
            continue
        item = next(
            (candidate for candidate in drawn if _rects_match(candidate.get("rect"), row.get("_rect"))),
            None,
        )
        if item is None:
            continue
        own = row.get("_core")
        for band in bands:
            if band is own:
                continue
            # A speck on the shared edge is the curve. A stroke is the defect.
            if _new_ink_in_band(item.get("painted"), item.get("origin"), source_bgr, band) >= 12:
                row["glyphFail"] = True
                row["_invadeFail"] = True
                break


def _separate_line(job: dict) -> dict:
    """Ink for one line. The plate is only read, so lines run together across cores."""
    plate = job["plate"]
    text = job["text"]
    left, top, right, bottom = job["bounds"]
    core = job["core"]
    inner_left, inner_top, inner_right, inner_bottom = job["inner"]
    owner = job["owner"]
    plate_cores = job["cores"]
    if right - left < 4 or bottom - top < 4:
        return {
            "line": _line(text, "raster", "The box was too small to trace."),
            "box": _raster_box(text, left, top, right, bottom, core, "line", None),
        }
    crop = np.ascontiguousarray(plate[top:bottom, left:right])
    style = _sample_style(crop)

    def reject(reason: str, scope: str = "line") -> dict:
        return {
            "line": _line(text, "raster", reason),
            "box": _raster_box(text, left, top, right, bottom, core, scope, style),
        }

    mask, colour = segment_ink(crop)
    if mask is None:
        return reject("The ink could not be separated from the picture.")
    mask = ink_touching(mask, (
        inner_left - left, inner_top - top, inner_right - left, inner_bottom - top,
    ))
    if mask is None:
        return reject("The ink sat outside this box, so it stayed in the picture.")
    mask, colour = refine_ink(crop, mask)
    if mask is None or colour is None:
        return reject("The ink could not be separated from the picture.")
    # A logo in the same box is not a letter. A letter touching it stays.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask, halo = split_solid_blobs(mask, gray)
    mask, colour = refine_ink(crop, mask)
    if mask is None or colour is None:
        return reject("The ink could not be separated from the picture.")
    # Only this line's ink. A neighbour's ascender in the expanded crop stays put.
    owned = _keep_line_glyphs(mask, left, top, owner, plate_cores)
    if owned is None:
        return reject("The ink sat outside this box, so it stayed in the picture.")
    refined_mask, refined_colour = refine_ink(crop, owned)
    if refined_mask is not None and refined_colour is not None:
        mask, colour = refined_mask, refined_colour
    else:
        mask = owned
    # A word-wide swash stays in the picture. Tracing it, or painting it
    # out, leaves bars, and the source-ink fringe would hide those bars
    # from the page guard.
    mask = _strip_flourish(mask)
    return {"pending": {
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
        "style": style,
    }}


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

    global _spawn_count
    _spawn_count = 0
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
    jobs = []
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
        jobs.append({
            "plate": plate,
            "text": text,
            "owner": owner,
            "cores": plate_cores,
            "bounds": (left, top, right, bottom),
            "core": (
                inner_left,
                inner_top,
                max(1, inner_right - inner_left),
                max(1, inner_bottom - inner_top),
            ),
            "inner": (inner_left, inner_top, inner_right, inner_bottom),
        })
    for separated in _run_parallel(_separate_line, jobs):
        if separated.get("pending") is not None:
            pending.append(separated["pending"])
            continue
        if separated.get("line") is not None:
            raster_lines.append(separated["line"])
        if separated.get("box") is not None:
            raster_boxes.append(separated["box"])
    harmonise_pending(pending)
    traced = _trace_many([item["mask"] for item in pending])
    for item, paths in zip(pending, traced):
        text = item["text"]
        mask = item["mask"]
        left, top, right, bottom = item["left"], item["top"], item["right"], item["bottom"]
        core = item.get("core")
        if not paths:
            raster_lines.append(_line(text, "raster", "The trace was empty, so this box stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph", item.get("style")))
            continue
        score, accepted, painted = _accept_trace(mask, paths)
        if not accepted:
            raster_lines.append(_line(text, "raster", f"The trace did not match the ink ({score:.2f}), so this box stayed in the picture."))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph", item.get("style")))
            continue
        check_crop = _hide_foreign_ink(item["crop"], mask)
        reason = shape_gate(check_crop, mask, painted, item["inner"])
        if reason:
            raster_lines.append(_line(text, "raster", reason))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph", item.get("style")))
            continue
        if _topology_fails(check_crop, painted, plate_ppi):
            if os.environ.get("TOPO_DEBUG"):
                sys.stderr.write(f"[topo] line {text!r}\n")
            raster_lines.append(_line(
                text, "raster",
                "A letter lost its tail or its counter, so this line stayed in the picture.",
            ))
            raster_boxes.append(_raster_box(text, left, top, right, bottom, core, "paragraph", item.get("style")))
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
            "style": item.get("style"),
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

    gate_started = time.perf_counter()
    text_gate, drawn, _ignored, source_guard = _apply_text_gate(
        blocks, drawn, raster_lines, raster_boxes, plate, pristine, output_pdf,
        trim_w, trim_h, bleed_mm, placed, {}, bgr.shape, source_guard,
    )
    gate_s = time.perf_counter() - gate_started
    retype_started = time.perf_counter()
    retyped = []
    try:
        from vector_retype import retype_rejected

        retyped = retype_rejected(
            plate, pristine, blocks, raster_lines, raster_boxes, drawn, placed,
            bgr.shape, text_gate,
        )
    except Exception as exc:
        sys.stderr.write(f"[vector-retype] skipped ({str(exc)[:160]})\n")
        retyped = []
    retype_s = time.perf_counter() - retype_started
    plate, sharpen_note = _upgrade_raster(plate, drawn, placed, bgr)
    qa = _write_pdf(plate, drawn, output_pdf, trim_w, trim_h, bleed_mm, placed, retyped)
    if not qa.get("wrote") or not qa.get("cmyk") or not qa.get("boxes") or not qa.get("ppi") or (retyped and not qa.get("fonts")):
        from vector_text_v2 import _discard, _fail
        _discard(output_pdf)
        reason = "The traced press file failed the check, so the original lettering was kept."
        if not qa.get("cmyk"):
            reason = "The press file was not CMYK, so the original lettering was kept."
        elif not qa.get("boxes"):
            reason = "The trim box was wrong, so the original lettering was kept."
        elif not qa.get("ppi"):
            reason = "The picture was under 400 PPI, so the original lettering was kept."
        elif retyped and not qa.get("fonts"):
            reason = "The retyped fonts were not embedded, so the original lettering was kept."
        return _fail(started, reason, provider=provider, qa=qa)

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
    for item in retyped:
        core = item.get("core") or (0, 0, 0, 0)
        vector_lines.append({
            "text": item["text"],
            "mode": "vector",
            "font": item.get("font") or "",
            "reason": "",
            "match": item.get("overlap"),
            "pt": _points(int(core[3]), placed.get("ppi") or MIN_PPI),
            "retyped": True,
            "plateRect": [int(v) for v in core[:4]],
        })
    still_raster = [row for row in raster_lines if row.get("mode") != "vector"]
    amber = bool(still_raster)
    reason = ""
    if amber:
        reason = "Some lettering stayed in the picture because its trace did not match. Glance at it before printing."
    decisions = [
        f"The lettering was traced as vector shapes ({len(drawn)} boxes).",
        "The original raster letter under each trace was painted out, so the vector edge is what prints.",
        f"The picture was enlarged with {provider}.",
        "The press file is CMYK at 400 PPI or more, with the trim 5 mm inside the bleed.",
    ]
    if retyped:
        decisions.append(
            f"{len(retyped)} lines the trace left as a picture were set as vector type in a matching font."
        )
    if sharpen_note:
        decisions.append(sharpen_note)
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
        "retype_s": round(retype_s, 3),
        "total_s": round(time.perf_counter() - started, 3),
        "spawns": int(_spawn_count),
    }
    timing_line = (
        f"Timing: OCR {timings['ocr_s']:.2f}s, upscale {timings['enlarge_s']:.2f}s, "
        f"trace {timings['trace_s']:.2f}s, colour {timings['colour_s']:.2f}s, "
        f"PDF {timings['compose_s']:.2f}s, gate {timings['gate_s']:.2f}s, "
        f"retype {timings['retype_s']:.2f}s."
    )
    decisions.append(timing_line)
    sys.stderr.write("[vector-trace] " + timing_line + "\n")
    sys.stderr.write("[vector-trace] text gate\n")
    for row in text_gate:
        flag = "OK" if row.get("ok") else "FAIL"
        sys.stderr.write(
            f"  {flag} {row.get('mode')} ssim={float(row.get('ssim') or 0):.3f} "
            f"white={bool(row.get('whiteBlock'))} clip={bool(row.get('clipped'))} "
            f"glyph={bool(row.get('glyphFail'))} iou={row.get('glyphIou')} mismatch={bool(row.get('mismatch'))} "
            f"{row.get('text')!r} -> {row.get('render')!r}\n"
        )
    edge = _content_edge(drawn, raster_boxes, plate.shape, float((placed or {}).get("ppi") or MIN_PPI), bleed_mm)
    return {
        "ok": True,
        "amber": amber,
        "reason": reason,
        "decisions": decisions,
        "elapsed_s": timings["total_s"],
        "timings": timings,
        "lines": vector_lines + still_raster,
        "qa": qa,
        "provider": provider,
        "pdf": output_pdf,
        "vector_lines": len(vector_lines),
        "raster_lines": len(still_raster),
        "mode": "trace",
        "text_gate": text_gate,
        "source_guard": bool(source_guard),
        "edge": edge,
        "placement": {
            "artBox": [int(v) for v in (placed.get("art_box") or (0, 0, plate.shape[1], plate.shape[0]))],
            "ppi": int(placed.get("ppi") or MIN_PPI),
            "plate": [int(plate.shape[0]), int(plate.shape[1])],
        },
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
    # A light letter on a light or mixed ground is the solid core. Midtones
    # belong to a logo or a blur and would glue that logo onto the last glyph.
    # Gray type on a dark ground is the stroke itself: cutting it at 220
    # deletes the bar and hides a drip, so the gate never sees the damage.
    if int(ink.sum()) > 20 and float(np.median(gray[ink])) >= 160.0:
        border_tone = float(np.median(gray[border])) if int(border.sum()) else 128.0
        solid = ink & (gray >= 220)
        if border_tone < 80.0 and int(solid.sum()) < 0.45 * int(ink.sum()):
            level = border_tone + 0.50 * (float(np.median(gray[ink])) - border_tone)
            ink = ink & (gray >= level)
        else:
            ink = solid
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


def _otsu_ink(image: np.ndarray):
    """Ink, distance from the border paper, and how far that ink sits from the paper.

    Light type and dark type use the same cut. None when the crop is flat.
    """
    if image is None or getattr(image, "ndim", 0) != 3 or image.shape[0] < 8 or image.shape[1] < 8:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    border = np.zeros(gray.shape, np.bool_)
    border[:2, :] = True
    border[-2:, :] = True
    border[:, :2] = True
    border[:, -2:] = True
    if int(border.sum()) < 8:
        return None
    paper = float(np.median(gray[border]))
    distance = np.abs(gray.astype(np.float32) - paper)
    sample = np.clip(distance, 0, 255).astype(np.uint8)
    if float(sample.std()) < 4.0:
        return None
    _level, binary = cv2.threshold(sample, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary > 0
    if float(ink[border].mean()) > 0.5:
        ink = ~ink
    if int(ink.sum()) < 20:
        return None
    core = float(np.median(distance[ink]))
    if core < 12.0:
        return None
    return ink, distance, core


def _gray_pair(source_bgr: np.ndarray, render_bgr: np.ndarray):
    """Both crops at the render's size, as gray. None when either side is empty."""
    if source_bgr is None or render_bgr is None or getattr(source_bgr, "size", 0) == 0:
        return None
    if getattr(render_bgr, "ndim", 0) != 3 or render_bgr.shape[0] < 8 or render_bgr.shape[1] < 8:
        return None
    if render_bgr.shape[:2] != source_bgr.shape[:2]:
        source_bgr = cv2.resize(
            source_bgr,
            (int(render_bgr.shape[1]), int(render_bgr.shape[0])),
            interpolation=cv2.INTER_AREA,
        )
    source = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    render = cv2.cvtColor(render_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return source, render


def _paper_ink(gray: np.ndarray):
    """Ink against the border paper. None when the crop is flat."""
    border = np.concatenate([
        gray[:2, :].ravel(), gray[-2:, :].ravel(),
        gray[:, :2].ravel(), gray[:, -2:].ravel(),
    ])
    if border.size < 8:
        return None
    paper = float(np.median(border))
    distance = np.abs(gray - paper)
    if float(distance.std()) < 4.0:
        return None
    _level, binary = cv2.threshold(
        np.clip(distance, 0, 255).astype(np.uint8),
        0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    ink = binary > 0
    edge = np.zeros(gray.shape, np.bool_)
    edge[:2, :] = True
    edge[-2:, :] = True
    edge[:, :2] = True
    edge[:, -2:] = True
    if float(ink[edge].mean()) > 0.5:
        ink = ~ink
    if int(ink.sum()) < 20:
        return None
    return ink, paper, distance


def _paint_halo_fails(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """True when the paint-out beside a stroke is a flat patch on a varied picture.

    White type on a photo (the T of HARVEST) leaves a grey shoulder where the
    picture's soft edge was. Navy and cream paper are flat on both sides, so
    a hard vector edge there is not this fault.
    """
    pair = _gray_pair(source_bgr, render_bgr)
    if pair is None:
        return False
    source, render = pair
    found = _paper_ink(render)
    if found is None:
        return False
    _ink, paper, dist_r = found
    dist_s = np.abs(source - paper)
    level = max(40.0, 0.55 * float(np.percentile(dist_r, 98)))
    ink = dist_r > level
    if int(ink.sum()) < 20:
        return False
    outside = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 3)
    ring = (outside >= 1.0) & (outside <= 3.0)
    delta = np.abs(source - render)
    # The picture still holds the letter. The render was pulled back to paper.
    shoulder = ring & (dist_s > 36.0) & (dist_r < 22.0) & (delta > 24.0)
    count = int(np.count_nonzero(shoulder))
    # A one-pixel rim on flat navy is the vector's hard edge. The mark on
    # HARVEST is a wide patch, and the picture behind it still changes.
    if count < 80:
        return False
    paper_pix = np.where(outside >= 4.0, source, float(np.mean(source)))
    mean = cv2.blur(paper_pix, (15, 15))
    mean2 = cv2.blur(paper_pix * paper_pix, (15, 15))
    spread = np.sqrt(np.clip(mean2 - mean * mean, 0, None))
    return float(np.median(spread[shoulder])) >= 12.0


def _ghost_double_fails(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """True when a second copy of the stroke sits off to the side of the picture.

    A one-pixel harder edge is not a second letter. A copy three pixels away,
    repeated along the line, is the blotchy double on the small card type.
    """
    pair = _gray_pair(source_bgr, render_bgr)
    if pair is None:
        return False
    source, render = pair
    source_ink = _paper_ink(source)
    render_ink = _paper_ink(render)
    if source_ink is None or render_ink is None:
        return False
    source_ink = source_ink[0]
    render_ink = render_ink[0]
    outside = cv2.distanceTransform((~source_ink).astype(np.uint8), cv2.DIST_L2, 3)
    stray = (render_ink & (outside >= 3.0)).astype(np.uint8)
    if int(stray.sum()) < 16:
        return False
    parts = _components(source_ink.astype(np.uint8) * 255, 8)
    if len(parts) < 2:
        return False
    letter_h = float(np.median([part["h"] for part in parts]))
    if letter_h < 4.0:
        return False
    count, _labels, stats, _centres = cv2.connectedComponentsWithStats(stray, 8)
    ghosts = 0
    for index in range(1, count):
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 6 or height < 0.45 * letter_h:
            continue
        if width <= 4 or width < 0.35 * max(height, 1):
            ghosts += 1
    return ghosts >= 2


def _double_edge_fails(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """True when dark type has a second edge just outside the picture's stroke.

    On the small card lines the copy sits closer than the ghost test looks,
    so the picture and the vector both show. A one-pixel harder edge stays
    inside the ring and does not count. Light type on a dark ground is a
    different fault and is left to the halo check.
    """
    pair = _gray_pair(source_bgr, render_bgr)
    if pair is None:
        return False
    source, render = pair
    border = np.concatenate([
        source[:2, :].ravel(), source[-2:, :].ravel(),
        source[:, :2].ravel(), source[:, -2:].ravel(),
    ])
    if border.size < 8:
        return False
    paper = float(np.median(border))
    # Cream card stock. Navy and photo grounds are not this defect.
    if paper < 160.0:
        return False
    ink = source < (paper - 28.0)
    ink_n = int(np.count_nonzero(ink))
    if ink_n < 80:
        return False
    parts = _components(ink.astype(np.uint8) * 255, 8)
    heights = [part["h"] for part in parts if part["area"] >= 8 and part["h"] >= 4]
    # Headings are taller than this. The doubled copy is the small body type.
    if len(heights) < 2 or float(np.median(heights)) >= 11.0:
        return False
    outside = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 3)
    ring = (outside >= 1.2) & (outside < 2.6)
    # The vector is darker than the picture in that ring: a second stroke.
    count = int(np.count_nonzero(ring & ((source - render) > 16.0)))
    return count >= 40 and count >= 0.10 * ink_n


def _short_faint_fails(source_bgr: np.ndarray, ssim: float) -> bool:
    """True when a short line on cream paper only barely matches.

    Letters under 12px are not scored glyph by glyph, because one pixel is
    a fifth of the stroke. The footer words are that short and the trace is
    visibly thinner, so a modest score sends the whole line back to the picture.
    """
    if ssim >= 0.92:
        return False
    pair = _gray_pair(source_bgr, source_bgr)
    if pair is None:
        return False
    source = pair[0]
    border = np.concatenate([
        source[:2, :].ravel(), source[-2:, :].ravel(),
        source[:, :2].ravel(), source[:, -2:].ravel(),
    ])
    if border.size < 8:
        return False
    paper = float(np.median(border))
    if paper < 160.0:
        return False
    ink = source < (paper - 28.0)
    parts = _components(ink.astype(np.uint8) * 255, 8)
    heights = [part["h"] for part in parts if part["area"] >= 8 and part["h"] >= 4]
    if len(heights) < 2:
        return False
    median_h = float(np.median(heights))
    return 4.0 <= median_h < 12.0


def _letter_height(source_bgr: np.ndarray) -> float:
    """Median height of the letter-sized ink, in source pixels. 0 when there is none."""
    pair = _gray_pair(source_bgr, source_bgr)
    if pair is None:
        return 0.0
    found = _paper_ink(pair[0])
    if found is None:
        return 0.0
    ink = found[0]
    parts = _components(ink.astype(np.uint8) * 255, 8)
    heights = [part["h"] for part in parts if part["area"] >= 8 and part["h"] >= 4]
    if len(heights) < 2:
        return 0.0
    return float(np.median(heights))


def _upscaled_ink(image_bgr: np.ndarray, scale: int, nearest: bool):
    """Ink on a larger copy. Nearest keeps a hard trace from growing a bar it never drew."""
    if image_bgr is None or image_bgr.ndim != 3:
        return None
    interpolation = cv2.INTER_NEAREST if nearest else cv2.INTER_CUBIC
    big = cv2.resize(
        image_bgr,
        (max(1, image_bgr.shape[1] * scale), max(1, image_bgr.shape[0] * scale)),
        interpolation=interpolation,
    )
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY).astype(np.float32)
    border = np.concatenate([
        gray[:2, :].ravel(), gray[-2:, :].ravel(),
        gray[:, :2].ravel(), gray[:, -2:].ravel(),
    ])
    if border.size < 8:
        return None
    paper = float(np.median(border))
    # The stroke is a small share of a wide line, so a page percentile is the paper.
    if paper >= 150.0:
        dark = gray[gray <= paper - 16.0]
        if dark.size < 12:
            return None
        tone = float(np.percentile(dark, 30))
        span = paper - tone
        if span < 18.0:
            return None
        ink = gray < (paper - 0.42 * span)
    else:
        light = gray[gray >= paper + 16.0]
        if light.size < 12:
            return None
        tone = float(np.percentile(light, 70))
        span = tone - paper
        if span < 18.0:
            return None
        ink = gray > (paper + 0.42 * span)
    return ink


def _enclosed_holes(component: np.ndarray, min_hole: int) -> int:
    """Counters fully inside the glyph. A speck smaller than ``min_hole`` is not one."""
    ys, xs = np.where(component)
    if ys.size < 12:
        return 0
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = np.zeros((y1 - y0 + 2, x1 - x0 + 2), np.uint8)
    crop[1:-1, 1:-1][component[y0:y1, x0:x1]] = 255
    inv = cv2.bitwise_not(crop)
    filled = inv.copy()
    flood = np.zeros((filled.shape[0] + 2, filled.shape[1] + 2), np.uint8)
    cv2.floodFill(filled, flood, (0, 0), 128)
    holes = (filled == 255).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(holes, 8)
    return sum(1 for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= min_hole)


def _small_glyph_fails(source_bgr: np.ndarray, render_bgr: np.ndarray) -> bool:
    """True when a short letter lost a counter, a crossbar, or a stroke.

    Letters under 16px are not scored by the 1× overlap check: one pixel is a
    fifth of the stroke, so that check skips them. The picture is enlarged so
    a thin bar still closes its counter, and the trace is enlarged without
    smoothing so a missing bar stays missing. One such letter sends the line
    back to the picture.
    """
    height = _letter_height(source_bgr)
    # Taller type is already scored glyph by glyph. A speck is not a letter.
    if height < 4.0 or height >= 16.0:
        return False
    if render_bgr is None or render_bgr.shape[:2] != source_bgr.shape[:2]:
        source_bgr = _match_scale(source_bgr, render_bgr)
    scale = 4
    source_ink = _upscaled_ink(source_bgr, scale, nearest=False)
    render_ink = _upscaled_ink(render_bgr, scale, nearest=True)
    if source_ink is None or render_ink is None:
        return False
    if source_ink.shape != render_ink.shape:
        return False
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(source_ink.astype(np.uint8), 8)
    parts = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 24:
            continue
        parts.append((
            area,
            int(stats[index, cv2.CC_STAT_LEFT]),
            int(stats[index, cv2.CC_STAT_TOP]),
            int(stats[index, cv2.CC_STAT_WIDTH]),
            int(stats[index, cv2.CC_STAT_HEIGHT]),
            index,
        ))
    if len(parts) < 2:
        return False
    median_area = float(np.median([part[0] for part in parts]))
    median_h = float(np.median([part[4] for part in parts]))
    floor = max(24, int(round(0.18 * median_area)))
    for area, x, y, width, glyph_h, index in parts:
        if area < floor or glyph_h < 0.62 * median_h or glyph_h < 10:
            continue
        y0 = max(0, y - 2)
        x0 = max(0, x - 2)
        y1 = min(source_ink.shape[0], y + glyph_h + 2)
        x1 = min(source_ink.shape[1], x + width + 2)
        window = labels[y0:y1, x0:x1] == index
        # A one-pixel shift still belongs to this letter. The neighbour does not.
        zone = cv2.dilate(window.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        drawn = render_ink[y0:y1, x0:x1] & zone
        # A broken arm can sit a couple of pixels off the stroke and still inside
        # the letter's own box. The next letter is outside that box.
        drawn_box = render_ink[y0:y1, x0:x1]
        min_hole = max(16, int(round(0.025 * area)))
        source_holes = _enclosed_holes(window, min_hole)
        render_holes = _enclosed_holes(drawn, max(8, min_hole // 2))
        if source_holes > 0 and render_holes < source_holes:
            if os.environ.get("SMALL_GLYPH_DEBUG"):
                sys.stderr.write(f"[small] holes {source_holes}->{render_holes} h={glyph_h}\n")
            return True
        # A stroke that breaks into a second piece. A one-pixel nick is not a gap.
        spec = max(16, int(round(0.12 * area)))
        pieces, piece_labels, piece_stats, _piece_cent = cv2.connectedComponentsWithStats(
            drawn_box.astype(np.uint8), 8,
        )
        substantial = []
        for piece in range(1, pieces):
            if int(piece_stats[piece, cv2.CC_STAT_AREA]) >= spec:
                substantial.append(piece_labels == piece)
        source_pieces, _source_labels, source_stats, _source_cent = cv2.connectedComponentsWithStats(
            window.astype(np.uint8), 8,
        )
        source_n = sum(1 for piece in range(1, source_pieces) if int(source_stats[piece, cv2.CC_STAT_AREA]) >= spec)
        if len(substantial) > source_n and len(substantial) >= 2:
            substantial.sort(key=lambda mask: int(mask.sum()), reverse=True)
            gap = cv2.distanceTransform((~substantial[0]).astype(np.uint8), cv2.DIST_L2, 3)
            gap_px = float(gap[substantial[1]].min()) if int(substantial[1].sum()) else 0.0
            # Four pixels at this scale is one pixel on the page.
            # A neighbour that only clips the empty corner of the box does not
            # overlap this letter. A broken stroke is still on the letter, is a
            # large piece of it, or has moved a full two pixels away.
            piece = int(substantial[1].sum())
            if gap_px >= 4.0 and piece >= spec:
                overlap = int(np.count_nonzero(substantial[1] & window))
                on_letter = overlap >= 0.35 * piece
                large = piece >= 0.25 * area
                clear = gap_px >= 8.0
                if on_letter or large or clear:
                    if os.environ.get("SMALL_GLYPH_DEBUG"):
                        sys.stderr.write(f"[small] split gap {gap_px:.1f} h={glyph_h}\n")
                    return True
    return False


def _glyph_tiles(source_bgr: np.ndarray, render_bgr: np.ndarray) -> list:
    """Enlarged source/trace pairs for each short letter, left to right.

    Empty when the line is not the small type this re-read is for.
    """
    height = _letter_height(source_bgr)
    if height < 4.0 or height >= 16.0:
        return []
    if render_bgr.shape[:2] != source_bgr.shape[:2]:
        source_bgr = _match_scale(source_bgr, render_bgr)
    source_ink = _upscaled_ink(source_bgr, 1, nearest=True)
    render_ink = _upscaled_ink(render_bgr, 1, nearest=True)
    if source_ink is None or render_ink is None or source_ink.shape != render_ink.shape:
        return []
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(source_ink.astype(np.uint8), 8)
    parts = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        glyph_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        if area < 8 or glyph_h < max(4.0, 0.55 * height):
            continue
        parts.append((
            int(stats[index, cv2.CC_STAT_LEFT]),
            int(stats[index, cv2.CC_STAT_TOP]),
            int(stats[index, cv2.CC_STAT_WIDTH]),
            glyph_h,
            index,
        ))
    if len(parts) < 2:
        return []
    tiles = []
    for x, y, width, glyph_h, index in parts:
        y0 = max(0, y - 1)
        x0 = max(0, x - 1)
        y1 = min(source_ink.shape[0], y + glyph_h + 1)
        x1 = min(source_ink.shape[1], x + width + 1)
        window = labels[y0:y1, x0:x1] == index
        zone = cv2.dilate(window.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        drawn = render_ink[y0:y1, x0:x1] & zone

        def tile(mask: np.ndarray) -> np.ndarray:
            canvas = np.full(mask.shape, 255, np.uint8)
            canvas[mask] = 20
            target_h = 48
            target_w = max(8, int(round(canvas.shape[1] * target_h / float(max(1, canvas.shape[0])))))
            big = cv2.resize(canvas, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
            return cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)

        tiles.append((tile(window), tile(drawn)))
    return tiles


def _glyph_letter(text: str) -> str:
    """One letter, case kept. Punctuation and a blank read are not a letter."""
    cleaned = re.sub(r"[^0-9A-Za-zÀ-ÿıİ]", "", str(text or ""))
    if len(cleaned) != 1:
        return ""
    return cleaned


def _letters_confusable(left: str, right: str) -> bool:
    """True when a tiny glyph reader swaps shapes it cannot tell apart.

    An e read as a c is a different letter. An I read as a 1 is the same stroke.
    """
    groups = ("Il1|", "O0oQ", "S5s", "B8", "G6", "Z2z")
    pair = {left, right}
    return any(pair <= set(group) for group in groups)


def _small_reads_differ(readings: list) -> list:
    """Indexes whose trace letter is not the picture's letter.

    ``readings`` is (source text, source score, trace text, trace score) per glyph.
    A pair the reader cannot see does not fail the line. Two confident, different
    letters do. A case flip, or an I read as a 1, is the reader, not a new letter.
    """
    bad = []
    for index, item in enumerate(readings):
        source_text, source_score, render_text, render_score = item
        source_letter = _glyph_letter(source_text)
        render_letter = _glyph_letter(render_text)
        if source_score < 0.70 or render_score < 0.70:
            continue
        if not source_letter or not render_letter:
            continue
        if source_letter.lower() == render_letter.lower():
            continue
        if _letters_confusable(source_letter, render_letter):
            continue
        bad.append(index)
    return bad


def _recognize_glyphs(images: list) -> list:
    """Read each glyph. One batch, no second detection pass over the page."""
    if not images:
        return []
    from ocr_reader import _load

    engine, _name = _load()
    recognizer = engine.text_rec
    saved = recognizer.rec_batch_num
    recognizer.rec_batch_num = max(int(saved or 1), 32)
    try:
        result = engine.recognize_txt(images)
    finally:
        recognizer.rec_batch_num = saved
    texts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    found = []
    for index in range(len(images)):
        text = str(texts[index] if index < len(texts) else "")
        score = float(scores[index] if index < len(scores) else 0.0)
        found.append((text, score))
    return found


def _min_glyph_iou(source_bgr: np.ndarray, render_bgr: np.ndarray) -> float:
    """Lowest IoU of one source glyph against the render in that same place.

    A one-pixel outline is the vector's hard edge against the picture's soft
    edge, so it does not count. Paint that fills a paper gap inside the
    letter, or a bar that stands off the stroke, does. 1.0 means the crop
    has no letter-sized glyphs to judge.
    """
    if source_bgr is None or render_bgr is None or getattr(source_bgr, "size", 0) == 0:
        return 1.0
    if render_bgr.shape[:2] != source_bgr.shape[:2]:
        source_bgr = cv2.resize(
            source_bgr,
            (int(render_bgr.shape[1]), int(render_bgr.shape[0])),
            interpolation=cv2.INTER_AREA,
        )
    packed = _otsu_ink(source_bgr)
    render_packed = _otsu_ink(render_bgr)
    if packed is None or render_packed is None:
        return 1.0
    source_ink, distance, core = packed
    render_ink = render_packed[0]
    source_parts = _components(source_ink.astype(np.uint8) * 255, 16)
    render_parts = _components(render_ink.astype(np.uint8) * 255, 16)
    if len(source_parts) < 2 or len(render_parts) < 2:
        return 1.0
    heights = [part["h"] for part in source_parts]
    areas = [part["area"] for part in source_parts]
    median_h = float(np.median(heights))
    median_area = float(np.median(areas))
    if median_h < 8.0:
        return 1.0
    floor = max(16, int(round(0.12 * median_area)))
    letters = [
        part for part in source_parts
        if part["area"] >= floor and part["h"] >= 0.55 * median_h
    ]
    if len(letters) < 2:
        return 1.0
    # A short bar beside a digit is not a letter. The letters are the tall set.
    tallest = max(part["h"] for part in letters)
    tall = [part for part in letters if part["h"] >= 0.70 * tallest]
    # Kept so a heavy rim on a slightly shorter letter (the E of APOSTLE) is
    # still judged after the tall filter drops it.
    rim_letters = letters
    if len(tall) >= 2:
        letters = tall
    # On a letter shorter than 12px, one pixel is a fifth of the stroke.
    # That edge is not a different glyph. The school N is well above this.
    if float(np.median([part["h"] for part in letters])) < 12.0:
        return 1.0
    radius = cv2.distanceTransform(source_ink.astype(np.uint8), cv2.DIST_L2, 3)
    stroke = float(np.median(radius[source_ink])) if int(source_ink.sum()) else 1.0
    ksize = int(round(1.6 * stroke)) * 2 + 1
    # Wider than the stroke bridges a counter (an O, an A) and calls the
    # paper a filled gap. Seven pixels still closes the channel through an N.
    ksize = max(3, min(7, ksize))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    closed = cv2.morphologyEx(source_ink.astype(np.uint8), cv2.MORPH_CLOSE, kernel) > 0
    # A gap the close bridges counts only where the picture is still paper.
    interior = closed & ~source_ink & (distance <= 0.40 * core)
    outside = cv2.distanceTransform((~source_ink).astype(np.uint8), cv2.DIST_L2, 3)
    near_render = cv2.distanceTransform((~render_ink).astype(np.uint8), cv2.DIST_L2, 3)
    height, width = source_ink.shape[:2]
    worst = 1.0
    for part in letters:
        x0 = max(0, part["x"] - ksize)
        y0 = max(0, part["y"] - ksize)
        x1 = min(width, part["x"] + part["w"] + ksize)
        y1 = min(height, part["y"] + part["h"] + ksize)
        source_box = np.zeros((y1 - y0, x1 - x0), np.bool_)
        source_box[part["pixels"][y0:y1, x0:x1]] = True
        render_box = render_ink[y0:y1, x0:x1]
        extra = render_box & ~source_box
        # The hard edge may sit one pixel outside the soft picture. A filled
        # counter is inside the letter, so it still counts.
        ignore = extra & ~interior[y0:y1, x0:x1] & (outside[y0:y1, x0:x1] < 1.5)
        counted = extra & ~ignore
        # A bar beside the letter (the final E of APOSTLE) stands clear of the
        # stroke. The one-pixel hard edge, and a soft photo edge, stay put.
        side_bar = False
        far = extra & (outside[y0:y1, x0:x1] >= 2.5)
        if int(np.count_nonzero(far)) >= 10:
            _n, _labels, stats, _cent = cv2.connectedComponentsWithStats(far.astype(np.uint8), 8)
            for index in range(1, _n):
                area_b = int(stats[index, cv2.CC_STAT_AREA])
                height_b = int(stats[index, cv2.CC_STAT_HEIGHT])
                width_b = int(stats[index, cv2.CC_STAT_WIDTH])
                thin = width_b <= 4 or width_b < 0.35 * max(height_b, 1)
                if area_b >= 10 and height_b >= 0.50 * part["h"] and thin:
                    side_bar = True
                    break
        # A one-pixel nick inside a counter is the soft edge. A channel
        # through an N is one piece, so it still counts.
        inside = (counted & interior[y0:y1, x0:x1]).astype(np.uint8)
        pieces, labels, stats, _centres = cv2.connectedComponentsWithStats(inside, 8)
        if pieces > 1:
            specks = np.zeros(inside.shape, np.bool_)
            for index in range(1, pieces):
                if int(stats[index, cv2.CC_STAT_AREA]) < 12:
                    specks[labels == index] = True
            counted = counted & ~specks
        missing = source_box & ~render_box
        near = near_render[y0:y1, x0:x1]
        # A one-pixel inset is the same hard edge. A gap that continues past
        # that pixel is a missing stroke, so the fringe of the gap still counts.
        far = missing & (near >= 1.5)
        attached = cv2.dilate(far.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        ignore_missing = missing & (near < 1.5) & ~attached
        kept = source_box & ~ignore_missing
        inter = int(np.count_nonzero(kept & render_box))
        union = int(np.count_nonzero(kept | counted))
        score = 0.0 if union == 0 else inter / float(union)
        if side_bar:
            score = min(score, 0.40)
        if score < worst:
            worst = score
    # A rim as heavy as the stroke itself is a second edge, not antialiasing.
    # Friday's hard edge is a small fraction of the letter. The E of APOSTLE is not.
    for part in rim_letters:
        if part["h"] < 0.55 * median_h:
            continue
        x0 = max(0, part["x"] - 3)
        y0 = max(0, part["y"] - 2)
        x1 = min(width, part["x"] + part["w"] + 4)
        y1 = min(height, part["y"] + part["h"] + 2)
        rim = (
            render_ink[y0:y1, x0:x1]
            & ~source_ink[y0:y1, x0:x1]
            & (outside[y0:y1, x0:x1] < 2.2)
        )
        rim_n = int(np.count_nonzero(rim))
        # 0.64 keeps a small card letter (the email, about 0.63) and still
        # rejects the heavy rim on the E of APOSTLE (about 0.66).
        if rim_n > 0.64 * part["area"]:
            worst = min(worst, 0.40)
    letter_h = float(np.median([part["h"] for part in letters]))
    for part in render_parts:
        if part["area"] < floor or part["h"] < 0.70 * letter_h:
            continue
        overlap = int(np.count_nonzero(part["pixels"] & source_ink))
        if overlap >= 0.35 * part["area"]:
            continue
        worst = min(worst, overlap / float(part["area"]))
    return worst


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
    rows = read_blocks(canvas, extra=False, fast=True)
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
    rows = read_blocks(strip, extra=False, fast=True)
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
    # A section that is already mostly the picture is not worth a second read.
    early_style = []
    for index, row in enumerate(report):
        live = row["_vector"] and not row["_revert"]
        early_style.append({
            "rect": row["_core"],
            "raster": not live,
            "colour": _sample_style(row.get("_source")),
            "indexes": [index] if live else [],
        })
    for index in _column_style_demote(early_style):
        report[index]["_revert"] = True
    vector_slots = [row for row in vector_slots if not row["_revert"]]

    def _judge_row(row: dict) -> dict:
        """Pixel checks only. The second OCR is reserved for rows these reject."""
        text = row["render"] or row["text"]
        score = _score_modest(row["_source"], row["_render"], row["text"], text)
        judged = {
            "ssim": score["ssim"],
            "whiteBlock": score["whiteBlock"],
            "clipped": score["clipped"],
            "pixelFail": bool(score["pixelFail"]),
            "ok": bool(score["ok"]),
        }
        if not row["_vector"] or row.get("_revert"):
            judged["glyphIou"] = None
            judged["haloFail"] = False
            return judged
        glyph_iou = _min_glyph_iou(row["_source"], row["_render"])
        halo = _paint_halo_fails(row["_source"], row["_render"])
        ghost = _ghost_double_fails(row["_source"], row["_render"])
        # A second edge, or a short line the glyph check does not judge.
        doubled = _double_edge_fails(row["_source"], row["_render"])
        faint = _short_faint_fails(row["_source"], float(score["ssim"]))
        # Short letters skip the 1× overlap. A lost counter or a broken stroke
        # still sends the line back. That decision does not need a second read.
        stroke = _small_glyph_fails(row["_source"], row["_render"])
        judged["glyphIou"] = round(float(glyph_iou), 3)
        judged["haloFail"] = bool(halo or ghost or doubled)
        letter_h = _letter_height(row["_source"])
        judged["strokeFail"] = bool(stroke)
        judged["smallText"] = 4.0 <= letter_h < 16.0
        # Body type only. A heading the stroke check kept is already large
        # enough that a per-letter read just disagrees with itself.
        judged["glyphOcr"] = 4.0 <= letter_h < 10.0
        judged["glyphFail"] = bool(
            glyph_iou < 0.85 or halo or ghost or doubled or faint or stroke
        )
        # A doubled small line goes back to the picture on its own. It does not
        # pull the rest of the paragraph, or one blotchy line blanks the column.
        # A broken letter does pull its paragraph: the heading and the line
        # under it have to be the same weight.
        judged["lineOnly"] = bool(
            doubled and glyph_iou >= 0.85 and not halo and not ghost and not faint and not stroke
        )
        return judged

    judged_rows = _run_parallel(_judge_row, report)
    for row, judged in zip(report, judged_rows):
        row["ssim"] = judged["ssim"]
        row["whiteBlock"] = judged["whiteBlock"]
        row["clipped"] = judged["clipped"]
        row["_pixelFail"] = judged["pixelFail"]
        row["glyphIou"] = judged["glyphIou"]
        if judged.get("haloFail"):
            row["haloFail"] = True
        if not row["_vector"]:
            row["ok"] = bool(judged["ok"])
            row["render"] = _norm_text(row["text"])
            continue
        if judged.get("glyphFail"):
            row["glyphFail"] = True
        if judged.get("strokeFail"):
            row["_strokeFail"] = True
        if judged.get("smallText"):
            row["_smallText"] = True
        if judged.get("glyphOcr"):
            row["_glyphOcr"] = True
        if judged.get("lineOnly"):
            row["_lineOnly"] = True
    # A path that draws ink the picture does not have inside the next line
    # goes back. The victim line is left as it was. This does not need a read.
    _flag_neighbour_ink(report, by_rect, base)
    # A line the mask already accepted does not need a second read. Copying
    # the source text keeps the letter check honest. Only a rejected line is
    # read back, so the gate can say what the vector actually showed.
    accepted = []
    rejected = []
    # A line sent back only because of a second edge does not need a second
    # read. The picture is what prints. Reading it again is most of the gate.
    quiet = []
    for row in vector_slots:
        low_ssim = float(row["ssim"]) < SSIM_FLOOR
        if row.get("_lineOnly") or row.get("_strokeFail") or row.get("_invadeFail"):
            quiet.append(row)
        elif row["glyphFail"] or row["_pixelFail"] or low_ssim:
            rejected.append(row)
        else:
            accepted.append(row)
    for row in accepted:
        row["render"] = _norm_text(row["text"])
        row["mismatch"] = False
    for row in quiet:
        row["render"] = _norm_text(row["text"])
        row["mismatch"] = False
    readings = _read_render_strip(
        [row["_render"] for row in rejected],
        [row["text"] for row in rejected],
    ) if rejected else []
    for row, reading in zip(rejected, readings):
        row["render"] = reading
        row["mismatch"] = not _reads_match(row["text"], reading)
    # Short lines the stroke check kept are read one letter at a time. The
    # trace letter has to be the picture's letter. One batch covers the page.
    small_rows = [row for row in accepted if row.get("_glyphOcr")]
    if small_rows:
        paired = []
        owners = []
        for row_index, row in enumerate(small_rows):
            tiles = _glyph_tiles(row["_source"], row["_render"])
            for source_tile, render_tile in tiles:
                paired.append(source_tile)
                paired.append(render_tile)
                owners.append(row_index)
        if paired:
            seen = _recognize_glyphs(paired)
            grouped = {}
            for owner, offset in zip(owners, range(0, len(seen), 2)):
                source_text, source_score = seen[offset] if offset < len(seen) else ("", 0.0)
                render_text, render_score = seen[offset + 1] if offset + 1 < len(seen) else ("", 0.0)
                grouped.setdefault(owner, []).append((source_text, source_score, render_text, render_score))
            for owner, row_readings in grouped.items():
                if not _small_reads_differ(row_readings):
                    continue
                row = small_rows[owner]
                row["glyphFail"] = True
                letters = "".join(_glyph_letter(item[2]) or "?" for item in row_readings)
                row["render"] = letters
                row["mismatch"] = True
                if os.environ.get("SMALL_GLYPH_DEBUG"):
                    sys.stderr.write(f"[small] ocr {row.get('text')!r} -> {letters!r}\n")
    for row in report:
        if row["_vector"] and not row.get("render"):
            row["render"] = _norm_text(row["text"])
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
            if any(
                (report[i]["_revert"] or report[i].get("_paragraph"))
                and not report[i].get("_lineOnly")
                for i in members
            ):
                fallback += 1
        if fallback <= 1:
            continue
        for line_id in grouped:
            for index in line_members[line_id]:
                if report[index]["_vector"]:
                    report[index]["_revert"] = True
    style_rows = []
    for index, row in enumerate(report):
        live = row["_vector"] and not row["_revert"]
        style_rows.append({
            "rect": row["_core"],
            "raster": not live,
            "colour": _sample_style(row.get("_source")),
            "indexes": [index] if live else [],
        })
    for index in _column_style_demote(style_rows):
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
            if row.get("_invadeFail"):
                why = "The trace crossed into the next line, so this line stayed in the picture."
            elif row.get("mismatch") or row.get("glyphFail"):
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
    for item in kept:
        item.pop("_ink", None)
        item.pop("core", None)
    for row in report:
        for key in ("_rect", "_core", "_vector", "_source", "_render", "_revert", "_pixelFail", "_paragraph", "_lineOnly", "_strokeFail", "_invadeFail", "_smallText", "_glyphOcr"):
            row.pop(key, None)
    return report, kept, qa, source_guard


def _upgrade_raster(plate, drawn, placed, source_bgr):
    """Sharpen the photo layer up to the plate size. Painted-out letters stay put.

    Real-ESRGAN runs only when a token and credit are available, and it is
    fitted to the same art box. Otherwise the Lanczos plate gets a mild unsharp.
    Vector text is drawn later, on top of this picture.
    """
    try:
        scale_mm = float((placed or {}).get("scale_mm") or 0)
        if scale_mm <= 0:
            return plate, ""
        source_dpi = 25.4 / scale_mm
        if source_dpi >= 299:
            return plate, ""
        art = (placed or {}).get("art_box") or (0, 0, plate.shape[1], plate.shape[0])
        paste_x, paste_y, art_w, art_h = [int(v) for v in art]
        upgraded = None
        note = ""
        remote = _remote_art(source_bgr, art_w, art_h)
        if remote is not None:
            fitted = remote
            if fitted.shape[1] != art_w or fitted.shape[0] != art_h:
                fitted = cv2.resize(fitted, (max(1, art_w), max(1, art_h)), interpolation=cv2.INTER_LANCZOS4)
            upgraded = plate.copy()
            y1 = min(plate.shape[0], paste_y + fitted.shape[0])
            x1 = min(plate.shape[1], paste_x + fitted.shape[1])
            y0 = max(0, paste_y)
            x0 = max(0, paste_x)
            if y1 > y0 and x1 > x0:
                upgraded[y0:y1, x0:x1] = fitted[y0 - paste_y:y0 - paste_y + (y1 - y0), x0 - paste_x:x0 - paste_x + (x1 - x0)]
            note = "The press picture was enlarged with Real-ESRGAN. The vector lettering was left as it is."
        if upgraded is None:
            from ai_upscale import photo_unsharp

            upgraded = photo_unsharp(plate)
            note = "The press picture was sharpened with Lanczos to the print size. The vector lettering was left as it is."
        protect = _paint_protect(plate, drawn)
        if int(protect.max()) > 0:
            keep = protect > 0
            upgraded[keep] = plate[keep]
        return upgraded, note
    except Exception as exc:
        sys.stderr.write(f"[vector-trace] raster sharpen skipped ({str(exc)[:140]})\n")
        return plate, ""


def _remote_art(source_bgr, art_w: int, art_h: int):
    if source_bgr is None or art_w < 8 or art_h < 8:
        return None
    try:
        from ai_upscale import full_frame_esrgan

        return full_frame_esrgan(source_bgr, timeout_s=12.0)
    except Exception:
        return None


def _paint_protect(plate, drawn) -> np.ndarray:
    """Painted-out letters, plus a few pixels, so a sharpen cannot halo the hole."""
    mask = np.zeros(plate.shape[:2], np.uint8)
    for item in drawn or []:
        _stamp_fringe(mask, item)
    if int(mask.max()) == 0:
        return mask
    return cv2.dilate(mask, np.ones((9, 9), np.uint8))


def _content_edge(drawn, raster_boxes, plate_shape, ppi: float, bleed_mm: float) -> dict:
    """Text in the bleed crosses the cut. Text inside 3 mm of the trim is too close."""
    height, width = int(plate_shape[0]), int(plate_shape[1])
    if ppi <= 0 or width < 4 or height < 4:
        return {"overCut": [], "nearTrim": []}
    bleed_px = float(bleed_mm) / 25.4 * float(ppi)
    safe_px = (float(bleed_mm) + 3.0) / 25.4 * float(ppi)
    over, near = [], []
    boxes = []
    for item in list(drawn or []) + list(raster_boxes or []):
        rect = item.get("core") or item.get("rect")
        text = str(item.get("text") or "").strip()
        if not rect or len(rect) < 4 or len(text) < 2:
            continue
        boxes.append((text, rect))
    for text, rect in boxes:
        x, y, bw, bh = [float(v) for v in rect[:4]]
        margin = min(x, y, width - (x + bw), height - (y + bh))
        if margin < bleed_px - (ppi / 25.4):
            over.append(text[:80])
        elif margin < safe_px - 1:
            near.append(text[:80])
    return {"overCut": over[:8], "nearTrim": near[:8]}


def _points(height_px: int, ppi: int) -> float:
    if ppi <= 0:
        return 0.0
    return round(float(height_px) / float(ppi) * 72.0, 2)


def _write_pdf(plate, drawn, output_pdf, trim_w, trim_h, bleed_mm, placed, retyped=None) -> dict:
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
        if retyped:
            from vector_retype import paint_retyped

            paint_retyped(page, retyped, sx, sy)
            try:
                doc.subset_fonts()
            except Exception:
                pass
        _set_boxes(page, trim_w, trim_h, bleed_mm)
        doc.save(output_pdf, deflate=True, garbage=1)
        qa["wrote"] = True
    finally:
        doc.close()
    _inspect_plate(output_pdf, trim_w, trim_h, bleed_mm, qa)
    if retyped:
        from vector_retype import fonts_embedded

        qa["fonts"] = bool(fonts_embedded(output_pdf))
        qa["retyped"] = len(retyped)
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


def _run_parallel(fn, items: list) -> list:
    """Run `fn` across the cores this process is allowed to use. Order is kept.

    The work is OpenCV on separate crops, so threads share the memory and do
    not pay a Windows process start per line.
    """
    items = list(items)
    workers = _workers()
    if len(items) <= 1 or workers <= 1:
        return [fn(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


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
    """One potrace process for the page. Order stays the page order.

    Each trace is a few milliseconds. Starting a process per line costs about
    80ms on Windows, so the lines share one bitmap. Building the 4× bitmaps
    is the part that spreads across cores. Potrace itself stays one process.
    """
    if not masks:
        return []
    binaries = _run_parallel(_trace_binary, masks)
    placed = _potrace_placed(binaries)
    return [_scale_paths(paths, 1.0 / TRACE_SCALE) for paths in placed]


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


def _trace_binary(mask: np.ndarray):
    """The 4× bitmap potrace fits. None when the mask is empty."""
    if mask is None or int(np.max(mask)) == 0:
        return None
    height, width = mask.shape[:2]
    big = cv2.resize(mask, (width * TRACE_SCALE, height * TRACE_SCALE), interpolation=cv2.INTER_CUBIC)
    # A fraction of a source pixel. It takes the stair off the cubic edge
    # (the gold school line at 600 dpi) and does not close a channel that is
    # still open at this scale.
    big = cv2.GaussianBlur(big, (0, 0), 0.7)
    _thr, binary = cv2.threshold(big, 127, 255, cv2.THRESH_BINARY)
    return binary


def _paths_from_svg(svg: str, height: int) -> list:
    if not svg:
        return []
    transform = _svg_transform(svg, height)
    # A missing scale used to fall back to 1 instead of potrace's 0.1, which
    # draws the letters about ten times too big. No transform means no paths.
    if transform is None:
        return []
    paths = []
    for raw in re.findall(r'<path\b[^>]*\bd="([^"]+)"', svg, flags=re.I | re.S):
        subpaths = _parse_path(raw, transform)
        if subpaths:
            paths.append(subpaths)
    return paths


def _group_center(group) -> tuple | None:
    xs = []
    ys = []
    for sub in group:
        for _cmd, pts in sub:
            for x, y in pts:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5


def _shift_group(group, dx: float, dy: float):
    shifted = []
    for sub in group:
        shifted.append([(cmd, [(x - dx, y - dy) for x, y in pts]) for cmd, pts in sub])
    return shifted


def _potrace_placed(bitmaps: list) -> list:
    """Paths in each bitmap's own pixel space. One process traces the page."""
    results = [[] for _ in bitmaps]
    usable = [
        index for index, bitmap in enumerate(bitmaps)
        if bitmap is not None and bitmap.size and int(bitmap.max()) > 0
    ]
    if not usable:
        return results
    chunks = []
    current = []
    pixels = 0
    for index in usable:
        area = int(bitmaps[index].shape[0]) * int(bitmaps[index].shape[1])
        if current and pixels + area > 24_000_000:
            chunks.append(current)
            current = []
            pixels = 0
        current.append(index)
        pixels += area
    if current:
        chunks.append(current)
    for chunk in chunks:
        _potrace_chunk(bitmaps, chunk, results)
    return results


def _potrace_chunk(bitmaps: list, indexes: list, results: list) -> None:
    gap = 8
    width = max(int(bitmaps[index].shape[1]) for index in indexes)
    height = sum(int(bitmaps[index].shape[0]) for index in indexes) + gap * (len(indexes) - 1)
    canvas = np.zeros((height, width), np.uint8)
    slots = []
    top = 0
    for index in indexes:
        bitmap = bitmaps[index]
        band_h, band_w = bitmap.shape[:2]
        canvas[top:top + band_h, :band_w] = bitmap
        slots.append((index, top, band_h, band_w))
        top += band_h + gap
    svg = _potrace_svg(canvas)
    groups = _paths_from_svg(svg, canvas.shape[0])
    if not groups:
        for index in indexes:
            bitmap = bitmaps[index]
            results[index] = _paths_from_svg(_potrace_svg(bitmap), bitmap.shape[0])
        return
    # A hole stays with the letter it sits in. Even-odd fill then punches the counter.
    buckets = {index: [] for index in indexes}
    for group in groups:
        for sub in group:
            center = _group_center([sub])
            if center is None:
                continue
            cx, cy = center
            owner = None
            best = 1e18
            for index, slot_top, band_h, band_w in slots:
                if 0 <= cx <= band_w and slot_top <= cy <= slot_top + band_h:
                    owner = (index, slot_top)
                    break
                dx = 0.0 if 0 <= cx <= band_w else min(abs(cx), abs(cx - band_w))
                dy = 0.0 if slot_top <= cy <= slot_top + band_h else min(abs(cy - slot_top), abs(cy - (slot_top + band_h)))
                dist = dx + dy
                if dist < best:
                    best = dist
                    owner = (index, slot_top)
            if owner is None:
                continue
            index, slot_top = owner
            buckets[index].append(_shift_group([sub], 0.0, float(slot_top))[0])
    for index, subs in buckets.items():
        if subs:
            results[index].append(subs)


def _dot_number(value: float) -> str:
    """A number strtod accepts when the decimal mark is a dot."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _potrace_svg(binary: np.ndarray) -> str:
    global _spawn_count
    height, width = binary.shape[:2]
    bits = (binary > 127).astype(np.uint8)
    pad = (8 - (width % 8)) % 8
    if pad:
        bits = np.pad(bits, ((0, 0), (0, pad)))
    packed = np.packbits(bits, axis=1)
    # Potrace traces black. PBM 1-bits are black, and those are our ink pixels.
    payload = f"P4\n{width} {height}\n".encode() + packed.tobytes()
    from host_paths import c_numeric_env, find_potrace

    program = find_potrace()
    env = c_numeric_env()
    # 1 is the same as 1.0, and it parses when the decimal mark is a comma.
    alpha = _dot_number(ALPHAMAX)
    opt = _dot_number(OPTTOLERANCE)
    result = _run_potrace(program, payload, alpha, opt, env)
    if result.returncode != 0 and ("," not in alpha or "," not in opt):
        # Windows en-ZA: strtod stops at the dot, so 0.2 is rejected. Retry with a comma.
        result = _run_potrace(program, payload, alpha.replace(".", ","), opt.replace(".", ","), env)
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _run_potrace(program: str, payload: bytes, alpha: str, opt: str, env: dict):
    global _spawn_count
    _spawn_count += 1
    return subprocess.run(
        [
            program, "-s", "--flat",
            "-t", str(TURDSIZE),
            "-a", alpha,
            "-O", opt,
            "-u", "10",
            "-o", "-",
        ],
        input=payload,
        check=False,
        capture_output=True,
        timeout=30,
        env=env,
    )


def _dots_in_transform(svg: str) -> str:
    """Turn potrace's %f decimal commas back into dots. Path data is untouched."""
    def fix(match):
        return _POTRACE_FLOAT.sub(r"\1.\2", match.group(0))

    return re.sub(r'transform="[^"]*"', fix, svg, count=1, flags=re.I)


def _svg_transform(svg: str, height: int):
    found = re.search(
        r"translate\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)\s*scale\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)",
        _dots_in_transform(svg),
    )
    if not found:
        return None
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
