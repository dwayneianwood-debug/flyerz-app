#!/usr/bin/env python3
"""
FLYERZ V1.0 — PRE-DEPLOY GATEKEEPER
=====================================
This script MUST pass before the application is allowed to start.
It enforces the 5 Architectural Laws and all V1.0 stability rules.

If any rule is violated, the script exits with code 1 and prints
a detailed Rule Violation report. The application MUST NOT start.

Checks:
  1. All 57+ anti-regression tests pass
  2. GS memory leashes: MaxBitmap=50MB, BufferSpace=50MB
  3. DPI metadata logic pinned at 300
  4. Color intent: RenderIntent=1 (Relative Colorimetric), KPreserve=2
  5. _enforce_final_mediabox() frozen (LAST geometry pass marker)
  6. Single-layer raster enforcement in compile pipeline
  7. _resource_wipe preserves original_input_path
  8. Raster-first handoff present in compile pipeline

Exit 0 = all gates pass — app may start.
Exit 1 = violation detected — app MUST NOT start.
"""

import sys
import os
import re
import subprocess
import time

BOLD = "\033[1m"
RESET = "\033[0m"
RED = "\033[91m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SERVER_DIR)

violations = []
passes = []


def check(rule_id, description, passed, detail=""):
    if passed:
        passes.append({"rule": rule_id, "desc": description, "detail": detail})
        print(f"  {GREEN}✓{RESET}  [{rule_id}] {description}")
    else:
        violations.append({"rule": rule_id, "desc": description, "detail": detail})
        print(f"  {RED}✗{RESET}  [{rule_id}] {description}")
        if detail:
            print(f"       {RED}{detail}{RESET}")


def gate_1_test_suite():
    print(f"\n{BOLD}Gate 1: Anti-Regression Test Suite{RESET}")
    print(f"{'─'*60}")

    test_script = os.path.join(SERVER_DIR, "test_pipeline.py")
    if not os.path.exists(test_script):
        check("G1.1", "test_pipeline.py exists", False, f"Missing: {test_script}")
        return

    check("G1.1", "test_pipeline.py exists", True)

    try:
        proc = subprocess.run(
            [sys.executable, test_script],
            capture_output=True, text=True, timeout=300
        )
        all_passed = proc.returncode == 0
        match = re.search(r"ALL (\d+) CHECKS PASSED", proc.stdout)
        count = match.group(1) if match else "?"
        check("G1.2", f"All {count} anti-regression checks pass",
              all_passed,
              f"Exit code: {proc.returncode}. Tail: {proc.stdout[-200:]}" if not all_passed else f"{count} checks passed")
    except subprocess.TimeoutExpired:
        check("G1.2", "Anti-regression suite completes within timeout", False, "Timed out after 300s")
    except Exception as e:
        check("G1.2", "Anti-regression suite runs successfully", False, str(e))


def gate_2_gs_memory_leash():
    print(f"\n{BOLD}Gate 2: Ghostscript Memory Leash Enforcement{RESET}")
    print(f"{'─'*60}")

    files_to_check = ["smart_bleed.py", "compile_press_pdf.py", "precision_resize.py"]

    for fname in files_to_check:
        fpath = os.path.join(SERVER_DIR, fname)
        if not os.path.exists(fpath):
            continue

        with open(fpath, "r") as f:
            src = f.read()

        if "NumRenderingThreads" not in src:
            continue

        thread_matches = re.findall(r"NumRenderingThreads=(\d+)", src)
        for val in thread_matches:
            check("G2.1", f"GS threads ≤ 1 in {fname}",
                  int(val) <= 1,
                  f"Found NumRenderingThreads={val} — must be 1")

        bitmap_matches = re.findall(r"MaxBitmap[=\s]+(\d+)", src)
        for val in bitmap_matches:
            check("G2.2", f"GS MaxBitmap ≤ 50MB in {fname}",
                  int(val) <= 50_000_000,
                  f"Found MaxBitmap={val}")

        buffer_matches = re.findall(r"BufferSpace=(\d+)", src)
        for val in buffer_matches:
            check("G2.3", f"GS BufferSpace ≤ 50MB in {fname}",
                  int(val) <= 50_000_000,
                  f"Found BufferSpace={val}")

        band_matches = re.findall(r"BandBufferSpace=(\d+)", src)
        for val in band_matches:
            check("G2.4", f"GS BandBufferSpace ≤ 50MB in {fname}",
                  int(val) <= 50_000_000,
                  f"Found BandBufferSpace={val}")


