#!/usr/bin/env python3
"""One-step press file for sales.

Decides the product fit and 5mm bleed without asking. AI raster artwork is
set as vector type (vector text rebuild v2). If that check fails, the picture
is only enlarged, the original lettering stays, and the job is marked amber.
The press PDF for every other file is built by the existing compile script.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import subprocess
import sys
import tempfile
from typing import Optional

BLEED_MM = 5.0
UPSCALE_AMBER = 3.0
ASPECT_AMBER = 0.12
OCR_AMBER = 0.55
MAX_HONEST_UPSCALE = 4.0
MIN_DPI_AFTER_UPSCALE = 150.0
MM_TO_PT = 72.0 / 25.4

OFFICE_EXT = {".doc", ".docx", ".ppt", ".pptx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
VECTOR_EXT = {".pdf", ".ai", ".eps"}

OFFICE_MESSAGE = (
    "Thanks for sending this. Word and PowerPoint files cannot go on the printing press. "
    "Please send a PDF (in Word or PowerPoint choose File, then Save as PDF) "
    "or a high-resolution JPG or PNG. A print file should be about 300 DPI at the finished size, "
    "with 5mm of extra image around the edge if you can."
)
CORRUPT_MESSAGE = (
    "We could not open this file. It may be damaged, or it was saved in a program we cannot read. "
    "Please send it again as a PDF, JPG, or PNG."
)
COMPILE_MESSAGE = (
    "We could not build a press file from this. Please send a PDF or a clear JPG or PNG of the artwork."
)


def _products() -> list:
    path = os.path.join(os.path.dirname(__file__), "..", "shared", "quick-print-products.json")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _product_by_id(product_id: str) -> Optional[dict]:
    for product in _products():
        if product.get("id") == product_id:
            return product
    return None


def _cover_scale(src_w: int, src_h: int, trim_w: float, trim_h: float) -> float:
    target_w = max(1.0, float(trim_w) / 25.4 * 300.0)
    target_h = max(1.0, float(trim_h) / 25.4 * 300.0)
    return max(target_w / max(1, src_w), target_h / max(1, src_h))


def too_small(src_w: int, src_h: int, trim_w: float, trim_h: float) -> bool:
    scale = _cover_scale(src_w, src_h, trim_w, trim_h)
    if scale <= 0:
        return True
    after = (300.0 / scale) * MAX_HONEST_UPSCALE
    return after < MIN_DPI_AFTER_UPSCALE


def too_small_message(label: str, trim_w: float, trim_h: float) -> str:
    need_w = int(math.ceil(float(trim_w) / 25.4 * 300.0))
    need_h = int(math.ceil(float(trim_h) / 25.4 * 300.0))
    return (
        f"Thanks for the picture. It is too small to print sharply on {label}, even if we enlarge it. "
        f"Please send a larger file. For {label} that is about {need_w} × {need_h} pixels, "
        "or a PDF exported from the design program."
    )


def decide_light(facts: dict) -> dict:
    """Traffic light. Amber is only for a real risk. Red means no press file."""
    kind = facts.get("kind")
    if kind == "office":
        return {"light": "red", "reasons": ["Word and PowerPoint files cannot be printed as they are."], "clientMessage": OFFICE_MESSAGE}
    if kind in ("corrupt", "unsupported") or facts.get("readable") is False:
        return {"light": "red", "reasons": ["The file could not be read."], "clientMessage": CORRUPT_MESSAGE}
    if facts.get("tooSmall"):
        return {
            "light": "red",
            "reasons": ["The picture is far too small to print at this size."],
            "clientMessage": facts.get("tooSmallMessage") or too_small_message("this size", 148, 210),
        }
    if facts.get("unextendable"):
        from green_gate import client_message

        return {
            "light": "red",
            "reasons": ["The picture's shape cannot be extended to this print size."],
            "clientMessage": client_message("proportions"),
        }
    if facts.get("missingContent"):
        from green_gate import client_message

        return {
            "light": "red",
            "reasons": ["The page looks empty, so some of the artwork is missing."],
            "clientMessage": client_message("missing"),
        }
    if not facts.get("compiled"):
        return {
            "light": "red",
            "reasons": [facts.get("compileError") or "A press file could not be built."],
            "clientMessage": COMPILE_MESSAGE,
        }

    reasons = []
    if facts.get("ocrDoubtful"):
        reasons.append(
            str(facts.get("ocrDoubtfulReason") or "").strip()
            or "Some marks did not look like real words, so they were left unchanged. Glance at the picture."
        )
    if facts.get("ocrLow"):
        reasons.append("The rebuilt text was hard to read, so check the spelling before it is printed.")
    if facts.get("textNearTrim"):
        reasons.append("Some text is still very close to the trim after the safe-zone shrink.")
    upscale = float(facts.get("upscale") or 1)
    if upscale >= UPSCALE_AMBER:
        reasons.append(f"The picture was enlarged {upscale:.1f} times to reach print size. Glance at fine detail.")
    if facts.get("aspectExtended") and float(facts.get("aspectDelta") or 0) >= ASPECT_AMBER:
        reasons.append("The picture was a different shape, so the edges were extended. Glance at those edges.")
    # The checklist already says whether leftover pictures are sharp enough.
    if facts.get("vectorAmber") and not facts.get("checklist"):
        note = str(facts.get("vectorAmberReason") or "").strip()
        if note and note not in reasons:
            reasons.append(note)
    if facts.get("enginePassed") is False:
        note = str(facts.get("engineReason") or "").strip() or "The press check flagged this file."
        if note not in reasons:
            reasons.append(note)
    checklist = facts.get("checklist") if isinstance(facts.get("checklist"), dict) else None
    if checklist:
        for item in checklist.get("items") or []:
            if item.get("passed"):
                continue
            detail = str(item.get("detail") or item.get("label") or "").strip()
            if detail and detail not in reasons:
                reasons.append(detail)
        if checklist.get("severity") == "red":
            from green_gate import client_message

            return {
                "light": "red",
                "reasons": reasons or ["This file cannot go to press as it is."],
                "clientMessage": str(checklist.get("clientMessage") or client_message("cut")),
            }
    if reasons:
        return {"light": "amber", "reasons": reasons, "clientMessage": ""}
    return {"light": "green", "reasons": [], "clientMessage": ""}


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "artwork").replace("\x00", "")
    return base[:180] or "artwork"


def _ext(path: str, filename: str) -> str:
    return os.path.splitext(filename or path)[1].lower()


def _to_bgr(img):
    import cv2
    import numpy as np

    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        white = np.full_like(bgr, 255)
        return (bgr * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return img[:, :, :3].copy()


def _read_image(path: str):
    import cv2

    return _to_bgr(cv2.imread(path, cv2.IMREAD_UNCHANGED))


def _write_png(img, path: str) -> None:
    import cv2
    from PIL import Image

    ok = cv2.imwrite(path, img)
    if not ok:
        raise RuntimeError("could not write the working picture")
    with Image.open(path) as im:
        im.save(path, format="PNG", dpi=(300, 300))


def _pdf_trim_mm(path: str) -> Optional[tuple]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count < 1:
            return None
        page = doc[0]
        box = page.trimbox if page.trimbox.width > 2 and page.trimbox.height > 2 else page.mediabox
        return (box.width * 25.4 / 72.0, box.height * 25.4 / 72.0)
    finally:
        doc.close()


def _fit_page_to_trim(page_w: float, page_h: float, trim_w: float, trim_h: float, tolerance: float = 2.5):
    """The page as this trim, in either orientation, including bleed already on the page."""
    from press_ready_engine import inferred_side_bleed

    direct = inferred_side_bleed(page_w, page_h, trim_w, trim_h, trim_tol=tolerance)
    if direct:
        return {"trimW": float(trim_w), "trimH": float(trim_h), "rotated": False, "bleed": direct}
    swapped = inferred_side_bleed(page_w, page_h, trim_h, trim_w, trim_tol=tolerance)
    if swapped:
        return {"trimW": float(trim_h), "trimH": float(trim_w), "rotated": True, "bleed": swapped}
    return None


def _product_for_trim(trim_w: float, trim_h: float):
    for product in _products():
        if abs(float(product["widthMm"]) - trim_w) <= 0.2 and abs(float(product["heightMm"]) - trim_h) <= 0.2:
            return product
    return None


def _remember_product(decisions: list, product: dict, trim_w: float, trim_h: float) -> None:
    line = f"Product set to {product.get('label')} ({trim_w:g} × {trim_h:g} mm)."
    for index, existing in enumerate(decisions):
        if str(existing).startswith("Product set to "):
            decisions[index] = line
            return
    decisions.append(line)


def _match_product(width_mm: float, height_mm: float, tolerance: float = 2.5):
    """Best catalog size. An unrotated landscape product wins over turning the portrait one."""
    best = None
    for product in _products():
        fit = _fit_page_to_trim(width_mm, height_mm, float(product["widthMm"]), float(product["heightMm"]), tolerance)
        if not fit:
            continue
        extra = abs(width_mm - fit["trimW"]) + abs(height_mm - fit["trimH"])
        rank = (1 if fit["rotated"] else 0, extra)
        if best is None or rank < best[0]:
            best = (rank, product, fit["rotated"])
    if not best:
        return None, False
    product, rotated = best[1], best[2]
    if rotated:
        twin = _product_for_trim(float(product["heightMm"]), float(product["widthMm"]))
        if twin:
            return twin, False
    return product, rotated


def _render_pdf_image(path: str):
    import numpy as np
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        long_pt = max(page.rect.width, page.rect.height, 1)
        zoom = min(300.0 / 72.0, 4500.0 / long_pt)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        return arr[:, :, :3][:, :, ::-1].copy()
    finally:
        doc.close()


def _prepare_vector(path: str, ext: str) -> tuple:
    if ext not in (".ai", ".eps"):
        return path, ""
    from illustrator_intake import prepare_illustrator_file

    prepared = prepare_illustrator_file(path, ext)
    if not prepared.get("success"):
        return "", "This Illustrator file could not be opened. In Illustrator, save a PDF copy and send that."
    return prepared.get("pdfPath") or path, ""


def _rotate_to_product(img, trim_w: float, trim_h: float, decisions: list) -> tuple:
    import cv2

    height, width = img.shape[:2]
    src = width / float(height)
    target = float(trim_w) / float(trim_h)
    swap = float(trim_h) / float(trim_w)
    delta = abs(src - target) / target
    swap_delta = abs(src - swap) / swap
    if swap_delta < 0.08 and swap_delta + 0.01 < delta:
        turned = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        decisions.append("The picture was turned on its side so it matches the product. It was not stretched.")
        return turned, True
    return img, False


# A shape just inside the old 64px window must not be mirrored out. The orange-disc
# check sits at row 40, so the reflected rim stays shallower than that.
EDGE_SAMPLE_PX = 5
MIRROR_RIM_PX = 28
# Pads shorter than this (the 5 mm bleed, the orange-disc test) keep the shallow rim.
# A tall gap mirrors a block as tall as the gap. A 28px rim smears into stripes.
TALL_PAD_PX = 120
# The blur at the join is a soft continuation, then it climbs over 15–20 mm.
TALL_BLUR_S0 = 3.0
TALL_BLUR_SMAX = 80.0
TALL_RAMP_MM = 18.0
# The blend steps into the picture, and stops 2 mm short of text or a logo.
TALL_OVERLAP_MM = 7.0
TALL_CLEAR_MM = 2.0
# Lettering stays this far inside the trim when a short page is lengthened.
SAFE_ZONE_MM = 4.0
EDGE_LOWPASS_FRACTION = 0.04


def _lowpass_along(row, fraction: float = EDGE_LOWPASS_FRACTION):
    """Blur one row along its length. `row` is (width, 3)."""
    import cv2
    import numpy as np

    width = int(row.shape[0])
    sigma = max(1.0, float(fraction) * width)
    blurred = cv2.GaussianBlur(np.ascontiguousarray(row.reshape(1, width, 3)), (0, 0), sigma)
    return blurred.reshape(width, 3)


def _reflect_from_edge(edge_band, pad: int):
    """Mirror a rim. Row 0 of the result is the edge row, touching the picture."""
    import numpy as np

    rim = int(edge_band.shape[0])
    if rim <= 1:
        return np.repeat(edge_band[:1], pad, axis=0)
    period = 2 * (rim - 1)
    distance = np.arange(pad, dtype=np.int32)
    pos = distance % period
    pos = np.where(pos < rim, pos, period - pos)
    src = rim - 1 - pos
    return edge_band[src]


def _blur_level(image, sigma: float, strong: bool):
    """Gaussian. A strong extension blurs a smaller copy so sigma 80 stays cheap."""
    import cv2

    if sigma <= 0.05:
        return image
    if strong and sigma >= 12.0:
        factor = min(8.0, max(2.0, float(sigma) / 8.0))
        small_w = max(8, int(round(image.shape[1] / factor)))
        small_h = max(8, int(round(image.shape[0] / factor)))
        small = cv2.resize(image, (small_w, small_h), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), float(sigma) / factor)
        return cv2.resize(small, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(image, (0, 0), float(sigma))


def _progressive_blur(strip, smax: float, reach: float, s0: float = 0.0, strong: bool = False):
    """Blur grows with distance from row 0. Far rows lose the repeated rim."""
    import numpy as np

    count = int(strip.shape[0])
    base = np.ascontiguousarray(strip.astype(np.float32))
    if count == 0 or smax <= 0.05:
        return base
    s0 = float(max(0.0, s0))
    smax = float(max(float(smax), s0))
    ladder = [0.0, 2.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0, 50.0, 65.0, 80.0]
    sigs = [value for value in ladder if s0 - 0.05 <= value <= smax + 0.05]
    if not sigs or sigs[0] > s0 + 0.05:
        sigs.insert(0, s0)
    if sigs[-1] < smax - 0.05:
        sigs.append(smax)
    if len(sigs) < 2:
        sigs.append(sigs[-1] + 1.0)
    levels = [base if sigma <= 0.05 else _blur_level(base, sigma, strong) for sigma in sigs]
    if count == 1:
        return levels[-1]
    distance = np.arange(count, dtype=np.float32)
    span = max(float(reach), 1.0)
    amount = np.clip(distance / span, 0.0, 1.0) ** 1.05
    target = s0 + amount * (smax - s0)
    edges = np.asarray(sigs, np.float32)
    index = np.clip(np.searchsorted(edges, target, side="right") - 1, 0, len(sigs) - 2)
    lo = edges[index]
    hi = edges[index + 1]
    alpha = ((target - lo) / np.maximum(hi - lo, 1e-6)).astype(np.float32)
    out = np.empty_like(base)
    for step in range(len(sigs) - 1):
        chosen = np.where(index == step)[0]
        if chosen.size == 0:
            continue
        weight = alpha[chosen][:, None, None]
        out[chosen] = levels[step][chosen] * (1.0 - weight) + levels[step + 1][chosen] * weight
    # A shallow pad keeps the real edge pixel. A tall pad is already blurred at s0.
    if s0 <= 0.05:
        out[0] = base[0]
    return out


def _mm_px(mm: float, px_per_mm: float, minimum: int = 1) -> int:
    return max(int(minimum), int(round(float(mm) * float(px_per_mm))))


def _smoothstep(t):
    """Zero slope at both ends, so a blend does not leave a new edge."""
    import numpy as np

    x = np.clip(np.asarray(t, np.float32), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _first_ink_row(art, px_per_mm: float, from_end: bool = False):
    """Distance from this edge to the first text or logo line.

    A photograph has edge energy on every row. Lettering is a step up from
    the rows above it, held for about half a millimetre. None means this
    edge has no such line inside the search.
    """
    import cv2
    import numpy as np

    if art is None or getattr(art, "size", 0) == 0 or art.shape[0] < 8:
        return None
    gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)
    if from_end:
        gray = gray[::-1]
    plane = gray.astype(np.float32)
    horizontal = np.abs(cv2.Sobel(plane, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=1)
    vertical = np.abs(cv2.Sobel(plane, cv2.CV_32F, 0, 1, ksize=3)).mean(axis=1)
    energy = np.maximum(horizontal, vertical)
    sigma = max(0.8, 0.30 * float(px_per_mm))
    smooth = cv2.GaussianBlur(energy.reshape(1, -1), (0, 0), sigma).ravel()
    window = _mm_px(6.0, px_per_mm, 5)
    need = _mm_px(0.45, px_per_mm, 2)
    search = min(int(smooth.size), _mm_px(24.0, px_per_mm, need + window))
    run = 0
    for y in range(search):
        lo = max(0, y - window)
        if y - lo < max(4, window // 3):
            run = 0
            continue
        bg = float(np.percentile(smooth[lo:y], 35))
        if float(smooth[y]) > max(42.0, bg * 2.5) and float(smooth[y]) > bg + 18.0:
            run += 1
            if run >= need:
                return int(y - need + 1)
        else:
            run = 0
    return None


def _join_overlap(art, px_per_mm: float, from_end: bool = False) -> int:
    """How far the blend may enter the picture: 6–8 mm, but 2 mm clear of type."""
    target = _mm_px(TALL_OVERLAP_MM, px_per_mm, 4)
    clear = _mm_px(TALL_CLEAR_MM, px_per_mm, 2)
    ink = _first_ink_row(art, px_per_mm, from_end=from_end)
    if ink is None:
        return min(target, max(0, int(art.shape[0]) // 6))
    return max(0, min(target, int(ink) - clear))


def _protect_rows(art, px_per_mm: float, from_end: bool = False) -> int:
    """Edge rows kept out of the inpaint, so the join is not a filled grey line."""
    ink = _first_ink_row(art, px_per_mm, from_end=from_end)
    clear = _mm_px(TALL_CLEAR_MM, px_per_mm, 2)
    if ink is None:
        return min(clear * 2, max(0, int(art.shape[0]) // 6))
    return max(0, min(int(art.shape[0]) - 1, int(ink) - 1))


def _scrub_marks(band, protect_px: int = 0, protect_end: bool = False):
    """Inpaint lettering and logos on a copy used only to grow the page.

    The art itself is not changed. A flat field and a wide colour bar are left,
    because those are the colour the extension is supposed to continue.
    `protect_px` rows against the picture stay real, so the fill cannot paint
    a light line on the join.
    """
    import cv2
    import numpy as np

    height, width = band.shape[:2]
    if height < 12 or width < 12:
        return band
    scale = 1.0
    work = band
    long_edge = max(height, width)
    if long_edge > 900:
        scale = 900.0 / float(long_edge)
        work = cv2.resize(
            band,
            (max(8, int(round(width * scale))), max(8, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    short = min(work.shape[:2])
    # Compact panels and lettering. A full-height colour bar is wider than this and stays.
    object_px = int(max(31, min(short * 0.36, 201)))
    if object_px % 2 == 0:
        object_px += 1
    object_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (object_px, object_px))
    opened = cv2.morphologyEx(work, cv2.MORPH_OPEN, object_kernel)
    closed = cv2.morphologyEx(work, cv2.MORPH_CLOSE, object_kernel)
    background = (opened.astype(np.float32) + closed.astype(np.float32)) * 0.5
    object_delta = np.max(np.abs(work.astype(np.float32) - background), axis=2)
    kernel_px = int(max(9, min(31, round(short * 0.04))))
    if kernel_px % 2 == 0:
        kernel_px += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_px, kernel_px))
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    white = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    marks = np.zeros(gray.shape, np.uint8)
    marks[(black > 20) | (white > 20) | (object_delta > 22)] = 255
    column_frac = (marks > 0).mean(axis=0)
    marks[:, column_frac > 0.65] = 0
    if int(marks.max()) == 0:
        return band
    marks = cv2.dilate(marks, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    if protect_px > 0:
        prot = max(1, int(round(float(protect_px) * scale)))
        prot = min(prot, marks.shape[0] - 1)
        if protect_end:
            marks[-prot:] = 0
        else:
            marks[:prot] = 0
    if int(marks.max()) == 0:
        return band
    keep = (marks == 0).astype(np.float32)
    sigma = 18.0
    num = cv2.GaussianBlur(work.astype(np.float32) * keep[..., None], (0, 0), sigma)
    den = cv2.GaussianBlur(keep, (0, 0), sigma)[..., None]
    filled = num / np.maximum(den, 1e-3)
    out = work.astype(np.float32)
    painted = marks > 0
    out[painted] = filled[painted]
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    if scale != 1.0:
        out = cv2.resize(out, (width, height), interpolation=cv2.INTER_LINEAR)
    return out


def _feather_to_edge(pad, edge_row, at_end: bool, depth: int = 36):
    """Blend the seam onto a horizontally blurred real edge so the join is not a line."""
    import numpy as np

    if pad.shape[0] < 4:
        return pad
    edge = _lowpass_along(np.asarray(edge_row, np.float32), fraction=0.08)
    depth = min(int(depth), int(pad.shape[0]))
    out = pad.astype(np.float32)
    for dist in range(depth):
        y = (out.shape[0] - 1 - dist) if at_end else dist
        weight = (dist / float(depth)) ** 0.85
        out[y] = edge * (1.0 - weight) + out[y] * weight
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _mirror_extend(band, pad: int, forward: bool, strong: bool = False, ramp_px: int | None = None, protect_px: int = 0):
    """Reflect the rim and blur it more the further it sits from the picture.

    A short pad reflects the outer 28px. A tall pad reflects a block as tall as
    the gap (tiled when the picture is shorter than the gap), with the lettering
    painted out of that copy first. The blur starts near sigma 3 on the join
    and reaches its full width over about 18 mm. `band` row 0 is the edge when
    forward is false.
    """
    import numpy as np

    width = int(band.shape[1])
    if pad <= 0:
        return np.zeros((0, width, 3), np.uint8)
    if strong:
        source = _scrub_marks(band, protect_px=protect_px, protect_end=forward)
    else:
        source = band
    work = np.ascontiguousarray(source.astype(np.float32))
    if not forward:
        work = work[::-1]
    reflected = _reflect_from_edge(work, pad)
    if strong:
        reach = float(ramp_px if ramp_px else max(int(pad), 1))
        blurred = _progressive_blur(reflected, TALL_BLUR_SMAX, reach, s0=TALL_BLUR_S0, strong=True)
    else:
        reach = float(min(max(pad - 1, 1), 110))
        smax = float(min(56.0, max(7.0, pad * 0.14)))
        blurred = _progressive_blur(reflected, smax, reach, s0=0.0, strong=False)
    out = np.clip(np.rint(blurred), 0, 255).astype(np.uint8)
    if not forward:
        out = out[::-1]
    return out


def _match_join_colour(pad, art, at_end: bool, depth: int):
    """Per-column low-pass so the rows against the picture share its edge colour.

    A filled mirror can leave one light row. The correction is full on the join
    and fades, so it does not repaint the rest of the extension.
    """
    import numpy as np

    rows = min(3, int(pad.shape[0]), int(art.shape[0]))
    if rows < 1 or pad.shape[1] != art.shape[1]:
        return pad
    if at_end:
        ext = pad[-rows:].astype(np.float32).mean(axis=0)
        edge = art[:rows].astype(np.float32).mean(axis=0)
    else:
        ext = pad[:rows].astype(np.float32).mean(axis=0)
        edge = art[-rows:].astype(np.float32).mean(axis=0)
    delta = _lowpass_along(edge - ext, fraction=0.025)
    out = pad.astype(np.float32)
    depth = min(int(depth), int(out.shape[0]))
    if depth < 1:
        return pad
    span = float(max(depth - 1, 1))
    for dist in range(depth):
        fade = float(1.0 - _smoothstep(dist / span))
        y = (out.shape[0] - 1 - dist) if at_end else dist
        out[y] = out[y] + delta * fade
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _feather_into_art(pad, art, overlap: int, at_end: bool):
    """Smoothstep the extension into the picture. The far end of the ramp is the real art."""
    import numpy as np

    overlap = int(min(max(int(overlap), 0), int(pad.shape[0]), int(art.shape[0])))
    if overlap < 2 or pad.shape[1] != art.shape[1]:
        return art
    out = np.array(art, copy=True)
    original = art.astype(np.float32)
    soft = pad.astype(np.float32)
    for dist in range(overlap):
        alpha = float(_smoothstep((dist + 0.5) / float(overlap)))
        if at_end:
            mixed = soft[-(dist + 1)] * (1.0 - alpha) + original[dist] * alpha
            out[dist] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
        else:
            mixed = soft[dist] * (1.0 - alpha) + original[-(dist + 1)] * alpha
            out[-(dist + 1)] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    return out


def vertical_streaks_dominate(band) -> bool:
    """True when an extension is vertical colour stripes.

    Vertical streaks have strong horizontal gradient energy, almost no change
    down a column, and column means that jump from one x to the next. A soft
    wash, a flat field and a real gradient do not.
    """
    import cv2
    import numpy as np

    if band is None or getattr(band, "ndim", 0) != 3:
        return False
    if band.shape[0] < 24 or band.shape[1] < 24:
        return False
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if gray.shape[0] > 80:
        gray = gray[: gray.shape[0] - 36]
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    energy_x = float(np.mean(gx * gx))
    energy_y = float(np.mean(gy * gy)) + 1e-6
    ratio = energy_x / energy_y
    var_y = float(np.mean(np.var(gray, axis=0)))
    columns = gray.mean(axis=0)
    sigma = max(8.0, 0.06 * float(columns.size))
    slow = cv2.GaussianBlur(columns.reshape(1, -1), (0, 0), sigma).ravel()
    var_x = float(np.var(columns - slow))
    return bool(ratio >= 6.0 and var_y <= 30.0 and var_x >= 40.0)


def _luma(bgr):
    import numpy as np

    image = bgr.astype(np.float32)
    return image[..., 0] * 0.114 + image[..., 1] * 0.587 + image[..., 2] * 0.299


def _orient_join(image, join: int, side: str):
    """Put the extension above row `join`, which is the first row of the picture."""
    import numpy as np

    side = str(side or "top")
    if side == "top":
        return image, int(join)
    if side == "bottom":
        flipped = image[::-1]
        return flipped, int(image.shape[0] - int(join))
    turned = np.ascontiguousarray(np.transpose(image, (1, 0, 2)))
    if side == "left":
        return turned, int(join)
    flipped = turned[::-1]
    return flipped, int(turned.shape[0] - int(join))


def seam_metrics(image, join: int, side: str = "top", px_per_mm: float | None = None) -> dict:
    """Row-to-row luminance step at a large-block join, and a thin-band check.

    The join step is the mean absolute change across the seam, ±3 rows.
    It fails when that step is more than twice the larger of the median step
    inside the picture and inside the extension, or when the join/reference
    ratio is above 0.4. A one-row light or dark band fails the row-mean check.
    """
    import numpy as np

    work, seam = _orient_join(image, join, side)
    luma = _luma(work)
    height = int(luma.shape[0])
    ppm = float(px_per_mm) if px_per_mm else (300.0 / 25.4)
    gap = _mm_px(TALL_OVERLAP_MM + 2.0, ppm, 8)
    slab = _mm_px(12.0, ppm, 12)
    seam = int(max(3, min(height - 4, int(seam))))

    window = luma[seam - 3:seam + 4]
    join_step = float(np.mean(np.abs(np.diff(window, axis=0))))

    def median_step(start: int, stop: int) -> float:
        start = int(max(0, start))
        stop = int(min(height, stop))
        if stop - start < 4:
            return 0.0
        steps = np.abs(np.diff(luma[start:stop], axis=0)).mean(axis=1)
        return float(np.median(steps))

    art_step = median_step(seam + gap, seam + gap + slab)
    ext_step = median_step(seam - gap - slab, seam - gap)
    reference = max(art_step, ext_step)
    step_limit = 2.0 * reference
    ratio = join_step / max(reference, 1e-6)
    ok_step = bool(join_step <= step_limit + 0.05)
    # A smeared pad can stay under 2× and still show a hard line. 0.4 is the
    # accepted join (0.165) with room, and it rejects the 0.899 colour pad.
    # A smooth gradient has a reference step of about 0, so join/reference
    # explodes on a 0.02 step the 2× rule already accepts. The cap applies
    # once the picture has real texture, as on the poster.
    ok_ratio = True if reference < 0.5 else bool(ratio <= 0.4 + 1e-9)

    means = luma.mean(axis=1)
    band_dev = float(max(
        abs(means[seam] - 0.5 * (means[seam - 2] + means[seam + 2])),
        abs(means[seam - 1] - 0.5 * (means[seam - 3] + means[seam + 1])),
    ))
    art_lo = min(height - 2, seam + gap)
    art_hi = min(height, art_lo + slab)
    jumps = np.abs(np.diff(means[art_lo:art_hi])) if art_hi - art_lo >= 4 else np.array([0.0])
    typical = float(np.median(jumps)) if jumps.size else 0.0
    band_limit = max(3.0, 2.0 * typical)
    ok_band = bool(band_dev <= band_limit + 0.05)
    return {
        "joinStep": round(join_step, 3),
        "artStep": round(art_step, 3),
        "extStep": round(ext_step, 3),
        "stepLimit": round(step_limit, 3),
        "stepRatio": round(ratio, 3),
        "bandDev": round(band_dev, 3),
        "bandLimit": round(band_limit, 3),
        "okStep": ok_step,
        "okRatio": ok_ratio,
        "okBand": ok_band,
        "ok": bool(ok_step and ok_band and ok_ratio),
    }


def _colour_pad(band, pad: int, forward: bool):
    """Continue one edge from its own colour. Nothing from inside the picture is copied out.

    A mirrored photograph, blurred on a coarse grid, stretches into blocky streaks.
    The colour here is a blur along the rim, plus a little grain that is the same
    in every direction, so it cannot become a stripe.
    `band` row 0 is the picture edge when forward is false.
    """
    import numpy as np

    width = int(band.shape[1])
    if pad <= 0:
        return np.zeros((0, width, 3), np.uint8)
    work = np.ascontiguousarray(band.astype(np.float32))
    if not forward:
        work = work[::-1]
    height = int(work.shape[0])
    depth = min(5, height)
    strip = work[-depth:]
    median = np.median(strip, axis=0)
    distance = np.linalg.norm(strip - median[None, :, :], axis=2)
    mad = float(np.median(distance)) if distance.size else 0.0
    thresh = max(36.0, mad * 4.0)
    outlier = distance > thresh
    cleaned = strip.copy()
    if outlier.any():
        filled = np.repeat(median[None, :, :], depth, axis=0)
        cleaned[outlier] = filled[outlier]
    edge = _lowpass_along(cleaned[-1], EDGE_LOWPASS_FRACTION)
    broad = _lowpass_along(edge, 0.15)
    broad_far = _lowpass_along(_lowpass_along(cleaned[0], EDGE_LOWPASS_FRACTION), 0.15)
    slope = np.clip((broad - broad_far) / float(max(depth - 1, 1)), -0.35, 0.35)
    steps = np.arange(1, pad + 1, dtype=np.float32)[:, None, None]
    fade = np.exp(-steps / 80.0)
    ease = np.clip((steps - 4.0) / 24.0, 0.0, 1.0)
    continued = edge[None, :, :] * (1.0 - ease) + broad[None, :, :] * ease
    continued = continued + slope[None, :, :] * steps * fade
    inlier = ~outlier
    pair = inlier[:, :-1] & inlier[:, 1:] if width > 1 else inlier
    sigma = np.zeros(3, np.float32)
    if width > 1:
        delta = cleaned[:, 1:, :] - cleaned[:, :-1, :]
        for channel in range(3):
            vals = delta[:, :, channel][pair]
            sigma[channel] = float(np.std(vals) / np.sqrt(2.0)) if vals.size else 0.0
    sigma = np.clip(sigma, 0.0, 8.0)
    rng = np.random.default_rng((depth * 10007 + width * 17 + pad) % (2**32))
    grain = rng.normal(0.0, 1.0, (pad, width, 3)).astype(np.float32) * sigma.reshape(1, 1, 3)
    true_edge = work[-1]
    seam = np.clip(steps / 4.0, 0.0, 1.0)
    grain_in = np.clip(steps / 4.0, 0.0, 1.0)
    mixed = true_edge[None, :, :] * (1.0 - seam) + continued * seam
    out = np.clip(np.rint(mixed + grain * grain_in), 0, 255).astype(np.uint8)
    if not forward:
        out = out[::-1]
    return out


def _extend_vertical(img, top: int, bottom: int, px_per_mm: float | None = None):
    import numpy as np

    ppm = float(px_per_mm) if px_per_mm else (300.0 / 25.4)
    parts = []
    height = img.shape[0]
    core = img if img.dtype == np.uint8 else np.clip(img, 0, 255).astype(np.uint8)
    art = core
    ramp = _mm_px(TALL_RAMP_MM, ppm, 8)
    match_depth = _mm_px(6.0, ppm, 4)
    sample = min(5, height)

    def strong_pad(forward: bool, pad: int, block: int):
        """The accepted tall join: blur starts near sigma 3 and feathers into the art."""
        nonlocal art
        source = core[-block:] if forward else core[:block]
        protect = _protect_rows(core, ppm, from_end=forward)
        overlap = _join_overlap(core, ppm, from_end=forward)
        if art is core:
            art = np.ascontiguousarray(core)
        built = _mirror_extend(
            source, pad, forward=forward, strong=True, ramp_px=ramp, protect_px=protect,
        )
        # The pad's first row touches the picture when the pad is below it.
        at_end = not forward
        built = _match_join_colour(built, art, at_end=at_end, depth=max(match_depth, overlap))
        art = _feather_into_art(built, art, overlap, at_end=at_end)
        return built

    def short_pad(forward: bool, pad: int):
        """A short side stays a flat continuation. A mirrored 28px rim becomes stripes."""
        source = core[-sample:] if forward else core[:sample]
        return _colour_pad(source, pad, forward=forward)

    top_part = None
    bottom_part = None
    if top:
        if top >= TALL_PAD_PX:
            top_part = strong_pad(False, top, min(top, height))
        else:
            top_part = short_pad(False, top)
    if bottom:
        if bottom >= TALL_PAD_PX:
            bottom_part = strong_pad(True, bottom, min(bottom, height))
        else:
            bottom_part = short_pad(True, bottom)
    if top_part is not None:
        parts.append(top_part)
    parts.append(art)
    if bottom_part is not None:
        parts.append(bottom_part)
    if len(parts) == 1:
        return parts[0]
    return np.concatenate(parts, axis=0)


def _extend_edges(img, top: int, bottom: int, left: int, right: int, px_per_mm: float | None = None):
    """Grow a picture without copying one row of pixels down the page."""
    import numpy as np

    out = img
    if top or bottom:
        out = _extend_vertical(out, top, bottom, px_per_mm=px_per_mm)
    if left or right:
        turned = np.ascontiguousarray(np.transpose(out, (1, 0, 2)))
        turned = _extend_vertical(turned, left, right, px_per_mm=px_per_mm)
        out = np.ascontiguousarray(np.transpose(turned, (1, 0, 2)))
    return out


def _bottom_bar_row(img) -> int | None:
    """Row where a flat contact band starts, or None when the bottom is just the picture.

    The band is a solid colour with the address painted on it, stopped by a rule
    or a change of colour. A flat page, a gradient and a photograph are not a band.
    """
    import numpy as np

    height, width = img.shape[:2]
    if height < 48 or width < 32:
        return None
    tail_n = min(10, height // 5)
    tail = img[-tail_n:].astype(np.float32)
    # Std across the row. A solid footer is near zero; channel differences are not.
    if float(tail.std(axis=1).mean()) > 10.0:
        return None
    colour = np.median(tail.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(img.astype(np.float32) - colour.reshape(1, 1, 3), axis=2)
    frac = (distance < 60.0).mean(axis=1)
    if float(frac[-tail_n:].mean()) < 0.92:
        return None
    max_run = int(height * 0.28)
    min_run = max(8, int(height * 0.04))
    max_gap = max(8, int(height * 0.03))
    last_good = height - 1
    gap = 0
    stop = max(-1, height - 1 - max_run - max_gap)
    for y in range(height - 1, stop, -1):
        if frac[y] >= 0.40:
            last_good = y
            gap = 0
            continue
        # A gold rule or a photo edge. Do not step across it into the picture.
        if frac[y] < 0.32:
            break
        gap += 1
        if gap > max_gap:
            break
    start = int(last_good)
    run = height - start
    if run < min_run or run > max_run:
        return None
    above = frac[max(0, start - run):start]
    if above.size and float(np.mean(above)) > 0.72:
        return None
    return start


def _soften_art_rim(canvas, top: int, left: int, art_h: int, art_w: int, include_top: bool = True):
    """Feather only the outer rim of the picture into a blur. The contact band stays sharp."""
    import cv2
    import numpy as np

    top_depth = min(32, max(0, art_h // 8))
    side_depth = min(12, max(0, art_w // 10))
    if top_depth < 4 and side_depth < 4:
        return canvas
    y0, x0 = int(top), int(left)
    y1, x1 = y0 + int(art_h), x0 + int(art_w)
    art = canvas[y0:y1, x0:x1]
    if art.size == 0:
        return canvas
    blurred = cv2.GaussianBlur(art, (0, 0), 7)
    alpha = np.zeros(art.shape[:2], np.float32)
    if include_top and top_depth >= 4:
        ramp = np.linspace(0.62, 0.0, top_depth, dtype=np.float32)
        alpha[:top_depth] = np.maximum(alpha[:top_depth], ramp[:, None])
    if side_depth >= 4:
        ramp = np.linspace(0.45, 0.0, side_depth, dtype=np.float32)
        alpha[:, :side_depth] = np.maximum(alpha[:, :side_depth], ramp[None, :])
        alpha[:, -side_depth:] = np.maximum(alpha[:, -side_depth:], ramp[::-1][None, :])
    mixed = art.astype(np.float32) * (1.0 - alpha[..., None]) + blurred.astype(np.float32) * alpha[..., None]
    out = canvas.copy()
    out[y0:y1, x0:x1] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    return out


def _restitch(canvas, top: int, left: int, art_h: int, art_w: int, reach: int = 8, include_top: bool = True, include_sides: bool = True):
    """Pull a short pad's seam onto the picture. A tall blend already owns its join."""
    import numpy as np

    out = canvas
    y0, x0 = int(top), int(left)
    y1, x1 = y0 + int(art_h), x0 + int(art_w)
    if y0 <= 0 or x1 <= x0:
        return out
    if include_top:
        edge = out[y0, x0:x1].astype(np.float32)
        for dist in range(1, reach + 1):
            y = y0 - dist
            if y < 0:
                break
            weight = dist / float(reach + 1)
            row = out[y, x0:x1].astype(np.float32)
            out[y, x0:x1] = np.clip(edge * (1.0 - weight) + row * weight, 0, 255).astype(np.uint8)
    if not include_sides:
        return out
    for dist in range(1, reach + 1):
        weight = dist / float(reach + 1)
        x = x0 - dist
        if x >= 0:
            edge_col = out[y0:y1, x0].astype(np.float32)
            col = out[y0:y1, x].astype(np.float32)
            out[y0:y1, x] = np.clip(edge_col * (1.0 - weight) + col * weight, 0, 255).astype(np.uint8)
        x = x1 - 1 + dist
        if x < out.shape[1]:
            edge_col = out[y0:y1, x1 - 1].astype(np.float32)
            col = out[y0:y1, x].astype(np.float32)
            out[y0:y1, x] = np.clip(edge_col * (1.0 - weight) + col * weight, 0, 255).astype(np.uint8)
    return out


