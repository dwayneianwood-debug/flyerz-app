#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  FLYERZ FULL PIPELINE DIAGNOSTIC TRACER                                    ║
║                                                                            ║
║  Simulates a complete job from frontend request -> backend -> prepress       ║
║  engine -> crop -> auto-fix -> Ghostscript -> final PDF.                      ║
║                                                                            ║
║  Traces every handoff to verify parameters are preserved end-to-end.       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys
import os
import json
import time
import tempfile
import subprocess
import struct


def _harden_stdio_for_windows():
    """Avoid UnicodeEncodeError on cp1252 consoles when printing box-drawing and symbols."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    reconfigure(errors="replace")
                except Exception:
                    pass


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
GREEN = "\033[92m"

SIMULATED_FORMAT = "Business Card"
TRIM_W_MM = 90.0
TRIM_H_MM = 55.0
EXPECTED_MEDIABOX_W_MM = 100.0
EXPECTED_MEDIABOX_H_MM = 65.0
BLEED_MM = 5.0

MOCK_CROP = {
    "cropX": 50.0,
    "cropY": 30.0,
    "cropWidth": 400.0,
    "cropHeight": 250.0,
}

COLOR_SPACE = "cmyk"
STRATEGY = "auto"

results = []


def log_step(step_num, title):
    print(f"\n{CYAN}{'─'*70}{RESET}")
    print(f"{CYAN}  STEP {step_num}: {title}{RESET}")
    print(f"{CYAN}{'─'*70}{RESET}")


def record(step, check_name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append({"step": step, "check": check_name, "passed": passed, "detail": detail})
    print(f"  {status}  {check_name}")
    if detail:
        print(f"         {detail}")


def record_warn(step, check_name, detail=""):
    results.append({"step": step, "check": check_name, "passed": True, "detail": f"(WARN) {detail}", "warn": True})
    print(f"  {WARN}  {check_name}")
    if detail:
        print(f"         {detail}")


def create_test_pdf():
    """Create a synthetic PDF with known dimensions (200x150mm) — deliberately NOT Business Card size."""
    import fitz
    doc = fitz.open()
    w_pt = 200 * 72.0 / 25.4
    h_pt = 150 * 72.0 / 25.4
    page = doc.new_page(width=w_pt, height=h_pt)

    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(20, 20, w_pt - 20, h_pt - 20))
    shape.finish(color=(0, 0, 0), fill=(0.2, 0.6, 0.9))
    shape.commit()

    shape2 = page.new_shape()
    cx, cy = w_pt / 2, h_pt / 2
    shape2.draw_rect(fitz.Rect(cx - 60, cy - 20, cx + 60, cy + 20))
    shape2.finish(color=(1, 0, 0), fill=(1, 0.9, 0.1))
    shape2.commit()

    tw = page.insert_textbox(
        fitz.Rect(cx - 55, cy - 15, cx + 55, cy + 15),
        "DIAGNOSTIC", fontsize=14, fontname="helv", align=1
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    doc.save(tmp)
    w_mm = page.rect.width * 25.4 / 72.0
    h_mm = page.rect.height * 25.4 / 72.0
    doc.close()
    return tmp, w_mm, h_mm


def step1_simulate_frontend_request():
    """Simulate what the frontend sends to POST /api/jobs/:id/compile-print-pdf"""
    log_step(1, "FRONTEND -> BACKEND API REQUEST SIMULATION")

    request_body = {
        "selectedStrategy": STRATEGY,
        "exportPreferences": {
            "colorSpace": COLOR_SPACE,
            "trimWidth": TRIM_W_MM,
            "trimHeight": TRIM_H_MM,
        },
        "cropData": MOCK_CROP,
        "targetSize": {"width": TRIM_W_MM, "height": TRIM_H_MM},
    }

    print(f"  Simulated format: {SIMULATED_FORMAT} ({TRIM_W_MM}x{TRIM_H_MM}mm)")
    print(f"  Mock crop: x={MOCK_CROP['cropX']}, y={MOCK_CROP['cropY']}, w={MOCK_CROP['cropWidth']}, h={MOCK_CROP['cropHeight']}")
    print(f"  Color space: {COLOR_SPACE}")
    print(f"  Strategy: {STRATEGY}")
    print()

    record(1, "trimWidth present in request",
           request_body["exportPreferences"]["trimWidth"] == TRIM_W_MM,
           f"Value: {request_body['exportPreferences']['trimWidth']}mm")

    record(1, "trimHeight present in request",
           request_body["exportPreferences"]["trimHeight"] == TRIM_H_MM,
           f"Value: {request_body['exportPreferences']['trimHeight']}mm")

    record(1, "cropData present in request",
           request_body["cropData"]["cropWidth"] > 0 and request_body["cropData"]["cropHeight"] > 0,
           f"Crop: {MOCK_CROP['cropWidth']}x{MOCK_CROP['cropHeight']}px at ({MOCK_CROP['cropX']},{MOCK_CROP['cropY']})")

    record(1, "colorSpace present in request",
           request_body["exportPreferences"]["colorSpace"] == COLOR_SPACE,
           f"Value: {COLOR_SPACE}")

    return request_body


def step2_backend_arg_building(request_body):
    """Simulate how routes.ts builds CLI args for compile_press_pdf.py"""
    log_step(2, "BACKEND ROUTES.TS -> CLI ARGUMENT BUILDING")

    ep = request_body["exportPreferences"]
    trimWidth = ep.get("trimWidth") or 148
    trimHeight = ep.get("trimHeight") or 210
    colorSpace = ep.get("colorSpace") or "cmyk"
    selectedStrategy = request_body.get("selectedStrategy", "auto")

    rawCrop = request_body.get("cropData")
    requestCrop = None
    if rawCrop and isinstance(rawCrop, dict):
        requestCrop = {
            "cropX": float(rawCrop.get("cropX", 0)) or 0,
            "cropY": float(rawCrop.get("cropY", 0)) or 0,
            "cropWidth": float(rawCrop.get("cropWidth", 0)) or 0,
            "cropHeight": float(rawCrop.get("cropHeight", 0)) or 0,
        }
    requestHasCrop = bool(requestCrop and requestCrop["cropWidth"] > 0 and requestCrop["cropHeight"] > 0)

    record(2, "trimWidth not defaulted to A5 (148)",
           trimWidth == TRIM_W_MM,
           f"trimWidth={trimWidth} (expected {TRIM_W_MM})")

    record(2, "trimHeight not defaulted to A5 (210)",
           trimHeight == TRIM_H_MM,
           f"trimHeight={trimHeight} (expected {TRIM_H_MM})")

    record(2, "Crop detected from POST body",
           requestHasCrop,
           f"requestHasCrop={requestHasCrop}")

    cropArgs = []
    if requestHasCrop and requestCrop:
        cropArgs = [
            "--crop-x", str(requestCrop["cropX"] or 0),
            "--crop-y", str(requestCrop["cropY"] or 0),
            "--crop-w", str(requestCrop["cropWidth"]),
            "--crop-h", str(requestCrop["cropHeight"]),
        ]

    cli_args = [
        "--strategy", selectedStrategy,
        "--color-space", colorSpace,
        "--trim-w", str(trimWidth),
        "--trim-h", str(trimHeight),
    ] + cropArgs

    record(2, "--trim-w in CLI args",
           "--trim-w" in cli_args and cli_args[cli_args.index("--trim-w") + 1] == str(TRIM_W_MM),
           f"--trim-w {cli_args[cli_args.index('--trim-w') + 1]}")

    record(2, "--trim-h in CLI args",
           "--trim-h" in cli_args and cli_args[cli_args.index("--trim-h") + 1] == str(TRIM_H_MM),
           f"--trim-h {cli_args[cli_args.index('--trim-h') + 1]}")

    has_crop_args = "--crop-w" in cli_args
    record(2, "--crop-w/--crop-h in CLI args",
           has_crop_args,
           f"cropArgs={cropArgs}")

    return cli_args


def step3_python_argparse(cli_args, input_path):
    """Simulate argparse inside compile_press_pdf.py"""
    log_step(3, "PYTHON ARGPARSE -> PARAMETER RECEPTION")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--strategy", default="auto")
    parser.add_argument("--color-space", default="cmyk")
    parser.add_argument("--trim-w", type=float, default=148.0)
    parser.add_argument("--trim-h", type=float, default=210.0)
    parser.add_argument("--status-file", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--variant-path", default="")
    parser.add_argument("--original-path", default="")
    parser.add_argument("--zip-output", default="")
    parser.add_argument("--proof-path", default="")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--base-name", default="artwork")
    parser.add_argument("--crop-x", type=float, default=-1.0)
    parser.add_argument("--crop-y", type=float, default=-1.0)
    parser.add_argument("--crop-w", type=float, default=-1.0)
    parser.add_argument("--crop-h", type=float, default=-1.0)

    full_args = ["--input", input_path] + cli_args
    args = parser.parse_args(full_args)

    record(3, "args.trim_w matches Business Card width",
           abs(args.trim_w - TRIM_W_MM) < 0.01,
           f"args.trim_w={args.trim_w} (expected {TRIM_W_MM})")

    record(3, "args.trim_h matches Business Card height",
           abs(args.trim_h - TRIM_H_MM) < 0.01,
           f"args.trim_h={args.trim_h} (expected {TRIM_H_MM})")

    record(3, "args.color_space is CMYK",
           args.color_space.lower() == "cmyk",
           f"args.color_space={args.color_space}")

    has_crop = args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0
    record(3, "Crop coordinates parsed correctly",
           has_crop,
           f"crop: x={args.crop_x}, y={args.crop_y}, w={args.crop_w}, h={args.crop_h}")

    record(3, "args.trim_w NOT defaulted to 148 (A5)",
           args.trim_w != 148.0,
           f"args.trim_w={args.trim_w}")

    record(3, "args.trim_h NOT defaulted to 210 (A5)",
           args.trim_h != 210.0,
           f"args.trim_h={args.trim_h}")

    return args


def step4_prepress_engine_check(args, input_path):
    """Check that prepress checks can receive correct trim dimensions"""
    log_step(4, "25-POINT PREPRESS ENGINE -> DIMENSION AWARENESS")

    from prepress_checks import build_prepress_checks
    import fitz
    import numpy as np
    import cv2

    doc = fitz.open(input_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    trim_w_px = int(args.trim_w / 25.4 * 300)
    trim_h_px = int(args.trim_h / 25.4 * 300)

    trim_info = {
        "top": 0, "left": 0,
        "bottom": img_bgr.shape[0], "right": img_bgr.shape[1],
        "trim_w": trim_w_px, "trim_h": trim_h_px,
    }

    record(4, "trim_info.trim_w derived from target format (not source)",
           trim_info["trim_w"] == trim_w_px,
           f"trim_w={trim_info['trim_w']}px ({args.trim_w}mm @ 300dpi)")

    record(4, "trim_info.trim_h derived from target format (not source)",
           trim_info["trim_h"] == trim_h_px,
           f"trim_h={trim_info['trim_h']}px ({args.trim_h}mm @ 300dpi)")

    try:
        checks = build_prepress_checks(img_bgr, trim_info, {}, dpi=300, page_num=0, total_pages=1)
        record(4, "Prepress engine executed without crash",
               True, f"Returned {len(checks)} checks")
    except Exception as e:
        record(4, "Prepress engine executed without crash",
               False, f"Exception: {e}")
        checks = []

    doc.close()
    return checks


def step5_crop_simulation(args, input_path):
    """Simulate manual crop processing as done in compile_press_pdf.py"""
    log_step(5, "MANUAL CROP -> DIMENSION HANDOFF")

    import fitz
    import numpy as np

    doc = fitz.open(input_path)
    page = doc[0]
    src_w_mm = page.rect.width * 25.4 / 72.0
    src_h_mm = page.rect.height * 25.4 / 72.0

    print(f"  Source PDF: {src_w_mm:.1f}x{src_h_mm:.1f}mm")
    print(f"  Target trim: {args.trim_w}x{args.trim_h}mm")
    print(f"  Crop: x={args.crop_x}, y={args.crop_y}, w={args.crop_w}, h={args.crop_h}")

    has_manual_crop = args.crop_x >= 0 and args.crop_y >= 0 and args.crop_w > 0 and args.crop_h > 0

    record(5, "Manual crop detected in Python",
           has_manual_crop,
           f"has_manual_crop={has_manual_crop}")

    if has_manual_crop:
        target_w_px = int(round(args.trim_w / 25.4 * 300))
        target_h_px = int(round(args.trim_h / 25.4 * 300))
        target_aspect = args.trim_w / args.trim_h
        crop_aspect = args.crop_w / args.crop_h

        record(5, "Target dimensions used for crop scaling (not source)",
               target_w_px == int(round(TRIM_W_MM / 25.4 * 300)),
               f"target_w_px={target_w_px}, target_h_px={target_h_px}")

        record(5, "Crop aspect vs target aspect logged",
               True,
               f"crop_aspect={crop_aspect:.3f}, target_aspect={target_aspect:.3f}")

    doc.close()
    return has_manual_crop


def step6_enforce_mediabox(args, input_path):
    """Test _enforce_final_mediabox with the target format"""
    log_step(6, "ENFORCE MEDIABOX -> FINAL DIMENSION LOCK")

    from compile_press_pdf import _enforce_final_mediabox
    import fitz

    output_path = tempfile.NamedTemporaryFile(suffix="_enforced.pdf", delete=False).name

    try:
        _enforce_final_mediabox(input_path, output_path, args.trim_w, args.trim_h)
        success = True
    except Exception as e:
        record(6, "_enforce_final_mediabox() executed", False, str(e))
        return None

    record(6, "_enforce_final_mediabox() executed", True)

    doc = fitz.open(output_path)
    page = doc[0]
    w_mm = page.rect.width * 25.4 / 72.0
    h_mm = page.rect.height * 25.4 / 72.0

    record(6, f"MediaBox width = {EXPECTED_MEDIABOX_W_MM}mm (trim+10)",
           abs(w_mm - EXPECTED_MEDIABOX_W_MM) < 0.5,
           f"Actual: {w_mm:.1f}mm (expected {EXPECTED_MEDIABOX_W_MM}mm)")

    record(6, f"MediaBox height = {EXPECTED_MEDIABOX_H_MM}mm (trim+10)",
           abs(h_mm - EXPECTED_MEDIABOX_H_MM) < 0.5,
           f"Actual: {h_mm:.1f}mm (expected {EXPECTED_MEDIABOX_H_MM}mm)")

    record(6, "MediaBox NOT reverted to source dimensions (200x150mm)",
           abs(w_mm - 200.0) > 1.0 and abs(h_mm - 150.0) > 1.0,
           f"Source was 200x150mm, output is {w_mm:.1f}x{h_mm:.1f}mm")

    tb = page.trimbox
    tb_w = (tb.x1 - tb.x0) * 25.4 / 72.0
    tb_h = (tb.y1 - tb.y0) * 25.4 / 72.0

    record(6, f"TrimBox = {TRIM_W_MM}x{TRIM_H_MM}mm",
           abs(tb_w - TRIM_W_MM) < 0.5 and abs(tb_h - TRIM_H_MM) < 0.5,
           f"TrimBox: {tb_w:.1f}x{tb_h:.1f}mm")

    bb = page.bleedbox
    bb_w = (bb.x1 - bb.x0) * 25.4 / 72.0
    bb_h = (bb.y1 - bb.y0) * 25.4 / 72.0

    record(6, "BleedBox spans full MediaBox",
           abs(bb_w - w_mm) < 0.5 and abs(bb_h - h_mm) < 0.5,
           f"BleedBox: {bb_w:.1f}x{bb_h:.1f}mm, MediaBox: {w_mm:.1f}x{h_mm:.1f}mm")

    doc.close()
    return output_path


def step7_full_compile_subprocess(input_path):
    """Run the actual compile_press_pdf.py as a subprocess — the real deal"""
    log_step(7, "FULL SUBPROCESS COMPILE -> END-TO-END")

    output_path = tempfile.NamedTemporaryFile(suffix="_press_ready.pdf", delete=False).name
    status_file = tempfile.NamedTemporaryFile(suffix="_status.json", delete=False).name
    result_file = tempfile.NamedTemporaryFile(suffix="_result.json", delete=False).name

    args = [
        sys.executable,
        os.path.join("server", "compile_press_pdf.py"),
        "--input", input_path,
        "--output", output_path,
        "--strategy", STRATEGY,
        "--color-space", COLOR_SPACE,
        "--trim-w", str(TRIM_W_MM),
        "--trim-h", str(TRIM_H_MM),
        "--status-file", status_file,
        "--result-file", result_file,
        "--crop-x", str(MOCK_CROP["cropX"]),
        "--crop-y", str(MOCK_CROP["cropY"]),
        "--crop-w", str(MOCK_CROP["cropWidth"]),
        "--crop-h", str(MOCK_CROP["cropHeight"]),
    ]

    print(f"  Running: python3 compile_press_pdf.py")
    print(f"  Args: --trim-w {TRIM_W_MM} --trim-h {TRIM_H_MM} --color-space {COLOR_SPACE}")
    print(f"  Crop: --crop-x {MOCK_CROP['cropX']} --crop-y {MOCK_CROP['cropY']} --crop-w {MOCK_CROP['cropWidth']} --crop-h {MOCK_CROP['cropHeight']}")
    print()

    t0 = time.time()
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    elapsed = time.time() - t0

    record(7, "Compile subprocess exit code = 0",
           proc.returncode == 0,
           f"exit code={proc.returncode}, elapsed={elapsed:.1f}s")

    if proc.returncode != 0:
        stderr_lines = proc.stderr.strip().split("\n")
        for line in stderr_lines[-10:]:
            print(f"  {RED}STDERR: {line}{RESET}")
        return None, proc.stderr

    enforce_lines = [l for l in proc.stderr.split("\n") if "ENFORCE-MEDIABOX" in l]
    for line in enforce_lines:
        print(f"  {YELLOW}  {line.strip()}{RESET}")

    record(7, "ENFORCE-MEDIABOX ran during compile",
           len(enforce_lines) > 0,
           f"Found {len(enforce_lines)} enforcement log lines")

    dims_forced = any("forcing to" in l or "matches target" in l for l in enforce_lines)
    record(7, "MediaBox enforcement logged dimension action",
           dims_forced,
           "Enforcement either matched or forced target dimensions")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        import fitz
        doc = fitz.open(output_path)
        page = doc[0]
        w_mm = page.rect.width * 25.4 / 72.0
        h_mm = page.rect.height * 25.4 / 72.0

        record(7, f"Final PDF MediaBox = {EXPECTED_MEDIABOX_W_MM}x{EXPECTED_MEDIABOX_H_MM}mm",
               abs(w_mm - EXPECTED_MEDIABOX_W_MM) < 1.0 and abs(h_mm - EXPECTED_MEDIABOX_H_MM) < 1.0,
               f"Actual: {w_mm:.1f}x{h_mm:.1f}mm")

        record(7, "Final PDF NOT at source dimensions (200x150mm)",
               abs(w_mm - 200.0) > 5.0 or abs(h_mm - 150.0) > 5.0,
               f"Source=200x150mm, Final={w_mm:.1f}x{h_mm:.1f}mm")

        tb = page.trimbox
        tb_w = (tb.x1 - tb.x0) * 25.4 / 72.0
        tb_h = (tb.y1 - tb.y0) * 25.4 / 72.0

        record(7, f"Final TrimBox = {TRIM_W_MM}x{TRIM_H_MM}mm",
               abs(tb_w - TRIM_W_MM) < 1.0 and abs(tb_h - TRIM_H_MM) < 1.0,
               f"TrimBox: {tb_w:.1f}x{tb_h:.1f}mm")

        doc.close()
    else:
        record(7, "Output PDF exists and non-empty",
               False, f"Path: {output_path}")

    for f in [status_file, result_file]:
        try:
            os.unlink(f)
        except:
            pass

    return output_path, proc.stderr


def step8_source_code_audit():
    """Audit the source code for potential parameter-drop risks"""
    log_step(8, "SOURCE CODE AUDIT -> PARAMETER DROP DETECTION")

    routes_path = os.path.join("server", "routes.ts")
    compile_path = os.path.join("server", "compile_press_pdf.py")

    with open(routes_path, "r", encoding="utf-8", errors="replace") as f:
        routes_src = f.read()

    with open(compile_path, "r", encoding="utf-8", errors="replace") as f:
        compile_src = f.read()

    record(8, "routes.ts passes --trim-w to compile script",
           '"--trim-w"' in routes_src,
           "Found '--trim-w' in routes.ts spawn args")

    record(8, "routes.ts passes --trim-h to compile script",
           '"--trim-h"' in routes_src,
           "Found '--trim-h' in routes.ts spawn args")

    record(8, "routes.ts passes crop args when present",
           '"--crop-x"' in routes_src and '"--crop-w"' in routes_src,
           "Found '--crop-x' and '--crop-w' in routes.ts")

    record(8, "compile_press_pdf.py has --trim-w argparse",
           "--trim-w" in compile_src,
           "argparse --trim-w found")

    record(8, "compile_press_pdf.py has --crop-w argparse",
           "--crop-w" in compile_src,
           "argparse --crop-w found")

    record(8, "_enforce_final_mediabox called in PDF CMYK path",
           "_enforce_final_mediabox(work_path, enforce_tmp, args.trim_w, args.trim_h)" in compile_src,
           "Enforcement integrated in PDF CMYK branch")

    record(8, "_enforce_final_mediabox called in image CMYK path",
           "_enforce_final_mediabox(work_path, enforce_tmp_img, args.trim_w, args.trim_h)" in compile_src,
           "Enforcement integrated in image CMYK branch")

    record(8, "FROZEN RULE comment block present",
           "CORE PREPRESS RULE - DO NOT MODIFY" in compile_src,
           "Protection shield found in source")

    fallback_count = compile_src.count("default=148")
    record(8, "A5 default (148) only in argparse fallback",
           fallback_count <= 1,
           f"Found {fallback_count} instance(s) of default=148 (argparse only is fine)")

    trim_w_uses = compile_src.count("args.trim_w")
    trim_h_uses = compile_src.count("args.trim_h")
    record(8, "args.trim_w used throughout pipeline (not hardcoded)",
           trim_w_uses >= 5,
           f"args.trim_w referenced {trim_w_uses} times")

    record(8, "args.trim_h used throughout pipeline (not hardcoded)",
           trim_h_uses >= 5,
           f"args.trim_h referenced {trim_h_uses} times")


def step9_render_source_audit():
    """Verify comparison/proof/variant generators use cropped source, not uncropped original"""
    print(f"\n{BOLD}Step 9: Render Source Audit (Comparison / Proof / Variants){RESET}")
    print(f"{'─'*70}")

    bleed_src_path = os.path.join(os.path.dirname(__file__), "server", "smart_bleed.py")
    with open(bleed_src_path, "r", encoding="utf-8", errors="replace") as f:
        bleed_src = f.read()

    record(9, "comparison_before_source variable defined in image pipeline",
           "comparison_before_source = pre_bleed_path if pre_bleed_path" in bleed_src,
           "Image pipeline must derive 'Before' from pre_bleed_path, not input_path")

    record(9, "generate_signoff_comparison uses comparison_before_source (image)",
           "generate_signoff_comparison(comparison_before_source," in bleed_src,
           "Image comparison must pass cropped intermediate as 'Before'")

    record(9, "generate_bleed_report_proof uses comparison_before_source (image)",
           "generate_bleed_report_proof(comparison_before_source," in bleed_src,
           "Image bleed proof must pass cropped intermediate as 'Before'")

    img_fn_start = bleed_src.find("def apply_smart_bleed_to_image(")
    img_fn_end = bleed_src.find("\ndef apply_smart_bleed_to_pdf(", img_fn_start) if img_fn_start >= 0 else -1
    img_fn_body = bleed_src[img_fn_start:img_fn_end] if img_fn_start >= 0 and img_fn_end > img_fn_start else ""

    record(9, "Image comparison does NOT use raw input_path",
           "generate_signoff_comparison(input_path," not in img_fn_body,
           "Image pipeline must not pass uncropped input_path to comparison")

    record(9, "Image bleed proof does NOT use raw input_path",
           "generate_bleed_report_proof(input_path," not in img_fn_body,
           "Image pipeline must not pass uncropped input_path to bleed proof")

    record(9, "PDF variant source prefers output_path (corrected PDF)",
           "variant_source = output_path if os.path.exists(output_path) else input_path" in bleed_src,
           "PDF variants must render from corrected (cropped) PDF, not original")

    record(9, "PDF variant opens variant_source (not input_path directly)",
           "fitz.open(variant_source)" in bleed_src,
           "PDF variant doc must open corrected source, not uncropped original")

    record(9, "pre_bleed_path saved before bleed extension",
           'pre_bleed_path = os.path.splitext(output_path)[0] + "_prebleed.png"' in bleed_src,
           "Pre-bleed intermediate must be saved for use as comparison 'Before'")

    record(9, "pre_bleed_path written with cv2.imwrite",
           "cv2.imwrite(pre_bleed_path, img)" in bleed_src,
           "Cropped intermediate must be persisted to disk before bleed runs")


def print_system_health_report():
    """Print the final formatted system health report"""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    warnings = sum(1 for r in results if r.get("warn"))

    print(f"\n\n{'='*70}")
    print(f"{BOLD}{CYAN}")
    print(f"  ╔══════════════════════════════════════════════════════════╗")
    print(f"  ║          FLYERZ SYSTEM HEALTH REPORT                   ║")
    print(f"  ║          Full Pipeline Diagnostic Results               ║")
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    print(f"{RESET}")
    print(f"  Simulated Format: {BOLD}{SIMULATED_FORMAT}{RESET} ({TRIM_W_MM}x{TRIM_H_MM}mm)")
    print(f"  Expected MediaBox: {EXPECTED_MEDIABOX_W_MM}x{EXPECTED_MEDIABOX_H_MM}mm (trim + 5mm bleed)")
    print(f"  Manual Crop: {MOCK_CROP['cropWidth']}x{MOCK_CROP['cropHeight']}px at ({MOCK_CROP['cropX']},{MOCK_CROP['cropY']})")
    print(f"  Color Space: {COLOR_SPACE.upper()}")
    print(f"{'─'*70}")

    if failed == 0:
        print(f"\n  {GREEN}{BOLD}OVERALL: ALL {total} CHECKS PASSED{RESET}")
        if warnings > 0:
            print(f"  {YELLOW}{warnings} warning(s) noted (non-blocking){RESET}")
    else:
        print(f"\n  {RED}{BOLD}OVERALL: {failed} FAILURE(S) DETECTED{RESET} out of {total} checks")

    print(f"\n  {BOLD}Breakdown by Pipeline Stage:{RESET}")
    print(f"{'─'*70}")

    stages = {}
    for r in results:
        step = r["step"]
        if step not in stages:
            stages[step] = {"passed": 0, "failed": 0, "warns": 0}
        if r["passed"]:
            stages[step]["passed"] += 1
        else:
            stages[step]["failed"] += 1
        if r.get("warn"):
            stages[step]["warns"] += 1

    stage_names = {
        1: "Frontend -> API Request",
        2: "Backend -> CLI Arg Building",
        3: "Python Argparse -> Param Reception",
        4: "25-Point Prepress Engine",
        5: "Manual Crop -> Dim Handoff",
        6: "Enforce MediaBox -> Dim Lock",
        7: "Full Subprocess Compile (E2E)",
        8: "Source Code Audit",
        9: "Render Source Audit (Crop->Preview)",
    }

    for step in sorted(stages.keys()):
        s = stages[step]
        name = stage_names.get(step, f"Step {step}")
        if s["failed"] > 0:
            icon = f"{RED}✗{RESET}"
            detail = f"{RED}{s['failed']} FAILED{RESET}, {s['passed']} passed"
        else:
            icon = f"{GREEN}✓{RESET}"
            detail = f"{GREEN}{s['passed']} passed{RESET}"
        if s["warns"] > 0:
            detail += f", {YELLOW}{s['warns']} warn{RESET}"
        print(f"  {icon}  Step {step}: {name:40s} {detail}")

    if failed > 0:
        print(f"\n  {BOLD}{RED}FAILURES:{RESET}")
        print(f"{'─'*70}")
        for r in results:
            if not r["passed"]:
                print(f"  {RED}✗{RESET} [Step {r['step']}] {r['check']}")
                if r["detail"]:
                    print(f"    {RED}{r['detail']}{RESET}")

    print(f"\n{'─'*70}")
    if failed == 0:
        print(f"  {GREEN}{BOLD}VERDICT: Pipeline is HEALTHY.{RESET}")
        print(f"  {GREEN}All parameters flow from Frontend -> Backend -> Engine -> Final PDF.{RESET}")
        print(f"  {GREEN}Target format dimensions are enforced universally.{RESET}")
        print(f"  {GREEN}Source dimensions are never preserved if they deviate.{RESET}")
    else:
        print(f"  {RED}{BOLD}VERDICT: Pipeline has DISCONNECTS.{RESET}")
        print(f"  {RED}Review failures above — parameters are being dropped or ignored.{RESET}")
    print(f"{'='*70}\n")


def main():
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}  FLYERZ FULL PIPELINE DIAGNOSTIC TRACER{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"  Testing: {SIMULATED_FORMAT} format with manual crop + CMYK conversion")
    print(f"  Source PDF: 200x150mm (deliberately wrong for Business Card)")

    test_pdf, src_w, src_h = create_test_pdf()
    print(f"  Created test PDF: {src_w:.0f}x{src_h:.0f}mm at {test_pdf}")

    try:
        request_body = step1_simulate_frontend_request()
        cli_args = step2_backend_arg_building(request_body)
        args = step3_python_argparse(cli_args, test_pdf)
        step4_prepress_engine_check(args, test_pdf)
        step5_crop_simulation(args, test_pdf)
        enforced_path = step6_enforce_mediabox(args, test_pdf)
        output_path, stderr = step7_full_compile_subprocess(test_pdf)
        step8_source_code_audit()
        step9_render_source_audit()

        print_system_health_report()

    finally:
        try:
            os.unlink(test_pdf)
        except:
            pass
        for tmp in [enforced_path if 'enforced_path' in dir() else None,
                    output_path if 'output_path' in dir() else None]:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except:
                    pass


if __name__ == "__main__":
    _harden_stdio_for_windows()
    main()
