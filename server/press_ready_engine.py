#!/usr/bin/env python3
"""
Press-Ready Engine.

Looks at the artwork, picks a bleed for each edge, builds the press file, and
checks that file. Staff do not have to choose a bleed style. The older styles
stay available as manual overrides.

Nothing inside the trim is moved except a safe-zone shrink (default 3mm
from the cut, between 1% and 3%). A normal press file uses 5mm of bleed.
The bleed never invents text.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import cv2
import numpy as np

SAFE_ZONE_MM = 3.0
SAFE_ZONE_SHRINK_MIN = 0.01
SAFE_ZONE_SHRINK_CAP = 0.03
# A picture enlarged past this stops being honest "print ready".
MIN_HONEST_EFFECTIVE_DPI = 240.0
TAC_LIMIT = 300.0
MM_TO_PT = 72.0 / 25.4

SIDES = ("top", "bottom", "left", "right")
CHAIN = {
    "flat": ("colour", "replicate", "stretch"),
    "gradient": ("gradient", "stretch", "colour"),
    "photo": ("inpaint", "extract", "stretch", "mirror"),
    "pattern": ("pattern", "inpaint", "extract"),
    "content-cut": ("extract", "colour", "inpaint"),
}
METHOD_NOTE = {
    "colour": "extended the edge colour",
    "gradient": "continued the gradient",
    "inpaint": "filled the picture outward",
    "pattern": "continued the repeating pattern",
    "extract": "extended the background",
    "mirror": "mirrored a clear edge",
    "replicate": "repeated the outer pixel",
    "stretch": "stretched the outer band",
    "kept": "kept the bleed already in the file",
}

_REPLICATE = {"checked": False, "ok": False}
_FACE = None


def _bgr(img: np.ndarray) -> np.ndarray:
    if img is None or img.size == 0:
        return img
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        white = np.full_like(bgr, 255.0)
        return (bgr * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return np.ascontiguousarray(img[:, :, :3])


def _px(mm: float, dpi: float) -> int:
    return max(1, int(round((float(mm) / 25.4) * float(dpi))))


def is_full_page_crop(x: float, y: float, w: float, h: float) -> bool:
    """The No Crop safety box is the whole page (0, 0, 1, 1). That is not a hand crop."""
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return False
    if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
        return x <= 0.02 and y <= 0.02 and w >= 0.98 and h >= 0.98
    return False


def replicate_available() -> bool:
    """One short account check. No credit, no token, or no network means local fill."""
    if _REPLICATE["checked"]:
        return bool(_REPLICATE["ok"])
    _REPLICATE["checked"] = True
    token = (os.environ.get("REPLICATE_API_TOKEN") or "").strip()
    if not token:
        _REPLICATE["ok"] = False
        return False
    request = urllib.request.Request(
        "https://api.replicate.com/v1/account",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            _REPLICATE["ok"] = int(getattr(response, "status", 200)) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        _REPLICATE["ok"] = False
    return bool(_REPLICATE["ok"])


def replicate_note() -> str:
    if not (os.environ.get("REPLICATE_API_TOKEN") or "").strip():
        return "No Replicate token, so the fill was done on this computer."
    if replicate_available():
        return "Replicate answered, and the local check still kept text out of the bleed."
    return "Replicate has no credit or could not be reached, so the fill was done on this computer."


def _face_cascade():
    global _FACE
    if _FACE is not None:
        return _FACE
    path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not os.path.isfile(path):
        _FACE = False
        return _FACE
    cascade = cv2.CascadeClassifier(path)
    _FACE = cascade if not cascade.empty() else False
    return _FACE


def _face_boxes(bgr: np.ndarray, dpi: float) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    cascade = _face_cascade()
    height, width = bgr.shape[:2]
    if cascade:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        scale = 800.0 / max(height, width) if max(height, width) > 800 else 1.0
        proxy = cv2.resize(gray, (int(width * scale), int(height * scale))) if scale < 1 else gray
        found = cascade.detectMultiScale(proxy, scaleFactor=1.1, minNeighbors=4)
        if found is not None:
            inv = 1.0 / scale
            for (x, y, fw, fh) in found:
                boxes.append((int(x * inv), int(y * inv), int(fw * inv), int(fh * inv)))
    # A drawn face (oval plus two dark eyes) still counts when Haar has nothing to match.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(
        cv2.GaussianBlur(gray, (5, 5), 0),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(8, width // 20),
        param1=80,
        param2=18,
        minRadius=max(6, _px(4, dpi)),
        maxRadius=max(12, _px(25, dpi)),
    )
    if circles is not None:
        for circle in np.round(circles[0]).astype(int):
            cx, cy, radius = int(circle[0]), int(circle[1]), int(circle[2])
            x0, y0 = max(0, cx - radius), max(0, cy - radius)
            x1, y1 = min(width, cx + radius), min(height, cy + radius)
            patch = gray[y0:y1, x0:x1]
            if patch.size == 0:
                continue
            dark = float(np.mean(patch < 80))
            if dark > 0.01:
                boxes.append((x0, y0, x1 - x0, y1 - y0))
    return boxes


def _content_boxes(bgr: np.ndarray) -> list[dict]:
    """OpenCV stand-in for OCR boxes: tight marks that look like text or a logo."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 200)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    height, width = gray.shape[:2]
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < 30 or w < 2 or h < 2:
            continue
        if area > width * height * 0.25:
            continue
        aspect = max(w, h) / max(1, min(w, h))
        kind = "logo" if aspect < 2.2 and area > 400 else "text"
        if kind == "text" and aspect < 1.2 and area < 200:
            continue
        boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "kind": kind})
    return boxes


