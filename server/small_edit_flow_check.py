#!/usr/bin/env python3
"""Ian's small-edit flow: he types the new value, the cat keeps the old format.

This is the acceptance test for a date, a phone number, a time, and a venue.
The preview is shown first. The file changes only after yes, and undo puts it back.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="small-edit-")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
ART = "/opt/cursor/artifacts/cat-flow"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SCRIPT = os.path.join(os.path.dirname(__file__), "designer_assistant.py")

ROWS: list[dict] = []
FAILURES: list[str] = []
TRANSCRIPT: list[str] = []


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


def _text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        return doc[0].get_text("text") or ""
    finally:
        doc.close()


def _visual(src: str, preview: str, key: str) -> tuple[str, list[str]]:
    """Pixel, region, and page gates. A case fails when any one of them fails."""
    from artwork_edits import _load, gate_line, visual_gates

    prop = (_load(key).get("proposal") or {})
    staged = os.path.join(preview, "staged.pdf")
    problems = visual_gates(
        src,
        staged,
        prop.get("guardBox") or prop.get("editBox"),
        str(prop.get("shownOld") or prop.get("old") or ""),
        str(prop.get("shownNew") or prop.get("new") or ""),
    )
    return gate_line(problems), problems


def _live(path: str, line: str) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=420, height=240)
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((36, 80), line, fontsize=16, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 130), "Keep me", fontsize=14, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    doc.save(path)
    doc.close()


def _flow(name: str, line: str, message: str, expect: str, absent: str) -> None:
    from artwork_edits import confirm, propose, undo

    src = os.path.join(ROOT, f"{name}.pdf")
    dest = os.path.join(ROOT, f"{name}-out.pdf")
    preview = os.path.join(ROOT, f"preview-{name}")
    _live(src, line)
    original = _sha(src)
    asked = propose(src, message, name, preview)
    reply = (asked or {}).get("reply") or ""
    TRANSCRIPT.append(f"## {name}\n{message}\n{reply}\n")
    problems = []
    if not asked or asked.get("pending") is not True or "not applied" not in reply.lower():
        problems.append(reply[:180] or "no preview")
    if os.path.isfile(dest) or _sha(src) != original:
        problems.append("applied before yes")
    if expect not in reply:
        problems.append(f"reply missed {expect}")
    staged = os.path.join(preview, "staged.pdf")
    staged_text = _text(staged) if os.path.isfile(staged) else ""
    if expect not in staged_text or absent in staged_text:
        problems.append("staged " + staged_text.replace("\n", " ")[:120])
    if "Keep me" not in staged_text:
        problems.append("other text lost")
    before = os.path.join(preview, "before.png")
    after = os.path.join(preview, "after.png")
    if not (os.path.isfile(before) and os.path.isfile(after)) or _sha(before) == _sha(after):
        problems.append("preview pictures")
    else:
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(before, os.path.join(ART, f"{name}-before.png"))
        shutil.copyfile(after, os.path.join(ART, f"{name}-after.png"))
    gate, gate_problems = _visual(src, preview, name)
    problems.extend(gate_problems)
    if problems:
        _record(name, False, "; ".join(problems) + " " + gate)
        return
    done = confirm(name, dest)
    TRANSCRIPT.append(f"yes -> {done.get('reply')}\n")
    if not done.get("ok") or expect not in _text(dest) or absent in _text(dest):
        problems.append("confirm " + _text(dest).replace("\n", " ")[:80])
    if not any(item.get("id") == "undo-edit" for item in (done.get("actions") or [])):
        problems.append("no undo")
    if _sha(src) != original:
        problems.append("source changed")
    undone = undo(name, dest)
    TRANSCRIPT.append(f"undo -> {undone.get('reply')}\n")
    if not undone.get("ok") or absent not in _text(dest) or expect in _text(dest):
        problems.append("undo " + _text(dest).replace("\n", " ")[:80])
    if os.path.isfile(dest) and _sha(dest) != original:
        problems.append("undo bytes differ")
    _record(name, not problems, "; ".join(problems) or f"{expect} {gate}")


def _full_font() -> None:
    """A subset tag resolves to the face shipped with the app."""
    from fontTools.ttLib import TTFont

    from artwork_edits import _glyphs_present, _named_font

    wanted = (
        "PWSYDC+Anton-Regular",
        "Montserrat-Regular",
        "Poppins-Regular",
        "OpenSans-Regular",
        "Roboto-Regular",
        "Lato-Regular",
        "Oswald-Regular",
        "BebasNeue-Regular",
        "PlayfairDisplay-Regular",
        "ABCDEF+Raleway-Regular",
        "LeagueSpartan-Regular",
        "ArchivoBlack-Regular",
        "Inter-Regular",
        "Nunito-Regular",
        "GreatVibes-Regular",
    )
    problems = []
    anton = _named_font("PWSYDC+Anton-Regular") or ""
    if os.path.basename(anton) != "Anton-Regular.ttf":
        problems.append("anton " + (os.path.basename(anton) or "missing"))
    for name in wanted:
        path = _named_font(name) or ""
        if not path or not _glyphs_present(path, "Community Centre 082"):
            problems.append(name)
            continue
        if "Raleway" in name or "Playfair" in name or name.startswith("Montserrat"):
            weight = int(TTFont(path)["OS/2"].usWeightClass)
            if weight != 400:
                problems.append(f"{name} weight {weight}")
    bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts"))
    if anton and not os.path.abspath(anton).startswith(bundled):
        problems.append("anton not bundled")
    _record("full-font", not problems, "; ".join(problems) or "15 families, regular weight")


def _subset_file(text: str, family: str) -> str:
    """A Canva-style subset that only contains the letters already on the page."""
    import pymupdf as fitz
    import pikepdf
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    anton = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts", "Anton-Regular.ttf"))
    subset_path = os.path.join(ROOT, family.replace(" ", "") + "-subset.ttf")
    face = TTFont(anton)
    subsetter = Subsetter(options=Options())
    subsetter.populate(text=text)
    subsetter.subset(face)
    full = f"{family} Regular"
    ps = family.replace(" ", "") + "-Regular"
    for rec in face["name"].names:
        if rec.nameID in (1, 16):
            rec.string = family
        elif rec.nameID in (2, 17):
            rec.string = "Regular"
        elif rec.nameID == 4:
            rec.string = full
        elif rec.nameID == 6:
            rec.string = ps
    face.save(subset_path)
    src = os.path.join(ROOT, family.replace(" ", "") + "-page.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=160)
    page.insert_font(fontname="FACE", fontfile=subset_path)
    page.insert_text((36, 80), text, fontsize=16, fontname="FACE", fontfile=subset_path, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    pdf = pikepdf.open(src, allow_overwriting_input=True)
    token = family.replace(" ", "")
    for item in pdf.pages[0].Resources.Font.values():
        item["/BaseFont"] = pikepdf.Name(f"/PWSYDC+{token}-Regular")
    pdf.save(src)
    pdf.close()
    return src


def _subset_gap() -> None:
    """The subset lacks the new digits, so the words are set in the full Anton face."""
    import pymupdf as fitz
    from artwork_edits import propose

    src = _subset_file("PHONE: 067 1345 937", "Anton")
    preview = os.path.join(ROOT, "preview-subset-gap")
    asked = propose(src, "change the phone number to 082 123 4567", "subset-gap", preview)
    reply = (asked or {}).get("reply") or ""
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    problems = []
    if "Anton Regular" not in reply or "amber" in reply.lower():
        problems.append(reply[:180] or "no reply")
    if "082 123 4567" not in text or "067 1345 937" in text or "082 1234 567" in text:
        problems.append(text.replace("\n", " ")[:120] or "no staged text")
    if os.path.isfile(staged):
        doc = fitz.open(staged)
        try:
            blob = ""
            for xref in doc[0].get_contents() or []:
                blob += (doc.xref_stream(int(xref)) or b"").decode("latin1", "replace")
            names = [str(item[3]) for item in (doc[0].get_fonts() or [])]
        finally:
            doc.close()
        if "0 0 0 1 k" not in blob:
            problems.append("black was not kept as K-only")
        full = [name for name in names if "Anton" in name and "+" not in name]
        if not full or any(name == "Helvetica" for name in names):
            problems.append("font " + ", ".join(names))
    gate, gate_problems = _visual(src, preview, "subset-gap")
    problems.extend(gate_problems)
    _record("subset-gap", not problems, "; ".join(problems) or f"full Anton, 100% K {gate}")


def _subset_closest() -> None:
    """An unknown family with a short subset is the closest bundled face, and it says so."""
    from artwork_edits import propose

    src = _subset_file("PHONE: 067 1345 937", "Xylophone")
    preview = os.path.join(ROOT, "preview-subset-closest")
    asked = propose(src, "change the phone number to 082 123 4567", "subset-closest", preview)
    reply = (asked or {}).get("reply") or ""
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    problems = []
    if "amber" not in reply.lower() or "closest font" not in reply.lower():
        problems.append(reply[:180] or "silent")
    if "082 123 4567" not in text or "082 1234 567" in text:
        problems.append(text.replace("\n", " ")[:120] or "no staged text")
    if os.path.isfile(staged):
        import pymupdf as fitz

        doc = fitz.open(staged)
        try:
            names = [str(item[3]) for item in (doc[0].get_fonts() or [])]
        finally:
            doc.close()
        if any(name == "Helvetica" for name in names) or not any("+" not in name and "Xylophone" not in name for name in names):
            problems.append("font " + ", ".join(names))
    gate, gate_problems = _visual(src, preview, "subset-closest")
    problems.extend(gate_problems)
    _record("subset-closest", not problems, "; ".join(problems) or f"amber closest {gate}")


def _subset_font() -> None:
    """A Canva subset tag (ABCDEF+Anton-Regular) is still the Anton file."""
    import pymupdf as fitz
    import pikepdf
    from artwork_edits import _embedded_font

    src = os.path.join(ROOT, "subset-anton.pdf")
    font = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "fonts", "Anton-Regular.ttf"))
    doc = fitz.open()
    page = doc.new_page(width=320, height=120)
    page.insert_font(fontname="ANTON", fontfile=font)
    page.insert_text((16, 70), "Call 067 1345 937", fontsize=16, fontname="ANTON", fontfile=font)
    doc.save(src)
    doc.close()
    pdf = pikepdf.open(src, allow_overwriting_input=True)
    for face in pdf.pages[0].Resources.Font.values():
        face["/BaseFont"] = pikepdf.Name("/ABCDEF+Anton-Regular")
    pdf.save(src)
    pdf.close()
    doc = fitz.open(src)
    try:
        found = _embedded_font(doc, doc[0], "Anton-Regular")
    finally:
        doc.close()
    _record("subset-font", bool(found), found or "subset tag hid Anton")


def _phone_icon() -> None:
    """A phone icon read as empty brackets is not part of the number."""
    from artwork_edits import _select_hit
    from edit_style import swap_text

    words = [
        {"text": "®)", "left": 10, "top": 40, "width": 20, "height": 18},
        {"text": "073", "left": 40, "top": 40, "width": 30, "height": 18},
        {"text": "703", "left": 80, "top": 40, "width": 30, "height": 18},
        {"text": "0766", "left": 120, "top": 40, "width": 40, "height": 18},
    ]
    hit = _select_hit(words, {"kind": "phone", "new": "082 123 4567"})
    problems = []
    text = (hit or {}).get("text") or ""
    if text != "073 703 0766" or "®" in text or "()" in text:
        problems.append("boxes " + repr(text))
    found, styled, full = swap_text("() 073 703 0766", "phone", "082 123 4567")
    if styled != "082 123 4567" or "()" in (full or "") or "()" in (found or ""):
        problems.append(f"empty {found!r} {styled!r} {full!r}")
    _found, _styled, call = swap_text("Call () 073 703 0766", "phone", "082 123 4567")
    if "()" in (call or "") or "082 123 4567" not in (call or ""):
        problems.append(f"call {call!r}")
    _found, kept, _full = swap_text("(073) 703 0766", "phone", "082 123 4567")
    if kept != "(082) 123 4567":
        problems.append(f"real brackets {kept!r}")
    _record("phone-icon", not problems, "; ".join(problems) or "073 703 0766")


def _raster_date() -> None:
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import propose

    src = os.path.join(ROOT, "raster-date.pdf")
    png = os.path.join(ROOT, "raster-date.png")
    plate = Image.new("RGB", (900, 240), (255, 255, 255))
    draw = ImageDraw.Draw(plate)
    font = ImageFont.truetype(FONT, 42)
    draw.text((40, 80), "Saturday 5 March", font=font, fill=(0, 0, 0))
    plate.save(png)
    doc = fitz.open()
    page = doc.new_page(width=450, height=120)
    page.insert_image(page.rect, filename=png)
    doc.save(src)
    doc.close()
    preview = os.path.join(ROOT, "preview-raster-date")
    asked = propose(src, "change the date to 12 October 2026", "raster-date", preview)
    reply = (asked or {}).get("reply") or ""
    TRANSCRIPT.append(f"## raster-date\n{reply}\n")
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    gate, gate_problems = _visual(src, preview, "raster-date")
    ok = (
        asked and asked.get("pending") and "Monday 12 October" in text and "Saturday" not in text
        and "not applied" in reply.lower() and not gate_problems
    )
    if os.path.isfile(os.path.join(preview, "before.png")):
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(os.path.join(preview, "before.png"), os.path.join(ART, "raster-date-before.png"))
        shutil.copyfile(os.path.join(preview, "after.png"), os.path.join(ART, "raster-date-after.png"))
    detail = text.replace("\n", " ")[:80] or reply[:80]
    if gate_problems:
        detail = "; ".join(gate_problems)
    _record("raster-saturday", bool(ok), f"{detail} {gate}")


def _cat_yes() -> None:
    src = os.path.join(ROOT, "cat-date.pdf")
    dest = os.path.join(ROOT, "cat-date-out.pdf")
    preview = os.path.join(ROOT, "preview-cat-date")
    _live(src, "Saturday 5 March")
    original = _sha(src)
    env = os.environ.copy()

    def ask(message: str, action: str = "") -> dict:
        cmd = [sys.executable, SCRIPT, "--input", src, "--message", message, "--output", dest,
               "--job-id", "ian-date", "--preview-dir", preview, "--trim-w", "148", "--trim-h", "105"]
        if action:
            cmd.extend(["--action", action])
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180, check=False)
        payload = {}
        for line in reversed((proc.stdout or "").splitlines()):
            if line.startswith("{"):
                payload = json.loads(line)
                break
        TRANSCRIPT.append(f"## cat {action or message}\n{payload.get('reply') or proc.stderr[-200:]}\n")
        return payload

    asked = ask("change the date to 12 October 2026")
    problems = []
    gate, gate_problems = _visual(src, preview, "ian-date")
    problems.extend(gate_problems)
    if "Monday 12 October" not in (asked.get("reply") or "") or "not applied" not in (asked.get("reply") or "").lower():
        problems.append((asked.get("reply") or "no reply")[:160])
    if os.path.isfile(dest):
        problems.append("applied before yes")
    done = ask("yes")
    if "Monday 12 October" not in _text(dest) or "Saturday" in _text(dest):
        problems.append("yes " + _text(dest).replace("\n", " ")[:80])
    reply = done.get("reply") or ""
    if "25-point" not in reply or "E1" not in reply:
        problems.append("checks not re-run")
    for row in done.get("checks") or []:
        status = str(row.get("status") or "").lower()
        blob = " ".join(str(row.get(key) or "") for key in ("name", "detail", "id", "num")).lower()
        if status == "failed" and any(word in blob for word in ("font", "embed", "substitut", "helvetica")):
            problems.append("font check failed " + str(row.get("name") or row.get("num") or "")[:60])
    if _sha(src) != original:
        problems.append("source changed")
    undone = ask("undo")
    if "Saturday 5 March" not in _text(dest):
        problems.append("undo " + _text(dest).replace("\n", " ")[:80])
    if "25-point" not in (undone.get("reply") or ""):
        problems.append("undo checks")
    for row in undone.get("checks") or []:
        status = str(row.get("status") or "").lower()
        blob = " ".join(str(row.get(key) or "") for key in ("name", "detail", "id", "num")).lower()
        if status == "failed" and any(word in blob for word in ("font", "embed", "substitut", "helvetica")):
            problems.append("undo font check failed " + str(row.get("name") or "")[:40])
    _record("cat-yes-undo", not problems, "; ".join(problems) or f"Monday 12 October, then back {gate}")


def _overlap_time() -> None:
    """Stacked Anton lines share em-boxes. Replacing the time must leave both neighbours."""
    import pymupdf as fitz
    from artwork_edits import propose

    anton = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts", "Anton-Regular.ttf"))
    src = os.path.join(ROOT, "overlap-time.pdf")
    doc = fitz.open()
    page = doc.new_page(width=340, height=180)
    page.insert_font(fontname="ANTON", fontfile=anton)
    face = fitz.Font(fontfile=anton)
    page.insert_text((24, 48), "SUNDAY", fontsize=26, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    page.insert_text((16, 68), "MORNING WORSHIP ", fontsize=13, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    time_x = 16 + face.text_length("MORNING WORSHIP ", fontsize=13)
    page.insert_text((time_x, 68), "10:00AM", fontsize=13, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    page.insert_text((16, 82), "EVENING WORSHIP 5:00PM", fontsize=13, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    doc = fitz.open(src)
    boxes = []
    try:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    boxes.append((span.get("text") or "", fitz.Rect(span["bbox"])))
    finally:
        doc.close()
    problems = []
    time_box = next((box for text, box in boxes if "10:00AM" in text), None)
    sunday = next((box for text, box in boxes if "SUNDAY" in text), fitz.Rect())
    evening = next((box for text, box in boxes if "EVENING" in text), fitz.Rect())
    if time_box is None or (time_box & sunday).is_empty or (time_box & evening).is_empty:
        problems.append("fixture em-boxes do not overlap")
    preview = os.path.join(ROOT, "preview-overlap-time")
    asked = propose(src, "change the time from 10:00AM to 7:30 pm", "overlap-time", preview)
    reply = (asked or {}).get("reply") or ""
    TRANSCRIPT.append(f"## overlap-time\n{reply}\n")
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    if "7:30PM" not in text or "10:00" in text:
        problems.append(text.replace("\n", " ")[:140] or "time not replaced")
    if "SUNDAY" not in text or "EVENING WORSHIP 5:00PM" not in text:
        problems.append("neighbour lost " + text.replace("\n", " ")[:140])
    if os.path.isfile(staged):
        import pymupdf as fitz
        doc = fitz.open(staged)
        try:
            words = [item[4] for item in doc[0].get_text("words")]
        finally:
            doc.close()
        if "1" in words:
            problems.append("leftover 1")
    gate, gate_problems = _visual(src, preview, "overlap-time")
    problems.extend(gate_problems)
    if os.path.isfile(os.path.join(preview, "after.png")):
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(os.path.join(preview, "before.png"), os.path.join(ART, "overlap-time-before.png"))
        shutil.copyfile(os.path.join(preview, "after.png"), os.path.join(ART, "overlap-time-after.png"))
    _record("time-overlap", not problems, "; ".join(problems) or f"neighbours kept {gate}")


def _shrink_venue() -> None:
    """A longer venue that would leave a narrow column is set smaller, and the reply says so."""
    import pymupdf as fitz
    from artwork_edits import _load, propose

    src = os.path.join(ROOT, "shrink-venue.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(18, 28, 148, 110), color=(0.9, 0.9, 0.9), fill=(0.9, 0.9, 0.9), width=0)
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((26, 78), "CITY HALL", fontsize=16, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((220, 78), "Keep me", fontsize=14, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    preview = os.path.join(ROOT, "preview-shrink-venue")
    asked = propose(src, "change the venue to Community Centre", "shrink-venue", preview)
    reply = (asked or {}).get("reply") or ""
    TRANSCRIPT.append(f"## shrink-venue\n{reply}\n")
    prop = (_load("shrink-venue").get("proposal") or {})
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    problems = []
    if "smaller" not in reply.lower() or "column" not in reply.lower():
        problems.append(reply[:180] or "no shrink note")
    if not prop.get("shrunk") or float(prop.get("size") or 16) >= 16:
        problems.append(f"size {prop.get('size')}")
    if "COMMUNITY CENTRE" not in text or "CITY HALL" in text or "Keep me" not in text:
        problems.append(text.replace("\n", " ")[:140] or "text")
    gate, gate_problems = _visual(src, preview, "shrink-venue")
    problems.extend(gate_problems)
    if os.path.isfile(os.path.join(preview, "after.png")):
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(os.path.join(preview, "before.png"), os.path.join(ART, "shrink-venue-before.png"))
        shutil.copyfile(os.path.join(preview, "after.png"), os.path.join(ART, "shrink-venue-after.png"))
    _record("venue-shrink", not problems and bool(asked), "; ".join(problems) or f"shrunk to fit {gate}")


def _windows_font_paths() -> None:
    """A font that exists only on this computer is not used. The bundled Anton is."""
    from pathlib import Path

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    import artwork_edits as edits

    folder = tempfile.mkdtemp(prefix="win-fonts-")
    windir = os.path.join(folder, "Windows")
    local = os.path.join(folder, "Local")
    system = os.path.join(windir, "Fonts")
    user = os.path.join(local, "Microsoft", "Windows", "Fonts")
    os.makedirs(system)
    os.makedirs(user)
    bundled_root = os.path.join(os.path.dirname(edits.__file__), "fonts")
    shutil.copyfile(os.path.join(bundled_root, "Anton-Regular.ttf"), os.path.join(system, "Bahnschrift.ttf"))
    shutil.copyfile(FONT, os.path.join(user, "UserFace.ttf"))
    conf = os.path.join(folder, "fonts.conf")
    with open(conf, "w", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0"?><fontconfig><dir>/no/such/fonts</dir></fontconfig>\n')
    old_win = os.environ.get("WINDIR")
    old_local = os.environ.get("LOCALAPPDATA")
    old_fc = os.environ.get("FONTCONFIG_FILE")
    old_fc_path = os.environ.get("FONTCONFIG_PATH")
    old_index = edits._FONT_INDEX
    problems = []
    try:
        os.environ["WINDIR"] = windir
        os.environ["LOCALAPPDATA"] = local
        os.environ["FONTCONFIG_FILE"] = conf
        os.environ["FONTCONFIG_PATH"] = folder
        edits._FONT_INDEX = None
        dirs = edits._font_search_dirs()
        bundled = os.path.normpath(bundled_root)
        if dirs != [bundled]:
            problems.append("search " + repr(dirs))
        if edits._windows_font_dirs():
            problems.append("windows dirs were searched")
        rows = edits._font_index()
        paths = [os.path.abspath(row["path"]) for row in rows]
        if any(path.startswith(os.path.abspath(folder)) for path in paths):
            problems.append("machine font indexed")
        if any("/usr/share/fonts" in path for path in paths):
            problems.append("system font indexed")
        if any("dejavu" in os.path.basename(path).lower() for path in paths):
            problems.append("dejavu indexed")
        if not any("crimson" in os.path.basename(path).lower() for path in paths):
            problems.append("v2 serif missing")
        if any(row["kind"] != "bundled" for row in rows):
            problems.append("kind " + ",".join(sorted({row["kind"] for row in rows})))
        named = edits._named_font("PWSYDC+Anton-Regular")
        if not named or not os.path.abspath(named).startswith(bundled):
            problems.append("named " + str(named))
        serif_file = os.path.join(bundled_root, "v2", "CrimsonText-Regular.ttf")
        if not os.path.isfile(serif_file):
            problems.append("no bundled serif")
        else:
            face = ImageFont.truetype(serif_file, 48)
            plate = Image.new("RGB", (520, 90), (255, 255, 255))
            ImageDraw.Draw(plate).text((6, 8), "082 123 4567", font=face, fill=(20, 20, 20))
            crop = np.array(plate)[:, :, ::-1]
            first, first_score = edits._closest_font(crop, "082 123 4567")
            second, second_score = edits._closest_font(crop, "082 123 4567")
            if first != second or first_score != second_score:
                problems.append(f"unstable {first} {second}")
            if not os.path.abspath(first).startswith(bundled):
                problems.append("closest " + str(first))
            if edits._style_of(first) != "serif":
                problems.append("style " + edits._style_of(first) + " " + os.path.basename(first))
            if os.path.basename(first) != "CrimsonText-Regular.ttf":
                problems.append("face " + os.path.basename(first))
    finally:
        if old_win is None:
            os.environ.pop("WINDIR", None)
        else:
            os.environ["WINDIR"] = old_win
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local
        if old_fc is None:
            os.environ.pop("FONTCONFIG_FILE", None)
        else:
            os.environ["FONTCONFIG_FILE"] = old_fc
        if old_fc_path is None:
            os.environ.pop("FONTCONFIG_PATH", None)
        else:
            os.environ["FONTCONFIG_PATH"] = old_fc_path
        edits._FONT_INDEX = old_index
        shutil.rmtree(folder, ignore_errors=True)
    _record("bundled-fonts-only", not problems, "; ".join(problems) or "system fonts hidden, same bundled face twice")


def _bundled_space_stays_a_space() -> None:
    """Lato used to store a normal space as a non-breaking space. 12 OCT must read back as 12 OCT."""
    import pymupdf

    path = os.path.join(os.path.dirname(__file__), "fonts", "Lato-Regular.ttf")
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=80)
    page.insert_font(fontname="LATO", fontfile=path)
    page.insert_text((20, 50), "12 OCT", fontsize=18, fontname="LATO", fontfile=path)
    chars = "".join(chr(item[0]) for item in (page.get_texttrace() or [{}])[0].get("chars") or [])
    doc.close()
    _record("bundled-space", chars == "12 OCT", repr(chars))


def _windows_ocr_shape() -> None:
    """A numpy box must not be thrown away by a truth-value check."""
    import numpy as np
    from artwork_edits import _words_from_rows
    from ocr_reader import rows_from_output

    class Output:
        txts = ["073 703 0766", "12 March"]
        boxes = np.array([
            [[10, 20], [80, 20], [80, 40], [10, 40]],
            [[4, 50], [90, 50], [90, 70], [4, 70]],
        ], dtype=np.float32)
        scores = np.array([0.91, 0.82], dtype=np.float32)

    rows = rows_from_output((Output(), [0.2]))
    words = _words_from_rows(rows)
    flat = rows_from_output([[np.array([4.0, 8.0, 40.0, 22.0]), "Saturday", 0.5]])
    raw = _words_from_rows([[np.array([[1, 2], [30, 2], [30, 18], [1, 18]], dtype=np.float32), "082 123 4567", np.float32(0.99)]])
    problems = []
    texts = [item["text"] for item in words]
    if texts != ["073 703 0766", "12 March"]:
        problems.append("rows " + repr(texts))
    if not flat or flat[0][1] != "Saturday":
        problems.append("flat " + repr(flat))
    if not raw or raw[0]["text"] != "082 123 4567" or raw[0]["width"] < 2:
        problems.append("raw " + repr(raw))
    _record("windows-ocr-shape", not problems, "; ".join(problems) or "numpy boxes kept")


def _phone_grouping() -> None:
    """An odd 3-4-3 split keeps the digits Ian typed. Hyphens and 3-3-4 stay."""
    from edit_style import swap_text

    problems = []
    _found, styled, full = swap_text("PHONE: 067 1345 937", "phone", "082 123 4567")
    if styled != "082 123 4567" or full != "PHONE: 082 123 4567":
        problems.append(f"typed {styled!r} {full!r}")
    _found, hyphen, _full = swap_text("072-971-4247", "phone", "082 123 4567")
    if hyphen != "082-123-4567":
        problems.append(f"hyphen {hyphen!r}")
    _found, local, _full = swap_text("073 703 0766", "phone", "082 123 4567")
    if local != "082 123 4567":
        problems.append(f"local {local!r}")
    _record("phone-grouping", not problems, "; ".join(problems) or "082 123 4567")


def _no_helvetica() -> None:
    """Base Helvetica is not a file, so it is never inserted."""
    import pymupdf as fitz
    from artwork_edits import _install_face

    doc = fitz.open()
    page = doc.new_page(width=200, height=80)
    raised = False
    try:
        _install_face(page, "helv")
    except ValueError:
        raised = True
    finally:
        doc.close()
    _record("no-helvetica", raised, "base-14 refused" if raised else "helvetica installed")


def _baked_contact() -> None:
    """CONTACT is painted into the picture and set as live text. The phone edit leaves the heading."""
    import pymupdf as fitz
    from artwork_edits import propose

    anton = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts", "Anton-Regular.ttf"))
    plate = fitz.open()
    drawn = plate.new_page(width=360, height=200)
    drawn.insert_font(fontname="ANTON", fontfile=anton)
    drawn.insert_text((80, 70), "CONTACT", fontsize=28, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    drawn.insert_text((70, 108), "PHONE: 067 1345 937", fontsize=14, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    pix = drawn.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
    png = os.path.join(ROOT, "baked-contact.png")
    pix.save(png)
    plate.close()

    src = os.path.join(ROOT, "baked-contact.pdf")
    doc = fitz.open()
    page = doc.new_page(width=360, height=200)
    page.insert_image(page.rect, filename=png)
    page.insert_font(fontname="ANTON", fontfile=anton)
    page.insert_text((80, 70), "CONTACT", fontsize=28, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    page.insert_text((70, 108), "PHONE: 067 1345 937", fontsize=14, fontname="ANTON", fontfile=anton, color=(0, 0, 0))
    doc.save(src)
    doc.close()

    preview = os.path.join(ROOT, "preview-baked-contact")
    asked = propose(src, "change the phone number to 082 123 4567", "baked-contact", preview)
    reply = (asked or {}).get("reply") or ""
    TRANSCRIPT.append(f"## baked-contact\n{reply}\n")
    staged = os.path.join(preview, "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    problems = []
    if "Anton Regular" not in reply:
        problems.append(reply[:180] or "no face name")
    if "CONTACT" not in text or "082 123 4567" not in text or "067 1345 937" in text or "082 1234 567" in text:
        problems.append(text.replace("\n", " ")[:160] or "text")
    gate, gate_problems = _visual(src, preview, "baked-contact")
    problems.extend(gate_problems)
    if os.path.isfile(os.path.join(preview, "after.png")):
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(os.path.join(preview, "before.png"), os.path.join(ART, "baked-contact-before.png"))
        shutil.copyfile(os.path.join(preview, "after.png"), os.path.join(ART, "baked-contact-after.png"))
    _record("baked-contact", not problems and bool(asked), "; ".join(problems) or f"heading kept, phone retyped {gate}")


def _exactly_once() -> None:
    """A bare request applies only when that field is on the page once."""
    import pymupdf as fitz
    from artwork_edits import propose

    src = os.path.join(ROOT, "once.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=280)
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    black = (0, 0, 0)
    page.insert_text((36, 50), "10:00AM", fontsize=16, fontname="DEJAVU", fontfile=FONT, color=black)
    page.insert_text((36, 90), "5:00PM", fontsize=16, fontname="DEJAVU", fontfile=FONT, color=black)
    page.insert_text((36, 140), "CHURCH IS NOT ABOUT", fontsize=16, fontname="DEJAVU", fontfile=FONT, color=black)
    page.insert_text((36, 190), "18 BELLAVISTA ROAD HADDON", fontsize=16, fontname="DEJAVU", fontfile=FONT, color=black)
    page.insert_text((36, 240), "Keep me", fontsize=14, fontname="DEJAVU", fontfile=FONT, color=black)
    doc.save(src)
    doc.close()

    problems = []
    bare = propose(src, "change the time to 7:30 pm", "once-time", os.path.join(ROOT, "preview-once-time"))
    bare_reply = ((bare or {}).get("reply") or "").lower()
    if (bare or {}).get("pending") is True or "more than one" not in bare_reply or "safely change" not in bare_reply:
        problems.append("bare " + ((bare or {}).get("reply") or "")[:160])
    named = propose(src, "change the time from 10:00AM to 7:30PM", "once-named", os.path.join(ROOT, "preview-once-named"))
    staged = os.path.join(ROOT, "preview-once-named", "staged.pdf")
    body = _text(staged) if os.path.isfile(staged) else ""
    if (named or {}).get("pending") is not True or "7:30" not in body or "5:00PM" not in body or "Keep me" not in body:
        problems.append("named " + body.replace("\n", " ")[:120])
    venue = propose(src, "change the venue to Community Centre", "once-venue", os.path.join(ROOT, "preview-once-venue"))
    vstaged = os.path.join(ROOT, "preview-once-venue", "staged.pdf")
    vbody = _text(vstaged) if os.path.isfile(vstaged) else ""
    if (venue or {}).get("pending") is not True or "COMMUNITY CENTRE" not in vbody or "CHURCH IS NOT ABOUT" not in vbody:
        problems.append("venue " + ((venue or {}).get("reply") or "")[:120])
    if "BELLAVISTA" in vbody:
        problems.append("old venue left")
    _record("exactly-once", not problems, "; ".join(problems) or "named time applied, bare time refused, slogan kept")


def _write() -> None:
    os.makedirs(ART, exist_ok=True)
    lines = ["case\tresult\tdetail"]
    for row in ROWS:
        lines.append(f"{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}")
    with open(os.path.join(ART, "table.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with open(os.path.join(ART, "transcript.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(TRANSCRIPT))


def _natural_and_vague() -> None:
    """Dates and times in ordinary sentences, and a vague ask that changes nothing."""
    from artwork_edits import _incomplete_edit, _parse, propose

    phrases = [
        ("change 9 OCT to 12 OCT", "date", "9OCT", "12 OCT"),
        ("change the date from 9 OCT to 12 October 2026", "date", "9OCT", "12 October 2026"),
        ("from 9 OCT to 12 October 2026", "date", "9OCT", "12 October 2026"),
        ("change the date to 12 October 2026", "date", "", "12 October 2026"),
        ("change 10:00AM to 7:30PM", "time", "1000AM", "7:30PM"),
        ("change the time from 10:00AM to 7:30 pm", "time", "1000AM", "7:30 pm"),
        ("change 5/3/26 to 12/10/2026", "date", "53", "12/10/2026"),
        ("please change October 9 to 12 October 2026", "date", "OCTOBER9", "12 October 2026"),
    ]
    for message, kind, hint, new in phrases:
        parsed = _parse(message) or {}
        got_hint = re.sub(r"[^A-Z0-9]", "", str(parsed.get("oldHint") or "").upper())
        ok = parsed.get("kind") == kind and new.lower() in str(parsed.get("new") or "").lower()
        if hint and hint not in got_hint:
            ok = False
        _record("phrase-" + kind + "-" + re.sub(r"[^a-z0-9]+", "-", message.lower())[:32], ok, str(parsed)[:140])
    for message in ("change it", "make it better", "change the phone number", "change the venue to"):
        reply = str((propose("/no/such/file.pdf", message, "vague", ROOT) or {}).get("reply") or "")
        low = reply.lower()
        ok = "nothing was changed" in low and "everything else" not in low and reply.endswith(".")
        _record("vague-" + re.sub(r"[^a-z0-9]+", "-", message.lower())[:24], ok, reply[:160])
        if _incomplete_edit(message) is None:
            _record("vague-parse-" + message[:12], False, "not incomplete")


def _ocr_miss_keeps_the_edit() -> None:
    """A preview whose new words are real text still applies when OCR cannot read them."""
    import artwork_edits as edits

    src = os.path.join(ROOT, "ocr-miss.pdf")
    _live(src, "Doors 9 OCT")
    real_tesseract = edits._tesseract
    real_rapid = edits._rapid_line
    edits._tesseract = lambda *_args, **_kwargs: ""
    edits._rapid_line = lambda *_args, **_kwargs: ""
    try:
        asked = edits.propose(src, "change the date from 9 OCT to 12 October 2026", "ocr-miss", os.path.join(ROOT, "preview-ocr-miss"))
    finally:
        edits._tesseract = real_tesseract
        edits._rapid_line = real_rapid
    reply = (asked or {}).get("reply") or ""
    staged = os.path.join(ROOT, "preview-ocr-miss", "staged.pdf")
    body = _text(staged) if os.path.isfile(staged) else ""
    ok = asked.get("pending") is True and "12 OCT" in body and "could not read" not in reply.lower()
    outside = ""
    if ok:
        import numpy as np

        before = edits._render_rgb(src, 1)
        after = edits._render_rgb(staged, 1)
        box = (edits._load("ocr-miss").get("proposal") or {}).get("editBox") or [0, 0, 0, 0]
        delta = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2)
        y0, y1 = int(box[1]), int(box[3]) + 1
        x0, x1 = int(box[0]), int(box[2]) + 1
        outside_n = int((delta > 12).sum()) - int((delta[y0:y1, x0:x1] > 12).sum())
        outside = f" outside {outside_n}"
        if outside_n > 40:
            ok = False
    _record("ocr-miss-date", ok, (reply[:120] or body[:80]) + outside)


def _font_weight() -> None:
    """Cooper Black must not replace a regular phone face."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    import artwork_edits as edits

    regular = FONT
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    folder = os.path.join(ROOT, "win-fonts")
    os.makedirs(folder, exist_ok=True)
    regular_path = os.path.join(folder, "arial.ttf")
    heavy_path = os.path.join(folder, "CooperBlack.ttf")
    shutil.copyfile(regular, regular_path)
    shutil.copyfile(bold, heavy_path)
    face = ImageFont.truetype(regular, 48)
    canvas = Image.new("RGB", (520, 80), (255, 255, 255))
    ImageDraw.Draw(canvas).text((6, 8), "082 123 4567", font=face, fill=(20, 20, 20))
    crop = np.array(canvas)[:, :, ::-1]
    previous = edits._FONT_INDEX
    edits._FONT_INDEX = [
        {"path": regular_path, "kind": "bundled", "family": "arial", "style": "regular"},
        {"path": heavy_path, "kind": "bundled", "family": "cooper", "style": "black"},
    ]
    try:
        path, score = edits._closest_font(crop, "082 123 4567")
        both = os.path.basename(path).lower().startswith("arial") and score >= 0
        edits._FONT_INDEX = [
            {"path": heavy_path, "kind": "bundled", "family": "cooper", "style": "black"},
        ]
        refused, refused_score = edits._closest_font(crop, "082 123 4567")
        only = refused == "" and refused_score < 0
        serif_src = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
        sans_path = os.path.join(folder, "bahnschrift.ttf")
        serif_path = os.path.join(folder, "times.ttf")
        shutil.copyfile(regular, sans_path)
        shutil.copyfile(serif_src, serif_path)
        serif_face = ImageFont.truetype(serif_src, 48)
        serif_plate = Image.new("RGB", (520, 90), (255, 255, 255))
        ImageDraw.Draw(serif_plate).text((6, 8), "082 123 4567", font=serif_face, fill=(20, 20, 20))
        serif_crop = np.array(serif_plate)[:, :, ::-1]
        edits._FONT_INDEX = [
            {"path": sans_path, "kind": "bundled", "family": "bahnschrift", "style": "regular"},
            {"path": serif_path, "kind": "bundled", "family": "timesnewroman", "style": "regular"},
        ]
        chosen, chosen_score = edits._closest_font(serif_crop, "082 123 4567")
        serif_won = os.path.basename(chosen).lower().startswith("times") and chosen_score >= 0
    finally:
        edits._FONT_INDEX = previous
    _record("font-not-cooper", both and only, f"{os.path.basename(path)} {score:.1f} only {refused_score}")
    _record("font-serif-not-bahnschrift", serif_won, f"{os.path.basename(chosen)} {chosen_score:.1f}")


