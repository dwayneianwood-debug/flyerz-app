#!/usr/bin/env python3
"""
Solid colour-border bleed.

The artwork is placed at trim size and a flat CMYK border fills the bleed.
No mirroring, stretching, or resampling of the trim pixels. Preset inks are
defined in CMYK percent; RGB is only the on-screen equivalent.
"""

from __future__ import annotations

import json
import os
import sys
import zlib
from typing import Any

import numpy as np

# Rich black matches smart_bleed PRESS_SAFE_RICH_BLACK (C40 M30 Y30 K100).
PRESETS: list[dict[str, Any]] = [
    {"id": "white", "label": "White", "c": 0, "m": 0, "y": 0, "k": 0, "r": 255, "g": 255, "b": 255},
    {"id": "black", "label": "Black", "c": 0, "m": 0, "y": 0, "k": 100, "r": 0, "g": 0, "b": 0},
    {"id": "richBlack", "label": "Rich Black", "c": 40, "m": 30, "y": 30, "k": 100, "r": 0, "g": 0, "b": 0},
    {"id": "cyan", "label": "Cyan", "c": 100, "m": 0, "y": 0, "k": 0, "r": 0, "g": 255, "b": 255},
    {"id": "magenta", "label": "Magenta", "c": 0, "m": 100, "y": 0, "k": 0, "r": 255, "g": 0, "b": 255},
    {"id": "yellow", "label": "Yellow", "c": 0, "m": 0, "y": 100, "k": 0, "r": 255, "g": 255, "b": 0},
    {"id": "red", "label": "Red", "c": 0, "m": 100, "y": 100, "k": 0, "r": 255, "g": 0, "b": 0},
    {"id": "blue", "label": "Blue", "c": 100, "m": 100, "y": 0, "k": 0, "r": 0, "g": 0, "b": 255},
    {"id": "green", "label": "Green", "c": 100, "m": 0, "y": 100, "k": 0, "r": 0, "g": 255, "b": 0},
]


def clamp_pct(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(100.0, number))


def cmyk_tuple(c: Any, m: Any, y: Any, k: Any) -> tuple[float, float, float, float]:
    return (clamp_pct(c), clamp_pct(m), clamp_pct(y), clamp_pct(k))


def cmyk_to_rgb(c: float, m: float, y: float, k: float) -> tuple[int, int, int]:
    """Naive CMYK → sRGB for on-screen swatches and the RGB proof."""
    C, M, Y, K = (clamp_pct(c) / 100.0, clamp_pct(m) / 100.0, clamp_pct(y) / 100.0, clamp_pct(k) / 100.0)
    r = int(round(255 * (1.0 - C) * (1.0 - K)))
    g = int(round(255 * (1.0 - M) * (1.0 - K)))
    b = int(round(255 * (1.0 - Y) * (1.0 - K)))
    return r, g, b


def cmyk_to_bgr(cmyk: tuple[float, float, float, float]) -> tuple[int, int, int]:
    r, g, b = cmyk_to_rgb(*cmyk)
    return b, g, r


def bgr_to_cmyk(b: float, g: float, r: float) -> tuple[float, float, float, float]:
    rn, gn, bn = max(0.0, min(255.0, r)) / 255.0, max(0.0, min(255.0, g)) / 255.0, max(0.0, min(255.0, b)) / 255.0
    k = 1.0 - max(rn, gn, bn)
    if k >= 0.999:
        return (0.0, 0.0, 0.0, 100.0)
    c = (1.0 - rn - k) / (1.0 - k)
    m = (1.0 - gn - k) / (1.0 - k)
    y = (1.0 - bn - k) / (1.0 - k)
    return (
        round(max(0.0, min(1.0, c)) * 100.0, 2),
        round(max(0.0, min(1.0, m)) * 100.0, 2),
        round(max(0.0, min(1.0, y)) * 100.0, 2),
        round(k * 100.0, 2),
    )


def sample_edge_cmyk(img: np.ndarray) -> tuple[float, float, float, float]:
    """Average of the outer 1-pixel ring, then one CMYK conversion."""
    if img is None or img.size == 0:
        return (0.0, 0.0, 0.0, 0.0)
    plane = img
    if plane.ndim == 2:
        plane = np.repeat(plane[:, :, None], 3, axis=2)
    if plane.shape[2] == 4:
        plane = plane[:, :, :3]
    h, w = plane.shape[:2]
    if h < 1 or w < 1:
        return (0.0, 0.0, 0.0, 0.0)
    strips = [plane[0, :, :], plane[-1, :, :]]
    if h > 2:
        strips.append(plane[1:-1, 0, :])
        strips.append(plane[1:-1, -1, :])
    edge = np.concatenate([s.reshape(-1, 3) for s in strips], axis=0).astype(np.float64)
    mean = edge.mean(axis=0)
    return bgr_to_cmyk(float(mean[0]), float(mean[1]), float(mean[2]))


