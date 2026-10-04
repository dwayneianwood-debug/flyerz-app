#!/usr/bin/env python3
"""Checklist and the red client messages. Nothing is sent."""

from __future__ import annotations

import os
import tempfile

from PIL import Image

from green_gate import (
    CUT_MESSAGE,
    DECORATIVE_REASON,
    MISSING_MESSAGE,
    PROPORTION_MESSAGE,
    SMALL_PICTURE_REASON,
    assess,
    client_message,
)
from quick_print import decide_light, make_print_ready


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _bled(path: str) -> None:
    import pymupdf as fitz

    mm = 72.0 / 25.4
    bleed = 5 * mm
    trim_w, trim_h = 148 * mm, 210 * mm
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


def _image_pdf(path: str, pixels: int, page_pt: float) -> None:
    import io

    import numpy as np
    import pymupdf as fitz
    from PIL import Image

    image = Image.fromarray(np.full((pixels, pixels, 3), 40, np.uint8), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    doc = fitz.open()
    page = doc.new_page(width=page_pt, height=page_pt)
    page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(path)
    doc.close()


def test_embedded_dpi() -> None:
    folder = tempfile.mkdtemp(prefix="gate-dpi-")
    sharp = os.path.join(folder, "sharp.pdf")
    soft = os.path.join(folder, "soft.pdf")
    _image_pdf(sharp, 400, 72)
    _image_pdf(soft, 100, 72)
    high = assess(sharp, 20, 20, {"upscale": 2.0})
    low = assess(soft, 20, 20, {"upscale": 2.0})
    items_high = {item["id"]: item for item in high["items"]}
    items_low = {item["id"]: item for item in low["items"]}
    check("dpi-embedded-passes", items_high["resolution"]["passed"], str(items_high["resolution"]))
    check("dpi-embedded-fails", items_low["resolution"]["passed"] is False and "100" in items_low["resolution"]["detail"], str(items_low["resolution"]))


def test_sharpen_keeps_the_hole() -> None:
    import numpy as np

    from vector_trace import _upgrade_raster

    rng = np.random.default_rng(1)
    plate = rng.integers(40, 220, (80, 120, 3), dtype=np.uint8)
    hole = plate.copy()
    drawn = [{
        "_ink": np.ones((20, 40), np.uint8) * 255,
        "origin": (40, 30),
    }]
    # The fringe of that ink is the hole. Source DPI under 300 forces a sharpen.
    placed = {"scale_mm": 25.4 / 180.0, "art_box": (0, 0, 120, 80)}
    upgraded, note = _upgrade_raster(plate, drawn, placed, plate)
    check("sharpen-note", note == "The picture was enlarged with Lanczos.", note)
    check("sharpen-plate", np.array_equal(upgraded, hole))
    from ai_upscale import PlateUpscale
    from vector_trace import _paint_protect

    ready = PlateUpscale(plate, 148, 210)
    ready._done.set()
    ready.image = np.full(plate.shape, (0, 180, 40), np.uint8)
    upgraded, note = _upgrade_raster(plate, drawn, placed, plate, ready)
    protect = _paint_protect(plate, drawn)
    check("sharpen-esrgan-note", "Real-ESRGAN" in note, note)
    check("sharpen-hole", np.array_equal(upgraded[protect > 0], hole[protect > 0]))
    check("sharpen-photo", not np.array_equal(upgraded[protect == 0], plate[protect == 0]))


def test_messages() -> None:
    check("msg-cut", "cut line" in client_message("cut").lower() and client_message("cut") == CUT_MESSAGE)
    check("msg-missing", "missing" in MISSING_MESSAGE.lower())
    check("msg-shape", "proportions" in PROPORTION_MESSAGE.lower())
    check("msg-low", "300" in client_message("low-res"))
    shape = decide_light({"unextendable": True})
    check("shape-red", shape["light"] == "red" and shape["clientMessage"] == PROPORTION_MESSAGE, str(shape))
    blank = decide_light({"missingContent": True})
    check("missing-red", blank["light"] == "red" and blank["clientMessage"] == MISSING_MESSAGE, str(blank))
    cut = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {
            "severity": "red",
            "clientMessage": CUT_MESSAGE,
            "items": [{"id": "safe", "label": "safe", "passed": False, "detail": "Text sits on the cut line (HELLO)."}],
        },
    })
    check("cut-red", cut["light"] == "red" and "cut line" in cut["clientMessage"].lower(), str(cut))
    amber = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {
            "items": [
                {"id": "cmyk", "label": "CMYK", "passed": False, "detail": "The press picture is not CMYK."},
                {"id": "bleed", "label": "bleed", "passed": True, "detail": ""},
            ],
        },
    })
    check("checklist-amber", amber["light"] == "amber" and any("CMYK" in line for line in amber["reasons"]), str(amber))
    green = decide_light({
        "compiled": True,
        "enginePassed": True,
        "checklist": {"items": [{"id": "bleed", "label": "bleed", "passed": True, "detail": ""}]},
    })
    check("checklist-green", green["light"] == "green" and green["clientMessage"] == "", str(green))


