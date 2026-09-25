#!/usr/bin/env python3
"""Quality checks for the pure crop tool. Not part of the prepress pipeline."""

import io
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import pikepdf
from PIL import Image, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pure_crop import (  # noqa: E402
    crop_jpeg,
    crop_pdf_boxes,
    crop_png,
    image_to_pdf,
    map_visual_crop_to_raw,
    pdf_to_jpeg,
    read_jpeg_geometry,
    save_crop,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        raise SystemExit(f"FAIL {name}: {detail}")
    print(f"ok {name}")


def test_orientation_mapping() -> None:
    raw = Image.new("RGB", (32, 24))
    px = raw.load()
    for y in range(24):
        for x in range(32):
            px[x, y] = ((x * 7) % 256, (y * 11) % 256, (x * 3 + y) % 256)
    for orientation, transpose in (
        (2, Image.FLIP_LEFT_RIGHT),
        (3, Image.ROTATE_180),
        (6, Image.ROTATE_270),
        (8, Image.ROTATE_90),
    ):
        shown = raw.transpose(transpose)
        x, y, w, h = 4, 3, 10, 8
        expected = shown.crop((x, y, x + w, y + h))
        rx, ry, rw, rh = map_visual_crop_to_raw(orientation, raw.width, raw.height, x, y, w, h)
        got = raw.crop((rx, ry, rx + rw, ry + rh)).transpose(transpose)
        check(
            f"orientation-{orientation}-crop",
            got.size == expected.size and list(got.getdata()) == list(expected.getdata()),
            f"mapped {(rx, ry, rw, rh)} vs visual {expected.size}",
        )


def test_jpeg_lossless() -> None:
    rng = np.random.default_rng(4)
    arr = rng.integers(0, 256, (128, 96, 3), dtype=np.uint8)
    src = Image.fromarray(arr, "RGB")
    folder = tempfile.mkdtemp(prefix="purecrop-jpg-")
    path = os.path.join(folder, "noise.jpg")
    src.save(path, quality=60, subsampling=2)
    decoded = np.array(Image.open(path))
    x, y, w, h = 16, 16, 64, 48
    out = os.path.join(folder, "crop.jpg")
    info = crop_jpeg(path, out, x, y, w, h, "rect")
    check("jpeg-method-lossless", info["method"] == "jpegtran", info["method"])
    cropped = np.array(Image.open(out))
    expected = decoded[y : y + h, x : x + w]
    interior = np.abs(cropped.astype(int) - expected.astype(int))[1:-1, 1:-1]
    check(
        "jpeg-pixels-identical",
        cropped.shape == expected.shape and int(interior.max()) == 0,
        f"shape {cropped.shape} interior max {interior.max() if interior.size else 'empty'}",
    )
    check(
        "jpeg-qtables-unchanged",
        Image.open(path).quantization == Image.open(out).quantization,
    )
    geom_in = read_jpeg_geometry(path)
    geom_out = read_jpeg_geometry(out)
    check("jpeg-subsampling-kept", geom_in["subsampling_label"] == geom_out["subsampling_label"], geom_out["subsampling_label"])

    # Not MCU aligned: exact requested size, still a JPEG.
    out2 = os.path.join(folder, "odd.jpg")
    info2 = crop_jpeg(path, out2, 3, 5, 50, 41, "rect")
    odd = Image.open(out2)
    check("jpeg-unaligned-size", odd.size == (50, 41), str(odd.size))
    check("jpeg-unaligned-fallback", info2["method"] == "reencode-q100", info2["method"])


def test_png_lossless_and_ellipse() -> None:
    rng = np.random.default_rng(9)
    arr = rng.integers(0, 256, (80, 60, 4), dtype=np.uint8)
    arr[:, :, 3] = 255
    src = Image.fromarray(arr, "RGBA")
    folder = tempfile.mkdtemp(prefix="purecrop-png-")
    path = os.path.join(folder, "art.png")
    src.save(path)
    out = os.path.join(folder, "crop.png")
    crop_png(path, out, 10, 8, 40, 30, "rect")
    got = np.array(Image.open(out))
    expected = arr[8 : 38, 10 : 50]
    check("png-pixels-identical", got.shape == expected.shape and np.array_equal(got, expected))

    ellipse = os.path.join(folder, "ellipse.png")
    crop_png(path, ellipse, 10, 8, 40, 30, "ellipse")
    ell = np.array(Image.open(ellipse))
    check("png-ellipse-corner-transparent", int(ell[0, 0, 3]) == 0, str(ell[0, 0]))
    check("png-ellipse-center-kept", np.array_equal(ell[15, 20, :3], expected[15, 20, :3]) and ell[15, 20, 3] == 255)


def test_pdf_vector_crop() -> None:
    import fitz

    folder = tempfile.mkdtemp(prefix="purecrop-pdf-")
    src_path = os.path.join(folder, "page.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((30, 40), "VECTOR TEXT STAYS", fontsize=18)
    page.insert_text((30, 280), "OUTSIDE THE CROP", fontsize=14)
    doc.save(src_path)
    doc.close()

    out = os.path.join(folder, "cropped.pdf")
    # Visual top band includes the first line and excludes the bottom line.
    info = crop_pdf_boxes(src_path, out, 1, 10, 15, 220, 80, "rect")
    check("pdf-box-method", info["method"] == "pdf-boxes")
    opened = fitz.open(out)
    text = opened[0].get_text()
    check("pdf-text-selectable", "VECTOR TEXT STAYS" in text, repr(text))
    check("pdf-page-size", abs(opened[0].rect.width - 220) < 0.2 and abs(opened[0].rect.height - 80) < 0.2, str(opened[0].rect))
    fonts = opened[0].get_fonts()
    check("pdf-still-has-font", len(fonts) > 0, str(fonts))
    opened.close()

    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        content = page.Contents.read_bytes() if not isinstance(page.Contents, pikepdf.Array) else b"".join(
            bytes(part.read_bytes()) for part in page.Contents
        )
        check("pdf-content-has-text-operator", b"Tj" in content or b"TJ" in content)
        boxes = [page.mediabox, page.cropbox, page.trimbox, page.bleedbox]
        check(
            "pdf-boxes-match",
            all(abs(float(box[2]) - float(box[0]) - 220) < 0.2 and abs(float(box[3]) - float(box[1]) - 80) < 0.2 for box in boxes),
            str(boxes),
        )

    ellipse = os.path.join(folder, "ellipse.pdf")
    crop_pdf_boxes(src_path, ellipse, 1, 10, 15, 220, 80, "ellipse")
    with pikepdf.open(ellipse) as pdf:
        content = pdf.pages[0].Contents.read_bytes()
        check("pdf-ellipse-keeps-text", b"Tj" in content or b"TJ" in content)
        check("pdf-ellipse-has-clip", b"W n" in content)
    ell_doc = fitz.open(ellipse)
    ell_text = ell_doc[0].get_text()
    check("pdf-ellipse-text-selectable", "VECTOR TEXT STAYS" in ell_text, repr(ell_text))
    ell_doc.close()

    jpg = os.path.join(folder, "render.jpg")
    rendered = pdf_to_jpeg(src_path, jpg, 1, 10, 15, 220, 80, "rect", 300)
    expected_w = round(220 * 300 / 72)
    expected_h = round(80 * 300 / 72)
    check("pdf-jpg-size", abs(rendered["width"] - expected_w) <= 2 and abs(rendered["height"] - expected_h) <= 2, str(rendered))
    check("pdf-jpg-opens", Image.open(jpg).format == "JPEG")


def test_image_pdf_keeps_jpeg() -> None:
    rng = np.random.default_rng(2)
    arr = rng.integers(0, 256, (96, 64, 3), dtype=np.uint8)
    folder = tempfile.mkdtemp(prefix="purecrop-embed-")
    src = os.path.join(folder, "photo.jpg")
    Image.fromarray(arr, "RGB").save(src, quality=70, subsampling=2, dpi=(300, 300))
    cropped_jpg = os.path.join(folder, "cropped.jpg")
    crop_jpeg(src, cropped_jpg, 16, 16, 48, 32, "rect")
    pdf_path = os.path.join(folder, "wrapped.pdf")
    image_to_pdf(src, pdf_path, 16, 16, 48, 32, "rect", "jpg")
    with pikepdf.open(pdf_path) as pdf:
        image = list(pdf.pages[0].Resources.XObject.values())[0]
        stream = image.read_raw_bytes()
        original = open(cropped_jpg, "rb").read()
        check("pdf-jpeg-dct", str(image.Filter) == "/DCTDecode" and stream == original, f"{image.Filter} {len(stream)} vs {len(original)}")
        box = [float(v) for v in pdf.pages[0].mediabox]
        check("pdf-page-matches-dpi", abs(box[2] - 48 * 72 / 300) < 0.05 and abs(box[3] - 32 * 72 / 300) < 0.05, str(box))


def test_png_pdf_pixels() -> None:
    arr = np.zeros((40, 30, 3), dtype=np.uint8)
    arr[:, :] = (20, 40, 60)
    arr[0, 0] = (255, 0, 0)
    folder = tempfile.mkdtemp(prefix="purecrop-pngpdf-")
    src = os.path.join(folder, "flat.png")
    Image.fromarray(arr, "RGB").save(src, dpi=(300, 300))
    pdf_path = os.path.join(folder, "flat.pdf")
    image_to_pdf(src, pdf_path, 5, 4, 20, 16, "rect", "png")
    import fitz

    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    got = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    expected = arr[4 : 20, 5 : 25]
    doc.close()
    check("png-pdf-size", got.shape[1] == 20 and got.shape[0] == 16, str(got.shape))
    check("png-pdf-pixels", np.array_equal(got[:, :, :3], expected), "pixel mismatch")
    with pikepdf.open(pdf_path) as pdf:
        image = list(pdf.pages[0].Resources.XObject.values())[0]
        check("png-pdf-not-jpeg", "/DCTDecode" not in str(image.Filter), str(image.Filter))


def test_script_has_no_ghostscript() -> None:
    text = open(os.path.join(os.path.dirname(__file__), "pure_crop.py"), encoding="utf-8").read().lower()
    check("no-ghostscript", "ghostscript" not in text and "gswin" not in text)


def test_cli_roundtrip() -> None:
    folder = tempfile.mkdtemp(prefix="purecrop-cli-")
    src = os.path.join(folder, "cli.png")
    Image.new("RGB", (30, 20), (9, 8, 7)).save(src)
    script = os.path.join(os.path.dirname(__file__), "pure_crop.py")
    proc = subprocess.run([sys.executable, script, "inspect", src], capture_output=True, text=True, check=False)
    info = json.loads(proc.stdout)
    check("cli-inspect", proc.returncode == 0 and info["width"] == 30, proc.stdout + proc.stderr)
    out = os.path.join(folder, "out.png")
    options = json.dumps({"fileType": "png", "output": "original", "shape": "rect", "x": 2, "y": 3, "width": 10, "height": 8, "page": 1})
    proc = subprocess.run([sys.executable, script, "save", src, out, options], capture_output=True, text=True, check=False)
    saved = json.loads(proc.stdout)
    check("cli-save", proc.returncode == 0 and saved["success"] and Image.open(out).size == (10, 8), proc.stdout + proc.stderr)


def test_exif_orientation_jpeg_roundtrip() -> None:
    raw = Image.new("RGB", (64, 48), (10, 20, 30))
    draw_px = raw.load()
    for x in range(64):
        draw_px[x, 0] = (255, 0, 0)
    exif = Image.Exif()
    exif[274] = 6
    folder = tempfile.mkdtemp(prefix="purecrop-ori-")
    path = os.path.join(folder, "turned.jpg")
    raw.save(path, quality=95, subsampling=0, exif=exif.tobytes())
    shown = ImageOps.exif_transpose(Image.open(path))
    # Crop a displayed region. 4:4:4 MCU is 8px. Displayed size is 48x64.
    out = os.path.join(folder, "turned-crop.jpg")
    info = crop_jpeg(path, out, 8, 8, 24, 32, "rect")
    result = ImageOps.exif_transpose(Image.open(out))
    expected = shown.crop((8, 8, 32, 40))
    check("oriented-jpeg-size", result.size == expected.size, f"{result.size} vs {expected.size} method={info['method']}")
    # q95 source is not pixel-identical after a second view, but jpegtran path should match the decoded visual crop.
    if info["method"] == "jpegtran":
        check("oriented-jpeg-pixels", list(result.getdata()) == list(expected.getdata()))


def main() -> None:
    # The tool must not import the press compiler.
    source = open(os.path.join(os.path.dirname(__file__), "pure_crop.py"), encoding="utf-8").read()
    if "compile_press_pdf" in source or "manual_crop" in source:
        raise SystemExit("pure_crop.py must stay independent of the prepress crop pipeline")
    test_script_has_no_ghostscript()
    test_orientation_mapping()
    test_jpeg_lossless()
    test_png_lossless_and_ellipse()
    test_pdf_vector_crop()
    test_image_pdf_keeps_jpeg()
    test_png_pdf_pixels()
    test_cli_roundtrip()
    test_exif_orientation_jpeg_roundtrip()
    # save_crop dispatch used by the HTTP route
    folder = tempfile.mkdtemp(prefix="purecrop-dispatch-")
    src = os.path.join(folder, "d.png")
    Image.new("RGB", (16, 16), (1, 2, 3)).save(src)
    out = os.path.join(folder, "d.jpg")
    result = save_crop(src, out, {"fileType": "png", "output": "jpg", "shape": "rect", "x": 1, "y": 1, "width": 8, "height": 8, "page": 1})
    check("dispatch-jpg", result["success"] and Image.open(out).size == (8, 8) and Image.open(out).format == "JPEG")
    print("all pure crop checks passed")


if __name__ == "__main__":
    main()