def apply_colour_border_bgr(img: np.ndarray, bleed_px: int, cmyk: tuple[float, float, float, float]) -> np.ndarray:
    """Pad with a solid border. The inner trim block is copied, not resampled."""
    bleed_px = max(0, int(bleed_px))
    if img is None or img.size == 0 or bleed_px == 0:
        return img
    bgr = cmyk_to_bgr(cmyk)
    h, w = img.shape[:2]
    if img.ndim == 2:
        color = int(round((bgr[0] + bgr[1] + bgr[2]) / 3))
        canvas = np.full((h + 2 * bleed_px, w + 2 * bleed_px), color, dtype=np.uint8)
        canvas[bleed_px : bleed_px + h, bleed_px : bleed_px + w] = img
        return canvas
    channels = img.shape[2]
    canvas = np.empty((h + 2 * bleed_px, w + 2 * bleed_px, channels), dtype=np.uint8)
    if channels == 4:
        canvas[:, :, 0] = bgr[0]
        canvas[:, :, 1] = bgr[1]
        canvas[:, :, 2] = bgr[2]
        canvas[:, :, 3] = 255
    else:
        canvas[:, :, 0] = bgr[0]
        canvas[:, :, 1] = bgr[1]
        canvas[:, :, 2] = bgr[2]
        if channels > 3:
            canvas[:, :, 3:] = 255
    canvas[bleed_px : bleed_px + h, bleed_px : bleed_px + w] = img
    return canvas


def _apply_crop(img: np.ndarray, crop_x: float, crop_y: float, crop_w: float, crop_h: float) -> np.ndarray:
    if crop_w <= 0 or crop_h <= 0:
        return img
    h, w = img.shape[:2]
    if crop_x <= 1.0 and crop_y <= 1.0 and crop_w <= 1.0 and crop_h <= 1.0 and crop_w > 0:
        x = int(round(crop_x * w))
        y = int(round(crop_y * h))
        cw = int(round(crop_w * w))
        ch = int(round(crop_h * h))
    else:
        x = int(round(crop_x))
        y = int(round(crop_y))
        cw = int(round(crop_w))
        ch = int(round(crop_h))
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    cw = min(cw, w - x)
    ch = min(ch, h - y)
    if cw > 10 and ch > 10:
        return img[y : y + ch, x : x + cw]
    return img


def _load_artwork_bgr(path: str, page: int) -> np.ndarray:
    import cv2

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz

        doc = fitz.open(path)
        try:
            index = max(0, min(page - 1, len(doc) - 1))
            pdf_page = doc[index]
            scale = 150.0 / 72.0
            pix = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                arr = arr[:, :, :3]
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        finally:
            doc.close()
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not read artwork: {path}")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img[:, :, :3]


def render_colour_border_preview(
    src: str,
    dest: str,
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float,
    cmyk: tuple[float, float, float, float],
    draw_lines: bool,
    page: int = 1,
    crop: tuple[float, float, float, float] | None = None,
    sample_edge: bool = False,
) -> dict[str, Any]:
    import cv2
    from smart_bleed import cover_scale_to_trim_px

    img = _load_artwork_bgr(src, page)
    if crop is not None:
        img = _apply_crop(img, *crop)
    preview_dpi = 110.0
    trim_w_px = max(1, int(round((trim_w_mm / 25.4) * preview_dpi)))
    trim_h_px = max(1, int(round((trim_h_mm / 25.4) * preview_dpi)))
    trimmed = cover_scale_to_trim_px(img, trim_w_px, trim_h_px)
    if sample_edge:
        cmyk = sample_edge_cmyk(trimmed)
    bleed_px = max(1, int(round((bleed_mm / 25.4) * preview_dpi)))
    bordered = apply_colour_border_bgr(trimmed, bleed_px, cmyk)
    if draw_lines:
        from bleed_preview import draw_bleed_preview

        bordered = draw_bleed_preview(bordered, bleed_mm, preview_dpi)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    if not cv2.imwrite(dest, bordered):
        raise RuntimeError("Could not write colour border preview")
    r, g, b = cmyk_to_rgb(*cmyk)
    return {
        "success": True,
        "c": cmyk[0],
        "m": cmyk[1],
        "y": cmyk[2],
        "k": cmyk[3],
        "r": r,
        "g": g,
        "b": b,
        "trim_w_mm": trim_w_mm,
        "trim_h_mm": trim_h_mm,
        "bleed_mm": bleed_mm,
        "width": int(bordered.shape[1]),
        "height": int(bordered.shape[0]),
    }


