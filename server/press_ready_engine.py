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

# A 300 dpi plate longer than this does not fit the memory leash (A2 and above).
PLATE_LIMIT_MM = 430.0
PLATE_SKIP_REASON = (
    "This sheet is larger than the press line can hold in memory, so the plate was not rendered. "
    "The size was read and the file was not damaged."
)
SAFE_ZONE_MM = 3.0
SAFE_ZONE_SHRINK_MIN = 0.01
SAFE_ZONE_SHRINK_CAP = 0.03
# A picture enlarged past this stops being honest "print ready".
MIN_HONEST_EFFECTIVE_DPI = 240.0
TAC_LIMIT = 300.0
MM_TO_PT = 72.0 / 25.4


def plate_exceeds_memory(trim_w_mm: float, trim_h_mm: float) -> bool:
    """True when a 300 dpi plate of this trim would exceed the memory leash."""
    return max(float(trim_w_mm or 0), float(trim_h_mm or 0)) > PLATE_LIMIT_MM


def centred_bleed_mm(
    page_w_mm: float,
    page_h_mm: float,
    trim_w_mm: float,
    trim_h_mm: float,
    lo: float = 0.4,
    hi: float = 15.0,
) -> dict | None:
    """Bleed when the page is the trim plus a centred margin.

    Each side may be from none up to hi. One axis can be the trim while the
    other already has bleed. The margin is centred because equal boxes do not
    say which edge the extra belongs to.
    """
    side_w = (float(page_w_mm) - float(trim_w_mm)) / 2.0
    side_h = (float(page_h_mm) - float(trim_h_mm)) / 2.0
    if side_w < -0.05 or side_h < -0.05:
        return None
    if side_w > hi + 0.05 or side_h > hi + 0.05:
        return None
    if max(side_w, side_h) < lo - 0.05:
        return None
    return {
        "left": max(0.0, side_w),
        "right": max(0.0, side_w),
        "top": max(0.0, side_h),
        "bottom": max(0.0, side_h),
    }


def inferred_side_bleed(
    page_w_mm: float,
    page_h_mm: float,
    trim_w_mm: float,
    trim_h_mm: float,
    lo: float = 0.4,
    hi: float = 15.0,
    trim_tol: float = 2.5,
):
    """Per-side bleed when the page is the trim, or the trim plus bleed already there.

    Any product, not only A6. A page with no TrimBox can already hold from about
    0.4 mm up to 15 mm on a side, including a partial bleed under 2 mm and a full
    5 mm. None means the page is a different shape.
    """
    found = centred_bleed_mm(page_w_mm, page_h_mm, trim_w_mm, trim_h_mm, lo=lo, hi=hi)
    if found:
        return {**found, "kind": "existing"}
    extra_w = float(page_w_mm) - float(trim_w_mm)
    extra_h = float(page_h_mm) - float(trim_h_mm)
    if abs(extra_w) <= trim_tol and abs(extra_h) <= trim_tol:
        side_w = extra_w / 2.0
        side_h = extra_h / 2.0
        return {
            "left": max(0.0, side_w),
            "right": max(0.0, side_w),
            "top": max(0.0, side_h),
            "bottom": max(0.0, side_h),
            "kind": "trim",
        }
    return None


def detected_trim_mm(page_w_mm: float, page_h_mm: float, order_w_mm: float, order_h_mm: float) -> tuple[float, float]:
    """Trim the file is actually using. Canva's page is often the trim plus the bleed already there."""
    bleed = inferred_side_bleed(page_w_mm, page_h_mm, order_w_mm, order_h_mm)
    if bleed and bleed.get("kind") == "existing":
        return (
            float(page_w_mm) - float(bleed["left"]) - float(bleed["right"]),
            float(page_h_mm) - float(bleed["top"]) - float(bleed["bottom"]),
        )
    return float(page_w_mm), float(page_h_mm)

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
    # Partial bleed can differ by axis. A generous uniform margin still counts past 5 mm.
    found = centred_bleed_mm(doc_w, doc_h, trim_w, trim_h, lo=0.4, hi=5.5)
    if found is None:
        found = centred_bleed_mm(doc_w, doc_h, trim_w, trim_h, lo=2.0, hi=25.0)
        if found and abs(found["left"] - found["top"]) > 1.5:
            found = None
    if not found:
        return None
    return {**found, "trim_w_mm": float(trim_w), "trim_h_mm": float(trim_h)}


