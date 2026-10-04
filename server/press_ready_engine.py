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
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

from http_headers import external_headers

SAFE_ZONE_MM = 3.0
SAFE_ZONE_SHRINK_MIN = 0.01
SAFE_ZONE_SHRINK_CAP = 0.03
# A picture enlarged past this stops being honest "print ready".
MIN_HONEST_EFFECTIVE_DPI = 240.0
TAC_LIMIT = 300.0
MM_TO_PT = 72.0 / 25.4


def inferred_side_bleed(
    page_w_mm: float,
    page_h_mm: float,
    trim_w_mm: float,
    trim_h_mm: float,
    lo: float = 2.0,
    hi: float = 15.0,
    trim_tol: float = 2.5,
):
    """Per-side bleed when the page is the trim, or the trim plus bleed already there.

    A Canva page often has no TrimBox. Two to 15 mm on each side is bleed that is
    already in the file, including a full 5 mm and a more generous bleed. None
    means the page is a different shape.
    """
    extra_w = float(page_w_mm) - float(trim_w_mm)
    extra_h = float(page_h_mm) - float(trim_h_mm)
    side_w = extra_w / 2.0
    side_h = extra_h / 2.0
    if (lo - 0.05) <= side_w <= (hi + 0.05) and (lo - 0.05) <= side_h <= (hi + 0.05):
        return {
            "left": side_w,
            "right": side_w,
            "top": side_h,
            "bottom": side_h,
            "kind": "existing",
        }
    if abs(extra_w) <= trim_tol and abs(extra_h) <= trim_tol:
        return {
            "left": max(0.0, side_w),
            "right": max(0.0, side_w),
            "top": max(0.0, side_h),
            "bottom": max(0.0, side_h),
            "kind": "trim",
        }
    return None

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

# A "no" (no credit, blocked, or offline) is tried again after a few minutes.
# A "yes" stays for this run so the account is not checked on every page.
REPLICATE_NEGATIVE_TTL_S = 5 * 60
_REPLICATE = {"checked_at": 0.0, "ok": False}
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
    """One short account check. No credit, no token, or no network means local fill.

    A negative answer expires after a few minutes so adding credit, or a
    Cloudflare block that has cleared, is picked up without restarting.
    """
    now = time.monotonic()
    checked_at = float(_REPLICATE.get("checked_at") or 0.0)
    if checked_at > 0:
        if _REPLICATE.get("ok"):
            return True
        if (now - checked_at) < REPLICATE_NEGATIVE_TTL_S:
            return False
    token = (os.environ.get("REPLICATE_API_TOKEN") or "").strip()
    _REPLICATE["checked_at"] = now
    if not token:
        _REPLICATE["ok"] = False
        return False
    request = urllib.request.Request(
        "https://api.replicate.com/v1/account",
        headers=external_headers({"Authorization": f"Bearer {token}"}),
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            _REPLICATE["ok"] = int(getattr(response, "status", 200)) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        _REPLICATE["ok"] = False
    return bool(_REPLICATE["ok"])


def replicate_note() -> str:
    """Local fill unless Real-ESRGAN is explicitly on. Do not contact Replicate otherwise."""
    from host_paths import esrgan_enabled

    if not esrgan_enabled():
        return "Real-ESRGAN is off, so nothing was sent to Replicate."
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


def _content_image_xrefs(doc, page) -> list:
    """Image xrefs that are the artwork. A soft-mask grey is not one of them."""
    full = page.get_images(full=True) or []
    masks = set()
    for item in full:
        if len(item) > 1 and int(item[1] or 0) > 0:
            masks.add(int(item[1]))
    xrefs = []
    for item in full:
        xref = int(item[0])
        if xref in masks:
            continue
        xrefs.append(xref)
    return xrefs


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
    images = _content_image_xrefs(doc, page)
    if images:
        info = doc.extract_image(images[0])
        if info.get("colorspace") not in (4, "CMYK", None) and info.get("cs-name") not in ("DeviceCMYK", None):
            # PyMuPDF uses numeric colorspace 4 for CMYK. Soft-mask greys are not the picture.
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
    from gs_binary import find_gs_binary
    return find_gs_binary()


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
        "-sColorConversionStrategyForImages=CMYK",
        "-dDownsampleColorImages=false",
        "-dDownsampleGrayImages=false",
        "-dDownsampleMonoImages=false",
        "-c",
        "<< /AutoFilterColorImages false /AutoFilterGrayImages false "
        "/ColorImageFilter /FlateEncode /GrayImageFilter /FlateEncode "
        "/DownsampleColorImages false /DownsampleGrayImages false >> setdistillerparams "
        "<< /MaxBitmap 50000000 /BufferSize 50000000 >> setuserparams "
        "<< /HWResolution [300 300] >> setpagedevice",
        "-f", src,
    ]
    if os.path.isfile(icc):
        cmd.insert(cmd.index("-f"), f"-sDefaultCMYKProfile={icc}")
    from host_paths import c_numeric_env, icc_file

    try:
        srgb = icc_file("srgb.icc")
    except FileNotFoundError:
        srgb = ""
    if srgb and os.path.isfile(srgb):
        cmd.insert(cmd.index("-f"), f"-sDefaultRGBProfile={srgb}")
    completed = subprocess.run(cmd, check=False, timeout=90, capture_output=True, env=c_numeric_env())
    from gs_binary import ghostscript_succeeded

    # Informational Ghostscript stderr is not a failure. The exit and the file are.
    if not ghostscript_succeeded(completed.returncode, dest, min_bytes=64):
        raise RuntimeError(f"Ghostscript did not write the CMYK file (exit {completed.returncode}).")


