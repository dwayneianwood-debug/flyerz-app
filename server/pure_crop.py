#!/usr/bin/env python3
"""
Pure crop tool for the Manual Crop menu.

Crops JPG, PNG, and PDF without the prepress pipeline: it does not call gs,
does not convert colour space, and does not resample or rasterise vector PDFs.

Rectangle JPEG crops use jpegtran (lossless, MCU-aligned) when that lines up.
Otherwise JPEG is re-encoded at quality 100 with the original chroma
subsampling, EXIF, and ICC. PNG crops are lossless. PDF crops only rewrite the
page boxes (and an optional ellipse clip) so text and vectors stay selectable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

import pikepdf
from PIL import Image, ImageChops, ImageDraw, ImageOps

Image.MAX_IMAGE_PIXELS = 300_000_000

PREVIEW_MAX_PX = 1800
DEFAULT_DPI = 300.0


def _fail(message: str) -> None:
    print(json.dumps({"success": False, "error": message}))
    sys.exit(1)


def displayed_size(orientation: int, raw_w: int, raw_h: int) -> tuple[int, int]:
    if orientation in (5, 6, 7, 8):
        return raw_h, raw_w
    return raw_w, raw_h


def map_visual_pixel_to_raw(orientation: int, raw_w: int, raw_h: int, dx: int, dy: int) -> tuple[int, int]:
    """Inverse of Pillow's EXIF transpose. dx/dy are displayed pixel indices."""
    if orientation == 2:
        return raw_w - 1 - dx, dy
    if orientation == 3:
        return raw_w - 1 - dx, raw_h - 1 - dy
    if orientation == 4:
        return dx, raw_h - 1 - dy
    if orientation == 5:
        return dy, dx
    if orientation == 6:
        return dy, raw_h - 1 - dx
    if orientation == 7:
        return raw_w - 1 - dy, raw_h - 1 - dx
    if orientation == 8:
        return raw_w - 1 - dy, dx
    return dx, dy


def map_visual_crop_to_raw(
    orientation: int, raw_w: int, raw_h: int, x: int, y: int, w: int, h: int
) -> tuple[int, int, int, int]:
    """Map a displayed-pixel crop to raw JPEG storage pixels (x, y, w, h)."""
    disp_w, disp_h = displayed_size(orientation, raw_w, raw_h)
    x = max(0, min(int(x), disp_w - 1))
    y = max(0, min(int(y), disp_h - 1))
    w = max(1, min(int(w), disp_w - x))
    h = max(1, min(int(h), disp_h - y))
    corners = (
        (x, y),
        (x + w - 1, y),
        (x, y + h - 1),
        (x + w - 1, y + h - 1),
    )
    raw_pts = [map_visual_pixel_to_raw(orientation, raw_w, raw_h, cx, cy) for cx, cy in corners]
    left = min(px for px, _ in raw_pts)
    top = min(py for _, py in raw_pts)
    right = max(px for px, _ in raw_pts) + 1
    bottom = max(py for _, py in raw_pts) + 1
    return left, top, right - left, bottom - top


def read_jpeg_geometry(path: str) -> dict[str, Any]:
    """SOF sampling. MCU size is 8 * max horizontal/vertical sample factor."""
    with open(path, "rb") as handle:
        data = handle.read()
    i = 0
    size = len(data)
    while i < size - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD8:
            i += 2
            continue
        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD9, 0x01):
            i += 2
            continue
        if i + 4 > size:
            break
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            if i + 10 > size:
                break
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            ncomp = data[i + 9]
            max_h = 1
            max_v = 1
            for comp in range(ncomp):
                hv = data[i + 11 + comp * 3]
                max_h = max(max_h, hv >> 4)
                max_v = max(max_v, hv & 0x0F)
            if max_h == 1 and max_v == 1:
                subsampling = 0
                label = "4:4:4"
            elif max_h == 2 and max_v == 1:
                subsampling = 1
                label = "4:2:2"
            else:
                subsampling = 2
                label = "4:2:0"
            return {
                "width": width,
                "height": height,
                "components": ncomp,
                "mcu_w": 8 * max_h,
                "mcu_h": 8 * max_v,
                "subsampling": subsampling,
                "subsampling_label": label,
            }
        i += 2 + length
    raise ValueError("Could not read JPEG frame header")