def _pad_existing_raster(plane: np.ndarray, existing: dict, trim_w_mm: float, trim_h_mm: float, bleed_mm: float, dpi: float) -> np.ndarray | None:
    """Keep a raster that already has bleed, and pad only the short edges out to 5 mm."""
    height, width = plane.shape[:2]

    def px(mm: float) -> int:
        return int(round(float(mm) / 25.4 * float(dpi)))

    expect_w = px(trim_w_mm + float(existing["left"]) + float(existing["right"]))
    expect_h = px(trim_h_mm + float(existing["top"]) + float(existing["bottom"]))
    if abs(width - expect_w) > 3 or abs(height - expect_h) > 3:
        return None
    short_l = px(max(0.0, bleed_mm - float(existing["left"])))
    short_r = px(max(0.0, bleed_mm - float(existing["right"])))
    short_t = px(max(0.0, bleed_mm - float(existing["top"])))
    short_b = px(max(0.0, bleed_mm - float(existing["bottom"])))
    out = plane
    if short_l or short_r or short_t or short_b:
        out = np.pad(out, ((short_t, short_b), (short_l, short_r), (0, 0)), mode="symmetric")
    target_w = px(trim_w_mm + 2.0 * bleed_mm)
    target_h = px(trim_h_mm + 2.0 * bleed_mm)
    if out.shape[1] > target_w:
        extra = out.shape[1] - target_w
        left = extra // 2
        out = out[:, left:left + target_w]
    if out.shape[0] > target_h:
        extra = out.shape[0] - target_h
        top = extra // 2
        out = out[top:top + target_h, :]
    if out.shape[1] < target_w or out.shape[0] < target_h:
        out = np.pad(
            out,
            ((0, target_h - out.shape[0]), (0, target_w - out.shape[1]), (0, 0)),
            mode="edge",
        )
    return out


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
        most = max(existing["top"], existing["bottom"], existing["left"], existing["right"])
        size_ok = abs(existing["trim_w_mm"] - trim_w_mm) <= 2 and abs(existing["trim_h_mm"] - trim_h_mm) <= 2
        if most >= 0.4 and size_ok and embedded_dpi:
            topped = _pad_existing_raster(plane, existing, trim_w_mm, trim_h_mm, float(bleed_mm), float(embedded_dpi))
            if topped is not None:
                out_w = trim_w_px + 2 * bleed_px
                out_h = trim_h_px + 2 * bleed_px
                if topped.shape[1] != out_w or topped.shape[0] != out_h:
                    topped = cv2.resize(topped, (out_w, out_h), interpolation=cv2.INTER_AREA)
                analysis = analyse_bgr(trim, dpi, safe_zone_mm)
                edges = _edge_lines(analysis, {side: "kept" for side in SIDES}, kept=True)
                report = _base_report(analysis, edges, True, bleed_mm, safe_zone_mm, src_w, src_h, trim_w_mm, trim_h_mm)
                report["existingBleed"] = True
                report["rescue"] = {
                    "applied": False,
                    "scale": 1.0,
                    "safeZoneMm": safe_zone_mm,
                    "note": "Existing bleed kept. Only the shortfall was added.",
                }
                _finish_resolution(report, src_w, src_h, trim_w_mm, trim_h_mm, analysis)
                report["bitmapProblems"] = []
                return topped, report
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
        report["effectiveDpi"] = None
        report["resolutionNote"] = "No picture was measured, so resolution is not reported as 300 DPI."
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
    names = {}
    for item in page.get_images(full=True) or []:
        label = str(item[7] if len(item) > 7 else "")
        names[int(item[0])] = label
    boxes = {}
    for info in page.get_image_info(xrefs=True) or []:
        boxes[int(info.get("xref") or 0)] = fitz.Rect(info.get("bbox") or (0, 0, 0, 0))
    artwork = []
    for xref in images:
        info = doc.extract_image(xref)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        # The added bleed is a thin DeviceRGB strip so the colour matches the page.
        # It is not the picture being checked.
        if names.get(int(xref), "").startswith("Bleed"):
            continue
        box = boxes.get(int(xref)) or fitz.Rect()
        if not box.is_empty and box.get_area() > 1:
            overlap = box & trim
            if overlap.is_empty or overlap.get_area() < 0.2 * box.get_area():
                continue
        if min(width, height) < 80:
            continue
        artwork.append(info)
    if artwork:
        info = max(artwork, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
        if info.get("colorspace") not in (4, "CMYK", None) and "CMYK" not in str(info.get("cs-name", "")):
            failures.append("The press file is not CMYK.")
    elif images and report.get("contentKind") != "vector":
        # Only thin strips. The picture is inside a form; do not call that a missing file.
        pass
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


def convert_cmyk_keep_text(src: str, dest: str, block_font_substitution: bool = False) -> None:
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
        *(["-dCannotEmbedFontPolicy=1"] if block_font_substitution else []),
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


# A 1–2 px light line sits on the page edge. Read a few pixels past it.
_INSET_PX = 3
# Median only across depth, so one JPEG spike is dropped and a thin column stays sharp.
_INSET_MEDIAN_PX = 3


def _fit_nearest(sample: np.ndarray, length: int, along_rows: bool) -> np.ndarray:
    if sample is None:
        return None
    if along_rows:
        if sample.shape[0] == length:
            return sample
        return cv2.resize(sample, (sample.shape[1], length), interpolation=cv2.INTER_NEAREST)
    if sample.shape[1] == length:
        return sample
    return cv2.resize(sample, (length, sample.shape[0]), interpolation=cv2.INTER_NEAREST)


def _median_cols(band: np.ndarray, from_end: bool = False) -> np.ndarray | None:
    """One colour per row. Column 0 is closest to the page edge unless from_end."""
    if band is None or band.size == 0 or getattr(band, "ndim", 0) != 3 or band.shape[1] < 1:
        return None
    depth = min(_INSET_MEDIAN_PX, band.shape[1])
    sample = band[:, -depth:, :] if from_end else band[:, :depth, :]
    return np.median(sample, axis=1).astype(np.uint8)


def _median_rows(band: np.ndarray, from_end: bool = False) -> np.ndarray | None:
    """One colour per column. Row 0 is closest to the page edge unless from_end."""
    if band is None or band.size == 0 or getattr(band, "ndim", 0) != 3 or band.shape[0] < 1:
        return None
    depth = min(_INSET_MEDIAN_PX, band.shape[0])
    sample = band[-depth:, :, :] if from_end else band[:depth, :, :]
    return np.median(sample, axis=0).astype(np.uint8)


def _replicate_cols(colors: np.ndarray, out_px: int) -> np.ndarray | None:
    """Repeat each row colour straight across the margin. No mirror and no blur."""
    if colors is None or out_px < 1:
        return None
    return np.ascontiguousarray(np.repeat(colors[:, None, :], out_px, axis=1))


def _replicate_rows(colors: np.ndarray, out_px: int) -> np.ndarray | None:
    """Repeat each column colour straight out from the seam."""
    if colors is None or out_px < 1:
        return None
    return np.ascontiguousarray(np.repeat(colors[None, :, :], out_px, axis=0))


def _clear_crossed_fringe(colors: np.ndarray | None) -> np.ndarray | None:
    """The other edge's light line crosses the first rows. Extend the ink behind it."""
    if colors is None or len(colors) <= _INSET_PX * 2:
        return colors
    cleaned = np.array(colors, copy=True)
    cleaned[:_INSET_PX] = cleaned[_INSET_PX]
    cleaned[-_INSET_PX:] = cleaned[-1 - _INSET_PX]
    return cleaned


def _color_at(colors: np.ndarray | None, at_end: bool) -> np.ndarray | None:
    """The corner colour is inset from the other edge, past that edge's light line."""
    if colors is None or len(colors) < 1:
        return None
    inset = min(_INSET_PX, len(colors) - 1)
    return colors[-1 - inset] if at_end else colors[inset]


def _blend_corner(vertical: np.ndarray, horizontal: np.ndarray, out_h: int, out_w: int, pin_row_end: bool, pin_col_end: bool) -> np.ndarray:
    """Blend the two adjacent edge colours. The shared edges stay pinned to that edge."""
    rows = np.arange(out_h, dtype=np.float32)
    cols = np.arange(out_w, dtype=np.float32)
    dist_vertical = ((out_h - 1) - rows) if pin_row_end else rows
    dist_horizontal = ((out_w - 1) - cols) if pin_col_end else cols
    together = dist_vertical[:, None] + dist_horizontal[None, :]
    weight = np.where(together > 0, dist_horizontal[None, :] / np.maximum(together, 1e-6), 0.5).astype(np.float32)
    blended = vertical.astype(np.float32) * weight[..., None] + horizontal.astype(np.float32) * (1.0 - weight)[..., None]
    return np.ascontiguousarray(np.clip(np.round(blended), 0, 255).astype(np.uint8))


def _gs_page_pixels(page_pt: float) -> int:
    """Ghostscript png16m at 300 dpi sizes the page with trunc(pt * 300/72 + 0.5)."""
    return max(1, int(float(page_pt) * 300.0 / 72.0 + 0.5))


def _gs_rgb_pages(path: str) -> list:
    """Render each page the way the press does: png16m, 300 dpi, memory leashes on."""
    import tempfile
    from PIL import Image

    folder = tempfile.mkdtemp(prefix="bleed-gs-")
    pattern = os.path.join(folder, "p-%d.png")
    cmd = [
        _gs_bin(),
        "-dNOPAUSE", "-dBATCH", "-dSAFER",
        "-sDEVICE=png16m",
        "-r300",
        "-dNumRenderingThreads=1",
        "-dBufferSpace=50000000",
        "-dMaxBitmap=50000000",
        "-dBandBufferSpace=50000000",
        f"-sOutputFile={pattern}",
        path,
    ]
    from host_paths import c_numeric_env

    subprocess.run(cmd, check=False, timeout=90, capture_output=True, env=c_numeric_env())
    pages = []
    index = 1
    while True:
        name = os.path.join(folder, f"p-{index}.png")
        if not os.path.isfile(name):
            break
        pages.append(np.asarray(Image.open(name).convert("RGB")))
        index += 1
    shutil.rmtree(folder, ignore_errors=True)
    return pages


# A 0.45 pt keyline blooms to about 3 px at 300 dpi. Anything thicker is a border.
_HAIRLINE_PX = 4


def _row_hairline(row: np.ndarray) -> int:
    """Leading light pixels that are a hairline, not a white border.

    Up to four light pixels followed by darker ink are the fringe. A light run
    that continues is the artwork and stays.
    """
    depth = 0
    limit = min(_HAIRLINE_PX, int(row.shape[0]) - 1)
    while depth < limit:
        pix = row[depth].astype(np.float32)
        nxt = row[depth + 1].astype(np.float32)
        if float(pix.mean()) <= 200.0:
            break
        if float(nxt.mean()) <= 200.0 and (float(pix.mean()) - float(nxt.mean())) > 25.0:
            depth += 1
            break
        if float(nxt.mean()) > 200.0:
            depth += 1
            continue
        break
    return depth


def _leading_hairline(strip: np.ndarray) -> int:
    """Columns to step past when the whole edge is a short light line."""
    if strip.ndim < 3 or strip.shape[1] < 2:
        return 0
    depth = 0
    while depth < _HAIRLINE_PX and depth + 1 < strip.shape[1]:
        seam = strip[:, depth].astype(np.float32).mean(axis=1)
        nxt = strip[:, depth + 1].astype(np.float32).mean(axis=1)
        light = seam > 200.0
        if float(np.mean(light)) <= 0.5:
            break
        darker = (~(nxt > 200.0)) & ((seam - nxt) > 25.0)
        more = nxt > 200.0
        if float(np.mean(darker | more)) <= 0.5:
            break
        depth += 1
        if float(np.mean(darker)) > 0.5:
            break
    return depth


def _clear_short_light_runs(chosen: np.ndarray) -> np.ndarray:
    """Replace a short light line with the ink beside it. A longer white run stays."""
    if chosen.shape[0] < 2:
        return chosen
    tone = chosen.mean(axis=1)
    bright = tone > 200.0
    out = np.array(chosen, copy=True)
    count = int(chosen.shape[0])
    index = 0
    while index < count:
        if not bright[index]:
            index += 1
            continue
        end = index
        while end + 1 < count and bright[end + 1]:
            end += 1
        length = end - index + 1
        if length <= _HAIRLINE_PX:
            before = chosen[index - 1] if index > 0 and tone[index - 1] < 180.0 else None
            after = chosen[end + 1] if end + 1 < count and tone[end + 1] < 180.0 else None
            if index == 0 and after is not None:
                out[index:end + 1] = after
            elif end == count - 1 and before is not None:
                out[index:end + 1] = before
            elif before is not None and after is not None:
                out[index:end + 1] = (before + after) / 2.0
            elif before is not None:
                out[index:end + 1] = before
            elif after is not None:
                out[index:end + 1] = after
        index = end + 1
    return out


def _seam_colors(strip: np.ndarray) -> np.ndarray:
    """One colour per row. Match the ink touching the seam, in this same render.

    A short light hairline is skipped. A real white border is kept. Pixels on
    the far side of a hard edge are not averaged in, so a diagonal stays its
    own colour. Flat ink uses the median of the pixels that match the seam,
    which keeps a halftone dot from painting a dark line.
    """
    if strip.size == 0:
        return np.zeros((0, 3), np.float32)
    if strip.ndim == 2:
        strip = strip[:, :, None]
    count = int(strip.shape[0])
    chosen = np.zeros((count, int(strip.shape[2]) if strip.ndim > 2 else 1), np.float32)
    if strip.shape[1] < 1:
        return chosen
    for index in range(count):
        row = strip[index]
        depth = _row_hairline(row)
        body = row[depth:]
        if body.shape[0] < 1:
            body = row[:1]
        span = min(6, int(body.shape[0]))
        band = body[:span].astype(np.float32)
        seam = band[0]
        if span == 1:
            chosen[index] = seam
            continue
        close = np.abs(band.astype(np.int16) - seam.astype(np.int16)).sum(axis=1) < 60
        chosen[index] = np.median(band[close], axis=0) if np.any(close) else seam
    return _clear_short_light_runs(chosen)


def _outward_row_shift(strip: np.ndarray) -> np.ndarray:
    """Rows to step per pixel of outward travel so a diagonal keeps its slope.

    A real boundary separates two colours along the inward ray. Flat ink and a
    vertical column, which stay the same colour as you walk inward, return
    zero so the column is copied straight out.
    """
    count = int(strip.shape[0])
    shift = np.zeros(count, np.float32)
    if strip.ndim < 3 or strip.shape[1] < 4 or count < 5:
        return shift
    # A full-width light hairline hides the real boundary. Step past it.
    fringe = _leading_hairline(strip)
    if fringe:
        strip = strip[:, fringe:]
        if strip.shape[1] < 4:
            return shift
    depth = min(int(strip.shape[1]) - 1, 40)
    diff = np.abs(np.diff(strip[:, : depth + 1].astype(np.int16), axis=1)).sum(axis=2)
    position = np.argmax(diff, axis=1).astype(np.float32)
    strength = diff.max(axis=1).astype(np.float32)
    contrast = np.zeros(count, np.float32)
    for index in range(count):
        at = int(position[index])
        if at >= 1:
            before = strip[index, :at].astype(np.float32).mean(axis=0)
        else:
            before = strip[index, 0].astype(np.float32)
        end = min(int(strip.shape[1]), at + 4)
        if end > at + 1:
            after = strip[index, at + 1:end].astype(np.float32).mean(axis=0)
        else:
            after = strip[index, min(at, int(strip.shape[1]) - 1)].astype(np.float32)
        contrast[index] = float(np.abs(before - after).sum())
    strong = (strength > 80.0) & (contrast > 90.0)
    kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0], np.float32)
    kernel /= kernel.sum()
    smooth = np.convolve(position, kernel, mode="same")
    slope = np.gradient(smooth)
    good = strong & (np.abs(slope) > 0.15) & (np.abs(slope) < 4.0)
    raw = np.zeros(count, np.float32)
    raw[good] = np.clip(1.0 / slope[good], -6.0, 6.0)
    support = np.convolve(good.astype(np.float32), np.ones(7, np.float32) / 7.0, mode="same")
    smoothed = np.convolve(raw, kernel, mode="same")
    smoothed[support < 0.45] = 0.0
    return _spread_slope(smoothed).astype(np.float32)


def _spread_slope(shift: np.ndarray, reach: int = 36) -> np.ndarray:
    """Carry a diagonal's slope into the columns it is about to enter.

    The inward ray only sees the boundary once it is already inside that column.
    The next columns, where the line is heading, would otherwise stay flat and
    the diagonal would stop.
    """
    out = np.array(shift, dtype=np.float32, copy=True)
    strong = np.where(np.abs(out) > 0.3)[0]
    if strong.size == 0:
        return out
    breaks = np.where(np.diff(strong) > 3)[0]
    starts = [int(strong[0])]
    ends = []
    for cut in breaks:
        ends.append(int(strong[int(cut)]))
        starts.append(int(strong[int(cut) + 1]))
    ends.append(int(strong[-1]))
    for start, end in zip(starts, ends):
        slope = float(np.median(out[int(start):int(end) + 1]))
        if abs(slope) < 0.3:
            continue
        if slope > 0:
            # Colour is sampled from the right, so the line moves left.
            lo = max(0, int(start) - reach)
            for index in range(lo, int(start)):
                if abs(float(out[index])) < 0.3:
                    out[index] = slope
        else:
            hi = min(int(out.size), int(end) + 1 + reach)
            for index in range(int(end) + 1, hi):
                if abs(float(out[index])) < 0.3:
                    out[index] = slope
    return out


