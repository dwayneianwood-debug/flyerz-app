#!/usr/bin/env python3
"""Press-ready matrix. Builds fixtures, compiles press PDFs, and runs the same preflight."""

from __future__ import annotations

import os
import sys
import tempfile
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from press_ready_engine import (  # noqa: E402
    compile_raster_canvas,
    compile_vector_press,
    preflight_pdf,
    write_cmyk_pdf,
    _px,
)

SIZES = (
    ("card", 90.0, 50.0),
    ("dl", 99.0, 210.0),
    ("a5", 148.0, 210.0),
    ("a4", 210.0, 297.0),
    ("a3", 297.0, 420.0),
)
BLEEDS = (3.0, 5.0, 10.0)
ART_DIR = "/opt/cursor/artifacts"
os.makedirs(ART_DIR, exist_ok=True)

passes = []
fails = []
corners = []


def record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        passes.append(name)
        print(f"PASS {name}")
    else:
        fails.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def _marker(img: np.ndarray) -> np.ndarray:
    out = img.copy()
    h, w = out.shape[:2]
    out[h // 2 - 6:h // 2 + 6, w // 2 - 6:w // 2 + 6] = (255, 0, 255)
    return out


def make_rasters(folder: str) -> dict:
    rng = np.random.default_rng(7)
    photo = _marker(rng.integers(25, 210, (1600, 2400, 3), dtype=np.uint8))
    small = _marker(rng.integers(25, 210, (480, 640, 3), dtype=np.uint8))
    flat = np.full((1800, 2400, 3), (210, 70, 30), np.uint8)
    cv2.putText(flat, "OPEN", (16, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 4)
    cv2.putText(flat, "DAY", (16, flat.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 4)
    flat = _marker(flat)
    mixed = np.full((1400, 2000, 3), (40, 90, 200), np.uint8)
    mixed[mixed.shape[0] // 2:] = rng.integers(20, 200, (mixed.shape[0] - mixed.shape[0] // 2, 2000, 3), dtype=np.uint8)
    mixed = _marker(mixed)
    grad = np.zeros((1200, 1800, 3), np.uint8)
    for x in range(1800):
        grad[:, x] = (20, int(x * 180 / 1800) + 20, 70)
    grad = _marker(grad)
    pattern = np.zeros((900, 1400, 3), np.uint8)
    pattern[:] = (30, 40, 160)
    pattern[:, 0::18] = (30, 40, 220)
    pattern[:, 1::18] = (20, 30, 140)
    pattern = _marker(pattern)
    # High contrast so Haar / circle detection sees a face sitting on the right edge.
    face = np.full((900, 1400, 3), (40, 50, 60), np.uint8)
    cv2.circle(face, (1320, 450), 80, (210, 200, 190), -1)
    cv2.circle(face, (1290, 425), 10, (15, 15, 15), -1)
    cv2.circle(face, (1345, 425), 10, (15, 15, 15), -1)
    cv2.ellipse(face, (1320, 490), (22, 10), 0, 10, 170, (30, 30, 40), 3)
    face = _marker(face)
    files = {
        "photo": photo,
        "small-photo": small,
        "flat-text": flat,
        "mixed": mixed,
        "gradient": grad,
        "pattern": pattern,
        "face": face,
    }
    for name, shape in (("ai-1024", (1024, 1024)), ("ai-1024x1536", (1536, 1024)), ("ai-1792x1024", (1024, 1792))):
        tile = rng.integers(30, 200, (shape[0], shape[1], 3), dtype=np.uint8)
        files[name] = _marker(tile)
    # Already has 5mm bleed on a 90x50 card, at 300 DPI.
    dpi = 300.0
    bleed = _px(5, dpi)
    tw, th = _px(90, dpi), _px(50, dpi)
    ready = np.full((th + 2 * bleed, tw + 2 * bleed, 3), (20, 20, 220), np.uint8)
    ready[bleed:-bleed, bleed:-bleed] = (220, 40, 40)
    ready = _marker(ready)
    path = os.path.join(folder, "already.jpg")
    from PIL import Image
    rgb = cv2.cvtColor(ready, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(path, format="JPEG", quality=95, dpi=(300, 300))
    files["already-file"] = path
    return files


def make_vectors(folder: str) -> dict:
    import pymupdf as fitz

    paths = {}
    plain = os.path.join(folder, "vector.pdf")
    doc = fitz.open()
    page = doc.new_page(width=90 * 72 / 25.4, height=50 * 72 / 25.4)
    page.draw_rect(page.rect, fill=(0.15, 0.35, 0.75))
    page.insert_text((page.rect.width / 2 - 28, page.rect.height / 2), "HELLO", fontsize=14, fontname="helv", color=(1, 1, 1))
    doc.save(plain)
    doc.close()
    paths["vector"] = plain

    bled = os.path.join(folder, "vector-bleed.pdf")
    doc = fitz.open()
    media_w = (90 + 10) * 72 / 25.4
    media_h = (50 + 10) * 72 / 25.4
    page = doc.new_page(width=media_w, height=media_h)
    page.draw_rect(page.rect, fill=(0.85, 0.1, 0.1))
    inset = 5 * 72 / 25.4
    page.draw_rect(fitz.Rect(inset, inset, media_w - inset, media_h - inset), fill=(0.1, 0.25, 0.7))
    page.insert_text((media_w / 2 - 24, media_h / 2), "HELLO", fontsize=12, fontname="helv", color=(1, 1, 1))
    page.set_trimbox(fitz.Rect(inset, inset, media_w - inset, media_h - inset))
    page.set_bleedbox(page.rect)
    doc.save(bled)
    doc.close()
    paths["vector-bleed"] = bled

    raster_pdf = os.path.join(folder, "raster.pdf")
    doc = fitz.open()
    page = doc.new_page(width=90 * 72 / 25.4, height=50 * 72 / 25.4)
    tile = np.random.default_rng(3).integers(20, 200, (200, 360, 3), dtype=np.uint8)
    tile[90:110, 170:190] = (255, 0, 255)
    png = os.path.join(folder, "raster-src.png")
    cv2.imwrite(png, tile)
    page.insert_image(page.rect, filename=png)
    doc.save(raster_pdf)
    doc.close()
    paths["raster-pdf"] = raster_pdf

    ai_path = os.path.join(folder, "campaign.ai")
    doc = fitz.open()
    page = doc.new_page(width=90 * 72 / 25.4, height=50 * 72 / 25.4)
    page.draw_rect(page.rect, fill=(0.95, 0.75, 0.1))
    page.insert_text((page.rect.width / 2 - 28, page.rect.height / 2), "HELLO", fontsize=14, fontname="helv", color=(0, 0, 0))
    doc.save(ai_path)
    doc.close()
    paths["illustrator"] = ai_path

    eps_path = os.path.join(folder, "mark.eps")
    eps_path_ps = f"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 255 142
0.95 0.85 0.1 setrgbcolor
0 0 255 142 rectfill
0 0 0 setrgbcolor
/Helvetica findfont 18 scalefont setfont
90 70 moveto (HELLO) show
showpage
"""
    with open(eps_path, "w", encoding="ascii") as handle:
        handle.write(eps_path_ps)
    paths["eps"] = eps_path
    return paths


def _expect_attention(report: dict) -> bool:
    reason = (report.get("reason") or "").lower()
    return "dpi" in reason or "picture is about" in reason or "larger file" in reason


def check_raster(name: str, image, trim_w: float, trim_h: float, bleed: float, embedded_dpi=None) -> None:
    label = f"{name} {trim_w:g}x{trim_h:g} {bleed:g}mm"
    if isinstance(image, str):
        import cv2 as _cv
        image = _cv.imread(image, cv2.IMREAD_COLOR)
    t0 = time.time()
    canvas, report = compile_raster_canvas(image, trim_w, trim_h, bleed, embedded_dpi)
    if report.get("status") == "needs-attention":
        record(label + " (needs a larger file)", _expect_attention(report), report.get("reason", "no reason"))
        return
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "press.pdf")
    write_cmyk_pdf(canvas, path, trim_w, trim_h, bleed)
    checked = preflight_pdf(path, trim_w, trim_h, bleed, report)
    methods = [edge.get("method") for edge in checked.get("edges") or []]
    mirrored_blocked = any(
        edge.get("method") == "mirror" and (edge.get("text") or edge.get("face") or edge.get("logo"))
        for edge in checked.get("edges") or []
    )
    face_seen = any(edge.get("face") for edge in checked.get("edges") or [])
    face_mirrored = any(
        edge.get("face") and edge.get("method") == "mirror"
        for edge in checked.get("edges") or []
    )
    # The drawn face stays on the card. Larger pages cover-crop it off the trim.
    face_required = name == "face" and abs(trim_w - 90) < 0.1 and abs(trim_h - 50) < 0.1
    rescue = (checked.get("rescue") or {}) if face_required else {}
    rescue_ok = (not face_required) or (rescue.get("applied") is True and float(rescue.get("scale") or 1) >= 0.96)
    ok = (
        checked.get("passed") is True
        and checked.get("status") == "ready"
        and not mirrored_blocked
        and not face_mirrored
        and (face_seen if face_required else True)
        and rescue_ok
    )
    detail = checked.get("reason") or str(methods)
    if face_required and not face_seen:
        detail = "face at the edge was not detected"
    elif face_required and not rescue_ok:
        detail = "safe-zone rescue was not recorded"
    record(label, ok, detail)
    if name in ("photo", "flat-text", "mixed", "gradient", "pattern", "face", "ai-1024") and abs(bleed - 5) < 0.1 and abs(trim_w - 90) < 0.1:
        corners.append((label, canvas))
    if time.time() - t0 > 8:
        print(f"  slow {label} {time.time() - t0:.1f}s")


def check_vector(name: str, path: str, trim_w: float, trim_h: float, bleed: float, require_text: bool) -> None:
    label = f"{name} {trim_w:g}x{trim_h:g} {bleed:g}mm"
    folder = tempfile.mkdtemp()
    out = os.path.join(folder, "press.pdf")
    t0 = time.time()
    try:
        result = compile_vector_press(path, out, trim_w, trim_h, bleed)
    except Exception as exc:
        record(label, False, str(exc)[:180])
        return
    report = result.get("report") or {}
    if not result.get("used"):
        record(label, report.get("status") == "needs-attention" and bool(report.get("fix")), report.get("reason", "unused"))
        return
    import pymupdf as fitz
    doc = fitz.open(out)
    text = doc[0].get_text("text") or ""
    doc.close()
    text_ok = (not require_text) or ("HELLO" in text)
    ok = report.get("passed") is True and text_ok
    record(label, ok, (report.get("reason") or "") + ("" if text_ok else " text missing"))
    if name in ("vector", "vector-bleed", "illustrator", "eps") and abs(bleed - 5) < 0.1 and abs(trim_w - 90) < 0.1:
        import pymupdf as fitz
        doc = fitz.open(out)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.6, 0.6), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        doc.close()
        corners.append((label, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)))
    if time.time() - t0 > 8:
        print(f"  slow {label} {time.time() - t0:.1f}s")


def _four_corners(image: np.ndarray, side: int) -> np.ndarray:
    """Top-left, top-right, bottom-left, bottom-right of one press page."""
    h, w = image.shape[:2]
    take_y = min(side, h)
    take_x = min(side, w)
    tile = np.full((side * 2, side * 2, 3), 255, np.uint8)
    parts = (
        image[:take_y, :take_x],
        image[:take_y, w - take_x:],
        image[h - take_y:, :take_x],
        image[h - take_y:, w - take_x:],
    )
    spots = ((0, 0), (0, side), (side, 0), (side, side))
    for part, (y, x) in zip(parts, spots):
        tile[y:y + part.shape[0], x:x + part.shape[1]] = part
    return tile


def contact_sheet() -> None:
    if not corners:
        return
    cell = 160
    cols = 4
    rows = int(np.ceil(len(corners) / cols))
    sheet = np.full((rows * (cell + 28), cols * cell, 3), 245, np.uint8)
    for index, (label, image) in enumerate(corners):
        tile = _four_corners(image, cell // 2)
        r, c = divmod(index, cols)
        y = r * (cell + 28)
        x = c * cell
        sheet[y:y + cell, x:x + cell] = tile
        cv2.putText(sheet, label[:22], (x + 4, y + cell + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (20, 20, 20), 1)
    dest = os.path.join(ART_DIR, "bleed-corner-contact-sheet.png")
    cv2.imwrite(dest, sheet)
    print(f"contact {dest}")


def main() -> None:
    from press_ready_engine import is_full_page_crop
    record("full-page crop is not a hand crop", is_full_page_crop(0, 0, 1, 1) and not is_full_page_crop(0.1, 0.1, 0.4, 0.5), "")
    started = time.time()
    folder = tempfile.mkdtemp(prefix="bleed-matrix-")
    rasters = make_rasters(folder)
    vectors = make_vectors(folder)
    # One no-credit token check, then the rest with no token.
    os.environ["REPLICATE_API_TOKEN"] = "no-credit"
    check_raster("photo-no-credit", rasters["photo"], 90, 50, 5)
    os.environ.pop("REPLICATE_API_TOKEN", None)
    for name, image in rasters.items():
        if name == "already-file":
            for bleed in BLEEDS:
                check_raster("already", image, 90, 50, bleed, embedded_dpi=300)
            continue
        if name == "small-photo":
            check_raster(name, image, 297, 420, 5)
            check_raster(name, image, 90, 50, 5)
            continue
        for bleed in BLEEDS:
            for _size, width, height in SIZES:
                check_raster(name, image, width, height, bleed)
    for name, path in vectors.items():
        require = name != "raster-pdf"
        for bleed in BLEEDS:
            for _size, width, height in SIZES:
                check_vector(name, path, width, height, bleed, require)
    contact_sheet()
    print(f"\n{len(passes)} passed, {len(fails)} failed, {time.time() - started:.0f}s")
    if fails:
        print("FAILURES")
        for item in fails:
            print(" -", item)
        sys.exit(1)


if __name__ == "__main__":
    main()
