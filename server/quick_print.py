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
    if facts.get("vectorAmber"):
        note = str(facts.get("vectorAmberReason") or "").strip()
        if note and note not in reasons:
            reasons.append(note)
    if facts.get("enginePassed") is False:
        note = str(facts.get("engineReason") or "").strip() or "The press check flagged this file."
        if note not in reasons:
            reasons.append(note)
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


def _match_product(width_mm: float, height_mm: float, tolerance: float = 2.5):
    best = None
    rotated = False
    for product in _products():
        if abs(width_mm - product["widthMm"]) <= tolerance and abs(height_mm - product["heightMm"]) <= tolerance:
            return product, False
        if abs(width_mm - product["heightMm"]) <= tolerance and abs(height_mm - product["widthMm"]) <= tolerance:
            best = product
            rotated = True
    return best, rotated


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


def _progressive_blur(strip, smax: float, reach: float):
    """Blur grows with distance from row 0. Far rows lose the repeated rim."""
    import cv2
    import numpy as np

    count = int(strip.shape[0])
    base = np.ascontiguousarray(strip.astype(np.float32))
    if count == 0 or smax <= 0.05:
        return base
    sigs = [0.0, 1.5, 3.0, 6.0, 12.0, 22.0, 36.0, 52.0]
    sigs = [value for value in sigs if value < smax - 0.05]
    sigs.append(float(smax))
    levels = [base if sigma <= 0.05 else cv2.GaussianBlur(base, (0, 0), sigma) for sigma in sigs]
    if count == 1:
        return base
    distance = np.arange(count, dtype=np.float32)
    span = max(float(reach), 1.0)
    amount = np.clip(distance / span, 0.0, 1.0) ** 1.05
    target = amount * float(smax)
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
    # The pixel against the picture is the real edge, not a blurred neighbour.
    out[0] = base[0]
    return out


def _mirror_extend(band, pad: int, forward: bool):
    """Reflect the rim and blur it more the further it sits from the picture.

    No column is smeared on its own, and the picture pixels are not copied as a
    repeated border. `band` is the rim. When forward is false, the first row is
    the edge and the pad is built upward.
    """
    import numpy as np

    width = int(band.shape[1])
    if pad <= 0:
        return np.zeros((0, width, 3), np.uint8)
    work = np.ascontiguousarray(band.astype(np.float32))
    if not forward:
        work = work[::-1]
    reflected = _reflect_from_edge(work, pad)
    reach = float(min(max(pad - 1, 1), 110))
    smax = float(min(56.0, max(7.0, pad * 0.14)))
    blurred = _progressive_blur(reflected, smax, reach)
    out = np.clip(np.rint(blurred), 0, 255).astype(np.uint8)
    if not forward:
        out = out[::-1]
    return out


def _extend_vertical(img, top: int, bottom: int):
    import numpy as np

    parts = []
    height = img.shape[0]
    rim = min(MIRROR_RIM_PX, height)
    core = img if img.dtype == np.uint8 else np.clip(img, 0, 255).astype(np.uint8)
    if top:
        parts.append(_mirror_extend(core[:rim], top, forward=False))
    parts.append(core)
    if bottom:
        parts.append(_mirror_extend(core[-rim:], bottom, forward=True))
    if len(parts) == 1:
        return parts[0]
    return np.concatenate(parts, axis=0)


def _extend_edges(img, top: int, bottom: int, left: int, right: int):
    """Grow a picture without copying one row of pixels down the page."""
    import numpy as np

    out = img
    if top or bottom:
        out = _extend_vertical(out, top, bottom)
    if left or right:
        turned = np.ascontiguousarray(np.transpose(out, (1, 0, 2)))
        turned = _extend_vertical(turned, left, right)
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


def _soften_art_rim(canvas, top: int, left: int, art_h: int, art_w: int):
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
    if top_depth >= 4:
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


def _restitch(canvas, top: int, left: int, art_h: int, art_w: int, reach: int = 8):
    """Pull the pad's seam row onto the feathered picture so the join is not a line."""
    import numpy as np

    out = canvas
    y0, x0 = int(top), int(left)
    y1, x1 = y0 + int(art_h), x0 + int(art_w)
    if y0 <= 0 or x1 <= x0:
        return out
    edge = out[y0, x0:x1].astype(np.float32)
    for dist in range(1, reach + 1):
        y = y0 - dist
        if y < 0:
            break
        weight = dist / float(reach + 1)
        row = out[y, x0:x1].astype(np.float32)
        out[y, x0:x1] = np.clip(edge * (1.0 - weight) + row * weight, 0, 255).astype(np.uint8)
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
    vertical = _extend_edges(img, top, bottom, 0, 0)
    fitted = _extend_edges(vertical, 0, 0, side_left, side_right)
    if fitted.shape[0] != canvas_h or fitted.shape[1] != canvas_w:
        return None
    fitted = _soften_art_rim(fitted, top, side_left, height, width)
    fitted = _restitch(fitted, top, side_left, height, width)
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
        fitted = _extend_edges(img, top, pad - top, 0, 0)
    else:
        new_w = max(width, int(round(height * target)))
        pad = new_w - width
        left = pad // 2
        fitted = _extend_edges(img, 0, 0, left, pad - left)
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


def _write_proof(press_path: str, png_path: str, pdf_path: str, caption: str) -> None:
    import pymupdf as fitz

    src = fitz.open(press_path)
    try:
        page = src[0]
        proof = fitz.open()
        footer = 32
        out = proof.new_page(width=page.rect.width, height=page.rect.height + footer)
        out.show_pdf_page(page.rect, src, 0)
        trim = page.trimbox
        out.draw_rect(trim, color=(0.86, 0.05, 0.45), width=1.4)
        out.insert_text(
            (14, page.rect.height + 20),
            caption[:180],
            fontsize=8,
            fontname="helv",
            color=(0.15, 0.15, 0.15),
        )
        proof.save(pdf_path, deflate=True, garbage=4)
        pix = out.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
        pix.save(png_path)
        proof.close()
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
                        found, _rotated = _match_product(*measured)
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
            matches = False
            if measured:
                matches = abs(measured[0] - trim_w) <= 2 and abs(measured[1] - trim_h) <= 2
                decisions.append(
                    f"The PDF trim is {measured[0]:.1f} × {measured[1]:.1f} mm."
                )
            if matches:
                decisions.append("The PDF already fits this product, so the page was kept and sent to the press engine.")
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
            # Extend first. The trace reads this canvas, so the paths land on the same pixels.
            raster, aspect_extended, aspect_delta = _extend_to_product(raster, trim_w, trim_h, work_path, decisions)
            vector_source = raster
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
        if isinstance(engine, dict) and engine.get("existingBleed"):
            existing_kept = True
            decisions.append("This file already had 5 mm bleed. That bleed was kept and was not added again.")
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
    info = decide_light(facts)
    result = _blank(info, decisions, product, quantity, notes)
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
            _write_proof(
                press_path,
                proof_png,
                proof_pdf,
                "Proof. The pink line is the trim. This is not the press file.",
            )
            result["proofPng"] = proof_png
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
