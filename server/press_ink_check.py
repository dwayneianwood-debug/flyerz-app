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


def main() -> None:
    test_konly_repair()
    test_mirror_plate()
    test_mask_skip()
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("press ink checks passed")


if __name__ == "__main__":
    main()
