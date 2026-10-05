#!/usr/bin/env python3
"""K-only repair, one mirrored plate, and the cheap image walk."""

from __future__ import annotations

import os
import tempfile
import time

import numpy as np

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _plate(path: str):
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        images = page.get_images(full=True) or []
        pix = fitz.Pixmap(doc, int(images[0][0]))
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :4].copy()
        return arr
    finally:
        doc.close()


def test_konly_repair() -> None:
    import pymupdf as fitz
    from PIL import Image
    import io

    from client_file_audit import repair_cmyk_images
    from extra_checks import press_ink_facts

    folder = tempfile.mkdtemp(prefix="ink-")
    height, width = 400, 600
    arr = np.zeros((height, width, 4), np.uint8)
    arr[:, :] = (40, 20, 180, 10)
    arr[40:160, 40:260] = (200, 80, 30, 40)
    arr[220:250, 80:200] = (184, 178, 148, 171)
    # Hollow letter: the stroke fill is about 0.22, under the old 0.35 cutoff.
    arr[180:240, 320:400] = (184, 178, 148, 171)
    arr[184:236, 324:396] = (40, 20, 180, 10)
    arr[300:340, 80:160] = (255, 255, 255, 255)
    before = arr.copy()
    buf = io.BytesIO()
    Image.fromarray(arr, mode="CMYK").save(buf, format="TIFF", compression="raw", dpi=(300, 300))
    doc = fitz.open()
    page = doc.new_page(width=width * 72 / 300, height=height * 72 / 300)
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    path = os.path.join(folder, "rich.pdf")
    doc.save(path)
    doc.close()
    repair_cmyk_images(path, text_only=True)
    after = _plate(path)
    text = after[220:250, 80:200]
    hollow = after[180:240, 320:400]
    navy = after[40:160, 40:260]
    photo = after[0:30, 0:30]
    konly = int(((text[:, :, 3] >= 230) & (text[:, :, :3].sum(axis=-1) <= 13)).sum())
    hollow_k = int(((hollow[:, :, 3] >= 230) & (hollow[:, :, :3].sum(axis=-1) <= 13)).sum())
    tac = float(after.astype(np.int16).sum(axis=-1).max()) / 2.55
    navy_same = int(np.abs(navy.astype(int) - before[40:160, 40:260].astype(int)).sum()) == 0
    photo_same = int(np.abs(photo.astype(int) - before[0:30, 0:30].astype(int)).sum()) == 0
    facts = press_ink_facts(path)
    check("konly-pixels", konly > 30, str(konly))
    check("hollow-letter", hollow_k > 30, str(hollow_k))
    check("tac-capped", tac <= 300.5, f"{tac:.1f}")
    check("navy-unchanged", navy_same, "navy moved")
    check("photo-unchanged", photo_same, "photo moved")
    check("facts-konly", bool(facts.get("small_k")) and not facts.get("small_rich") and float(facts.get("max_tac") or 999) <= 300.5, str(facts))


def test_mirror_plate() -> None:
    import pymupdf as fitz
    from PIL import Image
    from press_ready_engine import compile_vector_press, press_window_seam

    folder = tempfile.mkdtemp(prefix="mirror-")
    image = np.full((900, 1200, 3), (40, 70, 140), np.uint8)
    image[:, 400:] = (170, 168, 166)
    png = os.path.join(folder, "page.png")
    Image.fromarray(image, mode="RGB").save(png)
    src = os.path.join(folder, "page.pdf")
    doc = fitz.open()
    page = doc.new_page(width=433.5, height=312)
    page.insert_image(page.rect, filename=png)
    page.insert_text((40, 40), "WEBSITE", fontsize=11, fontname="helv", color=(0, 0, 0))
    for box in (page.mediabox,):
        page.set_cropbox(page.rect)
        page.set_trimbox(page.rect)
        page.set_bleedbox(page.rect)
    doc.save(src)
    doc.close()
    out = os.path.join(folder, "press.pdf")
    result = compile_vector_press(src, out, 148, 105, 5)
    seams = press_window_seam(src, out)
    worst = max((float(row["page_max"]) if row.get("page_max") is not None else 99.0 for row in seams), default=99.0)
    doc = fitz.open(out)
    try:
        images = doc[0].get_images(full=True) or []
        raw = doc[0].read_contents().decode("latin1", "replace")
        text = doc[0].get_text("text") or ""
        pix = fitz.Pixmap(doc, int(images[0][0])) if images else None
        channels = 0 if pix is None else pix.n
    finally:
        doc.close()
    rgb = " rg" in raw or " RG" in raw or "DeviceRGB" in raw
    check("mirror-used", bool(result.get("used")) and worst < 5.0, f"used {result.get('used')} dE {worst}")
    check("mirror-one-image", len(images) == 1 and channels >= 4 and not rgb, f"images {len(images)} n {channels} rgb {rgb}")
    check("mirror-text", "WEBSITE" in text, text[:80])