def jpeg_crop_is_mcu_aligned(geom: dict[str, Any], x: int, y: int, w: int, h: int) -> bool:
    mcu_w = geom["mcu_w"]
    mcu_h = geom["mcu_h"]
    img_w = geom["width"]
    img_h = geom["height"]
    if x % mcu_w != 0 or y % mcu_h != 0:
        return False
    if (x + w) != img_w and w % mcu_w != 0:
        return False
    if (y + h) != img_h and h % mcu_h != 0:
        return False
    if w <= 0 or h <= 0 or x + w > img_w or y + h > img_h:
        return False
    return True


def image_dpi(im: Image.Image) -> tuple[float, float]:
    dpi = im.info.get("dpi")
    if isinstance(dpi, tuple) and len(dpi) >= 2:
        dx, dy = float(dpi[0] or 0), float(dpi[1] or 0)
        if dx > 1 and dy > 1:
            return dx, dy
    return DEFAULT_DPI, DEFAULT_DPI


def exif_orientation(im: Image.Image) -> int:
    try:
        orientation = im.getexif().get(274, 1)
    except Exception:
        orientation = 1
    try:
        orientation = int(orientation)
    except (TypeError, ValueError):
        return 1
    if orientation < 1 or orientation > 8:
        return 1
    return orientation


def inspect_image(path: str) -> dict[str, Any]:
    im = Image.open(path)
    im.load()
    raw_w, raw_h = im.size
    orientation = exif_orientation(im)
    disp_w, disp_h = displayed_size(orientation, raw_w, raw_h)
    dpi_x, dpi_y = image_dpi(im)
    return {
        "width": disp_w,
        "height": disp_h,
        "rawWidth": raw_w,
        "rawHeight": raw_h,
        "orientation": orientation,
        "dpiX": dpi_x,
        "dpiY": dpi_y,
        "mode": im.mode,
    }


def inspect_pdf(path: str) -> list[dict[str, Any]]:
    import fitz

    doc = fitz.open(path)
    pages = []
    for index, page in enumerate(doc):
        rect = page.rect
        pages.append(
            {
                "page": index + 1,
                "widthPt": rect.width,
                "heightPt": rect.height,
                "rotation": int(page.rotation or 0),
            }
        )
    doc.close()
    return pages