def test_bled_pdf_explains_itself() -> None:
    folder = tempfile.mkdtemp(prefix="gate-bled-")
    path = os.path.join(folder, "already.pdf")
    _bled(path)
    report = assess(path, 148, 210, {})
    items = {item["id"]: item for item in report["items"]}
    check("bled-ids", {"bleed", "boxes", "cmyk", "fonts", "safe", "letters"} <= set(items), str(list(items)))
    check("bled-bleed", items["bleed"]["passed"] and items["boxes"]["passed"], str(items["bleed"]) + str(items["boxes"]))
    check("bled-not-cmyk", items["cmyk"]["passed"] is False, str(items["cmyk"]))
    check("bled-font", items["fonts"]["passed"] is False, str(items["fonts"]))
    check("bled-safe", items["safe"]["passed"] is True, str(items["safe"]))
    check("bled-not-red", report["severity"] != "red", str(report["severity"]))


def test_blank_and_wrong_shape() -> None:
    folder = tempfile.mkdtemp(prefix="gate-red-")
    white = os.path.join(folder, "blank.png")
    Image.new("RGB", (800, 1100), (255, 255, 255)).save(white)
    result = make_print_ready(white, os.path.join(folder, "out-blank"), 148, 210, "a5", "A5", filename="blank.png")
    check("blank-red", result["light"] == "red" and "missing" in result["clientMessage"].lower(), result.get("clientMessage"))
    check("blank-not-sent", "send" not in result["clientMessage"].lower() or "please send" in result["clientMessage"].lower())

    import pymupdf as fitz

    wrong = os.path.join(folder, "card.pdf")
    doc = fitz.open()
    page = doc.new_page(width=90 * 72 / 25.4, height=50 * 72 / 25.4)
    page.insert_text((20, 30), "WRONG SHAPE", fontsize=12, fontname="helv")
    doc.save(wrong)
    doc.close()
    shaped = make_print_ready(wrong, os.path.join(folder, "out-shape"), 148, 210, "a5", "A5", filename="card.pdf")
    check("shape-job-red", shaped["light"] == "red" and "proportions" in shaped["clientMessage"].lower(), shaped.get("clientMessage"))
    check("shape-no-auto", shaped.get("clientMessage") and "http" not in shaped["clientMessage"].lower())


def test_paint_and_ink_colour() -> None:
    import pymupdf as fitz

    folder = tempfile.mkdtemp(prefix="gate-type-")

    def draw(path: str, behind: bool, color: tuple) -> None:
        doc = fitz.open()
        page = doc.new_page(width=220, height=90)
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
        if behind:
            page.draw_rect(fitz.Rect(26, 30, 122, 61), color=None, fill=(0.62, 0.62, 0.62))
        page.insert_text((30, 52), "Hello there", fontsize=18, fontname="helv", color=color)
        doc.save(path)
        doc.close()

    clean = os.path.join(folder, "clean.pdf")
    dirty = os.path.join(folder, "dirty.pdf")
    grey = os.path.join(folder, "grey.pdf")
    draw(clean, False, (0.05, 0.08, 0.04))
    draw(dirty, True, (0.05, 0.08, 0.04))
    draw(grey, False, (0.55, 0.55, 0.55))
    context = {"textGate": [{"text": "Hello there", "retyped": True, "sourceInk": [13, 20, 10], "mode": "vector"}]}
    clean_items = {item["id"]: item for item in assess(clean, 50, 20, context)["items"]}
    dirty_items = {item["id"]: item for item in assess(dirty, 50, 20, context)["items"]}
    grey_items = {item["id"]: item for item in assess(grey, 50, 20, context)["items"]}
    check("paint-clean", clean_items["paint"]["passed"], str(clean_items["paint"]))
    check("paint-dirty", dirty_items["paint"]["passed"] is False, str(dirty_items["paint"]))
    check("colour-core", clean_items["typecolour"]["passed"], str(clean_items["typecolour"]))
    check("colour-grey", grey_items["typecolour"]["passed"] is False, str(grey_items["typecolour"]))
    black = os.path.join(folder, "black.pdf")
    draw(black, False, (0, 0, 0))
    black_items = {item["id"]: item for item in assess(black, 50, 20, context)["items"]}
    check("colour-black", black_items["typecolour"]["passed"] is False, str(black_items["typecolour"]))


