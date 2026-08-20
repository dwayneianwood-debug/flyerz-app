#!/usr/bin/env python3
"""
Flyerz.co.za Artwork Intelligence — Bleed Preview Generator

Generates a high-resolution preview image of the corrected artwork showing:
  - The full bleed area
  - Red trim/cut lines where the guillotine will cut
  - Safe zone boundary (dashed green)
  - Dimension labels
  - Bleed area shading

Output: PNG image ready for client review or download.
"""

import sys
import json
import os
import shutil
import glob as globmod
import traceback

import cv2
import numpy as np
import fitz


def find_gs_binary() -> str:
    gs_path = shutil.which("gs")
    if gs_path:
        return gs_path
    nix_matches = globmod.glob("/nix/store/*/bin/gs")
    for p in sorted(nix_matches, reverse=True):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "gs"


GS_BIN = find_gs_binary()
BLEED_MM = 5.0
SAFE_ZONE_MM = 3.0


def _mm_to_px(mm: float, dpi: float) -> int:
    return max(0, int(round((mm / 25.4) * dpi)))


def _px_to_mm(px: int, dpi: float) -> float:
    return (px / dpi) * 25.4


def draw_bleed_preview(img_bgr: np.ndarray, bleed_mm: float, dpi: float,
                       page_num: int = 1, page_label: str = "") -> np.ndarray:
    h, w = img_bgr.shape[:2]
    bleed_px = _mm_to_px(bleed_mm, dpi)

    trim_left = bleed_px
    trim_top = bleed_px
    trim_right = w - bleed_px
    trim_bottom = h - bleed_px

    trim_left = max(0, min(trim_left, w))
    trim_top = max(0, min(trim_top, h))
    trim_right = max(trim_left, min(trim_right, w))
    trim_bottom = max(trim_top, min(trim_bottom, h))

    preview = img_bgr.copy()

    overlay = preview.copy()

    cv2.rectangle(overlay, (0, 0), (w, trim_top), (0, 0, 180), -1)
    cv2.rectangle(overlay, (0, trim_bottom), (w, h), (0, 0, 180), -1)
    cv2.rectangle(overlay, (0, trim_top), (trim_left, trim_bottom), (0, 0, 180), -1)
    cv2.rectangle(overlay, (trim_right, trim_top), (w, trim_bottom), (0, 0, 180), -1)

    alpha = 0.25
    preview = cv2.addWeighted(overlay, alpha, preview, 1 - alpha, 0)

    line_thickness = max(2, int(dpi / 100))

    cv2.rectangle(preview, (trim_left, trim_top), (trim_right, trim_bottom),
                  (0, 0, 255), line_thickness)

    corner_len = max(20, int(dpi / 10))
    corners = [
        (trim_left, trim_top),
        (trim_right, trim_top),
        (trim_left, trim_bottom),
        (trim_right, trim_bottom),
    ]
    for cx, cy in corners:
        dx = 1 if cx == trim_left else -1
        dy = 1 if cy == trim_top else -1
        cv2.line(preview, (cx, cy), (cx + dx * corner_len, cy), (0, 0, 255), line_thickness + 1)
        cv2.line(preview, (cx, cy), (cx, cy + dy * corner_len), (0, 0, 255), line_thickness + 1)

    font_scale = max(0.5, dpi / 500)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_thickness = max(1, int(dpi / 200))

    safe_px = _mm_to_px(SAFE_ZONE_MM, dpi)
    safe_left = trim_left + safe_px
    safe_top = trim_top + safe_px
    safe_right = trim_right - safe_px
    safe_bottom = trim_bottom - safe_px

    if safe_right > safe_left and safe_bottom > safe_top:
        dash_len = max(8, int(dpi / 30))
        safe_color = (0, 200, 0)
        safe_thick = max(1, line_thickness - 1)

        for x in range(safe_left, safe_right, dash_len * 2):
            x_end = min(x + dash_len, safe_right)
            cv2.line(preview, (x, safe_top), (x_end, safe_top), safe_color, safe_thick)
            cv2.line(preview, (x, safe_bottom), (x_end, safe_bottom), safe_color, safe_thick)

        for y in range(safe_top, safe_bottom, dash_len * 2):
            y_end = min(y + dash_len, safe_bottom)
            cv2.line(preview, (safe_left, y), (safe_left, y_end), safe_color, safe_thick)
            cv2.line(preview, (safe_right, y), (safe_right, y_end), safe_color, safe_thick)

        ruler_half = max(1, int(dpi / 200))
        ruler_overlay = preview.copy()
        ruler_x1 = max(safe_right - ruler_half, trim_left)
        ruler_x2 = min(safe_right + ruler_half, trim_right)
        cv2.rectangle(ruler_overlay, (ruler_x1, trim_top), (ruler_x2, trim_bottom), (0, 0, 255), -1)
        preview = cv2.addWeighted(ruler_overlay, 0.35, preview, 0.65, 0)

        ruler_label = "CUT LINE"
        rl_scale = font_scale * 0.45
        rl_thick = max(1, font_thickness - 1)
        rl_x = safe_right + ruler_half + 4
        rl_y = trim_top + int(30 * font_scale)
        if rl_x + 60 < trim_right and rl_y > trim_top:
            cv2.putText(preview, ruler_label, (rl_x, rl_y), font,
                        rl_scale, (0, 0, 255), rl_thick, cv2.LINE_AA)

    trim_w_mm = _px_to_mm(trim_right - trim_left, dpi)
    trim_h_mm = _px_to_mm(trim_bottom - trim_top, dpi)
    total_w_mm = _px_to_mm(w, dpi)
    total_h_mm = _px_to_mm(h, dpi)

    label_trim = f"Trim: {trim_w_mm:.1f} x {trim_h_mm:.1f} mm"
    label_total = f"Total (with bleed): {total_w_mm:.1f} x {total_h_mm:.1f} mm"
    label_bleed = f"Bleed: {bleed_mm:.1f} mm all sides"

    labels = [label_trim, label_total, label_bleed]
    if page_label:
        labels.insert(0, page_label)

    label_y_start = int(h - 15 * font_scale * len(labels) - 10)
    label_bg_h = int(20 * font_scale * len(labels) + 20)
    label_bg_top = max(0, label_y_start - 10)

    cv2.rectangle(preview, (0, label_bg_top), (w, h), (40, 40, 40), -1)
    cv2.rectangle(preview, (0, label_bg_top), (w, h), (0, 0, 255), line_thickness)

    for i, label in enumerate(labels):
        text_size = cv2.getTextSize(label, font, font_scale * 0.8, font_thickness)[0]
        text_x = (w - text_size[0]) // 2
        text_y = label_bg_top + 20 + int(i * 22 * font_scale)

        cv2.putText(preview, label, (text_x + 1, text_y + 1), font,
                    font_scale * 0.8, (0, 0, 0), font_thickness + 1, cv2.LINE_AA)
        cv2.putText(preview, label, (text_x, text_y), font,
                    font_scale * 0.8, (255, 255, 255), font_thickness, cv2.LINE_AA)

    bleed_label = "BLEED AREA"
    bl_scale = font_scale * 0.6
    bl_thick = max(1, font_thickness - 1)

    positions = [
        (w // 2, bleed_px // 2),
        (w // 2, h - bleed_px // 2),
        (bleed_px // 2, h // 2),
        (w - bleed_px // 2, h // 2),
    ]

    for bx, by in positions:
        bs = cv2.getTextSize(bleed_label, font, bl_scale, bl_thick)[0]
        bx_pos = bx - bs[0] // 2
        by_pos = by + bs[1] // 2
        if bx_pos > 0 and by_pos > 0 and bx_pos + bs[0] < w and by_pos < h:
            cv2.putText(preview, bleed_label, (bx_pos, by_pos), font,
                        bl_scale, (180, 180, 255), bl_thick, cv2.LINE_AA)

    cut_label = "CUT LINE"
    cl_scale = font_scale * 0.5
    cl_thick = max(1, font_thickness - 1)

    cl_positions = [
        (trim_left + 10, trim_top - 5),
        (trim_right - 80, trim_top - 5),
        (trim_left + 10, trim_bottom + 15),
    ]
    for clx, cly in cl_positions:
        if 0 < clx < w and 0 < cly < h:
            cv2.putText(preview, cut_label, (clx, cly), font,
                        cl_scale, (0, 0, 255), cl_thick, cv2.LINE_AA)

    sz_label = "SAFE ZONE"
    sz_scale = font_scale * 0.45
    if safe_right > safe_left and safe_bottom > safe_top:
        cv2.putText(preview, sz_label, (safe_left + 5, safe_top + int(15 * font_scale)),
                    font, sz_scale, (0, 200, 0), bl_thick, cv2.LINE_AA)

    return preview


def generate_bleed_preview_pdf(corrected_pdf_path: str, output_png_path: str,
                                bleed_mm: float = BLEED_MM, page_index: int = 0,
                                target_width_mm: float = 0, target_height_mm: float = 0) -> dict:
    if not os.path.exists(corrected_pdf_path):
        return {"success": False, "error": "Corrected PDF not found"}

    doc = fitz.open(corrected_pdf_path)
    page_count = len(doc)

    if page_index >= page_count:
        doc.close()
        return {"success": False, "error": f"Page {page_index + 1} not found (PDF has {page_count} pages)"}

    results = []

    for pg_idx in range(page_count):
        page = doc[pg_idx]
        rect = page.rect

        render_dpi = 200
        mat = fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=True)
        img_rgba = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)
        alpha_ch = img_rgba[:, :, 3:4].astype(np.float32) / 255.0
        rgb_ch = img_rgba[:, :, :3].astype(np.float32)
        white_bg = np.full_like(rgb_ch, 255.0)
        img_np = (rgb_ch * alpha_ch + white_bg * (1.0 - alpha_ch)).astype(np.uint8)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        del pix, img_rgba

        if target_width_mm > 0 and target_height_mm > 0:
            total_mm_w = target_width_mm + (bleed_mm * 2)
            total_mm_h = target_height_mm + (bleed_mm * 2)
            dpi_w = (img_bgr.shape[1] / total_mm_w) * 25.4
            dpi_h = (img_bgr.shape[0] / total_mm_h) * 25.4
            display_dpi = min(dpi_w, dpi_h)
        else:
            display_dpi = render_dpi

        page_label = f"Page {pg_idx + 1} of {page_count}" if page_count > 1 else ""
        preview = draw_bleed_preview(img_bgr, bleed_mm, display_dpi,
                                      page_num=pg_idx + 1, page_label=page_label)
        del img_bgr

        if page_count == 1:
            out_path = output_png_path
        else:
            base, ext = os.path.splitext(output_png_path)
            out_path = f"{base}_page{pg_idx + 1}{ext}"

        cv2.imwrite(out_path, preview, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        del preview

        if target_width_mm > 0 and target_height_mm > 0:
            trim_w_mm = target_width_mm
            trim_h_mm = target_height_mm
            page_w_mm = trim_w_mm + (bleed_mm * 2)
            page_h_mm = trim_h_mm + (bleed_mm * 2)
        else:
            page_w_mm = rect.width * 25.4 / 72
            page_h_mm = rect.height * 25.4 / 72
            trim_w_mm = page_w_mm - (bleed_mm * 2)
            trim_h_mm = page_h_mm - (bleed_mm * 2)

        results.append({
            "page": pg_idx + 1,
            "previewPath": out_path,
            "totalSize_mm": [round(page_w_mm, 1), round(page_h_mm, 1)],
            "trimSize_mm": [round(trim_w_mm, 1), round(trim_h_mm, 1)],
            "bleed_mm": bleed_mm,
        })

    doc.close()

    return {
        "success": True,
        "pages": results,
        "pageCount": page_count,
    }


def generate_bleed_preview_image(corrected_img_path: str, output_png_path: str,
                                  bleed_mm: float = BLEED_MM,
                                  target_width_mm: float = 0, target_height_mm: float = 0) -> dict:
    img = cv2.imread(corrected_img_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return {"success": False, "error": "Could not read corrected image"}

    if len(img.shape) > 2 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    h, w = img.shape[:2]

    if target_width_mm > 0 and target_height_mm > 0:
        trim_w_mm = target_width_mm
        trim_h_mm = target_height_mm
        total_w_mm = trim_w_mm + (bleed_mm * 2)
        total_h_mm = trim_h_mm + (bleed_mm * 2)
        dpi_w = (w / total_w_mm) * 25.4
        dpi_h = (h / total_h_mm) * 25.4
        display_dpi = min(dpi_w, dpi_h)
    else:
        from PIL import Image as PILImage
        try:
            with PILImage.open(corrected_img_path) as pil_img:
                dpi_info = pil_img.info.get("dpi")
                display_dpi = float(max(dpi_info)) if dpi_info else 300.0
        except Exception:
            display_dpi = 300.0
        total_w_mm = _px_to_mm(w, display_dpi)
        total_h_mm = _px_to_mm(h, display_dpi)
        trim_w_mm = total_w_mm - (bleed_mm * 2)
        trim_h_mm = total_h_mm - (bleed_mm * 2)

    preview = draw_bleed_preview(img, bleed_mm, display_dpi)
    cv2.imwrite(output_png_path, preview, [cv2.IMWRITE_PNG_COMPRESSION, 6])

    return {
        "success": True,
        "pages": [{
            "page": 1,
            "previewPath": output_png_path,
            "totalSize_mm": [round(total_w_mm, 1), round(total_h_mm, 1)],
            "trimSize_mm": [round(trim_w_mm, 1), round(trim_h_mm, 1)],
            "bleed_mm": bleed_mm,
        }],
        "pageCount": 1,
    }


def _detect_proof_dpi(img_path: str, fallback: float = 300.0) -> float:
    try:
        from PIL import Image as PILImage
        with PILImage.open(img_path) as im:
            dpi_info = im.info.get("dpi")
            if dpi_info:
                return float(max(dpi_info[0], dpi_info[1]))
    except Exception:
        pass
    return float(fallback)


def generate_before_after_cut_proofs(
    src_path: str,
    before_path: str,
    after_path: str,
    bleed_mm: float = BLEED_MM,
    dpi: float | None = None,
) -> dict:
    """
    From a full-bleed proof raster, write:
      - before_path: full canvas with cut/crop box + bleed tint (what printer gets)
      - after_path: trimmed to finished size (what client receives after guillotine)
    """
    if not src_path or not os.path.exists(src_path):
        return {"success": False, "error": f"Source proof missing: {src_path}"}

    img = cv2.imread(src_path, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return {"success": False, "error": f"Could not read proof image: {src_path}"}

    dpi_f = float(dpi) if dpi and dpi > 0 else _detect_proof_dpi(src_path, 300.0)
    h, w = img.shape[:2]
    bleed_px = _mm_to_px(bleed_mm, dpi_f)
    bleed_px = max(1, min(bleed_px, w // 2 - 1, h // 2 - 1))

    before = draw_bleed_preview(
        img,
        bleed_mm,
        dpi_f,
        page_label="BEFORE CUT — includes bleed (outside red line is trimmed off)",
    )
    if not cv2.imwrite(before_path, before, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        return {"success": False, "error": f"Failed to write before-cut proof: {before_path}"}

    tl = bleed_px
    tt = bleed_px
    tr = w - bleed_px
    tb = h - bleed_px
    after = img[tt:tb, tl:tr].copy()
    if after.size == 0:
        return {"success": False, "error": "After-cut crop produced empty image"}

    # Small caption bar so the finished-size proof is self-explanatory when opened alone
    ah, aw = after.shape[:2]
    bar_h = max(28, int(dpi_f / 12))
    canvas = np.full((ah + bar_h, aw, 3), 40, dtype=np.uint8)
    canvas[0:ah, 0:aw] = after
    label = "AFTER CUT — finished size (what you receive)"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.4, dpi_f / 700)
    thick = max(1, int(dpi_f / 250))
    (tw, th), _ = cv2.getTextSize(label, font, scale, thick)
    tx = max(8, (aw - tw) // 2)
    ty = ah + (bar_h + th) // 2
    cv2.putText(canvas, label, (tx, ty), font, scale, (255, 255, 255), thick, cv2.LINE_AA)

    if not cv2.imwrite(after_path, canvas, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        return {"success": False, "error": f"Failed to write after-cut proof: {after_path}"}

    return {
        "success": True,
        "beforePath": before_path,
        "afterPath": after_path,
        "bleed_mm": bleed_mm,
        "bleed_px": bleed_px,
        "dpi": dpi_f,
    }


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(json.dumps({"success": False, "error": "Usage: bleed_preview.py <input> <output> <file_type> [bleed_mm]"}))
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    file_type = sys.argv[3].lower()
    bleed_mm = float(sys.argv[4]) if len(sys.argv) > 4 else BLEED_MM
    target_w = float(sys.argv[5]) if len(sys.argv) > 5 else 0
    target_h = float(sys.argv[6]) if len(sys.argv) > 6 else 0

    try:
        if file_type == "pdf":
            result = generate_bleed_preview_pdf(input_path, output_path, bleed_mm,
                                                 target_width_mm=target_w, target_height_mm=target_h)
        else:
            result = generate_bleed_preview_image(input_path, output_path, bleed_mm,
                                                   target_width_mm=target_w, target_height_mm=target_h)

        print(json.dumps(result))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
