#!/usr/bin/env python3
"""End-to-end checks for sales quick mode. Engines are called, not rewritten."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from quick_print import BLEED_MM, decide_light, make_print_ready, too_small

ART = os.environ.get("QUICK_PRINT_ARTIFACTS", "/opt/cursor/artifacts")
FONT = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _font(size: int):
    if os.path.exists(FONT):
        return ImageFont.truetype(FONT, size)
    return ImageFont.load_default()


def _docx(path: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>Please print this</w:t></w:r></w:p></w:body></w:document>",
        )


def _flyer(path: str) -> None:
    image = Image.new("RGB", (1024, 1024), (24, 72, 140))
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, 80, 940, 400), fill=(240, 120, 20))
    draw.rounded_rectangle((120, 460, 900, 900), radius=36, fill=(12, 28, 70), outline=(210, 170, 90), width=8)
    draw.text((180, 560), "MARKET DAY", font=_font(72), fill=(255, 236, 180))
    draw.text((180, 700), "SATURDAY", font=_font(54), fill=(255, 255, 255))
    image.save(path, format="PNG")


def _wide(path: str) -> None:
    image = Image.new("RGB", (1800, 700), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 120, 700), fill=(220, 20, 20))
    draw.rectangle((1680, 0, 1800, 700), fill=(20, 40, 220))
    draw.rectangle((800, 250, 1000, 450), fill=(20, 180, 60))
    image.save(path, format="PNG")


def _bled_pdf(path: str) -> None:
    import pymupdf as fitz

    mm = 72.0 / 25.4
    bleed = 5 * mm
    trim_w = 148 * mm
    trim_h = 210 * mm
    doc = fitz.open()
    page = doc.new_page(width=trim_w + 2 * bleed, height=trim_h + 2 * bleed)
    page.draw_rect(page.rect, color=None, fill=(0.75, 0.08, 0.08))
    page.draw_rect(fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h), color=None, fill=(0.1, 0.55, 0.2))
    page.insert_text((bleed + 36, bleed + 80), "ALREADY BLEED", fontsize=28, fontname="helv", color=(1, 1, 1))
    trim = fitz.Rect(bleed, bleed, bleed + trim_w, bleed + trim_h)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    doc.save(path)
    doc.close()


def _render(path: str, dest: str) -> np.ndarray:
    import cv2
    import pymupdf as fitz

    doc = fitz.open(path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cv2.imwrite(dest, bgr)
    doc.close()
    return bgr


def _run(src: str, name: str, product: str = "a5") -> dict:
    out = tempfile.mkdtemp(prefix=f"quick-{name}-")
    return make_print_ready(src, out, 148, 210, product, "A5", filename=os.path.basename(src))


def test_rules() -> None:
    office = decide_light({"kind": "office"})
    check("word-rule", office["light"] == "red" and "Word" in office["clientMessage"])
    small = decide_light({"tooSmall": True, "tooSmallMessage": "too small please"})
    check("small-rule", small["light"] == "red" and "too small" in small["clientMessage"])
    green = decide_light({"compiled": True, "enginePassed": True, "upscale": 1.2, "aspectDelta": 0.01})
    check("green-rule", green["light"] == "green" and green["reasons"] == [])
    amber = decide_light({"compiled": True, "enginePassed": True, "aspectExtended": True, "aspectDelta": 0.4, "upscale": 1})
    check("amber-rule", amber["light"] == "amber" and any("extended" in line for line in amber["reasons"]))
    quiet = decide_light({"compiled": True, "enginePassed": True, "aspectExtended": True, "aspectDelta": 0.03, "upscale": 1.4})
    check("small-aspect-stays-green", quiet["light"] == "green", str(quiet))
    doubtful = decide_light({
        "compiled": True,
        "enginePassed": True,
        "upscale": 1,
        "ocrDoubtful": True,
        "ocrDoubtfulReason": "Some marks did not look like real words, so they were left unchanged. Glance at the picture.",
    })
    check("doubtful-amber", doubtful["light"] == "amber" and any("left unchanged" in line for line in doubtful["reasons"]), str(doubtful))
    check("a5-80px-too-small", too_small(80, 80, 148, 210))
    check("a5-1024-not-too-small", not too_small(1024, 1024, 148, 210))
    check("bleed-constant", BLEED_MM == 5)


def test_word(root: str) -> None:
    path = os.path.join(root, "brief.docx")
    _docx(path)
    result = _run(path, "word")
    check("word-red", result["light"] == "red", result["light"])
    check("word-message", "Word" in result["clientMessage"] and "PDF" in result["clientMessage"], result["clientMessage"])
    check("word-no-press", not result.get("pressPath"))


def test_corrupt(root: str) -> None:
    path = os.path.join(root, "broken.jpg")
    with open(path, "wb") as handle:
        handle.write(b"this is not a jpeg file at all, it is just text")
    result = _run(path, "corrupt")
    check("corrupt-red", result["light"] == "red" and not result.get("pressPath"), result["light"])


def test_too_small(root: str) -> None:
    path = os.path.join(root, "tiny.png")
    Image.new("RGB", (80, 80), (200, 20, 20)).save(path)
    result = _run(path, "tiny")
    check("tiny-red", result["light"] == "red" and "small" in result["clientMessage"].lower(), result["clientMessage"])
    check("tiny-no-press", not result.get("pressPath"))


def test_existing(root: str) -> None:
    path = os.path.join(root, "already.pdf")
    _bled_pdf(path)
    result = _run(path, "existing")
    check("existing-green", result["light"] == "green", f"{result['light']} {result.get('reasons')}")
    media = float(result.get("mediaWidthMm") or 0)
    check("existing-bleed-not-doubled", 156 <= media <= 161, str(result.get("mediaWidthMm")))
    check("existing-not-20mm", abs(media - 168) > 4, str(media))
    check("existing-kept", result.get("existingBleedKept") is True or any("not added again" in line for line in result["decisions"]), str(result["decisions"]))
    press = result["pressPath"]
    import pymupdf as fitz
    doc = fitz.open(press)
    text = doc[0].get_text("text")
    doc.close()
    check("existing-text-kept", "ALREADY" in text, text[:120])
    check("existing-not-traced", "traced" not in " ".join(result.get("decisions") or []).lower(), str(result.get("decisions"))[:300])
    _render(press, os.path.join(ART, "quick-print-existing-bleed.png"))


def test_wide(root: str) -> None:
    path = os.path.join(root, "banner.png")
    _wide(path)
    result = _run(path, "wide")
    check("wide-amber", result["light"] == "amber", f"{result['light']} {result.get('reasons')}")
    check("wide-reason", any("extended" in line.lower() for line in result["reasons"]), str(result["reasons"]))
    check("wide-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000))
    check("wide-bleed", abs(float(result.get("mediaWidthMm") or 0) - 158) < 3, str(result.get("mediaWidthMm")))
    rendered = _render(result["pressPath"], os.path.join(ART, "quick-print-wrong-aspect.png"))
    check("wide-portrait", rendered.shape[0] > rendered.shape[1], str(rendered.shape))
    rgb = rendered[:, :, ::-1].astype(np.int16)
    red = (rgb[:, :, 0] > 140) & (rgb[:, :, 0] > rgb[:, :, 1] + 40) & (rgb[:, :, 0] > rgb[:, :, 2] + 40)
    blue = (rgb[:, :, 2] > 70) & (rgb[:, :, 2] > rgb[:, :, 0] + 15) & (rgb[:, :, 2] > rgb[:, :, 1] + 10)
    green = (rgb[:, :, 1] > 90) & (rgb[:, :, 1] > rgb[:, :, 0] + 30) & (rgb[:, :, 1] > rgb[:, :, 2] + 20)
    check("wide-keeps-both-ends", int(red.sum()) > 30 and int(blue.sum()) > 30, f"red {int(red.sum())} blue {int(blue.sum())}")
    import cv2
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(green.astype(np.uint8), 8)
    big = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_AREA] > 40]
    check("wide-has-centre", len(big) == 1, f"{len(big)} green marks")
    blob = big[0]
    ratio = float(blob[cv2.CC_STAT_WIDTH]) / float(max(1, blob[cv2.CC_STAT_HEIGHT]))
    check(
        "wide-not-stretched",
        0.8 <= ratio <= 1.25,
        f"{int(blob[cv2.CC_STAT_WIDTH])}x{int(blob[cv2.CC_STAT_HEIGHT])} ratio {ratio:.2f}",
    )
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    images = doc[0].get_images()
    doc.close()
    check("wide-one-image", len(images) == 1, str(len(images)))


def test_flyer(root: str) -> None:
    path = os.path.join(root, "ai-flyer.png")
    _flyer(path)
    result = _run(path, "flyer")
    check("flyer-light", result["light"] in ("green", "amber"), f"{result['light']} {result.get('reasons')}")
    check("flyer-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000))
    media_w = float(result.get("mediaWidthMm") or 0)
    media_h = float(result.get("mediaHeightMm") or 0)
    trim_w = float(result.get("trimWidthMm") or 0)
    trim_h = float(result.get("trimHeightMm") or 0)
    check("flyer-trim", abs(trim_w - 148) < 2 and abs(trim_h - 210) < 2, f"{trim_w}x{trim_h}")
    check("flyer-bleed-5", abs(media_w - 158) < 3 and abs(media_h - 220) < 3, f"{media_w}x{media_h}")
    check("flyer-not-doubled", abs(media_w - 168) > 4, str(media_w))
    rendered = _render(result["pressPath"], os.path.join(ART, "quick-print-ai-flyer.png"))
    check("flyer-not-blank", float(rendered.std()) > 12, str(rendered.std()))
    if result.get("proofPng") and os.path.exists(result["proofPng"]):
        shutil.copyfile(result["proofPng"], os.path.join(ART, "quick-print-ai-flyer-proof.png"))
    joined = " ".join(result.get("decisions") or [])
    check("flyer-records-decisions", "5 mm" in joined and "vector" in joined.lower(), joined[:700])
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    text = page.get_text("text") or ""
    fonts = page.get_fonts()
    images = page.get_images()
    drawings = page.get_drawings()
    info = doc.extract_image(images[0][0]) if images else {}
    inset = float(page.trimbox.x0) * 25.4 / 72.0
    doc.close()
    fonts_on = os.environ.get("VECTOR_FONTS", "").strip().lower() in ("1", "on", "true", "yes")
    if fonts_on:
        check("flyer-vector-text", "MARKET" in text.upper(), text.replace("\n", " | ")[:240])
        check("flyer-embedded-font", bool(fonts) and all("+" in str(item[3]) for item in fonts), str(fonts)[:240])
    else:
        check("flyer-traced", "traced" in joined.lower() and len(drawings) >= 1, f"drawings {len(drawings)} {joined[:240]}")
    check("flyer-cmyk-image", info.get("colorspace") == 4, str(info.get("colorspace")))
    check("flyer-trim-inset", abs(inset - 5) < 0.5, f"{inset:.2f}")


def test_shapes(root: str) -> None:
    """Square flyer. The circle stays in the picture. The real lines become vector type."""
    from ai_rebuild_check import _draw_shape_flyer, _orange_disc

    path = os.path.join(root, "shapes.png")
    _draw_shape_flyer(path)
    out = tempfile.mkdtemp(prefix="quick-shapes-")
    result = make_print_ready(path, out, 148, 148, "custom", "148 × 148 mm", filename="shapes.png")
    check("shapes-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000))
    joined = " ".join(result.get("decisions") or [])
    check("shapes-sets-type", "vector" in joined.lower(), joined[:500])
    rendered = _render(result["pressPath"], os.path.join(ART, "quick-print-shape-circle.png"))
    disc = _orange_disc(rendered)
    check("shapes-circle", disc is not None, "" if disc else "no orange disc")
    if disc is not None:
        area, aspect, fill = disc
        check("shapes-circle-round", 0.8 <= aspect <= 1.25 and fill >= 0.6 and area > 2000, f"area {area} aspect {aspect:.2f} fill {fill:.2f}")
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    text = doc[0].get_text("text") or ""
    images = doc[0].get_images()
    drawings = doc[0].get_drawings()
    doc.close()
    fonts_on = os.environ.get("VECTOR_FONTS", "").strip().lower() in ("1", "on", "true", "yes")
    if fonts_on:
        check("shapes-vector-words", "MARKET" in text.upper(), text.replace("\n", " | ")[:180])
    else:
        check("shapes-traced", "traced" in joined.lower() and len(drawings) >= 1, f"drawings {len(drawings)}")
    check("shapes-still-a-picture", len(images) >= 1, str(len(images)))


def _column_variance(strip: np.ndarray) -> float:
    import cv2

    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if gray.shape[0] < 2:
        return 0.0
    return float(np.mean(np.var(gray, axis=0)))


def _streak(strip: np.ndarray, outward_axis: int) -> float:
    """How much the band is striped along the edge.

    Average along the outward direction, remove the slow gradient, and report
    the std of what is left. Column-shaped noise survives that average.
    """
    import cv2

    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if gray.shape[outward_axis] < 1 or gray.size == 0:
        return 0.0
    profile = gray.mean(axis=outward_axis)
    length = int(profile.shape[0])
    if length < 8:
        return float(np.std(profile))
    sigma = max(8.0, 0.04 * length)
    smooth = cv2.GaussianBlur(profile.reshape(1, -1), (0, 0), sigma).ravel()
    return float(np.std(profile - smooth))


def _no_more_streak_than_the_edge(name: str, band: np.ndarray, edge: np.ndarray, outward_axis: int, seam_at_end: bool = True) -> None:
    # The few pixels against the picture repeat the real rim. The stripes a
    # customer would see are the rest of the band.
    drop = 6
    if band.shape[outward_axis] > drop + 8:
        if outward_axis == 0:
            band = band[:-drop] if seam_at_end else band[drop:]
        else:
            band = band[:, :-drop] if seam_at_end else band[:, drop:]
    depth = min(band.shape[outward_axis], edge.shape[outward_axis])
    if outward_axis == 0:
        band_part = band[:depth]
        edge_part = edge[:depth]
    else:
        band_part = band[:, :depth]
        edge_part = edge[:, :depth]
    band_s = _streak(band_part, outward_axis)
    edge_s = _streak(edge_part, outward_axis)
    check(
        f"{name}-no-columns",
        band_s <= edge_s * 1.05 + 0.25,
        f"band {band_s:.3f} edge {edge_s:.3f}",
    )


def _gradient_picture(width: int, height: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy = np.linspace(0, 1, height)[:, None]
    xx = np.linspace(0, 1, width)[None, :]
    field = yy * 0.65 + xx * 0.35
    picture = np.zeros((height, width, 3), np.float32)
    picture[..., 0] = 30 + field * 90
    picture[..., 1] = 24 + field * 40
    picture[..., 2] = 90 + (1.0 - field) * 100
    picture += rng.normal(0, 9, picture.shape)
    return np.clip(picture, 0, 255).astype(np.uint8)


def _texture_picture(width: int, height: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    picture = rng.normal(110, 28, (height, width, 3))
    import cv2
    picture = cv2.GaussianBlur(picture.astype(np.float32), (0, 0), 1.1)
    return np.clip(picture, 0, 255).astype(np.uint8)


def _flat_picture(width: int, height: int) -> np.ndarray:
    return np.full((height, width, 3), (40, 90, 160), np.uint8)


def _photo_picture(width: int, height: int, seed: int) -> np.ndarray:
    """Slow patches plus fine grain, the way a photograph looks at a small size."""
    import cv2

    rng = np.random.default_rng(seed)
    coarse = rng.normal(0, 1, (max(2, height // 10), max(2, width // 10), 3)).astype(np.float32)
    coarse = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC)
    coarse = cv2.GaussianBlur(coarse, (0, 0), 1.6)
    fine = rng.normal(0, 1, (height, width, 3)).astype(np.float32)
    return np.clip(118 + coarse * 16 + fine * 7, 0, 255).astype(np.uint8)


def test_extended_band_does_not_streak() -> None:
    """The shape gap must not turn edge noise into columns, or repeat one row."""
    import cv2
    from quick_print import _extend_to_product

    for name, picture in (
        ("gradient", _gradient_picture(360, 360, 5)),
        ("texture", _texture_picture(360, 360, 9)),
        ("flat", _flat_picture(360, 360)),
        ("photo", _photo_picture(360, 360, 13)),
    ):
        fitted, extended, _delta = _extend_to_product(picture, 148, 210, "", [])
        check(f"{name}-extended", extended is True and fitted.shape[0] > picture.shape[0], str(fitted.shape))
        pad = (fitted.shape[0] - picture.shape[0]) // 2
        check(f"{name}-pad", pad >= 8, str(pad))
        band = fitted[:pad]
        neighbour = picture[:pad]
        _no_more_streak_than_the_edge(name, band, neighbour, outward_axis=0)
        bottom = fitted[-pad:]
        _no_more_streak_than_the_edge(f"{name}-bottom", bottom, picture[-pad:], outward_axis=0, seam_at_end=False)
        if name in ("gradient", "texture", "photo"):
            band_var = _column_variance(band)
            old = cv2.copyMakeBorder(picture, pad, fitted.shape[0] - picture.shape[0] - pad, 0, 0, cv2.BORDER_REPLICATE)
            old_var = _column_variance(old[:pad])
            check(
                f"{name}-not-a-copied-row",
                band_var > old_var + 0.5,
                f"band {band_var:.2f} copied-row {old_var:.2f}",
            )
        middle = fitted[pad:pad + picture.shape[0]]
        check(f"{name}-keeps-picture", middle.shape == picture.shape and np.array_equal(middle, picture))

    # Portrait picture on a landscape product: the gap is at the sides.
    portrait = _photo_picture(240, 420, 21)
    fitted, extended, _delta = _extend_to_product(portrait, 210, 148, "", [])
    check("side-extended", extended is True and fitted.shape[1] > portrait.shape[1], str(fitted.shape))
    side = (fitted.shape[1] - portrait.shape[1]) // 2
    check("side-pad", side >= 8, str(side))
    _no_more_streak_than_the_edge("side-left", fitted[:, :side], portrait[:, :side], outward_axis=1)
    _no_more_streak_than_the_edge("side-right", fitted[:, -side:], portrait[:, -side:], outward_axis=1, seam_at_end=False)
    middle = fitted[:, side:side + portrait.shape[1]]
    # A tall side pad feathers a few millimetres into the picture. The interior stays exact.
    inset = int(round(8.0 * fitted.shape[0] / 148.0))
    check(
        "side-keeps-picture",
        middle.shape == portrait.shape and np.array_equal(middle[:, inset:-inset], portrait[:, inset:-inset]),
    )


def _orange(bgr: np.ndarray) -> np.ndarray:
    """The test disc is pure magenta, which this gradient never is."""
    rgb = bgr[:, :, ::-1].astype(np.int16)
    return (rgb[:, :, 0] > 220) & (rgb[:, :, 2] > 220) & (rgb[:, :, 1] < 40)


def _disc_picture(top: int, size: int = 360) -> np.ndarray:
    """Gradient with a strong orange disc. `top` is the disc's top row (negative overlaps the edge)."""
    picture = _gradient_picture(size, size, 4)
    radius = 70
    cy = top + radius
    cx = int(size * 0.72)
    yy, xx = np.ogrid[:size, :size]
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius * radius
    picture[disc] = (255, 0, 255)
    return picture


