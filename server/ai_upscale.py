#!/usr/bin/env python3
"""Optional AI upscale that runs on the artwork before bleed.

Uses the existing Replicate helper in ai_enhancements.py. The pinned model is
nightmareai/real-esrgan version f121d640 (latest public Real-ESRGAN on Replicate,
face enhancement off so logos and type stay faithful).

When REPLICATE_API_TOKEN is missing, a local Lanczos resize plus a light unsharp
mask is used and labelled as a basic enhancement. API failures keep the original
and never raise, so the print job can continue.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

import cv2
import numpy as np

# Latest public version of nightmareai/real-esrgan (scale up to 10, face_enhance default false).
# https://replicate.com/nightmareai/real-esrgan/versions/f121d640bd286e1fdc67f9799164c1d5be36ff74576ee11c803ae5b665dd46aa
UPSCALE_MODEL_OWNER = "nightmareai"
UPSCALE_MODEL_NAME = "real-esrgan"
UPSCALE_MODEL_VERSION = "f121d640bd286e1fdc67f9799164c1d5be36ff74576ee11c803ae5b665dd46aa"
UPSCALE_MODEL_ID = f"{UPSCALE_MODEL_OWNER}/{UPSCALE_MODEL_NAME}"

TARGET_DPI = 300
MAX_LONG_EDGE = 4000
DEFAULT_BLEED_MM = 5.0
SOFT_LAPLACIAN_MAX = 80.0

AI_BUSY_MESSAGE = (
    "The AI upscaler is busy right now, so we'll keep your original artwork and continue."
)
AI_FAILED_MESSAGE = (
    "The AI upscaler isn't available right now, so we'll keep your original artwork and continue."
)
BASIC_MESSAGE = (
    "AI upscaling isn't configured on this computer, so a basic enhancement was applied "
    "(high-quality resize and sharpen). This is not an AI result. You can keep your original instead."
)

Provider = Callable[[str, int, int, int], tuple]
_PROVIDER: Optional[Provider] = None


def set_upscale_provider(provider: Optional[Provider]) -> None:
    """Test hook. provider(path, model_scale, target_w, target_h) -> (out_path, error)."""
    global _PROVIDER
    _PROVIDER = provider


def upscale_model_identity() -> dict:
    return {
        "owner": UPSCALE_MODEL_OWNER,
        "name": UPSCALE_MODEL_NAME,
        "version": UPSCALE_MODEL_VERSION,
        "id": UPSCALE_MODEL_ID,
    }


def effective_print_dpi(px_w: int, px_h: int, trim_w_mm: float, trim_h_mm: float, bleed_mm: float = DEFAULT_BLEED_MM) -> int:
    """DPI of these pixels when printed at trim size plus bleed on every side."""
    width_in = (float(trim_w_mm) + 2.0 * float(bleed_mm)) / 25.4
    height_in = (float(trim_h_mm) + 2.0 * float(bleed_mm)) / 25.4
    if width_in <= 0 or height_in <= 0 or px_w <= 0 or px_h <= 0:
        return 0
    return int(min(px_w / width_in, px_h / height_in))


def choose_upscale_plan(src_w: int, src_h: int, trim_w_mm: float, trim_h_mm: float, bleed_mm: float = DEFAULT_BLEED_MM) -> dict:
    """Pick a Real-ESRGAN scale (1, 2, or 4) that aims for 300 DPI, capped at 4000px."""
    need_w = (float(trim_w_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI
    need_h = (float(trim_h_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI
    factor = max(need_w / max(src_w, 1), need_h / max(src_h, 1))
    long_edge = max(src_w, src_h)
    if factor <= 1:
        model_scale = 2 if long_edge * 2 <= MAX_LONG_EDGE else 1
    elif factor <= 2:
        model_scale = 2
    else:
        model_scale = 4
    while model_scale > 1 and long_edge * model_scale > MAX_LONG_EDGE:
        model_scale //= 2

    cover = max(factor, 1.0)
    out_w = int(round(src_w * cover))
    out_h = int(round(src_h * cover))
    long_out = max(out_w, out_h, 1)
    if long_out > MAX_LONG_EDGE:
        shrink = MAX_LONG_EDGE / float(long_out)
        out_w = max(1, int(round(out_w * shrink)))
        out_h = max(1, int(round(out_h * shrink)))
    if factor <= 1 and max(src_w, src_h) <= MAX_LONG_EDGE:
        out_w, out_h = src_w, src_h

    return {
        "model_scale": int(model_scale),
        "target_w": int(out_w),
        "target_h": int(out_h),
        "needed_factor": round(float(factor), 3),
        "effective_dpi": effective_print_dpi(src_w, src_h, trim_w_mm, trim_h_mm, bleed_mm),
    }


def looks_soft(bgr: np.ndarray) -> bool:
    if bgr is None or bgr.size == 0:
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
    score = float(cv2.Laplacian(small, cv2.CV_64F).var())
    return score < SOFT_LAPLACIAN_MAX


def light_sharpen_bgr(bgr: np.ndarray) -> np.ndarray:
    """Gentle unsharp mask for type and logo edges. Radius stays under a pixel."""
    blurred = cv2.GaussianBlur(bgr, (0, 0), 0.7)
    sharp = cv2.addWeighted(bgr, 1.28, blurred, -0.28, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)


def _cap_long_edge(bgr: np.ndarray) -> np.ndarray:
    height, width = bgr.shape[:2]
    long_edge = max(height, width)
    if long_edge <= MAX_LONG_EDGE:
        return bgr
    scale = MAX_LONG_EDGE / float(long_edge)
    return cv2.resize(
        bgr,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_LANCZOS4,
    )


def basic_lanczos_upscale(bgr: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    target_w = max(1, int(target_w))
    target_h = max(1, int(target_h))
    resized = cv2.resize(bgr, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
    return light_sharpen_bgr(_cap_long_edge(resized))


def _flatten_bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        white = np.full_like(bgr, 255.0)
        return (bgr * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return img[:, :, :3]


def _write_png(path: str, bgr: np.ndarray, dpi: int = TARGET_DPI) -> None:
    from PIL import Image

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    image.save(path, format="PNG", dpi=(dpi, dpi))
    image.close()


def _comparison_views(source: np.ndarray, enhanced: np.ndarray, max_px: int = 720) -> tuple:
    """Center crops shown at the same size. The original crop stays at its native pixels so it looks softer."""
    src_h, src_w = source.shape[:2]
    enh_h, enh_w = enhanced.shape[:2]
    side = max(48, int(min(src_w, src_h) * 0.42))
    sx = max(0, (src_w - side) // 2)
    sy = max(0, (src_h - side) // 2)
    before = source[sy:sy + side, sx:sx + side]
    scale_x = enh_w / float(max(src_w, 1))
    scale_y = enh_h / float(max(src_h, 1))
    ex = min(enh_w - 1, max(0, int(round(sx * scale_x))))
    ey = min(enh_h - 1, max(0, int(round(sy * scale_y))))
    crop_w = max(1, int(round(side * scale_x)))
    crop_h = max(1, int(round(side * scale_y)))
    after = enhanced[ey:min(enh_h, ey + crop_h), ex:min(enh_w, ex + crop_w)]
    if after.size == 0:
        after = enhanced
    return before, _preview_bgr(after, max_px)


def _preview_bgr(bgr: np.ndarray, max_px: int = 900) -> np.ndarray:
    height, width = bgr.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_px:
        return bgr
    scale = max_px / float(long_edge)
    return cv2.resize(
        bgr,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def classify_artwork(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    raster_ext = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
    if ext in raster_ext:
        return {"eligible": True, "kind": "raster", "reason": ""}
    if ext != ".pdf":
        return {
            "eligible": False,
            "kind": "unsupported",
            "reason": "AI Enhance works on JPG, PNG, and raster PDF pages.",
        }
    try:
        import fitz
    except Exception:
        return {
            "eligible": False,
            "kind": "vector_pdf",
            "reason": "This PDF was left unchanged. AI Enhance is for raster artwork.",
        }
    try:
        doc = fitz.open(path)
    except Exception:
        return {
            "eligible": False,
            "kind": "vector_pdf",
            "reason": "This PDF was left unchanged. AI Enhance is for raster artwork.",
        }
    try:
        if doc.page_count < 1:
            return {"eligible": False, "kind": "vector_pdf", "reason": "This PDF has no pages to enhance."}
        page = doc[0]
        text = (page.get_text("text") or "").strip()
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        images = page.get_images(full=True) or []
        vectorish = len(text) >= 8 or len(drawings) >= 5
        if images and not vectorish:
            return {"eligible": True, "kind": "raster_pdf", "reason": ""}
        if images and len(text) < 40 and len(drawings) < 15:
            return {"eligible": True, "kind": "raster_pdf", "reason": ""}
        return {
            "eligible": False,
            "kind": "vector_pdf",
            "reason": "This is a vector PDF, so it was left unchanged. AI Enhance is for blurry photos and raster artwork.",
        }
    finally:
        doc.close()


def load_artwork_bgr(path: str) -> tuple:
    """Return (bgr, kind). Raises ValueError when the file is not a raster candidate."""
    kind = classify_artwork(path)
    if not kind["eligible"]:
        raise ValueError(kind["reason"] or "Artwork is not a raster candidate")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _load_raster_pdf_bgr(path), kind
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Could not read artwork image")
    return _flatten_bgr(img), kind


def _load_raster_pdf_bgr(path: str) -> np.ndarray:
    import fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        images = page.get_images(full=True) or []
        if images:
            pix = fitz.Pixmap(doc, images[0][0])
            if pix.n >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            elif pix.colorspace and pix.colorspace.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4:
                return _flatten_bgr(arr)
            if pix.n == 1:
                return cv2.cvtColor(arr[:, :, 0], cv2.COLOR_GRAY2BGR)
            return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
        scale = 150.0 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    finally:
        doc.close()


def _health_note(provider: str, scale: int) -> str:
    if provider == "basic":
        return (
            "Basic enhancement was applied before bleed (high-quality Lanczos resize and unsharp mask). "
            "The AI upscaler was not available, so this is not an AI result. "
            "Bleed was added afterwards and was not enhanced."
        )
    return (
        f"AI enhancement was applied before bleed using {UPSCALE_MODEL_ID} "
        f"version {UPSCALE_MODEL_VERSION} at scale {int(scale)}. "
        "Bleed, including any solid colour border, was added afterwards and was not AI-processed."
    )


def _original_result(message: str, src_w: int, src_h: int, plan: dict, kind: str) -> dict:
    return {
        "success": True,
        "used_original": True,
        "provider": "original",
        "basic": False,
        "stub": False,
        "enhancement": "ai_upscale",
        "model": UPSCALE_MODEL_ID,
        "version": UPSCALE_MODEL_VERSION,
        "scale": 1,
        "message": message,
        "note": "",
        "enhanced_path": "",
        "src_w": src_w,
        "src_h": src_h,
        "out_w": src_w,
        "out_h": src_h,
        "effective_dpi": plan.get("effective_dpi", 0),
        "enhanced_dpi": plan.get("effective_dpi", 0),
        "kind": kind,
        "original_preserved": True,
    }


def _finish_bgr(bgr: np.ndarray, plan: dict, sharpen: bool = True) -> np.ndarray:
    height, width = bgr.shape[:2]
    target_w = int(plan["target_w"])
    target_h = int(plan["target_h"])
    if width != target_w or height != target_h:
        # Model scale is 2 or 4. Land on the print target when it still fits the cap.
        if max(target_w, target_h) <= MAX_LONG_EDGE and (target_w > width or target_h > height):
            bgr = cv2.resize(bgr, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
        elif max(width, height) > MAX_LONG_EDGE:
            bgr = _cap_long_edge(bgr)
    bgr = _cap_long_edge(bgr)
    if sharpen:
        bgr = light_sharpen_bgr(bgr)
    return bgr


def apply_ai_upscale(input_path: str, options: Optional[dict] = None) -> dict:
    options = options or {}
    trim_w = float(options.get("trim_w_mm", 148))
    trim_h = float(options.get("trim_h_mm", 210))
    bleed_mm = float(options.get("bleed_mm", DEFAULT_BLEED_MM))
    kind_info = classify_artwork(input_path)
    if not kind_info["eligible"]:
        return {
            "success": True,
            "used_original": True,
            "eligible": False,
            "provider": "original",
            "basic": False,
            "message": kind_info["reason"],
            "note": "",
            "kind": kind_info["kind"],
            "enhancement": "ai_upscale",
            "model": UPSCALE_MODEL_ID,
            "version": UPSCALE_MODEL_VERSION,
            "original_preserved": True,
        }

    try:
        source, kind_info = load_artwork_bgr(input_path)
    except Exception as exc:
        return _original_result(
            f"Couldn't prepare the artwork for enhancement, so the original will be used. {str(exc)[:160]}",
            0, 0, {}, "raster",
        )

    src_h, src_w = source.shape[:2]
    plan = choose_upscale_plan(src_w, src_h, trim_w, trim_h, bleed_mm)
    soft = looks_soft(source)
    forced = str(options.get("provider") or "").strip().lower()

    from ai_enhancements import _call_replicate, _get_replicate_token, _to_data_uri

    token = _get_replicate_token()
    assume_token = bool(options.get("assume_token"))
    provider_name = "basic"
    enhanced = None
    message = BASIC_MESSAGE

    try:
        if forced == "basic" or (_PROVIDER is None and forced != "stub" and not token and not assume_token):
            enhanced = basic_lanczos_upscale(source, plan["target_w"], plan["target_h"])
            provider_name = "basic"
            message = BASIC_MESSAGE
        elif _PROVIDER is not None or forced == "stub":
            if _PROVIDER is None:
                return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])
            work = _preview_bgr(source, 1600) if max(src_w, src_h) > 1600 else source
            temp_in = options.get("provider_input_path") or (input_path + ".upscale-src.png")
            _write_png(temp_in, work, dpi=72)
            out_path, error = _PROVIDER(temp_in, int(plan["model_scale"]), int(plan["target_w"]), int(plan["target_h"]))
            if error or not out_path or not os.path.exists(out_path):
                sys.stderr.write(f"[AI-UPSCALE] provider failed: {error}\n")
                err_text = str(error or "").lower()
                busy = "busy" in err_text or "timeout" in err_text or "timed out" in err_text
                return _original_result(AI_BUSY_MESSAGE if busy else AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])
            loaded = cv2.imread(out_path, cv2.IMREAD_COLOR)
            if loaded is None:
                return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])
            enhanced = _finish_bgr(loaded, plan, sharpen=True)
            provider_name = "stub" if forced == "stub" or not token else "replicate"
            message = (
                f"AI enhancement was applied with {UPSCALE_MODEL_ID} "
                f"version {UPSCALE_MODEL_VERSION} (scale {plan['model_scale']})."
                if provider_name == "replicate"
                else f"Upscale preview ready (scale {plan['model_scale']})."
            )
        else:
            # Token present (or assumed): call the pinned Real-ESRGAN version.
            if int(plan["model_scale"]) < 2:
                enhanced = basic_lanczos_upscale(source, plan["target_w"], plan["target_h"])
                provider_name = "basic"
                message = (
                    "The artwork is already near the size limit, so a basic sharpening resize was used "
                    "instead of sending it to the AI upscaler. You can keep your original instead."
                )
            else:
                upload = source
                if max(src_w, src_h) > 2000:
                    upload = _preview_bgr(source, 2000)
                temp_in = options.get("provider_input_path") or (input_path + ".upscale-src.png")
                _write_png(temp_in, upload, dpi=72)
                try:
                    data_uri = _to_data_uri(temp_in)
                except ValueError as ve:
                    sys.stderr.write(f"[AI-UPSCALE] upload prep failed: {ve}\n")
                    return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])
                model_input = {
                    "image": data_uri,
                    "scale": int(plan["model_scale"]),
                    "face_enhance": False,
                }
                del data_uri
                out_path, error = _call_replicate(
                    "ai_upscale",
                    UPSCALE_MODEL_OWNER,
                    UPSCALE_MODEL_NAME,
                    model_input,
                    version=UPSCALE_MODEL_VERSION,
                )
                if error or not out_path:
                    sys.stderr.write(f"[AI-UPSCALE] replicate failed: {error}\n")
                    friendly = AI_BUSY_MESSAGE if error and ("busy" in error.lower() or "timeout" in error.lower() or "timed out" in error.lower()) else AI_FAILED_MESSAGE
                    return _original_result(friendly, src_w, src_h, plan, kind_info["kind"])
                loaded = cv2.imread(out_path, cv2.IMREAD_COLOR)
                if loaded is None:
                    return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])
                enhanced = _finish_bgr(loaded, plan, sharpen=True)
                provider_name = "replicate"
                message = (
                    f"AI enhancement was applied with {UPSCALE_MODEL_ID} "
                    f"version {UPSCALE_MODEL_VERSION} (scale {plan['model_scale']})."
                )
    except Exception as exc:
        sys.stderr.write(f"[AI-UPSCALE] unexpected failure: {exc}\n")
        return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])

    if enhanced is None:
        return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])

    out_h, out_w = enhanced.shape[:2]
    output_path = options.get("output_path") or (os.path.splitext(input_path)[0] + "_ai_upscale.png")
    try:
        _write_png(output_path, enhanced, dpi=TARGET_DPI)
    except Exception as exc:
        sys.stderr.write(f"[AI-UPSCALE] could not save enhanced file: {exc}\n")
        return _original_result(AI_FAILED_MESSAGE, src_w, src_h, plan, kind_info["kind"])

    before_preview = options.get("before_preview_path")
    after_preview = options.get("after_preview_path")
    try:
        before_view, after_view = _comparison_views(source, enhanced)
        if before_preview:
            _write_png(before_preview, before_view, dpi=72)
        if after_preview:
            _write_png(after_preview, after_view, dpi=72)
    except Exception as exc:
        sys.stderr.write(f"[AI-UPSCALE] preview write failed (non-fatal): {exc}\n")

    note = _health_note(provider_name, int(plan["model_scale"]))
    meta_path = output_path + ".json"
    try:
        import json
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump({
                "src_w": src_w,
                "src_h": src_h,
                "kind": kind_info["kind"],
                "provider": provider_name,
                "scale": int(plan["model_scale"]),
                "model": UPSCALE_MODEL_ID,
                "version": UPSCALE_MODEL_VERSION,
                "note": note,
            }, handle)
    except Exception as exc:
        sys.stderr.write(f"[AI-UPSCALE] sidecar write failed (non-fatal): {exc}\n")

    enhanced_dpi = effective_print_dpi(out_w, out_h, trim_w, trim_h, bleed_mm)
    return {
        "success": True,
        "used_original": False,
        "eligible": True,
        "provider": provider_name,
        "basic": provider_name == "basic",
        "stub": provider_name == "stub",
        "enhancement": "ai_upscale",
        "model": UPSCALE_MODEL_ID,
        "version": UPSCALE_MODEL_VERSION,
        "scale": int(plan["model_scale"]),
        "message": message,
        "note": note,
        "enhanced_path": output_path,
        "src_w": src_w,
        "src_h": src_h,
        "out_w": out_w,
        "out_h": out_h,
        "effective_dpi": plan["effective_dpi"],
        "enhanced_dpi": enhanced_dpi,
        "soft": soft,
        "kind": kind_info["kind"],
        "original_preserved": True,
        "target_w": plan["target_w"],
        "target_h": plan["target_h"],
    }


def assess_artwork(path: str, trim_w_mm: float, trim_h_mm: float, bleed_mm: float = DEFAULT_BLEED_MM) -> dict:
    kind = classify_artwork(path)
    if not kind["eligible"]:
        return {
            "success": True,
            "eligible": False,
            "kind": kind["kind"],
            "suggestion": False,
            "strong": False,
            "effective_dpi": None,
            "soft": False,
            "message": kind["reason"],
            "explanation": "Make blurry or low-resolution artwork crisp and clear.",
        }
    try:
        bgr, kind = load_artwork_bgr(path)
    except Exception as exc:
        return {
            "success": True,
            "eligible": False,
            "kind": kind["kind"],
            "suggestion": False,
            "strong": False,
            "message": str(exc)[:200],
            "explanation": "Make blurry or low-resolution artwork crisp and clear.",
        }
    height, width = bgr.shape[:2]
    dpi = effective_print_dpi(width, height, trim_w_mm, trim_h_mm, bleed_mm)
    soft = looks_soft(bgr)
    strong = dpi < 200 or (soft and dpi < 300)
    suggestion = dpi < 300 or soft
    if dpi < 200:
        hint = f"This artwork is about {dpi} DPI at the print size, so it may look blurry. Turn on AI Enhance to make it crisp."
    elif dpi < 300:
        hint = f"This artwork is about {dpi} DPI at the chosen print size, under 300 DPI. AI Enhance can make it clearer."
    elif soft:
        hint = "This artwork looks a little soft. AI Enhance can crisp up text and edges."
    else:
        hint = ""
    plan = choose_upscale_plan(width, height, trim_w_mm, trim_h_mm, bleed_mm)
    return {
        "success": True,
        "eligible": True,
        "kind": kind["kind"],
        "suggestion": suggestion,
        "strong": strong,
        "effective_dpi": dpi,
        "soft": soft,
        "width": width,
        "height": height,
        "message": hint,
        "explanation": "Make blurry or low-resolution artwork crisp and clear.",
        "model": UPSCALE_MODEL_ID,
        "version": UPSCALE_MODEL_VERSION,
        "plan_scale": plan["model_scale"],
    }


def suggestion_copy(dpi: Optional[int], soft: bool) -> str:
    if dpi is not None and dpi < 200:
        return f"This artwork is about {dpi} DPI at the print size, so it may look blurry. Turn on AI Enhance to make it crisp."
    if dpi is not None and dpi < 300:
        return f"This artwork is about {dpi} DPI at the chosen print size, under 300 DPI. AI Enhance can make it clearer."
    if soft:
        return "This artwork looks a little soft. AI Enhance can crisp up text and edges."
    return ""