def test_mask_skip() -> None:
    import pymupdf as fitz
    from PIL import Image
    from client_file_audit import _image_rows

    folder = tempfile.mkdtemp(prefix="mask-")
    png = os.path.join(folder, "big.png")
    Image.new("RGB", (1800, 1200), (20, 40, 80)).save(png)
    mask = os.path.join(folder, "mask.png")
    Image.new("L", (1800, 1200), 255).save(mask)
    doc = fitz.open()
    page = doc.new_page(width=400, height=280)
    page.insert_image(page.rect, filename=png)
    path = os.path.join(folder, "big.pdf")
    doc.save(path)
    doc.close()
    started = time.perf_counter()
    rows = _image_rows(path)
    elapsed = time.perf_counter() - started
    check("mask-fast", elapsed < 3.0 and rows and rows[0].get("decorative") is False, f"{elapsed:.2f}s {rows}")


def test_jpeg_plate_matches_konly() -> None:
    """Production plates are CMYK JPEGs. read_bytes throws, and the rewrite still has to stick."""
    import io

    import pymupdf as fitz
    from PIL import Image

    from client_file_audit import repair_cmyk_images

    folder = tempfile.mkdtemp(prefix="jpeg-ink-")
    height, width = 80, 120
    arr = np.zeros((height, width, 4), np.uint8)
    arr[:, :] = (10, 20, 30, 0)
    arr[20:55, 15:80] = (184, 178, 148, 171)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="CMYK").save(buf, format="JPEG", quality=95, subsampling=0)
    doc = fitz.open()
    page = doc.new_page(width=width * 72 / 300, height=height * 72 / 300)
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    path = os.path.join(folder, "plate.pdf")
    doc.save(path)
    doc.close()
    # A second reader is how the laptop kept the file locked. Close it before the replace
    # inside repair; this one is closed here so the assertion reads the replaced file.
    report = repair_cmyk_images(path, text_only=True)
    after = _plate(path)
    text = after[20:55, 15:80]
    konly = int(((text[:, :, 3] >= 230) & (text[:, :, :3].sum(axis=-1) <= 13)).sum())
    tac = float(after.astype(np.int16).sum(axis=-1).max()) / 2.55
    check("jpeg-konly", konly > 30 and int(report.get("images") or 0) >= 1, f"konly {konly} {report}")
    check("jpeg-tac", tac <= 300.5, f"{tac:.1f}")
    check("jpeg-log", os.path.exists(path + ".ink.json") and not report.get("error"), str(report.get("why")))


def test_rgb_skip_is_explained() -> None:
    import io

    import pymupdf as fitz
    from PIL import Image

    from client_file_audit import repair_cmyk_images

    folder = tempfile.mkdtemp(prefix="rgb-ink-")
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 0, 0)).save(buf, format="JPEG")
    doc = fitz.open()
    page = doc.new_page(width=40, height=30)
    page.insert_image(page.rect, stream=buf.getvalue())
    path = os.path.join(folder, "rgb.pdf")
    doc.save(path)
    doc.close()
    report = repair_cmyk_images(path, text_only=True)
    skips = report.get("skips") or []
    explained = report.get("why") == "not-cmyk" or any(row.get("skip") == "not-cmyk" for row in skips)
    check("rgb-explained", int(report.get("images") or 0) == 0 and explained, str(report))


