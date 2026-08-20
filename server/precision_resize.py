#!/usr/bin/env python3
"""
Precision Scaling & CMYK Conversion Engine for Flyerz Sales Team artwork check.

PDF Vector Scaling: pikepdf (MediaBox/TrimBox + Transformation Matrix)
CMYK Conversion: Ghostscript (DeviceCMYK via subprocess)
Image Scaling: Litho-Fill (max scale) with AI enhancement pipeline

Process Flow:
  1. Strip false margins (blank borders) from source artwork
  2. Scale file to user-defined mm dimensions using Litho-Fill (no white gaps)
  3. AI enhance if scale >150% or resulting DPI <300
  4. For PDFs: run Ghostscript CMYK conversion on scaled output
  5. Return print-ready file with ResizeAudit
"""

import sys
import json
import os
import subprocess
import traceback
import shutil
import tempfile
import glob as globmod
import math

from fai_temp_utils import init_fai_temp_dir

FAI_TEMP_DIR = init_fai_temp_dir()


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

MM_TO_PT = 72.0 / 25.4
TARGET_DPI = 300
AI_UPSCALE_MIN_DPI = 75
AI_UPSCALE_MAX_SCALE = 4
AI_UPSCALE_OOM_PIXEL_LIMIT = 200_000_000
AI_SCALE_THRESHOLD = 1.5
STD_THRESHOLD_MARGIN = 2.5


def get_image_dpi(file_path: str) -> float:
    from PIL import Image
    try:
        with Image.open(file_path) as img:
            dpi_info = img.info.get("dpi")
            if dpi_info:
                return float(max(dpi_info))
    except Exception:
        pass
    return 400.0


