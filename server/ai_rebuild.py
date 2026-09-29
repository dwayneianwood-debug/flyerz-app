#!/usr/bin/env python3
"""Rebuild likely AI-generated raster artwork into press-ready type.

The picture stays a picture. Words found by OCR are removed, the background is
enlarged to 300 DPI at trim plus the Flyerz 5mm bleed, and the words are put
back as real vector text in a bundled open-licence font (Liberation Sans).

Replicate is used for inpainting and upscaling only when the account accepts
the call. A missing token, HTTP 402, or any other failure uses the local
OpenCV inpaint and Lanczos path. This module never raises out of assess() or
rebuild(); the print job continues either way.

The existing Press-Ready Engine is not modified. The PDF written here is trim
plus 5mm bleed with a trim box, so Automatic bleed can keep that ring.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image

BLEED_MM = 5.0
TARGET_DPI = 300
MAX_LONG_EDGE = 4500
MM_TO_PT = 72.0 / 25.4

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_REGULAR = os.path.join(FONT_DIR, "LiberationSans-Regular.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "LiberationSans-Bold.ttf")

EXTRA_META = (
    "gemini",
    "google imagen",
    "imagen",
    "midjourney",
    "chatgpt",
    "openai",
    "c2pa",
    "jumbf",
    "content credentials",
)

OcrFn = Callable[[np.ndarray], list]
InpaintFn = Callable[[np.ndarray, np.ndarray], np.ndarray]
UpscaleFn = Callable[[np.ndarray, int, int], np.ndarray]
AccountFn = Callable[[], str]

_OCR: Optional[OcrFn] = None
_INPAINT: Optional[InpaintFn] = None
_UPSCALE: Optional[UpscaleFn] = None
_ACCOUNT: Optional[AccountFn] = None
_RAPID = None


def set_providers(
    ocr: Optional[OcrFn] = None,
    inpaint: Optional[InpaintFn] = None,
    upscale: Optional[UpscaleFn] = None,
    account: Optional[AccountFn] = None,
) -> None:
    """Test hooks. Pass None to restore the real reader, local tools, and account check."""
    global _OCR, _INPAINT, _UPSCALE, _ACCOUNT
    _OCR = ocr
    _INPAINT = inpaint
    _UPSCALE = upscale
    _ACCOUNT = account


def reset_providers() -> None:
    set_providers(None, None, None, None)


def replicate_credit_status() -> str:
    """ok, none, 402, or error. Never raises."""
    if _ACCOUNT is not None:
        try:
            status = str(_ACCOUNT() or "error")
        except Exception:
            return "error"
        if status in ("ok", "none", "402", "error"):
            return status
        return "error"
    token = (os.environ.get("REPLICATE_API_TOKEN") or "").strip()
    if not token:
        return "none"
    try:
        req = urllib.request.Request(
            "https://api.replicate.com/v1/account",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return "ok" if getattr(resp, "status", 200) == 200 else "error"
    except urllib.error.HTTPError as exc:
        if exc.code == 402:
            return "402"
        return "error"
    except Exception:
        return "error"


def _credit_note(status: str) -> str:
    if status == "402":
        return "Replicate returned 402 (no credit). Local fallback used."
    if status == "none":
        return "No Replicate token. Local fallback used."
    if status != "ok":
        return "Replicate was not reachable. Local fallback used."
    return ""


def _load_bgr(path: str) -> np.ndarray:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pymupdf as fitz

        doc = fitz.open(path)
        try:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
        finally:
            doc.close()
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Could not read artwork")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        white = np.full_like(bgr, 255.0)
        return (bgr * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return img[:, :, :3]


def _pdf_has_live_type(path: str) -> bool:
    if os.path.splitext(path)[1].lower() != ".pdf":
        return False
    try:
        import pymupdf as fitz

        doc = fitz.open(path)
    except Exception:
        return False
    try:
        if doc.page_count < 1:
            return False
        page = doc[0]
        text = (page.get_text("text") or "").strip()
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        return len(text) >= 2 or len(drawings) >= 8
    finally:
        doc.close()


def _meta_blob(path: str) -> str:
    parts: list[str] = []
    try:
        with Image.open(path) as im:
            for value in (im.info or {}).values():
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, bytes):
                    parts.append(value.decode("latin-1", errors="ignore"))
    except Exception:
        pass
    try:
        with open(path, "rb") as handle:
            parts.append(handle.read(400_000).decode("latin-1", errors="ignore"))
    except Exception:
        pass
    return "\n".join(parts).lower()


def _target_pixels(trim_w_mm: float, trim_h_mm: float, bleed_mm: float = BLEED_MM) -> tuple[int, int]:
    width = int(math.ceil((float(trim_w_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI))
    height = int(math.ceil((float(trim_h_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI))
    width = max(32, width)
    height = max(32, height)
    long_edge = max(width, height)
    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / float(long_edge)
        width = max(32, int(round(width * scale)))
        height = max(32, int(round(height * scale)))
    return width, height


def effective_dpi(px_w: int, px_h: int, trim_w_mm: float, trim_h_mm: float, bleed_mm: float = BLEED_MM) -> int:
    width_in = (float(trim_w_mm) + 2.0 * float(bleed_mm)) / 25.4
    height_in = (float(trim_h_mm) + 2.0 * float(bleed_mm)) / 25.4
    if width_in <= 0 or height_in <= 0 or px_w <= 0 or px_h <= 0:
        return 0
    return int(min(px_w / width_in, px_h / height_in))


def assess(path: str, trim_w_mm: float = 148, trim_h_mm: float = 210, bleed_mm: float = BLEED_MM) -> dict:
    """Flag likely AI raster artwork. A normal photo or a vector PDF is not flagged."""
    try:
        return _assess(path, trim_w_mm, trim_h_mm, bleed_mm)
    except Exception as exc:
        return {
            "success": True,
            "detected": False,
            "autoRebuild": False,
            "reasons": [],
            "message": f"AI artwork check skipped: {str(exc)[:180]}",
        }


def _assess(path: str, trim_w_mm: float, trim_h_mm: float, bleed_mm: float) -> dict:
    from ai_artwork import detect_ai_artwork

    live_type = _pdf_has_live_type(path)
    if live_type:
        return {
            "success": True,
            "detected": False,
            "autoRebuild": False,
            "rasterOnly": False,
            "reasons": ["This file already has vector or text layers, so AI Rebuild stays off."],
            "recommendation": "",
        }
    bgr = _load_bgr(path)
    src_h, src_w = bgr.shape[:2]
    try:
        base = detect_ai_artwork(path, bgr)
    except Exception:
        base = {
            "detected": False,
            "reasons": [],
            "src_w": src_w,
            "src_h": src_h,
        }
    blob = _meta_blob(path)
    extra = [phrase for phrase in EXTRA_META if phrase in blob and phrase not in " ".join(base.get("reasons") or []).lower()]
    reasons = list(base.get("reasons") or [])
    for phrase in extra:
        if phrase in ("c2pa", "jumbf", "content credentials"):
            line = "file has content-credentials metadata"
        else:
            line = f"file metadata mentions {phrase}"
        if line not in reasons:
            reasons.append(line)
    dpi_now = effective_dpi(src_w, src_h, trim_w_mm, trim_h_mm, bleed_mm)
    if dpi_now and dpi_now < TARGET_DPI:
        reasons.append(f"about {dpi_now} DPI at the print size including 5mm bleed")
    detected = (not live_type) and bool(
        base.get("detected")
        or extra
        or (base.get("src_w") and dpi_now < 200 and any("AI image size" in r for r in reasons))
    )
    if live_type:
        detected = False
        reasons = ["This file already has vector or text layers, so AI Rebuild stays off."]
    elif not detected:
        reasons = []
    return {
        "success": True,
        "detected": detected,
        "autoRebuild": detected,
        "rasterOnly": not live_type,
        "reasons": reasons,
        "src_w": int(base.get("src_w") or src_w),
        "src_h": int(base.get("src_h") or src_h),
        "effective_dpi": dpi_now,
        "recommendation": (
            "This looks like AI-generated artwork. Rebuild is on: words are retyped crisp and the picture is enlarged for the press."
            if detected
            else ""
        ),
    }


def _rapid_blocks(bgr: np.ndarray) -> list:
    global _RAPID
    from rapidocr_onnxruntime import RapidOCR

    if _RAPID is None:
        _RAPID = RapidOCR()
    result, _elapsed = _RAPID(bgr)
    if not result:
        return []
    height, width = bgr.shape[:2]
    blocks = []
    for index, item in enumerate(result):
        box, text, score = item[0], str(item[1] or "").strip(), float(item[2] if len(item) > 2 else 0)
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        bw = max(2.0, x1 - x0)
        bh = max(2.0, y1 - y0)
        blocks.append(_block(index, text, x0, y0, bw, bh, width, height, bgr, score))
    return blocks


def _block(index: int, text: str, x: float, y: float, w: float, h: float, width: int, height: int, bgr: np.ndarray, score: float = 1) -> dict:
    x = max(0.0, min(float(x), width - 1))
    y = max(0.0, min(float(y), height - 1))
    w = max(2.0, min(float(w), width - x))
    h = max(2.0, min(float(h), height - y))
    return {
        "id": f"t{index + 1}",
        "text": text,
        "bbox": [round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4)],
        "color_hex": _text_hex(bgr, int(x), int(y), int(w), int(h)),
        "bold": _looks_bold(bgr, int(x), int(y), int(w), int(h)),
        "score": round(score, 3),
    }


def _text_hex(bgr: np.ndarray, x: int, y: int, w: int, h: int) -> str:
    roi = bgr[y:y + h, x:x + w]
    if roi.size == 0:
        return "#111111"
    flat = roi.reshape(-1, 3).astype(np.float32)
    if flat.shape[0] > 800:
        flat = flat[:: max(1, flat.shape[0] // 800)]
    border = np.concatenate([
        roi[0, :, :],
        roi[-1, :, :],
        roi[:, 0, :],
        roi[:, -1, :],
    ]).astype(np.float32)
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(flat - bg, axis=1)
    ink = flat[dist > 28] if np.any(dist > 28) else flat
    colour = np.median(ink, axis=0)
    rgb = (int(colour[2]), int(colour[1]), int(colour[0]))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _looks_bold(bgr: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    roi = bgr[y:y + h, x:x + w]
    if roi.size == 0 or h < 8:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if binary.mean() < 8:
        return False
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    stroke = float(dist.max()) * 2.0
    return stroke >= max(3.0, h * 0.16)


def _gemini_blocks(bgr: np.ndarray) -> tuple[list, str]:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return [], ""
    try:
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return [], "Gemini image encode failed"
        import base64

        payload = {
            "contents": [{
                "parts": [
                    {"text": (
                        "OCR this artwork. Return JSON {\"blocks\":[{\"text\",\"bbox\":[x,y,w,h] as fractions 0-1, "
                        "\"color_hex\",\"bold\"}]}. Transcribe the spelling you see. Do not invent words."
                    )},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.tobytes()).decode("ascii")}},
                ]
            }],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        height, width = bgr.shape[:2]
        blocks = []
        for index, item in enumerate(parsed.get("blocks") or []):
            raw = str(item.get("text") or "").strip()
            if not raw:
                continue
            box = item.get("bbox") or [0, 0, 0.2, 0.05]
            x, y, bw, bh = [float(v) for v in box[:4]]
            blocks.append(_block(index, raw, x * width, y * height, bw * width, bh * height, width, height, bgr))
            if item.get("color_hex"):
                blocks[-1]["color_hex"] = str(item["color_hex"])[:7]
            if "bold" in item:
                blocks[-1]["bold"] = bool(item["bold"])
        return blocks, ""
    except Exception as exc:
        return [], str(exc)[:180]


def read_text_blocks(bgr: np.ndarray) -> tuple[list, str, str]:
    """Return blocks, engine name, note. Engine is hook, local, gemini, or none."""
    if _OCR is not None:
        try:
            blocks = _OCR(bgr) or []
            return [_normalise_hook_block(b, i, bgr) for i, b in enumerate(blocks)], "hook", "OCR used the supplied reader."
        except Exception as exc:
            return [], "none", f"OCR reader failed: {str(exc)[:160]}"
    try:
        blocks = _rapid_blocks(bgr)
        if blocks:
            return blocks, "local", "OCR used RapidOCR on this computer."
    except Exception as exc:
        rapid_err = str(exc)[:160]
    else:
        rapid_err = ""
    blocks, gem_err = _gemini_blocks(bgr)
    if blocks:
        return blocks, "gemini", "OCR used Gemini because local RapidOCR found no text."
    note = "No text was read."
    if rapid_err:
        note = f"Local OCR was not available ({rapid_err})."
    if gem_err:
        note = note + f" Gemini: {gem_err}"
    return [], "none", note


def _normalise_hook_block(block: dict, index: int, bgr: np.ndarray) -> dict:
    height, width = bgr.shape[:2]
    bbox = block.get("bbox") or [0.1, 0.1, 0.4, 0.1]
    if max(bbox) > 1.5:
        x, y, bw, bh = [float(v) for v in bbox[:4]]
    else:
        x, y, bw, bh = float(bbox[0]) * width, float(bbox[1]) * height, float(bbox[2]) * width, float(bbox[3]) * height
    made = _block(index, str(block.get("text") or ""), x, y, bw, bh, width, height, bgr, float(block.get("score") or 1))
    if block.get("id"):
        made["id"] = str(block["id"])
    if block.get("color_hex"):
        made["color_hex"] = str(block["color_hex"])[:7]
    if "bold" in block:
        made["bold"] = bool(block["bold"])
    return made


def _mask_from_blocks(shape: tuple, blocks: list) -> np.ndarray:
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    for block in blocks:
        x, y, bw, bh = block["bbox"]
        x0 = int(round(x * width))
        y0 = int(round(y * height))
        x1 = int(round((x + bw) * width))
        y1 = int(round((y + bh) * height))
        pad = max(2, int(round(0.12 * (y1 - y0))))
        cv2.rectangle(mask, (x0 - pad, y0 - pad), (x1 + pad, y1 + pad), 255, -1)
    if mask.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _local_inpaint(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if not mask.any():
        return bgr
    return cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)


def _cover(bgr: np.ndarray, target_w: int, target_h: int, sharpen: bool) -> tuple[np.ndarray, float, int, int]:
    height, width = bgr.shape[:2]
    scale = max(target_w / float(width), target_h / float(height))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    if sharpen:
        from ai_upscale import light_sharpen_bgr
        resized = light_sharpen_bgr(resized)
    x0 = max(0, (new_w - target_w) // 2)
    y0 = max(0, (new_h - target_h) // 2)
    crop = resized[y0:y0 + target_h, x0:x0 + target_w]
    if crop.shape[0] != target_h or crop.shape[1] != target_w:
        crop = cv2.resize(resized, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
        x0, y0, scale = 0, 0, target_w / float(width)
    return crop, scale, x0, y0


def _hex_rgb(value: str) -> tuple[float, float, float]:
    raw = (value or "#111111").lstrip("#")
    if len(raw) != 6:
        raw = "111111"
    try:
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except Exception:
        r, g, b = 17, 17, 17
    return r / 255.0, g / 255.0, b / 255.0


def _apply_edits(blocks: list, edits: list) -> list:
    if not edits:
        return blocks
    by_id = {str(item.get("id")): item for item in edits if isinstance(item, dict)}
    merged = []
    for block in blocks:
        edit = by_id.get(str(block.get("id")))
        if edit and "text" in edit:
            block = dict(block)
            block["text"] = str(edit.get("text") or "")
        if str(block.get("text") or "").strip():
            merged.append(block)
    known = {str(block.get("id")) for block in blocks}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        if str(edit.get("id")) in known:
            continue
        text = str(edit.get("text") or "").strip()
        if text:
            merged.append(edit)
    return merged


def _write_preview(path: str, bgr: np.ndarray, max_px: int = 900) -> None:
    height, width = bgr.shape[:2]
    long_edge = max(height, width, 1)
    if long_edge > max_px:
        scale = max_px / float(long_edge)
        bgr = cv2.resize(
            bgr,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(path, format="PNG", dpi=(TARGET_DPI, TARGET_DPI))


def _typeset(background: np.ndarray, blocks: list, out_pdf: str, trim_w_mm: float, trim_h_mm: float, src_shape: tuple, scale: float, off_x: int, off_y: int) -> None:
    import pymupdf as fitz

    canvas_h, canvas_w = background.shape[:2]
    src_h, src_w = src_shape[:2]
    media_w = (float(trim_w_mm) + 2.0 * BLEED_MM) * MM_TO_PT
    media_h = (float(trim_h_mm) + 2.0 * BLEED_MM) * MM_TO_PT
    doc = fitz.open()
    page = doc.new_page(width=media_w, height=media_h)
    ok, buf = cv2.imencode(".png", background)
    if not ok:
        raise RuntimeError("Could not encode the rebuilt background")
    page.insert_image(page.rect, stream=buf.tobytes())
    regular = FONT_REGULAR if os.path.exists(FONT_REGULAR) else None
    bold = FONT_BOLD if os.path.exists(FONT_BOLD) else regular
    if regular:
        page.insert_font(fontname="FlyerzSans", fontfile=regular)
    if bold:
        page.insert_font(fontname="FlyerzSansBold", fontfile=bold)
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        x, y, bw, bh = [float(v) for v in block["bbox"][:4]]
        sx, sy = x * src_w, y * src_h
        sw, sh = bw * src_w, bh * src_h
        cx = sx * scale - off_x
        cy = sy * scale - off_y
        cw = sw * scale
        ch = sh * scale
        # PDF origin is the bottom left.
        x0 = cx / canvas_w * media_w
        y1 = media_h - (cy / canvas_h * media_h)
        x1 = (cx + cw) / canvas_w * media_w
        y0 = media_h - ((cy + ch) / canvas_h * media_h)
        rect = fitz.Rect(x0, min(y0, y1), max(x0, x1), max(y0, y1))
        if rect.width < 2 or rect.height < 2:
            continue
        fontname = "FlyerzSansBold" if block.get("bold") and bold else "FlyerzSans" if regular else "helv"
        size = max(6.0, rect.height * 0.78)
        colour = _hex_rgb(str(block.get("color_hex") or "#111111"))
        for _ in range(6):
            spare = page.insert_textbox(rect, text, fontname=fontname, fontsize=size, color=colour, align=1)
            if spare >= 0:
                break
            size *= 0.85
    bleed_pt = BLEED_MM * MM_TO_PT
    trim = fitz.Rect(bleed_pt, bleed_pt, media_w - bleed_pt, media_h - bleed_pt)
    page.set_mediabox(page.rect)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    os.makedirs(os.path.dirname(out_pdf) or ".", exist_ok=True)
    doc.save(out_pdf, deflate=True, garbage=4)
    doc.close()


def _render_page(pdf_path: str, max_px: int = 900) -> np.ndarray:
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        long_pt = max(page.rect.width, page.rect.height, 1)
        scale = min(2.0, (max_px / long_pt))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    finally:
        doc.close()


def rebuild(path: str, options: Optional[dict] = None) -> dict:
    """Run OCR, inpaint, upscale, and vector typeset. Always returns a dict."""
    try:
        return _rebuild(path, options or {})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return {
            "success": False,
            "detected": False,
            "message": f"AI Rebuild could not finish, so the original artwork will be used. {str(exc)[:180]}",
            "steps": [{"name": "AI Rebuild", "engine": "local", "ok": False, "note": str(exc)[:180]}],
            "blocks": [],
            "ocrText": "",
        }


def _remove_text(bgr: np.ndarray, mask: np.ndarray, credit: str) -> tuple[np.ndarray, str, str]:
    note = _credit_note(credit)
    if credit == "ok" and mask.any():
        if _INPAINT is not None:
            try:
                filled = _INPAINT(bgr, mask)
                if filled is not None and getattr(filled, "shape", None) == bgr.shape:
                    return filled, "replicate", "Text was removed with Replicate inpainting."
            except Exception as exc:
                note = f"Replicate inpaint failed ({str(exc)[:120]}). Local fallback used."
        else:
            remote = _replicate_inpaint(bgr, mask)
            if remote is not None:
                return remote, "replicate", "Text was removed with Replicate inpainting."
            note = note or "Replicate inpaint was not available. Local fallback used."
    filled = _local_inpaint(bgr, mask)
    engine = "local"
    detail = "Text was removed with OpenCV inpaint on this computer."
    if note:
        detail = detail + " " + note
    return filled, engine, detail


def _replicate_inpaint(bgr: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
    try:
        from ai_enhancements import _call_replicate, _to_data_uri

        folder = tempfile.mkdtemp(prefix="ai-rebuild-inpaint-")
        image_path = os.path.join(folder, "image.png")
        mask_path = os.path.join(folder, "mask.png")
        cv2.imwrite(image_path, bgr)
        cv2.imwrite(mask_path, mask)
        out, err = _call_replicate(
            "ai_rebuild_inpaint",
            "stability-ai",
            "stable-diffusion-inpainting",
            {
                "image": _to_data_uri(image_path),
                "mask": _to_data_uri(mask_path),
                "prompt": "clean background, no text, no letters",
            },
        )
        if err or not out or not os.path.exists(str(out)):
            return None
        loaded = cv2.imread(str(out), cv2.IMREAD_COLOR)
        if loaded is None or loaded.shape[:2] != bgr.shape[:2]:
            return None
        return loaded
    except Exception:
        return None


def _enlarge(clean: np.ndarray, target_w: int, target_h: int, credit: str) -> tuple[np.ndarray, str, str, float, int, int]:
    note = _credit_note(credit)
    if credit == "ok":
        if _UPSCALE is not None:
            try:
                bigger = _UPSCALE(clean, target_w, target_h)
                if bigger is not None:
                    fitted, scale, x0, y0 = _cover(bigger, target_w, target_h, sharpen=False)
                    return fitted, "replicate", "Background enlarged with the Replicate upscaler.", scale, x0, y0
            except Exception as exc:
                note = f"Replicate upscale failed ({str(exc)[:120]}). Local fallback used."
        else:
            remote = _replicate_upscale(clean, target_w, target_h)
            if remote is not None:
                fitted, scale, x0, y0 = _cover(remote, target_w, target_h, sharpen=False)
                return fitted, "replicate", "Background enlarged with the Replicate upscaler.", scale, x0, y0
            note = note or "Replicate upscale was not available. Local fallback used."
    fitted, scale, x0, y0 = _cover(clean, target_w, target_h, sharpen=True)
    detail = "Background enlarged with Lanczos and a light sharpen on this computer."
    if note:
        detail = detail + " " + note
    return fitted, "local", detail, scale, x0, y0


def _replicate_upscale(bgr: np.ndarray, target_w: int, target_h: int) -> Optional[np.ndarray]:
    try:
        from ai_enhancements import _call_replicate, _to_data_uri
        from ai_upscale import UPSCALE_MODEL_NAME, UPSCALE_MODEL_OWNER, UPSCALE_MODEL_VERSION

        folder = tempfile.mkdtemp(prefix="ai-rebuild-upscale-")
        image_path = os.path.join(folder, "image.png")
        cv2.imwrite(image_path, bgr)
        scale = 2 if max(target_w / max(bgr.shape[1], 1), target_h / max(bgr.shape[0], 1)) <= 2.2 else 4
        out, err = _call_replicate(
            "ai_rebuild_upscale",
            UPSCALE_MODEL_OWNER,
            UPSCALE_MODEL_NAME,
            {"image": _to_data_uri(image_path), "scale": scale, "face_enhance": False},
            version=UPSCALE_MODEL_VERSION,
        )
        if err or not out or not os.path.exists(str(out)):
            return None
        loaded = cv2.imread(str(out), cv2.IMREAD_COLOR)
        return loaded if loaded is not None else None
    except Exception:
        return None


def _rebuild(path: str, options: dict) -> dict:
    trim_w = float(options.get("trim_w_mm") or 148)
    trim_h = float(options.get("trim_h_mm") or 210)
    verdict = assess(path, trim_w, trim_h, BLEED_MM)
    if options.get("force") is not True and not verdict.get("detected") and not options.get("blocks"):
        return {
            "success": True,
            "detected": False,
            "skipped": False,
            "message": "This artwork was not rebuilt because it does not look like an AI raster.",
            "steps": [],
            "blocks": [],
            "ocrText": "",
            "reasons": verdict.get("reasons") or [],
        }

    source = _load_bgr(path)
    credit = replicate_credit_status()
    steps: list[dict] = []
    edits = options.get("blocks") if isinstance(options.get("blocks"), list) else None
    clean_path = options.get("clean_path") or ""

    if edits and clean_path and os.path.exists(clean_path):
        clean = _load_bgr(clean_path)
        # Edits reuse the already cleaned full-page background. It is already at print size.
        blocks = []
        for index, item in enumerate(edits):
            if not isinstance(item, dict):
                continue
            made = dict(item)
            made.setdefault("id", f"t{index + 1}")
            blocks.append(made)
        steps.append({"name": "OCR", "engine": "review", "ok": True, "note": "Staff spelling was used. OCR was not run again."})
        steps.append({"name": "Remove text", "engine": "review", "ok": True, "note": "The cleaned background from the last rebuild was kept."})
        background = clean
        if background.shape[1] != _target_pixels(trim_w, trim_h)[0] or background.shape[0] != _target_pixels(trim_w, trim_h)[1]:
            background, _scale, _x0, _y0 = _cover(clean, *_target_pixels(trim_w, trim_h), sharpen=False)
        scale = background.shape[1] / float(source.shape[1])
        off_x = 0
        off_y = 0
        # Blocks are fractions of the original. Cover-fit them onto the print canvas.
        fitted, scale, off_x, off_y = _cover(source, background.shape[1], background.shape[0], sharpen=False)
        del fitted
        up_engine = "review"
        steps.append({"name": "Upscale", "engine": "review", "ok": True, "note": "Print-size background kept from the last rebuild."})
    else:
        blocks, ocr_engine, ocr_note = read_text_blocks(source)
        blocks = _apply_edits(blocks, edits or [])
        steps.append({"name": "OCR", "engine": "local" if ocr_engine in ("local", "hook") else ocr_engine, "ok": True, "note": ocr_note})
        mask = _mask_from_blocks(source.shape, blocks)
        clean, inpaint_engine, inpaint_note = _remove_text(source, mask, credit)
        steps.append({"name": "Remove text", "engine": inpaint_engine, "ok": True, "note": inpaint_note})
        target_w, target_h = _target_pixels(trim_w, trim_h)
        background, up_engine, up_note, scale, off_x, off_y = _enlarge(clean, target_w, target_h, credit)
        steps.append({"name": "Upscale", "engine": up_engine, "ok": True, "note": up_note})
        if options.get("clean_path"):
            _write_preview(options["clean_path"], background, max_px=max(background.shape[:2]))

    out_pdf = options.get("output_pdf") or os.path.join(tempfile.gettempdir(), "ai-rebuild.pdf")
    _typeset(background, blocks, out_pdf, trim_w, trim_h, source.shape, scale, off_x, off_y)
    font_note = "Words were typeset with bundled Liberation Sans." if os.path.exists(FONT_REGULAR) else "Words were typeset with a built-in font."
    steps.append({"name": "Retypeset", "engine": "local", "ok": True, "note": font_note})

    before_path = options.get("before_path") or ""
    after_path = options.get("after_path") or ""
    if before_path:
        _write_preview(before_path, source)
    if after_path and os.path.exists(out_pdf):
        _write_preview(after_path, _render_page(out_pdf))

    ocr_text = " | ".join(str(block.get("text") or "").strip() for block in blocks if str(block.get("text") or "").strip())
    engines = ", ".join(f"{step['name']} {step['engine']}" for step in steps)
    return {
        "success": True,
        "detected": True,
        "autoRebuild": True,
        "skipped": False,
        "pdfPath": out_pdf,
        "blocks": blocks,
        "ocrText": ocr_text,
        "steps": steps,
        "reasons": verdict.get("reasons") or [],
        "recommendation": verdict.get("recommendation") or "",
        "effective_dpi": effective_dpi(background.shape[1], background.shape[0], trim_w, trim_h, BLEED_MM),
        "src_w": int(source.shape[1]),
        "src_h": int(source.shape[0]),
        "out_w": int(background.shape[1]),
        "out_h": int(background.shape[0]),
        "bleed_mm": BLEED_MM,
        "replicate": credit,
        "message": f"AI Rebuild finished. {engines}.",
        "note": f"AI Rebuild. {engines}. Text: {ocr_text or '(none)'}.",
    }


def main() -> None:
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "message": "Usage: ai_rebuild.py <assess|rebuild> <image> [options_json]"}))
        return
    action = sys.argv[1]
    path = sys.argv[2]
    options = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    if action == "assess":
        result = assess(path, float(options.get("trim_w_mm", 148)), float(options.get("trim_h_mm", 210)))
    elif action == "rebuild":
        result = rebuild(path, options)
    else:
        result = {"success": False, "message": f"Unknown action: {action}"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