def test_sharpness_and_one_resample() -> None:
    import cv2
    import numpy as np
    import pymupdf as fitz

    from vector_plate import _enlarge

    rng = np.random.default_rng(3)
    sharp = rng.integers(0, 255, (160, 220, 3), dtype=np.uint8)
    soft = cv2.GaussianBlur(sharp, (0, 0), 2.4)
    same, provider = _enlarge(sharp, sharp.shape[1], sharp.shape[0])
    check("one-resample", provider == "Lanczos" and np.array_equal(same, sharp))

    def write(image: np.ndarray, path: str) -> None:
        from PIL import Image

        height, width = image.shape[:2]
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        buffer = __import__("io").BytesIO()
        Image.fromarray(rgb).save(buffer, format="PNG")
        doc = fitz.open()
        page = doc.new_page(width=width * 72 / 600, height=height * 72 / 600)
        page.insert_image(page.rect, stream=buffer.getvalue())
        doc.save(path)
        doc.close()

    folder = tempfile.mkdtemp(prefix="gate-sharp-")
    sharp_pdf = os.path.join(folder, "sharp.pdf")
    soft_pdf = os.path.join(folder, "soft.pdf")
    write(sharp, sharp_pdf)
    write(soft, soft_pdf)
    context = {
        "sourceBgr": sharp,
        "placement": {"artBox": [0, 0, sharp.shape[1], sharp.shape[0]], "ppi": 600},
    }
    sharp_items = {item["id"]: item for item in assess(sharp_pdf, 20, 16, context)["items"]}
    soft_items = {item["id"]: item for item in assess(soft_pdf, 20, 16, context)["items"]}
    check("sharp-kept", sharp_items["sharp"]["passed"], str(sharp_items["sharp"]))
    check("sharp-soft", soft_items["sharp"]["passed"] is False, str(soft_items["sharp"]))


def test_picture_text_is_not_green() -> None:
    """Raster lettering is amber. Vector and retyped lines stay clear of that."""
    import numpy as np

    folder = tempfile.mkdtemp(prefix="gate-picture-")
    path = os.path.join(folder, "page.pdf")
    # A flat page is read as empty and turns the job red. This one has a picture.
    rng = np.random.default_rng(2)
    noisy = Image.fromarray(rng.integers(30, 200, (90, 120, 3), dtype=np.uint8), mode="RGB")
    import io
    import pymupdf as fitz

    buffer = io.BytesIO()
    noisy.save(buffer, format="JPEG", quality=80)
    doc = fitz.open()
    page = doc.new_page(width=120, height=90)
    page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(path)
    doc.close()
    place = {"artBox": [0, 0, 240, 80], "plate": [80, 240], "ppi": 600}
    # The box covers the whole plate, so the line is far over 14 pt.
    display = [{"text": "DISPLAY HEADING", "mode": "raster", "boxMm": [-5, -5, 100, 60]}]

    def report_for(gate, source=None):
        context = {"textGate": gate}
        if source is not None:
            context["sourceBgr"] = source
            context["placement"] = place
        report = assess(path, 90, 50, context)
        return report, {item["id"]: item for item in report["items"]}

    small = [{"text": "Boosts your body's defences", "mode": "raster", "boxMm": [2, 2, 30, 2.0]}]
    report, items = report_for(small)
    check(
        "picture-small",
        items["picturetext"]["passed"] is False and items["picturetext"]["detail"] == SMALL_PICTURE_REASON,
        str(items.get("picturetext")),
    )
    check("picture-small-not-red", report["severity"] != "red", str(report["severity"]))
    light = decide_light({"compiled": True, "enginePassed": True, "checklist": {"items": report["items"], "severity": report["severity"]}})
    check(
        "picture-small-amber",
        light["light"] == "amber" and SMALL_PICTURE_REASON in light["reasons"] and light["clientMessage"] == "",
        str(light),
    )

    vector = [{"text": "Boosts your body's defences", "mode": "vector", "ok": True, "boxMm": [2, 2, 30, 2.0]}]
    _report, items = report_for(vector)
    check("picture-vector", items["picturetext"]["passed"] is True, str(items["picturetext"]))
    retyped = [{"text": "Boosts your body's defences", "mode": "vector", "retyped": True, "boxMm": [2, 2, 30, 2.0]}]
    _report, items = report_for(retyped)
    check("picture-retyped", items["picturetext"]["passed"] is True, str(items["picturetext"]))
    icon = [{"text": "+", "mode": "raster", "boxMm": [2, 2, 2, 2]}]
    _report, items = report_for(icon)
    check("picture-icon", items["picturetext"]["passed"] is True, str(items["picturetext"]))

    flat = np.full((80, 240, 3), 230, np.uint8)
    flat[20:60, 30:210] = (25, 40, 30)
    _report, items = report_for(display, flat)
    check(
        "picture-heading",
        items["picturetext"]["passed"] is False and items["picturetext"]["detail"] == SMALL_PICTURE_REASON,
        str(items.get("picturetext")),
    )
    check("picture-heading-not-decor", "decorative" not in items, str(items.get("decorative")))

    gradient = np.full((80, 240, 3), 230, np.uint8)
    for x in range(30, 210):
        fade = (x - 30) / 180.0
        gradient[20:60, x] = (20, int(30 + 140 * fade), int(25 + 20 * fade))
    _report, items = report_for(display, gradient)
    check(
        "picture-decor",
        items.get("decorative", {}).get("passed") is False and items["decorative"]["detail"] == DECORATIVE_REASON,
        str(items.get("decorative")),
    )
    check("picture-decor-not-small", items["picturetext"]["passed"] is True, str(items.get("picturetext")))
    sentence = [{"text": "A wide range of conditions today", "mode": "raster", "boxMm": [-5, -5, 100, 60]}]
    _report, items = report_for(sentence, gradient)
    check(
        "picture-body-not-decor",
        items["picturetext"]["passed"] is False and "decorative" not in items,
        str(items.get("picturetext")) + str(items.get("decorative")),
    )