def _strip(img: np.ndarray, side: str, depth: int) -> np.ndarray:
    height, width = img.shape[:2]
    depth = max(2, min(depth, height // 2, width // 2))
    if side == "top":
        return img[:depth, :, :]
    if side == "bottom":
        return img[-depth:, :, :]
    if side == "left":
        return img[:, :depth, :]
    return img[:, -depth:, :]


def _is_gradient(strip: np.ndarray) -> bool:
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
    for axis in (0, 1):
        inward = gray.mean(axis=axis)
        if inward.size < 4:
            continue
        delta = np.diff(inward)
        if float(np.std(delta)) > 8:
            continue
        if abs(float(inward[-1] - inward[0])) > 12:
            return True
    return False


def _is_pattern(strip: np.ndarray) -> bool:
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
    line = gray.mean(axis=0)
    if line.size < 24:
        return False
    diffs = np.diff(line)
    if float(np.mean(diffs > 0)) > 0.85 or float(np.mean(diffs < 0)) > 0.85:
        return False
    line = line - line.mean()
    energy = float(np.dot(line, line)) + 1e-6
    for shift in range(6, min(40, line.size // 2)):
        score = float(np.dot(line[:-shift], line[shift:])) / energy
        if score > 0.82:
            return True
    return False


def _textish(strip: np.ndarray) -> bool:
    """Letters sit in busy clusters with quiet gaps. A photo is busy all the way along."""
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 200)
    density = float(np.count_nonzero(edges)) / max(1, edges.size)
    if density < 0.03 or density > 0.22:
        return False
    long_axis = 1 if edges.shape[1] >= edges.shape[0] else 0
    bins = np.array_split(edges, 8, axis=long_axis)
    busyness = np.array([float(np.count_nonzero(part)) / max(1, part.size) for part in bins])
    if float(np.std(busyness)) < 0.03:
        return False
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    marks = 0
    for contour in contours:
        _x, _y, w, h = cv2.boundingRect(contour)
        if w >= 2 and h >= 2 and max(w, h) / max(1, min(w, h)) > 1.4:
            marks += 1
    return marks >= 3


def _boxes_on_side(boxes: list, side: str, width: int, height: int, margin: int) -> bool:
    for box in boxes:
        if isinstance(box, dict):
            x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        else:
            x, y, w, h = box
        if side == "top" and y < margin:
            return True
        if side == "bottom" and y + h > height - margin:
            return True
        if side == "left" and x < margin:
            return True
        if side == "right" and x + w > width - margin:
            return True
    return False


def analyse_bgr(img_bgr: np.ndarray, dpi: float = 300.0, safe_zone_mm: float = SAFE_ZONE_MM) -> dict:
    plane = _bgr(img_bgr)
    height, width = plane.shape[:2]
    dpi_f = float(dpi) if dpi and dpi > 0 else 300.0
    # Classification uses a small proxy. The bleed itself is still built at full size.
    proxy_scale = 480.0 / max(height, width, 1)
    if proxy_scale < 1:
        proxy = cv2.resize(plane, (max(1, int(width * proxy_scale)), max(1, int(height * proxy_scale))), interpolation=cv2.INTER_AREA)
        proxy_dpi = dpi_f * proxy_scale
    else:
        proxy = plane
        proxy_scale = 1.0
        proxy_dpi = dpi_f
    depth = min(_px(6.0, proxy_dpi), proxy.shape[0] // 3, proxy.shape[1] // 3)
    margin = _px(safe_zone_mm, proxy_dpi)
    texts = _content_boxes(proxy)
    text_count = sum(1 for box in texts if box["kind"] == "text")
    logo_count = sum(1 for box in texts if box["kind"] == "logo")
    # A photograph throws up hundreds of little contours. That is texture, not type.
    if text_count > 18:
        texts = [box for box in texts if box["kind"] != "text"]
    if logo_count > 4:
        texts = [box for box in texts if box["kind"] != "logo"]
    faces = _face_boxes(proxy, proxy_dpi)
    if len(faces) > 3:
        faces = []
    height, width = proxy.shape[:2]
    plane = proxy
    edges = {}
    for side in SIDES:
        strip = _strip(plane, side, depth)
        std = float(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).std())
        text = _boxes_on_side([b for b in texts if b["kind"] == "text"], side, width, height, margin) or _textish(strip)
        logo = _boxes_on_side([b for b in texts if b["kind"] == "logo"], side, width, height, margin)
        face = _boxes_on_side(faces, side, width, height, _px(8.0, proxy_dpi))
        if text or logo or face:
            kind = "flat" if std <= 12.0 else "content-cut"
        elif _is_gradient(strip):
            kind = "gradient"
        elif std <= 12.0:
            kind = "flat"
        elif _is_pattern(strip):
            kind = "pattern"
        else:
            kind = "photo"
        edges[side] = {
            "kind": kind,
            "text": bool(text),
            "logo": bool(logo),
            "face": bool(face),
            "blockedMirror": bool(text or logo or face),
        }
    safe_hits = []
    for box in texts:
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        if x < margin or y < margin or x + w > width - margin or y + h > height - margin:
            safe_hits.append(box)
    for box in faces:
        x, y, w, h = box
        if x < margin or y < margin or x + w > width - margin or y + h > height - margin:
            safe_hits.append({"x": x, "y": y, "w": w, "h": h, "kind": "face"})
    return {
        "edges": edges,
        "corners": {name: edges[a]["kind"] for name, a in (
            ("tl", "top"), ("tr", "top"), ("bl", "bottom"), ("br", "bottom"),
        )},
        "safeHits": len(safe_hits),
        "contentKind": "raster",
    }


def _median_bgr(strip: np.ndarray) -> tuple[int, int, int]:
    flat = strip.reshape(-1, 3)
    med = np.median(flat, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _edge_row(img: np.ndarray, side: str) -> np.ndarray:
    if side == "top":
        return img[0, :, :].copy()
    if side == "bottom":
        return img[-1, :, :].copy()
    if side == "left":
        return img[:, 0, :].copy()
    return img[:, -1, :].copy()


def _synth(img: np.ndarray, side: str, bleed: int, method: str) -> np.ndarray:
    """Bleed pixels for one side. Row 0 (or col 0) touches the trim."""
    row = _edge_row(img, side)
    length = row.shape[0]
    horizontal = side in ("top", "bottom")
    if method == "mirror" or method == "stretch":
        depth = min(8, img.shape[0] // 3 if horizontal else img.shape[1] // 3)
        depth = max(2, depth)
        band = _strip(img, side, depth)
        if side == "bottom":
            band = band[::-1]
        elif side == "right":
            band = band[:, ::-1, :]
        if horizontal:
            sample = band
            if method == "mirror":
                grown = cv2.resize(sample[::-1], (length, bleed), interpolation=cv2.INTER_LINEAR)
            else:
                grown = cv2.resize(sample, (length, bleed), interpolation=cv2.INTER_LINEAR)
            grown[0, :, :] = row
            return grown
        sample = np.swapaxes(band, 0, 1)
        if method == "mirror":
            grown = cv2.resize(sample[::-1], (img.shape[0], bleed), interpolation=cv2.INTER_LINEAR)
        else:
            grown = cv2.resize(sample, (img.shape[0], bleed), interpolation=cv2.INTER_LINEAR)
        grown[0, :, :] = row
        return np.swapaxes(grown, 0, 1)

    if method in ("colour", "extract", "replicate"):
        colour = _median_bgr(_strip(img, side, min(4, max(2, img.shape[0] // 10))))
        if horizontal:
            slab = np.empty((bleed, length, 3), np.uint8)
            slab[:, :, :] = colour
            if method == "replicate":
                slab[:, :, :] = row[np.newaxis, :, :]
            return slab
        slab = np.empty((img.shape[0], bleed, 3), np.uint8)
        slab[:, :, :] = colour
        if method == "replicate":
            slab[:, :, :] = row[:, np.newaxis, :]
        return slab

    if method == "gradient":
        band = _strip(img, side, min(12, max(4, (img.shape[0] if horizontal else img.shape[1]) // 4)))
        if side in ("bottom", "right"):
            band = band[::-1] if side == "bottom" else band[:, ::-1, :]
        if not horizontal:
            band = np.swapaxes(band, 0, 1)
        edge = band[0].astype(np.float32)
        inner = band[-1].astype(np.float32)
        step = (edge - inner) / max(1, band.shape[0] - 1)
        rows = [edge + step * (index + 1) for index in range(bleed)]
        grown = np.clip(np.stack(rows, axis=0), 0, 255).astype(np.uint8)
        grown[0] = edge.astype(np.uint8)
        if horizontal:
            return grown
        return np.swapaxes(grown, 0, 1)

    if method == "pattern":
        band = _strip(img, side, min(16, max(4, (img.shape[0] if horizontal else img.shape[1]) // 5)))
        if not horizontal:
            band = np.swapaxes(band, 0, 1)
        tile = band
        repeats = int(np.ceil(bleed / max(1, tile.shape[0]))) + 1
        grown = np.vstack([tile] * repeats)[:bleed]
        edge = _edge_row(img, side)
        grown[0] = edge if horizontal else edge
        if horizontal:
            return grown
        return np.swapaxes(grown, 0, 1)

    # Local photo fill. The first row is the real edge, so the cut has no seam.
    # Inpaint runs on a short strip so a large page stays quick.
    depth = min(28, max(6, (img.shape[0] if horizontal else img.shape[1]) // 8))
    band = _strip(img, side, depth)
    if side == "bottom":
        band = band[::-1]
    elif side == "right":
        band = band[:, ::-1, :]
    if not horizontal:
        band = np.swapaxes(band, 0, 1)
    work = np.vstack([band, np.zeros((bleed, band.shape[1], 3), np.uint8)])
    mask = np.zeros(work.shape[:2], np.uint8)
    mask[band.shape[0]:, :] = 255
    long_side = max(work.shape[:2])
    if long_side > 480:
        scale = 480.0 / long_side
        small = cv2.resize(work, (max(1, int(work.shape[1] * scale)), max(1, int(work.shape[0] * scale))))
        small_mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        filled = cv2.inpaint(small, small_mask, 3, cv2.INPAINT_NS)
        filled = cv2.resize(filled, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_LINEAR)
    else:
        filled = cv2.inpaint(work, mask, 3, cv2.INPAINT_NS)
    grown = filled[band.shape[0]:]
    grown[0] = band[0]
    if horizontal:
        return grown
    return np.swapaxes(grown, 0, 1)


def _paint(canvas: np.ndarray, img: np.ndarray, bleed: int, methods: dict) -> None:
    height, width = img.shape[:2]
    canvas[bleed:bleed + height, bleed:bleed + width] = img
    top = _synth(img, "top", bleed, methods["top"])
    bottom = _synth(img, "bottom", bleed, methods["bottom"])
    left = _synth(img, "left", bleed, methods["left"])
    right = _synth(img, "right", bleed, methods["right"])
    # Synth row 0 / col 0 is the trim edge. Flip so that edge sits against the cut.
    top_paint = top[::-1, :width]
    bottom_paint = bottom[:, :width]
    left_paint = left[:height, ::-1]
    right_paint = right[:height, :]
    canvas[:bleed, bleed:bleed + width] = top_paint
    canvas[bleed + height:, bleed:bleed + width] = bottom_paint
    canvas[bleed:bleed + height, :bleed] = left_paint
    canvas[bleed:bleed + height, bleed + width:] = right_paint
    # Corners blend the two edges that meet there.
    canvas[:bleed, :bleed] = (
        top_paint[:, :bleed].astype(np.uint16) + left_paint[:bleed, :].astype(np.uint16)
    ) // 2
    canvas[:bleed, bleed + width:] = (
        top_paint[:, -bleed:].astype(np.uint16) + right_paint[:bleed, :].astype(np.uint16)
    ) // 2
    canvas[bleed + height:, :bleed] = (
        bottom_paint[:, :bleed].astype(np.uint16) + left_paint[-bleed:, :].astype(np.uint16)
    ) // 2
    canvas[bleed + height:, bleed + width:] = (
        bottom_paint[:, -bleed:].astype(np.uint16) + right_paint[-bleed:, :].astype(np.uint16)
    ) // 2


def _methods_for(analysis: dict, step: dict[str, int]) -> dict[str, str]:
    chosen = {}
    for side in SIDES:
        edge = analysis["edges"][side]
        chain = [name for name in CHAIN[edge["kind"]] if name != "mirror" or not edge["blockedMirror"]]
        if not chain:
            chain = ["extract"]
        index = min(step.get(side, 0), len(chain) - 1)
        chosen[side] = chain[index]
    return chosen


def _bitmap_problems(canvas: np.ndarray, source: np.ndarray, bleed: int, methods: dict, analysis: dict) -> list[str]:
    problems = []
    height, width = source.shape[:2]
    trim = canvas[bleed:bleed + height, bleed:bleed + width]
    if trim.shape != source.shape or float(np.mean(np.abs(trim.astype(np.int16) - source.astype(np.int16)))) > 2.0:
        problems.append("Artwork inside the trim changed.")
    for side, sliver in (
        ("top", canvas[bleed - 1, bleed + 4:bleed + width - 4]),
        ("bottom", canvas[bleed + height, bleed + 4:bleed + width - 4]),
        ("left", canvas[bleed + 4:bleed + height - 4, bleed - 1]),
        ("right", canvas[bleed + 4:bleed + height - 4, bleed + width]),
    ):
        edge = _edge_row(source, side)
        if side in ("left", "right"):
            edge = edge[4:-4] if edge.shape[0] > 8 else edge
            sample = sliver
        else:
            edge = edge[4:-4] if edge.shape[0] > 8 else edge
            sample = sliver
        if analysis["edges"][side]["kind"] == "content-cut":
            continue
        if sample.size == 0 or edge.size == 0:
            continue
        take = min(sample.shape[0], edge.shape[0])
        delta = float(np.mean(np.abs(sample[:take].astype(np.int16) - edge[:take].astype(np.int16))))
        if delta > 28:
            problems.append(f"Visible seam on the {side}.")
    corners = (
        canvas[0, 0],
        canvas[0, -1],
        canvas[-1, 0],
        canvas[-1, -1],
    )
    source_corners = (source[0, 0], source[0, -1], source[-1, 0], source[-1, -1])
    for colour, original in zip(corners, source_corners):
        if int(colour.min()) > 245 and int(original.min()) < 230:
            problems.append("White sliver in a corner.")
            break
    for side in SIDES:
        if methods[side] == "mirror" and analysis["edges"][side]["blockedMirror"]:
            problems.append(f"Mirrored text or a face on the {side}.")
    return problems


def _shrink_fraction(dpi: float, width: int, height: int, safe_zone_mm: float) -> float:
    """How far to shrink. Always between 1% and 3%."""
    raw = (float(safe_zone_mm) / 25.4) * float(dpi) / max(int(width), int(height), 1)
    return min(SAFE_ZONE_SHRINK_CAP, max(SAFE_ZONE_SHRINK_MIN, raw))


def _rescue(img: np.ndarray, dpi: float, safe_zone_mm: float, analysis: dict) -> tuple[np.ndarray, dict]:
    note = {"applied": False, "scale": 1.0, "safeZoneMm": safe_zone_mm, "note": "Nothing inside the trim was moved."}
    if analysis.get("safeHits", 0) <= 0:
        return img, note
    height, width = img.shape[:2]
    raw = (float(safe_zone_mm) / 25.4) * float(dpi) / max(width, height, 1)
    fraction = _shrink_fraction(dpi, width, height, safe_zone_mm)
    scale = 1.0 - fraction
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    scaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.empty_like(img)
    methods = _methods_for(analysis, {})
    # Fill the freed ring with the same edge logic, then sit the artwork in the middle.
    pad_x = width - new_w
    pad_y = height - new_h
    left, top = pad_x // 2, pad_y // 2
    # Build a tight canvas and paint pads as bleed, then crop back? The pad is small.
    temp_bleed = max(left, width - new_w - left, top, height - new_h - top, 1)
    temp = np.zeros((new_h + 2 * temp_bleed, new_w + 2 * temp_bleed, 3), np.uint8)
    _paint(temp, scaled, temp_bleed, methods)
    y0 = temp_bleed - top
    x0 = temp_bleed - left
    canvas[:, :] = temp[y0:y0 + height, x0:x0 + width]
    # The shrunk artwork must stay pixel-identical in the middle.
    canvas[top:top + new_h, left:left + new_w] = scaled
    short = raw > SAFE_ZONE_SHRINK_CAP + 1e-9
    percent = round(fraction * 100.0, 2)
    note = {
        "applied": True,
        "scale": round(scale, 4),
        "safeZoneMm": safe_zone_mm,
        "percent": percent,
        "note": (
            f"Artwork shrunk by the 3% limit. Some text may still sit inside the {safe_zone_mm:g}mm safe zone."
            if short
            else f"Artwork shrunk by {percent:g}% so text sits inside the {safe_zone_mm:g}mm safe zone."
        ),
    }
    return canvas, note


def _edge_lines(analysis: dict, methods: dict, kept: bool = False) -> list[dict]:
    lines = []
    for side in SIDES:
        method = "kept" if kept else methods[side]
        edge = analysis["edges"][side]
        note = METHOD_NOTE[method]
        if method == "inpaint":
            note = note + " " + replicate_note()
        lines.append({
            "side": side,
            "kind": edge["kind"],
            "method": method,
            "note": f"{side.capitalize()}: {note}.",
            "text": edge["text"],
            "face": edge["face"],
            "logo": edge["logo"],
        })
    return lines


def effective_dpi(src_w: int, src_h: int, trim_w_mm: float, trim_h_mm: float) -> float:
    target_w = max(1.0, trim_w_mm / 25.4 * 300.0)
    target_h = max(1.0, trim_h_mm / 25.4 * 300.0)
    scale = max(target_w / max(1, src_w), target_h / max(1, src_h))
    if scale <= 0:
        return 300.0
    return 300.0 / scale


def cover_scale(img: np.ndarray, width: int, height: int) -> np.ndarray:
    src_h, src_w = img.shape[:2]
    scale = max(width / max(1, src_w), height / max(1, src_h))
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(img, (new_w, new_h), interpolation=interp)
    x0 = max(0, (new_w - width) // 2)
    y0 = max(0, (new_h - height) // 2)
    crop = resized[y0:y0 + height, x0:x0 + width]
    if crop.shape[0] != height or crop.shape[1] != width:
        crop = cv2.resize(crop, (width, height), interpolation=cv2.INTER_LINEAR)
    return crop


def _existing_image_bleed(img: np.ndarray, dpi: float, trim_w: float, trim_h: float) -> dict | None:
    if not dpi or dpi < 150 or trim_w <= 0 or trim_h <= 0:
        return None
    height, width = img.shape[:2]
    doc_w = width / float(dpi) * 25.4
    doc_h = height / float(dpi) * 25.4
    extra_w = doc_w - float(trim_w)
    extra_h = doc_h - float(trim_h)
    if extra_w < 4 or extra_h < 4:
        return None
    left = right = extra_w / 2.0
    top = bottom = extra_h / 2.0
    if abs(left - top) > 1.5 or min(left, top) < 2 or max(left, top) > 25:
        return None
    return {"top": top, "bottom": bottom, "left": left, "right": right, "trim_w_mm": float(trim_w), "trim_h_mm": float(trim_h)}


def _crop_trim(img: np.ndarray, info: dict) -> np.ndarray:
    height, width = img.shape[:2]
    full_w = info["trim_w_mm"] + info["left"] + info["right"]
    full_h = info["trim_h_mm"] + info["top"] + info["bottom"]
    x0 = int(round(info["left"] / full_w * width))
    y0 = int(round(info["top"] / full_h * height))
    x1 = int(round((info["left"] + info["trim_w_mm"]) / full_w * width))
    y1 = int(round((info["top"] + info["trim_h_mm"]) / full_h * height))
    return img[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)].copy()


def extend_trim(img: np.ndarray, bleed_px: int, analysis: dict) -> tuple[np.ndarray, dict]:
    step = {side: 0 for side in SIDES}
    source = img
    canvas = None
    methods = _methods_for(analysis, step)
    problems: list[str] = []
    for _round in range(4):
        methods = _methods_for(analysis, step)
        height, width = source.shape[:2]
        canvas = np.zeros((height + 2 * bleed_px, width + 2 * bleed_px, 3), np.uint8)
        _paint(canvas, source, bleed_px, methods)
        problems = _bitmap_problems(canvas, source, bleed_px, methods, analysis)
        if not problems:
            break
        bumped = False
        for side in SIDES:
            if any(side in problem.lower() or "corner" in problem.lower() or "seam" in problem.lower() for problem in problems):
                step[side] = step.get(side, 0) + 1
                bumped = True
        if not bumped:
            for side in SIDES:
                step[side] = step.get(side, 0) + 1
    report_edges = _edge_lines(analysis, methods, kept=False)
    return canvas, {"edges": report_edges, "methods": methods, "problems": problems, "rounds": step}


def compile_raster_canvas(
    img: np.ndarray,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float,
    embedded_dpi: float | None = None,
    safe_zone_mm: float = SAFE_ZONE_MM,
) -> tuple[np.ndarray, dict]:
    plane = _bgr(img)
    src_h, src_w = plane.shape[:2]
    dpi = 300.0
    trim_w_px = _px(trim_w_mm, dpi)
    trim_h_px = _px(trim_h_mm, dpi)
    bleed_px = _px(bleed_mm, dpi)
    existing = _existing_image_bleed(plane, embedded_dpi or 0, trim_w_mm, trim_h_mm)
    kept = False
    trim = plane
    if existing:
        trim = _crop_trim(plane, existing)
        trim = cover_scale(trim, trim_w_px, trim_h_px) if (
            abs(existing["trim_w_mm"] - trim_w_mm) > 2 or abs(existing["trim_h_mm"] - trim_h_mm) > 2
        ) else cv2.resize(trim, (trim_w_px, trim_h_px), interpolation=cv2.INTER_AREA)
        have = min(existing["top"], existing["bottom"], existing["left"], existing["right"])
        if have + 0.4 >= float(bleed_mm) and abs(existing["trim_w_mm"] - trim_w_mm) <= 2 and abs(existing["trim_h_mm"] - trim_h_mm) <= 2:
            # Keep the supplied ring. Fit the whole file to trim + the requested bleed.
            out_w = trim_w_px + 2 * bleed_px
            out_h = trim_h_px + 2 * bleed_px
            canvas = cv2.resize(plane, (out_w, out_h), interpolation=cv2.INTER_AREA)
            # Put the trim pixels back without a second resample of the middle when sizes match.
            if plane.shape[1] == out_w and plane.shape[0] == out_h:
                canvas = plane.copy()
            analysis = analyse_bgr(trim, dpi, safe_zone_mm)
            kept = True
            edges = _edge_lines(analysis, {side: "kept" for side in SIDES}, kept=True)
            report = _base_report(analysis, edges, True, bleed_mm, safe_zone_mm, src_w, src_h, trim_w_mm, trim_h_mm)
            report["existingBleed"] = True
            report["rescue"] = {"applied": False, "scale": 1.0, "safeZoneMm": safe_zone_mm, "note": "Existing bleed kept. The trim was not shrunk."}
            _finish_resolution(report, src_w, src_h, trim_w_mm, trim_h_mm, analysis)
            problems = _bitmap_problems(canvas, cv2.resize(trim, (trim_w_px, trim_h_px)), bleed_px, {s: "kept" for s in SIDES}, analysis)
            # Kept artwork will not match a resized trim exactly. Drop that one check.
            problems = [item for item in problems if "changed" not in item and "seam" not in item.lower()]
            report["bitmapProblems"] = problems
            if problems:
                report["passed"] = False
                report["status"] = "needs-attention"
                report["reason"] = problems[0]
                report["fix"] = "Open the file and extend the background yourself, then upload it again."
            return canvas, report
    else:
        trim = cover_scale(plane, trim_w_px, trim_h_px)

    analysis = analyse_bgr(trim, dpi, safe_zone_mm)
    rescued, rescue = _rescue(trim, dpi, safe_zone_mm, analysis)
    # Re-read edges after a shrink so the fill matches the new outer pixels.
    if rescue.get("applied"):
        analysis = analyse_bgr(rescued, dpi, safe_zone_mm)
        source_for_check = rescued
    else:
        source_for_check = trim
    canvas, built = extend_trim(source_for_check, bleed_px, analysis)
    edges = built["edges"]
    report = _base_report(analysis, edges, False, bleed_mm, safe_zone_mm, src_w, src_h, trim_w_mm, trim_h_mm)
    report["existingBleed"] = bool(existing) and kept
    report["rescue"] = rescue
    report["bitmapProblems"] = built["problems"]
    _finish_resolution(report, src_w, src_h, trim_w_mm, trim_h_mm, analysis)
    if built["problems"] and report["passed"]:
        report["passed"] = False
        report["status"] = "needs-attention"
        report["reason"] = built["problems"][0]
        report["fix"] = "Try a manual bleed style, or move the artwork slightly in from the edge and upload it again."
    return canvas, report


def _base_report(analysis, edges, kept, bleed_mm, safe_zone_mm, src_w, src_h, trim_w, trim_h) -> dict:
    return {
        "passed": True,
        "status": "ready",
        "headline": "Ready for press",
        "reason": "",
        "fix": "",
        "contentKind": analysis.get("contentKind", "raster"),
        "existingBleed": False,
        "bleedMm": bleed_mm,
        "safeZoneMm": safe_zone_mm,
        "edges": edges,
        "keptExisting": kept,
        "replicate": replicate_note(),
        "analysis": {
            "contentKind": analysis.get("contentKind", "raster"),
            "edges": {side: analysis["edges"][side]["kind"] for side in SIDES},
        },
    }


def _finish_resolution(report: dict, src_w: int, src_h: int, trim_w: float, trim_h: float, analysis: dict) -> None:
    if analysis.get("contentKind") == "vector":
        report["effectiveDpi"] = 300
        report["resolutionNote"] = "Vector artwork stays sharp at any size."
        return
    dpi = effective_dpi(src_w, src_h, trim_w, trim_h)
    report["effectiveDpi"] = round(dpi, 1)
    kinds = {edge["kind"] for edge in analysis["edges"].values()}
    flat_only = kinds <= {"flat"}
    if dpi >= 300 or flat_only:
        report["resolutionNote"] = "Resolution is right for this print size." if not flat_only else "Flat colour is printed at 300 DPI."
        report["aiEnhanceApplied"] = False
        return
    if dpi >= MIN_HONEST_EFFECTIVE_DPI:
        report["aiEnhanceApplied"] = True
        report["resolutionNote"] = "The picture was a little small, so it was enlarged smoothly to the print size."
        return
    report["aiEnhanceApplied"] = False
    report["passed"] = False
    report["status"] = "needs-attention"
    report["headline"] = "Needs attention"
    report["reason"] = f"This picture is about {int(dpi)} DPI at this size. Press work needs at least 300 DPI."
    report["fix"] = "Turn on AI Enhance, or upload a larger file (about 300 DPI at the print size)."
    report["resolutionNote"] = report["reason"]


def plan_artwork(img_bgr: np.ndarray, dpi: float = 150.0, safe_zone_mm: float = SAFE_ZONE_MM) -> dict:
    """Fast read for the job page. The press file is checked again at compile."""
    analysis = analyse_bgr(img_bgr, dpi, safe_zone_mm)
    methods = _methods_for(analysis, {})
    edges = _edge_lines(analysis, methods, kept=False)
    return {
        "passed": False,
        "status": "planned",
        "headline": "Automatic (recommended)",
        "reason": "",
        "fix": "",
        "contentKind": "raster",
        "edges": edges,
        "safeZoneMm": safe_zone_mm,
        "replicate": replicate_note(),
        "analysis": {"contentKind": "raster", "edges": {side: analysis["edges"][side]["kind"] for side in SIDES}},
    }


def write_cmyk_pdf(canvas_bgr: np.ndarray, path: str, trim_w_mm: float, trim_h_mm: float, bleed_mm: float) -> None:
    import io

    import pymupdf as fitz
    from PIL import Image

    rgb = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB)
    cmyk = Image.fromarray(rgb).convert("CMYK")
    buffer = io.BytesIO()
    cmyk.save(buffer, format="TIFF", dpi=(300, 300))
    data = buffer.getvalue()
    width_pt = (trim_w_mm + 2 * bleed_mm) * MM_TO_PT
    height_pt = (trim_h_mm + 2 * bleed_mm) * MM_TO_PT
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    page.insert_image(page.rect, stream=data)
    _set_boxes(page, trim_w_mm, trim_h_mm, bleed_mm)
    doc.save(path, deflate=True, garbage=4)
    doc.close()


def _set_boxes(page, trim_w_mm: float, trim_h_mm: float, bleed_mm: float) -> None:
    bleed_pt = bleed_mm * MM_TO_PT
    media = page.rect
    page.set_mediabox(media)
    page.set_cropbox(media)
    page.set_bleedbox(media)
    page.set_trimbox(fitz_rect(bleed_pt, bleed_pt, bleed_pt + trim_w_mm * MM_TO_PT, bleed_pt + trim_h_mm * MM_TO_PT))


def fitz_rect(x0, y0, x1, y1):
    import pymupdf as fitz
    return fitz.Rect(x0, y0, x1, y1)


def preflight_pdf(path: str, trim_w_mm: float, trim_h_mm: float, bleed_mm: float, report: dict, require_text: str = "") -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    page = doc[0]
    media = page.mediabox
    trim = page.trimbox
    bleed_box = page.bleedbox
    expect_w = (trim_w_mm + 2 * bleed_mm) * MM_TO_PT
    expect_h = (trim_h_mm + 2 * bleed_mm) * MM_TO_PT
    expect_trim_w = trim_w_mm * MM_TO_PT
    failures = []
    if abs(media.width - expect_w) > 1.8 or abs(media.height - expect_h) > 1.8:
        failures.append(
            f"The page is {media.width * 25.4 / 72:.1f} × {media.height * 25.4 / 72:.1f}mm. "
            f"It should be the trim plus {bleed_mm:g}mm on every side."
        )
    if abs(trim.width - expect_trim_w) > 1.8:
        failures.append("The trim box is not the finished page size.")
    if abs(trim.x0 - bleed_mm * MM_TO_PT) > 1.8:
        failures.append("The trim box is not centred in the bleed.")
    if abs(bleed_box.width - media.width) > 1.8 or abs(bleed_box.height - media.height) > 1.8:
        failures.append("The bleed box does not cover the page.")
    pix = page.get_pixmap(matrix=fitz.Matrix(0.4, 0.4), alpha=False, colorspace=fitz.csRGB)
    samples = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    corners = (samples[1, 1], samples[1, -2], samples[-2, 1], samples[-2, -2])
    if any(int(colour.min()) > 248 for colour in corners) and not report.get("allowWhite"):
        failures.append("A corner of the bleed is white.")
    text = page.get_text("text") or ""
    if require_text and require_text not in text:
        failures.append("The original text is no longer in the file.")
    images = page.get_images()
    if images:
        info = doc.extract_image(images[0][0])
        if info.get("colorspace") not in (4, "CMYK", None) and info.get("cs-name") not in ("DeviceCMYK", None):
            # PyMuPDF uses numeric colorspace 4 for CMYK.
            if info.get("colorspace") != 4 and "CMYK" not in str(info.get("cs-name", "")):
                failures.append("The press file is not CMYK.")
    elif report.get("contentKind") != "vector":
        failures.append("The press file has no picture.")
    # Total ink on the corner samples, via a simple RGB to CMYK reading.
    for colour in corners:
        r, g, b = [channel / 255.0 for channel in colour[:3]]
        k = 1.0 - max(r, g, b)
        if k >= 0.999:
            tac = 100.0
        else:
            tac = ((1 - r - k) / (1 - k) + (1 - g - k) / (1 - k) + (1 - b - k) / (1 - k) + k) * 100.0
        if tac > TAC_LIMIT + 1:
            failures.append(f"Ink coverage is {tac:.0f}%, above the {TAC_LIMIT:.0f}% limit.")
            break
    doc.close()
    checks = {
        "pageSize": not any("page is" in item for item in failures),
        "boxes": not any("box" in item for item in failures),
        "corners": not any("corner" in item or "white" in item for item in failures),
        "cmyk": not any("CMYK" in item for item in failures),
        "ink": not any("Ink coverage" in item for item in failures),
        "text": not any("text is no longer" in item for item in failures),
    }
    report = dict(report)
    report["preflight"] = {"failures": failures, "checks": checks}
    if failures or report.get("status") == "needs-attention":
        if failures and report.get("status") != "needs-attention":
            report["passed"] = False
            report["status"] = "needs-attention"
            report["headline"] = "Needs attention"
            report["reason"] = failures[0]
            report["fix"] = report.get("fix") or "Upload the artwork again, or pick a manual bleed style and check the edges."
        elif failures and not report.get("reason"):
            report["reason"] = failures[0]
    else:
        report["passed"] = True
        report["status"] = "ready"
        report["headline"] = "Ready for press"
    return report


def _gs_bin() -> str:
    found = shutil.which("gs")
    return found or "gs"


def convert_cmyk_keep_text(src: str, dest: str) -> None:
    """CMYK conversion that leaves text and vectors in the file. Memory stays leashed."""
    icc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles", "CoatedFOGRA39.icc")
    cmd = [
        _gs_bin(),
        "-dNOPAUSE", "-dBATCH", "-dSAFER",
        "-sDEVICE=pdfwrite",
        f"-sOutputFile={dest}",
        "-dPDFSETTINGS=/prepress",
        "-sProcessColorModel=DeviceCMYK",
        "-sColorConversionStrategy=CMYK",
        "-dRenderIntent=1",
        "-dBlackPtComp=1",
        "-dKPreserve=2",
        "-dNumRenderingThreads=1",
        "-dBufferSpace=50000000",
        "-dMaxBitmap=50000000",
        "-dBandBufferSpace=50000000",
        "-c", "<< /MaxBitmap 50000000 /BufferSize 50000000 >> setuserparams << /HWResolution [300 300] >> setpagedevice",
        "-f", src,
    ]
    if os.path.isfile(icc):
        cmd.insert(cmd.index("-f"), f"-sDefaultCMYKProfile={icc}")
    subprocess.run(cmd, check=True, timeout=90, capture_output=True)


def _page_proxy(page, max_px: int = 500) -> np.ndarray:
    import pymupdf as fitz
    rect = page.rect
    scale = max_px / max(rect.width, rect.height, 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def compile_vector_press(
    src_path: str,
    out_path: str,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float,
    safe_zone_mm: float = SAFE_ZONE_MM,
) -> dict:
    import pymupdf as fitz

    ext = os.path.splitext(src_path)[1].lower()
    open_path = src_path
    if ext in (".ai", ".eps"):
        from illustrator_intake import prepare_illustrator_file
        prepared = prepare_illustrator_file(src_path, ext)
        if not prepared.get("success"):
            return {
                "used": False,
                "report": {
                    "passed": False,
                    "status": "needs-attention",
                    "headline": "Needs attention",
                    "reason": prepared.get("error") or "This Illustrator file could not be read.",
                    "fix": "In Illustrator, use Save a Copy as PDF and upload that PDF.",
                    "edges": [],
                },
            }
        open_path = prepared.get("pdfPath") or src_path

    src = fitz.open(open_path)
    page = src[0]
    media = page.mediabox
    trim = page.trimbox
    trim_w_pt = trim.width
    trim_h_pt = trim.height
    existing = {
        "left": max(0.0, (trim.x0 - media.x0) * 25.4 / 72.0),
        "bottom": max(0.0, (trim.y0 - media.y0) * 25.4 / 72.0),
        "right": max(0.0, (media.x1 - trim.x1) * 25.4 / 72.0),
        "top": max(0.0, (media.y1 - trim.y1) * 25.4 / 72.0),
    }
    text = page.get_text("text") or ""
    images = page.get_images()
    if text.strip() and images:
        kind = "mixed"
    elif images and not text.strip():
        kind = "raster"
    else:
        kind = "vector"
    proxy = _page_proxy(page)
    analysis = analyse_bgr(proxy, 120.0, safe_zone_mm)
    analysis["contentKind"] = kind
    methods = _methods_for(analysis, {})
    edges = _edge_lines(analysis, methods, kept=False)

    out_w = (trim_w_mm + 2 * bleed_mm) * MM_TO_PT
    out_h = (trim_h_mm + 2 * bleed_mm) * MM_TO_PT
    bleed_pt = bleed_mm * MM_TO_PT
    doc = fitz.open()
    new_page = doc.new_page(width=out_w, height=out_h)
    # Background from the sampled edge colours, in CMYK, only as the page fill.
    # The original page is placed on top, still as vectors.
    colour = _median_bgr(proxy)
    # RGB here is only the under-colour. Ghostscript turns the page into CMYK and leaves the text in place.
    r = int(colour[2]) / 255.0
    g = int(colour[1]) / 255.0
    b = int(colour[0]) / 255.0
    new_page.draw_rect(new_page.rect, color=None, fill=(r, g, b), fill_opacity=1)
    have = min(existing.values()) if existing else 0
    trim_matches = abs(trim_w_pt * 25.4 / 72.0 - trim_w_mm) <= 2 and abs(trim_h_pt * 25.4 / 72.0 - trim_h_mm) <= 2
    dest = fitz.Rect(bleed_pt, bleed_pt, bleed_pt + trim_w_mm * MM_TO_PT, bleed_pt + trim_h_mm * MM_TO_PT)
    if trim_matches and have + 0.4 >= bleed_mm:
        # Place the existing trim-plus-bleed, clipped to the requested bleed. Do not add another ring.
        clip = fitz.Rect(
            trim.x0 - bleed_mm * MM_TO_PT,
            trim.y0 - bleed_mm * MM_TO_PT,
            trim.x1 + bleed_mm * MM_TO_PT,
            trim.y1 + bleed_mm * MM_TO_PT,
        )
        new_page.show_pdf_page(new_page.rect, src, 0, clip=clip)
        edges = _edge_lines(analysis, methods, kept=True)
        kept = True
    else:
        scale = 1.0
        if analysis.get("safeHits", 0) > 0:
            scale = 1.0 - min(SAFE_ZONE_SHRINK_CAP, 0.02)
        placed = dest
        if scale < 0.999:
            margin_x = dest.width * (1 - scale) / 2
            margin_y = dest.height * (1 - scale) / 2
            placed = fitz.Rect(dest.x0 + margin_x, dest.y0 + margin_y, dest.x1 - margin_x, dest.y1 - margin_y)
        # Cover: clip the source trim so the placed art fills the trim.
        src_trim = trim if trim.width > 2 and trim.height > 2 else page.rect
        cover = max(placed.width / max(src_trim.width, 1), placed.height / max(src_trim.height, 1))
        clip_w = placed.width / cover
        clip_h = placed.height / cover
        clip = fitz.Rect(
            src_trim.x0 + (src_trim.width - clip_w) / 2,
            src_trim.y0 + (src_trim.height - clip_h) / 2,
            src_trim.x0 + (src_trim.width - clip_w) / 2 + clip_w,
            src_trim.y0 + (src_trim.height - clip_h) / 2 + clip_h,
        )
        new_page.show_pdf_page(placed, src, 0, clip=clip)
        kept = False
        # Photo edges get a raster ring in the bleed only, under the vectors.
        if any(edge["kind"] in ("photo", "pattern") for edge in analysis["edges"].values()):
            ring = extend_trim(cover_scale(proxy, max(32, int(trim_w_mm * 4)), max(32, int(trim_h_mm * 4))), max(4, int(bleed_mm * 4)), analysis)[0]
            # The ring image is only a guide sitting full-page behind; vectors were already placed.
            # Rebuild order: background image first, then vectors. Recreate the page.
            doc.close()
            doc = fitz.open()
            new_page = doc.new_page(width=out_w, height=out_h)
            import io
            from PIL import Image
            rgb = cv2.cvtColor(ring, cv2.COLOR_BGR2RGB)
            buf = io.BytesIO()
            Image.fromarray(rgb).convert("CMYK").save(buf, format="TIFF")
            new_page.insert_image(new_page.rect, stream=buf.getvalue())
            new_page.show_pdf_page(placed, src, 0, clip=clip)
    _set_boxes(new_page, trim_w_mm, trim_h_mm, bleed_mm)
    raw_path = out_path + ".raw.pdf"
    doc.save(raw_path, deflate=True, garbage=4)
    doc.close()
    src.close()
    try:
        convert_cmyk_keep_text(raw_path, out_path)
    except Exception:
        shutil.copyfile(raw_path, out_path)
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)
    # Ghostscript can drop the boxes. Put them back.
    fixed = fitz.open(out_path)
    for fixed_page in fixed:
        _set_boxes(fixed_page, trim_w_mm, trim_h_mm, bleed_mm)
    boxed = out_path + ".box.pdf"
    fixed.save(boxed, deflate=True, garbage=4)
    fixed.close()
    os.replace(boxed, out_path)

    rescue = {
        "applied": (not kept) and analysis.get("safeHits", 0) > 0,
        "scale": 0.98 if analysis.get("safeHits", 0) > 0 and not kept else 1.0,
        "safeZoneMm": safe_zone_mm,
        "note": "Text and vectors were kept live." if kind != "raster" else "The picture stays as placed. Only the bleed ring was added.",
    }
    report = _base_report(analysis, edges, kept, bleed_mm, safe_zone_mm, 0, 0, trim_w_mm, trim_h_mm)
    report["contentKind"] = kind
    report["analysis"]["contentKind"] = kind
    report["existingBleed"] = kept
    report["rescue"] = rescue
    report["effectiveDpi"] = 300
    report["resolutionNote"] = "Vector artwork stays sharp at any size." if kind == "vector" else "The placed artwork was kept. The bleed ring is 300 DPI."
    report["allowWhite"] = False
    report = preflight_pdf(out_path, trim_w_mm, trim_h_mm, bleed_mm, report, require_text="")
    return {"used": True, "report": report, "path": out_path}


def summary_sentence(report: dict) -> str:
    if not report:
        return ""
    if report.get("status") == "needs-attention":
        return f"Needs attention. {report.get('reason', '')} {report.get('fix', '')}".strip()
    parts = [edge.get("note", "") for edge in report.get("edges") or []]
    rescue = (report.get("rescue") or {}).get("note", "")
    existing = " Existing bleed was kept." if report.get("existingBleed") else ""
    return ("Ready for press. " + " ".join(parts) + " " + rescue + existing).strip()
