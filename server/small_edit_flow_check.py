#!/usr/bin/env python3
"""Ian's small-edit flow: he types the new value, the cat keeps the old format.

This is the acceptance test for a date, a phone number, a time, and a venue.
The preview is shown first. The file changes only after yes, and undo puts it back.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    _record(name, not problems, "; ".join(problems) or f"{expect} {gate}")


def _full_font() -> None:
    """A subset tag resolves to the bundled face, and Windows fonts are on the search path."""
    from fontTools.ttLib import TTFont

    from artwork_edits import _glyphs_present, _named_font, _windows_font_dirs

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
    windows = any(
        folder.endswith(os.path.join("Windows", "Fonts")) or folder.endswith("\\Fonts")
        for folder in _windows_font_dirs()
    )
    if not windows:
        problems.append("windows fonts path")
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
    if "082 1234 567" not in text or "067 1345 937" in text:
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
    if "082 1234 567" not in text:
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
    if _sha(src) != original:
        problems.append("source changed")
    undone = ask("undo")
    if "Saturday 5 March" not in _text(dest):
        problems.append("undo " + _text(dest).replace("\n", " ")[:80])
    if "25-point" not in (undone.get("reply") or ""):
        problems.append("undo checks")
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
    asked = propose(src, "change the time to 7:30 pm", "overlap-time", preview)
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


def _write() -> None:
    os.makedirs(ART, exist_ok=True)
    lines = ["case\tresult\tdetail"]
    for row in ROWS:
        lines.append(f"{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}")
    with open(os.path.join(ART, "table.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with open(os.path.join(ART, "transcript.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(TRANSCRIPT))


def run() -> list[str]:
    date = "change the date to 12 October 2026"
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
    _overlap_time()
    _shrink_venue()
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