def _page_proxy(page, max_px: int = 500) -> np.ndarray:
    import pymupdf as fitz
    rect = page.rect
    scale = max_px / max(rect.width, rect.height, 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


# One device pixel at 300 DPI. Edge replication samples this radius only.
_EDGE_PIXEL_PT = 72.0 / 300.0
# Drawn on top of the live page so a renderer cannot leave a white hairline.
_SEAM_COVER_PT = _EDGE_PIXEL_PT * 2


def _pixmap_cmyk(page, clip):
    """DeviceCMYK samples. An RGB round-trip turns a navy edge into a grey band."""
    import pymupdf as fitz

    if clip is None or clip.width < 0.15 or clip.height < 0.15:
        return None
    pix = page.get_pixmap(
        matrix=fitz.Matrix(300.0 / 72.0, 300.0 / 72.0),
        clip=clip,
        alpha=False,
        colorspace=fitz.csCMYK,
    )
    if pix.width < 1 or pix.height < 1 or pix.n < 4:
        return None
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return np.ascontiguousarray(arr[:, :, :4])


def _insert_cmyk(page, cmyk, rect) -> None:
    if cmyk is None or rect is None or rect.width < 0.2 or rect.height < 0.2:
        return
    target_w = max(1, int(round(rect.width / 72.0 * 300.0)))
    target_h = max(1, int(round(rect.height / 72.0 * 300.0)))
    if cmyk.shape[1] != target_w or cmyk.shape[0] != target_h:
        cmyk = cv2.resize(cmyk, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(cmyk), mode="CMYK").save(
        buf, format="TIFF", compression="raw", dpi=(300, 300),
    )
    page.insert_image(rect, stream=buf.getvalue())


# Skip a 1–2 px light or anti-aliased line, then read a few pixels further in.
_FRINGE_PX = 2
_INSET_SAMPLE_PX = 6


def _drop_light_fringe(sample: np.ndarray) -> np.ndarray:
    """Column 0 is the page edge. A near-white 1–2 px line is replaced by the ink behind it."""
    if sample is None or sample.ndim != 3 or sample.shape[1] < 2:
        return sample
    cleaned = np.array(sample, copy=True)
    ink = cleaned.astype(np.int16).sum(axis=2)
    depth = cleaned.shape[1]
    for col in range(min(2, depth - 1)):
        deeper = min(col + 2, depth - 1)
        pale = (ink[:, col] < 55) & (ink[:, col] + 25 < ink[:, deeper])
        cleaned[pale, col] = cleaned[pale, deeper]
    return cleaned


def _pingpong(length: int, depth: int) -> np.ndarray:
    if depth <= 1:
        return np.zeros(max(0, length), np.int32)
    cycle = depth * 2 - 2
    pos = np.mod(np.arange(length), cycle)
    return np.where(pos < depth, pos, cycle - pos).astype(np.int32)


def _extend_from_seam(sample: np.ndarray, out_px: int, seam_at_end: bool) -> np.ndarray:
    """Mirror a strip whose column 0 faces the seam. Blur only the added columns."""
    if sample is None or sample.size == 0 or out_px < 1:
        return None
    cleaned = _drop_light_fringe(sample)
    mirrored = cleaned[:, _pingpong(out_px, cleaned.shape[1]), :]
    if seam_at_end:
        mirrored = mirrored[:, ::-1, :]
    blurred = cv2.GaussianBlur(np.ascontiguousarray(mirrored), (3, 3), 0.8)
    cover = min(2, out_px)
    if seam_at_end:
        blurred[:, -cover:, :] = mirrored[:, -cover:, :]
    else:
        blurred[:, :cover, :] = mirrored[:, :cover, :]
    return np.ascontiguousarray(blurred)


def _extend_rows_from_seam(sample: np.ndarray, out_px: int, seam_at_end: bool) -> np.ndarray:
    """sample row 0 faces the seam. Result rows run across the margin."""
    if sample is None or sample.size == 0:
        return None
    swapped = np.ascontiguousarray(np.swapaxes(sample, 0, 1))
    extended = _extend_from_seam(swapped, out_px, seam_at_end)
    if extended is None:
        return None
    return np.ascontiguousarray(np.swapaxes(extended, 0, 1))


def _extend_corner(patch: np.ndarray, out_h: int, out_w: int, source_row_end: bool, source_col_end: bool, output_row_end: bool, output_col_end: bool) -> np.ndarray:
    """Mirror a patch out from the artwork corner. The seam sides are not blurred."""
    if patch is None or patch.size == 0 or out_h < 1 or out_w < 1:
        return None
    src = patch[::-1, :, :] if source_row_end else patch
    src = src[:, ::-1, :] if source_col_end else src
    src = _drop_light_fringe(src)
    src = np.swapaxes(_drop_light_fringe(np.swapaxes(src, 0, 1)), 0, 1)
    filled = src[_pingpong(out_h, src.shape[0])[:, None], _pingpong(out_w, src.shape[1])[None, :], :]
    if output_row_end:
        filled = filled[::-1, :, :]
    if output_col_end:
        filled = filled[:, ::-1, :]
    filled = np.ascontiguousarray(filled)
    blurred = cv2.GaussianBlur(filled, (3, 3), 0.8)
    cover = min(2, out_h, out_w)
    if output_row_end:
        blurred[-cover:, :, :] = filled[-cover:, :, :]
    else:
        blurred[:cover, :, :] = filled[:cover, :, :]
    if output_col_end:
        blurred[:, -cover:, :] = filled[:, -cover:, :]
    else:
        blurred[:, :cover, :] = filled[:, :cover, :]
    return np.ascontiguousarray(blurred)


def _fit_length(sample: np.ndarray, length: int, along_rows: bool) -> np.ndarray:
    if sample is None:
        return None
    if along_rows:
        if sample.shape[0] == length:
            return sample
        return cv2.resize(sample, (sample.shape[1], length), interpolation=cv2.INTER_LINEAR)
    if sample.shape[1] == length:
        return sample
    return cv2.resize(sample, (length, sample.shape[0]), interpolation=cv2.INTER_LINEAR)


def _paint_cmyk_edge(page, placed) -> None:
    """Continue each edge from a sample a few pixels inside the page.

    A 1–2 px light or anti-aliased line on the existing bleed is ignored.
    The margin is a per-row or per-column mirror of that inset sample, with a
    slight blur only in the added zone. Two pixels of the clean ink cover the
    join so the light line is not left as a hairline.
    """
    import pymupdf as fitz

    full = page.rect
    pixel = _EDGE_PIXEL_PT
    cover = _SEAM_COVER_PT
    fringe = pixel * _FRINGE_PX
    sample_span = pixel * _INSET_SAMPLE_PX
    placed = fitz.Rect(placed)
    if placed.width < 1 or placed.height < 1:
        return
    gaps = {
        "left": max(0.0, placed.x0 - full.x0),
        "right": max(0.0, full.x1 - placed.x1),
        "top": max(0.0, placed.y0 - full.y0),
        "bottom": max(0.0, full.y1 - placed.y1),
    }
    if max(gaps.values()) < 0.3:
        return

    def clip_of(box):
        rect = fitz.Rect(*box) & page.rect
        if rect.width < 0.12 or rect.height < 0.12:
            return None
        return rect

    def grab(box):
        return _pixmap_cmyk(page, clip_of(box))

    scale = 300.0 / 72.0
    if gaps["left"] > 0.3:
        rect = fitz.Rect(full.x0, placed.y0, placed.x0 + cover, placed.y1)
        band = _fit_length(
            grab((placed.x0 + fringe, placed.y0, placed.x0 + fringe + sample_span, placed.y1)),
            max(1, int(round(rect.height * scale))),
            True,
        )
        _insert_cmyk(page, _extend_from_seam(band, max(1, int(round(rect.width * scale))), True), rect)
    if gaps["right"] > 0.3:
        rect = fitz.Rect(placed.x1 - cover, placed.y0, full.x1, placed.y1)
        band = grab((placed.x1 - fringe - sample_span, placed.y0, placed.x1 - fringe, placed.y1))
        if band is not None:
            band = band[:, ::-1, :]
        band = _fit_length(band, max(1, int(round(rect.height * scale))), True)
        _insert_cmyk(page, _extend_from_seam(band, max(1, int(round(rect.width * scale))), False), rect)
    if gaps["top"] > 0.3:
        rect = fitz.Rect(placed.x0, full.y0, placed.x1, placed.y0 + cover)
        band = _fit_length(
            grab((placed.x0, placed.y0 + fringe, placed.x1, placed.y0 + fringe + sample_span)),
            max(1, int(round(rect.width * scale))),
            False,
        )
        _insert_cmyk(page, _extend_rows_from_seam(band, max(1, int(round(rect.height * scale))), True), rect)
    if gaps["bottom"] > 0.3:
        rect = fitz.Rect(placed.x0, placed.y1 - cover, placed.x1, full.y1)
        band = grab((placed.x0, placed.y1 - fringe - sample_span, placed.x1, placed.y1 - fringe))
        if band is not None:
            band = band[::-1, :, :]
        band = _fit_length(band, max(1, int(round(rect.width * scale))), False)
        _insert_cmyk(page, _extend_rows_from_seam(band, max(1, int(round(rect.height * scale))), False), rect)

    if gaps["left"] > 0.3 and gaps["top"] > 0.3:
        rect = fitz.Rect(full.x0, full.y0, placed.x0 + cover, placed.y0 + cover)
        patch = grab((
            placed.x0 + fringe, placed.y0 + fringe,
            placed.x0 + fringe + sample_span, placed.y0 + fringe + sample_span,
        ))
        _insert_cmyk(page, _extend_corner(
            patch, max(1, int(round(rect.height * scale))), max(1, int(round(rect.width * scale))),
            False, False, True, True,
        ), rect)
    if gaps["right"] > 0.3 and gaps["top"] > 0.3:
        rect = fitz.Rect(placed.x1 - cover, full.y0, full.x1, placed.y0 + cover)
        patch = grab((
            placed.x1 - fringe - sample_span, placed.y0 + fringe,
            placed.x1 - fringe, placed.y0 + fringe + sample_span,
        ))
        _insert_cmyk(page, _extend_corner(
            patch, max(1, int(round(rect.height * scale))), max(1, int(round(rect.width * scale))),
            False, True, True, False,
        ), rect)
    if gaps["left"] > 0.3 and gaps["bottom"] > 0.3:
        rect = fitz.Rect(full.x0, placed.y1 - cover, placed.x0 + cover, full.y1)
        patch = grab((
            placed.x0 + fringe, placed.y1 - fringe - sample_span,
            placed.x0 + fringe + sample_span, placed.y1 - fringe,
        ))
        _insert_cmyk(page, _extend_corner(
            patch, max(1, int(round(rect.height * scale))), max(1, int(round(rect.width * scale))),
            True, False, False, True,
        ), rect)
    if gaps["right"] > 0.3 and gaps["bottom"] > 0.3:
        rect = fitz.Rect(placed.x1 - cover, placed.y1 - cover, full.x1, full.y1)
        patch = grab((
            placed.x1 - fringe - sample_span, placed.y1 - fringe - sample_span,
            placed.x1 - fringe, placed.y1 - fringe,
        ))
        _insert_cmyk(page, _extend_corner(
            patch, max(1, int(round(rect.height * scale))), max(1, int(round(rect.width * scale))),
            True, True, False, False,
        ), rect)


def _vector_page_bleed(page, trim_w_mm: float, trim_h_mm: float) -> tuple[dict, str]:
    """Box inset when a TrimBox exists, otherwise the size match when every box is equal."""
    media = page.mediabox
    trim = page.trimbox if page.trimbox.width > 2 and page.trimbox.height > 2 else media
    boxes_differ = abs(trim.width - media.width) >= 1.5 or abs(trim.height - media.height) >= 1.5
    if boxes_differ:
        existing = {
            "left": max(0.0, (trim.x0 - media.x0) * 25.4 / 72.0),
            "bottom": max(0.0, (trim.y0 - media.y0) * 25.4 / 72.0),
            "right": max(0.0, (media.x1 - trim.x1) * 25.4 / 72.0),
            "top": max(0.0, (media.y1 - trim.y1) * 25.4 / 72.0),
        }
        trim_w = trim.width * 25.4 / 72.0
        trim_h = trim.height * 25.4 / 72.0
        if min(existing.values()) >= 1.5 and abs(trim_w - trim_w_mm) <= 2.0 and abs(trim_h - trim_h_mm) <= 2.0:
            return existing, "boxes"
        return {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}, "none"
    page_w = media.width * 25.4 / 72.0
    page_h = media.height * 25.4 / 72.0
    bleed = inferred_side_bleed(page_w, page_h, trim_w_mm, trim_h_mm)
    if bleed and bleed.get("kind") == "existing":
        return {side: float(bleed[side]) for side in ("left", "right", "top", "bottom")}, "partial"
    if bleed and bleed.get("kind") == "trim":
        return {side: float(bleed[side]) for side in ("left", "right", "top", "bottom")}, "trim"
    return {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}, "none"


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
    if src.page_count < 1:
        src.close()
        return {
            "used": False,
            "report": {
                "passed": False,
                "status": "needs-attention",
                "headline": "Needs attention",
                "reason": "The PDF has no pages.",
                "fix": "Upload the file again.",
                "edges": [],
            },
        }
    page = src[0]
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
    kept = False
    extended = False
    shrunk = False
    edge_placements = []
    for index in range(src.page_count):
        src_page = src[index]
        new_page = doc.new_page(width=out_w, height=out_h)
        existing, mode = _vector_page_bleed(src_page, trim_w_mm, trim_h_mm)
        have = min(existing.values()) if existing else 0.0
        trim_box = src_page.trimbox if src_page.trimbox.width > 2 else src_page.mediabox
        if mode == "boxes" and have + 0.4 >= bleed_mm:
            # Already 5 mm. Clip to that bleed. Do not add another ring and do not shrink.
            clip = fitz.Rect(
                trim_box.x0 - bleed_mm * MM_TO_PT,
                trim_box.y0 - bleed_mm * MM_TO_PT,
                trim_box.x1 + bleed_mm * MM_TO_PT,
                trim_box.y1 + bleed_mm * MM_TO_PT,
            )
            new_page.show_pdf_page(new_page.rect, src, index, clip=clip)
            kept = True
        elif mode in ("partial", "boxes") and have >= 2.0:
            # Keep the bleed that is already there. Mirror only the shortfall out to 5 mm.
            extra_l = max(0.0, existing["left"] * MM_TO_PT - bleed_pt)
            extra_r = max(0.0, existing["right"] * MM_TO_PT - bleed_pt)
            extra_t = max(0.0, existing["top"] * MM_TO_PT - bleed_pt)
            extra_b = max(0.0, existing["bottom"] * MM_TO_PT - bleed_pt)
            src_rect = src_page.rect
            clip = fitz.Rect(
                src_rect.x0 + extra_l,
                src_rect.y0 + extra_t,
                src_rect.x1 - extra_r,
                src_rect.y1 - extra_b,
            )
            short_l = max(0.0, bleed_pt - existing["left"] * MM_TO_PT)
            short_r = max(0.0, bleed_pt - existing["right"] * MM_TO_PT)
            short_t = max(0.0, bleed_pt - existing["top"] * MM_TO_PT)
            short_b = max(0.0, bleed_pt - existing["bottom"] * MM_TO_PT)
            placed = fitz.Rect(short_l, short_t, short_l + clip.width, short_t + clip.height)
            new_page.show_pdf_page(placed, src, index, clip=clip)
            if max(short_l, short_r, short_t, short_b) >= 0.4:
                extended = True
                edge_placements.append((placed.x0, placed.y0, placed.x1, placed.y1))
            else:
                kept = True
                edge_placements.append(None)
        else:
            dest = fitz.Rect(bleed_pt, bleed_pt, bleed_pt + trim_w_mm * MM_TO_PT, bleed_pt + trim_h_mm * MM_TO_PT)
            scale = 1.0
            if analysis.get("safeHits", 0) > 0:
                scale = 1.0 - min(SAFE_ZONE_SHRINK_CAP, 0.02)
                shrunk = scale < 0.999
            placed = dest
            if scale < 0.999:
                margin_x = dest.width * (1 - scale) / 2
                margin_y = dest.height * (1 - scale) / 2
                placed = fitz.Rect(dest.x0 + margin_x, dest.y0 + margin_y, dest.x1 - margin_x, dest.y1 - margin_y)
            src_trim = src_page.rect
            if abs(trim_box.width - src_page.mediabox.width) >= 1.5 or abs(trim_box.height - src_page.mediabox.height) >= 1.5:
                src_trim = trim_box
            cover = max(placed.width / max(src_trim.width, 1), placed.height / max(src_trim.height, 1))
            clip_w = placed.width / cover
            clip_h = placed.height / cover
            clip = fitz.Rect(
                src_trim.x0 + (src_trim.width - clip_w) / 2,
                src_trim.y0 + (src_trim.height - clip_h) / 2,
                src_trim.x0 + (src_trim.width - clip_w) / 2 + clip_w,
                src_trim.y0 + (src_trim.height - clip_h) / 2 + clip_h,
            )
            new_page.show_pdf_page(placed, src, index, clip=clip)
            edge_placements.append((placed.x0, placed.y0, placed.x1, placed.y1))
        if len(edge_placements) < index + 1:
            edge_placements.append(None)
        _set_boxes(new_page, trim_w_mm, trim_h_mm, bleed_mm)
    if kept and not extended:
        edges = _edge_lines(analysis, methods, kept=True)
    page_count = src.page_count
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
    for fixed_page, placement in zip(fixed, edge_placements):
        if placement:
            _paint_cmyk_edge(fixed_page, fitz.Rect(*placement))
        _set_boxes(fixed_page, trim_w_mm, trim_h_mm, bleed_mm)
    boxed = out_path + ".box.pdf"
    fixed.save(boxed, deflate=True, garbage=4)
    fixed.close()
    os.replace(boxed, out_path)

    full_keep = kept and not extended
    rescue = {
        "applied": shrunk,
        "scale": 0.98 if shrunk else 1.0,
        "safeZoneMm": safe_zone_mm,
        "note": "Text and vectors were kept live." if kind != "raster" else "The picture stays as placed. Only the bleed ring was added.",
    }
    report = _base_report(analysis, edges, full_keep, bleed_mm, safe_zone_mm, 0, 0, trim_w_mm, trim_h_mm)
    report["contentKind"] = kind
    report["analysis"]["contentKind"] = kind
    report["existingBleed"] = bool(kept or extended)
    report["fullBleedKept"] = full_keep
    report["rescue"] = rescue
    report["pageCount"] = page_count
    report["effectiveDpi"] = 300
    report["resolutionNote"] = "Vector artwork stays sharp at any size." if kind == "vector" else "The placed artwork was kept. The bleed ring is 300 DPI."
    report["allowWhite"] = False
    report = preflight_pdf(out_path, trim_w_mm, trim_h_mm, bleed_mm, report, require_text="")
    return {"used": True, "report": report, "path": out_path, "pageCount": page_count}


def summary_sentence(report: dict) -> str:
    if not report:
        return ""
    if report.get("status") == "needs-attention":
        return f"Needs attention. {report.get('reason', '')} {report.get('fix', '')}".strip()
    parts = [edge.get("note", "") for edge in report.get("edges") or []]
    rescue = (report.get("rescue") or {}).get("note", "")
    existing = " Existing bleed was kept." if report.get("existingBleed") else ""
    return ("Ready for press. " + " ".join(parts) + " " + rescue + existing).strip()


def _srgb_lab(rgb) -> np.ndarray:
    """CIE Lab from a mean sRGB triple. Used for seam delta E."""
    channel = np.asarray(rgb, dtype=np.float64) / 255.0

    def linear(value):
        return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)

    red, green, blue = linear(channel)
    x = red * 0.4124564 + green * 0.3575761 + blue * 0.1804375
    y = red * 0.2126729 + green * 0.7151522 + blue * 0.0721750
    z = red * 0.0193339 + green * 0.1191920 + blue * 0.9503041
    white = (0.95047, 1.0, 1.08883)

    def pivot(value):
        return np.where(value > 0.008856, np.cbrt(value), 7.787 * value + 16.0 / 116.0)

    fx, fy, fz = (pivot(x / white[0]), pivot(y / white[1]), pivot(z / white[2]))
    return np.array([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], dtype=np.float64)


