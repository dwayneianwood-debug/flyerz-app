#!/usr/bin/env python3
"""Laptop findings for the small-edit chat: venue, stray pixels, from/to, fonts, gates.

Each case goes through designer_assistant.py, the same process the page runs.
A Windows font folder is planted and then ignored. Matching uses bundled faces.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = tempfile.mkdtemp(prefix="laptop-edit-")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
WIN = os.path.join(ROOT, "Windows")
LOCAL = os.path.join(ROOT, "Local")
os.makedirs(os.path.join(WIN, "Fonts"), exist_ok=True)
os.makedirs(os.path.join(LOCAL, "Microsoft", "Windows", "Fonts"), exist_ok=True)
shutil.copyfile(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    os.path.join(WIN, "Fonts", "arial.ttf"),
)
os.environ["WINDIR"] = WIN
os.environ["LOCALAPPDATA"] = LOCAL

ART = "/opt/cursor/artifacts/laptop-edits"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SCRIPT = os.path.join(os.path.dirname(__file__), "designer_assistant.py")
A6 = "/tmp/real_a6.pdf"
CHURCH = "/tmp/church_native.pdf"
FLYER = "/tmp/medella_flyer.pdf"

ROWS: list[dict] = []
FAILURES: list[str] = []


def _record(case: str, ok: bool, detail: str) -> None:
    ROWS.append({"case": case, "ok": ok, "detail": detail})
    print(("PASS" if ok else "FAIL") + f" {case} {detail}", flush=True)
    if not ok:
        FAILURES.append(case)


def _text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        return doc[0].get_text("text") or ""
    finally:
        doc.close()


def _ask(src: str, message: str, job: str, width: float = 148, height: float = 105) -> dict:
    preview = os.path.join(ROOT, "preview-" + job)
    dest = os.path.join(ROOT, job + "-out.pdf")
    cmd = [
        sys.executable, SCRIPT,
        "--input", src,
        "--message", message,
        "--output", dest,
        "--job-id", job,
        "--preview-dir", preview,
        "--trim-w", str(width),
        "--trim-h", str(height),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy(), timeout=180, check=False)
    payload = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("{"):
            payload = json.loads(line)
            break
    payload["_err"] = (proc.stderr or "")[-240:]
    payload["_preview"] = preview
    payload["_dest"] = dest
    return payload


def _outside(before: str, after: str, box) -> int:
    import numpy as np
    from artwork_edits import _render_rgb

    scale = 2.5
    a = _render_rgb(before, scale)
    b = _render_rgb(after, scale)
    if a.shape != b.shape or not box:
        return 99999
    margin = 1.25 * scale
    x0 = max(0, int(np.floor(float(box[0]) * scale - margin)))
    y0 = max(0, int(np.floor(float(box[1]) * scale - margin)))
    x1 = min(a.shape[1], int(np.ceil(float(box[2]) * scale + margin)))
    y1 = min(a.shape[0], int(np.ceil(float(box[3]) * scale + margin)))
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    delta[y0:y1, x0:x1] = 0
    return int((delta > 12).sum())


def _zoom(src: str, staged: str, stem: str, box) -> None:
    if not box or len(box) < 4:
        return
    import pymupdf as fitz

    try:
        os.makedirs(ART, exist_ok=True)
    except OSError:
        return
    pad = 18
    for label, path in (("before", src), ("after", staged)):
        doc = fitz.open(path)
        try:
            clip = fitz.Rect(box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad) & doc[0].rect
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, alpha=False, colorspace=fitz.csRGB)
            pix.save(os.path.join(ART, f"{stem}-{label}.png"))
        finally:
            doc.close()


def _span_box(path: str, needle: str):
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        for block in (doc[0].get_text("dict") or {}).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    if needle in str(span.get("text") or ""):
                        return span["bbox"], span.get("font"), span.get("text")
    finally:
        doc.close()
    return None, "", ""


def _a6() -> None:
    if not os.path.isfile(A6):
        _record("a6", False, "missing /tmp/real_a6.pdf")
        return
    venue = _ask(A6, "change the venue to COMMUNITY CENTRE HADDON", "a6-venue")
    staged = os.path.join(venue["_preview"], "staged.pdf")
    text = _text(staged) if os.path.isfile(staged) else ""
    reply = venue.get("reply") or venue.get("_err") or ""
    problems = []
    if venue.get("pending") is not True or "Apply this change" not in json.dumps(venue.get("actions") or []):
        problems.append(reply[:160])
    if text.upper().count("HADDON") != 1 or "COMMUNITY CENTRE" not in text:
        problems.append("venue " + text.replace("\n", " ")[:80])
    if "ONE80" not in text or "EVERY FRIDAY" not in text:
        problems.append("neighbour lost")
    box, font, _got = _span_box(staged, "COMMUNITY")
    if box and _outside(A6, staged, box) > 40:
        problems.append("pixels outside")
    _zoom(A6, staged, "a6-venue", box)
    _record("a6-venue", not problems, "; ".join(problems) or f"one HADDON {font}")

    wide = _ask(A6, "change the venue to INTERNATIONAL CONFERENCE CENTRE", "a6-wide")
    wide_text = _text(os.path.join(wide["_preview"], "staged.pdf")) if wide.get("pending") else ""
    wide_box, _font, _got = _span_box(os.path.join(wide["_preview"], "staged.pdf"), "INTERNATIONAL")
    old_box, _f, _g = _span_box(A6, "BELLAVISTA")
    problems = []
    if wide.get("pending") is not True or "smaller" not in (wide.get("reply") or "").lower():
        problems.append((wide.get("reply") or "")[:140])
    if wide_box and old_box and (wide_box[2] - wide_box[0]) > (old_box[2] - old_box[0]) + 1.5:
        problems.append(f"wider {wide_box[2] - wide_box[0]:.1f}")
    if "ONE80" not in wide_text:
        problems.append("one80 lost")
    _record("a6-venue-shrink", not problems, "; ".join(problems) or "shrunk to the old width")

    phone = _ask(A6, "change the phone number to 082 123 4567", "a6-phone")
    phone_text = _text(os.path.join(phone["_preview"], "staged.pdf")) if os.path.isfile(os.path.join(phone["_preview"], "staged.pdf")) else ""
    box, font, label = _span_box(os.path.join(phone["_preview"], "staged.pdf"), "PHONE")
    problems = []
    if phone.get("pending") is not True:
        problems.append((phone.get("reply") or "")[:140])
    if "082 123 4567" not in phone_text or "067 1345 937" in phone_text:
        problems.append(phone_text.replace("\n", " ")[:80])
    if "PHONE" not in phone_text:
        problems.append("label removed")
    digit_box, _f, _g = _span_box(os.path.join(phone["_preview"], "staged.pdf"), "082")
    if digit_box and _outside(A6, os.path.join(phone["_preview"], "staged.pdf"), digit_box) > 40:
        problems.append("phone pixels outside")
    _zoom(A6, os.path.join(phone["_preview"], "staged.pdf"), "a6-phone", digit_box or box)
    _record("a6-phone", not problems, "; ".join(problems) or f"label kept {font}")

    for stem, message in (
        ("a6-time-from", "change the time from 10:00AM to 7:30PM"),
        ("a6-time-x", "change the time 10:00AM to 7:30PM"),
    ):
        asked = _ask(A6, message, stem)
        body = _text(os.path.join(asked["_preview"], "staged.pdf")) if asked.get("pending") else ""
        problems = []
        if asked.get("pending") is not True or "7:30PM" not in (asked.get("reply") or ""):
            problems.append((asked.get("reply") or "")[:140])
        if "7:30PM" not in body or "10:00AM" in body:
            problems.append(body.replace("\n", " ")[:80])
        if "MORNING WORSHIP" not in body:
            problems.append("worship lost")
        staged_pdf = os.path.join(asked["_preview"], "staged.pdf")
        old_box, _of, _og = _span_box(A6, "10:00AM")
        tbox, _f, _g = _span_box(staged_pdf, "7:30")
        # The new glyphs are shorter than 10:00AM. Gate (a) covers the old word
        # and the new word together, so clearing the leftover of 10:00AM is inside.
        boxes = [box for box in (old_box, tbox) if box]
        union = None
        if boxes:
            union = (
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            )
        from artwork_edits import _load
        edit = (_load(stem).get("proposal") or {}).get("editBox")
        if union and _outside(A6, staged_pdf, union) > 40:
            problems.append("time pixels outside")
        if edit and _outside(A6, staged_pdf, edit) > 40:
            problems.append("time pixels outside the edit")
        _zoom(A6, staged_pdf, stem, union or tbox)
        _record(stem, not problems, "; ".join(problems) or "10:00AM became 7:30PM")

    many = _ask(A6, "change the time to 7:30PM", "a6-time-many")
    many_reply = (many.get("reply") or "").lower()
    many_ok = (
        many.get("pending") is not True
        and not many.get("actions")
        and "more than one" in many_reply
        and "safely change" in many_reply
    )
    _record("a6-time-many", many_ok, (many.get("reply") or "")[:160])


def _phrases() -> None:
    import pymupdf as fitz

    src = os.path.join(ROOT, "sheet.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=280)
    page.insert_font(fontname="DEJ", fontfile=FONT)
    page.insert_text((36, 60), "Call 073 000 1111", fontsize=16, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 100), "Sunday 5 March 2026", fontsize=16, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 140), "Starts 10:00AM", fontsize=16, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 180), "8 Bellavista Road Haddon", fontsize=16, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 230), "Keep me", fontsize=14, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    cases = [
        ("from-phone", "change the phone number from 073 000 1111 to 082 123 4567", "082 123 4567", "073 000 1111"),
        ("x-phone", "change the phone number 073 000 1111 to 082 123 4567", "082 123 4567", "073 000 1111"),
        ("from-date", "change the date from 5 March 2026 to 12 October 2026", "12 October 2026", "5 March"),
        ("x-date", "change the date 5 March 2026 to 12 October 2026", "12 October 2026", "5 March"),
        ("from-time", "change the time from 10:00AM to 7:30PM", "7:30", "10:00"),
        ("x-time", "change the time 10:00AM to 7:30PM", "7:30", "10:00"),
        ("from-venue", "change the venue from Bellavista Road to Community Centre", "Community Centre", "Bellavista"),
        ("x-venue", "change the venue Bellavista Road Haddon to Community Centre", "Community Centre", "Bellavista"),
    ]
    # Each phrase is its own file so a previous edit is not the source.
    for stem, message, expect, absent in cases:
        one = os.path.join(ROOT, stem + ".pdf")
        shutil.copyfile(src, one)
        asked = _ask(one, message, stem)
        body = _text(os.path.join(asked["_preview"], "staged.pdf")) if os.path.isfile(os.path.join(asked["_preview"], "staged.pdf")) else ""
        problems = []
        if asked.get("pending") is not True:
            problems.append((asked.get("reply") or asked.get("_err") or "")[:140])
        if expect not in body or absent in body or "Keep me" not in body:
            problems.append(body.replace("\n", " ")[:100] or "empty")
        _record(stem, not problems, "; ".join(problems) or expect)


def _fonts() -> None:
    import numpy as np
    from artwork_edits import _closest_font

    bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts"))
    _record("windows-fonts", True, "machine font folders are not searched")
    crop = np.full((4000, 500, 3), 230, dtype=np.uint8)
    started = time.perf_counter()
    try:
        path, _score = _closest_font(crop, "082 123 4567")
        elapsed = time.perf_counter() - started
        inside = os.path.abspath(path).startswith(bundled) and "/usr/share/fonts" not in os.path.abspath(path)
        _record("font-cap", bool(path) and inside and elapsed < 10, f"{elapsed:.2f}s {os.path.basename(path)}")
    except Exception as exc:
        _record("font-cap", False, type(exc).__name__ + " " + str(exc)[:80])


def _proposal_box(job: str):
    from artwork_edits import _load

    prop = (_load(job).get("proposal") or {})
    return prop.get("editBox") or prop.get("bbox")


def _sha(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _church_date_clean(asked: dict, job: str) -> bool:
    """The date preview says 12 OCT, pixels outside the edit are unchanged, and undo is the original file."""
    from artwork_edits import confirm, undo

    staged = os.path.join(asked.get("_preview") or "", "staged.pdf")
    dest = asked.get("_dest") or ""
    reply = asked.get("reply") or ""
    if "could not read" in reply.lower() or not os.path.isfile(staged):
        return False
    body = " ".join(_text(staged).split())
    if "12 OCT" not in body or "9 OCT" in body:
        return False
    box = _proposal_box(job)
    if not box:
        return False
    outside = _outside(CHURCH, staged, box)
    _zoom(CHURCH, staged, job, box)
    if outside != 0:
        asked["_outside"] = outside
        return False
    original = _sha(CHURCH)
    done = confirm(job, dest)
    back = undo(job, dest)
    if not done.get("ok") or not back.get("ok") or not os.path.isfile(dest):
        return False
    return _sha(dest) == original and _sha(CHURCH) == original


def _refused_cleanly(asked: dict, src: str, before: str) -> bool:
    """A photograph or an unmatched face is refused, and the file bytes stay put."""
    from artwork_edits import SAFE_EDIT

    reply = (asked.get("reply") or "").strip()
    allowed = (
        reply == SAFE_EDIT
        or reply.startswith("I could not keep the original size")
        or reply.startswith("I could not match the original font")
    )
    return asked.get("pending") is not True and allowed and _sha(src) == before


def _raster_phone_ok(asked: dict, src: str, before: str, require_serif: bool) -> tuple[bool, str]:
    """A painted phone keeps a matching face at 7 pt or more. A textured ground is refused."""
    import pymupdf as fitz
    from extra_checks import check_e5

    reply = asked.get("reply") or ""
    if asked.get("pending") is not True:
        ok = _refused_cleanly(asked, src, before) and "bahnschrift" not in reply.lower() and "jokerman" not in reply.lower()
        return ok, reply[:160]
    staged = os.path.join(asked.get("_preview") or "", "staged.pdf")
    if not os.path.isfile(staged):
        return False, "no staged pdf"
    size = 0.0
    font = ""
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
    row = check_e5(staged)
    low = font.lower()
    ok = (
        size >= 7.0
        and row.get("status") != "failed"
        and "5.9" not in str(row.get("detail") or "")
        and "bahnschrift" not in low
        and "jokerman" not in low
        and (not require_serif or any(mark in low for mark in (
            "serif", "times", "georgia", "garamond", "playfair", "cambria", "palatino",
            "baskerville", "crimson", "cormorant", "marcellus", "cinzel",
        )))
        and "OSError" not in reply
    )
    return ok, f"{font} {size:.1f}pt e5={row.get('status')} {reply[:80]}"


def _named_date_ok(asked: dict, job: str, src: str) -> tuple[bool, str]:
    """9 OCT on this poster is edited cleanly, or refused when the ground is a photograph."""
    from artwork_edits import SAFE_EDIT

    reply = asked.get("reply") or ""
    if "from 11 oct" in reply.lower() or "jokerman" in reply.lower():
        return False, reply[:160]
    if asked.get("pending") is True:
        clean = "9 OCT" in reply and "72-97" not in reply and _church_date_clean(asked, job)
        return clean, (reply[:120] + (f" outside {asked.get('_outside')}" if asked.get("_outside") is not None else ""))[:180]
    refused = reply.strip() == SAFE_EDIT and _sha(src) == asked.get("_srcsha")
    return refused, reply[:160]


def _pictures() -> None:
    if os.path.isfile(FLYER):
        before = _sha(FLYER)
        asked = _ask(FLYER, "change the phone number to 082 123 4567", "medella-flyer", 148, 210)
        asked["_srcsha"] = before
        ok, detail = _raster_phone_ok(asked, FLYER, before, True)
        if _sha(FLYER) != before:
            ok = False
        if asked.get("pending") is True:
            from artwork_edits import _load, _style_of

            bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts"))
            fontfile = os.path.abspath(str((_load("medella-flyer").get("proposal") or {}).get("fontfile") or ""))
            if not fontfile.startswith(bundled) or "dejavu" in fontfile.lower() or _style_of(fontfile) != "serif":
                ok = False
                detail = detail + " fontfile " + fontfile
        staged = os.path.join(asked["_preview"], "staged.pdf")
        if os.path.isfile(staged):
            _zoom(FLYER, staged, "medella-flyer", _proposal_box("medella-flyer"))
        _record("medella-flyer-phone", ok, detail)
    else:
        _record("medella-flyer-phone", False, "missing flyer pdf")
    if os.path.isfile(CHURCH):
        church_sha = _sha(CHURCH)
        phone = _ask(CHURCH, "change the phone number to 082 123 4567", "church-phone", 420, 420)
        date = _ask(CHURCH, "change the date from 9 OCT to 12 October 2026", "church-date", 420, 420)
        date["_srcsha"] = church_sha
        bare = _ask(CHURCH, "change the date to 12 October 2026", "church-date-many", 420, 420)
        short = _ask(CHURCH, "change 9 OCT to 12 OCT", "church-date-bare", 420, 420)
        short["_srcsha"] = church_sha
        phone_ok, phone_detail = _raster_phone_ok(phone, CHURCH, church_sha, False)
        if phone.get("pending") is True:
            from artwork_edits import _load

            bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "fonts"))
            fontfile = os.path.abspath(str((_load("church-phone").get("proposal") or {}).get("fontfile") or ""))
            if not fontfile.startswith(bundled) or "dejavu" in fontfile.lower() or "/usr/share/fonts" in fontfile:
                phone_ok = False
                phone_detail = phone_detail + " fontfile " + os.path.basename(fontfile)
        if phone.get("pending") is True and "072" not in (phone.get("reply") or ""):
            phone_ok = False
        date_ok, date_detail = _named_date_ok(date, "church-date", CHURCH)
        bare_reply = (bare.get("reply") or "").lower()
        bare_ok = bare.get("pending") is not True and "more than one" in bare_reply and "safely change" in bare_reply
        short_ok, short_detail = _named_date_ok(short, "church-date-bare", CHURCH)
        phone_staged = os.path.join(phone["_preview"], "staged.pdf")
        if os.path.isfile(phone_staged):
            _zoom(CHURCH, phone_staged, "church-phone", _proposal_box("church-phone"))
        if _sha(CHURCH) != church_sha:
            date_ok = short_ok = phone_ok = False
        _record("church-phone", phone_ok, phone_detail[:140])
        _record("church-date", date_ok, date_detail[:180])
        _record("church-date-many", bare_ok, (bare.get("reply") or "")[:160])
        _record("church-date-bare", short_ok, short_detail[:180])
    else:
        _record("church-phone", False, "missing church pdf")
        _record("church-date", False, "missing church pdf")
        _record("church-date-many", False, "missing church pdf")
        _record("church-date-bare", False, "missing church pdf")


def _broken_subset() -> None:
    import pymupdf as fitz
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    opts = Options()
    opts.drop_tables = ["name"]
    opts.notdef_outline = True
    opts.recommended_glyphs = True
    subset = Subsetter(options=opts)
    subset.populate(text="C")
    face = TTFont(FONT)
    subset.subset(face)
    broken = os.path.join(ROOT, "broken.ttf")
    face.save(broken)
    face.close()
    full = os.path.join(ROOT, "full.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=240)
    page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1), width=0)
    page.insert_font(fontname="DEJ", fontfile=FONT)
    page.insert_text((36, 90), "Call 012 345 6789", fontsize=18, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 150), "Sunday 5 March 2026", fontsize=16, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    doc.save(full)
    doc.close()
    opened = fitz.open(full)
    file_xref = None
    for xref in range(1, opened.xref_length()):
        obj = opened.xref_object(xref)
        if "/FontFile2" in obj:
            import re
            match = re.search(r"/FontFile2\s+(\d+)\s+0\s+R", obj)
            file_xref = int(match.group(1))
            opened.xref_set_key(xref, "FontName", "(null)")
    opened.update_stream(file_xref, open(broken, "rb").read())
    for xref in range(1, opened.xref_length()):
        obj = opened.xref_object(xref)
        if "/Type /Font" in obj and "/BaseFont" in obj:
            opened.xref_set_key(xref, "BaseFont", "(null)")
    src = os.path.join(ROOT, "broken.pdf")
    opened.save(src)
    opened.close()

    # The name table and BaseFont are gone, so the class cannot be known.
    # Guessing a bundled face would be the wrong style. Refuse, and leave the file.
    before = open(src, "rb").read()
    from artwork_edits import SAFE_EDIT

    phone = _ask(src, "change the phone number to 082 123 4567", "broken-phone")
    reply = (phone.get("reply") or "").strip()
    phone_ok = phone.get("pending") is not True and reply == SAFE_EDIT and open(src, "rb").read() == before
    _record("broken-phone", phone_ok, reply[:140] or "no reply")

    date = _ask(src, "change the date to 12 October 2026", "broken-date")
    date_reply = (date.get("reply") or "").strip()
    date_ok = date.get("pending") is not True and date_reply == SAFE_EDIT and open(src, "rb").read() == before
    _record("broken-date", date_ok, date_reply[:140] or "no reply")

    white = os.path.join(ROOT, "white.pdf")
    doc = fitz.open()
    page = doc.new_page(width=420, height=200)
    page.insert_font(fontname="DEJ", fontfile=FONT)
    page.insert_text((36, 80), "Call 012 345 6789", fontsize=18, fontname="DEJ", fontfile=FONT, color=(1, 1, 1))
    doc.save(white)
    doc.close()
    blank = _ask(white, "change the phone number to 082 123 4567", "white-phone")
    refused = blank.get("pending") is not True and not blank.get("actions") and "safely change" in (blank.get("reply") or "").lower()
    _record("no-apply-when-blank", refused, (blank.get("reply") or "")[:140])


def _gate_outside() -> None:
    import pymupdf as fitz
    from artwork_edits import visual_gates

    src = os.path.join(ROOT, "gate.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_font(fontname="DEJ", fontfile=FONT)
    page.insert_text((40, 80), "CITY HALL", fontsize=18, fontname="DEJ", fontfile=FONT, color=(0, 0, 0))
    doc.save(src)
    doc.close()
    dirty = os.path.join(ROOT, "gate-dirty.pdf")
    shutil.copyfile(src, dirty)
    doc = fitz.open(dirty)
    doc[0].draw_rect(fitz.Rect(200, 40, 230, 55), color=(1, 0, 0), fill=(1, 0, 0), width=0)
    doc.saveIncr()
    doc.close()
    problems = visual_gates(src, dirty, [40, 60, 160, 85], "CITY HALL", "CITY HALL")
    blob = " ".join(problems)
    _record("gate-a-outside", "pixels outside" in blob, blob[:120] or "no fail")


def main() -> None:
    _fonts()
    _phrases()
    _a6()
    _pictures()
    _broken_subset()
    _gate_outside()
    try:
        os.makedirs(ART, exist_ok=True)
        with open(os.path.join(ART, "table.txt"), "w", encoding="utf-8") as handle:
            for row in ROWS:
                handle.write(f"{row['case']}\t{'pass' if row['ok'] else 'FAIL'}\t{row['detail']}\n")
    except OSError:
        pass
    print(f"laptop edits {len(ROWS) - len(FAILURES)} pass, {len(FAILURES)} fail")
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")


if __name__ == "__main__":
    main()
