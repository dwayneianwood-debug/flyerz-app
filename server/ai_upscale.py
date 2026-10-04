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
import tempfile
import threading
import time
import urllib.error
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


def choose_upscale_plan(
    src_w: int,
    src_h: int,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float = DEFAULT_BLEED_MM,
    max_scale: float | None = None,
    skip_below_ppi: float | None = None,
) -> dict:
    """Pick a Real-ESRGAN scale (1, 2, or 4) that aims for 300 DPI, capped at 4000px.

    When max_scale is set, the resize itself also stops at that multiple.
    When skip_below_ppi is set, a picture under that ppi is not enlarged.
    """
    need_w = (float(trim_w_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI
    need_h = (float(trim_h_mm) + 2.0 * float(bleed_mm)) / 25.4 * TARGET_DPI
    factor = max(need_w / max(src_w, 1), need_h / max(src_h, 1))
    effective = effective_print_dpi(src_w, src_h, trim_w_mm, trim_h_mm, bleed_mm)
    if skip_below_ppi is not None and effective < float(skip_below_ppi):
        return {
            "model_scale": 1,
            "target_w": int(src_w),
            "target_h": int(src_h),
            "needed_factor": round(float(factor), 3),
            "effective_dpi": effective,
            "skipped_low": True,
            "applied_scale": 1,
        }
    long_edge = max(src_w, src_h)
    if factor <= 1:
        model_scale = 2 if long_edge * 2 <= MAX_LONG_EDGE else 1
    elif factor <= 2:
        model_scale = 2
    else:
        model_scale = 4
    while model_scale > 1 and long_edge * model_scale > MAX_LONG_EDGE:
        model_scale //= 2
    if max_scale is not None:
        model_scale = min(int(model_scale), int(max_scale) if float(max_scale) >= 2 else 1)

    cover = max(factor, 1.0)
    if max_scale is not None:
        cover = min(cover, float(max_scale))
    out_w = int(round(src_w * cover))
    out_h = int(round(src_h * cover))
    long_out = max(out_w, out_h, 1)
    if long_out > MAX_LONG_EDGE:
        shrink = MAX_LONG_EDGE / float(long_out)
        out_w = max(1, int(round(out_w * shrink)))
        out_h = max(1, int(round(out_h * shrink)))
    if factor <= 1 and max(src_w, src_h) <= MAX_LONG_EDGE:
        out_w, out_h = src_w, src_h

    applied = max(out_w / max(src_w, 1), out_h / max(src_h, 1))
    return {
        "model_scale": int(model_scale),
        "target_w": int(out_w),
        "target_h": int(out_h),
        "needed_factor": round(float(factor), 3),
        "effective_dpi": effective,
        "skipped_low": False,
        "applied_scale": round(float(applied), 3),
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


def photo_unsharp(bgr: np.ndarray) -> np.ndarray:
    """Lanczos plate, then a mild unsharp. The radius stays about one pixel."""
    blurred = cv2.GaussianBlur(bgr, (0, 0), 1.15)
    sharp = cv2.addWeighted(bgr, 1.45, blurred, -0.45, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)


# A 1600px upload does not come back inside a press job. Send the original
# when its long side is already at or under this, otherwise this long side.
# The model then scales 2 or 4, and the plate resizes that picture to the art.
PRESS_UPLOAD_EDGE = 1024
# After the rest of the job is done, wait this long for the prediction.
PRESS_GRACE_S = 8.0
# The wait also stops at this, measured from the moment the job loaded the picture.
JOB_BUDGET_S = 35.0
LANCZOS_MESSAGE = "The picture was enlarged with Lanczos."
ESRGAN_MESSAGE = "The picture was enlarged with Real-ESRGAN."


def log_replicate(status, error, token: str = "") -> None:
    """Status and error only. The token is never written."""
    detail = str(error or "").replace("\n", " ").strip()
    secret = str(token or "").strip()
    if secret and secret in detail:
        detail = detail.replace(secret, "")
    sys.stderr.write(
        f"[AI-UPSCALE] replicate status={status or 'unknown'} error={detail[:240]}\n"
    )


def _press_upload(bgr: np.ndarray) -> np.ndarray:
    """The original picture, or that picture brought down to a size the model can finish."""
    height, width = bgr.shape[:2]
    if max(height, width) <= PRESS_UPLOAD_EDGE:
        return bgr
    return _preview_bgr(bgr, PRESS_UPLOAD_EDGE)


def _press_scale(upload: np.ndarray, target_long: float) -> int:
    """2 or 4. A large upload stays at 2 so the model can return inside the job."""
    long_edge = max(int(upload.shape[0]), int(upload.shape[1]), 1)
    need = float(target_long) / float(long_edge)
    if long_edge <= 512 and need > 2.0:
        return 4
    return 2


def _plate_target_long(trim_w: float, trim_h: float) -> float:
    from vector_plate import PRESS_PPI

    long_mm = max(float(trim_w), float(trim_h)) + 2.0 * DEFAULT_BLEED_MM
    return long_mm / 25.4 * float(PRESS_PPI)


class PlateUpscale:
    """Real-ESRGAN started with the file, polled until compose needs the plate."""

    def __init__(self, bgr: np.ndarray, trim_w: float, trim_h: float, runner=None):
        self.bgr = bgr
        self.trim_w = float(trim_w)
        self.trim_h = float(trim_h)
        self.target_long = _plate_target_long(self.trim_w, self.trim_h)
        self._runner = runner
        self.image = None
        self.status = ""
        self.error = ""
        self.scale = 2
        self._token = ""
        self._cancel_url = ""
        self._stop = threading.Event()
        self._done = threading.Event()
        self.started = time.perf_counter()
        self._wall_deadline = time.time() + JOB_BUDGET_S
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="press-esrgan", daemon=True)
        self._thread.start()
        return self

    def take(self):
        """The model picture, or None once the grace and the job budget are both spent."""
        wait = self._grace()
        if not self._done.wait(wait):
            self.cancel()
            if not self.status:
                self.status = "deadline"
                self.error = "not back before compose"
            log_replicate(self.status, self.error, self._token)
            return None
        return self.image

    def cancel(self) -> None:
        self._stop.set()
        url = self._cancel_url
        token = self._token
        if not url or not token:
            return
        try:
            from ai_enhancements import _replicate_cancel

            _replicate_cancel(url, token)
        except Exception:
            pass

    def _grace(self) -> float:
        now = time.perf_counter()
        budget_left = JOB_BUDGET_S - (now - self.started)
        return max(0.0, min(PRESS_GRACE_S, budget_left))

    def _run(self) -> None:
        try:
            if self._runner is not None:
                upload = _press_upload(self.bgr)
                self.scale = _press_scale(upload, self.target_long)
                image, status, error = self._runner(upload, self.scale, self._wall_deadline)
                self.image = image if image is not None and getattr(image, "size", 0) else None
                self.status = str(status or "")
                self.error = str(error or "")
                if self.image is None:
                    log_replicate(self.status, self.error, self._token)
                return
            self._replicate()
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)[:240]
            log_replicate(self.status, self.error, self._token)
        finally:
            self._done.set()

    def _replicate(self) -> None:
        from ai_enhancements import (
            _download_to_ramdisk,
            _get_replicate_token,
            _replicate_create_prediction,
            _replicate_poll_prediction,
            _to_data_uri,
        )

        token = (_get_replicate_token() or "").strip()
        self._token = token
        if not token or self._stop.is_set():
            self.status = "no-token"
            return
        upload = _press_upload(self.bgr)
        self.scale = _press_scale(upload, self.target_long)
        sys.stderr.write(
            f"[AI-UPSCALE] replicate start model={UPSCALE_MODEL_ID} "
            f"version={UPSCALE_MODEL_VERSION} scale={self.scale} "
            f"upload={upload.shape[1]}x{upload.shape[0]}\n"
        )
        folder = tempfile.mkdtemp(prefix="press-esrgan-")
        src = os.path.join(folder, "src.png")
        _write_png(src, upload, dpi=72)
        model_input = {
            "image": _to_data_uri(src),
            "scale": int(self.scale),
            "face_enhance": False,
        }
        try:
            prediction = _replicate_create_prediction(
                UPSCALE_MODEL_OWNER,
                UPSCALE_MODEL_NAME,
                model_input,
                token,
                version=UPSCALE_MODEL_VERSION,
                deadline=self._wall_deadline,
                prefer_wait=False,
            )
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:240]
            except Exception:
                body = ""
            self.status = f"http-{exc.code}"
            self.error = body or str(exc)[:240]
            log_replicate(self.status, self.error, token)
            return
        except TimeoutError as exc:
            self.status = "timeout"
            self.error = str(exc)[:240]
            log_replicate(self.status, self.error, token)
            return
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)[:240]
            log_replicate(self.status, self.error, token)
            return
        self._cancel_url = str((prediction.get("urls") or {}).get("cancel") or "")
        status = str(prediction.get("status") or "")
        if status == "succeeded":
            self._accept(prediction, token)
            return
        if status in ("failed", "canceled"):
            self.status = status
            self.error = str(prediction.get("error") or "")
            log_replicate(self.status, self.error, token)
            return
        poll_url = str((prediction.get("urls") or {}).get("get") or "")
        if not poll_url:
            self.status = status or "no-poll"
            self.error = "The prediction had no poll URL."
            log_replicate(self.status, self.error, token)
            return
        while time.time() < self._wall_deadline and not self._stop.is_set():
            result = _replicate_poll_prediction(
                poll_url, token, min(self._wall_deadline, time.time() + 2.5),
            )
            if self._stop.is_set():
                break
            if not result:
                continue
            status = str(result.get("status") or "")
            if status == "succeeded":
                self._accept(result, token)
                return
            if status in ("failed", "canceled"):
                self.status = status
                self.error = str(result.get("error") or "")
                log_replicate(self.status, self.error, token)
                return
        if not self.status:
            self.status = "deadline" if not self._stop.is_set() else "canceled"
            self.error = self.error or "not back before compose"
            log_replicate(self.status, self.error, token)

    def _accept(self, prediction: dict, token: str) -> None:
        from ai_enhancements import _download_to_ramdisk

        output = prediction.get("output")
        if isinstance(output, str):
            output_url = output
        elif isinstance(output, list) and output:
            output_url = str(output[-1]) if isinstance(output[-1], str) else str(output[0])
        else:
            self.status = "bad-output"
            self.error = f"Unexpected API output format: {type(output).__name__}"
            log_replicate(self.status, self.error, token)
            return
        try:
            out_path = _download_to_ramdisk(output_url, "_upscaled.png")
        except Exception as exc:
            self.status = "download"
            self.error = str(exc)[:240]
            log_replicate(self.status, self.error, token)
            return
        loaded = cv2.imread(out_path, cv2.IMREAD_COLOR)
        if loaded is None or not loaded.size:
            self.status = "empty"
            self.error = "The model returned a file that could not be read."
            log_replicate(self.status, self.error, token)
            return
        self.image = loaded
        self.status = "succeeded"
        self.error = ""
        log_replicate(self.status, "", token)