def _delta_e(left, right) -> float:
    gap = _srgb_lab(left) - _srgb_lab(right)
    return float(np.sqrt(np.sum(gap * gap)))


def _delta_e00(left, right) -> float:
    """CIEDE2000 between two mean sRGB colours. Local seam windows use this."""
    import math

    lab1 = _srgb_lab(left)
    lab2 = _srgb_lab(right)
    L1, a1, b1 = (float(lab1[0]), float(lab1[1]), float(lab1[2]))
    L2, a2, b2 = (float(lab2[0]), float(lab2[1]), float(lab2[2]))
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    c7 = Cbar ** 7
    G = 0.5 * (1.0 - math.sqrt(c7 / (c7 + 25.0 ** 7)))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = math.hypot(a1p, b1)
    C2p = math.hypot(a2p, b2)

    def hue(b_value, a_value):
        return math.degrees(math.atan2(b_value, a_value)) % 360.0

    h1p = hue(b1, a1p)
    h2p = hue(b2, a2p)
    dL = L2 - L1
    dC = C2p - C1p
    if C1p * C2p == 0.0:
        dh = 0.0
        hbar = h1p + h2p
    else:
        dh = h2p - h1p
        if dh > 180.0:
            dh -= 360.0
        elif dh < -180.0:
            dh += 360.0
        hsum = h1p + h2p
        if abs(h1p - h2p) > 180.0:
            hbar = (hsum + 360.0) / 2.0 if hsum < 360.0 else (hsum - 360.0) / 2.0
        else:
            hbar = hsum / 2.0
    dH = 2.0 * math.sqrt(max(C1p * C2p, 0.0)) * math.sin(math.radians(dh / 2.0))
    Lbar = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    T = (
        1.0
        - 0.17 * math.cos(math.radians(hbar - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * hbar))
        + 0.32 * math.cos(math.radians(3.0 * hbar + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * hbar - 63.0))
    )
    dtheta = 30.0 * math.exp(-((hbar - 275.0) / 25.0) ** 2)
    cp7 = Cbarp ** 7
    Rc = 2.0 * math.sqrt(cp7 / (cp7 + 25.0 ** 7))
    Sl = 1.0 + (0.015 * (Lbar - 50.0) ** 2) / math.sqrt(20.0 + (Lbar - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbarp
    Sh = 1.0 + 0.015 * Cbarp * T
    Rt = -math.sin(math.radians(2.0 * dtheta)) * Rc
    return math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh))