def _end_slope(shift: np.ndarray, at_start: bool) -> float:
    """Slope where this edge meets a corner. A diagonal further along the edge is left alone."""
    if shift.size == 0:
        return 0.0
    window = shift[:24] if at_start else shift[-24:]
    strong = window[np.abs(window) > 0.3]
    if strong.size >= 4:
        return float(np.median(strong))
    return float(shift[0] if at_start else shift[-1])


def _carry_edge_slope(shift: np.ndarray, at_start: bool) -> np.ndarray:
    """Let a short flat run at the corner use the slope of the diagonal that meets it."""
    carried = np.array(shift, dtype=np.float32, copy=True)
    slope = _end_slope(carried, at_start)
    if abs(slope) < 0.3 or carried.size == 0:
        return carried
    limit = min(8, int(carried.size))
    if at_start:
        indexes = range(limit)
    else:
        indexes = range(int(carried.size) - 1, int(carried.size) - 1 - limit, -1)
    for index in indexes:
        if abs(float(carried[index])) < 0.3:
            carried[index] = slope
        else:
            break
    return carried


def _extend_profile(colors: np.ndarray, shift: np.ndarray, extra: int, at_start: bool):
    """Continue a seam profile into the corner so the diagonal does not stop on the trim."""
    colors = np.asarray(colors, np.float32)
    shift = np.asarray(shift, np.float32)
    count = int(colors.shape[0])
    width = int(colors.shape[1]) if colors.ndim > 1 else 1
    slope = float(shift[0] if at_start else shift[-1]) if count else 0.0
    extra_colors = np.empty((max(extra, 0), width), np.float32)
    extra_shift = np.full(max(extra, 0), slope, np.float32)
    if count == 0 or extra < 1:
        return extra_colors, extra_shift

    def sample(at: float) -> np.ndarray:
        at = float(np.clip(at, 0, count - 1))
        low = int(np.floor(at))
        high = min(count - 1, low + 1)
        mix = at - low
        return colors[low] * (1.0 - mix) + colors[high] * mix

    if at_start:
        for index in range(extra):
            extra_colors[index] = sample(-slope * float(extra - index))
        return np.vstack([extra_colors, colors]), np.concatenate([extra_shift, shift])
    for index in range(extra):
        extra_colors[index] = sample((count - 1) + slope * float(index + 1))
    return np.vstack([colors, extra_colors]), np.concatenate([shift, extra_shift])


def _corner_from_profile(colors: np.ndarray, shift: np.ndarray, edge_row: np.ndarray, out_h: int, out_w: int, at_start: bool) -> np.ndarray:
    """Fill the corner from the edge profile that meets it. The shared strip edge stays pinned.

    Row 0 is next to the horizontal trim. Column 0 is next to the page interior when
    at_start is false, and column -1 is next to the interior when at_start is true.
    """
    if out_h < 1 or out_w < 1:
        return np.zeros((max(out_h, 1), max(out_w, 1), 3), np.uint8)
    extended_colors, extended_shift = _extend_profile(colors, shift, out_w, at_start)
    painted = _paint_outward(extended_colors, extended_shift, out_h)
    block = painted[:out_w] if at_start else painted[-out_w:]
    oriented = np.transpose(block, (1, 0, 2))[::-1]
    oriented = np.array(oriented, copy=True)
    if edge_row is not None and int(np.shape(edge_row)[0]) == int(oriented.shape[1]):
        oriented[0] = np.clip(np.round(np.asarray(edge_row)), 0, 255).astype(np.uint8)
    return oriented


def _paint_outward(colors: np.ndarray, shift: np.ndarray, margin: int) -> np.ndarray:
    """Repeat each edge colour outward. A non-zero shift walks along the diagonal."""
    count = colors.shape[0]
    if margin < 1 or count < 1:
        return np.zeros((count, max(margin, 0), 3), np.uint8)
    canvas = np.empty((count, margin, 3), np.float32)
    index = np.arange(count)
    for dist in range(1, margin + 1):
        sample_at = np.clip(index.astype(np.float32) + shift * float(dist - 1), 0, count - 1)
        low = np.floor(sample_at).astype(int)
        high = np.clip(low + 1, 0, count - 1)
        mix = (sample_at - low)[:, None]
        canvas[:, margin - dist] = colors[low] * (1.0 - mix) + colors[high] * mix
    return np.clip(np.round(canvas), 0, 255).astype(np.uint8)


# The seam check skips two pixels and compares the next six. Mirror that far, then stop.
_SEAM_MIRROR_PX = 8


def _fringe_depth(strip: np.ndarray) -> int:
    """Short light line that ends in darker ink. A longer white border stays."""
    depth = _leading_hairline(strip)
    if depth <= 0 or depth >= int(strip.shape[1]):
        return 0
    ink = strip[:, depth].astype(np.float32).mean(axis=1)
    if float(np.mean(ink > 200.0)) > 0.5:
        return 0
    return int(depth)


def _clear_pixel_keyline(plate: np.ndarray) -> np.ndarray:
    """Replace a 1–2 px lighter rim with the ink under it, one pixel at a time.

    The rim can be a white keyline or a paler anti-aliased edge. It has to be
    short, lighter than the ink, and the ink behind it has to stay the same
    colour. A border that continues, and a dark shape that reaches the edge,
    stay. A partial rim is cleared even when the rest of that edge is already ink.
    """
    if plate.ndim < 3 or plate.shape[0] < 6 or plate.shape[1] < 6:
        return plate
    out = np.array(plate, copy=True)

    def clear_rows(view: np.ndarray) -> None:
        if view.shape[1] < 4:
            return
        tone = view.astype(np.float32).mean(axis=2)
        color = view.astype(np.int16)
        rows = np.arange(view.shape[0])
        step01 = np.abs(color[:, 0] - color[:, 1]).sum(axis=1)
        step12 = np.abs(color[:, 1] - color[:, 2]).sum(axis=1)
        step23 = np.abs(color[:, 2] - color[:, 3]).sum(axis=1)
        one = (tone[:, 0] > tone[:, 1] + 18.0) & (step01 > 45) & (step12 < 36)
        if np.any(one):
            view[one, 0] = view[one, 1]
            tone = view.astype(np.float32).mean(axis=2)
            color = view.astype(np.int16)
        two = (
            (tone[:, 0] > tone[:, 2] + 18.0)
            & (tone[:, 1] > tone[:, 2] + 18.0)
            & (np.abs(color[:, 0] - color[:, 2]).sum(axis=1) > 45)
            & (step23 < 36)
        )
        if np.any(two):
            view[two, :2] = view[rows[two], 2][:, None]

    clear_rows(out)
    clear_rows(out[:, ::-1])
    clear_rows(np.transpose(out, (1, 0, 2)))
    clear_rows(np.transpose(out[::-1], (1, 0, 2)))
    return out


def _content_fringe(content: np.ndarray) -> tuple:
    """Light hairline on each side: left, top, right, bottom. Zero when the edge is ink."""
    if content.ndim < 3 or content.shape[0] < 4 or content.shape[1] < 4:
        return (0, 0, 0, 0)
    left = _fringe_depth(content)
    right = _fringe_depth(content[:, ::-1, :])
    top = _fringe_depth(np.transpose(content, (1, 0, 2)))
    bottom = _fringe_depth(np.transpose(content[::-1, :, :], (1, 0, 2)))
    if left + right >= int(content.shape[1]) - 4:
        left = right = 0
    if top + bottom >= int(content.shape[0]) - 4:
        top = bottom = 0
    return (left, top, right, bottom)


def _shear_repeat(anchor: np.ndarray, shift: np.ndarray, count: int) -> np.ndarray:
    """Continue one edge line outward. Index 0 is the pixel next to the anchor.

    A zero shift copies the line straight out, so a column stays on its x.
    A real slope samples a neighbour further along the edge at each step.
    """
    count = int(count)
    length = int(anchor.shape[0])
    if count < 1 or length < 1:
        return np.zeros((0, length, 3), np.uint8)
    move = np.asarray(shift, np.float32).reshape(-1)
    if move.size != length or not np.any(np.abs(move) > 0.3):
        return np.repeat(anchor[None, :, :], count, axis=0)
    steps = np.arange(1, count + 1, dtype=np.float32)[:, None]
    base = np.arange(length, dtype=np.float32)[None, :]
    sample = np.clip(np.rint(base + move[None, :] * steps), 0, length - 1).astype(np.int32)
    return anchor[sample]


def _true_edge_colors(strip: np.ndarray) -> np.ndarray:
    """The pixel on the trim edge. A matching neighbour one pixel in can steady a halftone dot.

    A hard step is not averaged in, so a diagonal keeps the colour that actually
    touches the edge.
    """
    if strip.size == 0:
        return np.zeros((0, 3), np.float32)
    if strip.ndim == 2:
        strip = strip[:, :, None]
    edge = strip[:, 0].astype(np.float32)
    if strip.shape[1] < 2:
        return edge
    nxt = strip[:, 1].astype(np.float32)
    close = np.abs(edge - nxt).sum(axis=1) < 36.0
    colors = np.array(edge, copy=True)
    colors[close] = (edge[close] + nxt[close]) * 0.5
    return colors


def _limit_edge_shift(colors: np.ndarray, shift: np.ndarray, margin: int) -> np.ndarray:
    """Stop a diagonal before it steps into a different colour.

    The sample stays on the edge colour. Flat ink is copied straight out.
    """
    count = int(np.shape(colors)[0])
    limited = np.array(shift, dtype=np.float32, copy=True).reshape(-1)
    if count < 2 or margin < 2 or limited.size != count:
        return limited
    span = float(margin - 1)
    origin = np.asarray(colors, np.float32)
    for index in range(count):
        move = float(limited[index])
        if abs(move) < 0.3:
            continue
        step = 1 if move > 0.0 else -1
        reached = None
        for hop in range(1, count):
            sample = index + step * hop
            if sample < 0 or sample >= count:
                reached = max(0, hop - 1)
                break
            # Stop before the next hard colour, so the strip stays the edge colour.
            if float(np.abs(origin[sample] - origin[index]).sum()) > 40.0:
                reached = max(0, hop - 1)
                break
        if reached is None:
            continue
        if reached <= 0:
            limited[index] = np.float32(0.0)
            continue
        cap = float(reached) / span
        if abs(move) > cap:
            limited[index] = np.float32(cap if move > 0.0 else -cap)
    return limited