def start_plate_upscale(bgr: np.ndarray, trim_w: float, trim_h: float, runner=None):
    """Begin Real-ESRGAN only when VECTOR_ESRGAN is on and the picture is still soft.

    The setting stays off. A token on its own does not call Replicate.
    """
    from host_paths import esrgan_enabled

    if not esrgan_enabled():
        return None
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return None
    if (os.environ.get("VECTOR_SKIP_ESRGAN") or "").strip().lower() in ("1", "on", "true", "yes"):
        return None
    if runner is None:
        try:
            from ai_enhancements import _get_replicate_token

            if not (_get_replicate_token() or "").strip():
                return None
        except Exception:
            return None
    height, width = bgr.shape[:2]
    if effective_print_dpi(width, height, trim_w, trim_h, DEFAULT_BLEED_MM) >= 299:
        return None
    return PlateUpscale(bgr, trim_w, trim_h, runner=runner).start()


def full_frame_esrgan(bgr: np.ndarray, timeout_s: float = 12.0) -> Optional[np.ndarray]:
    """Real-ESRGAN of the whole picture when VECTOR_ESRGAN is on and a token is set.

    The setting stays off, so this does not call Replicate during a normal job.
    No token returns None. A failed call also returns None.
    The frame is not cover-cropped. The caller fits it onto the press plate.
    """
    from host_paths import esrgan_enabled

    if not esrgan_enabled():
        return None
    if bgr is None or bgr.size == 0:
        return None
    if (os.environ.get("VECTOR_SKIP_ESRGAN") or "").strip().lower() in ("1", "on", "true", "yes"):
        return None
    token = ""
    try:
        from ai_enhancements import _call_replicate, _get_replicate_token, _to_data_uri

        token = (_get_replicate_token() or "").strip()
    except Exception:
        return None
    if not token:
        return None
    height, width = bgr.shape[:2]
    folder = tempfile.mkdtemp(prefix="press-esrgan-")
    src = os.path.join(folder, "src.png")
    try:
        upload = bgr
        if max(height, width) > 1600:
            upload = _preview_bgr(bgr, 1600)
        _write_png(src, upload, dpi=72)
        from ai_enhancements import _call_replicate, _to_data_uri

        out_path, error = _call_replicate(
            "ai_upscale",
            UPSCALE_MODEL_OWNER,
            UPSCALE_MODEL_NAME,
            {"image": _to_data_uri(src), "scale": 2, "face_enhance": False},
            version=UPSCALE_MODEL_VERSION,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        log_replicate("error", str(exc)[:240], token)
        return None
    if error or not out_path or not os.path.exists(out_path):
        log_replicate("failed", error or "no picture", token)
        return None
    loaded = cv2.imread(out_path, cv2.IMREAD_COLOR)
    return loaded if loaded is not None and loaded.size else None


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
    raw_cap = options.get("max_scale")
    raw_floor = options.get("skip_below_ppi")
    plan = choose_upscale_plan(
        src_w,
        src_h,
        trim_w,
        trim_h,
        bleed_mm,
        max_scale=float(raw_cap) if raw_cap else None,
        skip_below_ppi=float(raw_floor) if raw_floor else None,
    )
    if plan.get("skipped_low"):
        return _original_result(
            "Under 75 ppi, so enhancement was not run.",
            src_w,
            src_h,
            plan,
            kind_info["kind"],
        )
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
            # Real-ESRGAN stays off unless VECTOR_ESRGAN is set. A token alone must not call Replicate.
            from host_paths import esrgan_enabled

            if not esrgan_enabled():
                enhanced = basic_lanczos_upscale(source, plan["target_w"], plan["target_h"])
                provider_name = "basic"
                message = BASIC_MESSAGE
            elif int(plan["model_scale"]) < 2:
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
