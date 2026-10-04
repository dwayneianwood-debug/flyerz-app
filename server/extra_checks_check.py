#!/usr/bin/env python3
"""Extra checks E1–E9. The 25-point list is not replaced."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extra_checks import (  # noqa: E402
    assess_extras,
    check_e1,
    check_e3,
    light_from_extra,
    normalize_order,
    should_fit_inside,
    worse_light,
)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MM = 72.0 / 25.4
FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok  " if ok else "FAIL") + " " + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _k_only(path: str, width_mm: float = 148, height_mm: float = 210) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=width_mm * MM, height=height_mm * MM)
    page.insert_font(fontname="deja", fontfile=FONT)
    page.insert_text((40, 80), "KONLY", fontsize=16, fontfile=FONT, fontname="deja", color=(0, 0, 0))
    raw = page.read_contents().replace(b"0 0 0 RG", b"0 0 0 1 k").replace(b"0 0 0 rg", b"0 0 0 1 k")
    doc.update_stream(page.get_contents()[0], raw)
    page.set_mediabox(page.rect)
    bleed = 5 * MM
    page.set_mediabox(fitz.Rect(-bleed, -bleed, page.rect.width + bleed, page.rect.height + bleed))
    # Keep a normal origin so the press-box check can use a rebuilt file instead.
    doc.save(path)
    doc.close()


def _press_cmyk(path: str) -> None:
    import pymupdf as fitz

    width = (148 + 10) * MM
    height = (210 + 10) * MM
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_font(fontname="deja", fontfile=FONT)
    page.insert_text((40, 80), "PRESS", fontsize=20, fontfile=FONT, fontname="deja", color=(0, 0, 0))
    raw = page.read_contents().replace(b"0 0 0 RG", b"0 0 0 1 k").replace(b"0 0 0 rg", b"0 0 0 1 k")
    doc.update_stream(page.get_contents()[0], raw)
    trim = fitz.Rect(5 * MM, 5 * MM, width - 5 * MM, height - 5 * MM)
    page.set_trimbox(trim)
    page.set_bleedbox(page.rect)
    page.set_cropbox(page.rect)
    doc.save(path)
    doc.close()


def _rgb_page(path: str) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(page.rect, color=(1, 0, 0), fill=(1, 0, 0))
    doc.save(path)
    doc.close()


def _sized(path: str, width_mm: float, height_mm: float, text: str = "PAGE", size: float = 18) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=width_mm * MM, height=height_mm * MM)
    page.insert_text((30, 60), text, fontsize=size, fontfile=FONT, fontname="deja")
    page.set_trimbox(page.rect)
    doc.save(path)
    doc.close()


def test_order_and_e1() -> None:
    order = normalize_order({})
    check("no-invented-size", order["widthMm"] is None and order["explicitSize"] is False)
    row = check_e1("/tmp/does-not-matter.pdf", order)
    check("e1-missing-product", row["id"] == "extra_E1" and row["status"] == "failed" and "148" not in row["detail"] and "invented" in row["detail"], row["detail"])
    folder = tempfile.mkdtemp(prefix="e1-")
    src = os.path.join(folder, "land.pdf")
    _sized(src, 210, 148, "TURN")
    turned = check_e1(src, {"widthMm": 148, "heightMm": 210, "explicitSize": True}, apply=True)
    check("e1-rotate", turned["status"] == "fixed", turned["detail"])
    wide = os.path.join(folder, "wide.pdf")
    _sized(wide, 297, 100, "WIDE")
    bad = check_e1(wide, {"widthMm": 148, "heightMm": 210, "explicitSize": True})
    check("e1-aspect-red", bad["status"] == "failed", bad["detail"])


def test_e2_e5() -> None:
    folder = tempfile.mkdtemp(prefix="e2-")
    booklet = os.path.join(folder, "book.pdf")
    import pymupdf as fitz

    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=148 * MM, height=210 * MM)
        page.insert_text((30, 60), "BODY", fontsize=16, fontfile=FONT)
    doc.save(booklet)
    doc.close()
    report = assess_extras(booklet, "", {"explicitSize": True, "widthMm": 148, "heightMm": 210, "sides": "booklet"})
    by_id = {row["id"]: row for row in report["checks"]}
    check("ids", [f"extra_E{n}" for n in range(1, 10)] == [row["id"] for row in report["checks"]], str([row["id"] for row in report["checks"]]))
    check("e2-booklet", by_id["extra_E2"]["status"] == "failed", by_id["extra_E2"]["detail"])
    tiny = os.path.join(folder, "tiny.pdf")
    _sized(tiny, 148, 210, "FINE", size=4)
    e5 = assess_extras(tiny, "", {"explicitSize": True, "widthMm": 148, "heightMm": 210})
    row = next(item for item in e5["checks"] if item["id"] == "extra_E5")
    check("e5-under-5", row["status"] == "failed", row["detail"])


def test_e3_and_nuclear() -> None:
    folder = tempfile.mkdtemp(prefix="e3-")
    rgb = os.path.join(folder, "rgb.pdf")
    cmyk = os.path.join(folder, "cmyk.pdf")
    _rgb_page(rgb)
    _press_cmyk(cmyk)
    order = {"explicitSize": True, "widthMm": 148, "heightMm": 210}
    red = check_e3(rgb, order, source_pages=1)
    check("e3-rgb-red", red["status"] == "failed" and "RGB" in red["detail"], red["detail"])
    held = check_e3(cmyk, order, source_pages=1)
    check("e3-k-held", held["status"] == "pass", held["detail"])
    src = os.path.join(folder, "src.pdf")
    out = os.path.join(folder, "nuclear.pdf")
    _sized(src, 148, 210, "KONLY", size=16)
    import pymupdf as fitz

    doc = fitz.open(src)
    page = doc[0]
    raw = page.read_contents().replace(b"0 0 0 RG", b"0 0 0 1 k").replace(b"0 0 0 rg", b"0 0 0 1 k")
    doc.update_stream(page.get_contents()[0], raw)
    doc.save(src, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    from compile_press_pdf import nuclear_rebuild_pdf_visual_mount

    nuclear_rebuild_pdf_visual_mount(src, out, 148, 210, 5)
    import numpy as np
    import pikepdf

    pdf = pikepdf.open(out)
    image = next(iter(pdf.pages[0].Resources.XObject.values()))
    data = image.read_bytes()
    arr = np.frombuffer(data, dtype=np.uint8).reshape(int(image.Height), int(image.Width), 4)
    ink = arr[:, :, 3] > 180
    mean = arr[ink].mean(axis=0) if int(ink.sum()) else [99, 99, 99, 0]
    pdf.close()
    check("nuclear-cmyk-k-only", int(ink.sum()) > 20 and mean[0] < 8 and mean[1] < 8 and mean[2] < 8, str(mean))
    gate = check_e3(out, order, source_pages=1)
    check("nuclear-gate", gate["status"] != "failed" or "RGB" not in gate["detail"], gate["detail"])


def test_e6_e7_e8() -> None:
    folder = tempfile.mkdtemp(prefix="e678-")
    bare = os.path.join(folder, "helv.pdf")
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "Helvetica", fontsize=18, fontname="helv")
    doc.save(bare)
    doc.close()
    report = assess_extras(bare, "", {"explicitSize": False})
    e6 = next(row for row in report["checks"] if row["id"] == "extra_E6")
    check("e6-no-local", e6["status"] == "failed", e6["detail"])

    spot = os.path.join(folder, "cut.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "ART", fontsize=18, fontfile=FONT)
    doc.save(spot)
    doc.close()
    import pikepdf

    pdf = pikepdf.open(spot, allow_overwriting_input=True)
    page = pdf.pages[0]
    alt = pikepdf.Array([pikepdf.Name("/Separation"), pikepdf.Name("/CutContour"), pikepdf.Name("/DeviceCMYK"), pikepdf.Array([0, 1, 0, 0])])
    resources = page.get("/Resources") or pikepdf.Dictionary()
    resources["/ColorSpace"] = pikepdf.Dictionary({"/CS1": alt})
    page["/Resources"] = resources
    page["/Contents"] = pdf.make_stream(b"/CS1 cs 1 scn 10 10 40 40 re S\n")
    pdf.save(spot)
    pdf.close()
    e7 = assess_extras(spot, "", {"explicitSize": True, "widthMm": 148, "heightMm": 210, "finishes": []})
    row = next(item for item in e7["checks"] if item["id"] == "extra_E7")
    check("e7-technical-red", row["status"] == "failed" and "CutContour" in row["detail"], row["detail"])

    white = os.path.join(folder, "white.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 80), "OK", fontsize=18, fontfile=FONT)
    doc.save(white)
    doc.close()
    pdf = pikepdf.open(white, allow_overwriting_input=True)
    page = pdf.pages[0]
    resources = page.get("/Resources") or pikepdf.Dictionary()
    resources["/ExtGState"] = pikepdf.Dictionary({
        "/W": pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/OP": True, "/op": True}),
    })
    page["/Resources"] = resources
    contents = page.get("/Contents")
    if isinstance(contents, pikepdf.Array):
        existing = b"\n".join(item.read_bytes() for item in contents)
    else:
        existing = contents.read_bytes() if contents is not None else b""
    page["/Contents"] = pdf.make_stream(existing + b"\n0 0 0 0 k /W gs 10 10 30 30 re f\n")
    pdf.save(white)
    pdf.close()
    fixed = assess_extras(white, "", {"explicitSize": False}, apply=True)
    e8 = next(item for item in fixed["checks"] if item["id"] == "extra_E8")
    check("e8-white-off", e8["status"] == "fixed", e8["detail"])


def test_e9_and_light() -> None:
    import pymupdf as fitz

    folder = tempfile.mkdtemp(prefix="e9-")
    path = os.path.join(folder, "cut.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((10, 30), "EDGE", fontsize=18, fontfile=FONT)
    doc.save(path)
    doc.close()
    close = os.path.join(folder, "close.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=180)
    page.insert_text((2, 20), "EDGE", fontsize=14, fontfile=FONT)
    doc.save(close)
    doc.close()
    doc = fitz.open(close)
    page = doc[0]
    fit = should_fit_inside(page, page.rect, fitz.Rect(0, 0, 200, 200))
    doc.close()
    check("e9-fit-when-text-cut", fit is True)
    # A very different shape stays on cover and is a red content cut.
    report = assess_extras(path, "", {"explicitSize": True, "widthMm": 50, "heightMm": 210})
    e9 = next(row for row in report["checks"] if row["id"] == "extra_E9")
    check("e9-text-cut-red", e9["status"] == "failed", e9["detail"])
    base = {"light": "green", "reasons": [], "clientMessage": ""}
    extra = light_from_extra([{"status": "failed", "num": "E1", "name": "Size", "detail": "too wide"}])
    mixed = worse_light(base, extra)
    check("worse-red", mixed["light"] == "red" and "E1" in mixed["clientMessage"], mixed["clientMessage"])
    still = worse_light({"light": "red", "reasons": ["25"], "clientMessage": "25 failed"}, {"light": "green", "reasons": [], "clientMessage": ""})
    check("twenty-five-stays-red", still["light"] == "red" and "25 failed" in still["clientMessage"])
    shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    test_order_and_e1()
    test_e2_e5()
    test_e3_and_nuclear()
    test_e6_e7_e8()
    test_e9_and_light()
    if FAILURES:
        print("extra checks failed: " + ", ".join(FAILURES))
        sys.exit(1)
    print("extra checks passed")


if __name__ == "__main__":
    main()
