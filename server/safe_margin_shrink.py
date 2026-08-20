#!/usr/bin/env python3
"""
Safe Margin Shrink — Radar mode (non-destructive)

Legacy crop-inward + reflect-extend has been disabled. This tool now:
- Copies artwork bytes unchanged to the output path (image or PDF).
- Runs Canny-based boundary analysis on the outer 30px to flag high-frequency
  geometry near trim (safe-zone radar for prepress reports).

Usage:
  python3 server/safe_margin_shrink.py <input_path> <output_path> [shrink_factor]
  python3 server/safe_margin_shrink.py <input_path> preview  [shrink_factor]
"""

import sys
import json
import os
import shutil
try:
    import fitz
except ImportError:
    fitz = None

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PIL import Image, ImageDraw
import numpy as np

BOUNDARY_PX = 30
CANNY_WEAK = 60
CANNY_STRONG = 180
EDGE_DENSITY_WARN = 0.012


def analyze_safe_zone_boundary_radar(rgb_np: np.ndarray, boundary_px: int = BOUNDARY_PX) -> dict:
    """
    Read-only radar: Canny edge density within `boundary_px` of each edge.
    """
    if not HAS_CV2:
        return {
            "warnings": ["OpenCV unavailable — boundary radar skipped"],
            "densities": {},
            "boundary_px": boundary_px,
        }
    h, w = rgb_np.shape[:2]
    if h < boundary_px * 2 or w < boundary_px * 2:
        return {
            "warnings": ["Image too small for 30px boundary band analysis"],
            "densities": {},
            "boundary_px": boundary_px,
        }
    gray = cv2.cvtColor(rgb_np, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, CANNY_WEAK, CANNY_STRONG)

    top = edges[0:boundary_px, :]
    bot = edges[h - boundary_px : h, :]
    lef = edges[:, 0:boundary_px]
    rig = edges[:, w - boundary_px : w]

    def _dens(band):
        return float(np.count_nonzero(band)) / float(band.size)

    densities = {
        "top": round(_dens(top), 5),
        "bottom": round(_dens(bot), 5),
        "left": round(_dens(lef), 5),
        "right": round(_dens(rig), 5),
    }
    warnings = []
    for side, d in densities.items():
        if d > EDGE_DENSITY_WARN:
            warnings.append(
                f"{side.capitalize()} edge: elevated Canny density ({d}) within {boundary_px}px "
                f"(possible high-frequency geometry near safe zone)"
            )
    if not warnings:
        warnings.append("No elevated edge activity in outer 30px boundary (radar pass).")
    return {"warnings": warnings, "densities": densities, "boundary_px": boundary_px}