def gate_3_dpi_metadata():
    print(f"\n{BOLD}Gate 3: DPI Metadata Pinned at 300{RESET}")
    print(f"{'─'*60}")

    bleed_path = os.path.join(SERVER_DIR, "smart_bleed.py")
    compile_path = os.path.join(SERVER_DIR, "compile_press_pdf.py")

    with open(bleed_path, "r") as f:
        bleed_src = f.read()
    with open(compile_path, "r") as f:
        compile_src = f.read()

    check("G3.1", "DEFAULT_DPI = 300 in smart_bleed.py",
          "DEFAULT_DPI = 300" in bleed_src or "TARGET_DPI = 300" in bleed_src,
          "DPI constant must be 300")

    has_set_dpi_bleed = "set_dpi(300, 300)" in bleed_src or "set_dpi(300,300)" in bleed_src or "set_dpi(dpi, dpi)" in bleed_src
    has_set_dpi_compile = "set_dpi(300, 300)" in compile_src or "set_dpi(dpi, dpi)" in compile_src or "set_dpi(render_dpi, render_dpi)" in compile_src
    check("G3.2", "PyMuPDF set_dpi configured in pipeline",
          has_set_dpi_bleed or has_set_dpi_compile,
          "PyMuPDF pixmap DPI must be set (via set_dpi) in smart_bleed.py or compile_press_pdf.py")

    check("G3.3", "HWResolution [300 300] in smart_bleed.py",
          "HWResolution [300 300]" in bleed_src or "HWResolution [{dpi} {dpi}]" in bleed_src,
          "GS HWResolution must be 300 DPI")

    has_pil_dpi = "dpi=(300, 300)" in bleed_src or "dpi=(300,300)" in bleed_src or "dpi=(TARGET_DPI, TARGET_DPI)" in bleed_src or "dpi=(dpi, dpi)" in bleed_src
    check("G3.4", "PIL DPI metadata configured in smart_bleed.py",
          has_pil_dpi,
          "PIL save must use DPI metadata (via dpi= parameter)")

    check("G3.5", "_prerasterize_pdf uses 300 DPI",
          "dpi=300" in compile_src or "dpi: int = 300" in compile_src,
          "Compile rasterization must use 300 DPI")


def gate_4_color_intent():
    print(f"\n{BOLD}Gate 4: Color Intent — Relative Colorimetric + KPreserve=2{RESET}")
    print(f"{'─'*60}")

    bleed_path = os.path.join(SERVER_DIR, "smart_bleed.py")
    with open(bleed_path, "r") as f:
        bleed_src = f.read()

    check("G4.1", "RenderIntent=1 (Relative Colorimetric)",
          "-dRenderIntent=1" in bleed_src,
          "GS must use Relative Colorimetric rendering intent")

    check("G4.2", "KPreserve=2 (Black preservation)",
          "-dKPreserve=2" in bleed_src,
          "GS must preserve black channel with KPreserve=2")

    check("G4.3", "ColorConversionStrategy=CMYK",
          "ColorConversionStrategy=CMYK" in bleed_src,
          "GS must force CMYK color conversion")

    check("G4.4", "BlackPointCompensation enabled",
          "BlackPointCompensation" in bleed_src,
          "GS must enable black point compensation")


def gate_5_frozen_functions():
    print(f"\n{BOLD}Gate 5: Frozen Functions & Architecture Laws{RESET}")
    print(f"{'─'*60}")

    compile_path = os.path.join(SERVER_DIR, "compile_press_pdf.py")
    bleed_path = os.path.join(SERVER_DIR, "smart_bleed.py")

    with open(compile_path, "r") as f:
        compile_src = f.read()
    with open(bleed_path, "r") as f:
        bleed_src = f.read()

    check("G5.1", "_enforce_final_mediabox exists in compile_press_pdf.py",
          "_enforce_final_mediabox" in compile_src,
          "FROZEN function must exist")

    check("G5.2", "_enforce_final_mediabox has geometry-pass marker",
          "LAST geometry pass" in compile_src and "PyMuPDF" in compile_src,
          "Must document final vector-safe Mediabox pass")

    check("G5.3", "Raster-first handoff in compile pipeline (PDF path)",
          "_prerasterize_pdf" in compile_src,
          "compile_press_pdf.py must pre-rasterize before CMYK conversion")

    check("G5.4", "Raster-first handoff in compile pipeline (image path)",
          "Raster-first handoff" in compile_src,
          "Must log raster-first handoff verification")

    check("G5.5", "Single-layer raster enforcement",
          "ghost layer prevention" in compile_src or "Single-layer raster" in compile_src,
          "Compile pipeline must enforce single-layer output")

    check("G5.6", "_resource_wipe preserves original_input_path",
          "original_input_path" in bleed_src and "_resource_wipe" in bleed_src,
          "_resource_wipe must preserve the original source file")

    check("G5.7", "gc.collect() before GS spawn",
          "gc.collect()" in bleed_src,
          "Must garbage collect before Ghostscript subprocess")

    check("G5.8", "_cap_pdf_image_dpi exists",
          "_cap_pdf_image_dpi" in bleed_src,
          "DPI cap function must exist for pre-GS image downsampling")