def test_repair_is_one_pass() -> None:
    from client_file_audit import _repair_array

    height, width = 900, 1200
    arr = np.full((height, width, 4), (30, 20, 10, 0), np.uint8)
    for y in range(8, height - 8, 18):
        for x in range(8, width - 8, 22):
            arr[y:y + 8, x:x + 10] = (184, 178, 148, 171)
    started = time.perf_counter()
    stats = _repair_array(arr, 300.0, text_only=True)
    elapsed = time.perf_counter() - started
    konly = int(((arr[:, :, 3] >= 230) & (arr[:, :, :3].sum(axis=-1) <= 13)).sum())
    check(
        "repair-fast",
        elapsed < 2.5 and int(stats.get("painted") or 0) > 100 and konly > 100,
        f"{elapsed:.2f}s painted {stats.get('painted')} components {stats.get('components')}",
    )


def test_large_plate_repair_stays_quick() -> None:
    """A flyer-sized plate used to spend most of a second in full-frame copies."""
    from client_file_audit import _repair_array

    height, width = 3600, 5000
    arr = np.zeros((height, width, 4), np.uint8)
    arr[:, :] = (20, 10, 5, 0)
    arr[80:420, 80:480] = (210, 200, 190, 220)
    for index in range(0, 1800, 36):
        arr[600 + index:612 + index, 180:320] = (184, 178, 148, 171)
    started = time.perf_counter()
    stats = _repair_array(arr, 400.0, text_only=True, tac_limit=2.80)
    elapsed = time.perf_counter() - started
    tac = float(arr.astype(np.uint16).sum(axis=-1).max()) / 2.55
    check(
        "flyer-sized-repair",
        elapsed < 0.5 and int(stats.get("painted") or 0) > 100 and tac <= 280.5,
        f"{elapsed:.2f}s painted {stats.get('painted')} tac {tac:.1f}",
    )


