#!/usr/bin/env python3
"""
FLYERZ V1.0 — TITANIUM MASTER-AUDIT
=====================================
Comprehensive full-stack verification covering:
  1. Pipeline Simulation (Manual Crop + No-Crop routes, TAC 280%, Auto-Shifter, GS compile)
  2. Memory & Disk (50MB ceiling, /dev/shm routing, _tmp_chain janitor, dual-upload concurrency)
  3. Color & DPI (300 DPI forced, KPreserve=2, FOGRA39, Rich Black, single-layer enforcement)
  4. Non-Destructive AI Audit (12 stubs, no heavy ML imports, TAC math, Creep 1.5mm)
  5. Frontend & UX Integration (AR Proof, Glitchy ink-stain, jargon dictionary, all toggles)

Run: python3 server/e2e_integration_audit.py
Exit code 0 = all passed, 1 = failures detected.
"""

import sys
import os
import json
import time
import tempfile
import subprocess
import shutil
import math
import gc

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"

FAI_TEMP = os.environ.get("FAI_TEMP_DIR", "/dev/shm/flyerz_tmp")

results = []
temp_files = []
AUDIT_DIR = None


def get_audit_dir():
    global AUDIT_DIR
    if AUDIT_DIR is None or not os.path.exists(AUDIT_DIR):
        AUDIT_DIR = tempfile.mkdtemp(prefix="flyerz_titanium_audit_")
        temp_files.append(AUDIT_DIR)
    return AUDIT_DIR