def _layout_with_bar(img, trim_w: float, trim_h: float):
    """Lengthen a footer poster. The band stays at the bottom, 4 mm inside the trim.

    Side pads of the same 4 mm keep flush lettering off the knife. The original
    rectangle is not scaled. Extra height above the picture is a mirrored blur.
    """
    height, width = img.shape[:2]
    if float(trim_w) <= 2.0 * SAFE_ZONE_MM + 1.0:
        return None
    canvas_w = int(round(width * float(trim_w) / (float(trim_w) - 2.0 * SAFE_ZONE_MM)))
    canvas_h = int(round(canvas_w * float(trim_h) / float(trim_w)))
    if canvas_w <= width + 2 or canvas_h <= height + 4:
        return None
    side_left = (canvas_w - width) // 2
    side_right = canvas_w - width - side_left
    bottom = max(1, int(round(SAFE_ZONE_MM * canvas_w / float(trim_w))))
    extra = canvas_h - height
    if extra <= bottom + 4:
        return None
    top = extra - bottom
    px_per_mm = float(canvas_w) / float(trim_w)
    vertical = _extend_edges(img, top, bottom, 0, 0, px_per_mm=px_per_mm)
    fitted = _extend_edges(vertical, 0, 0, side_left, side_right, px_per_mm=px_per_mm)
    if fitted.shape[0] != canvas_h or fitted.shape[1] != canvas_w:
        return None
    # A tall pad already feathered its join. The 4 mm side pads are still short.
    tall_top = top >= TALL_PAD_PX
    tall_side = side_left >= TALL_PAD_PX or side_right >= TALL_PAD_PX
    fitted = _soften_art_rim(fitted, top, side_left, height, width, include_top=not tall_top)
    fitted = _restitch(
        fitted, top, side_left, height, width,
        include_top=not tall_top, include_sides=not tall_side,
    )
    return fitted