def _top_band(picture: np.ndarray):
    from quick_print import _extend_to_product, decide_light

    fitted, extended, delta = _extend_to_product(picture, 148, 210, "", [])
    pad = (fitted.shape[0] - picture.shape[0]) // 2
    band = fitted[:pad]
    info = decide_light({
        "compiled": True,
        "enginePassed": True,
        "aspectExtended": extended,
        "aspectDelta": delta,
        "upscale": 1,
    })
    return band, pad, info, fitted, picture


def test_nearby_object_does_not_enter_the_band() -> None:
    """A strong colour just inside the old 64px window must not stain the new edge."""
    import cv2

    picture = _disc_picture(40)
    check("near-inside-old-window", bool(_orange(picture[:64]).any()))
    check("near-outside-thin-edge", not bool(_orange(picture[:5]).any()))
    band, pad, info, fitted, picture = _top_band(picture)
    check("near-pad", pad >= 8, str(pad))
    check("near-no-orange", int(_orange(band).sum()) == 0, str(int(_orange(band).sum())))
    smooth = cv2.GaussianBlur(band, (0, 0), 3)
    hot = (np.max(np.abs(band.astype(np.float32) - smooth), axis=2) > 45).astype(np.uint8)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(hot, 8)
    blobs = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > 6]
    check("near-no-patches", not blobs, str(blobs[:6]))
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).astype(np.float32)
    second = np.diff(gray.mean(axis=1), n=2)
    check("near-smooth", float(np.max(np.abs(second))) < 2.5, f"{float(np.max(np.abs(second))):.2f}")
    middle = fitted[pad:pad + picture.shape[0]]
    check("near-keeps-picture", np.array_equal(middle, picture))
    check("near-amber", info["light"] == "amber" and any("extended" in line for line in info["reasons"]), str(info))