def stamp_cmyk_bleed(
    pdf_path: str,
    cmyk: tuple[float, float, float, float],
    trim_w_mm: float,
    trim_h_mm: float,
    bleed_mm: float,
) -> dict[str, Any]:
    """Overwrite the bleed ring of each page's CMYK image. Trim pixels stay put."""
    import pikepdf

    c, m, y, k = cmyk
    ink = np.array(
        [
            int(round(c / 100.0 * 255)),
            int(round(m / 100.0 * 255)),
            int(round(y / 100.0 * 255)),
            int(round(k / 100.0 * 255)),
        ],
        dtype=np.uint8,
    )
    pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)
    stamped = 0
    try:
        for page in pdf.pages:
            resources = page.get("/Resources")
            if resources is None or "/XObject" not in resources:
                continue
            for name, obj in list(resources["/XObject"].items()):
                if str(obj.get("/Subtype")) != "/Image":
                    continue
                width = int(obj.get("/Width") or 0)
                height = int(obj.get("/Height") or 0)
                if width < 4 or height < 4:
                    continue
                if int(obj.get("/BitsPerComponent") or 8) != 8:
                    continue
                if "/Decode" in obj:
                    del obj["/Decode"]
                raw = obj.read_bytes()
                channels = 4
                if len(raw) != width * height * channels:
                    if len(raw) == width * height * 3:
                        channels = 3
                    else:
                        continue
                arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, channels).copy()
                if channels == 3:
                    rgb = cmyk_to_rgb(c, m, y, k)
                    color = np.array([rgb[0], rgb[1], rgb[2]], dtype=np.uint8)
                else:
                    color = ink
                bleed_x = max(1, int(round(width * bleed_mm / (trim_w_mm + 2 * bleed_mm))))
                bleed_y = max(1, int(round(height * bleed_mm / (trim_h_mm + 2 * bleed_mm))))
                bleed_x = min(bleed_x, width // 3)
                bleed_y = min(bleed_y, height // 3)
                arr[:bleed_y, :, :] = color
                arr[height - bleed_y :, :, :] = color
                arr[:, :bleed_x, :] = color
                arr[:, width - bleed_x :, :] = color
                obj.write(zlib.compress(arr.tobytes()), filter=pikepdf.Name("/FlateDecode"))
                stamped += 1
        pdf.save(pdf_path)
    finally:
        pdf.close()
    if stamped == 0:
        raise RuntimeError("Colour border could not find a page image to recolour")
    return {"stamped": stamped, "cmyk": [c, m, y, k]}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Usage: colour_border.py <presets|preview|sample>"}))
        sys.exit(1)
    command = sys.argv[1]
    try:
        if command == "presets":
            print(json.dumps({"success": True, "presets": PRESETS}))
            return
        if command == "sample":
            src = sys.argv[2]
            trim_w = float(sys.argv[3])
            trim_h = float(sys.argv[4])
            page = int(sys.argv[5]) if len(sys.argv) > 5 else 1
            from smart_bleed import cover_scale_to_trim_px

            img = _load_artwork_bgr(src, page)
            dpi = 110.0
            trimmed = cover_scale_to_trim_px(
                img,
                max(1, int(round(trim_w / 25.4 * dpi))),
                max(1, int(round(trim_h / 25.4 * dpi))),
            )
            cmyk = sample_edge_cmyk(trimmed)
            r, g, b = cmyk_to_rgb(*cmyk)
            print(json.dumps({"success": True, "c": cmyk[0], "m": cmyk[1], "y": cmyk[2], "k": cmyk[3], "r": r, "g": g, "b": b}))
            return
        if command == "preview":
            options = json.loads(sys.argv[2])
            crop = options.get("crop")
            crop_tuple = None
            if isinstance(crop, list) and len(crop) == 4:
                crop_tuple = (float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3]))
            info = render_colour_border_preview(
                options["src"],
                options["dest"],
                float(options.get("trimW") or 148),
                float(options.get("trimH") or 210),
                float(options.get("bleedMm") or 5),
                cmyk_tuple(options.get("c"), options.get("m"), options.get("y"), options.get("k")),
                bool(options.get("lines", True)),
                int(options.get("page") or 1),
                crop_tuple,
                bool(options.get("sampleEdge")),
            )
            print(json.dumps(info))
            return
        print(json.dumps({"success": False, "error": f"Unknown command: {command}"}))
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