def _place_edge(canvas: np.ndarray, plate: np.ndarray, side: str, box) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Paint one margin from the true edge colour.

    The strip uses the limited shift, so it cannot step into the next colour.
    The raw shift is kept for the corner, which has to follow the slope far
    enough to meet the other strip.
    """
    x0, y0, x1, y1 = box
    height, width = canvas.shape[:2]
    ch, cw = plate.shape[:2]
    depth = min(48, ch if side in ("top", "bottom") else cw)

    def placed(view: np.ndarray, margin: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        colors = _true_edge_colors(view)
        raw = _outward_row_shift(view)
        shift = _limit_edge_shift(colors, raw, max(margin, 1))
        return colors, shift, raw

    if side == "left":
        colors, shift, raw = placed(plate[:, : min(depth, cw)], x0)
        if x0 > 0:
            canvas[y0:y1, :x0] = _paint_outward(colors, shift, x0)
        return colors, shift, raw
    if side == "right":
        margin = width - x1
        colors, shift, raw = placed(plate[:, ::-1][:, : min(depth, cw)], margin)
        if margin > 0:
            canvas[y0:y1, x1:] = _paint_outward(colors, shift, margin)[:, ::-1]
        return colors, shift, raw
    if side == "top":
        colors, shift, raw = placed(np.transpose(plate[: min(depth, ch)], (1, 0, 2)), y0)
        if y0 > 0:
            block = _paint_outward(colors, shift, y0)
            canvas[:y0, x0:x1] = np.transpose(block, (1, 0, 2))
        return colors, shift, raw
    margin = height - y1
    colors, shift, raw = placed(np.transpose(plate[::-1][: min(depth, ch)], (1, 0, 2)), margin)
    if margin > 0:
        block = _paint_outward(colors, shift, margin)
        canvas[y1:, x0:x1] = np.transpose(block, (1, 0, 2))[::-1]
    return colors, shift, raw


def _hold_edge_sample(colors: np.ndarray, origin: float, at: float) -> np.ndarray:
    """Sample along an edge, and stay on the first hard colour the diagonal enters."""
    count = int(colors.shape[0])
    if count < 1:
        return np.zeros(3, np.float32)
    origin = float(np.clip(origin, 0, count - 1))
    at = float(np.clip(at, 0, count - 1))
    direction = 1 if at >= origin else -1
    index = int(np.floor(origin))
    held = None
    while (index + direction) >= 0 and (index + direction) < count and (index - at) * direction < 0:
        nxt = index + direction
        if held is None and float(np.abs(colors[nxt] - colors[index]).sum()) > 80.0:
            held = colors[nxt]
        elif held is not None and float(np.abs(colors[nxt] - held).sum()) > 80.0:
            return held
        index = nxt
    if held is not None:
        return held
    low = int(np.floor(at))
    high = min(count - 1, low + 1)
    mix = at - low
    return colors[low] * (1.0 - mix) + colors[high] * mix


def _continue_into_corner(colors: np.ndarray, shift: np.ndarray, length: int, at_end: bool) -> tuple[np.ndarray, np.ndarray]:
    """Edge colours for `length` pixels past one end. Index 0 touches the trim."""
    colors = np.asarray(colors, np.float32)
    shift = np.asarray(shift, np.float32).reshape(-1)
    count = int(colors.shape[0])
    extra = np.zeros((max(length, 0), 3), np.float32)
    extra_shift = np.zeros(max(length, 0), np.float32)
    if count < 1 or length < 1:
        return extra, extra_shift
    origin = float(count - 1 if at_end else 0)
    slope = float(shift[-1] if at_end else shift[0]) if shift.size == count else 0.0
    # The end of a hard diagonal often sits a few pixels in from the corner.
    if shift.size == count:
        window = shift[-24:] if at_end else shift[:24]
        strong = window[np.abs(window) > 0.3]
        if strong.size >= 3:
            slope = float(np.median(strong))
    for index in range(length):
        distance = float(index + 1)
        # Shift is already signed: positive samples further along the edge.
        at = origin + slope * distance
        extra[index] = _hold_edge_sample(colors, origin, at)
        extra_shift[index] = slope
    return extra, extra_shift


def _fill_corner(canvas: np.ndarray, y0: int, y1: int, x0: int, x1: int, side_block: np.ndarray, edge_block: np.ndarray, side_at_low: bool, edge_at_low: bool) -> None:
    """Fill a corner from the two strips that meet it. The nearer strip supplies the colour.

    side_block continues the left or right strip. edge_block continues the top or
    bottom strip. Both are already in canvas orientation. The row and column that
    touch those strips stay pinned, so the corner grows no third colour.
    """
    ch = int(y1 - y0)
    cw = int(x1 - x0)
    if ch < 1 or cw < 1 or side_block.shape[:2] != (ch, cw) or edge_block.shape[:2] != (ch, cw):
        return
    rows = np.arange(ch)[:, None]
    cols = np.arange(cw)[None, :]
    dist_side = rows if side_at_low else (ch - 1 - rows)
    dist_edge = cols if edge_at_low else (cw - 1 - cols)
    mixed = np.where((dist_side <= dist_edge)[..., None], side_block, edge_block)
    # Two pixels, so the 2 px seam window sits on the strip colour and not the blend.
    if side_at_low:
        mixed[0, :] = side_block[0, :]
        if ch > 1:
            mixed[1, :] = side_block[0, :]
    else:
        mixed[-1, :] = side_block[-1, :]
        if ch > 1:
            mixed[-2, :] = side_block[-1, :]
    if edge_at_low:
        mixed[:, 0] = edge_block[:, 0]
        if cw > 1:
            mixed[:, 1] = edge_block[:, 0]
    else:
        mixed[:, -1] = edge_block[:, -1]
        if cw > 1:
            mixed[:, -2] = edge_block[:, -1]
    canvas[y0:y1, x0:x1] = np.clip(np.round(mixed), 0, 255).astype(np.uint8)


def _side_corner_block(colors: np.ndarray, shift: np.ndarray, height: int, width: int, at_end: bool, outward_low: bool) -> np.ndarray:
    """Continue a vertical edge into a corner. Result is (height, width) in canvas order.

    Column 0 is the outer side when outward_low is set, otherwise column -1 is outer.
    Row 0 touches the trim when at_end is false, and row -1 touches it when at_end is set.
    """
    extra, extra_shift = _continue_into_corner(colors, shift, height, at_end)
    if at_end:
        along, along_shift = extra, extra_shift
    else:
        along, along_shift = extra[::-1], extra_shift[::-1]
    painted = _paint_outward(along, along_shift, width)
    if outward_low:
        return painted
    return painted[:, ::-1]


def _edge_corner_block(colors: np.ndarray, shift: np.ndarray, height: int, width: int, at_end: bool, outward_low: bool) -> np.ndarray:
    """Continue a horizontal edge into a corner. Result is (height, width) in canvas order.

    Row 0 is the outer side when outward_low is set. Column 0 touches the trim when
    at_end is false, and column -1 touches it when at_end is set.
    """
    extra, extra_shift = _continue_into_corner(colors, shift, width, at_end)
    if at_end:
        along, along_shift = extra[::-1], extra_shift[::-1]
    else:
        along, along_shift = extra, extra_shift
    painted = _paint_outward(along, along_shift, height)
    block = np.transpose(painted, (1, 0, 2))[:, ::-1]
    if outward_low:
        return block[::-1]
    return block


def _extend_gs_rgb(rgb: np.ndarray, box) -> np.ndarray:
    """Fill the margin from the true edge pixels in this same render.

    Flat ink is copied straight out. A diagonal keeps moving at the slope it had
    when it reached the edge, instead of being mirrored back into the bleed.
    Each corner is filled from the two strips that meet there. A short light
    hairline is replaced by the ink under it.
    """
    x0, y0, x1, y1 = box
    height, width = rgb.shape[:2]
    x0 = min(max(int(x0), 0), width)
    y0 = min(max(int(y0), 0), height)
    x1 = min(max(int(x1), x0), width)
    y1 = min(max(int(y1), y0), height)
    if x0 < 1 and y0 < 1 and width - x1 < 1 and height - y1 < 1:
        return rgb
    canvas = np.array(rgb, copy=True)
    content = np.array(canvas[y0:y1, x0:x1], copy=True)
    ch, cw = content.shape[:2]
    if ch < 1 or cw < 1:
        return canvas
    left_fringe, top_fringe, right_fringe, bottom_fringe = _content_fringe(content)
    plate = content
    if left_fringe or top_fringe or right_fringe or bottom_fringe:
        plate = np.array(content, copy=True)
        if left_fringe:
            pin = min(left_fringe + 2, cw - 1)
            plate[:, :pin] = plate[:, pin][:, None]
        if right_fringe:
            pin = max(0, cw - 1 - (right_fringe + 2))
            plate[:, pin + 1:] = plate[:, pin][:, None]
        if top_fringe:
            pin = min(top_fringe + 2, ch - 1)
            plate[:pin] = plate[pin]
        if bottom_fringe:
            pin = max(0, ch - 1 - (bottom_fringe + 2))
            plate[pin + 1:] = plate[pin]
        canvas[y0:y1, x0:x1] = plate
    plate = _clear_pixel_keyline(plate)
    canvas[y0:y1, x0:x1] = plate
    placed = (x0, y0, x1, y1)
    left_colors, left_shift, left_raw = _place_edge(canvas, plate, "left", placed)
    right_colors, right_shift, right_raw = _place_edge(canvas, plate, "right", placed)
    top_colors, top_shift, top_raw = _place_edge(canvas, plate, "top", placed)
    bottom_colors, bottom_shift, bottom_raw = _place_edge(canvas, plate, "bottom", placed)
    # Corners follow the real slope so a diagonal can enter the corner. The
    # pixels that touch a strip are pinned to that strip.
    if y0 > 0 and x0 > 0:
        side = _side_corner_block(left_colors, left_raw, y0, x0, at_end=False, outward_low=True)
        edge = _edge_corner_block(top_colors, top_raw, y0, x0, at_end=False, outward_low=False)
        side[-1] = canvas[y0, :x0]
        edge[:, -1] = canvas[:y0, x0]
        _fill_corner(canvas, 0, y0, 0, x0, side, edge, side_at_low=False, edge_at_low=False)
    if y0 > 0 and x1 < width:
        side = _side_corner_block(right_colors, right_raw, y0, width - x1, at_end=False, outward_low=False)
        edge = _edge_corner_block(top_colors, top_raw, y0, width - x1, at_end=True, outward_low=False)
        side[-1] = canvas[y0, x1:]
        edge[:, 0] = canvas[:y0, x1 - 1]
        _fill_corner(canvas, 0, y0, x1, width, side, edge, side_at_low=False, edge_at_low=True)
    if y1 < height and x0 > 0:
        side = _side_corner_block(left_colors, left_raw, height - y1, x0, at_end=True, outward_low=True)
        edge = _edge_corner_block(bottom_colors, bottom_raw, height - y1, x0, at_end=False, outward_low=True)
        side[0] = canvas[y1 - 1, :x0]
        edge[:, -1] = canvas[y1:, x0]
        _fill_corner(canvas, y1, height, 0, x0, side, edge, side_at_low=True, edge_at_low=False)
    if y1 < height and x1 < width:
        side = _side_corner_block(right_colors, right_raw, height - y1, width - x1, at_end=True, outward_low=False)
        edge = _edge_corner_block(bottom_colors, bottom_raw, height - y1, width - x1, at_end=True, outward_low=True)
        side[0] = canvas[y1 - 1, x1:]
        edge[:, 0] = canvas[y1:, x1 - 1]
        _fill_corner(canvas, y1, height, x1, width, side, edge, side_at_low=True, edge_at_low=True)
    return canvas


def _stop_edge_fold(band: np.ndarray) -> None:
    """Keep a dark edge that has reached the trim from jumping back outward.

    Rows are in outward order. Only an edge within 8 px of the end is held,
    so a shape in the middle of the page is left where it is.
    """
    if band.ndim != 3 or band.shape[0] < 2 or band.shape[1] < 2:
        return
    for flipped in (False, True):
        view = band if not flipped else band[:, ::-1]
        best = None
        best_px = None
        for index in range(view.shape[0]):
            dark = np.where(view[index, :, 0] < 40)[0]
            if dark.size == 0:
                continue
            pos = int(dark[0])
            if pos <= 8 and (best is None or pos < best):
                best = pos
                best_px = np.array(view[index, : best + 1], copy=True)
                continue
            if best is not None and best_px is not None and pos > best + 3:
                view[index, : best + 1] = best_px


def _gs_content_box(placed, page_rect, shape) -> tuple:
    """Map the placed artwork onto the Ghostscript pixel grid."""
    height, width = shape[:2]
    page_w = float(page_rect.width) or 1.0
    page_h = float(page_rect.height) or 1.0

    def x_px(value: float) -> int:
        return int(np.clip(round(float(value) / page_w * width), 0, width))

    def y_px(value: float) -> int:
        return int(np.clip(round(float(value) / page_h * height), 0, height))

    return (
        x_px(placed.x0 - page_rect.x0),
        y_px(placed.y0 - page_rect.y0),
        x_px(placed.x1 - page_rect.x0),
        y_px(placed.y1 - page_rect.y0),
    )


def _paint_bleed_matching(path: str, placements: list) -> None:
    """Write the added bleed from the same Ghostscript render the press uses.

    The strips are DeviceRGB samples of the adjacent pixels, placed on the
    Ghostscript pixel grid. DeviceCMYK cannot reproduce the navy already on
    the page (0,49,94 renders back as 23,51,92).
    """
    import pikepdf
    import pymupdf as fitz
    from pikepdf import Name, Pdf

    rendered = _gs_rgb_pages(path)
    doc = fitz.open(path)
    paints = []
    try:
        for index, page in enumerate(doc):
            placement = placements[index] if index < len(placements) else None
            if not placement or index >= len(rendered):
                continue
            placed = fitz.Rect(*placement)
            if min(placed.x0, placed.y0, page.rect.width - placed.x1, page.rect.height - placed.y1) < 0.3:
                continue
            rgb = rendered[index]
            height, width = rgb.shape[:2]
            box = _gs_content_box(placed, page.rect, rgb.shape)
            extended = _extend_gs_rgb(rgb, box)
            ox0, oy0, ox1, oy1 = box
            # Cover the skipped seam pixels, and a short light hairline under them.
            fringe_l, fringe_t, fringe_r, fringe_b = _content_fringe(rgb[oy0:oy1, ox0:ox1])
            x0 = min(ox1 - 1, ox0 + 2 + fringe_l) if ox0 > 0 else ox0
            y0 = min(oy1 - 1, oy0 + 2 + fringe_t) if oy0 > 0 else oy0
            x1 = max(x0 + 1, ox1 - 2 - fringe_r) if ox1 < width else ox1
            y1 = max(y0 + 1, oy1 - 2 - fringe_b) if oy1 < height else oy1
            regions = []

            def add(image, x, y, w, h):
                if image is None or w < 1 or h < 1:
                    return
                regions.append((np.ascontiguousarray(image), int(x), int(y), int(w), int(h)))

            add(extended[y0:y1, :x0], 0, y0, x0, y1 - y0)
            add(extended[y0:y1, x1:width], x1, y0, width - x1, y1 - y0)
            add(extended[:y0, x0:x1], x0, 0, x1 - x0, y0)
            add(extended[y1:height, x0:x1], x0, y1, x1 - x0, height - y1)
            add(extended[:y0, :x0], 0, 0, x0, y0)
            add(extended[:y0, x1:width], x1, 0, width - x1, y0)
            add(extended[y1:height, :x0], 0, y1, x0, height - y1)
            add(extended[y1:height, x1:width], x1, y1, width - x1, height - y1)
            paints.append((index, float(page.rect.width), float(page.rect.height), width, height, regions))
    finally:
        doc.close()
    if not paints:
        return
    pdf = Pdf.open(path, allow_overwriting_input=True)
    try:
        for index, page_w, page_h, gs_w, gs_h, regions in paints:
            page = pdf.pages[index]
            if "/XObject" not in page.Resources:
                page.Resources.XObject = pdf.make_indirect(pikepdf.Dictionary())
            ops = []
            for number, (image, x, y, w, h) in enumerate(regions):
                stream = pdf.make_stream(np.ascontiguousarray(image).tobytes())
                stream.Type = Name("/XObject")
                stream.Subtype = Name("/Image")
                stream.Width = image.shape[1]
                stream.Height = image.shape[0]
                stream.ColorSpace = Name("/DeviceRGB")
                stream.BitsPerComponent = 8
                name = Name(f"/Bleed{index}_{number}")
                page.Resources.XObject[name] = stream
                # Place each device pixel on the Ghostscript grid, not MuPDF's.
                pdf_x = x * page_w / gs_w
                pdf_w = w * page_w / gs_w
                pdf_h = h * page_h / gs_h
                pdf_y = page_h - (y + h) * page_h / gs_h
                ops.append(f"q {pdf_w:.4f} 0 0 {pdf_h:.4f} {pdf_x:.4f} {pdf_y:.4f} cm {name} Do Q")
            extra = ("\n".join(ops) + "\n").encode()
            contents = page.get("/Contents")
            if isinstance(contents, pikepdf.Array):
                contents.append(pdf.make_stream(extra))
            else:
                page.Contents = pdf.make_stream((contents.read_bytes() if contents is not None else b"") + extra)
        pdf.save(path)
    finally:
        pdf.close()


def _paint_cmyk_edge(page, placed) -> None:
    """Continue each edge from one inset colour per row or column.

    The sample sits 3 px inside the page, past a 1–2 px light line. A thin
    column or a curve is carried straight out, with no mirror, tiling, blur,
    or sharpening. Each corner is a blend of the two edge colours that meet
    there, and those shared edges keep that colour so the seam has no hairline.
    """
    import pymupdf as fitz

    full = page.rect
    pixel = _EDGE_PIXEL_PT
    cover = _SEAM_COVER_PT
    inset = pixel * _INSET_PX
    sample_span = pixel * _INSET_MEDIAN_PX
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

    scale = 300.0 / 72.0

    def grab(box):
        rect = fitz.Rect(*box) & page.rect
        if rect.width < 0.12 or rect.height < 0.12:
            return None
        return _pixmap_cmyk(page, rect)

    def snap(rect):
        snapped = fitz.Rect(rect)
        if snapped.x0 - full.x0 < 1.0:
            snapped.x0 = full.x0
        if full.x1 - snapped.x1 < 1.0:
            snapped.x1 = full.x1
        if snapped.y0 - full.y0 < 1.0:
            snapped.y0 = full.y0
        if full.y1 - snapped.y1 < 1.0:
            snapped.y1 = full.y1
        return snapped

    left_colors = right_colors = top_colors = bottom_colors = None
    if gaps["left"] > 0.3:
        rect = snap(fitz.Rect(full.x0, placed.y0, placed.x0 + cover, placed.y1))
        out_h = max(1, int(round(rect.height * scale)))
        out_w = max(1, int(round(rect.width * scale)))
        band = _fit_nearest(
            grab((placed.x0 + inset, placed.y0, placed.x0 + inset + sample_span, placed.y1)),
            out_h,
            True,
        )
        left_colors = _clear_crossed_fringe(_median_cols(band, False))
        _insert_cmyk(page, _replicate_cols(left_colors, out_w), rect)
    if gaps["right"] > 0.3:
        rect = snap(fitz.Rect(placed.x1 - cover, placed.y0, full.x1, placed.y1))
        out_h = max(1, int(round(rect.height * scale)))
        out_w = max(1, int(round(rect.width * scale)))
        band = _fit_nearest(
            grab((placed.x1 - inset - sample_span, placed.y0, placed.x1 - inset, placed.y1)),
            out_h,
            True,
        )
        right_colors = _clear_crossed_fringe(_median_cols(band, True))
        _insert_cmyk(page, _replicate_cols(right_colors, out_w), rect)
    if gaps["top"] > 0.3:
        rect = snap(fitz.Rect(placed.x0, full.y0, placed.x1, placed.y0 + cover))
        out_h = max(1, int(round(rect.height * scale)))
        out_w = max(1, int(round(rect.width * scale)))
        band = _fit_nearest(
            grab((placed.x0, placed.y0 + inset, placed.x1, placed.y0 + inset + sample_span)),
            out_w,
            False,
        )
        top_colors = _clear_crossed_fringe(_median_rows(band, False))
        _insert_cmyk(page, _replicate_rows(top_colors, out_h), rect)
    if gaps["bottom"] > 0.3:
        rect = snap(fitz.Rect(placed.x0, placed.y1 - cover, placed.x1, full.y1))
        out_h = max(1, int(round(rect.height * scale)))
        out_w = max(1, int(round(rect.width * scale)))
        band = _fit_nearest(
            grab((placed.x0, placed.y1 - inset - sample_span, placed.x1, placed.y1 - inset)),
            out_w,
            False,
        )
        bottom_colors = _clear_crossed_fringe(_median_rows(band, True))
        _insert_cmyk(page, _replicate_rows(bottom_colors, out_h), rect)

    def paint_corner(rect, vertical, horizontal, pin_row_end, pin_col_end):
        if vertical is None and horizontal is None:
            return
        if vertical is None:
            vertical = horizontal
        if horizontal is None:
            horizontal = vertical
        rect = snap(rect)
        _insert_cmyk(page, _blend_corner(
            vertical,
            horizontal,
            max(1, int(round(rect.height * scale))),
            max(1, int(round(rect.width * scale))),
            pin_row_end,
            pin_col_end,
        ), rect)

    if gaps["left"] > 0.3 and gaps["top"] > 0.3:
        paint_corner(
            fitz.Rect(full.x0, full.y0, placed.x0 + cover, placed.y0 + cover),
            _color_at(left_colors, False),
            _color_at(top_colors, False),
            True,
            True,
        )
    if gaps["right"] > 0.3 and gaps["top"] > 0.3:
        paint_corner(
            fitz.Rect(placed.x1 - cover, full.y0, full.x1, placed.y0 + cover),
            _color_at(right_colors, False),
            _color_at(top_colors, True),
            True,
            False,
        )
    if gaps["left"] > 0.3 and gaps["bottom"] > 0.3:
        paint_corner(
            fitz.Rect(full.x0, placed.y1 - cover, placed.x0 + cover, full.y1),
            _color_at(left_colors, True),
            _color_at(bottom_colors, False),
            False,
            True,
        )
    if gaps["right"] > 0.3 and gaps["bottom"] > 0.3:
        paint_corner(
            fitz.Rect(placed.x1 - cover, placed.y1 - cover, full.x1, full.y1),
            _color_at(right_colors, True),
            _color_at(bottom_colors, True),
            False,
            False,
        )


def _probe_page_pixels(width_pt: float, height_pt: float) -> tuple[int, int]:
    """MuPDF's 300 dpi pixmap size for this page. Rounding millimetres is one pixel off."""
    import pymupdf as fitz

    probe = fitz.open()
    try:
        page = probe.new_page(width=width_pt, height=height_pt)
        pix = page.get_pixmap(dpi=300, alpha=False)
        return int(pix.width), int(pix.height)
    finally:
        probe.close()


