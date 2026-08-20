#!/usr/bin/env python3
"""
[FAI] Manual Crop & Downscale — High Quality
Flyerz.co.za Artwork Intelligence

Performs lossless crop and high-quality downscale on PDF/JPG/PNG files.
No automatic modifications to background, bleed, or layout.

Usage:
  python3 manual_crop.py <input> <output> <file_type> <mode> [args...]

Modes:
  preview <input> <output> <file_type> preview
    -> Returns JSON with page dimensions for crop selection

  crop <input> <output> <file_type> crop <x> <y> <width> <height> <scale_percent>
    -> Crops to (x,y,w,h) then scales to scale_percent%
"""

import sys
import os
import json
import shutil
import numpy as np

MIN_SCALE = 10
MAX_SCALE = 100
PREVIEW_MAX_PX = 1200


def find_gs_binary():
    gs = shutil.which("gs")
    if gs:
        return gs
    import glob
    matches = glob.glob("/nix/store/*/bin/gs")
    if matches:
        return matches[0]
    return "gs"


def get_image_info(input_path, file_type):
    """Get dimensions and page count for crop UI."""
    if file_type == "pdf":
        import fitz
        doc = fitz.open(input_path)
        pages = []
        for i, page in enumerate(doc):
            rect = page.rect
            pages.append({
                "page": i + 1,
                "width_pt": round(rect.width, 1),
                "height_pt": round(rect.height, 1),
                "width_mm": round(rect.width * 25.4 / 72, 1),
                "height_mm": round(rect.height * 25.4 / 72, 1),
            })
        doc.close()
        return {"success": True, "pages": pages, "pageCount": len(pages)}
    else:
        import cv2
        img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return {"success": False, "error": "Failed to read image file"}
        h, w = img.shape[:2]
        return {
            "success": True,
            "pages": [{
                "page": 1,
                "width_px": w,
                "height_px": h,
            }],
            "pageCount": 1,
        }