def _extend_to_product(img, trim_w: float, trim_h: float, source_path: str, decisions: list) -> tuple:
    """Keep the whole picture. Fill a shape gap by mirroring the rim, not by smearing one pixel.

    A full-page mirror would repeat the artwork. Copying the outer pixel draws
    stripes. A contact band is left at the bottom of the page.
    """
    from ai_artwork import ratios_differ

    del source_path
    height, width = img.shape[:2]
    src = width / float(max(height, 1))
    target = float(trim_w) / float(trim_h)
    delta = abs(src - target) / target if target else 0
    if not ratios_differ(width, height, trim_w, trim_h):
        decisions.append("The picture already matches the product shape, so nothing was cropped or stretched.")
        return img, False, delta
    if src > target and _bottom_bar_row(img) is not None:
        fitted = _layout_with_bar(img, trim_w, trim_h)
        if fitted is not None:
            decisions.append(
                "The picture was a different shape from the product. The contact band was kept at the bottom, 4 mm inside the trim. The extra space continues the picture with a soft edge. It was not stretched and the picture was not copied."
            )
            return fitted, True, delta
    if src > target:
        new_h = max(height, int(round(width / target)))
        pad = new_h - height
        top = pad // 2
        ppm = float(new_h) / float(trim_h) if trim_h else None
        fitted = _extend_edges(img, top, pad - top, 0, 0, px_per_mm=ppm)
    else:
        new_w = max(width, int(round(height * target)))
        pad = new_w - width
        left = pad // 2
        ppm = float(new_w) / float(trim_w) if trim_w else None
        fitted = _extend_edges(img, 0, 0, left, pad - left, px_per_mm=ppm)
    decisions.append(
        "The picture was a different shape from the product. The whole picture was kept and the gap was filled by continuing the edge colour and texture. It was not stretched and the picture was not copied."
    )
    return fitted, True, delta