def _rgb_np_from_input(input_path: str):
    """RGB uint8 array and effective dpi hint for mm math in previews."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".pdf":
        if not fitz:
            raise RuntimeError("PyMuPDF (fitz) not available for PDF radar")
        doc = fitz.open(input_path)
        page = doc[0]
        dpi = 300
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        doc.close()
        del pix
        return rgb.copy(), dpi
    img = Image.open(input_path).convert("RGB")
    return np.array(img), 300


def shrink_image(input_path, output_path, shrink_factor=0.92):
    _ = shrink_factor
    img = Image.open(input_path).convert("RGB")
    original_np = np.array(img)
    width, height = img.size

    radar = analyze_safe_zone_boundary_radar(original_np, BOUNDARY_PX)
    result_img = Image.fromarray(original_np, "RGB")

    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".png":
        result_img.save(output_path, "PNG")
    else:
        result_img.save(output_path, "JPEG", quality=95)

    return {
        "success": True,
        "originalSize": [width, height],
        "innerSize": [width, height],
        "shrinkFactor": shrink_factor,
        "marginPx": [0, 0],
        "marginMm": [0.0, 0.0],
        "blendMethod": "radar_only_no_geometry_change",
        "outputPath": output_path,
        "safeZoneRadar": radar,
    }


def shrink_pdf(input_path, output_path, shrink_factor=0.92):
    _ = shrink_factor
    if not fitz:
        return {"success": False, "error": "PyMuPDF (fitz) not available"}

    rgb, dpi = _rgb_np_from_input(input_path)
    radar = analyze_safe_zone_boundary_radar(rgb, BOUNDARY_PX)
    del rgb

    shutil.copy2(input_path, output_path)

    doc = fitz.open(input_path)
    page_count = len(doc)
    page0 = doc[0]
    page_w_pt = page0.rect.width
    page_h_pt = page0.rect.height
    doc.close()

    return {
        "success": True,
        "pageCount": page_count,
        "pages": [{
            "page": 1,
            "originalSizeMm": [round(page_w_pt / 72 * 25.4, 1), round(page_h_pt / 72 * 25.4, 1)],
            "blendMethod": "radar_only_vector_preserved_copy",
            "dpi_hint": dpi,
            "note": "Radar used page 1 render only; all pages copied verbatim.",
        }],
        "shrinkFactor": shrink_factor,
        "outputPath": output_path,
        "safeZoneRadar": radar,
    }


def generate_preview(input_path, preview_path, shrink_factor=0.92):
    _ = shrink_factor
    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".pdf":
        if not fitz:
            return {"success": False, "error": "PyMuPDF not available"}
        rgb, dpi = _rgb_np_from_input(input_path)
    else:
        rgb = np.array(Image.open(input_path).convert("RGB"))
        dpi = 300

    radar = analyze_safe_zone_boundary_radar(rgb, BOUNDARY_PX)
    result_img = Image.fromarray(rgb, "RGB")
    draw = ImageDraw.Draw(result_img)
    draw.rectangle([0, 0, rgb.shape[1] - 1, rgb.shape[0] - 1], outline=(255, 0, 0), width=2)

    iy0, iy1 = BOUNDARY_PX, rgb.shape[0] - BOUNDARY_PX
    ix0, ix1 = BOUNDARY_PX, rgb.shape[1] - BOUNDARY_PX
    draw.rectangle([ix0, iy0, ix1 - 1, iy1 - 1], outline=(0, 200, 0), width=2)

    y_text = 8
    for wline in radar.get("warnings", [])[:6]:
        draw.text((BOUNDARY_PX + 4, y_text), wline[:100], fill=(255, 0, 0))
        y_text += 14

    width, height = result_img.width, result_img.height
    max_preview = 1200
    if width > max_preview or height > max_preview:
        ratio = min(max_preview / width, max_preview / height)
        result_img = result_img.resize(
            (int(width * ratio), int(height * ratio)), Image.Resampling.LANCZOS
        )

    result_img.save(preview_path, "PNG")

    return {
        "success": True,
        "previewPath": preview_path,
        "originalSize": [rgb.shape[1], rgb.shape[0]],
        "innerSize": [rgb.shape[1], rgb.shape[0]],
        "marginPx": [0, 0],
        "marginMm": [0.0, 0.0],
        "shrinkFactor": shrink_factor,
        "blendMethod": "radar_only_preview",
        "safeZoneRadar": radar,
    }


def auto_resolve_safe_zone(img: np.ndarray, target_bleed_px: int = 59,
                           bleed_strategy: str = "auto", dpi: float = 300.0):
    """
    Delegates to smart_bleed.auto_resolve_safe_zone — Shrink & Re-Bleed orchestrator around final bleed.
    """
    import smart_bleed as _sb
    out, _meta = _sb.auto_resolve_safe_zone(img, target_bleed_px, bleed_strategy, dpi)
    return out


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "error": "Usage: safe_margin_shrink.py <input> <output|preview> [shrink_factor]"}))
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    shrink_factor = float(sys.argv[3]) if len(sys.argv) > 3 else 0.92

    shrink_factor = max(0.50, min(0.99, shrink_factor))

    if not os.path.exists(input_path):
        print(json.dumps({"success": False, "error": f"Input file not found: {input_path}"}))
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()

    if output_path == "preview":
        base = os.path.splitext(input_path)[0]
        preview_path = f"{base}_shrink_preview.png"
        result = generate_preview(input_path, preview_path, shrink_factor)
    elif ext == ".pdf":
        result = shrink_pdf(input_path, output_path, shrink_factor)
    elif ext in (".jpg", ".jpeg", ".png"):
        result = shrink_image(input_path, output_path, shrink_factor)
    else:
        result = {"success": False, "error": f"Unsupported file type: {ext}"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