def test_picture_upscale_message() -> None:
    import numpy as np

    from ai_upscale import ESRGAN_MESSAGE, LANCZOS_MESSAGE, PlateUpscale
    from vector_trace import _enlarge_line, _upgrade_raster

    plate = np.full((80, 120, 3), 25, np.uint8)
    placed = {"scale_mm": 25.4 / 180.0, "art_box": (0, 0, 120, 80)}
    missed = PlateUpscale(plate, 148, 210)
    missed._done.set()
    _upgraded, note = _upgrade_raster(plate, [], placed, plate, missed)
    line = _enlarge_line("Lanczos", note)
    from vector_trace import _named_provider

    check("picture-one-lanczos", line == LANCZOS_MESSAGE and "was not used" not in line, line)
    check("provider-lanczos", _named_provider(note) == "Lanczos" and "Lanczos" in line, line)
    ready = PlateUpscale(plate, 148, 210)
    ready._done.set()
    ready.image = np.full((30, 40, 3), (0, 180, 0), np.uint8)
    _upgraded, note = _upgrade_raster(plate, [], placed, plate, ready)
    line = _enlarge_line("Real-ESRGAN", note)
    check("picture-one-esrgan", line == ESRGAN_MESSAGE and "Lanczos" not in line, line)
    check("provider-esrgan", _named_provider(note) == "Real-ESRGAN" and "Real-ESRGAN" in line, line)


def test_esrgan_only_with_token() -> None:
    import numpy as np

    from ai_upscale import full_frame_esrgan
    from vector_trace import _upscale_choice

    check("choice-esrgan", _upscale_choice("token", np.zeros((4, 4, 3), np.uint8)) == "esrgan")
    check("choice-fallback", _upscale_choice("token", None) == "lanczos")
    check("choice-lanczos", _upscale_choice("", None) == "lanczos")
    saved = os.environ.pop("REPLICATE_API_TOKEN", None)
    try:
        check("no-token-no-esrgan", full_frame_esrgan(np.zeros((16, 16, 3), np.uint8)) is None)
    finally:
        if saved is not None:
            os.environ["REPLICATE_API_TOKEN"] = saved


def main() -> None:
    test_messages()
    test_embedded_dpi()
    test_sharpen_keeps_the_hole()
    test_paint_and_ink_colour()
    test_sharpness_and_one_resample()
    test_picture_text_is_not_green()
    test_picture_upscale_message()
    test_esrgan_only_with_token()
    test_bled_pdf_explains_itself()
    test_blank_and_wrong_shape()
    print("GREEN GATE CHECKS PASSED")


if __name__ == "__main__":
    main()