def test_touching_object_stays_reasonable_and_amber() -> None:
    """A colour that does touch the edge may continue, but only as that edge, and the job stays amber."""
    import cv2

    picture = _disc_picture(-30)
    check("touch-on-edge", bool(_orange(picture[:1]).any()))
    band, _pad, info, fitted, picture = _top_band(picture)
    edge_orange = _orange(picture[:1])[0]
    cols = np.where(edge_orange)[0]
    check("touch-has-span", cols.size > 10, str(cols.size))
    # The edge colour is blurred along the rim, so the object softens sideways.
    spread = int(round(0.04 * picture.shape[1] * 3)) + 8
    lo = int(cols.min()) - spread
    hi = int(cols.max()) + spread
    band_orange = _orange(band)
    stray = [int(x) for x in np.where(band_orange.any(axis=0))[0] if x < lo or x > hi]
    check("touch-orange-stays-with-object", not stray, str(stray[:8]))
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(band_orange.astype(np.uint8), 8)
    floating = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 12:
            continue
        top = int(stats[index, cv2.CC_STAT_TOP])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if top + height < band.shape[0] - 1:
            floating.append((area, int(stats[index, cv2.CC_STAT_WIDTH]), height))
    check("touch-no-floating-dashes", not floating, str(floating))
    check("touch-amber", info["light"] == "amber" and any("extended" in line for line in info["reasons"]), str(info))
    middle = fitted[(fitted.shape[0] - picture.shape[0]) // 2:][:picture.shape[0]]
    check("touch-keeps-picture", np.array_equal(middle, picture))


def test_bottom_bar_stays_at_the_bottom() -> None:
    """A flat contact band is kept at the bottom. The picture above it is not centred."""
    from quick_print import _extend_to_product

    picture = _gradient_picture(360, 360, 3)
    picture[300:] = (40, 20, 80)
    picture[296:300] = (40, 180, 220)
    picture[180, 100:140] = (9, 8, 7)
    fitted, extended, _delta = _extend_to_product(picture, 148, 210, "", [])
    check("bar-extended", extended is True and fitted.shape[0] > picture.shape[0], str(fitted.shape))
    side = (fitted.shape[1] - picture.shape[1]) // 2
    found = None
    for y in range(fitted.shape[0]):
        if np.array_equal(fitted[y, side:side + picture.shape[1]][100:140], picture[180, 100:140]):
            # The marker is only on that row of the original. Confirm the neighbours differ.
            found = y
            break
    check("bar-marker", found is not None, "marker row missing")
    if found is None:
        return
    top_pad = found - 180
    bottom_pad = fitted.shape[0] - (found + (picture.shape[0] - 180))
    check("bar-more-space-above", top_pad > bottom_pad * 2, f"top {top_pad} bottom {bottom_pad}")
    px_per_mm = fitted.shape[1] / 148.0
    check("bar-safe-bottom", abs(bottom_pad / px_per_mm - 4.0) < 0.6, f"{bottom_pad / px_per_mm:.2f} mm")
    footer = np.array((40, 20, 80), np.float32)
    tail = fitted[-max(1, bottom_pad // 2)].astype(np.float32)
    # The continuation under the band is that band's colour, not a smear of the picture above it.
    delta = float(np.mean(np.abs(tail - footer)))
    check("bar-tail-colour", delta < 8.0, f"{delta:.2f}")
    # Interior of the picture, clear of the feathered rim, is the original.
    core = picture[80:260, 40:-40]
    placed = fitted[top_pad + 80:top_pad + 260, side + 40:side + picture.shape[1] - 40]
    check("bar-keeps-core", core.shape == placed.shape and np.array_equal(core, placed))


def test_tall_extension_is_not_striped() -> None:
    """A 28px rim smeared down a tall gap is vertical stripes. A tall block is not."""
    import cv2
    from quick_print import MIRROR_RIM_PX, _extend_vertical, _mirror_extend, vertical_streaks_dominate

    src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "catch_fire", "src.jpg"))
    picture = cv2.imread(src)
    check("stripe-fixture", picture is not None and picture.shape[0] > 400, str(None if picture is None else picture.shape))
    old = _mirror_extend(picture[:MIRROR_RIM_PX], 600, forward=False, strong=False)
    check("old-rim-is-striped", vertical_streaks_dominate(old), "the thin rim should fail the stripe gate")
    fitted = _extend_vertical(picture, 600, 0)
    check("tall-not-striped", vertical_streaks_dominate(fitted[:600]) is False, "tall extension still has vertical streaks")
    from quick_print import _join_overlap
    overlap = _join_overlap(picture, 300.0 / 25.4)
    check(
        "tall-keeps-picture",
        np.array_equal(fitted[600 + overlap + 2:], picture[overlap + 2:]),
        f"overlap {overlap}",
    )


def _save_poster_join(press_path: str, picture_path: str) -> None:
    """150 dpi trim, plus a 300 dpi strip centred on the extension join."""
    import cv2
    import pymupdf as fitz
    from PIL import Image
    from quick_print import SAFE_ZONE_MM, seam_metrics

    picture = cv2.imread(picture_path)
    trim_w, trim_h = 148.0, 210.0
    width = picture.shape[1]
    height = picture.shape[0]
    canvas_w = int(round(width * trim_w / (trim_w - 2.0 * SAFE_ZONE_MM)))
    canvas_h = int(round(canvas_w * trim_h / trim_w))
    bottom = max(1, int(round(SAFE_ZONE_MM * canvas_w / trim_w)))
    top = canvas_h - height - bottom
    join_mm = top / float(canvas_h) * trim_h
    os.makedirs(os.path.join(ART, "catch_fire"), exist_ok=True)

    def render(dpi: float):
        doc = fitz.open(press_path)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
        doc.close()
        bleed_px = int(round(5.0 / 25.4 * dpi))
        return rgb[bleed_px:rgb.shape[0] - bleed_px, bleed_px:rgb.shape[1] - bleed_px]

    trim150 = render(150.0)
    Image.fromarray(trim150).save(os.path.join(ART, "catch_fire", "trim_150.png"), dpi=(150, 150))
    trim300 = render(300.0)
    join_y = int(round(join_mm / 25.4 * 300.0))
    half = int(round(20.0 / 25.4 * 300.0))
    y0 = max(0, join_y - half)
    y1 = min(trim300.shape[0], join_y + half)
    crop = trim300[y0:y1]
    Image.fromarray(crop).save(os.path.join(ART, "catch_fire", "join_300.png"), dpi=(300, 300))
    metrics = seam_metrics(cv2.cvtColor(trim300, cv2.COLOR_RGB2BGR), join_y, side="top", px_per_mm=300.0 / 25.4)
    lines = [
        f"join_mm {join_mm:.2f}",
        f"crop_px {crop.shape[1]}x{crop.shape[0]}",
        f"joinStep {metrics['joinStep']}",
        f"artStep {metrics['artStep']}",
        f"extStep {metrics['extStep']}",
        f"stepLimit {metrics['stepLimit']}",
        f"stepRatio {metrics['stepRatio']}",
        f"bandDev {metrics['bandDev']}",
        f"bandLimit {metrics['bandLimit']}",
        f"ok {metrics['ok']}",
    ]
    with open(os.path.join(ART, "catch_fire", "seam_metrics.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("SEAM", " ".join(lines))
    check("catch-rendered-seam", metrics["ok"] is True, str(metrics))


def _save_church_line(press_path: str, gate: list) -> None:
    """600 dpi crop of the top church-name line, plus the per-box gate table."""
    import pymupdf as fitz
    from PIL import Image

    rows = [row for row in gate if row.get("text") == "GREATER HARVEST FAMILY CHURCH"]
    check("catch-church-rows", len(rows) >= 1, str(len(rows)))
    if not rows:
        return
    top = min(rows, key=lambda row: float(row.get("y") or 0))
    box = top.get("boxMm") or [0, 0, 0, 0]
    render = str(top.get("render") or "")
    check(
        "catch-top-church-complete",
        top.get("ok") is True
        and render == "GREATER HARVEST FAMILY CHURCH"
        and float(top.get("ssim") or 0) >= 0.85
        and top.get("whiteBlock") is False
        and top.get("clipped") is False
        and render.endswith("CHURCH"),
        str(top)[:500],
    )
    os.makedirs(os.path.join(ART, "catch_fire"), exist_ok=True)
    doc = fitz.open(press_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(600.0 / 72.0, 600.0 / 72.0), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    doc.close()
    bleed = int(round(5.0 / 25.4 * 600.0))
    trim = rgb[bleed:rgb.shape[0] - bleed, bleed:rgb.shape[1] - bleed]
    ppm = 600.0 / 25.4
    pad_x = int(round(2.0 * ppm))
    pad_y = int(round(3.5 * ppm))
    x0 = max(0, int(np.floor(float(box[0]) * ppm)) - pad_x)
    y0 = max(0, int(np.floor(float(box[1]) * ppm)) - pad_y)
    x1 = min(trim.shape[1], int(np.ceil((float(box[0]) + float(box[2])) * ppm)) + pad_x)
    y1 = min(trim.shape[0], int(np.ceil((float(box[1]) + float(box[3])) * ppm)) + pad_y)
    crop = trim[y0:y1, x0:x1]
    Image.fromarray(crop).save(os.path.join(ART, "catch_fire", "church_600.png"), dpi=(600, 600))
    print(f"CHURCH CROP {crop.shape[1]}x{crop.shape[0]} boxMm {box} render {render!r}")
    lines = ["ok mode ssim white clip y text => render"]
    for row in sorted(gate, key=lambda item: (float(item.get("y") or 0), str(item.get("text") or ""))):
        lines.append(
            f"{'OK' if row.get('ok') else 'FAIL'} {row.get('mode')} "
            f"ssim={float(row.get('ssim') or 0):.3f} white={bool(row.get('whiteBlock'))} "
            f"clip={bool(row.get('clipped'))} y={float(row.get('y') or 0):.4f} "
            f"{row.get('text')!r} => {row.get('render')!r}"
        )
    table = "\n".join(lines) + "\n"
    with open(os.path.join(ART, "catch_fire", "gate_table.txt"), "w", encoding="utf-8") as handle:
        handle.write(table)
    print(table)


def test_large_block_join_is_soft() -> None:
    """A tall extension meets the picture without a hard line or a pale row.

    The blur starts between sigma 2 and 4 and climbs over 15–20 mm. The blend
    may enter the picture by about 7 mm, and it stops 2 mm short of lettering.
    """
    import cv2
    from quick_print import (
        SAFE_ZONE_MM,
        TALL_BLUR_S0,
        TALL_CLEAR_MM,
        TALL_OVERLAP_MM,
        TALL_RAMP_MM,
        _extend_edges,
        _extend_vertical,
        _first_ink_row,
        _join_overlap,
        _layout_with_bar,
        seam_metrics,
    )

    check("blur-starts-soft", 2.0 <= TALL_BLUR_S0 <= 4.0, str(TALL_BLUR_S0))
    check("blur-ramps-over-15-20mm", 15.0 <= TALL_RAMP_MM <= 20.0, str(TALL_RAMP_MM))
    check("overlap-is-6-8mm", 6.0 <= TALL_OVERLAP_MM <= 8.0, str(TALL_OVERLAP_MM))
    check("text-clearance-is-2mm", abs(TALL_CLEAR_MM - 2.0) < 0.01, str(TALL_CLEAR_MM))

    ruled = np.full((120, 160, 3), 30, np.uint8)
    ruled[60] = 210
    hard = seam_metrics(ruled, 60, side="top", px_per_mm=4.0)
    check("hard-line-fails-seam", hard["ok"] is False and hard["okStep"] is False and hard["okBand"] is False, str(hard))

    src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "catch_fire", "src.jpg"))
    picture = cv2.imread(src)
    trim_w, trim_h = 148.0, 210.0
    canvas_w = int(round(picture.shape[1] * trim_w / (trim_w - 2.0 * SAFE_ZONE_MM)))
    ppm = canvas_w / trim_w
    ink = _first_ink_row(picture, ppm)
    overlap = _join_overlap(picture, ppm)
    clear = int(round(TALL_CLEAR_MM * ppm))
    check("poster-finds-title", ink is not None and 50 <= ink <= 90, str(ink))
    check("poster-stops-short-of-title", overlap <= int(ink) - clear, f"overlap {overlap} ink {ink} clear {clear}")
    fitted = _layout_with_bar(picture, trim_w, trim_h)
    check("poster-laid-out", fitted is not None, "layout failed")
    if fitted is None or ink is None:
        return
    top = fitted.shape[0] - picture.shape[0] - max(1, int(round(SAFE_ZONE_MM * canvas_w / trim_w)))
    side = (fitted.shape[1] - picture.shape[1]) // 2
    metrics = seam_metrics(fitted, top, side="top", px_per_mm=ppm)
    check("poster-seam", metrics["ok"] is True, str(metrics))
    art = fitted[top:top + picture.shape[0], side:side + picture.shape[1]]
    check(
        "poster-title-untouched",
        np.array_equal(art[ink:, 24:-24], picture[ink:, 24:-24]),
        f"ink row {ink}",
    )

    gradient = np.zeros((280, 200, 3), np.uint8)
    for y in range(gradient.shape[0]):
        gradient[y] = (30 + y // 4, 50, 90)
    bar = gradient.copy()
    bar[48:64] = (245, 245, 245)
    below = _extend_vertical(bar, 0, 220, px_per_mm=10.0)
    bottom = seam_metrics(below, bar.shape[0], side="bottom", px_per_mm=10.0)
    check("bottom-seam", bottom["ok"] is True, str(bottom))
    check("bottom-bar-untouched", np.array_equal(below[48:64], bar[48:64]))
    sided = _extend_edges(gradient, 0, 0, 160, 160, px_per_mm=10.0)
    left = seam_metrics(sided, 160, side="left", px_per_mm=10.0)
    right = seam_metrics(sided, 160 + gradient.shape[1], side="right", px_per_mm=10.0)
    check("side-seams", left["ok"] is True and right["ok"] is True, f"{left} {right}")


def test_catch_fire_raster_is_traced() -> None:
    """A square social JPG is not an AI export size. Lettering still has to be traced."""
    from ai_rebuild import assess

    src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "catch_fire", "src.jpg"))
    check("catch-fixture", os.path.exists(src), src)
    verdict = assess(src, 148, 210, BLEED_MM)
    check("catch-not-classified-ai", verdict.get("detected") is False, str(verdict.get("reasons"))[:240])
    out = tempfile.mkdtemp(prefix="catch-fire-")
    result = make_print_ready(src, out, 148, 210, "a5", "A5", filename="src.jpg")
    joined = " ".join(result.get("decisions") or [])
    check("catch-press", bool(result.get("pressPath") and os.path.getsize(result["pressPath"]) > 1000), str(result.get("reasons"))[:300])
    check("catch-traced", "this raster has lettering" in joined.lower() and "traced" in joined.lower(), joined[:600])
    media_w = float(result.get("mediaWidthMm") or 0)
    media_h = float(result.get("mediaHeightMm") or 0)
    trim_w = float(result.get("trimWidthMm") or 0)
    trim_h = float(result.get("trimHeightMm") or 0)
    check("catch-bleed-5", abs(media_w - 158) < 2 and abs(media_h - 220) < 2, f"{media_w}x{media_h}")
    check("catch-trim", abs(trim_w - 148) < 1.5 and abs(trim_h - 210) < 1.5, f"{trim_w}x{trim_h}")
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    drawings = page.get_drawings()
    images = page.get_images()
    info = doc.extract_image(images[0][0]) if images else {}
    pix = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    doc.close()
    boxes = 0
    for line in result.get("decisions") or []:
        if "traced as vector shapes (" in str(line):
            try:
                boxes = int(str(line).split("(")[1].split(" ")[0])
            except (IndexError, ValueError):
                boxes = 0
    # Same-colour paths share one drawing, so the box count is the trace count.
    check("catch-vector-paths", boxes >= 15 and len(drawings) >= 1, f"boxes {boxes} drawings {len(drawings)}")
    check("catch-cmyk", info.get("colorspace") == 4, str(info.get("colorspace")))
    inset = int(round(5.0 / 25.4 * (pix.w / (158 / 25.4))))
    trim = rgb[inset:rgb.shape[0] - inset, inset:rgb.shape[1] - inset]
    # The contact band sits at the bottom. The 4 mm under it is the same navy, not a smear of the portrait.
    tail = trim[-max(2, trim.shape[0] // 50):].astype(np.int16)
    navy = np.array([33, 15, 77], np.int16)  # RGB of the footer
    tail_delta = float(np.mean(np.abs(tail - navy)))
    check("catch-footer-at-bottom", tail_delta < 18, f"{tail_delta:.1f}")
    address = trim[int(trim.shape[0] * 0.90):int(trim.shape[0] * 0.97)]
    check("catch-address-above-footer", float(address.std()) > float(tail.std()) + 5, f"addr {address.std():.1f} tail {tail.std():.1f}")
    from quick_print import vertical_streaks_dominate
    import cv2
    top_band = cv2.cvtColor(trim[: max(24, int(trim.shape[0] * 0.22))], cv2.COLOR_RGB2BGR)
    check("catch-no-stripes", vertical_streaks_dominate(top_band) is False, "top extension is vertical streaks")
    gate = (result.get("vectorText") or {}).get("textGate") or []
    vector_bad = [row for row in gate if row.get("mode") == "vector" and not row.get("ok")]
    check("catch-vector-lines-match", bool(gate) and not vector_bad, str(vector_bad)[:500])
    stray = [row for row in gate if str(row.get("text") or "").strip() == "f" and row.get("mode") == "vector"]
    check("catch-small-glyph-stays-raster", not stray, str(stray)[:300])
    # A closed counter or a fragment stays raster, and the render must still read the real letters.
    for phrase in ("9AM", "6PM", "11AM", "SANDILE", "GREATER HARVEST FAMILY CHURCH"):
        hit = [row for row in gate if row.get("text") == phrase]
        check(
            "catch-line-" + phrase[:16],
            bool(hit) and all(
                row.get("ok") and str(row.get("render") or "") == phrase for row in hit
            ),
            str(hit)[:400],
        )
    wrong = [row for row in gate if str(row.get("render") or "") in {"8AM", "BANDILE", "CHURC"}]
    check("catch-no-wrong-character", not wrong, str(wrong)[:300])
    _save_poster_join(result["pressPath"], picture_path=src)
    _save_church_line(result["pressPath"], gate)


def main() -> None:
    test_rules()
    test_extended_band_does_not_streak()
    test_nearby_object_does_not_enter_the_band()
    test_touching_object_stays_reasonable_and_amber()
    test_bottom_bar_stays_at_the_bottom()
    test_tall_extension_is_not_striped()
    test_large_block_join_is_soft()
    test_catch_fire_raster_is_traced()
    root = tempfile.mkdtemp(prefix="quick-print-src-")
    try:
        test_word(root)
        test_corrupt(root)
        test_too_small(root)
        test_existing(root)
        test_wide(root)
        test_flyer(root)
        test_shapes(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("ALL QUICK PRINT CHECKS PASSED")


if __name__ == "__main__":
    main()
