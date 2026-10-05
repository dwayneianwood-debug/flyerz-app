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
    if problems:
        _record(name, False, "; ".join(problems))
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
    _record(name, not problems, "; ".join(problems) or expect)


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
    ok = asked and asked.get("pending") and "Monday 12 October" in text and "Saturday" not in text and "not applied" in reply.lower()
    if ok:
        os.makedirs(ART, exist_ok=True)
        shutil.copyfile(os.path.join(preview, "before.png"), os.path.join(ART, "raster-date-before.png"))
        shutil.copyfile(os.path.join(preview, "after.png"), os.path.join(ART, "raster-date-after.png"))
    _record("raster-saturday", bool(ok), text.replace("\n", " ")[:80] or reply[:80])


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
    _record("cat-yes-undo", not problems, "; ".join(problems) or "Monday 12 October, then back")


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
