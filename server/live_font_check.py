#!/usr/bin/env python3
"""Live text keeps its class. Picture reads stay quick. Ghosts are filled.

Each row reports offered or refused, the font, pixels outside the edit box, and undo.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time

ROOT = tempfile.mkdtemp(prefix="live-font-")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
sys.path.insert(0, os.path.dirname(__file__))

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
ART = "/opt/cursor/artifacts"

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


def _outside(before: str, after: str, box: list) -> int:
    import numpy as np
    from artwork_edits import _render_rgb

    if not box or len(box) < 4 or not os.path.isfile(after):
        return 999999
    left = _render_rgb(before, 2.5)
    right = _render_rgb(after, 2.5)
    if left.shape != right.shape:
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


def _subset_page(text: str, base_font: str, face_path: str) -> str:
    """A subset that cannot draw the new letters, named like the original face."""
    import pymupdf as fitz
    import pikepdf
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    safe = "".join(ch if ch.isalnum() else "-" for ch in base_font)[:40]
    subset_path = os.path.join(ROOT, safe + ".ttf")
    face = TTFont(face_path)
    subsetter = Subsetter(options=Options())
    subsetter.populate(text=text)
    subsetter.subset(face)
    family = base_font.split("-")[0]
    for rec in face["name"].names:
        if rec.nameID in (1, 4, 6, 16):
            rec.string = family
    face.save(subset_path)
    src = os.path.join(ROOT, safe + ".pdf")
    doc = fitz.open()
    page = doc.new_page(width=460, height=180)
    page.insert_font(fontname="FACE", fontfile=subset_path)
    page.insert_text((28, 100), text, fontsize=22, fontname="FACE", fontfile=subset_path, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    pdf = pikepdf.open(src, allow_overwriting_input=True)
    for item in pdf.pages[0].Resources.Font.values():
        item["/BaseFont"] = pikepdf.Name("/" + base_font.replace(" ", ""))
    pdf.save(src)
    pdf.close()
    return src


def _base14(text: str, fontname: str) -> str:
    import pymupdf as fitz

    src = os.path.join(ROOT, fontname + "-base.pdf")
    doc = fitz.open()
    page = doc.new_page(width=460, height=180)
    page.insert_text((28, 100), text, fontsize=22, fontname=fontname, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    return src


def _ask(src: str, message: str, key: str) -> dict:
    from artwork_edits import propose

    preview = os.path.join(ROOT, "preview-" + key)
    started = time.perf_counter()
    asked = propose(src, message, key, preview) or {}
    asked["_seconds"] = time.perf_counter() - started
    asked["_preview"] = preview
    asked["_src"] = src
    return asked


def _summarise(case: str, asked: dict, banned: tuple[str, ...] = (), allow_amber: bool = False) -> None:
    from artwork_edits import SAFE_EDIT, _load, confirm, undo

    reply = asked.get("reply") or ""
    preview = asked.get("_preview") or ""
    src = asked.get("_src") or ""
    staged = os.path.join(preview, "staged.pdf")
    prop = _load(case).get("proposal") or {}
    font = str(prop.get("faceLabel") or os.path.basename(str(prop.get("fontfile") or "")) or "refused")
    box = prop.get("guardBox") or prop.get("editBox") or []
    outside = _outside(src, staged, box) if asked.get("pending") else 0
    original = _sha(src)
    undo_ok = ""
    if asked.get("pending"):
        dest = os.path.join(ROOT, case + "-out.pdf")
        shutil.copyfile(src, dest)
        before = _sha(dest)
        done = confirm(case, dest)
        back = undo(case, dest) if done.get("ok") else {"ok": False}
        undo_ok = "undo-exact" if done.get("ok") and back.get("ok") and _sha(dest) == before else "undo-FAIL"
        if _sha(src) != original:
            undo_ok = "source-changed"
    else:
        undo_ok = "not-applied"
        if _sha(src) != original:
            undo_ok = "source-changed"
    offered = "offered" if asked.get("pending") else "refused"
    detail = f"{offered} font {font} outside {outside} {undo_ok} {asked.get('_seconds', 0):.2f}s | {reply[:110]}"
    low = (reply + " " + font).lower()
    problems = []
    if outside != 0:
        problems.append(f"outside {outside}")
    if undo_ok.endswith("FAIL") or undo_ok == "source-changed":
        problems.append(undo_ok)
    for word in banned:
        if word in low:
            problems.append("banned " + word)
    if asked.get("pending") and "amber" in reply.lower() and not allow_amber:
        problems.append("amber on a class match")
    _record(case, not problems, detail + ((" ; " + "; ".join(problems)) if problems else ""))


def _fonts() -> None:
    """Real text uses the same class. A mismatch is refused, not amber."""
    sans_bold = os.path.join(FONT_DIR, "LiberationSans-Bold.ttf")
    sans = os.path.join(FONT_DIR, "LiberationSans-Regular.ttf")
    serif = os.path.join(FONT_DIR, "v2", "LibreBaskerville-Regular.ttf")
    display = os.path.join(FONT_DIR, "BebasNeue-Regular.ttf")
    script = os.path.join(FONT_DIR, "v2", "GreatVibes-Regular.ttf")
    phone = "PHONE: 067 1345 937"
    date = "Doors 9 OCT"
    cases = [
        ("arial-bold", _subset_page(phone, "Arial-BoldMT", sans_bold), "change the phone number to 082 123 4567", ("liberationsans", "bold"), ("vibes", "parisienne", "pinyon", "bebas")),
        ("helvetica", _base14(phone, "helv"), "change the phone number to 082 123 4567", ("liberation",), ("vibes", "parisienne", "pinyon")),
        ("verdana", _subset_page(phone, "Verdana", sans), "change the phone number to 082 123 4567", ("nunito", "opensans", "lato", "liberation"), ("vibes", "pinyon", "bebas")),
        ("georgia", _subset_page(date, "Georgia", serif), "change the date from 9 OCT to 12 OCT", ("baskerville", "crimson", "garamond"), ("pinyon", "vibes", "bebas")),
        ("times", _base14(date, "tiro"), "change the date from 9 OCT to 12 OCT", ("baskerville", "crimson", "garamond"), ("bebas", "pinyon", "vibes")),
        ("impact", _subset_page(date, "Impact", display), "change the date from 9 OCT to 12 OCT", ("bebas", "anton", "oswald"), ("pinyon", "vibes", "parisienne")),
        ("montserrat-bold", _subset_page(date, "Montserrat-Bold", os.path.join(FONT_DIR, "v2", "Montserrat-700.ttf")), "change the date from 9 OCT to 12 OCT", ("montserrat",), ("vibes", "pinyon", "parisienne")),
        ("snell-script", _subset_page(date, "SnellRoundhand", script), "change the date from 9 OCT to 12 OCT", ("vibes", "parisienne", "pinyon", "allura"), ("liberation", "bebas")),
        ("oswald", _subset_page(date, "Oswald-Regular", os.path.join(FONT_DIR, "Oswald-Regular.ttf")), "change the date from 9 OCT to 12 OCT", ("oswald", "anton", "bebas"), ("vibes", "pinyon")),
    ]
    for case, src, message, wanted, banned in cases:
        asked = _ask(src, message, case)
        reply = (asked.get("reply") or "").lower()
        font = ""
        from artwork_edits import _load
        prop = _load(case).get("proposal") or {}
        font = (str(prop.get("faceLabel") or "") + " " + os.path.basename(str(prop.get("fontfile") or ""))).lower()
        _summarise(case, asked, banned)
        blob = reply + " " + font
        if asked.get("pending") and not any(word in blob for word in wanted):
            _record(case + "-class", False, "font not in class " + font + " reply " + reply[:80])
        elif not asked.get("pending"):
            polite = "safely change" in reply or "outside those words" in reply
            wrong = any(word in blob for word in banned)
            _record(case + "-class", polite and not wrong, "refused " + reply[:100])
    rockwell = _subset_page(date, "Rockwell", serif)
    asked = _ask(rockwell, "change the date from 9 OCT to 12 OCT", "rockwell")
    from artwork_edits import SAFE_EDIT
    reply = asked.get("reply") or ""
    _record(
        "rockwell-slab",
        reply.strip() == SAFE_EDIT and asked.get("pending") is not True and "amber" not in reply.lower(),
        reply[:140],
    )


def _side_ghost(staged: str, old_box: tuple, needle: str) -> tuple[float, float]:
    """How far the gap beside the new words is from the panel colour. 0 is clean."""
    import pymupdf as fitz
    import numpy as np
    from artwork_edits import _render_rgb

    doc = fitz.open(staged)
    new = None
    try:
        for block in (doc[0].get_text("dict") or {}).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    if needle in str(span.get("text") or ""):
                        new = span["bbox"]
    finally:
        doc.close()
    if not new:
        return 99.0, 99.0
    image = _render_rgb(staged, 4)
    scale = 4.0
    y0, y1 = int(old_box[1] * scale), int(old_box[3] * scale)
    panel = image[max(0, y0 - 28): max(1, y0 - 8), int(old_box[0] * scale): int((old_box[0] + 40) * scale)]
    if panel.size == 0:
        return 99.0, 99.0
    colour = panel.reshape(-1, 3).astype(np.float32).mean(axis=0)

    def gap(x0: float, x1: float) -> float:
        if x1 - x0 < 2:
            return 0.0
        band = image[y0:y1, int(x0 * scale): int(x1 * scale)]
        if band.size == 0:
            return 0.0
        delta = np.linalg.norm(band.astype(np.float32) - colour, axis=2)
        return float(np.median(delta))

    return gap(old_box[0], float(new[0]) - 1.0), gap(float(new[2]) + 1.0, old_box[2])


def _ghost() -> None:
    """A shorter venue removes the whole original line, including both sides."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont

    anton = os.path.join(FONT_DIR, "Anton-Regular.ttf")
    plate = Image.new("RGB", (920, 320), (232, 196, 92))
    ImageDraw.Draw(plate).text((72, 150), "18 BELLAVISTA ROAD HADDON", font=ImageFont.truetype(anton, 32), fill=(20, 20, 20))
    raster = os.path.join(ROOT, "venue-raster.pdf")
    png = raster + ".png"
    plate.save(png)
    doc = fitz.open()
    page = doc.new_page(width=460, height=160)
    page.insert_image(page.rect, filename=png)
    doc.save(raster)
    doc.close()
    asked = _ask(raster, "change the venue to The Grand Hall", "ghost-raster")
    _summarise("ghost-raster", asked, ("vibes", "pinyon"), allow_amber=True)

    # The words are baked into the picture and also sit on top as real text,
    # which is how the A6 venue kept a ghost on both sides.
    live = os.path.join(ROOT, "venue-live.pdf")
    doc = fitz.open()
    page = doc.new_page(width=460, height=160)
    page.draw_rect(page.rect, fill=(0.91, 0.77, 0.36), color=(0.91, 0.77, 0.36))
    page.insert_font(fontname="ANTON", fontfile=anton)
    page.insert_text((36, 90), "18 BELLAVISTA ROAD HADDON", fontsize=16, fontname="ANTON", fontfile=anton, color=(0.08, 0.08, 0.08))
    doc.save(live)
    doc.close()
    shot = fitz.open(live)
    rendered = os.path.join(ROOT, "venue-baked.png")
    shot[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(rendered)
    old_box = None
    for block in (shot[0].get_text("dict") or {}).get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                if "BELLAVISTA" in str(span.get("text") or ""):
                    old_box = tuple(span["bbox"])
    shot.close()
    src = os.path.join(ROOT, "venue-vector.pdf")
    doc = fitz.open()
    page = doc.new_page(width=460, height=160)
    page.insert_image(page.rect, filename=rendered)
    page.insert_font(fontname="ANTON", fontfile=anton)
    page.insert_text((36, 90), "18 BELLAVISTA ROAD HADDON", fontsize=16, fontname="ANTON", fontfile=anton, color=(0.08, 0.08, 0.08))
    doc.save(src)
    doc.close()
    asked = _ask(src, "change the venue to The Grand Hall", "ghost-vector")
    _summarise("ghost-vector", asked, ("vibes", "pinyon", "parisienne"))
    staged = os.path.join(asked["_preview"], "staged.pdf")
    if asked.get("pending") and old_box and os.path.isfile(staged):
        left, right = _side_ghost(staged, old_box, "GRAND")
        ok = left < 12 and right < 12
        _record("ghost-vector-sides", ok, f"panel gap left {left:.1f} right {right:.1f}")
    else:
        _record("ghost-vector-sides", False, (asked.get("reply") or "")[:120])


def _vague_and_picture() -> None:
    from artwork_edits import propose
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    asked = propose("/no/such.pdf", "make it look better", "vague-look", ROOT) or {}
    reply = asked.get("reply") or ""
    low = reply.lower()
    ok = (
        "nothing was changed" in low
        and "exact words" in low
        and "25-point" not in low
        and "check" not in low
        and asked.get("pending") is not True
    )
    _record("vague-look-better", ok, reply[:160])

    # Text baked into a noisy picture. A miss must not claim the date is absent.
    rng = np.random.default_rng(4)
    noise = rng.integers(40, 180, size=(420, 640, 3), dtype=np.uint8)
    plate = Image.fromarray(noise, "RGB")
    draw = ImageDraw.Draw(plate)
    face = ImageFont.truetype(os.path.join(FONT_DIR, "v2", "GreatVibes-Regular.ttf"), 28)
    draw.text((180, 180), "14 NOV", font=face, fill=(70, 60, 55))
    src = os.path.join(ROOT, "picture-date.pdf")
    png = src + ".png"
    plate.save(png)
    doc = fitz.open()
    page = doc.new_page(width=320, height=210)
    page.insert_image(page.rect, filename=png)
    doc.save(src)
    doc.close()
    started = time.perf_counter()
    asked = _ask(src, "change the date from 14 NOV to 20 November 2026", "picture-date")
    elapsed = time.perf_counter() - started
    reply = asked.get("reply") or ""
    low = reply.lower()
    ok = "isn't on the artwork" not in low and elapsed < 25 and ("can't read that clearly" in low or asked.get("pending") is True)
    _record("picture-date", ok, f"{elapsed:.2f}s {reply[:140]}")


def _speed() -> None:
    import artwork_edits as edits

    church_jpeg = "/tmp/ev/church_embedded.jpeg"
    flyer = "/tmp/medella_flyer.pdf"
    if not os.path.isfile(church_jpeg) or not os.path.isfile(flyer):
        _record("speed", False, "missing church jpeg or flyer")
        return
    import pymupdf as fitz
    src = os.path.join(ROOT, "church-speed.pdf")
    doc = fitz.open()
    page = doc.new_page(width=447.9, height=623.6)
    page.insert_image(page.rect, filename=church_jpeg)
    doc.save(src)
    doc.close()
    edits._OCR_MEM.clear()
    first = _ask(src, "change the date from 9 OCT to 12 October 2026", "speed-church")
    edits._OCR_MEM.clear()
    second = _ask(src, "change the date from 9 OCT to 12 October 2026", "speed-church")
    edits._OCR_MEM.clear()
    fly = _ask(flyer, "change the phone number to 082 123 4567", "speed-flyer")
    edits._OCR_MEM.clear()
    fly2 = _ask(flyer, "change the phone number to 082 123 4567", "speed-flyer")
    church_ok = first.get("pending") is True and first["_seconds"] < 20 and second["_seconds"] < 12
    flyer_ok = fly["_seconds"] < 20 and fly2["_seconds"] < 12 and (fly.get("pending") is True or "safely change" in (fly.get("reply") or "").lower() or "can't read" in (fly.get("reply") or "").lower())
    detail = (
        f"church {first['_seconds']:.2f}s retry {second['_seconds']:.2f}s pending {first.get('pending')} | "
        f"flyer {fly['_seconds']:.2f}s retry {fly2['_seconds']:.2f}s pending {fly.get('pending')} {(fly.get('reply') or '')[:60]}"
    )
    _record("speed", church_ok and flyer_ok, detail)
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "proposal-times.txt"), "w", encoding="utf-8") as handle:
        handle.write(detail + "\n")


def main() -> None:
    _fonts()
    _ghost()
    _vague_and_picture()
    _speed()
    os.makedirs(ART, exist_ok=True)
    lines = ["case\tresult\tdetail"]
    for row in ROWS:
        lines.append(f"{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}")
    with open(os.path.join(ART, "live-font-results.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"{len(ROWS) - len(FAILURES)} pass, {len(FAILURES)} fail")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