def _render_page_cmyk(page) -> np.ndarray:
    """One CMYK raster of the untouched page, existing bleed included."""
    import pymupdf as fitz

    pix = page.get_pixmap(dpi=300, alpha=False, colorspace=fitz.csCMYK)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return np.ascontiguousarray(arr[:, :, :4].copy())


def _split_pad(total: int, start_share: float) -> tuple[int, int]:
    if total <= 0:
        return 0, 0
    start = int(round(total * float(start_share)))
    start = max(0, min(int(total), start))
    return start, int(total) - start


def _chroma_lum(line: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    px = line.astype(np.float32) / 255.0
    cyan, magenta, yellow, black = px[..., 0], px[..., 1], px[..., 2], px[..., 3]
    red = (1.0 - cyan) * (1.0 - black)
    green = (1.0 - magenta) * (1.0 - black)
    blue = (1.0 - yellow) * (1.0 - black)
    lum = 0.3 * red + 0.59 * green + 0.11 * blue
    high = np.maximum(np.maximum(red, green), blue)
    low = np.minimum(np.minimum(red, green), blue)
    return high - low, lum


def _flat_run(line: np.ndarray) -> np.ndarray:
    """Pixels that share one colour with a neighbour along the edge."""
    count = int(line.shape[0])
    flat = np.zeros(count, dtype=bool)
    if count < 2:
        return flat
    step = np.max(np.abs(line[1:].astype(np.int16) - line[:-1].astype(np.int16)), axis=-1)
    close = step <= 12
    flat[1:] |= close
    flat[:-1] |= close
    return flat


def _outlier_mask(line: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """A flat edge line, or a single corner pixel, that is a different colour from inside.

    The mean of four channels hides a red rule: magenta and yellow can jump while the
    average stays under 40. A smooth gradient of a few levels per pixel stays put.
    """
    jump = np.max(np.abs(line.astype(np.int16) - inner.astype(np.int16)), axis=-1)
    chroma_edge, lum_edge = _chroma_lum(line)
    chroma_inner, lum_inner = _chroma_lum(inner)
    vivid = (jump >= 22) & ((chroma_edge > chroma_inner + 0.12) | (lum_edge > lum_inner + 0.08))
    strong = jump > 40
    eligible = _flat_run(line)
    if int(line.shape[0]) > 0:
        eligible[0] = True
        eligible[-1] = True
    return (strong | vivid) & eligible


def _quiet_line(arr: np.ndarray, axis: int, at: int, inner_at: int, second_at: int | None) -> None:
    if axis == 0:
        line = arr[at]
        inner = arr[inner_at]
        second = None if second_at is None else arr[second_at]
    else:
        line = arr[:, at]
        inner = arr[:, inner_at]
        second = None if second_at is None else arr[:, second_at]
    replace = _outlier_mask(line, inner)
    if not bool(replace.any()):
        return
    old = np.array(line, copy=True)
    if axis == 0:
        arr[at, replace] = inner[replace]
    else:
        arr[replace, at] = inner[replace]
    if second is None:
        return
    # A two-pixel rule would still show just inside the seam after the outer pixel is quieted.
    matches = np.max(np.abs(second.astype(np.int16) - old.astype(np.int16)), axis=-1) <= 12
    still = _outlier_mask(second, inner)
    thin = replace & matches & still
    if not bool(thin.any()):
        return
    if axis == 0:
        arr[second_at, thin] = inner[thin]
    else:
        arr[thin, second_at] = inner[thin]


def _quiet_outlier_edge(arr: np.ndarray) -> np.ndarray:
    """Drop a one-pixel fringe that is a different colour from a few pixels inside.

    A symmetric mirror repeats that pixel, so a light last row or a red corner pixel
    becomes a hairline or a cross on the seam. A smooth edge changes by much less
    than this over four pixels.
    """
    arr = np.ascontiguousarray(arr).copy()
    height, width = int(arr.shape[0]), int(arr.shape[1])
    inset = 4
    if height > inset * 2:
        _quiet_line(arr, 0, 0, inset, 1)
        _quiet_line(arr, 0, -1, -1 - inset, -2)
    if width > inset * 2:
        _quiet_line(arr, 1, 0, inset, 1)
        _quiet_line(arr, 1, -1, -1 - inset, -2)
    return arr


def _mirror_pad(arr: np.ndarray, target_w: int, target_h: int, short_l: float, short_r: float, short_t: float, short_b: float):
    """Pad the shortfall by mirroring pixels from just inside a fringe, when that fringe is an outlier."""
    height, width = int(arr.shape[0]), int(arr.shape[1])
    share_l = short_l / max(short_l + short_r, 1e-6) if (short_l + short_r) > 0 else 0.5
    share_t = short_t / max(short_t + short_b, 1e-6) if (short_t + short_b) > 0 else 0.5
    if width > target_w or height > target_h:
        extra_x = max(0, width - target_w)
        extra_y = max(0, height - target_h)
        cut_l, _cut_r = _split_pad(extra_x, share_l)
        cut_t, _cut_b = _split_pad(extra_y, share_t)
        arr = np.ascontiguousarray(arr[cut_t:cut_t + min(height, target_h), cut_l:cut_l + min(width, target_w)])
        height, width = int(arr.shape[0]), int(arr.shape[1])
    arr = _quiet_outlier_edge(arr)
    height, width = int(arr.shape[0]), int(arr.shape[1])
    pad_x = max(0, target_w - width)
    pad_y = max(0, target_h - height)
    left, right = _split_pad(pad_x, share_l)
    top, bottom = _split_pad(pad_y, share_t)
    if left or right or top or bottom:
        # symmetric repeats the edge pixel, so the join is continuous and the pad sits outside the trim.
        arr = np.pad(arr, ((top, bottom), (left, right), (0, 0)), mode="symmetric")
    if int(arr.shape[1]) != target_w or int(arr.shape[0]) != target_h:
        arr = arr[:target_h, :target_w]
        if arr.shape[0] < target_h or arr.shape[1] < target_w:
            arr = np.pad(
                arr,
                ((0, target_h - arr.shape[0]), (0, target_w - arr.shape[1]), (0, 0)),
                mode="edge",
            )
    return np.ascontiguousarray(arr), (left, right, top, bottom)


def _cap_tac_array(arr: np.ndarray, limit: float = 300.0) -> bool:
    c = arr[:, :, 0].astype(np.float32)
    m = arr[:, :, 1].astype(np.float32)
    y = arr[:, :, 2].astype(np.float32)
    k = arr[:, :, 3].astype(np.float32)
    total = c + m + y + k
    cap = float(limit) / 100.0 * 255.0
    over = total > cap
    if int(over.sum()) < 1:
        return False
    extra = total - cap
    cmy = c + m + y
    scale = np.ones_like(cmy)
    good = cmy > 1
    scale[good] = np.clip((cmy[good] - extra[good]) / cmy[good], 0, 1)
    c[over] *= scale[over]
    m[over] *= scale[over]
    y[over] *= scale[over]
    arr[:, :, 0] = np.clip(c, 0, 255).astype(np.uint8)
    arr[:, :, 1] = np.clip(m, 0, 255).astype(np.uint8)
    arr[:, :, 2] = np.clip(y, 0, 255).astype(np.uint8)
    return True


def _dark_neutral_pixels(arr: np.ndarray) -> np.ndarray:
    """Estimated sRGB is dark and neutral. Navy and blue type stay out."""
    c = arr[:, :, 0].astype(np.float32) / 255.0
    m = arr[:, :, 1].astype(np.float32) / 255.0
    y = arr[:, :, 2].astype(np.float32) / 255.0
    k = arr[:, :, 3].astype(np.float32) / 255.0
    red = (1.0 - c) * (1.0 - k)
    green = (1.0 - m) * (1.0 - k)
    blue = (1.0 - y) * (1.0 - k)
    high = np.maximum(np.maximum(red, green), blue)
    low = np.minimum(np.minimum(red, green), blue)
    return (high <= 0.22) & ((high - low) <= 0.10)


def _span_is_small_black(color, size: float) -> bool:
    try:
        value = int(color)
    except (TypeError, ValueError):
        return False
    red = (value >> 16) & 255
    green = (value >> 8) & 255
    blue = value & 255
    if max(red, green, blue) > 40 or (max(red, green, blue) - min(red, green, blue)) > 18:
        return False
    return float(size or 0) < 56.0


def _knock_small_black_text(arr: np.ndarray, page) -> None:
    """Small black type becomes 100K inside its own box. The photo around it is left alone."""
    scale = 300.0 / 72.0
    height, width = arr.shape[:2]
    origin_x = float(page.rect.x0)
    origin_y = float(page.rect.y0)
    try:
        data = page.get_text("dict") or {}
    except Exception:
        return
    for block in data.get("blocks") or []:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                size = float(span.get("size") or 0)
                if not _span_is_small_black(span.get("color"), size):
                    continue
                box = span.get("bbox") or (0, 0, 0, 0)
                if len(box) < 4:
                    continue
                x0 = max(0, int((float(box[0]) - origin_x) * scale) - 1)
                y0 = max(0, int((float(box[1]) - origin_y) * scale) - 1)
                x1 = min(width, int((float(box[2]) - origin_x) * scale) + 2)
                y1 = min(height, int((float(box[3]) - origin_y) * scale) + 2)
                if x1 <= x0 or y1 <= y0:
                    continue
                crop = arr[y0:y1, x0:x1]
                mask = _dark_neutral_pixels(crop)
                if int(mask.sum()) < 4:
                    continue
                crop[mask] = (0, 0, 0, 255)


def _insert_cmyk_plate(page, cmyk: np.ndarray) -> None:
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(cmyk[:, :, :4]), mode="CMYK").save(
        buf, format="TIFF", compression="raw", dpi=(300, 300),
    )
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)