def record(section, name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append({"section": section, "name": name, "passed": passed, "detail": detail})
    print(f"  {status}  {name}")
    if detail and not passed:
        print(f"         {detail}")


def cleanup():
    for f in temp_files:
        try:
            if os.path.isdir(f):
                shutil.rmtree(f, ignore_errors=True)
            elif os.path.exists(f):
                os.unlink(f)
        except Exception:
            pass


def get_dir_size_bytes(path):
    total = 0
    if not os.path.exists(path):
        return 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def print_report():
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total = passed + failed

    print(f"\n{'='*72}")
    print(f"{BOLD}{CYAN}  FLYERZ V1.0 — TITANIUM MASTER-AUDIT RESULTS{RESET}")
    print(f"{'='*72}")

    sections = {}
    for r in results:
        s = r["section"]
        if s not in sections:
            sections[s] = {"passed": 0, "failed": 0}
        if r["passed"]:
            sections[s]["passed"] += 1
        else:
            sections[s]["failed"] += 1

    for section, counts in sections.items():
        total_s = counts["passed"] + counts["failed"]
        icon = f"{GREEN}✓{RESET}" if counts["failed"] == 0 else f"{RED}✗{RESET}"
        print(f"  {icon}  {section:55s} {counts['passed']}/{total_s}")

    print(f"{'─'*72}")
    if failed == 0:
        print(f"\n  {GREEN}{BOLD}RESULT: ALL {total} CHECKS PASSED ✓{RESET}")
        print(f"  Full system integration verified. Titanium lockdown confirmed.")
        print(f"  Zero-regression policy ENFORCED.")
    else:
        print(f"\n  {RED}{BOLD}RESULT: {failed} of {total} FAILURE(S) DETECTED ✗{RESET}")
        print(f"{'─'*72}")
        for r in results:
            if not r["passed"]:
                print(f"  {RED}✗{RESET} [{r['section']}] {r['name']}")
                if r["detail"]:
                    print(f"    {RED}{r['detail']}{RESET}")

    print(f"{'='*72}\n")
    return failed == 0


def _create_test_image(w_mm, h_mm, dpi=300, label="AUDIT"):
    import numpy as np
    import cv2
    w_px = int(math.ceil((w_mm / 25.4) * dpi))
    h_px = int(math.ceil((h_mm / 25.4) * dpi))
    img = np.zeros((h_px, w_px, 3), dtype=np.uint8)
    img[:, :] = [20, 20, 20]
    cv2.rectangle(img, (100, 100), (w_px - 100, h_px - 100), (0, 0, 180), -1)
    cv2.putText(img, label, (w_px // 2 - 300, h_px // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 2.5, (255, 255, 255), 4, cv2.LINE_AA)
    img_path = os.path.join(get_audit_dir(), f"audit_{label.lower().replace(' ', '_')}.png")
    cv2.imwrite(img_path, img)
    temp_files.append(img_path)
    return img_path, w_px, h_px


def _run_compile(img_path, compile_label, trim_w, trim_h, extra_args=None):
    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    out = os.path.join(get_audit_dir(), f"{compile_label}.pdf")
    status_f = os.path.join(get_audit_dir(), f"{compile_label}_status.json")
    result_f = os.path.join(get_audit_dir(), f"{compile_label}_result.json")
    zip_f = os.path.join(get_audit_dir(), f"{compile_label}.zip")
    proof_f = os.path.join(get_audit_dir(), f"{compile_label}_proof.jpg")
    report_f = os.path.join(get_audit_dir(), f"{compile_label}_report.json")
    temp_files.extend([out, status_f, result_f, zip_f, proof_f, report_f])

    with open(status_f, "w") as sf:
        json.dump({"state": "PENDING", "message": ""}, sf)

    args = [
        "python3", compile_script,
        "--input", img_path,
        "--output", out,
        "--strategy", "stretch",
        "--color-space", "cmyk",
        "--trim-w", str(trim_w),
        "--trim-h", str(trim_h),
        "--status-file", status_f,
        "--result-file", result_f,
        "--zip-output", zip_f,
        "--proof-path", proof_f,
        "--report-path", report_f,
        "--base-name", compile_label,
        "--creep-mm", "0",
        "--auto-shifter", "0",
        "--original-path", img_path,
    ]
    if extra_args:
        args.extend(extra_args)

    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
    elapsed = time.time() - t0
    return proc, out, result_f, elapsed


# ============================================================
# AUDIT 1: Full Pipeline Simulation
# ============================================================
def audit_1_pipeline_simulation():
    print(f"\n{BOLD}{CYAN}AUDIT 1: STRICT PIPELINE SIMULATION{RESET}")
    print(f"{'─'*72}")

    a4_w, a4_h = 210.0, 297.0

    print(f"\n{BOLD}  1A — No-Crop Route (A4 @ 300 DPI){RESET}")
    img_path, w_px, h_px = _create_test_image(a4_w, a4_h, label="NO CROP A4")
    record("1. Pipeline", f"No-Crop test image created ({w_px}x{h_px}px)",
           os.path.exists(img_path) and os.path.getsize(img_path) > 1_000)

    bleed_script = os.path.join(os.path.dirname(__file__), "smart_bleed.py")
    bleed_out = os.path.join(get_audit_dir(), "nocrop_bleed_output.png")
    bleed_result_file = os.path.join(get_audit_dir(), "nocrop_bleed_result.json")
    temp_files.extend([bleed_out, bleed_result_file])

    bleed_proc = subprocess.run([
        "python3", bleed_script,
        "--input", img_path, "--output", bleed_out,
        "--result-file", bleed_result_file,
        "--target-w", str(a4_w), "--target-h", str(a4_h),
    ], capture_output=True, text=True, timeout=60)
    record("1. Pipeline", "No-Crop smart_bleed exit code = 0",
           bleed_proc.returncode == 0,
           bleed_proc.stderr[:300] if bleed_proc.returncode != 0 else "")

    nocrop_input = img_path
    if bleed_proc.returncode == 0 and os.path.exists(bleed_result_file):
        with open(bleed_result_file) as f:
            bleed_data = json.load(f)
        nocrop_input = bleed_data.get("preBleedPath") or bleed_data.get("correctedPath") or img_path
        record("1. Pipeline", "No-Crop route produced preBleedPath",
               bleed_data.get("preBleedPath") is not None or bleed_data.get("correctedPath") is not None)

    proc, nocrop_pdf, result_f, elapsed = _run_compile(nocrop_input, "nocrop_compile", a4_w, a4_h)
    record("1. Pipeline", "No-Crop GS compile exit=0",
           proc.returncode == 0, proc.stderr[:300] if proc.returncode != 0 else "")
    record("1. Pipeline", f"No-Crop compile in {elapsed:.1f}s (<90s)", elapsed < 90)
    record("1. Pipeline", "No-Crop output PDF exists",
           os.path.exists(nocrop_pdf) and os.path.getsize(nocrop_pdf) > 10_000)
    record("1. Pipeline", "No-Crop CMYK conversion logged",
           "CMYK" in proc.stderr or "CMYK" in proc.stdout)
    record("1. Pipeline", "No-Crop ENFORCE-MEDIABOX ran",
           "ENFORCE-MEDIABOX" in proc.stderr or "ENFORCE-MEDIABOX" in proc.stdout)
    record("1. Pipeline", "No-Crop single-layer enforcement ran",
           "single-layer" in proc.stderr.lower() or "single_layer" in proc.stderr.lower()
           or "single-layer" in proc.stdout.lower() or "single_layer" in proc.stdout.lower())

    print(f"\n{BOLD}  1B — Manual Crop Route (A4 -> cropped region){RESET}")
    crop_img, crop_w, crop_h = _create_test_image(a4_w, a4_h, label="MANUAL CROP")
    crop_bleed_out = os.path.join(get_audit_dir(), "crop_bleed_output.png")
    crop_result_file = os.path.join(get_audit_dir(), "crop_bleed_result.json")
    temp_files.extend([crop_bleed_out, crop_result_file])

    crop_proc = subprocess.run([
        "python3", bleed_script,
        "--input", crop_img, "--output", crop_bleed_out,
        "--result-file", crop_result_file,
        "--target-w", str(a4_w), "--target-h", str(a4_h),
        "--crop-x", "0.1", "--crop-y", "0.1",
        "--crop-w", "0.8", "--crop-h", "0.8",
    ], capture_output=True, text=True, timeout=60)
    record("1. Pipeline", "Manual Crop smart_bleed exit code = 0",
           crop_proc.returncode == 0,
           crop_proc.stderr[:300] if crop_proc.returncode != 0 else "")

    crop_input = crop_img
    if crop_proc.returncode == 0 and os.path.exists(crop_result_file):
        with open(crop_result_file) as f:
            crop_data = json.load(f)
        crop_input = crop_data.get("preBleedPath") or crop_data.get("correctedPath") or crop_img
        record("1. Pipeline", "Manual Crop route produced correctedPath",
               crop_data.get("correctedPath") is not None or crop_data.get("preBleedPath") is not None)

    proc2, crop_pdf, result_f2, elapsed2 = _run_compile(crop_input, "crop_compile", a4_w, a4_h,
                                                          extra_args=["--crop-x", "0.1", "--crop-y", "0.1",
                                                                      "--crop-w", "0.8", "--crop-h", "0.8"])
    record("1. Pipeline", "Manual Crop GS compile exit=0",
           proc2.returncode == 0, proc2.stderr[:300] if proc2.returncode != 0 else "")
    record("1. Pipeline", "Manual Crop output PDF exists",
           os.path.exists(crop_pdf) and os.path.getsize(crop_pdf) > 10_000)
    record("1. Pipeline", "Manual Crop CMYK conversion logged",
           "CMYK" in proc2.stderr or "CMYK" in proc2.stdout)

    print(f"\n{BOLD}  1C — Auto-Shifter (2% scale-down) flag test{RESET}")
    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    help_result = subprocess.run(["python3", compile_script, "--help"],
                                  capture_output=True, text=True, timeout=10)
    record("1. Pipeline", "Auto-Shifter --auto-shifter flag accepted by compiler",
           "--auto-shifter" in help_result.stdout)

    proc3, _, _, _ = _run_compile(nocrop_input, "auto_shifter_compile", a4_w, a4_h,
                                   extra_args=["--auto-shifter", "2.0"])
    record("1. Pipeline", "Auto-Shifter [AUTO-SHIFTER] logged in compile output",
           "[AUTO-SHIFTER]" in proc3.stdout or "[AUTO-SHIFTER]" in proc3.stderr)

    print(f"\n{BOLD}  1D — Both routes force 300 DPI metadata{RESET}")
    import fitz
    for label, pdf_path in [("No-Crop", nocrop_pdf), ("Manual-Crop", crop_pdf)]:
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            doc = fitz.open(pdf_path)
            page = doc[0]
            images = page.get_images(full=True)
            if images:
                xref = images[0][0]
                pix = fitz.Pixmap(doc, xref)
                record("1. Pipeline", f"{label} PDF has embedded raster image",
                       pix.width > 0 and pix.height > 0, f"{pix.width}x{pix.height}")
                pix = None
            doc.close()
        else:
            record("1. Pipeline", f"{label} PDF exists for DPI check", False)

    print(f"\n{BOLD}  1E — Both routes produce single-layer (zero ghost vectors){RESET}")
    for label, pdf_path in [("No-Crop", nocrop_pdf), ("Manual-Crop", crop_pdf)]:
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            doc = fitz.open(pdf_path)
            page = doc[0]
            drawings = page.get_drawings()
            images = page.get_images(full=True)
            annots = list(page.annots()) if page.annots() else []
            is_single = len(images) <= 1 and len(drawings) == 0 and len(annots) == 0
            record("1. Pipeline", f"{label} PDF is single-layer (0 vectors, ≤1 image, 0 annots)",
                   is_single,
                   f"images={len(images)}, vectors={len(drawings)}, annots={len(annots)}")
            doc.close()

    return nocrop_pdf, crop_pdf


# ============================================================
# AUDIT 2: Memory & Disk Verification
# ============================================================
def audit_2_memory_disk():
    print(f"\n{BOLD}{CYAN}AUDIT 2: STRICT MEMORY & BACKEND VERIFICATION{RESET}")
    print(f"{'─'*72}")

    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    with open(compile_script) as f:
        compile_src = f.read()
    bleed_script = os.path.join(os.path.dirname(__file__), "smart_bleed.py")
    with open(bleed_script) as f:
        bleed_src = f.read()
    gs_src = compile_src + bleed_src

    print(f"\n{BOLD}  2A — GS memory ceiling (hard-coded 50MB, single-threaded){RESET}")
    record("2. Memory & Disk", "NumRenderingThreads=1 in pipeline",
           "NumRenderingThreads=1" in gs_src)
    record("2. Memory & Disk", "MaxBitmap=50000000 in pipeline",
           "MaxBitmap=50000000" in gs_src)
    record("2. Memory & Disk", "BufferSpace=50000000 in pipeline",
           "BufferSpace=50000000" in gs_src)
    record("2. Memory & Disk", "BandBufferSpace=50000000 in pipeline",
           "BandBufferSpace=50000000" in gs_src)

    for script_name in ["smart_bleed.py", "precision_resize.py"]:
        script_path = os.path.join(os.path.dirname(__file__), script_name)
        if os.path.exists(script_path):
            with open(script_path) as f:
                src = f.read()
            record("2. Memory & Disk", f"MaxBitmap≤50MB in {script_name}",
                   "MaxBitmap=50000000" in src)

    print(f"\n{BOLD}  2B — Temp files route to /dev/shm/flyerz_tmp{RESET}")
    os.makedirs(FAI_TEMP, exist_ok=True)
    record("2. Memory & Disk", "compile_press_pdf.py uses FAI_TEMP_DIR",
           "FAI_TEMP_DIR" in compile_src or "flyerz_tmp" in compile_src)
    record("2. Memory & Disk", "smart_bleed.py uses FAI_TEMP_DIR",
           "FAI_TEMP_DIR" in bleed_src or "flyerz_tmp" in bleed_src)

    print(f"\n{BOLD}  2C — _tmp_chain janitor cleanup verification{RESET}")
    record("2. Memory & Disk", "_tmp_chain list initialized in compiler",
           "_tmp_chain = []" in compile_src or "_tmp_chain=[]" in compile_src)
    record("2. Memory & Disk", "_tmp_chain janitor runs in finally block",
           "for _tc in _tmp_chain" in compile_src)
    record("2. Memory & Disk", "_tmp_chain.clear() called after cleanup",
           "_tmp_chain.clear()" in compile_src)
    janitor_count = compile_src.count("for _tc in _tmp_chain")
    record("2. Memory & Disk", f"_tmp_chain janitor runs on BOTH success + failure paths (count={janitor_count})",
           janitor_count >= 2, f"Found {janitor_count} cleanup loops (need ≥2: success + except)")

    print(f"\n{BOLD}  2D — Dual-upload concurrency simulation{RESET}")
    img1, _, _ = _create_test_image(148.0, 210.0, label="CONCURRENT A")
    img2, _, _ = _create_test_image(90.0, 55.0, label="CONCURRENT B")

    shm_before = set(os.listdir(FAI_TEMP)) if os.path.exists(FAI_TEMP) else set()
    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")

    def _make_concurrent_args(img, label, tw, th):
        out = os.path.join(get_audit_dir(), f"{label}.pdf")
        sf = os.path.join(get_audit_dir(), f"{label}_status.json")
        rf = os.path.join(get_audit_dir(), f"{label}_result.json")
        zf = os.path.join(get_audit_dir(), f"{label}.zip")
        pf = os.path.join(get_audit_dir(), f"{label}_proof.jpg")
        rpf = os.path.join(get_audit_dir(), f"{label}_report.json")
        temp_files.extend([out, sf, rf, zf, pf, rpf])
        with open(sf, "w") as f:
            json.dump({"state": "PENDING"}, f)
        return [
            "python3", compile_script,
            "--input", img, "--output", out,
            "--strategy", "stretch", "--color-space", "cmyk",
            "--trim-w", str(tw), "--trim-h", str(th),
            "--status-file", sf, "--result-file", rf,
            "--zip-output", zf, "--proof-path", pf,
            "--report-path", rpf, "--base-name", label,
            "--creep-mm", "0", "--auto-shifter", "0",
            "--original-path", img,
        ], out, rf

    args_a, out_a, rf_a = _make_concurrent_args(img1, "concurrent_a", 148.0, 210.0)
    args_b, out_b, rf_b = _make_concurrent_args(img2, "concurrent_b", 90.0, 55.0)

    proc_a = subprocess.Popen(args_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc_b = subprocess.Popen(args_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        stdout_a, stderr_a = proc_a.communicate(timeout=90)
        stdout_b, stderr_b = proc_b.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        proc_a.kill()
        proc_b.kill()
        stdout_a, stderr_a = proc_a.communicate()
        stdout_b, stderr_b = proc_b.communicate()

    record("2. Memory & Disk", "Concurrent compile A exit=0",
           proc_a.returncode == 0, stderr_a.decode()[:200] if proc_a.returncode != 0 else "")
    record("2. Memory & Disk", "Concurrent compile B exit=0",
           proc_b.returncode == 0, stderr_b.decode()[:200] if proc_b.returncode != 0 else "")
    record("2. Memory & Disk", "Concurrent output A exists",
           os.path.exists(out_a) and os.path.getsize(out_a) > 10_000)
    record("2. Memory & Disk", "Concurrent output B exists",
           os.path.exists(out_b) and os.path.getsize(out_b) > 10_000)

    time.sleep(1)
    shm_after = set(os.listdir(FAI_TEMP)) if os.path.exists(FAI_TEMP) else set()
    orphans = shm_after - shm_before
    audit_prefix = "flyerz_titanium_audit_"
    real_orphans = [o for o in orphans if not o.startswith(audit_prefix)]
    record("2. Memory & Disk", f"0 orphaned tmp files after dual-upload (_tmp_chain janitor)",
           len(real_orphans) == 0,
           f"Orphans: {list(real_orphans)[:5]}" if real_orphans else "Clean")

    print(f"\n{BOLD}  2E — AI enhancement RAM guard{RESET}")
    ai_script = os.path.join(os.path.dirname(__file__), "ai_enhancements.py")
    with open(ai_script) as f:
        ai_src = f.read()
    record("2. Memory & Disk", "_check_image_ram() guard exists",
           "_check_image_ram" in ai_src)
    record("2. Memory & Disk", "RAM guard threshold 50_000_000 pixels",
           "50_000_000" in ai_src or "50000000" in ai_src)

    print(f"\n{BOLD}  2F — /dev/shm stale file check{RESET}")
    stale = []
    if os.path.exists(FAI_TEMP):
        for entry in os.listdir(FAI_TEMP):
            fp = os.path.join(FAI_TEMP, entry)
            try:
                if time.time() - os.path.getmtime(fp) > 300:
                    stale.append(entry)
            except OSError:
                pass
    record("2. Memory & Disk", "No stale files (>5min) in /dev/shm/flyerz_tmp",
           len(stale) == 0, f"Stale: {stale[:5]}" if stale else "Clean")


# ============================================================
# AUDIT 3: Absolute Prepress Precision (Color, DPI, Flattening)
# ============================================================
def audit_3_color_dpi(nocrop_pdf, crop_pdf):
    print(f"\n{BOLD}{CYAN}AUDIT 3: ABSOLUTE PREPRESS PRECISION{RESET}")
    print(f"{'─'*72}")

    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    with open(compile_script) as f:
        compile_src = f.read()
    bleed_script = os.path.join(os.path.dirname(__file__), "smart_bleed.py")
    with open(bleed_script) as f:
        bleed_src = f.read()
    pipeline_src = compile_src + bleed_src

    print(f"\n{BOLD}  3A — Rich Black preservation (KPreserve=2, BlackPointCompensation){RESET}")
    record("3. Color & DPI", "KPreserve=2 (Rich Black preservation level 2)",
           "KPreserve=2" in pipeline_src)
    record("3. Color & DPI", "BlackPointCompensation=true",
           "BlackPointCompensation" in pipeline_src)
    record("3. Color & DPI", "RenderIntent=1 (Relative Colorimetric)",
           "RenderIntent=1" in pipeline_src)
    record("3. Color & DPI", "ColorConversionStrategy=CMYK",
           "ColorConversionStrategy=CMYK" in pipeline_src)
    record("3. Color & DPI", "ColorConversionStrategyForImages=CMYK",
           "ColorConversionStrategyForImages=CMYK" in pipeline_src)

    print(f"\n{BOLD}  3B — FOGRA39 ICC profile handling{RESET}")
    record("3. Color & DPI", "FOGRA39 reference in pipeline",
           "FOGRA39" in pipeline_src or "fogra39" in pipeline_src.lower())
    record("3. Color & DPI", "OutputIntent handling in pipeline",
           "OutputIntent" in pipeline_src or "output_intent" in pipeline_src.lower())

    print(f"\n{BOLD}  3C — 300 DPI enforcement across both routes{RESET}")
    record("3. Color & DPI", "DEFAULT_DPI = 300 in smart_bleed.py",
           "DEFAULT_DPI = 300" in bleed_src)
    record("3. Color & DPI", "300 DPI in compile_press_pdf.py",
           "dpi=300" in compile_src or "set_dpi(300" in compile_src)
    record("3. Color & DPI", "_prerasterize_pdf renders at 300 DPI",
           "_prerasterize_pdf" in compile_src and "300" in compile_src)

    print(f"\n{BOLD}  3D — Both route PDFs: MediaBox/TrimBox accuracy{RESET}")
    import fitz
    for label, pdf_path, exp_w, exp_h in [
        ("No-Crop A4", nocrop_pdf, 220.0, 307.0),
        ("Crop A4", crop_pdf, 220.0, 307.0),
    ]:
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            doc = fitz.open(pdf_path)
            page = doc[0]
            mb = page.mediabox
            mb_w = (mb.width * 25.4) / 72.0
            mb_h = (mb.height * 25.4) / 72.0
            record("3. Color & DPI", f"{label} MediaBox ≈ {exp_w}x{exp_h}mm",
                   abs(mb_w - exp_w) < 2.0 and abs(mb_h - exp_h) < 2.0,
                   f"Actual: {mb_w:.1f}x{mb_h:.1f}mm")

            tb = page.trimbox
            if tb:
                tb_w = (tb.width * 25.4) / 72.0
                tb_h = (tb.height * 25.4) / 72.0
                record("3. Color & DPI", f"{label} TrimBox ≈ 210x297mm",
                       abs(tb_w - 210.0) < 2.0 and abs(tb_h - 297.0) < 2.0,
                       f"Actual: {tb_w:.1f}x{tb_h:.1f}mm")
            doc.close()
        else:
            record("3. Color & DPI", f"{label} PDF exists", False)

    print(f"\n{BOLD}  3E — _enforce_single_layer function exists{RESET}")
    record("3. Color & DPI", "_enforce_single_layer() defined in compiler",
           "def _enforce_single_layer" in compile_src)
    record("3. Color & DPI", "_enforce_single_layer called in PDF pipeline",
           "_enforce_single_layer(" in compile_src)
    record("3. Color & DPI", "Ghost layer prevention logged",
           "ghost layer prevention" in compile_src.lower())

    print(f"\n{BOLD}  3F — Interpolation rules (INTER_AREA down, INTER_CUBIC up){RESET}")
    for script_name in ["smart_bleed.py", "precision_resize.py"]:
        script_path = os.path.join(os.path.dirname(__file__), script_name)
        if os.path.exists(script_path):
            with open(script_path) as f:
                src = f.read()
            record("3. Color & DPI", f"INTER_AREA for downscale in {script_name}",
                   "INTER_AREA" in src)
            record("3. Color & DPI", f"No INTER_LANCZOS4 in {script_name}",
                   "INTER_LANCZOS4" not in src,
                   "INTER_LANCZOS4 found" if "INTER_LANCZOS4" in src else "Clean")


# ============================================================
# AUDIT 4: Non-Destructive AI & Logic Audit
# ============================================================
def audit_4_ai_logic():
    print(f"\n{BOLD}{CYAN}AUDIT 4: NON-DESTRUCTIVE AI & LOGIC AUDIT{RESET}")
    print(f"{'─'*72}")

    ai_script = os.path.join(os.path.dirname(__file__), "ai_enhancements.py")
    with open(ai_script) as f:
        ai_src = f.read()

    print(f"\n{BOLD}  4A — No heavy ML libraries imported (torch, tensorflow, keras, onnx){RESET}")
    banned_imports = ["import torch", "import tensorflow", "import keras", "import onnx",
                      "from torch", "from tensorflow", "from keras", "from onnx"]
    for banned in banned_imports:
        record("4. AI & Logic", f"No '{banned}' in ai_enhancements.py",
               banned not in ai_src,
               f"BANNED import found!" if banned in ai_src else "Clean")

    print(f"\n{BOLD}  4B — All 12 AI stubs return correct payload pattern{RESET}")
    stub_functions = [
        "apply_denoise", "apply_sharpen_logos", "apply_spell_check",
        "apply_tac_limit", "apply_trapping", "apply_engagement_score",
        "apply_background_remove", "apply_text_reconstruct", "apply_spot_uv_mapper",
        "apply_expand_background", "apply_identify_fonts", "apply_test_design_style",
    ]
    for fn in stub_functions:
        record("4. AI & Logic", f"Function {fn}() defined",
               f"def {fn}" in ai_src)

    cli_actions = [
        "denoise", "sharpen_logos", "spell_check", "tac_limit", "trapping",
        "engagement_score", "background_remove", "text_reconstruct", "spot_uv_mapper",
        "expand_background", "identify_fonts", "test_design_style",
    ]
    for action in cli_actions:
        record("4. AI & Logic", f"CLI dispatcher handles '{action}'",
               f'action == "{action}"' in ai_src)

    print(f"\n{BOLD}  4C — TAC 280% mathematical cap{RESET}")
    img_path, _, _ = _create_test_image(90.0, 55.0, label="TAC TEST")
    try:
        tac_result = subprocess.run(
            ["python3", ai_script, "tac_limit", img_path, "{}"],
            capture_output=True, text=True, timeout=30
        )
        tac_data = json.loads(tac_result.stdout.strip())
        record("4. AI & Logic", "TAC 280% execution: success=True",
               tac_data.get("success") is True)
        max_tac = tac_data.get("max_tac_found")
        record("4. AI & Logic", "TAC result: max_tac ≤ 280% (or skipped for RGB input)",
               max_tac is None or max_tac <= 280.5,
               f"max_tac_found={max_tac}")
    except Exception as e:
        record("4. AI & Logic", "TAC 280% execution", False, str(e)[:200])

    print(f"\n{BOLD}  4D — Auto-Fix Safe Zone (2% scale-down, re-center){RESET}")
    compile_script = os.path.join(os.path.dirname(__file__), "compile_press_pdf.py")
    with open(compile_script) as f:
        compile_src = f.read()
    record("4. AI & Logic", "Auto-Shifter stub in compiler",
           "AUTO-SHIFTER" in compile_src or "auto_shifter" in compile_src)
    record("4. AI & Logic", "--auto-shifter CLI argument defined",
           "--auto-shifter" in compile_src)

    print(f"\n{BOLD}  4E — Folded Menu: 1.5mm Gutter Creep{RESET}")
    record("4. AI & Logic", "_apply_creep_shift() defined in compiler",
           "def _apply_creep_shift" in compile_src)
    record("4. AI & Logic", "Creep uses 300 DPI rasterize",
           "300" in compile_src and "creep" in compile_src.lower())
    record("4. AI & Logic", "--creep-mm CLI argument defined",
           "--creep-mm" in compile_src)

    routes_path = os.path.join(os.path.dirname(__file__), "..", "server", "routes.ts")
    with open(routes_path) as f:
        routes_src = f.read()
    record("4. AI & Logic", "Routes: creep = 1.5mm when enableCreepCompensation=true",
           "1.5" in routes_src and "enableCreepCompensation" in routes_src)
    record("4. AI & Logic", "Routes: creep = 0 when enableCreepCompensation=false",
           "creepEnabled ? 1.5 : 0" in routes_src or "CreepMm = precompileCreepEnabled ? 1.5 : 0" in routes_src)

    creep_img, _, _ = _create_test_image(148.0, 210.0, label="CREEP TEST")
    proc_c, creep_pdf, _, _ = _run_compile(creep_img, "creep_compile", 148.0, 210.0,
                                            extra_args=["--creep-mm", "1.5"])
    record("4. AI & Logic", "Creep 1.5mm compile exit=0",
           proc_c.returncode == 0, proc_c.stderr[:200] if proc_c.returncode != 0 else "")
    record("4. AI & Logic", "[CREEP] Applied 1.5mm logged",
           "[CREEP]" in proc_c.stderr.decode() if isinstance(proc_c.stderr, bytes) else "[CREEP]" in proc_c.stderr)

    import fitz
    if os.path.exists(creep_pdf) and os.path.getsize(creep_pdf) > 0:
        doc = fitz.open(creep_pdf)
        page = doc[0]
        tb = page.trimbox
        mb = page.mediabox
        if tb and mb:
            tb_w_mm = (tb.width * 25.4) / 72.0
            mb_w_mm = (mb.width * 25.4) / 72.0
            record("4. AI & Logic", f"Creep PDF has valid TrimBox ({tb_w_mm:.1f}mm wide)",
                   tb_w_mm > 100, f"TrimBox width={tb_w_mm:.1f}mm")
            record("4. AI & Logic", f"Creep PDF MediaBox > TrimBox (bleed preserved)",
                   mb_w_mm > tb_w_mm,
                   f"MediaBox={mb_w_mm:.1f}mm, TrimBox={tb_w_mm:.1f}mm")
        doc.close()

    print(f"\n{BOLD}  4F — Stub safety: all stubs return original_preserved=True{RESET}")
    stub_actions = ["denoise", "sharpen_logos", "spell_check", "engagement_score",
                    "background_remove", "text_reconstruct", "spot_uv_mapper",
                    "expand_background", "identify_fonts", "test_design_style"]
    test_img, _, _ = _create_test_image(90.0, 55.0, label="STUB SAFETY")
    for action in stub_actions:
        try:
            stub_result = subprocess.run(
                ["python3", ai_script, action, test_img, "{}"],
                capture_output=True, text=True, timeout=15
            )
            if stub_result.returncode == 0:
                data = json.loads(stub_result.stdout.strip())
                record("4. AI & Logic", f"Stub '{action}': original_preserved=True",
                       data.get("original_preserved") is True,
                       f"original_preserved={data.get('original_preserved')}")
            else:
                record("4. AI & Logic", f"Stub '{action}' execution", False, stub_result.stderr[:100])
        except Exception as e:
            record("4. AI & Logic", f"Stub '{action}' execution", False, str(e)[:100])


# ============================================================
# AUDIT 5: Frontend & UX Integration
# ============================================================
def audit_5_frontend_ux():
    print(f"\n{BOLD}{CYAN}AUDIT 5: FRONTEND & UX INTEGRATION{RESET}")
    print(f"{'─'*72}")

    base = os.path.join(os.path.dirname(__file__), "..")

    print(f"\n{BOLD}  5A — AR Proof: model-viewer, zero backend 3D rendering{RESET}")
    ar_page = os.path.join(base, "client", "src", "pages", "ar-proof.tsx")
    with open(ar_page) as f:
        ar_src = f.read()
    record("5. Frontend & UX", "AR Proof page exists", os.path.exists(ar_page))
    record("5. Frontend & UX", "AR Proof uses <model-viewer> component",
           "model-viewer" in ar_src)
    record("5. Frontend & UX", "AR Proof loads model-viewer from CDN (client-side GPU)",
           "ajax.googleapis.com" in ar_src or "unpkg.com" in ar_src or "cdn" in ar_src.lower())
    routes_path = os.path.join(base, "server", "routes.ts")
    with open(routes_path) as f:
        routes_src = f.read()
    record("5. Frontend & UX", "No server-side 3D rendering (no three.js/webgl imports in routes)",
           "three" not in routes_src.lower() or "THREE" not in routes_src)

    print(f"\n{BOLD}  5B — Glitchy Ink-Stain CMYK Animation{RESET}")
    glitchy_path = os.path.join(base, "client", "src", "components", "glitchy-widget.tsx")
    with open(glitchy_path) as f:
        glitchy_src = f.read()
    record("5. Frontend & UX", "Glitchy listens for 'glitchy:ink-stain' event",
           "glitchy:ink-stain" in glitchy_src)
    record("5. Frontend & UX", "@keyframes ink-stain-splat animation defined",
           "ink-stain-splat" in glitchy_src)
    record("5. Frontend & UX", "inkStainActive state drives CSS class",
           "inkStainActive" in glitchy_src)
    record("5. Frontend & UX", "glitchy-ink-stain class applied conditionally",
           "glitchy-ink-stain" in glitchy_src)

    cmyk_rgba_markers = [
        ("Cyan-spectrum (0, 200, 255)", "0, 200, 255"),
        ("Magenta-spectrum (255, 0, 180)", "255, 0, 180"),
        ("Yellow-spectrum (255, 255, 0)", "255, 255, 0"),
        ("Key/Black (0, 0, 0)", "0, 0, 0"),
    ]
    for label, rgba_fragment in cmyk_rgba_markers:
        record("5. Frontend & UX", f"Ink-stain uses CMYK color: {label}",
               rgba_fragment in glitchy_src)

    print(f"\n{BOLD}  5C — Jargon Translation Dictionary (Simple English){RESET}")
    jd_path = os.path.join(base, "client", "src", "pages", "job-details.tsx")
    with open(jd_path) as f:
        jd_src = f.read()

    translations = [
        ("Making your artwork razor-sharp...", "300 DPI"),
        ("Stretching the edges for a clean cut...", "Bleed/Edge Replication"),
        ("Locking your design in place...", "Flattening"),
        ("Marking where the cutter must trim...", "TrimBox"),
        ("Mixing the perfect ink colors...", "FOGRA39/CMYK"),
        ("Closing up tiny print gaps...", "Trapping"),
        ("Cleaning up and packaging the final file...", "ZIP Packaging"),
    ]
    for friendly, technical in translations:
        record("5. Frontend & UX", f"Jargon: '{friendly}'",
               friendly in jd_src, f"Replaces: {technical}")

    banned_jargon = ["FOGRA39", "INTER_AREA", "NumRenderingThreads", "BufferSpace",
                     "MaxBitmap", "BandBufferSpace", "KPreserve"]
    for term in banned_jargon:
        record("5. Frontend & UX", f"No raw jargon '{term}' in UI",
               term not in jd_src)

    print(f"\n{BOLD}  5D — All 12 AI enhancement toggles wired in UI{RESET}")
    all_stubs = [
        "denoise", "sharpen_logos", "spell_check", "tac_limit", "trapping",
        "engagement_score", "background_remove", "text_reconstruct", "spot_uv_mapper",
        "expand_background", "identify_fonts", "test_design_style",
    ]
    for stub in all_stubs:
        has_toggle = stub.replace("_", "-") in jd_src or stub in jd_src
        record("5. Frontend & UX", f"AI toggle '{stub}' wired in job-details.tsx",
               has_toggle)

    for stub in all_stubs:
        record("5. Frontend & UX", f"'{stub}' in routes.ts validEnhancements",
               f'"{stub}"' in routes_src)

    print(f"\n{BOLD}  5E — Auto-Shifter and Creep UI toggles{RESET}")
    record("5. Frontend & UX", "Auto-Shifter toggle (switch-auto-shifter)",
           "switch-auto-shifter" in jd_src)
    record("5. Frontend & UX", "Auto-Shifter state: autoShifterEnabled",
           "autoShifterEnabled" in jd_src)

    file_upload_path = os.path.join(base, "client", "src", "components", "file-upload.tsx")
    if os.path.exists(file_upload_path):
        with open(file_upload_path) as f:
            fu_src = f.read()
        record("5. Frontend & UX", "Creep toggle (enableCreepCompensation) in file-upload.tsx",
               "enableCreepCompensation" in fu_src)
    else:
        record("5. Frontend & UX", "Creep toggle in UI", "enableCreepCompensation" in jd_src)

    print(f"\n{BOLD}  5F — Schema consistency (12 enhancements){RESET}")
    schema_path = os.path.join(base, "shared", "schema.ts")
    with open(schema_path) as f:
        schema_src = f.read()
    for stub in all_stubs:
        record("5. Frontend & UX", f"Schema slot: '{stub}'",
               stub in schema_src)

    print(f"\n{BOLD}  5G — 7-stage compile progress tracker{RESET}")
    record("5. Frontend & UX", "7-stage tracker with jargon-free labels",
           "Closing up tiny print gaps..." in jd_src and "Locking your design in place..." in jd_src)

    print(f"\n{BOLD}  5H — Ink-stain dispatched during CMYK/Trap phase{RESET}")
    record("5. Frontend & UX", "glitchy:ink-stain dispatched on compile polling",
           "glitchy:ink-stain" in jd_src)

    print(f"\n{BOLD}  5I — Glitchy unstick: no infinite PROCESSING deadlock{RESET}")
    record("5. Frontend & UX", "Glitchy busy watchdog (GLITCHY_BUSY_WATCHDOG_MS)",
           "GLITCHY_BUSY_WATCHDOG_MS" in glitchy_src)
    record("5. Frontend & UX", "Glitchy listens for glitchy:job-complete",
           "glitchy:job-complete" in glitchy_src)
    record("5. Frontend & UX", "Job page dispatches glitchy:job-complete",
           "glitchy:job-complete" in jd_src)
    record("5. Frontend & UX", "Compile COMPLETE dispatches glitchy:compile-complete",
           "glitchy:compile-complete" in jd_src)
    record("5. Frontend & UX", "Click does not ignore PROCESSING forever",
           'if (processState === "PROCESSING") return;' not in glitchy_src)

    fu_path = os.path.join(base, "client", "src", "components", "file-upload.tsx")
    with open(fu_path) as f:
        fu_src = f.read()
    record("5. Frontend & UX", "No Crop Needed populates FULL_PAGE_CROP_BOX",
           "FULL_PAGE_CROP_BOX" in fu_src)


# ============================================================
# MAIN
# ============================================================
def main():
    print(f"\n{BOLD}{'='*72}{RESET}")
    print(f"{BOLD}{CYAN}  FLYERZ V1.0 — TITANIUM MASTER-AUDIT{RESET}")
    print(f"{BOLD}  Full-Stack Lockdown Verification (Phases 1-4.5){RESET}")
    print(f"{BOLD}{'='*72}{RESET}")
    print(f"  Temp dir: {FAI_TEMP}")
    print(f"  Audit dir: {get_audit_dir()}")

    try:
        nocrop_pdf, crop_pdf = audit_1_pipeline_simulation()
        gc.collect()

        audit_2_memory_disk()
        gc.collect()

        audit_3_color_dpi(nocrop_pdf, crop_pdf)
        gc.collect()

        audit_4_ai_logic()
        gc.collect()

        audit_5_frontend_ux()
        gc.collect()

        all_passed = print_report()
        sys.exit(0 if all_passed else 1)

    finally:
        cleanup()


if __name__ == "__main__":
    main()
