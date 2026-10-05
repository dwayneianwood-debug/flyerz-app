#!/usr/bin/env python3
"""Files real customers send, on top of the every-size matrix.

Each case is judged with the 25-point list and E1–E9. A press plate is built
only when the sheet fits the memory leash.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

MM = 72.0 / 25.4
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
ROWS: list[dict] = []
FAILURES: list[str] = []


def _record(case: str, ok: bool, detail: str) -> None:
    ROWS.append({"case": case, "ok": ok, "detail": detail})
    print(("PASS" if ok else "FAIL") + f" {case} {detail}", flush=True)
    if not ok:
        FAILURES.append(case)


def _order(width: float, height: float, **extra) -> dict:
    return {"explicitSize": True, "widthMm": width, "heightMm": height, "cmykOnly": True, **extra}


def _by_id(report: dict) -> dict:
    return {row["id"]: row for row in report.get("checks") or []}


def _point(checks: list, num: str) -> dict:
    for row in checks:
        if str(row.get("num")) == num:
            return row
    return {}


def _press_mm(path: str) -> tuple[float, float]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        box = doc[0].mediabox
        return box.width * 25.4 / 72.0, box.height * 25.4 / 72.0
    finally:
        doc.close()


def _text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        return "\n".join((page.get_text("text") or "") for page in doc)
    finally:
        doc.close()


def _job(src: str, folder: str, width: float, height: float, product: str, label: str, detect: bool = False) -> dict:
    from quick_print import make_print_ready

    return make_print_ready(
        src,
        folder,
        width,
        height,
        product,
        label,
        filename=os.path.basename(src),
        detect_size=detect,
    )


def _page(width_mm: float, height_mm: float, text: str = "", rgb: tuple | None = (0.1, 0.35, 0.7), embed: bool = True):
    import pymupdf as fitz

    page_w = width_mm * MM
    page_h = height_mm * MM
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    if rgb is not None:
        page.draw_rect(page.rect, color=None, fill=rgb)
    if text:
        if embed and os.path.exists(FONT):
            page.insert_font(fontname="DEJAVU", fontfile=FONT)
            page.insert_text((page_w * 0.32, page_h * 0.55), text, fontsize=14, fontname="DEJAVU", color=(1, 1, 1))
        else:
            page.insert_text((page_w * 0.32, page_h * 0.55), text, fontsize=14, fontname="helv", color=(0, 0, 0))
    page.set_mediabox(page.rect)
    page.set_cropbox(page.rect)
    page.set_trimbox(page.rect)
    page.set_bleedbox(page.rect)
    return doc


def _save(doc, path: str) -> None:
    doc.save(path)
    doc.close()


def _expect_press(case: str, result: dict, width: float, height: float, problems: list[str]) -> None:
    press = result.get("pressPath") or ""
    if not press or not os.path.exists(press):
        problems.append("no press file")
        return
    got_w, got_h = _press_mm(press)
    if abs(got_w - (width + 10)) > 1.5 or abs(got_h - (height + 10)) > 1.5:
        problems.append(f"press {got_w:.1f}x{got_h:.1f}")


def _false_reasons(result: dict) -> list[str]:
    blob = " ".join(str(item) for item in list(result.get("reasons") or []) + list(result.get("decisions") or [])).lower()
    if result.get("clientMessage"):
        blob += " " + str(result.get("clientMessage")).lower()
    found = []
    for phrase in ("traceback", "could not be read", "cannot be extended"):
        if phrase in blob:
            found.append(phrase)
    return found


def test_inputs(folder: str) -> None:
    from extra_checks import assess_extras
    from twenty_five import assess
    import pymupdf as fitz
    from PIL import Image

    # JPG and PNG at 300 dpi for a card.
    for kind, ext in (("jpg", "JPEG"), ("png", "PNG")):
        path = os.path.join(folder, f"card.{kind}")
        image = Image.new("RGB", (int(round(90 / 25.4 * 300)), int(round(50 / 25.4 * 300))), (30, 90, 160))
        image.save(path, format=ext, dpi=(300, 300), quality=90)
        result = _job(path, os.path.join(folder, kind), 90, 50, "card-90x50", "Business card 90 × 50")
        problems = _false_reasons(result)
        if result.get("light") == "red":
            problems.append("red " + " ".join(result.get("reasons") or [])[:120])
        if result.get("productId") != "card-90x50":
            problems.append(f"product {result.get('productId')}")
        _expect_press(kind, result, 90, 50, problems)
        _record(f"input-{kind}", not problems, "; ".join(problems) or str(result.get("light")))

    # Phone screenshot: low-res RGB, still printable, so the light is amber for the enlarge.
    phone = os.path.join(folder, "phone.png")
    Image.new("RGB", (280, 160), (200, 40, 40)).save(phone)
    result = _job(phone, os.path.join(folder, "phone"), 90, 50, "card-90x50", "Business card 90 × 50")
    reasons = " ".join(result.get("reasons") or []).lower()
    problems = _false_reasons(result)
    if result.get("light") != "amber":
        problems.append(f"light {result.get('light')} {reasons[:80]}")
    if "enlarg" not in reasons:
        problems.append("no enlarge note")
    _expect_press("phone", result, 90, 50, problems)
    _record("input-phone-screenshot", not problems, "; ".join(problems) or "amber")

    # PDF-compatible Illustrator, and a real EPS.
    ai_pdf = os.path.join(folder, "art.pdf")
    _save(_page(90, 50, "AI"), ai_pdf)
    ai_path = os.path.join(folder, "art.ai")
    with open(ai_pdf, "rb") as src, open(ai_path, "wb") as dest:
        dest.write(src.read())
    result = _job(ai_path, os.path.join(folder, "ai"), 90, 50, "card-90x50", "Business card 90 × 50")
    problems = _false_reasons(result)
    joined = " ".join(result.get("decisions") or [])
    if "Illustrator" not in joined:
        problems.append("not read as illustrator")
    if result.get("light") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:100])
    _expect_press("ai", result, 90, 50, problems)
    _record("input-ai", not problems, "; ".join(problems) or str(result.get("light")))

    eps = os.path.join(folder, "art.eps")
    with open(eps, "w", encoding="ascii") as handle:
        handle.write(
            "%!PS-Adobe-3.0 EPSF-3.0\n"
            "%%BoundingBox: 0 0 255 142\n"
            "%%HiResBoundingBox: 0 0 255 142\n"
            "%%LanguageLevel: 2\n"
            "%%EndComments\n"
            "0.1 0.35 0.7 setrgbcolor\n"
            "0 0 255 142 rectfill\n"
            "showpage\n"
            "%%EOF\n"
        )
    result = _job(eps, os.path.join(folder, "eps"), 90, 50, "card-90x50", "Business card 90 × 50")
    problems = _false_reasons(result)
    if result.get("light") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:120])
    _expect_press("eps", result, 90, 50, problems)
    _record("input-eps", not problems, "; ".join(problems) or str(result.get("light")))

    # A Word file cannot go to press. A PDF exported from Word can.
    docx = os.path.join(folder, "brief.docx")
    with open(docx, "wb") as handle:
        handle.write(b"PK\x03\x04word-document-placeholder")
    result = _job(docx, os.path.join(folder, "docx"), 148, 210, "a5", "A5")
    reasons = " ".join(result.get("reasons") or [])
    problems = []
    if result.get("light") != "red" or "Word" not in reasons:
        problems.append(f"{result.get('light')} {reasons[:80]}")
    if result.get("pressPath"):
        problems.append("press file for a word document")
    if "traceback" in reasons.lower():
        problems.append("traceback")
    _record("input-docx", not problems, "; ".join(problems) or "office red")

    word_pdf = os.path.join(folder, "from-word.pdf")
    _save(_page(148, 210, "WORD"), word_pdf)
    result = _job(word_pdf, os.path.join(folder, "wordpdf"), 148, 210, "a5", "A5")
    problems = _false_reasons(result)
    if result.get("light") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:100])
    if "Word and PowerPoint" in " ".join(result.get("reasons") or []):
        problems.append("treated as office")
    _expect_press("wordpdf", result, 148, 210, problems)
    _record("input-word-export-pdf", not problems, "; ".join(problems) or str(result.get("light")))

    # CMYK and RGB PDFs.
    rgb = os.path.join(folder, "rgb.pdf")
    _save(_page(90, 50, "RGB"), rgb)
    points = assess(rgb, 90, 50)
    extras = assess_extras(rgb, "", _order(90, 50))
    e1 = _by_id(extras)["extra_E1"]
    problems = []
    if _point(points["checks"], "2").get("status") != "warning":
        problems.append("point 2 " + str(_point(points["checks"], "2").get("status")))
    if e1.get("status") != "pass":
        problems.append("E1 " + str(e1.get("status")))
    result = _job(rgb, os.path.join(folder, "rgbjob"), 90, 50, "card-90x50", "Business card 90 × 50")
    if result.get("light") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:80])
    _expect_press("rgb", result, 90, 50, problems)
    _record("input-rgb-pdf", not problems, "; ".join(problems) or str(result.get("light")))

    cmyk = os.path.join(folder, "cmyk-live.pdf")
    doc = _page(90, 50, "CMYK")
    page = doc[0]
    raw = page.read_contents().replace(b"0 0 0 rg", b"0 0 0 1 k").replace(b"1 1 1 rg", b"0 0 0 0 k")
    doc.update_stream(page.get_contents()[0], raw)
    _save(doc, cmyk)
    points = assess(cmyk, 90, 50)
    problems = []
    if _point(points["checks"], "2").get("status") not in ("passed", "warning"):
        problems.append("point 2 " + str(_point(points["checks"], "2").get("detail"))[:80])
    if _point(points["checks"], "4").get("status") == "failed":
        problems.append("font " + str(_point(points["checks"], "4").get("detail"))[:80])
    _record("input-cmyk-pdf", not problems, "; ".join(problems) or "cmyk")

    # Canva-style equal boxes, about 2.5 mm.
    canva = os.path.join(folder, "canva.pdf")
    doc = _page(90 + 5, 50 + 5, "CANVA")
    _save(doc, canva)
    result = _job(canva, os.path.join(folder, "canva"), 90, 50, "card-90x50", "Business card 90 × 50", detect=True)
    problems = _false_reasons(result)
    if result.get("productId") != "card-90x50":
        problems.append(f"product {result.get('productId')}")
    if not result.get("existingBleedKept"):
        problems.append("bleed not kept")
    if result.get("light") == "red":
        problems.append("red " + " ".join(result.get("reasons") or [])[:80])
    _expect_press("canva", result, 90, 50, problems)
    _record("input-canva-pdf", not problems, "; ".join(problems) or str(result.get("light")))

    # Outlined drawing: no font. Live embedded type. Live Helvetica, not embedded.
    outlined = os.path.join(folder, "outlined.pdf")
    doc = fitz.open()
    page = doc.new_page(width=90 * MM, height=50 * MM)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(20, 20, 80, 50))
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=1)
    shape.commit()
    _save(doc, outlined)
    extras = assess_extras(outlined, "", _order(90, 50))
    points = assess(outlined, 90, 50)
    problems = []
    if _by_id(extras)["extra_E6"]["status"] != "pass":
        problems.append("E6 " + _by_id(extras)["extra_E6"]["detail"][:80])
    if _point(points["checks"], "4").get("status") == "failed":
        problems.append("point 4 failed on outlined type")
    _record("input-outlined-text", not problems, "; ".join(problems) or "no font")

    embedded = os.path.join(folder, "embedded.pdf")
    _save(_page(90, 50, "LIVE"), embedded)
    extras = assess_extras(embedded, "", _order(90, 50))
    points = assess(embedded, 90, 50)
    problems = []
    if _by_id(extras)["extra_E6"]["status"] != "pass":
        problems.append("E6 " + _by_id(extras)["extra_E6"]["detail"][:80])
    if _point(points["checks"], "4").get("status") != "passed":
        problems.append("point 4 " + str(_point(points["checks"], "4").get("status")))
    _record("input-embedded-font", not problems, "; ".join(problems) or "embedded")

    bare = os.path.join(folder, "helvetica.pdf")
    _save(_page(90, 50, "HELV", rgb=(1, 1, 1), embed=False), bare)
    extras = assess_extras(bare, "", _order(90, 50))
    points = assess(bare, 90, 50)
    problems = []
    if _by_id(extras)["extra_E6"]["status"] != "failed":
        problems.append("E6 " + str(_by_id(extras)["extra_E6"]["status"]))
    if _point(points["checks"], "4").get("status") != "failed":
        problems.append("point 4 " + str(_point(points["checks"], "4").get("status")))
    result = _job(bare, os.path.join(folder, "helv"), 90, 50, "card-90x50", "Business card 90 × 50")
    blob = " ".join(result.get("reasons") or []).lower()
    if "embed" not in blob and "font" not in blob:
        problems.append("job did not name the font")
    if "traceback" in blob:
        problems.append("traceback")
    _record("input-font-not-embedded", not problems, "; ".join(problems) or "font red")


def test_multipage(folder: str) -> None:
    from extra_checks import assess_extras, check_e2
    import pymupdf as fitz

    def booklet(path: str, pages: int) -> None:
        doc = fitz.open()
        for index in range(pages):
            page = doc.new_page(width=148 * MM, height=210 * MM)
            page.insert_font(fontname="DEJAVU", fontfile=FONT)
            page.insert_text((40, 80), f"P{index + 1}", fontsize=16, fontname="DEJAVU")
        doc.save(path)
        doc.close()

    good = os.path.join(folder, "book8.pdf")
    booklet(good, 8)
    extras = assess_extras(good, "", _order(148, 210, sides="booklet"))
    bad = os.path.join(folder, "book6.pdf")
    booklet(bad, 6)
    extras_bad = assess_extras(bad, "", _order(148, 210, sides="booklet"))
    problems = []
    if _by_id(extras)["extra_E2"]["status"] != "pass":
        problems.append("8 " + _by_id(extras)["extra_E2"]["detail"][:80])
    if _by_id(extras_bad)["extra_E2"]["status"] != "failed":
        problems.append("6 " + str(_by_id(extras_bad)["extra_E2"]["status"]))
    _record("pages-booklet", not problems, "; ".join(problems) or "8 pass, 6 failed")

    front = os.path.join(folder, "front.pdf")
    back = os.path.join(folder, "back.pdf")
    _save(_page(90, 50, "FRONT"), front)
    _save(_page(90, 50, "BACK"), back)
    front_job = _job(front, os.path.join(folder, "frontjob"), 90, 50, "card-90x50", "Business card 90 × 50")
    back_job = _job(back, os.path.join(folder, "backjob"), 90, 50, "card-90x50", "Business card 90 × 50")
    problems = []
    for name, result, marker in (("front", front_job, "FRONT"), ("back", back_job, "BACK")):
        press = result.get("pressPath") or ""
        if not press or not os.path.exists(press):
            problems.append(f"{name} no press")
            continue
        text = _text(press)
        if marker not in text:
            problems.append(f"{name} missing {marker}")
        other = "BACK" if marker == "FRONT" else "FRONT"
        if other in text:
            problems.append(f"{name} contains {other}")
    merged = os.path.join(folder, "both.pdf")
    doc = fitz.open()
    for path in (front, back):
        src = fitz.open(path)
        doc.insert_pdf(src)
        src.close()
    doc.save(merged)
    doc.close()
    both = assess_extras(merged, "", _order(90, 50, sides="double"))
    if _by_id(both)["extra_E2"]["status"] != "pass":
        problems.append("pair " + _by_id(both)["extra_E2"]["detail"][:80])
    _record("pages-front-back-files", not problems, "; ".join(problems) or "separate")

    turned = os.path.join(folder, "foot.pdf")
    doc = fitz.open()
    for word in ("FRONT", "BACK"):
        page = doc.new_page(width=90 * MM, height=50 * MM)
        page.insert_font(fontname="DEJAVU", fontfile=FONT)
        page.insert_text((30, 40), word, fontsize=14, fontname="DEJAVU")
    doc.save(turned)
    doc.close()
    row = check_e2(turned, _order(90, 50, sides="double", headToFoot=True), apply=True)
    doc = fitz.open(turned)
    rotation = doc[1].rotation
    text = doc[1].get_text("text") or ""
    doc.close()
    problems = []
    if row.get("status") != "fixed" or "head-to-foot" not in row.get("detail", "").lower():
        problems.append(row.get("detail", "")[:80])
    if rotation != 180:
        problems.append(f"rotation {rotation}")
    if "BACK" not in text:
        problems.append("back text lost")
    _record("pages-head-to-foot", not problems, "; ".join(problems) or "turned")

    single = os.path.join(folder, "one.pdf")
    _save(_page(90, 50, "ONLY"), single)
    extras = assess_extras(single, "", _order(90, 50, sides="double"))
    detail = _by_id(extras)["extra_E2"]["detail"]
    ok = _by_id(extras)["extra_E2"]["status"] == "warning" and "Back blank" in detail
    _record("pages-single-for-double", ok, detail[:100])


def test_perfect(folder: str) -> None:
    import pymupdf as fitz

    trim_w, trim_h, bleed = 148.0, 210.0, 5.0
    page_w = (trim_w + 2 * bleed) * MM
    page_h = (trim_h + 2 * bleed) * MM
    path = os.path.join(folder, "perfect.pdf")
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.insert_font(fontname="deja", fontfile=FONT)
    page.insert_text((page_w * 0.35, page_h * 0.5), "READY", fontsize=14, fontname="deja", color=(0, 0, 0))
    raw = page.read_contents().replace(b"0 0 0 rg", b"0 0 0 1 k").replace(b"0 0 0 RG", b"0 0 0 1 K")
    fill = b"q 0.2 0 0 0 k 0 0 %.2f %.2f re f Q\n" % (page_w, page_h)
    page.clean_contents()
    doc.update_stream(page.get_contents()[0], fill + raw)
    inset = bleed * MM
    page.set_mediabox(page.rect)
    page.set_cropbox(page.rect)
    page.set_bleedbox(page.rect)
    page.set_trimbox(fitz.Rect(inset, inset, page_w - inset, page_h - inset))
    doc.save(path)
    doc.close()
    result = _job(path, os.path.join(folder, "perfect"), trim_w, trim_h, "a5", "A5")
    problems = []
    if result.get("light") != "green":
        problems.append(f"light {result.get('light')} {result.get('reasons')}")
    if result.get("reasons"):
        problems.append("reasons " + " ".join(result.get("reasons") or [])[:120])
    press = result.get("pressPath") or ""
    if not press or not os.path.exists(press):
        problems.append("no press file")
    else:
        if "READY" not in _text(press):
            problems.append("text changed")
        got_w, got_h = _press_mm(press)
        if abs(got_w - (trim_w + 10)) > 1.5 or abs(got_h - (trim_h + 10)) > 1.5:
            problems.append(f"press {got_w:.1f}x{got_h:.1f}")

        def trim_rgb(file_path: str):
            opened = fitz.open(file_path)
            try:
                clip = opened[0].trimbox
                pix = opened[0].get_pixmap(matrix=fitz.Matrix(1, 1), clip=clip, alpha=False, colorspace=fitz.csRGB)
                return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
            finally:
                opened.close()

        before = trim_rgb(path)
        after = trim_rgb(press)
        height = min(before.shape[0], after.shape[0])
        width = min(before.shape[1], after.shape[1])
        delta = np.abs(before[:height, :width].astype(int) - after[:height, :width].astype(int))
        if float(delta.mean()) > 0.5 or int(delta.max()) > 12:
            problems.append(f"pixel diff mean {float(delta.mean()):.3f} max {int(delta.max())}")
    _record("perfect-cmyk", not problems, "; ".join(problems) or "green, unchanged")


def test_heavy(folder: str) -> None:
    import pymupdf as fitz
    from PIL import Image
    from extra_checks import assess_extras
    # A1 page with a real 300 dpi photo placed at 100 mm, not a full-sheet bitmap.
    photo = os.path.join(folder, "photo.jpg")
    side = int(round(100 / 25.4 * 300))
    Image.new("RGB", (side, side), (20, 80, 40)).save(photo, quality=85, dpi=(300, 300))
    poster = os.path.join(folder, "a1.pdf")
    doc = fitz.open()
    page = doc.new_page(width=594 * MM, height=841 * MM)
    origin = 40 * MM
    page.insert_image(fitz.Rect(origin, origin, origin + 100 * MM, origin + 100 * MM), filename=photo)
    page.set_trimbox(page.rect)
    doc.save(poster)
    doc.close()
    started = time.perf_counter()
    result = _job(poster, os.path.join(folder, "a1"), 594, 841, "a1", "A1", detect=True)
    elapsed = time.perf_counter() - started
    extras = assess_extras(poster, "", _order(594, 841))
    problems = []
    if result.get("productId") != "a1":
        problems.append(f"product {result.get('productId')}")
    blob = " ".join(result.get("reasons") or []) + " " + " ".join(result.get("decisions") or [])
    press = result.get("pressPath") or ""
    if not press or not os.path.exists(press):
        problems.append("no press file " + blob[:100])
    else:
        got_w, got_h = _press_mm(press)
        if abs(got_w - 604) > 2 or abs(got_h - 851) > 2:
            problems.append(f"press {got_w:.1f}x{got_h:.1f}")
    if result.get("light") == "red":
        problems.append(f"red {blob[:100]}")
    if elapsed > 45:
        problems.append(f"time {elapsed:.1f}s")
    if _by_id(extras)["extra_E1"]["status"] != "pass":
        problems.append("E1 " + _by_id(extras)["extra_E1"]["detail"][:80])
    if "traceback" in blob.lower():
        problems.append("traceback")
    _record("heavy-a1-poster", not problems, "; ".join(problems) or f"press {elapsed:.1f}s")

    bulky = os.path.join(folder, "bulky.pdf")
    _save(_page(148, 210, "BULK"), bulky)
    import pikepdf

    pdf = pikepdf.open(bulky, allow_overwriting_input=True)
    # Zeros collapse under Flate to about 1 MB. A repeated 1 MiB random block
    # stays ~200 MB: the Flate window is 32 KiB, and the stream is stored raw
    # so the file on disk is the size a customer actually uploaded.
    block = os.urandom(1024 * 1024)
    pdf.Root["/FAIBulk"] = pikepdf.Stream(pdf, block * 200)
    pdf.save(bulky, compress_streams=False)
    pdf.close()
    size_mb = os.path.getsize(bulky) / (1024 * 1024)
    started = time.perf_counter()
    result = _job(bulky, os.path.join(folder, "bulk"), 148, 210, "a5", "A5")
    elapsed = time.perf_counter() - started
    problems = []
    if size_mb < 190:
        problems.append(f"file {size_mb:.0f} MB")
    if elapsed > 90:
        problems.append(f"time {elapsed:.1f}s")
    press = result.get("pressPath") or ""
    if not press or not os.path.exists(press):
        problems.append("no press " + " ".join(result.get("reasons") or [])[:80])
    elif "BULK" not in _text(press):
        problems.append("marker lost")
    if "traceback" in " ".join(result.get("reasons") or []).lower():
        problems.append("traceback")
    _record("heavy-200mb", not problems, "; ".join(problems) or f"{size_mb:.0f} MB in {elapsed:.1f}s")


def test_hard(folder: str) -> None:
    import pymupdf as fitz
    import pikepdf
    from extra_checks import assess_extras
    from twenty_five import assess
    from customer_files_check import _qr_image
    import cv2

    def add_spot(path: str, name: str) -> None:
        pdf = pikepdf.open(path, allow_overwriting_input=True)
        page = pdf.pages[0]
        alt = pikepdf.Array([
            pikepdf.Name("/Separation"),
            pikepdf.Name(f"/{name}"),
            pikepdf.Name("/DeviceCMYK"),
            pikepdf.Array([0, 1, 0, 0]),
        ])
        resources = page.get("/Resources") or pikepdf.Dictionary()
        resources["/ColorSpace"] = pikepdf.Dictionary({"/CS1": alt})
        page["/Resources"] = resources
        contents = page.get("/Contents")
        existing = contents.read_bytes() if contents is not None and not isinstance(contents, pikepdf.Array) else b""
        page["/Contents"] = pdf.make_stream(existing + b"\n/CS1 cs 1 scn 10 10 40 40 re S\n")
        pdf.save(path)
        pdf.close()

    pantone = os.path.join(folder, "pantone.pdf")
    _save(_page(90, 50, "SPOT"), pantone)
    add_spot(pantone, "Pantone 185 C")
    extras = assess_extras(pantone, "", _order(90, 50))
    detail = _by_id(extras)["extra_E7"]["detail"]
    ok = _by_id(extras)["extra_E7"]["status"] == "warning" and "Pantone" in detail
    _record("hard-pantone", ok, detail[:100])

    cut = os.path.join(folder, "cut.pdf")
    _save(_page(90, 50, "DIE"), cut)
    add_spot(cut, "CutContour")
    bare = assess_extras(cut, "", _order(90, 50, finishes=[]))
    matched = assess_extras(cut, "", _order(90, 50, finishes=["cut"]))
    problems = []
    if _by_id(bare)["extra_E7"]["status"] != "failed":
        problems.append("no finish " + str(_by_id(bare)["extra_E7"]["status"]))
    if _by_id(matched)["extra_E7"]["status"] == "failed":
        problems.append("finish still failed")
    _record("hard-dieline", not problems, "; ".join(problems) or "cut line named")

    shadow = os.path.join(folder, "shadow.pdf")
    _save(_page(90, 50, "SOFT"), shadow)
    pdf = pikepdf.open(shadow, allow_overwriting_input=True)
    page = pdf.pages[0]
    resources = page.get("/Resources") or pikepdf.Dictionary()
    resources["/ExtGState"] = pikepdf.Dictionary({
        "/DS": pikepdf.Dictionary({
            "/Type": pikepdf.Name("/ExtGState"),
            "/ca": 0.4,
            "/CA": 0.4,
            "/SMask": pikepdf.Name("/None"),
            "/BM": pikepdf.Name("/Multiply"),
        }),
    })
    page["/Resources"] = resources
    pdf.save(shadow)
    pdf.close()
    points = assess(shadow, 90, 50)
    row = _point(points["checks"], "6")
    ok = row.get("status") == "warning" and "transparency" in str(row.get("detail") or "").lower()
    _record("hard-transparency", ok, str(row.get("detail") or "")[:100])

    white = os.path.join(folder, "white-op.pdf")
    _save(_page(90, 50, "INK"), white)
    pdf = pikepdf.open(white, allow_overwriting_input=True)
    page = pdf.pages[0]
    resources = page.get("/Resources") or pikepdf.Dictionary()
    resources["/ExtGState"] = pikepdf.Dictionary({
        "/W": pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/OP": True, "/op": True}),
    })
    page["/Resources"] = resources
    contents = page.get("/Contents")
    existing = contents.read_bytes() if contents is not None and not isinstance(contents, pikepdf.Array) else b""
    page["/Contents"] = pdf.make_stream(existing + b"\n0 0 0 0 k /W gs 12 12 30 20 re f\n")
    pdf.save(white)
    pdf.close()
    extras = assess_extras(white, "", _order(90, 50))
    detail = _by_id(extras)["extra_E8"]["detail"]
    ok = _by_id(extras)["extra_E8"]["status"] == "failed" and "White" in detail
    _record("hard-overprint-white", ok, detail[:100])

    qr_path = os.path.join(folder, "qr.png")
    cv2.imwrite(qr_path, _qr_image("https://flyerz.co.za/pay"))
    qr_pdf = os.path.join(folder, "qr.pdf")
    doc = fitz.open()
    page = doc.new_page(width=148 * MM, height=210 * MM)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    # Inside the trim, within 5 mm of the cut, so the code is near the edge and still readable.
    side = 22 * MM
    margin = 2 * MM
    page.insert_image(fitz.Rect(margin, 210 * MM - margin - side, margin + side, 210 * MM - margin), filename=qr_path)
    doc.save(qr_pdf)
    doc.close()
    points = assess(qr_pdf, 148, 210)
    row = _point(points["checks"], "17")
    detail = str(row.get("detail") or "")
    ok = row.get("status") == "passed" and "flyerz.co.za/pay" in detail
    _record("hard-qr-near-edge", ok, detail[:100])


def test_concurrency(folder: str) -> None:
    spec_a = {
        "src": os.path.join(folder, "client-a.pdf"),
        "out": os.path.join(folder, "out-a"),
        "marker": "CLIENTA",
        "width": 90,
        "height": 50,
        "product": "card-90x50",
        "label": "Business card 90 × 50",
    }
    spec_b = {
        "src": os.path.join(folder, "client-b.pdf"),
        "out": os.path.join(folder, "out-b"),
        "marker": "CLIENTB",
        "width": 148,
        "height": 210,
        "product": "a5",
        "label": "A5",
    }
    _save(_page(spec_a["width"], spec_a["height"], spec_a["marker"]), spec_a["src"])
    _save(_page(spec_b["width"], spec_b["height"], spec_b["marker"]), spec_b["src"])
    script = os.path.abspath(__file__)
    procs = []
    for spec in (spec_a, spec_b):
        procs.append(subprocess.Popen(
            [sys.executable, script, "--job", json.dumps(spec)],
            cwd=os.path.dirname(script),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ))
    problems = []
    payloads = []
    try:
        for proc, spec in zip(procs, (spec_a, spec_b)):
            stdout, stderr = proc.communicate(timeout=120)
            if proc.returncode != 0:
                problems.append(f"{spec['marker']} exit {proc.returncode} {(stderr or '')[-180:]}")
                continue
            try:
                payloads.append(json.loads(stdout.strip().splitlines()[-1]))
            except Exception:
                problems.append(f"{spec['marker']} bad output {(stdout or '')[-120:]}")
    except subprocess.TimeoutExpired:
        for proc in procs:
            proc.kill()
        problems.append("timeout")
    if len(payloads) == 2:
        by_marker = {row.get("marker"): row for row in payloads}
        for spec in (spec_a, spec_b):
            row = by_marker.get(spec["marker"]) or {}
            if row.get("marker") != spec["marker"] or spec["marker"] not in str(row.get("text") or ""):
                problems.append(f"mix {spec['marker']} {row.get('text')}")
            other = "CLIENTB" if spec["marker"] == "CLIENTA" else "CLIENTA"
            if other in str(row.get("text") or ""):
                problems.append(f"{spec['marker']} saw {other}")
            if row.get("product") != spec["product"]:
                problems.append(f"product {row.get('product')}")
            width = float(row.get("width") or 0)
            height = float(row.get("height") or 0)
            if abs(width - (spec["width"] + 10)) > 1.5 or abs(height - (spec["height"] + 10)) > 1.5:
                problems.append(f"size {width:.1f}x{height:.1f}")
    _record("concurrency-two-clients", not problems, "; ".join(problems) or "both matched")


def _job_entry(raw: str) -> None:
    spec = json.loads(raw)
    result = _job(spec["src"], spec["out"], spec["width"], spec["height"], spec["product"], spec["label"])
    press = result.get("pressPath") or ""
    text = _text(press) if press and os.path.exists(press) else ""
    width, height = _press_mm(press) if press and os.path.exists(press) else (0, 0)
    print(json.dumps({
        "marker": spec["marker"],
        "text": text,
        "product": result.get("productId"),
        "light": result.get("light"),
        "width": width,
        "height": height,
    }))


def _write_table() -> None:
    out = "/opt/cursor/artifacts/customer_intake.txt"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = ["case\tresult\tdetail"]
    for row in ROWS:
        lines.append(f"{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run() -> list[str]:
    folder = tempfile.mkdtemp(prefix="customer-intake-")
    test_inputs(folder)
    test_multipage(folder)
    test_perfect(folder)
    test_heavy(folder)
    test_hard(folder)
    test_concurrency(folder)
    _write_table()
    return list(FAILURES)


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--job":
        _job_entry(sys.argv[2])
        return
    failed = run()
    print(f"customer intake {len(ROWS) - len(failed)} pass, {len(failed)} fail")
    if failed:
        raise SystemExit(f"{len(failed)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