def generate_preview(input_path, output_path, file_type):
    """Generate a preview image for visual crop selection."""
    if file_type == "pdf":
        import fitz
        from PIL import Image as PILImage
        doc = fitz.open(input_path)
        page = doc[0]
        rect = page.rect
        scale = min(PREVIEW_MAX_PX / rect.width, PREVIEW_MAX_PX / rect.height, 2.0)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=True)
        fg = PILImage.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        white_bg = PILImage.new("RGBA", (pix.width, pix.height), (255, 255, 255, 255))
        white_bg.paste(fg, mask=fg.split()[3])
        white_bg.convert("RGB").save(output_path)
        doc.close()
        return {
            "success": True,
            "previewPath": output_path,
            "previewWidth": pix.width,
            "previewHeight": pix.height,
            "sourceWidth": round(rect.width, 1),
            "sourceHeight": round(rect.height, 1),
            "scale": round(scale, 4),
        }
    else:
        import cv2
        img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return {"success": False, "error": "Failed to read image"}
        h, w = img.shape[:2]
        scale = min(PREVIEW_MAX_PX / w, PREVIEW_MAX_PX / h, 1.0)
        if scale < 1.0:
            new_w = int(round(w * scale))
            new_h = int(round(h * scale))
            preview = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            preview = img
            scale = 1.0
        cv2.imwrite(output_path, preview, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        return {
            "success": True,
            "previewPath": output_path,
            "previewWidth": preview.shape[1],
            "previewHeight": preview.shape[0],
            "sourceWidth": w,
            "sourceHeight": h,
            "scale": round(scale, 4),
        }


def crop_and_scale(input_path, output_path, file_type, x, y, width, height, scale_percent):
    """
    Crop to (x, y, width, height) in source coordinates, then scale to scale_percent%.
    Coordinates are in pt for PDF, px for images.
    """
    scale_percent = max(MIN_SCALE, min(MAX_SCALE, scale_percent))

    if file_type == "pdf":
        return _crop_pdf(input_path, output_path, x, y, width, height, scale_percent)
    else:
        return _crop_image(input_path, output_path, x, y, width, height, scale_percent)


def _crop_pdf(input_path, output_path, x_pt, y_pt, w_pt, h_pt, scale_percent):
    """Crop PDF page to rectangle, then optionally downscale."""
    import fitz

    render_dpi = 300
    src_doc = fitz.open(input_path)
    page_count = len(src_doc)
    out_doc = fitz.open()
    pages_processed = []

    for i in range(page_count):
        page = src_doc[i]
        orig_rect = page.rect

        crop_x = max(0, min(x_pt, orig_rect.width))
        crop_y = max(0, min(y_pt, orig_rect.height))
        crop_w = min(w_pt, orig_rect.width - crop_x)
        crop_h = min(h_pt, orig_rect.height - crop_y)

        if crop_w <= 0 or crop_h <= 0:
            pages_processed.append({"page": i + 1, "skipped": True, "reason": "Invalid crop region"})
            continue

        crop_rect = fitz.Rect(crop_x, crop_y, crop_x + crop_w, crop_y + crop_h)

        scale_render = render_dpi / 72.0
        mat = fitz.Matrix(scale_render, scale_render)
        pix = page.get_pixmap(matrix=mat, clip=crop_rect, colorspace=fitz.csRGB, alpha=False)

        import cv2
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        del pix

        cropped_h, cropped_w = img_bgr.shape[:2]

        if scale_percent < 100:
            factor = scale_percent / 100.0
            new_w = max(1, int(round(cropped_w * factor)))
            new_h = max(1, int(round(cropped_h * factor)))
            img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            sys.stderr.write(
                f"[FAI] Page {i+1}: Downscaled {cropped_w}x{cropped_h} -> {new_w}x{new_h} ({scale_percent}%)\n"
            )

        final_h, final_w = img_bgr.shape[:2]
        final_w_pt = final_w * 72.0 / render_dpi
        final_h_pt = final_h * 72.0 / render_dpi

        import tempfile
        tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_png_path = tmp_png.name
        tmp_png.close()
        import cv2 as cv2_write
        cv2_write.imwrite(tmp_png_path, img_bgr, [cv2_write.IMWRITE_PNG_COMPRESSION, 1])

        new_page = out_doc.new_page(width=final_w_pt, height=final_h_pt)
        new_page.insert_image(
            fitz.Rect(0, 0, final_w_pt, final_h_pt),
            filename=tmp_png_path,
        )
        os.unlink(tmp_png_path)

        pages_processed.append({
            "page": i + 1,
            "original_pt": (round(orig_rect.width, 1), round(orig_rect.height, 1)),
            "crop_rect_pt": (round(crop_x, 1), round(crop_y, 1), round(crop_w, 1), round(crop_h, 1)),
            "cropped_px": (cropped_w, cropped_h),
            "scale_percent": scale_percent,
            "final_px": (final_w, final_h),
            "final_pt": (round(final_w_pt, 1), round(final_h_pt, 1)),
            "final_mm": (round(final_w_pt * 25.4 / 72, 1), round(final_h_pt * 25.4 / 72, 1)),
        })

    src_doc.close()
    out_doc.save(output_path, deflate=True, garbage=4)
    out_doc.close()

    total_size = os.path.getsize(output_path)
    return {
        "success": True,
        "outputPath": output_path,
        "pages": pages_processed,
        "outputSize": total_size,
        "scalePercent": scale_percent,
    }


def _crop_image(input_path, output_path, x_px, y_px, w_px, h_px, scale_percent):
    """Crop image to rectangle, then optionally downscale."""
    import cv2

    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return {"success": False, "error": "Failed to read image file"}

    h, w = img.shape[:2]
    x1 = max(0, int(round(x_px)))
    y1 = max(0, int(round(y_px)))
    x2 = min(w, x1 + max(1, int(round(w_px))))
    y2 = min(h, y1 + max(1, int(round(h_px))))

    if x2 <= x1 or y2 <= y1:
        return {"success": False, "error": "Invalid crop region (zero area)"}

    cropped = img[y1:y2, x1:x2].copy()
    cropped_h, cropped_w = cropped.shape[:2]

    if scale_percent < 100:
        factor = scale_percent / 100.0
        new_w = max(1, int(round(cropped_w * factor)))
        new_h = max(1, int(round(cropped_h * factor)))
        cropped = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_AREA)
        sys.stderr.write(f"[FAI] Downscaled {cropped_w}x{cropped_h} -> {new_w}x{new_h} ({scale_percent}%)\n")

    final_h, final_w = cropped.shape[:2]

    ext = os.path.splitext(output_path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        cv2.imwrite(output_path, cropped, [cv2.IMWRITE_JPEG_QUALITY, 98])
    else:
        cv2.imwrite(output_path, cropped, [cv2.IMWRITE_PNG_COMPRESSION, 3])

    return {
        "success": True,
        "outputPath": output_path,
        "originalSize": (w, h),
        "cropRegion": (x1, y1, x2 - x1, y2 - y1),
        "croppedSize": (cropped_w, cropped_h),
        "scalePercent": scale_percent,
        "finalSize": (final_w, final_h),
        "outputFileSize": os.path.getsize(output_path),
    }


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(json.dumps({"success": False, "error": "Usage: manual_crop.py <input> <output> <file_type> <mode> [args...]"}))
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    file_type = sys.argv[3].lower()
    mode = sys.argv[4].lower()

    if file_type == "jpeg":
        file_type = "jpg"

    try:
        if mode == "preview":
            info = get_image_info(input_path, file_type)
            preview_result = generate_preview(input_path, output_path, file_type)
            result = {**info, **preview_result}
            print(json.dumps(result))

        elif mode == "crop":
            if len(sys.argv) < 10:
                print(json.dumps({"success": False, "error": "crop mode requires: x y width height scale_percent"}))
                sys.exit(1)

            x = float(sys.argv[5])
            y = float(sys.argv[6])
            width = float(sys.argv[7])
            height = float(sys.argv[8])
            scale_pct = float(sys.argv[9])

            result = crop_and_scale(input_path, output_path, file_type, x, y, width, height, scale_pct)
            print(json.dumps(result))

        else:
            print(json.dumps({"success": False, "error": f"Unknown mode: {mode}"}))
            sys.exit(1)

    except Exception as e:
        sys.stderr.write(f"[FAI] manual_crop.py error: {e}\n")
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