def _window_max_delta_e(rgb: np.ndarray, outside, inside, axis: str) -> float | None:
    """Max CIEDE2000 of sliding 2 mm windows. outside/inside are y0,y1,x0,x1."""
    oy0, oy1, ox0, ox1 = outside
    iy0, iy1, ix0, ix1 = inside
    height, width = rgb.shape[:2]
    oy0, ox0 = max(0, int(oy0)), max(0, int(ox0))
    iy0, ix0 = max(0, int(iy0)), max(0, int(ix0))
    oy1, ox1 = min(height, int(oy1)), min(width, int(ox1))
    iy1, ix1 = min(height, int(iy1)), min(width, int(ix1))
    if oy1 <= oy0 or ox1 <= ox0 or iy1 <= iy0 or ix1 <= ix0:
        return None
    window = max(4, int(round(2.0 / 25.4 * 300.0)))
    step = max(1, window // 4)
    worst = 0.0
    found = False
    if axis == "y":
        limit = min(oy1 - oy0, iy1 - iy0)
        if limit < 2:
            return None
        span = min(window, limit)
        for start in range(0, max(1, limit - span + 1), step):
            out_mean = rgb[oy0 + start:oy0 + start + span, ox0:ox1].reshape(-1, 3).mean(axis=0)
            in_mean = rgb[iy0 + start:iy0 + start + span, ix0:ix1].reshape(-1, 3).mean(axis=0)
            worst = max(worst, _delta_e00(out_mean, in_mean))
            found = True
    else:
        limit = min(ox1 - ox0, ix1 - ix0)
        if limit < 2:
            return None
        span = min(window, limit)
        for start in range(0, max(1, limit - span + 1), step):
            out_mean = rgb[oy0:oy1, ox0 + start:ox0 + start + span].reshape(-1, 3).mean(axis=0)
            in_mean = rgb[iy0:iy1, ix0 + start:ix0 + start + span].reshape(-1, 3).mean(axis=0)
            worst = max(worst, _delta_e00(out_mean, in_mean))
            found = True
    return round(worst, 2) if found else None


def _band_mean(image: np.ndarray, box) -> np.ndarray | None:
    y0, y1, x0, x1 = box
    y0, x0 = max(0, int(y0)), max(0, int(x0))
    y1, x1 = min(image.shape[0], int(y1)), min(image.shape[1], int(x1))
    if y1 <= y0 or x1 <= x0:
        return None
    return image[y0:y1, x0:x1].reshape(-1, image.shape[2]).mean(axis=0)


def edge_seam_delta_e(path: str, seam_x_pt: float, seam_y_pt: float, dpi: float = 300.0) -> list:
    """CIE76 between the ink just inside the seam and the extension just outside it.

    seam_x_pt / seam_y_pt are the distance from the media edge to the join.
    One row per page: left, right, top, bottom, and the four corners.
    """
    import pymupdf as fitz

    doc = fitz.open(path)
    rows = []
    try:
        scale = float(dpi) / 72.0
        band = 6
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
            height, width = rgb.shape[:2]
            sx = int(round(float(seam_x_pt) * scale))
            sy = int(round(float(seam_y_pt) * scale))
            sx = min(max(sx, band + 1), width - band - 1)
            sy = min(max(sy, band + 1), height - band - 1)
            pairs = {
                "left": ((sy, height - sy, sx - band, sx), (sy, height - sy, sx, sx + band)),
                "right": ((sy, height - sy, width - sx, width - sx + band), (sy, height - sy, width - sx - band, width - sx)),
                "top": ((sy - band, sy, sx, width - sx), (sy, sy + band, sx, width - sx)),
                "bottom": ((height - sy, height - sy + band, sx, width - sx), (height - sy - band, height - sy, sx, width - sx)),
            }
            # outside, inside. A white hairline sits in the outside band and moves the mean.
            edges = {}
            for name, (outside, inside) in pairs.items():
                out_mean = _band_mean(rgb, outside)
                in_mean = _band_mean(rgb, inside)
                edges[name] = None if out_mean is None or in_mean is None else round(_delta_e(out_mean, in_mean), 2)
            corners = {
                "tl": ((0, sy, 0, sx), (sy, sy + band, sx, sx + band)),
                "tr": ((0, sy, width - sx, width), (sy, sy + band, width - sx - band, width - sx)),
                "bl": ((height - sy, height, 0, sx), (height - sy - band, height - sy, sx, sx + band)),
                "br": ((height - sy, height, width - sx, width), (height - sy - band, height - sy, width - sx - band, width - sx)),
            }
            corner_de = {}
            for name, (outside, inside) in corners.items():
                out_mean = _band_mean(rgb, outside)
                in_mean = _band_mean(rgb, inside)
                corner_de[name] = None if out_mean is None or in_mean is None else round(_delta_e(out_mean, in_mean), 2)
            skip = 2
            local_edges = {
                "left": _window_max_delta_e(
                    rgb, (sy, height - sy, max(0, sx - band), sx), (sy, height - sy, sx + skip, sx + skip + band), "y",
                ),
                "right": _window_max_delta_e(
                    rgb,
                    (sy, height - sy, width - sx, min(width, width - sx + band)),
                    (sy, height - sy, width - sx - skip - band, width - sx - skip),
                    "y",
                ),
                "top": _window_max_delta_e(
                    rgb, (max(0, sy - band), sy, sx, width - sx), (sy + skip, sy + skip + band, sx, width - sx), "x",
                ),
                "bottom": _window_max_delta_e(
                    rgb,
                    (height - sy, min(height, height - sy + band), sx, width - sx),
                    (height - sy - skip - band, height - sy - skip, sx, width - sx),
                    "x",
                ),
            }
            win = max(4, int(round(2.0 / 25.4 * float(dpi))))
            local_corners = {
                "tl": _window_max_delta_e(rgb, (0, sy, 0, sx), (sy + skip, sy + skip + win, sx + skip, sx + skip + win), "y"),
                "tr": _window_max_delta_e(
                    rgb, (0, sy, width - sx, width), (sy + skip, sy + skip + win, width - sx - skip - win, width - sx - skip), "y",
                ),
                "bl": _window_max_delta_e(
                    rgb, (height - sy, height, 0, sx), (height - sy - skip - win, height - sy - skip, sx + skip, sx + skip + win), "y",
                ),
                "br": _window_max_delta_e(
                    rgb,
                    (height - sy, height, width - sx, width),
                    (height - sy - skip - win, height - sy - skip, width - sx - skip - win, width - sx - skip),
                    "y",
                ),
            }
            rows.append({
                "page": index + 1,
                **edges,
                "corners": corner_de,
                "local": {**local_edges, **local_corners},
            })
        return rows
    finally:
        doc.close()