def test_mirror_drops_fringe() -> None:
    from press_ready_engine import _mirror_pad, _quiet_outlier_edge

    arr = np.full((48, 64, 4), 90, np.uint8)
    arr[-1, :, :] = 250
    arr[:, -1, :] = 230
    out, _pads = _mirror_pad(arr, 96, 80, 2, 2, 2, 2)
    row_jump = float(np.abs(np.diff(out.astype(np.int16).mean(axis=(1, 2)))).max())
    col_jump = float(np.abs(np.diff(out.astype(np.int16).mean(axis=(0, 2)))).max())
    check("no-row-hairline", row_jump < 30, f"{row_jump:.1f}")
    check("no-col-hairline", col_jump < 30, f"{col_jump:.1f}")
    grad = np.zeros((48, 64, 4), np.uint8)
    for index in range(48):
        grad[index, :, :] = index * 3
    quiet = _quiet_outlier_edge(grad)
    check("gradient-kept", int(quiet[-1, 0, 0]) == int(grad[-1, 0, 0]), f"{int(quiet[-1, 0, 0])} vs {int(grad[-1, 0, 0])}")
    cross = np.full((48, 64, 4), 80, np.uint8)
    cross[-1, -1] = (20, 220, 220, 0)
    quiet_cross = _quiet_outlier_edge(cross)
    check("corner-cross", int(quiet_cross[-1, -1, 1]) < 100, str(int(quiet_cross[-1, -1, 1])))
    pale = np.full((48, 64, 4), 2, np.uint8)
    pale[-1, -1] = (8, 8, 8, 2)
    quiet_pale = _quiet_outlier_edge(pale)
    check("pale-corner-kept", int(quiet_pale[-1, -1, 0]) == 8, str(quiet_pale[-1, -1].tolist()))
    # A short red rule whose channel average stays under 40. The mirror used to double it into a cross.
    darker = np.array([20, 80, 70, 40], np.uint8)
    red = np.array([5, 115, 105, 25], np.uint8)
    rule = np.full((48, 64, 4), darker, np.uint8)
    rule[-1, -10:] = red
    rule[-10:, -1] = red
    rule[-2, -1] = red
    mean_gap = float(np.mean(np.abs(red.astype(np.int16) - darker.astype(np.int16))))
    quiet_rule = _quiet_outlier_edge(rule)
    corner = quiet_rule[-1, -1]
    arm = quiet_rule[-1, -4]
    stem = quiet_rule[-4, -1]
    kept = quiet_rule[-6, -8]
    # A two-pixel rule is the anti-aliased edge. Both pixels have to go or the mirror still draws it.
    thick = np.full((48, 64, 4), darker, np.uint8)
    thick[-2:, -12:] = red
    thick[-12:, -2:] = red
    quiet_thick = _quiet_outlier_edge(thick)
    padded, _pads = _mirror_pad(rule, 80, 64, 2, 2, 2, 2)
    mirrored = padded[-8:, -8:]
    still_red = int(np.min(np.max(np.abs(mirrored.astype(np.int16) - red.astype(np.int16)), axis=-1)))
    check(
        "red-cross-corner",
        mean_gap < 40
        and int(np.max(np.abs(corner.astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(arm.astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(stem.astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(kept.astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(quiet_thick[-1, -1].astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(quiet_thick[-2, -2].astype(int) - darker.astype(int)))) < 5
        and int(np.max(np.abs(quiet_thick[-6, -8].astype(int) - darker.astype(int)))) < 5
        and still_red > 20,
        f"mean {mean_gap:.1f} corner {corner.tolist()} mirror-gap {still_red}",
    )


def test_plate_buffer_is_the_image() -> None:
    """The press plate is edited in the same buffer the JPEG is saved from."""
    from PIL import Image

    buf = bytearray(8 * 6 * 4)
    image = Image.frombuffer("CMYK", (8, 6), buf, "raw", "CMYK", 0, 1)
    arr = np.frombuffer(buf, np.uint8).reshape(6, 8, 4)
    arr[3, 4] = (0, 0, 0, 255)
    check("buffer-shared", image.getpixel((4, 3)) == (0, 0, 0, 255), str(image.getpixel((4, 3))))


def test_locale_pin() -> None:
    from host_paths import pin_c_locale

    os.environ["LC_ALL"] = "en_ZA.UTF-8"
    os.environ["LC_NUMERIC"] = "en_ZA.UTF-8"
    seen = pin_c_locale()
    check("locale-c", "LC_ALL" not in os.environ and os.environ.get("LC_NUMERIC") == "C", str(seen))


def test_screen_proof_is_direct() -> None:
    """The before picture is a PyMuPDF render, not an OpenCV import."""
    import subprocess
    import sys

    import pymupdf as fitz

    folder = tempfile.mkdtemp(prefix="screen-proof-")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(fitz.Rect(40, 40, 120, 100), color=(0.8, 0.1, 0.1), fill=(0.8, 0.1, 0.1))
    src = os.path.join(folder, "job.pdf")
    doc.save(src)
    doc.close()
    out = os.path.join(folder, "job_proof.png")
    script = os.path.join(os.path.dirname(__file__), "screen_proof.py")
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, script, src, out, "1"], capture_output=True, text=True, timeout=20)
    elapsed = time.perf_counter() - started
    written = os.path.join(folder, "job_proof1.png")
    from PIL import Image

    white = False
    if os.path.exists(written):
        corner = Image.open(written).convert("RGB").getpixel((2, 2))
        white = min(corner) > 240
    check(
        "screen-proof-direct",
        proc.returncode == 0 and elapsed < 3 and white and "PAGES 1" in proc.stdout and "cv2" not in (proc.stderr or ""),
        f"{elapsed:.2f}s rc {proc.returncode} white {white} {proc.stderr[-200:]}",
    )


def test_screen_proof_is_fast() -> None:
    import pymupdf as fitz
    from smart_bleed import generate_visual_proof

    folder = tempfile.mkdtemp(prefix="proof-")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=300, height=200)
        page.insert_text((40, 80), "Before", fontsize=24, fontname="helv", color=(0, 0, 0))
    src = os.path.join(folder, "job.pdf")
    doc.save(src)
    doc.close()
    started = time.perf_counter()
    result = generate_visual_proof(src, os.path.join(folder, "proof.png"))
    elapsed = time.perf_counter() - started
    pages = result.get("proofPaths") or []
    check("proof-fast", bool(result.get("success")) and elapsed < 8 and len(pages) == 2, f"{elapsed:.2f}s {result}")


def main() -> None:
    test_konly_repair()
    test_jpeg_plate_matches_konly()
    test_rgb_skip_is_explained()
    test_repair_is_one_pass()
    test_large_plate_repair_stays_quick()
    test_plate_buffer_is_the_image()
    test_mirror_drops_fringe()
    test_locale_pin()
    test_screen_proof_is_direct()
    test_screen_proof_is_fast()
    test_mirror_plate()
    test_mask_skip()
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("press ink checks passed")


if __name__ == "__main__":
    main()
