#!/usr/bin/env python3
"""Checks for vector text rebuild v2. Engines are called, not rewritten."""

from __future__ import annotations

import os
import re
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vector_text_v2 import (
    BLEED_MM,
    FONTS,
    MIN_PPI,
    SANS_KEYS,
    SCRIPT_KEYS,
    _assign_role,
    _is_phone,
    _rescue_kind,
    _script_allowed,
    _scripty,
    _strip_side_icons,
    fit_line,
    font_path,
    rebuild_fitted,
)

ART = os.environ.get("QUICK_PRINT_ARTIFACTS", "/opt/cursor/artifacts")
SANS = os.path.join(os.path.dirname(__file__), "fonts", "LiberationSans-Bold.ttf")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def test_faces_and_leashes() -> None:
    for key, _name, _role in FONTS:
        path = font_path(key)
        check(f"font-{key}", os.path.exists(path) and os.path.getsize(path) > 1000, path)
    check("bleed-5", BLEED_MM == 5.0)
    check("ppi-400", MIN_PPI >= 400)
    root = os.path.dirname(__file__)
    leash = open(os.path.join(root, "smart_bleed.py"), encoding="utf-8", errors="ignore").read()
    check("leash-buffer", "BufferSpace=50000000" in leash or "-dBufferSpace=50000000" in leash or "50000000" in leash)
    check("leash-threads", "NumRenderingThreads=1" in leash or "-dNumRenderingThreads=1" in leash)


def test_width_fit() -> None:
    _size, _track, width = fit_line("cinzel-600", "MEDELLA", 180.0, 28.0)
    check("cinzel-fits-width", abs(width - 180.0) / 180.0 < 0.08, f"{width:.2f}")
    _size, _track, body = fit_line("crimson", "Live blood analysis", 220.0, 16.0)
    check("crimson-fits-width", abs(body - 220.0) / 220.0 < 0.12, f"{body:.2f}")


def test_script_stays_raster() -> None:
    ink = np.zeros((40, 180), np.uint8)
    cv2.line(ink, (8, 28), (170, 12), 255, 6)
    check("connected-ink-is-script", _scripty(ink, "Medella"))
    image = Image.new("RGB", (640, 400), (236, 228, 210))
    draw = ImageDraw.Draw(image)
    draw.line((40, 180, 420, 120), fill=(40, 70, 40), width=18)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    block = {
        "id": "t1",
        "text": "Medella",
        "bbox": [0.05, 0.28, 0.7, 0.22],
        "color_hex": "#284628",
        "bold": False,
        "score": 0.61,
    }
    out = tempfile.mkdtemp(prefix="vector-script-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        bgr, 90, 50, pdf, blocks=[block], reocr=lambda _path: "Medella",
    )
    check("script-not-set", result.get("ok") is False and not os.path.exists(pdf), str(result.get("reason")))
    check("script-amber", result.get("amber") is True)


