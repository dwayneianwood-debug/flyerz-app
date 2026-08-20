"""
Smart Prepress Enhancer — OpenCV-only (no DL). Lanczos upscale + conservative unsharp when
upscaling >15%; otherwise preserves legacy INTER_AREA / INTER_CUBIC behavior (zero regression).
"""
from __future__ import annotations

import sys

import cv2
import numpy as np

# Upscale by more than 15% vs source → eligible for Lanczos + unsharp
UPSCALE_ENHANCE_THRESHOLD = 1.15

# Soft RAM guard: skip unsharp on enormous matrices (peak memory ~channels × px)
_MAX_PIXELS_FOR_UNSHARP = 120_000_000


def _conservative_unsharp_bgr_or_gray(img: np.ndarray) -> np.ndarray:
    gaussian_3 = cv2.GaussianBlur(img, (0, 0), 2.0)
    return cv2.addWeighted(img, 1.5, gaussian_3, -0.5, 0)


def enhance_print_quality(
    img: np.ndarray,
    dst_width: int,
    dst_height: int,
    scale_factor: float,
) -> np.ndarray:
    """
    Resize to (dst_width, dst_height).

    If scale_factor > 1.15 and upscale: INTER_LANCZOS4 then conservative unsharp mask.
    Else: INTER_AREA (downscale) or INTER_CUBIC (modest changes) — matches prior compile behavior.

    On MemoryError / OpenCV errors / dimension errors: fallback resize without enhancement.
    """
    dw = max(1, int(dst_width))
    dh = max(1, int(dst_height))

    try:
        if img is None or img.size == 0:
            raise ValueError("empty image matrix")

        # Legacy path — no premium enhancement (zero regression for already-high-res assets)
        if scale_factor <= UPSCALE_ENHANCE_THRESHOLD or scale_factor <= 1.0:
            interp = cv2.INTER_AREA if scale_factor < 1.0 else cv2.INTER_CUBIC
            return cv2.resize(img, (dw, dh), interpolation=interp)

        upscaled = cv2.resize(img, (dw, dh), interpolation=cv2.INTER_LANCZOS4)

        h, w = upscaled.shape[:2]
        px = h * w
        if px <= _MAX_PIXELS_FOR_UNSHARP:
            if upscaled.ndim == 3 and upscaled.shape[2] == 4:
                bgr = upscaled[:, :, :3]
                alpha = upscaled[:, :, 3:4]
                sharp_bgr = _conservative_unsharp_bgr_or_gray(bgr)
                upscaled = np.concatenate([sharp_bgr, alpha], axis=2)
            else:
                upscaled = _conservative_unsharp_bgr_or_gray(upscaled)
            sys.stderr.write("[ENHANCE] Applied Lanczos4 + Unsharp Mask\n")
        else:
            sys.stderr.write(
                "[ENHANCE] Applied Lanczos4 only (skipped unsharp: size guard for memory safety)\n"
            )

        return upscaled

    except (MemoryError, cv2.error, ValueError) as ex:
        sys.stderr.write(f"[ENHANCE] Fallback (no enhancement): {ex}\n")
        return _fallback_resize(img, dw, dh, scale_factor)
    except Exception as ex:
        sys.stderr.write(f"[ENHANCE] Fallback (unexpected): {ex}\n")
        return _fallback_resize(img, dw, dh, scale_factor)


def _fallback_resize(img: np.ndarray, dw: int, dh: int, scale_factor: float) -> np.ndarray:
    try:
        interp = cv2.INTER_AREA if scale_factor < 1.0 else cv2.INTER_CUBIC
        return cv2.resize(img, (dw, dh), interpolation=interp)
    except Exception:
        return img