def gate_6_no_crop_safety():
    print(f"\n{BOLD}Gate 6: No-Crop Path Safety{RESET}")
    print(f"{'─'*60}")

    bleed_path = os.path.join(SERVER_DIR, "smart_bleed.py")
    compile_path = os.path.join(SERVER_DIR, "compile_press_pdf.py")

    with open(bleed_path, "r") as f:
        bleed_src = f.read()
    with open(compile_path, "r") as f:
        compile_src = f.read()

    check("G6.1", "preBleedPath set in apply_smart_bleed_to_pdf",
          'result["preBleedPath"]' in bleed_src or "result['preBleedPath']" in bleed_src,
          "PDF pipeline must return preBleedPath for downstream routing")

    check("G6.2", "Full-page crop_box initialization for no-crop PDFs",
          "full_page_crop" in bleed_src or "NO_CROP_FULL_PAGE" in bleed_src or "auto_crop_mockup_bounding_box" in bleed_src,
          "No-crop path must initialize a valid bounding box")

    crop_box_path = os.path.join(SERVER_DIR, "crop_box.py")
    check("G6.5", "crop_box.py helper exists for No Crop full-page fallback",
          os.path.exists(crop_box_path),
          "server/crop_box.py must provide ensure_full_page_crop_box")

    check("G6.3", "original_input_path preserved through pipeline",
          "original_input_path = input_path" in bleed_src,
          "Must capture original input before emergency/flatten rewrites")

    check("G6.4", "Compile accepts --crop-x/y/w/h arguments",
          "crop_x" in compile_src and "crop_w" in compile_src,
          "compile_press_pdf.py must accept crop coordinate arguments")


def print_final_report():
    total = len(violations) + len(passes)
    print(f"\n{'='*60}")
    print(f"{BOLD}{CYAN}  FLYERZ V1.0 — PRE-DEPLOY GATEKEEPER REPORT{RESET}")
    print(f"{'='*60}")
    print(f"  Total checks: {total}")
    print(f"  Passed:       {GREEN}{len(passes)}{RESET}")
    print(f"  Violations:   {RED if violations else GREEN}{len(violations)}{RESET}")
    print(f"{'─'*60}")

    if violations:
        print(f"\n  {RED}{BOLD}╔══════════════════════════════════════════════════════════╗{RESET}")
        print(f"  {RED}{BOLD}║  RULE VIOLATION DETECTED — APPLICATION MUST NOT START   ║{RESET}")
        print(f"  {RED}{BOLD}╚══════════════════════════════════════════════════════════╝{RESET}")
        print()
        for v in violations:
            print(f"  {RED}✗ [{v['rule']}] {v['desc']}{RESET}")
            if v["detail"]:
                print(f"    {YELLOW}-> {v['detail']}{RESET}")
        print()
        print(f"  {RED}{BOLD}FIX ALL VIOLATIONS BEFORE DEPLOYING.{RESET}")
    else:
        print(f"\n  {GREEN}{BOLD}╔══════════════════════════════════════════════════════════╗{RESET}")
        print(f"  {GREEN}{BOLD}║  ALL GATES PASSED ✓ — APPLICATION CLEARED TO START     ║{RESET}")
        print(f"  {GREEN}{BOLD}╚══════════════════════════════════════════════════════════╝{RESET}")
        print(f"  {GREEN}Flyerz V1.0 stability rules verified. Engine locked.{RESET}")

    print(f"{'='*60}\n")
    return len(violations) == 0


def main():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  FLYERZ V1.0 — PRE-DEPLOY GATEKEEPER{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  Verifying all stability rules before application start...")

    t0 = time.time()

    gate_1_test_suite()
    gate_2_gs_memory_leash()
    gate_3_dpi_metadata()
    gate_4_color_intent()
    gate_5_frozen_functions()
    gate_6_no_crop_safety()

    elapsed = time.time() - t0
    print(f"\n  Gatekeeper completed in {elapsed:.1f}s")

    all_clear = print_final_report()
    sys.exit(0 if all_clear else 1)


if __name__ == "__main__":
    main()