def render_pdf_preview(path: str, output_path: str, page_number: int) -> dict[str, Any]:
    import fitz

    doc = fitz.open(path)
    if page_number < 1 or page_number > len(doc):
        doc.close()
        raise ValueError("Page is out of range")
    page = doc[page_number - 1]
    rect = page.rect
    long_edge = max(rect.width, rect.height) or 1
    scale = min(PREVIEW_MAX_PX / long_edge, 3.0)
    scale = max(scale, 1.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pix.save(output_path)
    info = {
        "previewWidth": pix.width,
        "previewHeight": pix.height,
        "widthPt": rect.width,
        "heightPt": rect.height,
        "scale": scale,
    }
    doc.close()
    return info


def _clamp_visual_crop(x: float, y: float, w: float, h: float, bounds_w: float, bounds_h: float) -> tuple[float, float, float, float]:
    x = max(0.0, min(float(x), bounds_w))
    y = max(0.0, min(float(y), bounds_h))
    w = max(1.0, min(float(w), bounds_w - x))
    h = max(1.0, min(float(h), bounds_h - y))
    return x, y, w, h


def _jpegtran_crop(src: str, dest: str, x: int, y: int, w: int, h: int) -> bool:
    jpegtran = shutil.which("jpegtran")
    if not jpegtran:
        return False
    proc = subprocess.run(
        [jpegtran, "-copy", "all", "-perfect", "-crop", f"{w}x{h}+{x}+{y}", "-outfile", dest, src],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def _save_jpeg(im: Image.Image, dest: str, subsampling: int, dpi: tuple[float, float], icc: bytes | None, exif: bytes | None) -> None:
    kwargs: dict[str, Any] = {
        "format": "JPEG",
        "quality": 100,
        "subsampling": subsampling,
        "dpi": dpi,
        "optimize": False,
    }
    if icc:
        kwargs["icc_profile"] = icc
    if exif:
        kwargs["exif"] = exif
    im.save(dest, **kwargs)


def _ellipse_mask(size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)
    return mask


def _apply_ellipse(im: Image.Image, outside: str) -> Image.Image:
    mask = _ellipse_mask(im.size)
    if outside == "transparent":
        rgba = im.convert("RGBA")
        alpha = ImageChops.multiply(rgba.getchannel("A"), mask)
        rgba.putalpha(alpha)
        return rgba
    if im.mode == "CMYK":
        background = Image.new("CMYK", im.size, (0, 0, 0, 0))
        background.paste(im, mask=mask)
        return background
    if im.mode == "L":
        background = Image.new("L", im.size, 255)
        background.paste(im, mask=mask)
        return background
    rgb = im.convert("RGB") if im.mode not in ("RGB",) else im
    background = Image.new("RGB", im.size, (255, 255, 255))
    background.paste(rgb, mask=mask)
    return background


def _oriented_crop(path: str, x: int, y: int, w: int, h: int) -> tuple[Image.Image, Image.Image, int]:
    """Return (visual crop, original image, orientation). Visual pixels are upright."""
    im = Image.open(path)
    im.load()
    orientation = exif_orientation(im)
    shown = ImageOps.exif_transpose(im)
    disp_w, disp_h = shown.size
    x, y, w, h = (int(v) for v in _clamp_visual_crop(x, y, w, h, disp_w, disp_h))
    cropped = shown.crop((x, y, x + w, y + h))
    return cropped, im, orientation


def crop_jpeg(src: str, dest: str, x: int, y: int, w: int, h: int, shape: str) -> dict[str, Any]:
    im = Image.open(src)
    im.load()
    orientation = exif_orientation(im)
    raw_w, raw_h = im.size
    disp_w, disp_h = displayed_size(orientation, raw_w, raw_h)
    x, y, w, h = (int(round(v)) for v in _clamp_visual_crop(x, y, w, h, disp_w, disp_h))
    dpi = image_dpi(im)
    icc = im.info.get("icc_profile")
    method = "reencode-q100"

    if shape == "rect":
        raw_x, raw_y, raw_w, raw_h_crop = map_visual_crop_to_raw(orientation, raw_w, raw_h, x, y, w, h)
        geom = read_jpeg_geometry(src)
        if jpeg_crop_is_mcu_aligned(geom, raw_x, raw_y, raw_w, raw_h_crop) and _jpegtran_crop(
            src, dest, raw_x, raw_y, raw_w, raw_h_crop
        ):
            return {
                "success": True,
                "method": "jpegtran",
                "width": w,
                "height": h,
                "dpiX": dpi[0],
                "dpiY": dpi[1],
            }

    cropped, _original, _orientation = _oriented_crop(src, x, y, w, h)
    if shape == "ellipse":
        cropped = _apply_ellipse(cropped, "white")
    subsampling = 2
    try:
        subsampling = int(read_jpeg_geometry(src)["subsampling"])
    except Exception:
        subsampling = 2
    exif_bytes = None
    try:
        exif = cropped.getexif()
        if 274 in exif:
            del exif[274]
        exif_bytes = exif.tobytes() or None
    except Exception:
        exif_bytes = None
    if cropped.mode == "CMYK":
        pass
    elif cropped.mode not in ("RGB", "L"):
        cropped = cropped.convert("RGB")
    _save_jpeg(cropped, dest, subsampling, dpi, icc if isinstance(icc, bytes) else None, exif_bytes)
    return {
        "success": True,
        "method": method,
        "width": cropped.size[0],
        "height": cropped.size[1],
        "dpiX": dpi[0],
        "dpiY": dpi[1],
    }


def crop_png(src: str, dest: str, x: int, y: int, w: int, h: int, shape: str) -> dict[str, Any]:
    im = Image.open(src)
    im.load()
    dpi = image_dpi(im)
    icc = im.info.get("icc_profile")
    width, height = im.size
    x, y, w, h = (int(round(v)) for v in _clamp_visual_crop(x, y, w, h, width, height))
    cropped = im.crop((x, y, x + w, y + h))
    if shape == "ellipse":
        cropped = _apply_ellipse(cropped, "transparent")
    kwargs: dict[str, Any] = {"format": "PNG", "dpi": dpi}
    if isinstance(icc, bytes):
        kwargs["icc_profile"] = icc
    cropped.save(dest, **kwargs)
    return {
        "success": True,
        "method": "png-lossless",
        "width": cropped.size[0],
        "height": cropped.size[1],
        "dpiX": dpi[0],
        "dpiY": dpi[1],
    }


def _pdf_box_from_visual(path: str, page_number: int, x: float, y: float, w: float, h: float) -> tuple[tuple[float, float, float, float], Any]:
    """Visual top-left crop (what the user drags) to PDF user-space box."""
    import fitz
    import io

    doc = fitz.open(path)
    try:
        if page_number < 1 or page_number > len(doc):
            raise ValueError("Page is out of range")
        page = doc[page_number - 1]
        x, y, w, h = _clamp_visual_crop(x, y, w, h, page.rect.width, page.rect.height)
        visual = fitz.Rect(x, y, x + w, y + h) & page.rect
        if visual.width < 0.5 or visual.height < 0.5:
            raise ValueError("Crop is outside the page")
        page.set_cropbox(visual)
        # Fitz converts the visual rect into PDF coordinates only when the file is written.
        written = pikepdf.open(io.BytesIO(doc.tobytes()))
        box = written.pages[page_number - 1].cropbox
        written.close()
        return (float(box[0]), float(box[1]), float(box[2]), float(box[3])), visual
    finally:
        doc.close()


def _ellipse_clip_ops(x0: float, y0: float, x1: float, y1: float) -> bytes:
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    rx = abs(x1 - x0) / 2.0
    ry = abs(y1 - y0) / 2.0
    k = 0.5522847498307936
    kx = rx * k
    ky = ry * k
    ops = (
        "q\n"
        f"{cx + rx:.4f} {cy:.4f} m\n"
        f"{cx + rx:.4f} {cy + ky:.4f} {cx + kx:.4f} {cy + ry:.4f} {cx:.4f} {cy + ry:.4f} c\n"
        f"{cx - kx:.4f} {cy + ry:.4f} {cx - rx:.4f} {cy + ky:.4f} {cx - rx:.4f} {cy:.4f} c\n"
        f"{cx - rx:.4f} {cy - ky:.4f} {cx - kx:.4f} {cy - ry:.4f} {cx:.4f} {cy - ry:.4f} c\n"
        f"{cx + kx:.4f} {cy - ry:.4f} {cx + rx:.4f} {cy - ky:.4f} {cx + rx:.4f} {cy:.4f} c\n"
        "W n\n"
    )
    return ops.encode("ascii")


def _page_content_bytes(page: pikepdf.Page) -> bytes:
    contents = page.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        return b"\n".join(bytes(stream.read_bytes()) for stream in contents)
    return bytes(contents.read_bytes())


def crop_pdf_boxes(src: str, dest: str, page_number: int, x: float, y: float, w: float, h: float, shape: str) -> dict[str, Any]:
    box, visual = _pdf_box_from_visual(src, page_number, x, y, w, h)
    pdf_src = pikepdf.open(src)
    out = pikepdf.Pdf.new()
    out.pages.append(pdf_src.pages[page_number - 1])
    page = out.pages[0]
    array = pikepdf.Array([box[0], box[1], box[2], box[3]])
    page.MediaBox = array
    page.CropBox = array
    page.TrimBox = array
    page.BleedBox = array
    page.ArtBox = array
    if shape == "ellipse":
        original = _page_content_bytes(page)
        wrapped = _ellipse_clip_ops(*box) + b"\n" + original + b"\nQ\n"
        page.Contents = out.make_stream(wrapped)
    out.save(dest)
    pdf_src.close()
    out.close()
    return {
        "success": True,
        "method": "pdf-boxes" if shape == "rect" else "pdf-boxes-ellipse-clip",
        "widthPt": float(visual.width),
        "heightPt": float(visual.height),
        "box": list(box),
    }


def _page_size_pt(width_px: int, height_px: int, dpi_x: float, dpi_y: float) -> tuple[float, float]:
    return (width_px * 72.0 / dpi_x, height_px * 72.0 / dpi_y)


def embed_image_pdf(image_path: str, dest: str, dpi_x: float, dpi_y: float) -> None:
    """Embed an image in a one-page PDF. JPEG bytes stay DCT-encoded."""
    import img2pdf

    with Image.open(image_path) as im:
        width_px, height_px = im.size
    page_w, page_h = _page_size_pt(width_px, height_px, dpi_x, dpi_y)
    layout = img2pdf.get_layout_fun(pagesize=(page_w, page_h), fit=img2pdf.FitMode.fill)
    pdf_bytes = img2pdf.convert(image_path, layout_fun=layout)
    with open(dest, "wb") as handle:
        handle.write(pdf_bytes)


def image_to_pdf(src: str, dest: str, x: float, y: float, w: float, h: float, shape: str, file_type: str) -> dict[str, Any]:
    temp_image = dest + ".img"
    try:
        if file_type == "jpg":
            info = crop_jpeg(src, temp_image, int(round(x)), int(round(y)), int(round(w)), int(round(h)), shape)
            embed_image_pdf(temp_image, dest, info["dpiX"], info["dpiY"])
        else:
            info = crop_png(src, temp_image, int(round(x)), int(round(y)), int(round(w)), int(round(h)), "rect")
            if shape == "ellipse":
                im = Image.open(temp_image)
                im.load()
                flat = _apply_ellipse(im, "white")
                dpi = (info["dpiX"], info["dpiY"])
                flat.save(temp_image, format="PNG", dpi=dpi)
                info["width"], info["height"] = flat.size
            embed_image_pdf(temp_image, dest, info["dpiX"], info["dpiY"])
        return {
            "success": True,
            "method": "image-pdf",
            "width": info["width"],
            "height": info["height"],
            "dpiX": info["dpiX"],
            "dpiY": info["dpiY"],
        }
    finally:
        if os.path.exists(temp_image):
            os.remove(temp_image)


def pdf_to_jpeg(src: str, dest: str, page_number: int, x: float, y: float, w: float, h: float, shape: str, dpi: float) -> dict[str, Any]:
    import fitz

    dpi = float(dpi) if dpi and dpi > 1 else DEFAULT_DPI
    doc = fitz.open(src)
    try:
        if page_number < 1 or page_number > len(doc):
            raise ValueError("Page is out of range")
        page = doc[page_number - 1]
        x, y, w, h = _clamp_visual_crop(x, y, w, h, page.rect.width, page.rect.height)
        clip = fitz.Rect(x, y, x + w, y + h)
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        mode = "RGB" if pix.n >= 3 else "L"
        im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    if shape == "ellipse":
        im = _apply_ellipse(im, "white")
    im.save(dest, format="JPEG", quality=100, subsampling=0, dpi=(dpi, dpi))
    return {
        "success": True,
        "method": "pdf-render-jpeg",
        "width": im.size[0],
        "height": im.size[1],
        "dpiX": dpi,
        "dpiY": dpi,
    }


def png_to_jpeg(src: str, dest: str, x: float, y: float, w: float, h: float, shape: str) -> dict[str, Any]:
    cropped, _original, _orientation = _oriented_crop(src, int(round(x)), int(round(y)), int(round(w)), int(round(h)))
    if shape == "ellipse":
        cropped = _apply_ellipse(cropped, "white")
    elif cropped.mode in ("RGBA", "LA"):
        background = Image.new("RGB", cropped.size, (255, 255, 255))
        background.paste(cropped, mask=cropped.getchannel("A"))
        cropped = background
    elif cropped.mode != "RGB":
        cropped = cropped.convert("RGB")
    dpi = image_dpi(Image.open(src))
    _save_jpeg(cropped, dest, 0, dpi, None, None)
    return {
        "success": True,
        "method": "png-to-jpeg-q100",
        "width": cropped.size[0],
        "height": cropped.size[1],
        "dpiX": dpi[0],
        "dpiY": dpi[1],
    }


def save_crop(src: str, dest: str, options: dict[str, Any]) -> dict[str, Any]:
    file_type = options["fileType"]
    output = options["output"]
    shape = options.get("shape") or "rect"
    if shape not in ("rect", "ellipse"):
        raise ValueError("Shape must be rect or ellipse")
    if output not in ("original", "pdf", "jpg"):
        raise ValueError("Output must be original, pdf, or jpg")
    page = int(options.get("page") or 1)
    x = float(options["x"])
    y = float(options["y"])
    w = float(options["width"])
    h = float(options["height"])
    jpg_dpi = float(options.get("jpgDpi") or DEFAULT_DPI)

    if file_type == "pdf" and output in ("original", "pdf"):
        return crop_pdf_boxes(src, dest, page, x, y, w, h, shape)
    if file_type == "pdf" and output == "jpg":
        return pdf_to_jpeg(src, dest, page, x, y, w, h, shape, jpg_dpi)
    if file_type == "jpg" and output in ("original", "jpg"):
        return crop_jpeg(src, dest, int(round(x)), int(round(y)), int(round(w)), int(round(h)), shape)
    if file_type == "jpg" and output == "pdf":
        return image_to_pdf(src, dest, x, y, w, h, shape, "jpg")
    if file_type == "png" and output == "original":
        return crop_png(src, dest, int(round(x)), int(round(y)), int(round(w)), int(round(h)), shape)
    if file_type == "png" and output == "jpg":
        return png_to_jpeg(src, dest, x, y, w, h, shape)
    if file_type == "png" and output == "pdf":
        return image_to_pdf(src, dest, x, y, w, h, shape, "png")
    raise ValueError(f"Cannot save {file_type} as {output}")


def main() -> None:
    if len(sys.argv) < 3:
        _fail("Usage: pure_crop.py <inspect|preview|save> ...")
    command = sys.argv[1]
    try:
        if command == "inspect":
            path = sys.argv[2]
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                pages = inspect_pdf(path)
                print(json.dumps({"success": True, "fileType": "pdf", "pageCount": len(pages), "pages": pages}))
            elif ext in (".jpg", ".jpeg"):
                info = inspect_image(path)
                print(json.dumps({"success": True, "fileType": "jpg", "pageCount": 1, **info}))
            elif ext == ".png":
                info = inspect_image(path)
                print(json.dumps({"success": True, "fileType": "png", "pageCount": 1, **info}))
            else:
                _fail("Only JPG, PNG, and PDF files can be cropped.")
            return

        if command == "preview":
            path, output_path, page = sys.argv[2], sys.argv[3], int(sys.argv[4])
            info = render_pdf_preview(path, output_path, page)
            print(json.dumps({"success": True, **info}))
            return

        if command == "save":
            src, dest, raw_options = sys.argv[2], sys.argv[3], sys.argv[4]
            options = json.loads(raw_options)
            result = save_crop(src, dest, options)
            result["success"] = True
            print(json.dumps(result))
            return

        _fail(f"Unknown command: {command}")
    except Exception as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