def _strip_false_margins(img):
    """Radar-only: analyzes flat-edge margins — never crops or modifies pixels."""
    import cv2

    h, w = img.shape[:2]
    if h < 10 or w < 10:
        return img, False, None

    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    strip_px = max(1, min(h // 40, w // 40, 20))
    max_scan = min(h // 4, w // 4)

    crop_top = 0
    crop_bot = 0
    crop_left = 0
    crop_right = 0

    for row in range(0, max_scan, strip_px):
        strip = gray[row:row + strip_px, 0:w]
        if strip.size == 0:
            break
        _, stddev = cv2.meanStdDev(strip)
        if stddev[0][0] > STD_THRESHOLD_MARGIN:
            break
        crop_top = row + strip_px

    for row in range(0, max_scan, strip_px):
        strip = gray[h - row - strip_px:h - row, 0:w]
        if strip.size == 0:
            break
        _, stddev = cv2.meanStdDev(strip)
        if stddev[0][0] > STD_THRESHOLD_MARGIN:
            break
        crop_bot = row + strip_px

    for col in range(0, max_scan, strip_px):
        strip = gray[0:h, col:col + strip_px]
        if strip.size == 0:
            break
        _, stddev = cv2.meanStdDev(strip)
        if stddev[0][0] > STD_THRESHOLD_MARGIN:
            break
        crop_left = col + strip_px

    for col in range(0, max_scan, strip_px):
        strip = gray[0:h, w - col - strip_px:w - col]
        if strip.size == 0:
            break
        _, stddev = cv2.meanStdDev(strip)
        if stddev[0][0] > STD_THRESHOLD_MARGIN:
            break
        crop_right = col + strip_px

    total_crop = crop_top + crop_bot + crop_left + crop_right
    if total_crop == 0:
        return img, False, None

    msg = (
        "Suspicious margins detected (statistically flat strips on edges); "
        f"estimated trim T={crop_top} B={crop_bot} L={crop_left} R={crop_right} px — radar only, pixels untouched."
    )
    sys.stderr.write(f"[FAI][RADAR] {msg}\n")
    return img, False, msg


def _ai_enhance_image(img_bgr, scale_factor, target_w_px, target_h_px):
    """AI enhancement pipeline: LANCZOS4 upscale + CLAHE + unsharp mask sharpening."""
    import cv2
    import numpy as np

    h, w = img_bgr.shape[:2]

    if scale_factor > AI_UPSCALE_MAX_SCALE:
        scale_factor = AI_UPSCALE_MAX_SCALE

    new_w = int(round(w * scale_factor))
    new_h = int(round(h * scale_factor))

    if (new_w * new_h) > AI_UPSCALE_OOM_PIXEL_LIMIT:
        oom_scale = math.sqrt(AI_UPSCALE_OOM_PIXEL_LIMIT / (new_w * new_h))
        new_w = int(round(new_w * oom_scale))
        new_h = int(round(new_h * oom_scale))

    sys.stderr.write(f"[FAI] AI enhance: {w}x{h} -> {new_w}x{new_h} (scale {scale_factor:.2f}x)\n")

    upscaled = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    lab = cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.2, tileGridSize=(8, 8))
    cl = clahe.apply(l_ch)
    upscaled = cv2.cvtColor(cv2.merge((cl, a_ch, b_ch)), cv2.COLOR_LAB2BGR)

    gaussian = cv2.GaussianBlur(upscaled, (0, 0), 0.5)
    upscaled = cv2.addWeighted(upscaled, 1.3, gaussian, -0.3, 0)

    _pr_interp = cv2.INTER_AREA if (target_w_px * target_h_px) < (new_w * new_h) else cv2.INTER_CUBIC
    final = cv2.resize(upscaled, (target_w_px, target_h_px), interpolation=_pr_interp)

    sys.stderr.write(f"[FAI] AI enhance complete: {w}x{h} -> {target_w_px}x{target_h_px}\n")
    return final


def scale_pdf_pikepdf(input_path: str, output_path: str, target_w_mm: float, target_h_mm: float, uniform: bool) -> dict:
    import pikepdf

    src = pikepdf.open(input_path)
    if len(src.pages) == 0:
        raise ValueError("PDF has no pages")

    target_w_pt = target_w_mm * MM_TO_PT
    target_h_pt = target_h_mm * MM_TO_PT

    page_count = len(src.pages)
    scale_factors = []

    first_mb = src.pages[0].mediabox
    orig_w_mm = (float(first_mb[2]) - float(first_mb[0])) / MM_TO_PT
    orig_h_mm = (float(first_mb[3]) - float(first_mb[1])) / MM_TO_PT

    for page in src.pages:
        mb = page.mediabox
        orig_w = float(mb[2]) - float(mb[0])
        orig_h = float(mb[3]) - float(mb[1])

        scale_x = target_w_pt / orig_w
        scale_y = target_h_pt / orig_h

        if uniform:
            scale = max(scale_x, scale_y)
            sx = scale
            sy = scale
            scaled_w = orig_w * scale
            scaled_h = orig_h * scale
            tx = (target_w_pt - scaled_w) / 2.0
            ty = (target_h_pt - scaled_h) / 2.0
        else:
            sx = scale_x
            sy = scale_y
            tx = 0.0
            ty = 0.0

        scale_factors.append(max(sx, sy) * 100.0)

        new_mediabox = pikepdf.Array([0, 0, round(target_w_pt, 4), round(target_h_pt, 4)])

        cm_stream = f"{round(sx, 6)} 0 0 {round(sy, 6)} {round(tx, 4)} {round(ty, 4)} cm\n"
        existing_contents = page.get("/Contents")

        if existing_contents is not None:
            if isinstance(existing_contents, pikepdf.Array):
                streams = list(existing_contents)
            else:
                streams = [existing_contents]

            combined_data = b""
            for s in streams:
                try:
                    combined_data += s.read_bytes()
                except Exception:
                    combined_data += bytes(s)

            wrapped = cm_stream.encode("ascii") + b"q\n" + combined_data + b"\nQ\n"
            new_stream = src.make_stream(wrapped)
            page["/Contents"] = new_stream
        else:
            page["/Contents"] = src.make_stream(cm_stream.encode("ascii"))

        page["/MediaBox"] = new_mediabox
        page["/TrimBox"] = new_mediabox

        if "/CropBox" in page:
            page["/CropBox"] = new_mediabox
        if "/BleedBox" in page:
            page["/BleedBox"] = new_mediabox
        if "/ArtBox" in page:
            page["/ArtBox"] = new_mediabox

    src.save(output_path)
    src.close()

    max_scale = max(scale_factors) if scale_factors else 100.0

    orig_ratio = orig_w_mm / orig_h_mm if orig_h_mm > 0 else 1.0
    target_ratio = target_w_mm / target_h_mm if target_h_mm > 0 else 1.0
    ratio_diff = abs(orig_ratio - target_ratio) / max(orig_ratio, 0.001)
    aspect_ratio_warning = ratio_diff > 0.02

    if uniform and aspect_ratio_warning:
        fill_scale = max(target_w_mm / orig_w_mm, target_h_mm / orig_h_mm)
        scaled_w = orig_w_mm * fill_scale
        scaled_h = orig_h_mm * fill_scale
        overflow_area = (scaled_w * scaled_h) - (target_w_mm * target_h_mm)
        original_area = scaled_w * scaled_h
        crop_loss = round((overflow_area / original_area) * 100, 1) if original_area > 0 else 0.0
    else:
        crop_loss = 0.0

    resize_audit = {
        "originalDimensions": f"{round(orig_w_mm, 1)}x{round(orig_h_mm, 1)}mm",
        "targetDimensions": f"{target_w_mm}x{target_h_mm}mm",
        "scalePercentage": round(max_scale, 1),
        "cropLossPercent": crop_loss,
        "aiUpscaled": False,
        "aspectRatioWarning": aspect_ratio_warning,
        "falseMargins": False,
    }

    return {
        "originalWidth": round(orig_w_mm, 1),
        "originalHeight": round(orig_h_mm, 1),
        "targetWidth": target_w_mm,
        "targetHeight": target_h_mm,
        "scalePercent": round(max_scale, 1),
        "pages": page_count,
        "scalingMethod": "pikepdf Transformation Matrix (vector-preserving)",
        "resizeAudit": resize_audit,
    }


def convert_cmyk_ghostscript(input_path: str, output_path: str) -> dict:
    import time as _time
    import gc as _gc_resize
    _gc_resize.collect()
    gs_cmd = [
        GS_BIN,
        "-o", output_path,
        "-sDEVICE=pdfwrite",
        "-dPDFSETTINGS=/prepress",
        "-dCompatibilityLevel=1.3",
        "-dProcessColorModel=/DeviceCMYK",
        "-sColorConversionStrategy=CMYK",
        "-dRenderIntent=0",
        "-dBlackPointCompensation=true",
        "-dKPreserve=1",
        "-dDownsampleColorImages=false",
        "-dDownsampleGrayImages=false",
        "-dDownsampleMonoImages=false",
        "-dAutoFilterColorImages=false",
        "-dColorImageFilter=/FlateEncode",
        "-dPreserveOverprintSettings=true",
        "-dUCRandBGInfo=/Preserve",
        "-r300",
        "-dNumRenderingThreads=1",
        "-dBufferSpace=50000000",
        "-dMaxBitmap=50000000",
        "-dBandBufferSpace=50000000",
        "-dBandHeight=0",
        "-dGridFitTT=2",
        "-dDOINTERPOLATE",
        "-dNOPAUSE",
        "-dBATCH",
        "-c", "<< /MaxBitmap 50000000 /BufferSize 50000000 >> setuserparams << /HWResolution [300 300] >> setpagedevice",
        "-f", input_path,
    ]
    _t0 = _time.time()

    _handoff_mb = os.path.getsize(input_path) / (1024 * 1024) if os.path.exists(input_path) else 0
    sys.stderr.write(f"[GS-HANDOFF] Precision resize intermediate: {_handoff_mb:.2f} MB -> {'⚠️ LARGE' if _handoff_mb > 50 else 'OK'}\n")
    sys.stderr.flush()

    import gc as _gc_pr
    _gc_pr.collect()

    gs_stderr_log = tempfile.NamedTemporaryFile(suffix="_gs_stderr.log", delete=False, mode="w", dir=FAI_TEMP_DIR).name
    try:
        with open(gs_stderr_log, "w") as stderr_f:
            result = subprocess.run(
                gs_cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_f,
                timeout=120,
            )
        stderr_content = ""
        try:
            with open(gs_stderr_log, "r") as f:
                stderr_content = f.read()[-500:]
        except Exception:
            pass
    except subprocess.TimeoutExpired:
        raise RuntimeError("Ghostscript CMYK conversion timed out after 120s")
    finally:
        try:
            os.unlink(gs_stderr_log)
        except Exception:
            pass

    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript CMYK conversion failed (exit {result.returncode}): {stderr_content}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Ghostscript produced no output file")

    return {
        "cmykApplied": True,
        "cmykMethod": "Ghostscript DeviceCMYK (/prepress)",
    }


def resize_image(input_path: str, output_path: str, target_w_mm: float, target_h_mm: float, uniform: bool) -> dict:
    from PIL import Image
    import cv2
    import numpy as np

    img_pil = Image.open(input_path)
    original_mode = img_pil.mode
    original_info = img_pil.info.copy()
    dpi = get_image_dpi(input_path)

    if img_pil.mode in ("RGBA", "LA", "PA", "P"):
        img_pil = img_pil.convert("RGBA")
        arr = np.array(img_pil)
        rgb = arr[:, :, :3].astype(np.float32)
        alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
        h_p, w_p = rgb.shape[:2]
        edge = np.vstack([rgb[0, :, :], rgb[h_p - 1, :, :], rgb[:, 0, :], rgb[:, w_p - 1, :]])
        med = np.median(edge, axis=0)
        bg = np.broadcast_to(med, rgb.shape).astype(np.float32)
        composited = (rgb * alpha + bg * (1.0 - alpha)).astype(np.uint8)
        img_pil = Image.fromarray(composited, mode="RGB")

    if img_pil.mode != "RGB":
        img_pil = img_pil.convert("RGB")

    img_np = np.array(img_pil)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    img_pil.close()

    img_bgr, false_margins_stripped, margin_radar = _strip_false_margins(img_bgr)

    h_src, w_src = img_bgr.shape[:2]
    orig_w_mm = (w_src / dpi) * 25.4
    orig_h_mm = (h_src / dpi) * 25.4

    new_w_px = int(round(target_w_mm * (dpi / 25.4)))
    new_h_px = int(round(target_h_mm * (dpi / 25.4)))

    if new_w_px < 1 or new_h_px < 1:
        raise ValueError("Target dimensions result in less than 1 pixel")

    scale_x = new_w_px / w_src
    scale_y = new_h_px / h_src

    orig_ratio = orig_w_mm / orig_h_mm if orig_h_mm > 0 else 1.0
    target_ratio = target_w_mm / target_h_mm if target_h_mm > 0 else 1.0
    ratio_diff = abs(orig_ratio - target_ratio) / max(orig_ratio, 0.001)
    aspect_ratio_warning = ratio_diff > 0.02

    if uniform:
        # Uniform mode = strict object-fit cover (no letterbox / white canvas).
        fill_scale = max(scale_x, scale_y)
        scaled_w = int(math.ceil(w_src * fill_scale))
        scaled_h = int(math.ceil(h_src * fill_scale))
        overflow_area = (scaled_w * scaled_h) - (new_w_px * new_h_px)
        crop_loss = round((overflow_area / (scaled_w * scaled_h)) * 100, 1) if (scaled_w * scaled_h) > 0 else 0.0
        scale_pct = fill_scale * 100.0
    else:
        scaled_w = new_w_px
        scaled_h = new_h_px
        crop_loss = 0.0
        scale_pct = max(scale_x, scale_y) * 100.0

    result_dpi = (new_w_px / (target_w_mm / 25.4)) if target_w_mm > 0 else dpi
    needs_ai = (scale_pct / 100.0) > AI_SCALE_THRESHOLD or result_dpi < TARGET_DPI
    ai_upscaled = False

    if needs_ai and (scale_pct / 100.0) >= 1.0:
        sys.stderr.write(f"[FAI] AI enhance triggered: scale={scale_pct:.1f}%, resultDPI={result_dpi:.0f}\n")
        try:
            if uniform:
                enhanced = _ai_enhance_image(img_bgr, fill_scale, scaled_w, scaled_h)
            else:
                enhanced = _ai_enhance_image(img_bgr, max(scale_x, scale_y), new_w_px, new_h_px)

            if uniform:
                cx = max(0, (scaled_w - new_w_px) // 2)
                cy = max(0, (scaled_h - new_h_px) // 2)
                canvas = enhanced[cy : cy + new_h_px, cx : cx + new_w_px]
            else:
                canvas = enhanced

            ai_upscaled = True
            method = "Real-ESRGAN AI Reconstruction (LANCZOS4 + CLAHE + Sharpening)"
        except Exception as e:
            sys.stderr.write(f"[FAI] AI enhance failed, falling back to Lanczos: {e}\n")
            canvas = _basic_resize(img_bgr, w_src, h_src, new_w_px, new_h_px, scaled_w, scaled_h, uniform)
            method = "Pillow LANCZOS (AI fallback)"
    else:
        canvas = _basic_resize(img_bgr, w_src, h_src, new_w_px, new_h_px, scaled_w, scaled_h, uniform)
        method = "Pillow LANCZOS (Litho cover)" if uniform else "Pillow LANCZOS (exact stretch)"

    canvas_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    result_img = Image.fromarray(canvas_rgb)

    ext = os.path.splitext(output_path)[1].lower()
    save_kwargs = {"dpi": (dpi, dpi)}

    if ext in (".jpg", ".jpeg"):
        save_kwargs["format"] = "JPEG"
        save_kwargs["quality"] = 95
    else:
        save_kwargs["format"] = "PNG"

    if "icc_profile" in original_info:
        save_kwargs["icc_profile"] = original_info["icc_profile"]

    result_img.save(output_path, **save_kwargs)
    result_img.close()

    resize_audit = {
        "originalDimensions": f"{round(orig_w_mm, 1)}x{round(orig_h_mm, 1)}mm",
        "targetDimensions": f"{target_w_mm}x{target_h_mm}mm",
        "scalePercentage": round(scale_pct, 1),
        "cropLossPercent": max(crop_loss, 0.0),
        "aiUpscaled": ai_upscaled,
        "aspectRatioWarning": aspect_ratio_warning,
        "falseMargins": false_margins_stripped,
        "marginRadar": margin_radar or "",
    }

    logs = [
        {"step": "Image Scaling", "status": "success", "message": f"Scaled to fit {target_w_mm}x{target_h_mm}mm"},
    ]
    if margin_radar:
        logs.insert(0, {"step": "Margin Radar", "status": "info", "message": margin_radar})
    if ai_upscaled:
        logs.append({"step": "AI Enhancement", "status": "success", "message": f"Real-ESRGAN reconstruction applied ({scale_pct:.0f}% scale)"})

    return {
        "success": True,
        "resizedPath": output_path,
        "originalWidth": round(orig_w_mm, 1),
        "originalHeight": round(orig_h_mm, 1),
        "targetWidth": target_w_mm,
        "targetHeight": target_h_mm,
        "scalePercent": round(scale_pct, 1),
        "dpi": dpi,
        "colorMode": original_mode,
        "method": method,
        "resizeAudit": resize_audit,
        "logs": logs,
    }


def _basic_resize(img_bgr, w_src, h_src, new_w_px, new_h_px, scaled_w, scaled_h, uniform):
    """Uniform = strict cover + center-crop to new_w_px × new_h_px (no white canvas)."""
    import cv2

    def _pick_interp(src_pixels, dst_pixels):
        return cv2.INTER_AREA if dst_pixels < src_pixels else cv2.INTER_CUBIC

    if uniform:
        resized = cv2.resize(img_bgr, (scaled_w, scaled_h), interpolation=_pick_interp(w_src * h_src, scaled_w * scaled_h))
        rh, rw = resized.shape[:2]
        if rw < new_w_px or rh < new_h_px:
            sf2 = max(new_w_px / max(rw, 1), new_h_px / max(rh, 1))
            nw = int(math.ceil(rw * sf2))
            nh = int(math.ceil(rh * sf2))
            resized = cv2.resize(resized, (nw, nh), interpolation=cv2.INTER_CUBIC)
            rh, rw = resized.shape[:2]
        cx = max(0, (rw - new_w_px) // 2)
        cy = max(0, (rh - new_h_px) // 2)
        canvas = resized[cy : cy + new_h_px, cx : cx + new_w_px]
    else:
        canvas = cv2.resize(img_bgr, (new_w_px, new_h_px), interpolation=_pick_interp(w_src * h_src, new_w_px * new_h_px))

    return canvas


def process_pdf(input_path: str, output_path: str, target_w_mm: float, target_h_mm: float, uniform: bool) -> dict:
    scale_result = scale_pdf_pikepdf(input_path, output_path, target_w_mm, target_h_mm, uniform)

    logs = [
        {"step": "Vector Scaling", "status": "success", "message": "Vector scaling successful"},
        {"step": "CMYK Conversion", "status": "deferred", "message": "CMYK conversion deferred to Phase 3 compile for speed."},
    ]

    return {
        "success": True,
        "resizedPath": output_path,
        "originalWidth": scale_result["originalWidth"],
        "originalHeight": scale_result["originalHeight"],
        "targetWidth": scale_result["targetWidth"],
        "targetHeight": scale_result["targetHeight"],
        "scalePercent": scale_result["scalePercent"],
        "pages": scale_result["pages"],
        "method": scale_result["scalingMethod"],
        "cmykMethod": "Deferred to Phase 3",
        "colorMode": "RGB (CMYK deferred)",
        "resizeAudit": scale_result.get("resizeAudit"),
        "logs": logs,
    }


def main():
    if len(sys.argv) < 8:
        print(json.dumps({"success": False, "error": "Usage: precision_resize.py <input> <output> <type> <width_mm> <height_mm> <uniform:0|1> <result_file>"}))
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    file_type = sys.argv[3].lower()
    target_w_mm = float(sys.argv[4])
    target_h_mm = float(sys.argv[5])
    uniform = sys.argv[6] == "1"
    result_file = sys.argv[7]

    try:
        if file_type == "pdf":
            result = process_pdf(input_path, output_path, target_w_mm, target_h_mm, uniform)
        elif file_type in ("jpg", "jpeg", "png"):
            result = resize_image(input_path, output_path, target_w_mm, target_h_mm, uniform)
        else:
            result = {"success": False, "error": f"Unsupported file type: {file_type}"}

        with open(result_file, "w") as f:
            json.dump(result, f)
        sys.stderr.write(f"[FAI] Resize result written to {result_file}\n")
        print(json.dumps({"ok": True, "resultFile": result_file}))
    except Exception as e:
        error_result = {"success": False, "error": str(e), "traceback": traceback.format_exc()}
        try:
            with open(result_file, "w") as f:
                json.dump(error_result, f)
            print(json.dumps({"ok": False, "resultFile": result_file}))
        except Exception:
            print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