def _picture_pdf(path: str, image) -> None:
    import pymupdf as fitz

    png = path + ".png"
    image.save(png)
    doc = fitz.open()
    page = doc.new_page(width=image.size[0] / 2.0, height=image.size[1] / 2.0)
    page.insert_image(page.rect, filename=png)
    doc.save(path)
    doc.close()


def _outside_pixels(before: str, after: str, box: list) -> int:
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


def _missing_named_date() -> None:
    """9 OCT is not on a poster that has 10 OCT and 11 OCT. It must not become 11 OCT."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import SAFE_EDIT, _load, propose

    font = ImageFont.truetype("/workspace/server/fonts/Poppins-Bold.ttf", 64)
    small = ImageFont.truetype(FONT, 28)
    plate = Image.new("RGB", (980, 420), (248, 244, 236))
    draw = ImageDraw.Draw(plate)
    draw.text((700, 36), "SUNDAY", font=small, fill=(20, 20, 20))
    draw.text((80, 70), "10", font=font, fill=(20, 20, 20))
    draw.text((80, 160), "OCT", font=font, fill=(20, 20, 20))
    draw.text((360, 70), "11", font=font, fill=(20, 20, 20))
    draw.text((360, 160), "OCT", font=font, fill=(20, 20, 20))
    src = os.path.join(ROOT, "dates-10-11.pdf")
    _picture_pdf(src, plate)
    original = _sha(src)
    for message, stem in (
        ("change the date from 9 OCT to 12 October 2026", "missing-from"),
        ("change 9 OCT to 12 OCT", "missing-bare"),
    ):
        asked = propose(src, message, stem, os.path.join(ROOT, "preview-" + stem)) or {}
        reply = asked.get("reply") or ""
        low = reply.lower()
        staged = os.path.join(ROOT, "preview-" + stem, "staged.pdf")
        body = _text(staged) if os.path.isfile(staged) else ""
        ok = (
            asked.get("pending") is not True
            and "9 oct" in low
            and "10 oct" in low
            and "11 oct" in low
            and "nothing was changed" in low
            and "everything else" not in low
            and "from 11 oct" not in low
            and "12 oct" not in body.lower()
            and _sha(src) == original
        )
        _record("missing-" + stem, ok, reply[:180])
    flat = os.path.join(ROOT, "date-11-flat.pdf")
    one = Image.new("RGB", (640, 280), (248, 244, 236))
    pen = ImageDraw.Draw(one)
    pen.text((70, 40), "11", font=font, fill=(20, 20, 20))
    pen.text((70, 130), "OCT", font=font, fill=(20, 20, 20))
    _picture_pdf(flat, one)
    asked = propose(flat, "change the date from 11 OCT to 12 October 2026", "flat-11", os.path.join(ROOT, "preview-flat-11")) or {}
    staged = os.path.join(ROOT, "preview-flat-11", "staged.pdf")
    body = _text(staged) if os.path.isfile(staged) else ""
    box = (_load("flat-11").get("proposal") or {}).get("editBox")
    outside = _outside_pixels(flat, staged, box) if asked.get("pending") and box else 999999
    face = str((_load("flat-11").get("proposal") or {}).get("faceLabel") or (_load("flat-11").get("proposal") or {}).get("fontfile") or "")
    flat_body = " ".join(body.split())
    clean = (
        asked.get("pending") is True
        and "12 OCT" in flat_body
        and "11 OCT" not in flat_body
        and outside == 0
        and "jokerman" not in face.lower()
        and "could not read" not in (asked.get("reply") or "").lower()
    )
    _record("named-11-oct-clean", clean, f"outside {outside} {face} {(asked.get('reply') or '')[:100]}")
    noise = np.random.default_rng(3).integers(80, 220, size=(360, 720, 3), dtype=np.uint8)
    noise[:, :, 0] = np.clip(noise[:, :, 0].astype(np.int16) + 40, 0, 255)
    photo = Image.fromarray(noise, "RGB")
    pen = ImageDraw.Draw(photo)
    pen.text((80, 40), "11", font=font, fill=(255, 255, 255))
    pen.text((80, 140), "OCT", font=font, fill=(255, 255, 255))
    busy = os.path.join(ROOT, "date-11-photo.pdf")
    _picture_pdf(busy, photo)
    before = _sha(busy)
    asked = propose(busy, "change 11 OCT to 12 OCT", "photo-11", os.path.join(ROOT, "preview-photo-11")) or {}
    reply = asked.get("reply") or ""
    refused = asked.get("pending") is not True and reply.strip() == SAFE_EDIT and _sha(busy) == before
    _record("photo-date-refused", refused, reply[:160])


def _serif_phone_keeps_size() -> None:
    """A serif phone stays a serif, and a number that started large does not fall under 7 pt."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont
    from artwork_edits import _load, _style_of, propose
    from extra_checks import check_e5

    serif = os.path.join(os.path.dirname(__file__), "fonts", "v2", "CrimsonText-Regular.ttf")
    face = ImageFont.truetype(serif, 48)
    plate = Image.new("RGB", (980, 240), (255, 255, 255))
    ImageDraw.Draw(plate).text((48, 80), "073 703 0766", font=face, fill=(12, 12, 12))
    src = os.path.join(ROOT, "serif-phone.pdf")
    _picture_pdf(src, plate)
    asked = propose(src, "change the phone number to 082 123 4567", "serif-phone", os.path.join(ROOT, "preview-serif-phone")) or {}
    staged = os.path.join(ROOT, "preview-serif-phone", "staged.pdf")
    size = 0.0
    font = ""
    if os.path.isfile(staged):
        doc = fitz.open(staged)
        try:
            for block in (doc[0].get_text("dict") or {}).get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        if "082" in str(span.get("text") or ""):
                            size = float(span.get("size") or 0)
                            font = str(span.get("font") or "")
        finally:
            doc.close()
    row = check_e5(staged) if os.path.isfile(staged) else {}
    reply = asked.get("reply") or ""
    fontfile = str((_load("serif-phone").get("proposal") or {}).get("fontfile") or "")
    ok = (
        asked.get("pending") is True
        and size >= 7.0
        and _style_of(fontfile) == "serif"
        and fontfile.startswith(os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts")))
        and "bahnschrift" not in fontfile.lower()
        and "/usr/share/fonts" not in fontfile
        and row.get("status") != "failed"
        and "5.9" not in str(row.get("detail") or "")
    )
    _record("serif-phone-size", ok, f"{font} {size:.1f}pt e5={row.get('status')} {reply[:100]}")


def _is_this_right() -> None:
    """The short question runs the same stored check, including low resolution."""
    src = os.path.join(ROOT, "right.pdf")
    _live(src, "Keep me")
    stored = os.path.join(ROOT, "stored-checks.json")
    with open(stored, "w", encoding="utf-8") as handle:
        json.dump({
            "checks": [
                {"num": "6", "name": "Resolution", "status": "failed", "detail": "The picture is about 72 ppi. It needs about 300 ppi."},
                {"num": "1", "name": "Bleed", "status": "passed", "detail": "Bleed is 5 mm."},
            ]
        }, handle)
    replies = {}
    for message in ("is this right", "is this artwork right?"):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--input", src, "--stored", stored, "--message", message, "--job-id", "right"],
            capture_output=True, text=True, cwd=os.path.dirname(SCRIPT),
        )
        replies[message] = proc.stdout
        low = proc.stdout.lower()
        ok = "72 ppi" in low and "everything else" in low and proc.returncode == 0
        _record("check-" + re.sub(r"[^a-z]+", "-", message.lower()).strip("-"), ok, proc.stdout.replace("\n", " ")[:180])
    same = "72 ppi" in replies["is this right"] and "72 ppi" in replies["is this artwork right?"]
    _record("check-same-problems", same, "both list the low resolution")