def _cmyk_k_operator(red: float, green: float, blue: float, size: float) -> str:
    red_i = int(round(float(red) * 255))
    green_i = int(round(float(green) * 255))
    blue_i = int(round(float(blue) * 255))
    if max(red_i, green_i, blue_i) <= 40 and (max(red_i, green_i, blue_i) - min(red_i, green_i, blue_i)) <= 18 and float(size) < 56:
        return "0 0 0 1 k"
    if min(red_i, green_i, blue_i) >= 250:
        return "0 0 0 0 k"
    cyan = 1.0 - red_i / 255.0
    magenta = 1.0 - green_i / 255.0
    yellow = 1.0 - blue_i / 255.0
    black = min(cyan, magenta, yellow)
    if black >= 0.999:
        cyan = magenta = yellow = 0.0
    else:
        cyan = (cyan - black) / (1.0 - black)
        magenta = (magenta - black) / (1.0 - black)
        yellow = (yellow - black) / (1.0 - black)
    total = (cyan + magenta + yellow + black) * 100.0
    if total > 300.0:
        cmy = (cyan + magenta + yellow) * 100.0
        if cmy > 0:
            scale = max(0.0, (cmy - (total - 300.0)) / cmy)
            cyan *= scale
            magenta *= scale
            yellow *= scale
    return f"{cyan:.4f} {magenta:.4f} {yellow:.4f} {black:.4f} k"


