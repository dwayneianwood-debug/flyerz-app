#!/usr/bin/env python3
"""DPI, print-ready tick, and business-card size rules for the first quick check."""
import os
import re
import sys
import tempfile

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
from quick_check import run_quick_check

failures = []


def fail(message):
    failures.append(message)
    print("FAIL", message)


def check_by_id(report, check_id):
    return next(item for item in report["checks"] if item["id"] == check_id)


def save_png(path, width, height, dpi):
    Image.new("RGB", (width, height), (220, 20, 60)).save(path, dpi=(dpi, dpi))


def main():
    tmp = tempfile.mkdtemp(prefix="qc-rules-")
    square = os.path.join(tmp, "square.png")
    save_png(square, 1024, 1024, 72)

    sized = run_quick_check(square, "png", 90, 50)
    if "error" in sized:
        fail(f"sized check errored: {sized['error']}")
        return
    resolution = check_by_id(sized, "resolution")
    blob = f"{resolution.get('message', '')} {resolution.get('details', '')}"
    expected = min(1024 / (90 / 25.4), 1024 / (50 / 25.4))
    match = re.search(r"(\d+) DPI", resolution["message"])
    if not match or abs(int(match.group(1)) - round(expected)) > 1:
        fail(f"expected about {expected:.0f} DPI at 90x50, got {resolution['message']}")
    if "A4" in blob:
        fail(f"chosen-size check still mentions A4: {blob}")
    if "90 x 50 mm" not in blob:
        fail(f"chosen-size check does not name 90 x 50 mm: {blob}")

    blockers = [check_by_id(sized, key) for key in ("bleed", "cmyk", "resolution")]
    print_ready = check_by_id(sized, "print_ready")
    if any(not item["passed"] for item in blockers):
        if print_ready["passed"]:
            fail("print-ready tick is green while bleed, colour, or resolution failed")
        if "appears print-ready" in print_ready["message"]:
            fail(f"green print-ready sentence still shown: {print_ready['message']}")
        if print_ready.get("severity") != "HIGH":
            fail(f"print-ready severity should be HIGH, got {print_ready.get('severity')}")
    else:
        fail("expected the small RGB image to fail at least one of bleed, colour, or resolution")

    unsized = run_quick_check(square, "png")
    unsized_resolution = check_by_id(unsized, "resolution")
    unsized_blob = f"{unsized_resolution.get('message', '')} {unsized_resolution.get('details', '')}"
    if "A4" in unsized_blob:
        fail(f"no-size check mentions A4: {unsized_blob}")
    if "No print size was supplied" not in unsized_blob:
        fail(f"no-size check should say no print size was supplied: {unsized_blob}")
    if unsized_resolution["passed"]:
        fail("a 72 DPI tag with no print size should fail the resolution check")

    tagged = os.path.join(tmp, "tagged.png")
    save_png(tagged, 400, 400, 300)
    tagged_report = run_quick_check(tagged, "png")
    tagged_resolution = check_by_id(tagged_report, "resolution")
    if not tagged_resolution["passed"]:
        fail(f"a real 300 DPI tag with no print size should pass: {tagged_resolution}")
    tagged_ready = check_by_id(tagged_report, "print_ready")
    tagged_blockers = [check_by_id(tagged_report, key) for key in ("bleed", "cmyk")]
    if any(not item["passed"] for item in tagged_blockers) and tagged_ready["passed"]:
        fail("print-ready stayed green after colour or bleed failed")

    card_w = int(round(90 / 25.4 * 300))
    card_h = int(round(50 / 25.4 * 300))
    card = os.path.join(tmp, "card.png")
    save_png(card, card_w, card_h, 300)
    card_report = run_quick_check(card, "png", 90, 50)
    card_ready = check_by_id(card_report, "print_ready")
    if "Business Card (90x50mm)" not in (card_ready.get("details") or ""):
        fail(f"business card size should be 90x50: {card_ready.get('details')}")

    source = open(os.path.join(os.path.dirname(__file__), "quick_check.py"), encoding="utf-8").read()
    if "90, 55" in source:
        fail("quick check still lists a 90x55 business card")

    vector_path = os.path.join(tmp, "vector.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((10, 50), "Vector only")
    doc.save(vector_path)
    doc.close()
    vector = run_quick_check(vector_path, "pdf", 90, 50)
    vector_resolution = check_by_id(vector, "resolution")
    if vector_resolution["message"] != "Vector artwork, resolution independent":
        fail(f"vector DPI path changed: {vector_resolution['message']}")

    if failures:
        print(f"{len(failures)} quick-check rule(s) failed")
        sys.exit(1)
    print("all quick-check rules passed")


if __name__ == "__main__":
    main()