def run() -> list[str]:
    _windows_font_paths()
    _bundled_space_stays_a_space()
    _windows_ocr_shape()
    _phone_grouping()
    _no_helvetica()
    _natural_and_vague()
    _font_weight()
    date = "change the date to 12 October 2026"
    _flow("date-bare", "Doors 9 OCT", "change 9 OCT to 12 OCT", "12 OCT", "9 OCT")
    _flow("date-slash", "Doors 5/3", date, "12/10", "5/3")
    _flow("date-slash-year", "Doors 05/03/25", date, "12/10/26", "05/03/25")
    _flow("date-month", "Doors 5 March", date, "12 October", "5 March")
    _flow("date-month-year", "Doors 5 March 2025", date, "12 October 2026", "2025")
    _flow("date-abbr", "Doors 5 Mar", date, "12 Oct", "5 Mar")
    _flow("date-weekday", "Saturday 5 March", date, "Monday 12 October", "Saturday")
    _flow("date-caps", "SAT 5 MAR", date, "MON 12 OCT", "SAT")
    _flow("date-mdy", "October 5, 2025", date, "October 12, 2026", "October 5")
    phone = "change the phone number to 082 123 4567"
    _flow("phone-spaces", "Call 073 703 0766", phone, "082 123 4567", "073 703 0766")
    _flow("phone-empty", "Call () 073 703 0766", phone, "082 123 4567", "()")
    _phone_icon()
    _subset_font()
    _full_font()
    _subset_gap()
    _subset_closest()
    _flow("phone-hyphen", "Call 072-971-4247", phone, "082-123-4567", "072-971-4247")
    _flow("phone-parens", "Call (073) 703 0766", phone, "(082) 123 4567", "073")
    time = "change the time to 7:30 pm"
    _flow("time-ampm", "Starts 6PM", time, "7:30PM", "6PM")
    _flow("time-clock", "Starts 6:00 pm", time, "7:30 pm", "6:00")
    _flow("time-24h", "Starts 18:00", time, "19:30", "18:00")
    venue = "change the venue to Community Centre"
    _flow("venue-street", "8 Waterbok Street", venue, "Community Centre", "Waterbok")
    _flow("venue-caps", "CITY HALL", venue, "COMMUNITY CENTRE", "CITY HALL")
    _flow("venue-lower", "community hall", venue, "community centre", "community hall")
    _raster_date()
    _baked_contact()
    _overlap_time()
    _shrink_venue()
    _exactly_once()
    _ocr_miss_keeps_the_edit()
    _missing_named_date()
    _serif_phone_keeps_size()
    _is_this_right()
    _cat_yes()
    _write()
    return list(FAILURES)


def main() -> None:
    failed = run()
    print(f"small edit flow {len(ROWS) - len(failed)} pass, {len(failed)} fail")
    if failed:
        raise SystemExit(f"{len(failed)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
