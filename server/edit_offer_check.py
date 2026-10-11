#!/usr/bin/env python3
"""An edit is offered only when the font, size, weight, and fill will pass.

Covers the laptop church poster and Medella phone measurements, plus a wider
set: other faces, flat, gradient, and photo grounds, vector and raster type,
dates, phones, times, and venues.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="edit-offer-")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
sys.path.insert(0, os.path.dirname(__file__))

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
MONTSERRAT = os.path.join(FONT_DIR, "v2", "Montserrat-700.ttf")
POPPINS = os.path.join(FONT_DIR, "Poppins-Bold.ttf")
CRIMSON = os.path.join(FONT_DIR, "v2", "CrimsonText-Regular.ttf")
LATO = os.path.join(FONT_DIR, "Lato-Regular.ttf")
CHURCH = "/tmp/ev/church_embedded.jpeg"
WORDS = "/tmp/ev/ocrwords_churchA_2.json"

ROWS: list[dict] = []
FAILURES: list[str] = []


def _record(case: str, ok: bool, detail: str) -> None:
    ROWS.append({"case": case, "ok": ok, "detail": detail})
    print(("PASS" if ok else "FAIL") + f" {case} {detail}", flush=True)
    if not ok:
        FAILURES.append(case)


def _sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _picture(path: str, image) -> None:
    import pymupdf as fitz

    png = path + ".png"
    image.save(png)
    doc = fitz.open()
    page = doc.new_page(width=image.size[0] / 2.0, height=image.size[1] / 2.0)
    page.insert_image(page.rect, filename=png)
    doc.save(path)
    doc.close()


def _outside(before: str, after: str, box: list) -> int:
    import numpy as np
    from artwork_edits import _render_rgb

    left = _render_rgb(before, 2.5)
    right = _render_rgb(after, 2.5)
    if left.shape != right.shape or not box or len(box) < 4:
        return 999999
    delta = np.abs(left.astype(np.int16) - right.astype(np.int16)).max(axis=2)
    scale = 2.5
    margin = 1.25 * scale
    x0 = max(0, int(np.floor(float(box[0]) * scale - margin)))
    y0 = max(0, int(np.floor(float(box[1]) * scale - margin)))
    x1 = min(left.shape[1], int(np.ceil(float(box[2]) * scale + margin)))
    y1 = min(left.shape[0], int(np.ceil(float(box[3]) * scale + margin)))
    outside = delta.copy()
    outside[y0:y1, x0:x1] = 0
    return int((outside > 12).sum())


def _spans(path: str) -> list[dict]:
    import pymupdf as fitz

    found = []
    doc = fitz.open(path)
    try:
        for block in (doc[0].get_text("dict") or {}).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    found.append(span)
    finally:
        doc.close()
    return found


def _ask(src: str, message: str, key: str) -> dict:
    from artwork_edits import propose

    preview = os.path.join(ROOT, "preview-" + key)
    asked = propose(src, message, key, preview) or {}
    asked["_preview"] = preview
    asked["_srcsha"] = _sha(src)
    return asked


def _offered_clean(asked: dict, src: str, key: str) -> tuple[bool, str]:
    """Pending only when size, weight, fill, and E5 will pass. Otherwise the polite refusal."""
    from artwork_edits import SAFE_EDIT, _load
    from extra_checks import check_e5

    reply = (asked.get("reply") or "").strip()
    if _sha(src) != asked.get("_srcsha"):
        return False, "source bytes changed"
    if asked.get("pending") is not True:
        ok = reply == SAFE_EDIT or reply == "I can't read that clearly. Nothing was changed."
        return ok, reply[:180]
    prop = _load(key).get("proposal") or {}
    staged = os.path.join(asked.get("_preview") or "", "staged.pdf")
    if not os.path.isfile(staged):
        return False, "no staged pdf"
    fontfile = os.path.abspath(str(prop.get("fontfile") or ""))
    bundled = os.path.abspath(FONT_DIR)
    if not fontfile.startswith(bundled):
        return False, "font not bundled " + fontfile
    glyph = float(prop.get("glyphPt") or 0)
    digit = float(prop.get("digitPt") or 0)
    weight = float(prop.get("weightRatio") or 1)
    if glyph > 1 and digit > 0 and abs(digit - glyph) / glyph > 0.18:
        return False, f"size {digit:.2f} vs {glyph:.2f}"
    if weight > 1.35 or (0 < weight < 0.65):
        return False, f"weight {weight:.3f}"
    row = check_e5(staged)
    if row.get("status") == "failed":
        return False, "E5 failed " + str(row.get("detail"))
    box = prop.get("editBox") or prop.get("bbox")
    outside = _outside(src, staged, box) if box else 999999
    if outside != 0:
        return False, f"outside {outside}"
    sizes = [float(span.get("size") or 0) for span in _spans(staged)]
    if any(0 < size < 5 for size in sizes):
        return False, "span under 5 " + str(sizes)
    return True, f"{os.path.basename(fontfile)} glyph {glyph:.2f} digit {digit:.2f} w {weight:.3f} e5 {row.get('status')} outside {outside}"


def _laptop_words() -> None:
    from artwork_edits import SAFE_EDIT, _collect_hits, _select_hit, _parse, _unreadable_reply

    if not os.path.isfile(WORDS):
        _record("laptop-words", False, "missing ocr words")
        return
    words = json.load(open(WORDS, encoding="utf-8"))
    hits, merged = _collect_hits(words, "date")
    labels = sorted(hit["text"] for hit in hits)
    nine = _select_hit(words, _parse("change the date from 9 OCT to 12 October 2026"))
    eleven = _select_hit(words, _parse("change 11 OCT to 12 OCT"))
    missing = _select_hit(words, _parse("change 8 OCT to 12 OCT"))
    ok = (
        merged is True
        and labels == ["10 OCT", "11 OCT", "9 OCT"]
        and (nine or {}).get("text") == "9 OCT"
        and (eleven or {}).get("text") == "11 OCT"
        and (missing or {}).get("unreadable") is True
        and "OCT 6" not in labels
        and _unreadable_reply()["reply"] == "I can't read that clearly. Nothing was changed."
        and SAFE_EDIT == "I can't safely change this one, please send the editable file."
    )
    _record("laptop-words", ok, f"merged {merged} {labels} 9={(nine or {}).get('text')} 8={missing}")


def _gradient_date() -> None:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import confirm, undo

    font = ImageFont.truetype(MONTSERRAT, 90)
    month = ImageFont.truetype(MONTSERRAT, 64)
    arr = np.zeros((420, 640, 3), np.uint8)
    for y in range(arr.shape[0]):
        fade = y / (arr.shape[0] - 1)
        arr[y] = (int(211 - 40 * fade), int(83 + 30 * fade), int(124 + 20 * fade))
    plate = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(plate)
    draw.text((220, 70), "11", font=font, fill=(254, 254, 254))
    draw.text((200, 190), "OCT", font=month, fill=(254, 254, 254))
    src = os.path.join(ROOT, "gradient-date.pdf")
    _picture(src, plate)
    first = _ask(src, "change 11 OCT to 12 OCT", "gradient-date")
    second = _ask(src, "change 11 OCT to 12 OCT", "gradient-date-2")
    ok, detail = _offered_clean(first, src, "gradient-date")
    body = " ".join(span.get("text") or "" for span in _spans(os.path.join(first["_preview"], "staged.pdf")))
    same = (first.get("reply") == second.get("reply"))
    from artwork_edits import _load
    face = os.path.basename(str((_load("gradient-date").get("proposal") or {}).get("fontfile") or ""))
    face2 = os.path.basename(str((_load("gradient-date-2").get("proposal") or {}).get("fontfile") or ""))
    offered = ok and first.get("pending") is True and "12" in body and "OCT" in body and face.startswith("Montserrat-700") and face == face2 and same
    dest = os.path.join(ROOT, "gradient-out.pdf")
    import shutil
    shutil.copyfile(src, dest)
    original = _sha(dest)
    done = confirm("gradient-date", dest)
    back = undo("gradient-date", dest)
    offered = offered and done.get("ok") and back.get("ok") and _sha(dest) == original
    _record("gradient-date", offered, f"{face} {detail} undo {back.get('ok')} {body[:40]}")


def _photo_date() -> None:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import SAFE_EDIT

    font = ImageFont.truetype(POPPINS, 64)
    noise = np.random.default_rng(3).integers(40, 200, size=(360, 720, 3), dtype=np.uint8)
    plate = Image.fromarray(noise, "RGB")
    pen = ImageDraw.Draw(plate)
    pen.text((80, 40), "11", font=font, fill=(255, 255, 255))
    pen.text((80, 140), "OCT", font=font, fill=(255, 255, 255))
    src = os.path.join(ROOT, "photo-date.pdf")
    _picture(src, plate)
    before = _sha(src)
    asked = _ask(src, "change 11 OCT to 12 OCT", "photo-date")
    ok = asked.get("pending") is not True and (asked.get("reply") or "").strip() == SAFE_EDIT and _sha(src) == before
    _record("photo-date", ok, (asked.get("reply") or "")[:160])


def _flat_poppins_date() -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(POPPINS, 72)
    plate = Image.new("RGB", (520, 280), (248, 244, 236))
    pen = ImageDraw.Draw(plate)
    pen.text((60, 30), "11", font=font, fill=(20, 20, 20))
    pen.text((60, 130), "OCT", font=font, fill=(20, 20, 20))
    src = os.path.join(ROOT, "flat-date.pdf")
    _picture(src, plate)
    asked = _ask(src, "change 11 OCT to 12 OCT", "flat-date")
    ok, detail = _offered_clean(asked, src, "flat-date")
    _record("flat-poppins-date", ok and asked.get("pending") is True, detail)


def _serif_phone() -> None:
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import _load, _style_of

    face = ImageFont.truetype(CRIMSON, 22)
    plate = Image.new("RGB", (640, 160), (255, 255, 255))
    ImageDraw.Draw(plate).text((24, 60), "073 703 0766", font=face, fill=(12, 12, 12))
    src = os.path.join(ROOT, "serif-phone.pdf")
    _picture(src, plate)
    first = _ask(src, "change the phone number to 082 111 2222", "serif-phone")
    second = _ask(src, "change the phone number to 082 111 2222", "serif-phone-2")
    ok, detail = _offered_clean(first, src, "serif-phone")
    prop = _load("serif-phone").get("proposal") or {}
    prop2 = _load("serif-phone-2").get("proposal") or {}
    fontfile = str(prop.get("fontfile") or "")
    same = os.path.basename(fontfile) == os.path.basename(str(prop2.get("fontfile") or "")) and first.get("reply") == second.get("reply")
    serif = _style_of(fontfile) == "serif" and "crimson" in fontfile.lower()
    _record("serif-phone", ok and first.get("pending") is True and serif and same, detail + " " + os.path.basename(fontfile))


def _tiny_phone() -> None:
    """A number whose em would fall under 5 pt is refused before Apply."""
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import SAFE_EDIT
    from extra_checks import check_e5

    face = ImageFont.truetype(CRIMSON, 9)
    plate = Image.new("RGB", (420, 80), (255, 255, 255))
    ImageDraw.Draw(plate).text((8, 28), "073 703 0766", font=face, fill=(10, 10, 10))
    src = os.path.join(ROOT, "tiny-phone.pdf")
    _picture(src, plate)
    before = _sha(src)
    asked = _ask(src, "change the phone number to 082 111 2222", "tiny-phone")
    staged = os.path.join(asked.get("_preview") or "", "staged.pdf")
    # The staged file may exist, but Apply is not offered, and the source stays put.
    offered_bad = asked.get("pending") is True and os.path.isfile(staged) and check_e5(staged).get("status") == "failed"
    ok = asked.get("pending") is not True and (asked.get("reply") or "").strip() == SAFE_EDIT and _sha(src) == before and not offered_bad
    _record("tiny-phone", ok, (asked.get("reply") or "")[:160])


def _vector_date() -> None:
    import pymupdf as fitz

    src = os.path.join(ROOT, "vector-date.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=200)
    page.insert_font(fontname="MONT", fontfile=MONTSERRAT)
    page.insert_text((36, 80), "11 OCT", fontsize=28, fontname="MONT", fontfile=MONTSERRAT, color=(0.1, 0.1, 0.1))
    page.insert_text((36, 130), "Keep me", fontsize=14, fontname="MONT", fontfile=MONTSERRAT, color=(0.1, 0.1, 0.1))
    doc.save(src)
    doc.close()
    asked = _ask(src, "change 11 OCT to 12 OCT", "vector-date")
    from extra_checks import check_e5
    from artwork_edits import _load

    staged = os.path.join(asked.get("_preview") or "", "staged.pdf")
    spans = _spans(staged) if os.path.isfile(staged) else []
    body = " ".join(span.get("text") or "" for span in spans)
    names = " ".join(str(span.get("font") or "") for span in spans)
    prop = _load("vector-date").get("proposal") or {}
    fontfile = str(prop.get("fontfile") or "")
    row = check_e5(staged) if os.path.isfile(staged) else {}
    outside = _outside(src, staged, prop.get("editBox") or prop.get("bbox")) if asked.get("pending") else 999999
    ok = (
        asked.get("pending") is True
        and "12 OCT" in body
        and "Keep me" in body
        and "Montserrat" in names
        and "/usr/share/fonts" not in fontfile
        and row.get("status") != "failed"
        and outside == 0
        and _sha(src) == asked.get("_srcsha")
    )
    _record("vector-date", ok, f"{names} e5 {row.get('status')} outside {outside} {body[:40]}")


def _raster_time() -> None:
    from PIL import Image, ImageDraw, ImageFont

    face = ImageFont.truetype(LATO, 48)
    plate = Image.new("RGB", (520, 140), (255, 255, 255))
    ImageDraw.Draw(plate).text((30, 40), "10:00 AM", font=face, fill=(15, 15, 15))
    src = os.path.join(ROOT, "time.pdf")
    _picture(src, plate)
    asked = _ask(src, "change 10:00 AM to 7:30 PM", "raster-time")
    ok, detail = _offered_clean(asked, src, "raster-time")
    _record("raster-time", ok and asked.get("pending") is True, detail)


def _raster_venue() -> None:
    from PIL import Image, ImageDraw, ImageFont

    face = ImageFont.truetype(LATO, 36)
    plate = Image.new("RGB", (720, 140), (255, 255, 255))
    ImageDraw.Draw(plate).text((20, 46), "8 Waterbok Street", font=face, fill=(15, 15, 15))
    src = os.path.join(ROOT, "venue.pdf")
    _picture(src, plate)
    asked = _ask(src, "change the venue to Community Centre", "raster-venue")
    ok, detail = _offered_clean(asked, src, "raster-venue")
    _record("raster-venue", ok, detail + " pending " + str(asked.get("pending")))


def _church() -> None:
    import pymupdf as fitz
    from artwork_edits import SAFE_EDIT, _closest_font

    if not os.path.isfile(CHURCH):
        _record("church-date", False, "missing church jpeg")
        return
    src = os.path.join(ROOT, "church.pdf")
    doc = fitz.open()
    page = doc.new_page(width=447.9, height=623.6)
    page.insert_image(page.rect, filename=CHURCH)
    doc.save(src)
    doc.close()
    before = _sha(src)
    eleven = _ask(src, "change 11 OCT to 12 OCT", "church-11")
    nine = _ask(src, "change the date from 9 OCT to 12 October 2026", "church-9")
    import cv2
    image = cv2.imread(CHURCH)
    crop = image[2727:2727 + 362, 2073:2073 + 430]
    path, score = _closest_font(crop, "11 OCT")
    again, score2 = _closest_font(crop, "11 OCT")
    face = os.path.basename(path)
    refused = (
        eleven.get("pending") is not True
        and (eleven.get("reply") or "").strip() == SAFE_EDIT
        and nine.get("pending") is not True
        and "isn't on the artwork" not in (nine.get("reply") or "").lower()
        and "oct 6" not in (nine.get("reply") or "").lower()
        and _sha(src) == before
        and face == os.path.basename(again)
        and abs(score - score2) < 0.01
    )
    _record(
        "church-poster",
        refused,
        f"11 {(eleven.get('reply') or '')[:80]} | 9 {(nine.get('reply') or '')[:80]} | font {face} {score:.1f}",
    )


def main() -> None:
    _laptop_words()
    _gradient_date()
    _photo_date()
    _flat_poppins_date()
    _serif_phone()
    _tiny_phone()
    _vector_date()
    _raster_time()
    _raster_venue()
    _church()
    print(f"{len(ROWS) - len(FAILURES)} pass, {len(FAILURES)} fail")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