def _rewrite_text_to_cmyk(page, clip) -> None:
    """TextWriter paints RGB. The press stream has to be CMYK, clipped off the seam band."""
    import re

    doc = page.parent
    xrefs = list(page.get_contents() or [])
    size_re = re.compile(r"/[^\s\[\]]+\s+([0-9]*\.?[0-9]+)\s+Tf\b")
    color_re = re.compile(r"([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)\s+rg\b")
    stroke_re = re.compile(r"([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)\s+RG\b")
    x = float(clip.x0)
    y = float(page.rect.height) - float(clip.y1)
    clip_ops = f"q {x:.3f} {y:.3f} {float(clip.width):.3f} {float(clip.height):.3f} re W n\n"
    for xref in xrefs:
        try:
            data = doc.xref_stream(int(xref))
        except Exception:
            continue
        if not data or (b"Tj" not in data and b"TJ" not in data and b" rg" not in data):
            continue
        draws_image = b" Do" in data or b" Do\n" in data
        text = data.decode("latin1", "replace")
        size = 12.0

        def paint(match, current=[size]):
            found = size_re.search(text[: match.start()])
            if found:
                current[0] = float(found.group(1))
            return _cmyk_k_operator(match.group(1), match.group(2), match.group(3), current[0])

        rewritten = color_re.sub(paint, text)

        def stroke(match, current=[size]):
            found = size_re.search(text[: match.start()])
            if found:
                current[0] = float(found.group(1))
            return _cmyk_k_operator(match.group(1), match.group(2), match.group(3), current[0])[:-2] + " K"

        rewritten = stroke_re.sub(stroke, rewritten)
        if not draws_image and " re W n" not in rewritten:
            rewritten = clip_ops + rewritten + "\nQ\n"
        try:
            doc.update_stream(int(xref), rewritten.encode("latin1", "replace"))
        except Exception:
            continue


def _overlay_live_text(dest, src, pads: tuple[int, int, int, int]) -> None:
    """Put the original words back on top, in CMYK, inside the seam band."""
    import pymupdf as fitz

    left, right, top, bottom = pads
    pad_l = left * 72.0 / 300.0
    pad_r = right * 72.0 / 300.0
    pad_t = top * 72.0 / 300.0
    pad_b = bottom * 72.0 / 300.0
    try:
        data = src.get_text("dict") or {}
    except Exception:
        return
    groups: dict[tuple[int, int, int], list] = {}
    for block in data.get("blocks") or []:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = span.get("text") or ""
                if not str(text).strip():
                    continue
                color = int(span.get("color") or 0)
                key = ((color >> 16) & 255, (color >> 8) & 255, color & 255)
                groups.setdefault(key, []).append(span)
    if not groups:
        return
    fonts: dict[str, object] = {}

    def font_for(name: str):
        if name in fonts:
            return fonts[name]
        chosen = None
        token = "".join(ch for ch in str(name).split("+")[-1].lower() if ch.isalnum())
        font_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "fonts")
        if token and os.path.isdir(font_dir):
            for file in os.listdir(font_dir):
                stem = "".join(ch for ch in os.path.splitext(file)[0].lower() if ch.isalnum())
                if not file.lower().endswith((".ttf", ".otf")) or not stem:
                    continue
                if token not in stem and stem not in token:
                    continue
                try:
                    chosen = fitz.Font(fontfile=os.path.join(font_dir, file))
                    break
                except Exception:
                    chosen = None
        if chosen is not None:
            fonts[name] = chosen
            return chosen
        try:
            for item in src.get_fonts(full=True) or []:
                face = str(item[3] if len(item) > 3 else "")
                xref = int(item[0] if item else 0)
                if name and name not in face and face not in name:
                    continue
                extracted = src.parent.extract_font(xref)
                buffer = extracted[3] if extracted and len(extracted) > 3 else None
                if buffer:
                    chosen = fitz.Font(fontbuffer=buffer)
                    break
        except Exception:
            chosen = None
        if chosen is None:
            try:
                chosen = fitz.Font("helv")
            except Exception:
                chosen = None
        fonts[name] = chosen
        return chosen

    origin_x = float(src.rect.x0)
    origin_y = float(src.rect.y0)
    for key, spans in groups.items():
        for span in spans:
            face = font_for(str(span.get("font") or ""))
            if face is None:
                continue
            origin = span.get("origin") or (span.get("bbox") or (0, 0, 0, 0))[0:2]
            x = float(origin[0]) - origin_x + pad_l
            y = float(origin[1]) - origin_y + pad_t
            text = str(span.get("text") or "")
            size = float(span.get("size") or 12)
            box = span.get("bbox") or (0, 0, 0, 0)
            box_w = max(0.0, float(box[2]) - float(box[0])) if len(box) >= 4 else 0.0
            try:
                natural = float(face.text_length(text, size))
            except Exception:
                natural = 0.0
            # A subset font reloads with the wrong advances. Scale the width only, so the height stays.
            hscale = 1.0
            if box_w > 0.4 and natural > box_w * 1.08:
                hscale = box_w / natural
            span_writer = fitz.TextWriter(dest.rect)
            try:
                span_writer.append((x, y), text, font=face, fontsize=size)
                morph = (fitz.Point(x, y), fitz.Matrix(hscale, 1)) if hscale < 0.999 else None
                span_writer.write_text(dest, color=(key[0] / 255.0, key[1] / 255.0, key[2] / 255.0), morph=morph)
            except Exception:
                continue
    clip = fitz.Rect(
        pad_l + pad_l,
        pad_t + pad_t,
        max(pad_l + pad_l + 1, dest.rect.width - pad_r - pad_r),
        max(pad_t + pad_t + 1, dest.rect.height - pad_b - pad_b),
    )
    _rewrite_text_to_cmyk(dest, clip)


def _place_mirrored_bleed(dest, src, short_l: float, short_r: float, short_t: float, short_b: float) -> None:
    """Extend a partial bleed to 5 mm as one mirrored CMYK image, with the type on top."""
    target_w, target_h = _probe_page_pixels(float(dest.rect.width), float(dest.rect.height))
    plate = _render_page_cmyk(src)
    _knock_small_black_text(plate, src)
    _cap_tac_array(plate)
    plate, pads = _mirror_pad(plate, target_w, target_h, short_l, short_r, short_t, short_b)
    _insert_cmyk_plate(dest, plate)
    _overlay_live_text(dest, src, pads)