def test_overlap_and_qa_gate() -> None:
    image = _poster()
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    both = [
        _block("MARKET DAY", 0.12, 0.50, 0.62, 0.08, "#fff4d2"),
        _block("MARKET DAY", 0.14, 0.52, 0.60, 0.08, "#fff4d2"),
    ]
    out = tempfile.mkdtemp(prefix="vector-overlap-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(bgr, 148, 210, pdf, blocks=both, reocr=lambda _path: "MARKET DAY")
    modes = [line.get("mode") for line in result.get("lines") or []]
    check("overlap-one-vector", result.get("ok") is True and modes.count("vector") == 1, str(modes))
    check("overlap-amber", result.get("amber") is True, str(result.get("reason")))

    failed = rebuild_fitted(
        bgr, 148, 210, os.path.join(out, "bad.pdf"),
        blocks=[_block("MARKET DAY", 0.12, 0.50, 0.62, 0.08, "#fff4d2")],
        reocr=lambda _path: "",
    )
    check("qa-recall-fallback", failed.get("ok") is False and not os.path.exists(os.path.join(out, "bad.pdf")), str(failed.get("reason")))


def test_bullet_cmyk_and_boxes() -> None:
    image = _poster()
    draw = ImageDraw.Draw(image)
    draw.ellipse((78, 768, 108, 798), fill=(255, 244, 210))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    out = tempfile.mkdtemp(prefix="vector-bullet-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        bgr, 148, 210, pdf,
        blocks=[_block("SATURDAY", 0.14, 0.62, 0.46, 0.06, "#ffffff")],
        reocr=lambda _path: "SATURDAY",
    )
    check("bullet-job", result.get("ok") is True, str(result.get("reason")) + " " + str(result.get("qa")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    page = doc[0]
    text = page.get_text("text") or ""
    drawings = page.get_drawings()
    images = page.get_images()
    info = doc.extract_image(images[0][0])
    inset = float(page.trimbox.x0) * 25.4 / 72.0
    fonts = page.get_fonts()
    doc.close()
    check("bullet-word", "SATURDAY" in text, text[:120])
    check("bullet-dot", any(item.get("type") == "c" or item.get("items") for item in drawings), str(len(drawings)))
    check("bullet-cmyk", info.get("colorspace") == 4, str(info.get("colorspace")))
    check("bullet-ppi", float(result["qa"].get("ppi_x") or 0) >= 399, str(result.get("qa")))
    check("bullet-inset", abs(inset - 5) < 0.45, f"{inset:.2f}")
    check("bullet-subset", bool(fonts) and all("+" in str(item[3]) for item in fonts), str(fonts)[:180])
    check("bullet-one-image", len(images) == 1, str(len(images)))


def test_reread_cannot_swap_the_line() -> None:
    from vector_text_v2 import _close_reread

    check("surface-stays", _close_reread("The Surface", "Tho Surfuce", 0.95) is False)
    check("weak-read-replaced", _close_reread("Supts noral", "Supports natural", 0.68))
    check("best-restored", _close_reread("feel your bes, every day.", "feel your best, every day.", 0.97))
    check("spacing-restored", _close_reread("LIVE BLOODA NALYSIS", "LIVE BLOOD ANALYSIS", 0.9))


def test_refine_repairs_a_joined_word() -> None:
    from vector_text_v2 import _refine_blocks

    image = Image.new("RGB", (900, 200), (20, 40, 30))
    draw = ImageDraw.Draw(image)
    draw.text((40, 70), "LIVE BLOOD ANALYSIS", font=ImageFont.truetype(SANS, 42), fill=(255, 255, 255))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    block = {
        "text": "LIVE BLOODA NALYSIS",
        "bbox": [0.03, 0.28, 0.75, 0.42],
        "score": 0.72,
        "color_hex": "#ffffff",
    }
    refined = _refine_blocks(bgr, [block])
    text = str(refined[0]["text"]).upper()
    check("refine-blood", "BLOOD ANALYSIS" in text and "BLOODA" not in text, text)


def test_list_item_is_not_script() -> None:
    ink = np.zeros((36, 280), np.uint8)
    cv2.rectangle(ink, (4, 6), (270, 30), 255, -1)
    check("detox-not-script", _script_allowed("Detox and wellness support", ink) is False)
    check("phone-shape", _is_phone("073 703 0766"))
    check("name-rescue", _rescue_kind("Chandré") == "name")
    check("phone-rescue", _rescue_kind("073 703 0766") == "phone")


def test_tick_leaves_the_mask() -> None:
    ink = np.zeros((40, 220), np.uint8)
    cv2.line(ink, (6, 22), (14, 32), 255, 2)
    cv2.line(ink, (14, 32), (26, 8), 255, 2)
    cv2.rectangle(ink, (78, 8), (200, 32), 255, -1)
    stripped, removed = _strip_side_icons(ink)
    check("tick-gap-removed", bool(removed) and int(stripped[:, :40].max()) == 0, str(removed))
    check("tick-words-kept", int(stripped[:, 80:].max()) > 0)


def test_serif_page_is_consistent() -> None:
    """Caps, body, a list line, a name and a phone share the approved serif set."""
    crimson = font_path("crimson")
    cinzel = font_path("cinzel-600")
    image = Image.new("RGB", (980, 1400), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    body = ImageFont.truetype(crimson, 34)
    caps = ImageFont.truetype(cinzel, 42)
    script = ImageFont.truetype(font_path("parisienne"), 78)
    green = (36, 58, 40)
    draw.text((70, 40), "SEE BEYOND", font=caps, fill=green)
    draw.text((70, 160), "Early detection of imbalances", font=body, fill=green)
    draw.text((70, 230), "Supports natural healing daily", font=body, fill=green)
    draw.text((70, 300), "Detox and wellness support", font=body, fill=green)
    draw.text((70, 430), "Chandré", font=script, fill=green)
    draw.text((70, 560), "073 703 0766", font=body, fill=green)
    draw.text((80, 980), "Medella", font=ImageFont.truetype(font_path("parisienne"), 150), fill=green)
    blocks = [
        _block("SEE BEYOND", 0.06, 0.025, 0.42, 0.045, "#243a28"),
        _block("Early detection of imbalances", 0.06, 0.11, 0.62, 0.04, "#243a28"),
        _block("Supports natural healing daily", 0.06, 0.16, 0.62, 0.04, "#243a28"),
        _block("Detox and wellness support", 0.06, 0.21, 0.55, 0.04, "#243a28"),
        _block("Chandré", 0.06, 0.30, 0.28, 0.07, "#243a28"),
        _block("073 703 0766", 0.06, 0.39, 0.40, 0.04, "#243a28"),
        _block("Medella", 0.06, 0.68, 0.55, 0.14, "#243a28"),
    ]
    blocks[4]["score"] = 0.62
    blocks[5]["score"] = 0.4
    out = tempfile.mkdtemp(prefix="vector-serif-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(image_bgr(image), 148, 210, pdf, blocks=blocks, reocr=lambda _path: " ".join(
        line["text"] for line in blocks if line["text"] != "Medella"
    ))
    check("serif-job", result.get("ok") is True, str(result.get("reason")))
    vector = [line for line in result.get("lines") or [] if line.get("mode") == "vector"]
    fonts = {line.get("font") for line in vector}
    check("serif-no-sans", fonts.isdisjoint(SANS_KEYS), str(sorted(fonts)))
    body_fonts = {line.get("font") for line in vector if "detection" in line["text"] or "Supports" in line["text"] or "Detox" in line["text"]}
    check("serif-one-body", body_fonts == {"crimson"}, str(body_fonts))
    detox = next(line for line in vector if line["text"].startswith("Detox"))
    check("detox-not-script-face", detox.get("font") not in SCRIPT_KEYS, str(detox))
    caps_line = next(line for line in result.get("lines") or [] if line["text"] == "SEE BEYOND")
    check(
        "caps-are-cinzel",
        caps_line.get("mode") == "vector" and caps_line.get("font") in ("cinzel-400", "cinzel-500", "cinzel-600"),
        str(caps_line),
    )
    phone = next(line for line in result.get("lines") or [] if "073" in line["text"])
    name = next(line for line in result.get("lines") or [] if "hand" in line["text"].lower() or "Chand" in line["text"])
    check("phone-is-vector", phone.get("mode") == "vector" and phone.get("font") == "crimson", str(phone))
    check("name-is-vector", name.get("mode") == "vector" and name.get("font") not in SANS_KEYS, str(name))
    logo = next(line for line in result.get("lines") or [] if line["text"] == "Medella")
    check("logo-stays-raster", logo.get("mode") == "raster", str(logo))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = doc[0].get_text("text") or ""
    doc.close()
    check("logo-not-retyped", "Medella" not in text, text[:240])


def test_badge_digit_stays() -> None:
    image = Image.new("RGB", (900, 420), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 140, 200, 240), fill=(36, 92, 58))
    draw.text((136, 162), "1", font=ImageFont.truetype(SANS, 48), fill=(255, 255, 255))
    draw.text((230, 168), "A small drop of blood", font=ImageFont.truetype(font_path("crimson"), 32), fill=(36, 58, 40))
    draw.line((70, 310, 86, 332), fill=(36, 92, 58), width=4)
    draw.line((86, 332, 112, 292), fill=(36, 92, 58), width=4)
    draw.text((140, 300), "Looks at the cause", font=ImageFont.truetype(font_path("crimson"), 32), fill=(36, 58, 40))
    blocks = [
        _block("A small drop of blood", 0.24, 0.36, 0.55, 0.22, "#243a28"),
        _block("Looks at the cause", 0.06, 0.66, 0.55, 0.18, "#243a28"),
    ]
    out = tempfile.mkdtemp(prefix="vector-badge-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        image_bgr(image), 148, 80, pdf, blocks=blocks,
        reocr=lambda _path: "A small drop of blood Looks at the cause",
    )
    check("badge-job", result.get("ok") is True, str(result.get("reason")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    doc.close()
    white = (rgb[:, :, 0] > 210) & (rgb[:, :, 1] > 210) & (rgb[:, :, 2] > 210)
    green = (rgb[:, :, 1] > 70) & (rgb[:, :, 1] > rgb[:, :, 0] + 15) & (rgb[:, :, 1] > rgb[:, :, 2] + 10)
    # The digit is white pixels enclosed by the green disk.
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(green.astype(np.uint8), 8)
    held = False
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 80:
            continue
        ys, xs = np.where(labels == index)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        if int(white[y0:y1 + 1, x0:x1 + 1].sum()) > 8:
            held = True
    check("badge-digit-survives", held, f"white {int(white.sum())} green {int(green.sum())}")


def test_shared_scale_keeps_the_whole_picture() -> None:
    """The side stripe stays in the trim. The gap beside it is the continued edge, not a crop."""
    from quick_print import make_print_ready

    image = Image.new("RGB", (1024, 1536), (236, 228, 214))
    draw = ImageDraw.Draw(image)
    draw.rectangle((984, 0, 1023, 1535), fill=(210, 40, 40))
    draw.rectangle((960, 0, 983, 1535), fill=(30, 50, 190))
    draw.text((180, 680), "MARKET DAY", font=ImageFont.truetype(SANS, 72), fill=(30, 50, 30))
    folder = tempfile.mkdtemp(prefix="vector-place-")
    src = os.path.join(folder, "src.png")
    image.save(src)
    out = os.path.join(folder, "out")
    result = make_print_ready(src, out, 148, 210, "a5", "A5", filename="src.png")
    check("place-press", bool(result.get("pressPath")), str(result.get("reasons"))[:300])
    joined = " ".join(result.get("decisions") or [])
    check("place-one-scale", "one scale" in joined, joined[:400])
    import pymupdf as fitz
    doc = fitz.open(result["pressPath"])
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    inset = int(round(float(page.trimbox.x0) / page.rect.width * pix.w))
    doc.close()
    trim = rgb[:, inset:pix.w - inset]
    blue_at = None
    red_at = None
    for col in range(trim.shape[1] - 1, max(0, trim.shape[1] - 180), -1):
        probe = np.median(trim[:, col], axis=0)
        if blue_at is None and float(probe[2]) > float(probe[0]) + 15:
            blue_at = col
        if red_at is None and float(probe[0]) > float(probe[2]) + 25 and float(probe[0]) > 90:
            red_at = col
    check(
        "place-keeps-both-stripes",
        blue_at is not None and red_at is not None and red_at > blue_at,
        f"blue {blue_at} red {red_at}",
    )


def test_placement_matches_the_reference_fit() -> None:
    """The general fit reproduces the approved Medella placement. No side is hard-coded."""
    from vector_plate import fit_placement

    scale, off_x, off_y = fit_placement(1024, 1536, 148, 210, 5, [(51, 43, 936, 1531)])
    check("flyer-front-scale", abs(scale - 205 / 1536) < 1e-6, f"{scale}")
    check(
        "flyer-front-origin",
        abs(off_y - 7.5) < 1e-6 and abs(off_x - (158 - 1024 * 205 / 1536) / 2) < 1e-4,
        f"{off_x},{off_y}",
    )
    scale, off_x, off_y = fit_placement(1054, 1492, 148, 210, 5, [(63, 53, 1027, 1432)])
    check("flyer-back-scale", abs(scale - 148 / 1054) < 1e-9, f"{scale}")
    check("flyer-back-origin", abs(off_x - 5) < 1e-6, f"{off_x},{off_y}")
    scale, off_x, off_y = fit_placement(1586, 992, 90, 50, 5, [(221, 167, 1478, 865)])
    check("card-front-scale", abs(scale - 50 / 992) < 1e-9, f"{scale}")
    check("card-front-origin", abs(off_y - 5) < 1e-6, f"{off_x},{off_y}")


def test_erase_clears_the_original() -> None:
    image = Image.new("RGB", (420, 180), (240, 236, 228))
    draw = ImageDraw.Draw(image)
    draw.text((36, 48), "Through", font=ImageFont.truetype(font_path("crimson"), 52), fill=(20, 30, 20))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    line = {"text": "Through", "rect": (24, 36, 320, 100), "font": "crimson", "role": "body", "mode": "vector"}
    from vector_plate import erase_text

    clean, kept, skipped = erase_text(bgr, [line], [])
    check("erase-kept", len(kept) == 1 and not skipped, str([item.get("reason") for item in skipped]))
    crop = clean[48:130, 30:300]
    dark = int(((crop[:, :, 0] < 90) & (crop[:, :, 1] < 90) & (crop[:, :, 2] < 90)).sum())
    check("erase-no-ghost", dark < 40, f"dark pixels {dark}")


def test_core_ink_ignores_the_green_edge() -> None:
    """The stroke core is the dark ink. The green fringe around it is not."""
    from vector_plate import _core_bgr

    image = np.full((48, 220, 3), 245, np.uint8)
    cv2.rectangle(image, (20, 12), (200, 36), (20, 90, 30), -1)
    cv2.rectangle(image, (24, 16), (196, 32), (8, 8, 8), -1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    keep = gray < 180
    colour = _core_bgr(image, keep)
    chroma = (float(colour.max()) - float(colour.min())) / 255.0
    check("core-is-near-black", float(colour.max()) < 30 and chroma < 0.08, f"{colour.round(1)} chroma {chroma:.3f}")


def test_step_circles_are_the_only_badges() -> None:
    from vector_plate import find_badges

    flyer = cv2.imread(os.path.join(os.path.dirname(__file__), "..", "tests/fixtures/medella/flyer_front.png"))
    badges = find_badges(flyer)
    centres = sorted((round(item["cx"]), round(item["cy"])) for item in badges)
    check("four-step-circles", centres == [(711, 689), (711, 746), (711, 803), (711, 860)], str(centres))


def test_badge_number_is_not_retyped() -> None:
    image = Image.new("RGB", (800, 500), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 90, 150, 150), fill=(36, 92, 58))
    draw.text((108, 102), "4", font=ImageFont.truetype(font_path("crimson"), 28), fill=(255, 255, 255))
    draw.text((200, 100), "You receive a plan", font=ImageFont.truetype(font_path("crimson"), 32), fill=(20, 28, 18))
    blocks = [
        _block("4", 106 / 800, 100 / 500, 28 / 800, 32 / 500, "#ffffff"),
        _block("You receive a plan", 0.24, 0.18, 0.5, 0.12, "#141c12"),
    ]
    out = tempfile.mkdtemp(prefix="vector-step-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        image_bgr(image), 90, 50, pdf, blocks=blocks,
        reocr=lambda _path: "You receive a plan",
    )
    check("step-job", result.get("ok") is True, str(result.get("reason")))
    kept = [line for line in result.get("lines") or [] if line.get("text") == "4"]
    check("step-stays-raster", bool(kept) and kept[0].get("mode") == "raster" and kept[0].get("reason", "").startswith("Lettering inside"), str(kept))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = doc[0].get_text("text") or ""
    doc.close()
    check("step-not-in-pdf", "4" not in text and "plan" in text.lower(), text[:200])


def test_dotted_caps_keep_their_gaps() -> None:
    """Spaced caps keep the middot and the pixel gaps. The gaps are not letter-spacing."""
    from vector_text_v2 import _char_advances, _fit_width, _fitz_font, _size_for_gaps

    text = "HOLISTIC · NATURAL · PROFESSIONAL"
    font = _fitz_font("cinzel-600")
    box_w, box_h = 186.0, 11.0
    fracs = [0.038, 0.040, 0.036, 0.040]
    size, _track = _fit_width(font, text, box_w, box_h, "spaced")
    size = _size_for_gaps(font, text, size, box_w, fracs)
    advances = _char_advances(font, text, size, box_w, "spaced", fracs)
    spaces = [advance for ch, advance in zip(text, advances) if ch == " "]
    floors = [box_w * frac * 0.9 for frac in fracs]
    check("dot-gaps-held", len(spaces) == 4 and all(space >= floor for space, floor in zip(spaces, floors)), str([round(s, 2) for s in spaces]))

    image = Image.new("RGB", (980, 160), (18, 48, 34))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(font_path("cinzel-600"), 36)
    cursor = 40
    for word in ("HOLISTIC", "·", "NATURAL", "·", "PROFESSIONAL"):
        draw.text((cursor, 58), word, font=face, fill=(236, 242, 232))
        cursor += int(draw.textlength(word, font=face)) + 28
    out = tempfile.mkdtemp(prefix="vector-dots-")
    pdf = os.path.join(out, "press.pdf")
    block = {
        "text": text,
        "bbox": [0.03, 0.28, 0.92, 0.38],
        "score": 0.98,
        "color_hex": "#ecf2e8",
    }
    result = rebuild_fitted(
        cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR),
        148, 40, pdf, blocks=[block], reocr=lambda _path: text,
    )
    check("dot-job", result.get("ok") is True, str(result.get("reason")))
    line = next(item for item in result.get("lines") or [] if "HOLISTIC" in item.get("text", ""))
    check("dot-vector-text", line.get("mode") == "vector" and "·" in line.get("text", ""), str(line.get("text")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    raw = doc[0].get_text("rawdict")
    doc.close()
    found = ""
    for block_item in raw["blocks"]:
        if block_item.get("type") != 0:
            continue
        for row in block_item.get("lines") or []:
            chars = "".join(ch["c"] for span in row["spans"] for ch in span.get("chars") or [])
            if "HOLISTIC" in chars:
                found = chars
    check("dot-in-pdf", "·" in found and found.count(" ") >= 4, repr(found))


def test_long_script_is_not_a_serif() -> None:
    """A joined script sentence is Parisienne or it stays in the picture. Never a serif."""
    ink = np.zeros((48, 460), np.uint8)
    for start in (8, 120, 230, 340):
        cv2.line(ink, (start, 36), (start + 90, 10), 255, 4)
    text = "See your health, live your best life"
    role, key, _score = _assign_role(text, ink, "serif", "")
    check("long-script-face", role == "script" and key == "parisienne", f"{role} {key} {_score}")
    italic = np.zeros((40, 280), np.uint8)
    cv2.line(italic, (8, 30), (260, 8), 255, 3)
    unsure_role, _key, _score = _assign_role("When you know better,", italic, "serif", "")
    check("slant-is-script-or-raster-role", unsure_role in ("script", "unsure"), unsure_role)


def test_tracked_caps_keep_letter_and_word_gaps() -> None:
    """A wide gap between words stays a word space. The smaller gaps stay letter-spacing."""
    from ai_rebuild import measure_rhythm
    from vector_text_v2 import _char_advances, _fitz_font, _size_for_gaps

    canvas = Image.new("RGB", (640, 80), (244, 240, 230))
    draw = ImageDraw.Draw(canvas)
    face = ImageFont.truetype(font_path("cinzel-500"), 22)
    cursor = 30
    for word in ("AND", "HEALTH"):
        for index, ch in enumerate(word):
            draw.text((cursor, 28), ch, font=face, fill=(70, 78, 64))
            cursor += int(draw.textlength(ch, font=face)) + 8
        if word == "AND":
            cursor += 16
    bgr = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
    rhythm = measure_rhythm(bgr, [0.02, 0.15, 0.94, 0.7], "AND HEALTH")
    check("tracked-word-gap", len(rhythm["gaps"]) == 1 and rhythm["gaps"][0] > rhythm["track"] > 0, str(rhythm))
    font = _fitz_font("cinzel-500")
    text = "AND HEALTH"
    box_w = 280.0
    size = _size_for_gaps(font, text, 14.0, box_w, rhythm["gaps"], rhythm["track"])
    advances = _char_advances(font, text, size, box_w, "spaced", rhythm["gaps"], rhythm["track"])
    total = sum(advances) or 1.0
    space = advances[text.index(" ")]
    letter = advances[0] - float(font.text_length("A", fontsize=size))
    check("tracked-space-held", space >= total * rhythm["gaps"][0] * 0.9, f"{space:.2f} of {total:.2f}")
    check("tracked-letters-held", letter >= total * rhythm["track"] * 0.7, f"{letter:.2f} size {size:.2f}")


def test_symbol_beside_the_words_stays() -> None:
    """A heart or other mark in the line, which the words do not name, is not erased."""
    from vector_plate import erase_text

    image = Image.new("RGB", (520, 140), (236, 230, 214))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(font_path("parisienne"), 46)
    draw.text((20, 40), "live your best life", font=face, fill=(36, 36, 38))
    draw.ellipse((430, 52, 468, 90), outline=(36, 36, 38), width=3)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    line = {
        "text": "live your best life",
        "mode": "vector",
        "rect": (16, 36, 470, 80),
        "quad": [[16, 36], [486, 36], [486, 116], [16, 116]],
    }
    before = bgr[50:94, 424:474].copy()
    clean, kept, _skipped = erase_text(bgr, [line], [])
    after = clean[50:94, 424:474]
    changed = int(np.max(cv2.absdiff(before, after)))
    check("symbol-pixels-stay", changed <= 8 and bool(kept), f"delta {changed} kept {len(kept)}")
    words = cv2.absdiff(bgr[40:110, 20:400], clean[40:110, 20:400])
    check("words-were-erased", int(words.max()) > 40, str(int(words.max())))


def test_repeated_tokens_fail() -> None:
    """A neighbouring word repeated on the rebuild, and not in the source, is a failure."""
    from vector_text_v2 import repeats_hold, vector_rebuild_enabled

    check("repeat-aa", repeats_hold("A small drop of blood", "A A small drop of blood") is False)
    check("repeat-amp", repeats_hold("INFECTIONS &", "INFECTIONS & &") is False)
    check("repeat-only", repeats_hold("By appointment only", "By appointment only only") is False)
    check("repeat-dd", repeats_hold("vitamin D", "vitamin DD") is False)
    check("repeat-clean", repeats_hold("A small drop of blood", "A small drop of blood") is True)
    check("repeat-ocr-noise", repeats_hold("See your health,", "Pee you health 0 0 3") is True)
    check("repeat-source-pair", repeats_hold("ha ha", "ha ha") is True)
    check("vector-on", vector_rebuild_enabled() is True)
    os.environ["VECTOR_REBUILD"] = "0"
    try:
        check("vector-off", vector_rebuild_enabled() is False)
        image = _poster()
        bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        out = tempfile.mkdtemp(prefix="vector-off-")
        result = rebuild_fitted(bgr, 148, 210, os.path.join(out, "press.pdf"), blocks=[
            _block("MARKET DAY", 0.12, 0.50, 0.5, 0.08, "#fff4d2"),
        ], reocr=lambda _path: "MARKET DAY")
        check("vector-off-falls-back", result.get("ok") is False and "switched off" in str(result.get("reason")), str(result.get("reason")))
    finally:
        os.environ.pop("VECTOR_REBUILD", None)


def test_body_tracking_does_not_shrink_the_face() -> None:
    """A body line's own sidebearings are not added again, so the face is not tiny."""
    from ai_rebuild import measure_rhythm
    from vector_text_v2 import _char_advances, _fit_width, _fitz_font, _resolve_fit

    image = Image.new("RGB", (520, 90), (244, 240, 230))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(font_path("crimson"), 28)
    draw.text((24, 28), "Skin conditions", font=face, fill=(40, 48, 36))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    rhythm = measure_rhythm(bgr, [0.02, 0.08, 0.92, 0.78], "Skin conditions")
    font = _fitz_font("crimson")
    text = "Skin conditions"
    box_w, box_h = 220.0, 22.0
    fitted, _ignored = _fit_width(font, text, box_w, box_h, "body")
    size, gaps, track = _resolve_fit(
        font, text, box_w, box_h, "body", rhythm.get("gaps") or [], rhythm.get("track") or 0.0,
    )
    check(
        "body-face-holds-height",
        size >= fitted * 0.88,
        f"size {size:.2f} fitted {fitted:.2f} track {track} rhythm {rhythm}",
    )
    advances = _char_advances(font, text, size, box_w, "body", gaps, track)
    glyph = float(font.text_length("S", fontsize=size))
    check("body-letters-not-spread", advances[0] < glyph * 1.35, f"adv {advances[0]:.2f} glyph {glyph:.2f}")


def test_icon_region_stays() -> None:
    """A one-glyph read on a circular icon is not retyped, and the icon pixels stay."""
    image = Image.new("RGB", (640, 420), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 80, 150, 160), fill=(36, 92, 58))
    draw.text((98, 100), "4", font=ImageFont.truetype(font_path("crimson"), 32), fill=(255, 255, 255))
    draw.text((190, 100), "You receive a plan", font=ImageFont.truetype(font_path("crimson"), 28), fill=(20, 28, 18))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    before = bgr[80:160, 70:150].copy()
    blocks = [
        _block("4", 90 / 640, 96 / 420, 28 / 640, 36 / 420, "#ffffff"),
        _block("You receive a plan", 0.28, 0.22, 0.5, 0.12, "#141c12"),
    ]
    out = tempfile.mkdtemp(prefix="vector-icon-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(image_bgr(image), 148, 100, pdf, blocks=blocks, reocr=lambda _path: "You receive a plan")
    check("icon-job", result.get("ok") is True, str(result.get("reason")))
    mark = [line for line in result.get("lines") or [] if line.get("text") == "4"]
    check("icon-stays-raster", bool(mark) and mark[0].get("mode") == "raster", str(mark))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = doc[0].get_text("text") or ""
    page = doc[0]
    # The icon sits in the source. Sample the same disk on the press render.
    scale, off_x, off_y = __import__("vector_plate", fromlist=["fit_placement"]).fit_placement(
        640, 420, 148, 100, 5, [(70, 80, 150, 160)],
    )
    cx = ((70 + 150) / 2.0) * scale + off_x
    cy = ((80 + 160) / 2.0) * scale + off_y
    pix = page.get_pixmap(dpi=72, alpha=False, colorspace=fitz.csRGB)
    frame = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    px = int(round(cx / 25.4 * 72))
    py = int(round(cy / 25.4 * 72))
    doc.close()
    patch = frame[max(0, py - 8):py + 8, max(0, px - 8):px + 8]
    source_green = int(((before[:, :, 1] > 70) & (before[:, :, 1] > before[:, :, 2] + 10)).sum())
    render_green = int(((patch[:, :, 1] > 60) & (patch[:, :, 1] > patch[:, :, 2])).sum()) if patch.size else 0
    check("icon-not-retyped", "4" not in text and "plan" in text.lower(), text[:200])
    check("icon-disk-stays", source_green > 100 and render_green > 20, f"source {source_green} render {render_green} at {px},{py}")


def test_spacing_assert_catches_a_joined_word() -> None:
    from vector_text_v2 import gaps_hold, tokens_hold

    check("tokens-joined", tokens_hold("AND HEALTH", "ANDHEALTH", 0.95) is False)
    check("tokens-same", tokens_hold("AND HEALTH", "AND HEALTH", 0.95) is True)
    check("tokens-empty", tokens_hold("AND HEALTH", "", 0.0) is True)
    check("gaps-within", gaps_hold([0.11], [0.14]) is True)
    check("gaps-wide", gaps_hold([0.11], [0.16]) is False)
    check("gaps-missing", gaps_hold([0.11], []) is False)
    check("gaps-none", gaps_hold([], []) is True)


def test_a_poor_body_match_stays_ink() -> None:
    """A line the body face does not resemble is not set in that serif."""
    from vector_text_v2 import _choose_font
    image = Image.new("RGB", (980, 160), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(font_path("parisienne"), 72)
    draw.text((40, 30), "feel your best, every day.", font=face, fill=(40, 48, 36))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    block = {
        "text": "feel your best, every day.",
        "bbox": [0.03, 0.15, 0.9, 0.55],
        "score": 0.98,
        "color_hex": "#283024",
    }
    decision = _choose_font(bgr, block, "serif")
    check(
        "script-tag-not-crimson",
        decision.get("font") != "crimson",
        f"{decision.get('mode')} {decision.get('font')} {decision.get('role')}",
    )


def test_flyer_headings_are_cinzel() -> None:
    """The headings the designer set in Cinzel stay vector, including Regular."""
    from ai_rebuild import _text_hex
    from vector_text_v2 import _choose_font

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bgr = cv2.imread(os.path.join(root, "tests/fixtures/medella/flyer_front.png"))
    check("flyer-heading-source", bgr is not None, "flyer_front.png")
    height, width = bgr.shape[:2]
    boxes = {
        "SEE BEYOND": (521.9, 43.4, 813.7, 89.8),
        "OUR APPROACH": (397.0, 884.9, 572.2, 908.1),
        "BOOK YOUR": (759.6, 943.0, 893.0, 966.2),
    }
    for text, (x0, y0, x1, y1) in boxes.items():
        colour = _text_hex(bgr, int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        decision = _choose_font(bgr, {
            "text": text,
            "bbox": [x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height],
            "score": 0.99,
            "color_hex": colour,
        }, "serif", 210)
        check(
            f"heading-vector-{text.split()[0].lower()}",
            decision.get("mode") == "vector" and str(decision.get("font") or "").startswith("cinzel") and float(decision.get("pt") or 0) >= 8,
            f"{decision.get('mode')} {decision.get('font')} {decision.get('pt')} {decision.get('reason')}",
        )


def test_small_script_gets_an_outline() -> None:
    """Script is not given a fixed outline. A face already as heavy as the ink stays bare."""
    from vector_text_v2 import _match_stroke, _render_ink, _stroke_for

    dark = {"color": "#242424", "stroke": 0.0}
    check("script-no-fixed-outline", _stroke_for(dark, 10.0, "script") == 0.0)
    check("large-script-unchanged", _stroke_for(dark, 21.0, "script") == 0.0)
    check("body-not-outlined", _stroke_for(dark, 10.0, "body") == 0.0)
    check("light-script-unchanged", _stroke_for({"color": "#f4f1e4", "stroke": 0.0}, 10.0, "script") == 0.0)
    rendered = _render_ink("live your best life", font_path("parisienne"), 420, 64, "script")
    check("script-match-no-extra", rendered is not None and _match_stroke(rendered, "live your best life", "parisienne") == 0.0)
    heavy = cv2.dilate(rendered, np.ones((3, 3), np.uint8))
    factor = _match_stroke(heavy, "live your best life", "parisienne")
    check("script-outline-capped", 0 <= factor <= 0.012, f"{factor}")


def test_heavy_caps_face_stays_ink() -> None:
    """A large hairline heading is Cinzel Regular with a paper stroke. Under 8 pt it stays ink."""
    from vector_text_v2 import _choose_font, _draw_line

    image = Image.new("RGB", (860, 180), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    cursor = 24
    for word in ("SEE", "BEYOND"):
        for _ch in word:
            draw.line((cursor, 120, cursor, 40), fill=(90, 96, 82), width=1)
            cursor += 28
        cursor += 48
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    decision = _choose_font(bgr, {
        "text": "SEE BEYOND",
        "bbox": [0.02, 0.12, 0.9, 0.55],
        "score": 0.98,
        "color_hex": "#5a6052",
    }, "serif", 210)
    knock = float(decision.get("knockout") or 0)
    check(
        "hairline-heading-is-regular",
        decision.get("mode") == "vector" and decision.get("font") == "cinzel-400" and decision.get("pt", 0) >= 8,
        f"{decision.get('mode')} {decision.get('font')} {decision.get('pt')} {decision.get('reason')}",
    )
    check("hairline-heading-knockout", 0 < knock <= 0.05, f"{knock}")
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page(width=420, height=140)
    drawn = dict(decision)
    drawn["media_box"] = (12, 24, 380, 90)
    _draw_line(page, drawn, 1.0, 1.0)
    raw = doc.xref_stream(page.get_contents()[-1]).decode("latin1")
    doc.close()
    stroke = re.search(r"(?m)^([0-9.]+(?:\s+[0-9.]+){3}) K$", raw)
    fill = re.search(r"(?m)^([0-9.]+(?:\s+[0-9.]+){3}) k$", raw)
    width = re.search(r"(?m)^([0-9.]+) w$", raw)
    size = re.search(r"/F\d+ ([0-9.]+) Tf", raw)
    check("knockout-stroke-is-paper", bool(stroke and fill) and stroke.group(1) != fill.group(1), raw[:240])
    check(
        "knockout-width",
        bool(width and size) and float(width.group(1)) <= float(size.group(1)) * 0.05 + 0.05,
        raw[:240],
    )

    tiny = Image.new("RGB", (1800, 4000), (246, 241, 228))
    pen = ImageDraw.Draw(tiny)
    left = 80
    for word in ("SEE", "BEYOND"):
        for _ch in word:
            pen.line((left, 1644, left, 1604), fill=(90, 96, 82), width=1)
            left += 22
        left += 40
    small = _choose_font(cv2.cvtColor(np.array(tiny), cv2.COLOR_RGB2BGR), {
        "text": "SEE BEYOND",
        "bbox": [0.02, 0.40, 0.55, 0.012],
        "score": 0.98,
        "color_hex": "#5a6052",
    }, "serif", 210)
    check(
        "tiny-caps-stay-ink",
        small.get("mode") == "raster" and float(small.get("pt") or 99) < 8 and "heavier" in str(small.get("reason") or ""),
        f"{small.get('mode')} {small.get('pt')} {small.get('reason')}",
    )


def test_stroke_follows_each_line() -> None:
    """A face that is already heavier than the ink gets no extra stroke."""
    from vector_text_v2 import _match_stroke, _render_ink

    rendered = _render_ink("medella.lba@gmail.com", font_path("crimson"), 420, 48, "body")
    check("email-weight-stroke", rendered is not None and _match_stroke(rendered, "medella.lba@gmail.com", "crimson") == 0.0)
    heavy = cv2.dilate(rendered, np.ones((5, 5), np.uint8))
    factor = _match_stroke(heavy, "medella.lba@gmail.com", "crimson")
    check("heavier-ink-gets-a-stroke", 0 < factor <= 0.014, f"{factor}")


def test_body_stroke_and_press_black() -> None:
    """Neutral dark type is solid K. A dark green name keeps its colour. Stroke stays under 5%."""
    from vector_text_v2 import _cmyk

    black = _cmyk((12 / 255, 11 / 255, 12 / 255))
    check("near-black-is-k", black[3] > 0.95 and max(black[:3]) < 0.05, str(black))
    green = _cmyk((15 / 255, 42 / 255, 27 / 255))
    check("green-heading-keeps-chroma", green[1] > 0.05 or green[3] < 0.95, str(green))
    name = _cmyk((12 / 255, 32 / 255, 24 / 255))
    check("green-name-not-k", name[3] < 0.98 or name[1] > 0.04, str(name))
    image = Image.new("RGB", (720, 240), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 160, 719, 239), fill=(24, 70, 48))
    draw.text((40, 40), "Early detection of imbalances", font=ImageFont.truetype(font_path("crimson"), 36), fill=(20, 24, 18))
    blocks = [_block("Early detection of imbalances", 0.04, 0.12, 0.8, 0.28, "#141812")]
    out = tempfile.mkdtemp(prefix="vector-stroke-")
    pdf = os.path.join(out, "press.pdf")
    result = rebuild_fitted(
        image_bgr(image), 80, 30, pdf, blocks=blocks,
        reocr=lambda _path: "Early detection of imbalances",
    )
    check("stroke-job", result.get("ok") is True, str(result.get("reason")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    raw = ""
    for xref in doc[0].get_contents():
        raw += doc.xref_stream(xref).decode("latin1", errors="ignore") + "\n"
    images = doc[0].get_images()
    info = doc.extract_image(images[0][0]) if images else {}
    doc.close()
    check("stroke-not-five-percent", "2 Tr" not in raw, raw[-400:])
    check("plate-is-cmyk", info.get("colorspace") == 4, str(info.get("colorspace")))


def test_numeral_mask_spares_the_circle() -> None:
    image = Image.new("RGB", (240, 180), (246, 241, 228))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 30, 130, 120), fill=(36, 92, 58))
    draw.text((72, 48), "1", font=ImageFont.truetype(font_path("crimson"), 42), fill=(255, 255, 255))
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    green_before = (bgr[:, :, 1] > 70) & (bgr[:, :, 1] > bgr[:, :, 2] + 10) & (bgr[:, :, 1] > bgr[:, :, 0] + 10)
    line = {"text": "1", "rect": (64, 44, 48, 56), "mode": "vector"}
    from vector_plate import erase_text

    clean, _kept, _skipped = erase_text(bgr, [line], [])
    green_after = (clean[:, :, 1] > 70) & (clean[:, :, 1] > clean[:, :, 2] + 10) & (clean[:, :, 1] > clean[:, :, 0] + 10)
    kept_green = int((green_before & green_after).sum())
    check("circle-survives", kept_green > int(green_before.sum()) * 0.72, f"kept {kept_green} of {int(green_before.sum())}")


def _line_ssim(ours: np.ndarray, approved: np.ndarray, lines: list, mapper) -> float:
    """Mean structural similarity over the approved OCR line boxes."""
    scores = []
    gray_ours = cv2.cvtColor(ours, cv2.COLOR_BGR2GRAY)
    gray_approved = cv2.cvtColor(approved, cv2.COLOR_BGR2GRAY)
    for line in lines:
        text = str(line.get("text") or "").strip()
        if len(text) <= 1:
            continue
        x0, y0, x1, y1 = line["box"]
        ax, ay = mapper(x0, y0)
        bx, by = mapper(x1, y1)
        pad = 3
        xa = max(0, int(min(ax, bx)) - pad)
        ya = max(0, int(min(ay, by)) - pad)
        xb = min(gray_ours.shape[1], int(max(ax, bx)) + pad)
        yb = min(gray_ours.shape[0], int(max(ay, by)) + pad)
        if xb - xa < 8 or yb - ya < 8:
            continue
        left = gray_ours[ya:yb, xa:xb].astype(np.float64)
        right = gray_approved[ya:yb, xa:xb].astype(np.float64)
        if left.shape != right.shape or min(left.shape) < 6:
            continue
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        kernel = (7, 7)
        mu_left = cv2.GaussianBlur(left, kernel, 1.2)
        mu_right = cv2.GaussianBlur(right, kernel, 1.2)
        var_left = cv2.GaussianBlur(left * left, kernel, 1.2) - mu_left * mu_left
        var_right = cv2.GaussianBlur(right * right, kernel, 1.2) - mu_right * mu_right
        cov = cv2.GaussianBlur(left * right, kernel, 1.2) - mu_left * mu_right
        score = ((2 * mu_left * mu_right + c1) * (2 * cov + c2)) / (
            (mu_left ** 2 + mu_right ** 2 + c1) * (var_left + var_right + c2)
        )
        scores.append(float(score.mean()))
    if not scores:
        return 0.0
    return float(np.mean(scores))


def _render_trim(pdf: str, dest: str) -> np.ndarray:
    import subprocess

    media = dest + ".media.png"
    subprocess.check_call([
        "gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r300",
        "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
        f"-sOutputFile={media}", pdf,
    ])
    image = cv2.imread(media)
    inset = int(round(5 / 25.4 * 300))
    return image[inset:image.shape[0] - inset, inset:image.shape[1] - inset]


def test_medella_matches_approved_text() -> None:
    """The three approved sides stay close. A ghosted page scores far below this."""
    import json

    from vector_plate import fit_placement

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ocr = json.load(open(os.path.join(root, "tools/reference_vector_rebuild/run_data/ocr.json"), encoding="utf-8"))
    sides = (
        ("flyer_front", 148, 210, 0.58),
        ("flyer_back", 148, 210, 0.58),
        ("card_front", 90, 50, 0.55),
    )
    for name, trim_w, trim_h, floor in sides:
        src = os.path.join(root, "tests/fixtures/medella", f"{name}.png")
        bgr = cv2.imread(src)
        folder = tempfile.mkdtemp(prefix=f"medella-{name}-")
        pdf = os.path.join(folder, "press.pdf")
        result = rebuild_fitted(bgr, trim_w, trim_h, pdf)
        check(f"{name}-ok", result.get("ok") is True, str(result.get("reason")))
        check(f"{name}-time", float(result.get("elapsed_s") or 999) < 120, str(result.get("timings")))
        lines = ocr[name]["lines"]
        boxes = [tuple(line["box"]) for line in lines]
        scale, off_x, off_y = fit_placement(bgr.shape[1], bgr.shape[0], trim_w, trim_h, 5, boxes)

        def mapper(x, y, scale=scale, off_x=off_x, off_y=off_y):
            return (off_x + x * scale - 5) / 25.4 * 300, (off_y + y * scale - 5) / 25.4 * 300

        rendered = _render_trim(pdf, os.path.join(folder, "trim"))
        approved = cv2.imread(os.path.join(root, "tests/fixtures/medella/approved", f"{name}_300dpi_trim.png"))
        check(f"{name}-size", rendered.shape == approved.shape, f"{rendered.shape} vs {approved.shape}")
        score = _line_ssim(rendered, approved, lines, mapper)
        print(f"SSIM {name} {score:.3f}")
        check(f"{name}-ssim", score >= floor, f"{score:.3f} < {floor}")

    src = os.path.join(root, "tests/fixtures/medella/card_back.png")
    bgr = cv2.imread(src)
    folder = tempfile.mkdtemp(prefix="medella-card-back-")
    pdf = os.path.join(folder, "press.pdf")
    result = rebuild_fitted(bgr, 90, 50, pdf)
    check("card-back-ok", result.get("ok") is True, str(result.get("reason")))
    check("card-back-time", float(result.get("elapsed_s") or 999) < 120, str(result.get("timings")))
    check("card-back-vector", int(result.get("vector_lines") or 0) >= 40, str(result.get("vector_lines")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = " ".join((doc[0].get_text("text") or "").split()).upper()
    doc.close()
    check("card-back-words", "LIVE" in text and "BLOOD" in text and "IMMUNE" in text, text[:240])


def image_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def test_real_ocr_is_quick() -> None:
    import time

    image = _poster()
    path = os.path.join(tempfile.mkdtemp(prefix="vector-ocr-"), "src.png")
    image.save(path)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    out = tempfile.mkdtemp(prefix="vector-real-")
    pdf = os.path.join(out, "press.pdf")
    started = time.perf_counter()
    result = rebuild_fitted(bgr, 148, 210, pdf)
    elapsed = time.perf_counter() - started
    print(f"TIME real-ocr {elapsed:.1f}s {result.get('timings')}")
    check("real-ocr-time", elapsed < 90, f"{elapsed:.1f}s")
    check("real-ocr-ok", result.get("ok") is True, str(result.get("reason")) + " " + str((result.get("qa") or {}).get("reocr")))
    import pymupdf as fitz
    doc = fitz.open(pdf)
    text = (doc[0].get_text("text") or "").upper()
    doc.close()
    check("real-ocr-vector", "MARKET" in text, text[:200])
    os.makedirs(ART, exist_ok=True)
    _render(pdf, os.path.join(ART, "vector-v2-synthetic.png"))


def _poster() -> Image.Image:
    image = Image.new("RGB", (900, 1200), (24, 72, 140))
    draw = ImageDraw.Draw(image)
    draw.ellipse((560, 80, 820, 340), fill=(240, 120, 20))
    draw.rounded_rectangle((80, 520, 820, 1040), radius=28, fill=(12, 28, 70))
    font = ImageFont.truetype(SANS, 72)
    small = ImageFont.truetype(SANS, 48)
    draw.text((120, 620), "MARKET DAY", font=font, fill=(255, 244, 210))
    draw.text((120, 760), "SATURDAY", font=small, fill=(255, 255, 255))
    return image


def _block(text: str, x: float, y: float, w: float, h: float, colour: str) -> dict:
    return {
        "id": text,
        "text": text,
        "bbox": [x, y, w, h],
        "color_hex": colour,
        "bold": True,
        "score": 0.98,
    }


def _render(path: str, dest: str) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.1, 1.1), alpha=False)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    pix.save(dest)
    doc.close()


def main() -> None:
    test_faces_and_leashes()
    test_width_fit()
    test_script_stays_raster()
    test_overlap_and_qa_gate()
    test_bullet_cmyk_and_boxes()
    test_refine_repairs_a_joined_word()
    test_reread_cannot_swap_the_line()
    test_list_item_is_not_script()
    test_tick_leaves_the_mask()
    test_serif_page_is_consistent()
    test_badge_digit_stays()
    test_shared_scale_keeps_the_whole_picture()
    test_placement_matches_the_reference_fit()
    test_erase_clears_the_original()
    test_numeral_mask_spares_the_circle()
    test_core_ink_ignores_the_green_edge()
    test_step_circles_are_the_only_badges()
    test_badge_number_is_not_retyped()
    test_dotted_caps_keep_their_gaps()
    test_long_script_is_not_a_serif()
    test_tracked_caps_keep_letter_and_word_gaps()
    test_symbol_beside_the_words_stays()
    test_repeated_tokens_fail()
    test_body_tracking_does_not_shrink_the_face()
    test_icon_region_stays()
    test_spacing_assert_catches_a_joined_word()
    test_a_poor_body_match_stays_ink()
    test_heavy_caps_face_stays_ink()
    test_flyer_headings_are_cinzel()
    test_small_script_gets_an_outline()
    test_stroke_follows_each_line()
    test_body_stroke_and_press_black()
    test_real_ocr_is_quick()
    test_medella_matches_approved_text()
    print("ALL PASS")


if __name__ == "__main__":
    main()