def _text_near_trim(report: dict) -> bool:
    rescue = (report or {}).get("rescue") or {}
    note = str(rescue.get("note") or "")
    if "may still sit" in note:
        return True
    try:
        percent = float(rescue.get("percent") or 0)
    except (TypeError, ValueError):
        percent = 0
    return bool(rescue.get("applied")) and percent >= 2.9


def _boxes_mm(path: str) -> dict:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        media = page.mediabox
        trim = page.trimbox
        return {
            "mediaWidthMm": round(media.width * 25.4 / 72.0, 2),
            "mediaHeightMm": round(media.height * 25.4 / 72.0, 2),
            "trimWidthMm": round(trim.width * 25.4 / 72.0, 2),
            "trimHeightMm": round(trim.height * 25.4 / 72.0, 2),
        }
    finally:
        doc.close()


def _compile(src: str, output_pdf: str, trim_w: float, trim_h: float) -> dict:
    script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    status = tempfile.NamedTemporaryFile(prefix="quick-status-", suffix=".json", delete=False)
    result = tempfile.NamedTemporaryFile(prefix="quick-result-", suffix=".json", delete=False)
    status.close()
    result.close()
    cmd = [
        sys.executable,
        script,
        "--input", src,
        "--output", output_pdf,
        "--strategy", "auto",
        "--color-space", "cmyk",
        "--trim-w", str(trim_w),
        "--trim-h", str(trim_h),
        "--bleed-mm", str(BLEED_MM),
        "--status-file", status.name,
        "--result-file", result.name,
        "--base-name", "quick-print",
        "--creep-mm", "0",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=240,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        payload = {}
        if os.path.exists(result.name):
            try:
                with open(result.name, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                payload = {}
        payload["returncode"] = proc.returncode
        if proc.returncode != 0 and not payload.get("error"):
            tail = (proc.stderr or "")[-500:]
            payload["error"] = tail or "The press compile did not finish."
        return payload
    finally:
        for path in (status.name, result.name):
            try:
                os.unlink(path)
            except OSError:
                pass


def _write_proof(press_path: str, png_path: str, pdf_path: str, caption: str) -> list:
    """One proof picture per press page. Page 1 keeps the original png name."""
    import pymupdf as fitz

    src = fitz.open(press_path)
    paths = []
    try:
        proof = fitz.open()
        footer = 32
        stem, ext = os.path.splitext(png_path)
        for index, page in enumerate(src):
            out = proof.new_page(width=page.rect.width, height=page.rect.height + footer)
            out.show_pdf_page(page.rect, src, index)
            trim = page.trimbox
            out.draw_rect(trim, color=(0.86, 0.05, 0.45), width=1.4)
            out.insert_text(
                (14, page.rect.height + 20),
                caption[:180],
                fontsize=8,
                fontname="helv",
                color=(0.15, 0.15, 0.15),
            )
            page_png = png_path if index == 0 else f"{stem}-{index + 1}{ext}"
            pix = out.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            pix.save(page_png)
            paths.append(page_png)
        proof.save(pdf_path, deflate=True, garbage=4)
        proof.close()
        return paths
    finally:
        src.close()


def _blank(light_info: dict, decisions: list, product: dict, quantity, notes: str) -> dict:
    return {
        "light": light_info["light"],
        "reasons": light_info["reasons"],
        "decisions": decisions,
        "clientMessage": light_info["clientMessage"],
        "pressPath": "",
        "proofPng": "",
        "proofPdf": "",
        "bleedMm": BLEED_MM,
        "existingBleedKept": False,
        "productId": product.get("id"),
        "productLabel": product.get("label"),
        "trimW": product.get("widthMm"),
        "trimH": product.get("heightMm"),
        "quantity": quantity,
        "notes": notes,
        "upscale": 0,
        "pressEngine": None,
        "enginePassed": False,
    }


def make_print_ready(
    src_path: str,
    output_dir: str,
    trim_w: float,
    trim_h: float,
    product_id: str,
    product_label: str,
    filename: str = "",
    quantity=None,
    notes: str = "",
    detect_size: bool = False,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    display = _safe_name(filename or os.path.basename(src_path))
    decisions = [
        "Sales quick mode decided this file on its own. Nobody was asked a question.",
        "Bleed is 5 mm on every side.",
        "The press file is CMYK, with rich black kept.",
    ]
    product = {"id": product_id, "label": product_label, "widthMm": trim_w, "heightMm": trim_h}
    ext = _ext(src_path, display)

    if detect_size and ext in VECTOR_EXT | IMAGE_EXT:
        try:
            if ext in IMAGE_EXT:
                img = _read_image(src_path)
                if img is not None:
                    # Pixel size is not a trim size. Leave the default product.
                    pass
            else:
                opened, prep_error = _prepare_vector(src_path, ext) if ext in (".ai", ".eps") else (src_path, "")
                if opened and not prep_error:
                    measured = _pdf_trim_mm(opened)
                    if measured:
                        found, rotated = _match_product(*measured)
                        if found and rotated:
                            twin = _product_for_trim(float(found["heightMm"]), float(found["widthMm"]))
                            if twin:
                                found = twin
                            else:
                                found = {
                                    **found,
                                    "id": f"{found.get('id')}-landscape",
                                    "label": f"{found.get('label')} landscape",
                                    "widthMm": found["heightMm"],
                                    "heightMm": found["widthMm"],
                                }
                        if found:
                            product = found
                            trim_w = float(found["widthMm"])
                            trim_h = float(found["heightMm"])
                            decisions.append(f"The file's trim matches {found['label']}, so that size was used.")
        except Exception:
            pass

    decisions.insert(3, f"Product set to {product.get('label')} ({trim_w:g} × {trim_h:g} mm).")
    if quantity:
        decisions.append(f"Quantity noted: {quantity}.")
    if notes:
        decisions.append(f"Note from sales: {notes[:240]}")

    if ext in OFFICE_EXT:
        info = decide_light({"kind": "office"})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    if ext not in IMAGE_EXT | VECTOR_EXT:
        info = decide_light({"kind": "unsupported", "readable": False})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    if not os.path.exists(src_path) or os.path.getsize(src_path) < 16:
        info = decide_light({"kind": "corrupt", "readable": False})
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)

    work_path = src_path
    raster = None
    aspect_extended = False
    aspect_delta = 0.0
    upscale = 1.0
    existing_kept = False
    ocr_low = False
    ocr_doubtful = False
    ocr_doubtful_reason = ""
    vector_built = None
    vector_source = None
    upscale_job = None
    lettering_note = "The original lettering is kept."

    try:
        if ext in (".ai", ".eps"):
            opened, prep_error = _prepare_vector(src_path, ext)
            if prep_error or not opened:
                info = decide_light({"kind": "corrupt", "readable": False})
                info["clientMessage"] = prep_error or CORRUPT_MESSAGE
                info["reasons"] = ["This Illustrator file could not be opened."]
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            work_path = opened
            ext = ".pdf"
            decisions.append("The Illustrator file was read onto the normal PDF path.")

        live_type = False
        if ext == ".pdf":
            from ai_rebuild import _pdf_has_live_type

            # Live text and vector drawings stay as they are. A raster page can still be traced.
            live_type = _pdf_has_live_type(work_path)
            measured = _pdf_trim_mm(work_path)
            fit = _fit_page_to_trim(*measured, trim_w, trim_h) if measured else None
            if fit and fit["rotated"]:
                turned = _product_for_trim(fit["trimW"], fit["trimH"])
                if turned:
                    product = turned
                else:
                    product = {
                        **product,
                        "widthMm": fit["trimW"],
                        "heightMm": fit["trimH"],
                        "label": f"{product.get('label')} landscape",
                    }
                trim_w = fit["trimW"]
                trim_h = fit["trimH"]
                _remember_product(decisions, product, trim_w, trim_h)
                decisions.append("The page matches this product when it is turned the other way, so that orientation was used.")
            if measured:
                decisions.append(
                    f"The PDF trim is {measured[0]:.1f} × {measured[1]:.1f} mm."
                )
            if fit:
                bleed = fit["bleed"]
                if bleed.get("kind") == "existing":
                    decisions.append(
                        f"The page is the trim plus about {bleed['left']:.1f} mm of bleed already. "
                        "That bleed is kept and only the shortfall is added."
                    )
                else:
                    decisions.append("The PDF already fits this product, so the page was kept and sent to the press engine.")
            elif live_type:
                decisions.append(
                    "This PDF has live type and the wrong proportions. The edges cannot be extended without losing that type."
                )
                info = decide_light({"unextendable": True})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            else:
                decisions.append(
                    "The PDF shape does not match the product. The page was placed whole and the edges were extended. It was not stretched."
                )
                raster = _render_pdf_image(work_path)
                ext = ".png"
        if ext in IMAGE_EXT or raster is not None:
            if raster is None:
                raster = _read_image(work_path)
            if raster is None:
                info = decide_light({"kind": "corrupt", "readable": False})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            src_h, src_w = raster.shape[:2]
            if float(raster.mean()) > 250 and float(raster.std()) < 4:
                info = decide_light({"missingContent": True})
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            upscale = _cover_scale(src_w, src_h, trim_w, trim_h)
            if too_small(src_w, src_h, trim_w, trim_h):
                info = decide_light({
                    "tooSmall": True,
                    "tooSmallMessage": too_small_message(str(product.get("label") or "this size"), trim_w, trim_h),
                })
                return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
            from ai_rebuild import assess

            # Assess the original file. Extending the shape must not hide an AI-sized picture.
            assess_path = src_path if _ext(src_path, display) in IMAGE_EXT else work_path
            verdict = assess(assess_path, trim_w, trim_h, BLEED_MM)
            detected = bool(verdict.get("detected"))
            raster, _turned = _rotate_to_product(raster, trim_w, trim_h, decisions)
            picture = raster
            # A contact band is laid out first so the trace sits on that page and
            # the band stays at the bottom. A plain side pad is not part of the
            # read: the smaller scale was dropping small marks such as a card's "7".
            raster, aspect_extended, aspect_delta = _extend_to_product(raster, trim_w, trim_h, work_path, decisions)
            if aspect_extended and _bottom_bar_row(picture) is not None:
                vector_source = raster
            else:
                vector_source = picture
            try:
                from ai_upscale import start_plate_upscale

                upscale_job = start_plate_upscale(vector_source, trim_w, trim_h)
            except Exception as exc:
                sys.stderr.write(f"[AI-UPSCALE] not started ({str(exc)[:160]})\n")
                upscale_job = None
            fitted_path = os.path.join(output_dir, "fitted.png")
            _write_png(raster, fitted_path)
            work_path = fitted_path
            from vector_text_v2 import font_substitution_enabled, vector_rebuild_enabled

            fonts_on = font_substitution_enabled()
            rebuild_on = vector_rebuild_enabled()
            lettering_blocks = None
            has_lettering = False
            outer_ocr_s = 0.0
            # Font mode on an AI file reads the page itself. Every other raster is read once here.
            if rebuild_on and not live_type and not (fonts_on and detected):
                try:
                    from vector_text_v2 import read_blocks
                    from vector_trace import is_lettering

                    ocr_started = time.perf_counter()
                    found = read_blocks(vector_source, extra=False)
                    outer_ocr_s = time.perf_counter() - ocr_started
                    has_lettering = any(
                        isinstance(block, dict) and is_lettering(str(block.get("text") or ""))
                        for block in (found or [])
                    )
                    if not fonts_on:
                        lettering_blocks = found
                except Exception:
                    has_lettering = False
                    lettering_blocks = None
            trace_raster = rebuild_on and not live_type and (detected or has_lettering)
            if trace_raster:
                if detected and fonts_on:
                    decisions.append("This looks like AI-generated artwork. The words are set as vector type.")
                elif detected:
                    decisions.append("This looks like AI-generated artwork. The lettering is traced as vector shapes.")
                elif fonts_on:
                    decisions.append("This raster has lettering. The words are set as vector type.")
                else:
                    decisions.append("This raster has lettering. The lettering is traced as vector shapes.")
                try:
                    from vector_text_v2 import rebuild_fitted

                    vector_built = rebuild_fitted(
                        vector_source,
                        trim_w,
                        trim_h,
                        os.path.join(output_dir, "press.pdf"),
                        progress=_mark,
                        blocks=None if fonts_on else lettering_blocks,
                        ocr_s=outer_ocr_s if lettering_blocks is not None else None,
                        upscale=upscale_job,
                    )
                except Exception as exc:
                    vector_built = {
                        "ok": False,
                        "amber": True,
                        "reason": f"Vector type failed ({str(exc)[:140]}). The original lettering was kept.",
                        "decisions": [],
                    }
                if vector_built.get("ok"):
                    work_path = os.path.join(output_dir, "press.pdf")
                    if vector_built.get("mode") == "trace":
                        lettering_note = "The lettering was traced as vector shapes."
                    else:
                        lettering_note = "The lettering was set as vector type."
                    aspect_extended = False
                    decisions = [line for line in decisions if "gap was filled" not in line]
                    decisions.append(
                        "The whole picture was placed with one scale. Any gap was filled by continuing the edge."
                    )
                    for line in vector_built.get("decisions") or []:
                        decisions.append(str(line))
                    if vector_built.get("amber"):
                        lettering_note = vector_built.get("reason") or lettering_note
                else:
                    lettering_note = str(vector_built.get("reason") or "The vector check failed, so the original lettering was kept.")
                    decisions.append(lettering_note)
            elif detected and not rebuild_on:
                decisions.append("Vector type is switched off. The original lettering is kept and the picture is enlarged.")
            elif live_type:
                decisions.append("This file already has live type, so the lettering was left as it is.")
            else:
                decisions.append("This was not treated as AI artwork. The original lettering is kept.")
            if not (vector_built and vector_built.get("ok")):
                _mark("enlarging", lettering_note)
                upscaled_path = os.path.join(output_dir, "upscaled.png")
                try:
                    from ai_upscale import apply_ai_upscale

                    enlarged = apply_ai_upscale(fitted_path, {
                        "trim_w_mm": trim_w,
                        "trim_h_mm": trim_h,
                        "bleed_mm": 0,
                        "output_path": upscaled_path,
                    })
                except Exception as exc:
                    enlarged = {"used_original": True, "message": str(exc)[:160]}
                if enlarged.get("enhanced_path") and os.path.exists(str(enlarged.get("enhanced_path"))) and not enlarged.get("used_original"):
                    work_path = str(enlarged["enhanced_path"])
                    provider = str(enlarged.get("provider") or "basic")
                    if provider == "replicate":
                        decisions.append("The picture was enlarged with Real-ESRGAN. The original lettering was kept.")
                    else:
                        decisions.append("The picture was enlarged with Lanczos on this computer. The original lettering was kept.")
                else:
                    decisions.append("The original picture was kept and will be placed at 300 DPI. The original lettering was kept.")
            if vector_built and vector_built.get("ok"):
                if upscale >= 1.15:
                    decisions.append(
                        f"The original picture needs about {upscale:.1f}× to reach print size. The press picture is at least 400 PPI."
                    )
                else:
                    decisions.append("The original picture is already sharp enough. The press picture is at least 400 PPI.")
            elif upscale >= 1.15:
                decisions.append(f"The original picture needs about {upscale:.1f}× to reach 300 DPI at this size. The press file is built at 300 DPI.")
            else:
                decisions.append("The original picture is already sharp enough for 300 DPI at this size.")
    except Exception as exc:
        info = decide_light({"kind": "corrupt", "readable": False})
        decisions.append(f"The file could not be prepared ({str(exc)[:140]}).")
        return _finish(_blank(info, decisions, product, quantity, notes), output_dir)
    finally:
        if upscale_job is not None:
            try:
                upscale_job.cancel()
            except Exception:
                pass

    press_path = os.path.join(output_dir, "press.pdf")
    _mark("press", lettering_note)
    vector_ok = bool(vector_built and vector_built.get("ok") and os.path.exists(press_path) and os.path.getsize(press_path) > 1000)
    if vector_ok:
        compiled = {
            "success": True,
            "pressEngine": {
                "passed": True,
                "headline": "Vector type",
                "reason": "",
                "existingBleed": False,
            },
        }
        engine = compiled["pressEngine"]
        press_ok = True
        decisions.append("Automatic bleed added the 5 mm edge. The trim sits 5 mm inside that edge.")
    else:
        compiled = _compile(work_path, press_path, trim_w, trim_h)
        engine = compiled.get("pressEngine") or {}
        press_ok = bool(compiled.get("success") and os.path.exists(press_path) and os.path.getsize(press_path) > 1000)
        if isinstance(engine, dict) and engine.get("fullBleedKept"):
            existing_kept = True
            decisions.append("This file already had 5 mm bleed. That bleed was kept and was not added again.")
        elif isinstance(engine, dict) and engine.get("existingBleed"):
            existing_kept = True
        elif press_ok:
            decisions.append("Automatic bleed added the 5 mm edge. Bleed was not stacked on an existing 5 mm.")
    if isinstance(engine, dict) and engine.get("headline"):
        decisions.append(f"Press engine: {engine.get('headline')}.")

    facts = {
        "kind": "image",
        "readable": True,
        "tooSmall": False,
        "compiled": press_ok,
        "compileError": str(compiled.get("error") or "")[:180],
        "ocrLow": ocr_low,
        "ocrDoubtful": ocr_doubtful,
        "ocrDoubtfulReason": ocr_doubtful_reason,
        "textNearTrim": _text_near_trim(engine if isinstance(engine, dict) else {}),
        "upscale": upscale,
        "aspectExtended": aspect_extended,
        "aspectDelta": aspect_delta,
        "enginePassed": bool(engine.get("passed")) if press_ok else False,
        "engineReason": (engine.get("reason") if isinstance(engine, dict) else "") or "",
        "vectorAmber": bool(vector_built and vector_built.get("amber")),
        "vectorAmberReason": str((vector_built or {}).get("reason") or ""),
    }
    checklist = None
    if press_ok:
        try:
            from green_gate import assess

            checklist = assess(press_path, trim_w, trim_h, {
                "upscale": upscale,
                "textGate": (vector_built or {}).get("text_gate") or [],
                "edge": (vector_built or {}).get("edge") or {},
                "sourceBgr": vector_source,
                "placement": (vector_built or {}).get("placement") or {},
            })
            facts["checklist"] = checklist
        except Exception:
            checklist = None
    info = decide_light(facts)
    result = _blank(info, decisions, product, quantity, notes)
    result["checklist"] = list((checklist or {}).get("items") or [])
    result["upscale"] = round(upscale, 3)
    result["existingBleedKept"] = existing_kept
    result["enginePassed"] = facts["enginePassed"]
    result["pressEngine"] = engine if isinstance(engine, dict) else None
    if press_ok and info["light"] != "red":
        result["pressPath"] = press_path
        try:
            boxes = _boxes_mm(press_path)
            result.update(boxes)
        except Exception:
            pass
        _mark("proof", lettering_note)
        proof_png = os.path.join(output_dir, "proof.png")
        proof_pdf = os.path.join(output_dir, "proof.pdf")
        try:
            proof_pages = _write_proof(
                press_path,
                proof_png,
                proof_pdf,
                "Proof. The pink line is the trim. This is not the press file.",
            )
            result["proofPng"] = proof_pages[0] if proof_pages else proof_png
            result["proofPaths"] = proof_pages
            result["pageCount"] = len(proof_pages) or 1
            result["proofPdf"] = proof_pdf
            decisions.append("A client proof with the trim line was made. No approval step is required.")
        except Exception as exc:
            decisions.append(f"The press file is ready. The proof picture could not be drawn ({str(exc)[:80]}).")
    elif not press_ok:
        result["pressPath"] = ""
    result["decisions"] = decisions
    result["letteringNote"] = lettering_note
    if vector_built:
        result["vectorText"] = {
            "ok": bool(vector_built.get("ok")),
            "amber": bool(vector_built.get("amber")),
            "elapsed_s": vector_built.get("elapsed_s"),
            "timings": vector_built.get("timings") or {},
            "provider": vector_built.get("provider") or "",
            "vectorLines": vector_built.get("vector_lines") or 0,
            "rasterLines": vector_built.get("raster_lines") or 0,
            "qa": vector_built.get("qa") or {},
            "lines": vector_built.get("lines") or [],
            "textGate": vector_built.get("text_gate") or [],
            "sourceGuard": bool(vector_built.get("source_guard")),
            "edge": vector_built.get("edge") or {},
            "placement": vector_built.get("placement") or {},
        }
    return _finish(result, output_dir)


def _mark(stage: str, note: str = "") -> None:
    try:
        from job_progress import write_progress_from_env

        write_progress_from_env(stage, note)
    except Exception:
        pass


def _finish(result: dict, output_dir: str) -> dict:
    _mark("done", str(result.get("letteringNote") or "Finished."))
    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    lines = [f"Result: {str(result.get('light') or '').upper()}"]
    for line in result.get("decisions") or []:
        lines.append(f"- {line}")
    for line in result.get("reasons") or []:
        lines.append(f"- Look: {line}")
    if result.get("clientMessage"):
        lines.append("")
        lines.append(result["clientMessage"])
    with open(os.path.join(output_dir, "decisions.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Make one file print-ready")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trim-w", type=float, default=148)
    parser.add_argument("--trim-h", type=float, default=210)
    parser.add_argument("--product-id", default="a5")
    parser.add_argument("--product-label", default="A5")
    parser.add_argument("--filename", default="")
    parser.add_argument("--quantity", type=int, default=0)
    parser.add_argument("--notes", default="")
    parser.add_argument("--detect-size", action="store_true")
    parser.add_argument("--progress-file", default="")
    parser.add_argument("--result", default="")
    args = parser.parse_args()
    if args.progress_file:
        os.environ["JOB_PROGRESS_FILE"] = args.progress_file
        _mark("fitting", "Fitting the picture to the product.")
    if args.product_id and args.product_id != "auto":
        chosen = _product_by_id(args.product_id)
        if chosen:
            args.trim_w = float(chosen["widthMm"])
            args.trim_h = float(chosen["heightMm"])
            args.product_label = chosen["label"]
    result = make_print_ready(
        args.input,
        args.output_dir,
        args.trim_w,
        args.trim_h,
        args.product_id,
        args.product_label,
        filename=args.filename,
        quantity=args.quantity or None,
        notes=args.notes or "",
        detect_size=bool(args.detect_size or args.product_id == "auto"),
    )
    text = json.dumps(result)
    if args.result:
        with open(args.result, "w", encoding="utf-8") as handle:
            handle.write(text)
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