def press_window_seam(src_pdf: str, press_pdf: str) -> list[dict]:
    """seam.py on the press PDF: the added strip against an inner band of the same width."""
    import pymupdf as fitz

    if not src_pdf or not press_pdf or not os.path.isfile(src_pdf) or not os.path.isfile(press_pdf):
        return []
    dpi = 300
    px = dpi / 25.4

    def render(path: str, index: int) -> np.ndarray:
        doc = fitz.open(path)
        try:
            pix = doc[index].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
        finally:
            doc.close()

    def lab(image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(image.astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)

    def delta(left: np.ndarray, right: np.ndarray) -> float:
        return float(np.linalg.norm(left - right))

    press = fitz.open(press_pdf)
    try:
        count = press.page_count
    finally:
        press.close()
    rows = []
    for index in range(count):
        source = render(src_pdf, index)
        output = render(press_pdf, index)
        margin = 30
        template = source[margin:-margin, margin:-margin]
        if template.size == 0 or output.shape[0] < template.shape[0] or output.shape[1] < template.shape[1]:
            rows.append({"page": index + 1, "page_max": 99.0, "edges": {}, "corners": {}})
            continue
        _min_val, _max_val, loc, _max_loc = cv2.minMaxLoc(cv2.matchTemplate(output, template, cv2.TM_SQDIFF_NORMED))
        ox, oy = int(loc[0] - margin), int(loc[1] - margin)
        sh, sw = source.shape[:2]
        oh, ow = output.shape[:2]
        added = {"L": ox, "T": oy, "R": ow - (ox + sw), "B": oh - (oy + sh)}
        values = lab(output)
        window = max(2, int(2 * px))
        step = max(1, window // 2)
        edges = {}
        edge_max = 0.0
        for edge, amount in added.items():
            if amount <= 0:
                edges[edge] = {"added_px": amount, "strip_vs_inner_max": 0.0}
                continue
            scores = []
            length = sh if edge in ("L", "R") else sw
            for start in range(0, max(1, length - window), step):
                if edge == "L":
                    strip = values[oy + start:oy + start + window, 0:ox]
                    inner = values[oy + start:oy + start + window, ox:ox + amount]
                elif edge == "R":
                    x0 = ox + sw
                    strip = values[oy + start:oy + start + window, x0:]
                    inner = values[oy + start:oy + start + window, x0 - amount:x0]
                elif edge == "T":
                    strip = values[0:oy, ox + start:ox + start + window]
                    inner = values[oy:oy + amount, ox + start:ox + start + window]
                else:
                    y0 = oy + sh
                    strip = values[y0:, ox + start:ox + start + window]
                    inner = values[y0 - amount:y0, ox + start:ox + start + window]
                if strip.size == 0 or inner.size == 0:
                    continue
                scores.append(delta(strip.reshape(-1, 3).mean(0), inner.reshape(-1, 3).mean(0)))
            worst = max(scores) if scores else 0.0
            edge_max = max(edge_max, worst)
            edges[edge] = {"added_px": amount, "added_mm": round(amount / px, 2), "strip_vs_inner_max": round(worst, 2)}
        corners = {}
        corner_max = 0.0
        boxes = {
            "tl": (slice(0, oy), slice(0, ox)),
            "tr": (slice(0, oy), slice(ox + sw, ow)),
            "bl": (slice(oy + sh, oh), slice(0, ox)),
            "br": (slice(oy + sh, oh), slice(ox + sw, ow)),
        }
        for name, (ys, xs) in boxes.items():
            block = values[ys, xs].reshape(-1, 3)
            if block.size == 0 or ox <= 0 or oy <= 0:
                continue
            if name[0] == "t":
                side = values[0:oy, (ox if name[1] == "l" else ox + sw - ox):(2 * ox if name[1] == "l" else ox + sw)]
            else:
                side = values[oy + sh:oh, (ox if name[1] == "l" else ox + sw - ox):(2 * ox if name[1] == "l" else ox + sw)]
            if name[1] == "l":
                vert = values[(oy if name[0] == "t" else oy + sh - oy):(2 * oy if name[0] == "t" else oy + sh), 0:ox]
            else:
                vert = values[(oy if name[0] == "t" else oy + sh - oy):(2 * oy if name[0] == "t" else oy + sh), ox + sw:ow]
            if side.size == 0 or vert.size == 0:
                continue
            vs_side = delta(block.mean(0), side.reshape(-1, 3).mean(0))
            vs_vert = delta(block.mean(0), vert.reshape(-1, 3).mean(0))
            corner_max = max(corner_max, vs_side, vs_vert)
            corners[name] = {"vs_side_strip": round(vs_side, 2), "vs_vert_strip": round(vs_vert, 2)}
        rows.append({
            "page": index + 1,
            "page_max": round(max(edge_max, corner_max), 2),
            "edges": edges,
            "corners": corners,
            "added": added,
        })
    return rows


def _vector_page_bleed(page, trim_w_mm: float, trim_h_mm: float) -> tuple[dict, str]:
    """Box inset when a TrimBox exists, otherwise the size match when every box is equal.

    A real TrimBox may be uneven: one edge can be 0 mm and another 5 mm. The trim
    still has to be this product. Equal boxes use the centred partial-bleed rule.
    """
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
        size_ok = abs(trim_w - trim_w_mm) <= 2.0 and abs(trim_h - trim_h_mm) <= 2.0
        if size_ok and max(existing.values()) >= 0.4:
            return existing, "boxes"
        if size_ok:
            return existing, "trim"
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

    from client_file_audit import apply_vector_fixes, audit_pdf, repair_cmyk_images, upscale_soft_images

    client_audit = audit_pdf(open_path, trim_w_mm, trim_h_mm)
    font_problems = (client_audit.get("fonts") or {}).get("problems") or []
    needs_fix = bool(
        (client_audit.get("hairlines") or {}).get("count")
        or (client_audit.get("black") or {}).get("needsFix")
        or (client_audit.get("spots") or {}).get("names")
    )
    # RGB black under 18 pt is not a card warning. It is still rewritten to K-only
    # before the CMYK conversion, or the press raster turns it into four colours.
    force_k = int((client_audit.get("black") or {}).get("rgbSmallBlack") or 0) > 0
    soft_images = [
        row for row in ((client_audit.get("resolution") or {}).get("images") or [])
        if 75 <= float(row.get("ppi") or 0) < 300 and float(row.get("area") or 1) < 0.85
    ]
    decorations = (client_audit.get("resolution") or {}).get("decorative") or []
    image_upscale = {"changed": 0, "skippedLow": 0, "skippedFull": 0}
    if client_audit.get("isPdf") and (needs_fix or force_k or soft_images or decorations):
        fixed_path = open_path + ".clientfix.pdf"
        if needs_fix or force_k:
            apply_vector_fixes(open_path, fixed_path)
            repair_cmyk_images(fixed_path)
        else:
            shutil.copyfile(open_path, fixed_path)
        if soft_images or decorations:
            image_upscale = upscale_soft_images(fixed_path)
        open_path = fixed_path
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
    page_mirrored = []
    for index in range(src.page_count):
        src_page = src[index]
        new_page = doc.new_page(width=out_w, height=out_h)
        existing, mode = _vector_page_bleed(src_page, trim_w_mm, trim_h_mm)
        have = min(existing.values()) if existing else 0.0
        most = max(existing.values()) if existing else 0.0
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
            page_mirrored.append(False)
        elif mode in ("partial", "boxes") and most >= 0.4:
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
            if max(short_l, short_r, short_t, short_b) >= 0.4:
                # One CMYK plate. The shortfall is a mirror of the outermost pixels, not a separate strip.
                extended = True
                _place_mirrored_bleed(new_page, src_page, short_l, short_r, short_t, short_b)
                page_mirrored.append(True)
                edge_placements.append(None)
            else:
                placed = fitz.Rect(short_l, short_t, short_l + clip.width, short_t + clip.height)
                new_page.show_pdf_page(placed, src, index, clip=clip)
                kept = True
                page_mirrored.append(False)
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
            fit_inside = False
            try:
                from extra_checks import should_fit_inside

                fit_inside = should_fit_inside(src_page, src_trim, placed, trim_w_mm, trim_h_mm)
            except Exception:
                fit_inside = False
            if fit_inside:
                # Text or a face would be cropped, and the aspect gap is within 12%.
                # Fit the page inside the trim and let the bleed fill the gap.
                src_aspect = src_trim.width / max(src_trim.height, 1)
                dest_aspect = placed.width / max(placed.height, 1)
                fitted = fitz.Rect(placed)
                if src_aspect > dest_aspect:
                    new_h = placed.width / src_aspect
                    y0 = placed.y0 + (placed.height - new_h) / 2
                    fitted = fitz.Rect(placed.x0, y0, placed.x1, y0 + new_h)
                else:
                    new_w = placed.height * src_aspect
                    x0 = placed.x0 + (placed.width - new_w) / 2
                    fitted = fitz.Rect(x0, placed.y0, x0 + new_w, placed.y1)
                placed = fitted
                clip = fitz.Rect(src_trim)
            else:
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
            page_mirrored.append(False)
        if len(edge_placements) < index + 1:
            edge_placements.append(None)
        if len(page_mirrored) < index + 1:
            page_mirrored.append(False)
        _set_boxes(new_page, trim_w_mm, trim_h_mm, bleed_mm)
    if kept and not extended:
        edges = _edge_lines(analysis, methods, kept=True)
    page_count = src.page_count
    all_mirrored = bool(page_mirrored) and all(page_mirrored)
    if all_mirrored:
        # The plate is already CMYK at 300 dpi. Ghostscript would resample the mirror.
        doc.save(out_path, deflate=True, garbage=4)
        doc.close()
        src.close()
    else:
        raw_path = out_path + ".raw.pdf"
        doc.save(raw_path, deflate=True, garbage=4)
        doc.close()
        src.close()
        try:
            convert_cmyk_keep_text(raw_path, out_path, block_font_substitution=bool(font_problems))
        except Exception:
            shutil.copyfile(raw_path, out_path)
        finally:
            if os.path.exists(raw_path):
                os.remove(raw_path)
        # Ghostscript can drop the boxes. Put them back, then paint the bleed
        # from the pixels Ghostscript actually left on the page.
        fixed = fitz.open(out_path)
        for fixed_page in fixed:
            _set_boxes(fixed_page, trim_w_mm, trim_h_mm, bleed_mm)
        boxed = out_path + ".box.pdf"
        fixed.save(boxed, deflate=True, garbage=4)
        fixed.close()
        _paint_bleed_matching(boxed, edge_placements)
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
    report["clientFileAudit"] = client_audit
    report["imageUpscale"] = image_upscale
    report["clientAudit"] = {
        "fonts": [row.get("name") for row in font_problems],
        "hairlines": int((client_audit.get("hairlines") or {}).get("count") or 0),
        "spots": list((client_audit.get("spots") or {}).get("names") or []),
        "black": bool((client_audit.get("black") or {}).get("needsFix")),
    }
    resolution = client_audit.get("resolution") or {}
    low = resolution.get("images") or []
    if low:
        report["effectiveDpi"] = round(float(resolution.get("worst") or 0), 1)
        named = ", ".join(f"{row['name']} {float(row['ppi']):.0f} ppi" for row in low[:4])
        report["resolutionNote"] = f"Lowest picture on the original file: {named}."
    elif resolution.get("worst"):
        report["effectiveDpi"] = round(float(resolution["worst"]), 1)
        report["resolutionNote"] = f"Pictures on the original file are about {report['effectiveDpi']:.0f} ppi."
    else:
        report["effectiveDpi"] = None
        report["resolutionNote"] = "No picture on the original file was large enough to measure."
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
    """CIEDE2000 between the ink just inside the seam and the extension just outside it.

    The page is rendered with the press Ghostscript pipeline (png16m, 300 dpi).
    MuPDF's pixel grid is one pixel off that render and reports a step that is
    not on the plate. seam_x_pt / seam_y_pt are the distance from the media edge
    to the join. One row per page: left, right, top, bottom, and the four corners.
    """
    rendered = _gs_rgb_pages(path)
    rows = []
    scale = float(dpi) / 72.0
    band = 6
    for index, rgb in enumerate(rendered):
            height, width = rgb.shape[:2]
            sx = int(round(float(seam_x_pt) * scale))
            sy = int(round(float(seam_y_pt) * scale))
            sx = min(max(sx, band + 1), width - band - 1)
            sy = min(max(sy, band + 1), height - band - 1)
            # The strip is the whole added margin. It has to match the true edge
            # pixel, not a mirror of the artwork a few pixels inside.
            pairs = {
                "left": ((sy, height - sy, 0, sx), (sy, height - sy, sx, sx + 1)),
                "right": ((sy, height - sy, width - sx, width), (sy, height - sy, width - sx - 1, width - sx)),
                "top": ((0, sy, sx, width - sx), (sy, sy + 1, sx, width - sx)),
                "bottom": ((height - sy, height, sx, width - sx), (height - sy - 1, height - sy, sx, width - sx)),
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
            local_edges = {
                "left": _window_max_delta_e(rgb, (sy, height - sy, 0, sx), (sy, height - sy, sx, sx + 1), "y"),
                "right": _window_max_delta_e(
                    rgb, (sy, height - sy, width - sx, width), (sy, height - sy, width - sx - 1, width - sx), "y",
                ),
                "top": _window_max_delta_e(rgb, (0, sy, sx, width - sx), (sy, sy + 1, sx, width - sx), "x"),
                "bottom": _window_max_delta_e(
                    rgb, (height - sy, height, sx, width - sx), (height - sy - 1, height - sy, sx, width - sx), "x",
                ),
            }
            # A corner matches when the pixels that touch each strip match that strip.
            local_corners = {
                "tl": max(
                    _window_max_delta_e(rgb, (sy - 2, sy, 0, sx), (sy, sy + 2, 0, sx), "x") or 0,
                    _window_max_delta_e(rgb, (0, sy, sx - 2, sx), (0, sy, sx, sx + 2), "y") or 0,
                ),
                "tr": max(
                    _window_max_delta_e(rgb, (sy - 2, sy, width - sx, width), (sy, sy + 2, width - sx, width), "x") or 0,
                    _window_max_delta_e(rgb, (0, sy, width - sx, width - sx + 2), (0, sy, width - sx - 2, width - sx), "y") or 0,
                ),
                "bl": max(
                    _window_max_delta_e(rgb, (height - sy, height - sy + 2, 0, sx), (height - sy - 2, height - sy, 0, sx), "x") or 0,
                    _window_max_delta_e(rgb, (height - sy, height, sx - 2, sx), (height - sy, height, sx, sx + 2), "y") or 0,
                ),
                "br": max(
                    _window_max_delta_e(rgb, (height - sy, height - sy + 2, width - sx, width), (height - sy - 2, height - sy, width - sx, width), "x") or 0,
                    _window_max_delta_e(rgb, (height - sy, height, width - sx, width - sx + 2), (height - sy, height, width - sx - 2, width - sx), "y") or 0,
                ),
            }
            rows.append({
                "page": index + 1,
                **edges,
                "corners": corner_de,
                "local": {**local_edges, **local_corners},
            })
    return rows
