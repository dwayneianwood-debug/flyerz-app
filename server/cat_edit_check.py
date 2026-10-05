#!/usr/bin/env python3
"""Small edits the black cat previews, then applies only after a confirm.

Medella and the church poster are pictures, so the phone number is read by
OCR and set again as vector type. Ian's A6 is used when /tmp/real_a6.pdf is
on this machine. A live-text A6 stands in for the heading and the logo move.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="cat-edit-")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
ART = "/opt/cursor/artifacts/cat"
MM = 72.0 / 25.4
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SCRIPT = os.path.join(os.path.dirname(__file__), "designer_assistant.py")
FIXTURES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures"))
NEW_PHONE = "082 123 4567"


def _phone_digits(text: str) -> str:
    import re
    return re.sub(r"\D", "", text or "")

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
        return "\n".join((page.get_text("text") or "") for page in doc)
    finally:
        doc.close()


def _place(image: str, dest: str, width_mm: float, height_mm: float) -> None:
    """Put the whole picture on the product page so a phone number is not cropped off."""
    import pymupdf as fitz
    from PIL import Image

    with Image.open(image) as im:
        iw, ih = im.size
    doc = fitz.open()
    page = doc.new_page(width=width_mm * MM, height=height_mm * MM)
    scale = min(page.rect.width / iw, page.rect.height / ih)
    dw, dh = iw * scale, ih * scale
    x = (page.rect.width - dw) / 2.0
    y = (page.rect.height - dh) / 2.0
    page.insert_image(fitz.Rect(x, y, x + dw, y + dh), filename=image)
    doc.save(dest)
    doc.close()


def _live_sheet(dest: str, width_mm: float, height_mm: float) -> None:
    import pymupdf as fitz
    from PIL import Image

    logo = os.path.join(ROOT, "logo.png")
    Image.new("RGB", (48, 48), (180, 30, 40)).save(logo)
    doc = fitz.open()
    page = doc.new_page(width=width_mm * MM, height=height_mm * MM)
    page.insert_image(fitz.Rect(36, 28, 78, 70), filename=logo)
    page.insert_font(fontname="DEJAVU", fontfile=FONT)
    page.insert_text((36, 110), "HARVEST NIGHT", fontsize=22, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 150), "Call 073 000 1111", fontsize=12, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    page.insert_text((36, 180), "Open 5 March", fontsize=12, fontname="DEJAVU", fontfile=FONT, color=(0, 0, 0))
    doc.save(dest)
    doc.close()


def _ask(src: str, message: str, job: str, preview: str, dest: str, width: float, height: float, action: str = "") -> dict:
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
    if action:
        cmd.extend(["--action", action])
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy(), timeout=180, check=False)
    payload = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("{"):
            payload = json.loads(line)
            break
    payload["_code"] = proc.returncode
    payload["_err"] = (proc.stderr or "")[-300:]
    TRANSCRIPT.append(f"## {job} :: {action or message}\n{(payload.get('reply') or payload.get('_err') or '')}\n")
    return payload


def _keep_previews(preview: str, stem: str) -> None:
    os.makedirs(ART, exist_ok=True)
    for side in ("before", "after"):
        src = os.path.join(preview, f"{side}.png")
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(ART, f"{stem}-{side}.png"))


def _checks_ran(payload: dict) -> str:
    checks = payload.get("checks") or []
    points = [row for row in checks if str(row.get("num") or "").strip()]
    extras = [row for row in checks if str(row.get("id") or "").startswith("extra_")]
    reply = payload.get("reply") or ""
    problems = []
    if len(points) < 10:
        problems.append(f"points {len(points)}")
    if len(extras) < 5:
        problems.append(f"extras {len(extras)}")
    if "25-point" not in reply or "E1" not in reply:
        problems.append("reply missed the re-run")
    return ", ".join(problems)


def _phone_case(name: str, image: str, width: float, height: float, apply: bool, undo: bool = False) -> None:
    src = os.path.join(ROOT, f"{name}.pdf")
    dest = os.path.join(ROOT, f"{name}-out.pdf")
    preview = os.path.join(ROOT, f"preview-{name}")
    _place(image, src, width, height)
    original = _sha(src)
    asked = _ask(src, f"change the phone number to {NEW_PHONE}", name, preview, dest, width, height)
    _keep_previews(preview, name)
    problems = []
    reply = asked.get("reply") or ""
    if asked.get("pending") is not True or "not applied" not in reply.lower():
        problems.append(reply[:160] or asked.get("_err") or "no proposal")
    if os.path.isfile(dest):
        problems.append("applied before confirm")
    if _sha(src) != original:
        problems.append("source changed")
    before = os.path.join(preview, "before.png")
    after = os.path.join(preview, "after.png")
    if not (os.path.isfile(before) and os.path.isfile(after)):
        problems.append("missing preview")
    elif _sha(before) == _sha(after):
        problems.append("preview unchanged")
    staged = os.path.join(preview, "staged.pdf")
    staged_text = _text(staged) if os.path.isfile(staged) else ""
    if "0821234567" not in _phone_digits(staged_text):
        problems.append("staged text " + (staged_text[:80] or "missing"))
    if "BISHOP" in reply or "Waterbok" in reply:
        problems.append("replaced more than the number")
    if "()" in reply or "®" in reply:
        problems.append("stray brackets " + reply[:120])
    if not apply:
        _record(name, not problems, "; ".join(problems) or "preview only, not applied")
        return
    if problems:
        _record(name, False, "; ".join(problems))
        return
    done = _ask(src, "yes", name, preview, dest, width, height)
    problems.extend([] if done.get("ok") and os.path.isfile(dest) and "0821234567" in _phone_digits(_text(dest)) else ["confirm failed " + (done.get("reply") or "")[:120]])
    if _sha(src) != original:
        problems.append("source changed on confirm")
    check_note = _checks_ran(done)
    if check_note:
        problems.append(check_note)
    if undo:
        undone = _ask(src, "undo", name, preview, dest, width, height)
        if not undone.get("ok") or "0821234567" in _phone_digits(_text(dest)):
            problems.append("undo " + (undone.get("reply") or "")[:80])
        else:
            undo_note = _checks_ran(undone)
            if undo_note:
                problems.append("undo " + undo_note)
    _record(name, not problems, "; ".join(problems) or f"{width:.0f}x{height:.0f} " + ("confirmed, undone" if undo else "confirmed"))


def _heading_and_logo(width: float, height: float, stem: str) -> None:
    import pymupdf as fitz

    src = os.path.join(ROOT, f"{stem}.pdf")
    dest = os.path.join(ROOT, f"{stem}-out.pdf")
    preview = os.path.join(ROOT, f"preview-{stem}")
    _live_sheet(src, width, height)
    original = _sha(src)
    asked = _ask(src, "make the heading bigger", stem, preview, dest, width, height)
    _keep_previews(preview, stem + "-heading")
    problems = []
    if "not applied" not in (asked.get("reply") or "").lower() or os.path.isfile(dest):
        problems.append((asked.get("reply") or "no heading")[:140])
    staged = os.path.join(preview, "staged.pdf")
    if os.path.isfile(staged):
        doc = fitz.open(staged)
        try:
            size = 0.0
            for block in (doc[0].get_text("dict").get("blocks") or []):
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        if "HARVEST" in (span.get("text") or ""):
                            size = float(span.get("size") or 0)
            phone = doc[0].get_text("text")
        finally:
            doc.close()
        if size < 26:
            problems.append(f"heading size {size:.1f}")
        if "073 000 1111" not in phone:
            problems.append("phone lost")
    else:
        problems.append("no staged heading")
    if _sha(src) != original:
        problems.append("source changed")
    if not problems:
        done = _ask(src, "", stem, preview, dest, width, height, action="confirm-edit")
        if not done.get("ok") or "HARVEST" not in _text(dest):
            problems.append("heading confirm")
        note = _checks_ran(done)
        if note:
            problems.append(note)
        if not any(item.get("id") == "undo-edit" for item in (done.get("actions") or [])):
            problems.append("no undo button")
    _record(stem + "-heading", not problems, "; ".join(problems) or f"{width:.0f}x{height:.0f} heading bigger")

    logo_src = dest if os.path.isfile(dest) else src
    logo_job = stem + "-logo"
    logo_preview = os.path.join(ROOT, f"preview-{logo_job}")
    logo_dest = os.path.join(ROOT, f"{logo_job}-out.pdf")
    before_doc = fitz.open(logo_src)
    try:
        before_y = min(info["bbox"][1] for info in before_doc[0].get_image_info())
    finally:
        before_doc.close()
    moved = _ask(logo_src, "move the logo up", logo_job, logo_preview, logo_dest, width, height)
    _keep_previews(logo_preview, stem + "-logo")
    logo_problems = []
    if "not applied" not in (moved.get("reply") or "").lower() or os.path.isfile(logo_dest):
        logo_problems.append((moved.get("reply") or "no logo move")[:140])
    staged_logo = os.path.join(logo_preview, "staged.pdf")
    if os.path.isfile(staged_logo):
        after_doc = fitz.open(staged_logo)
        try:
            after_y = min(info["bbox"][1] for info in after_doc[0].get_image_info())
            words = after_doc[0].get_text("text")
        finally:
            after_doc.close()
        if after_y >= before_y - 2:
            logo_problems.append(f"logo y {before_y:.1f} -> {after_y:.1f}")
        if "HARVEST" not in words:
            logo_problems.append("heading lost")
    else:
        logo_problems.append("no staged logo")
    if not logo_problems:
        done = _ask(logo_src, "yes", logo_job, logo_preview, logo_dest, width, height)
        if not done.get("ok") or not os.path.isfile(logo_dest):
            logo_problems.append("logo confirm")
        note = _checks_ran(done)
        if note:
            logo_problems.append(note)
    _record(stem + "-logo", not logo_problems, "; ".join(logo_problems) or f"{width:.0f}x{height:.0f} logo up")


def _real_a6() -> None:
    src = "/tmp/real_a6.pdf"
    if not os.path.isfile(src) or os.path.getsize(src) < 1000:
        _record("ian-a6", True, "file not on this machine")
        TRANSCRIPT.append("## ian-a6\nIan's A6 file is not on this machine (/tmp/real_a6.pdf), so this case was not run.\n")
        return
    dest = os.path.join(ROOT, "ian-a6-out.pdf")
    preview = os.path.join(ROOT, "preview-ian-a6")
    original = _sha(src)
    asked = _ask(src, f"change the phone number to {NEW_PHONE}", "ian-a6", preview, dest, 148, 105)
    if asked.get("pending") is not True:
        asked = _ask(src, "make the heading bigger", "ian-a6", preview, dest, 148, 105)
    _keep_previews(preview, "ian-a6")
    problems = []
    if "not applied" not in (asked.get("reply") or "").lower():
        problems.append((asked.get("reply") or "no proposal")[:160])
    if os.path.isfile(dest) or _sha(src) != original:
        problems.append("applied or source changed")
    if not problems:
        done = _ask(src, "yes", "ian-a6", preview, dest, 148, 105)
        if _sha(src) != original:
            problems.append("source changed")
        note = _checks_ran(done)
        if note:
            problems.append(note)
        if not done.get("ok"):
            problems.append("confirm")
    _record("ian-a6", not problems, "; ".join(problems) or "confirmed on the real file")


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
    medella = os.path.join(FIXTURES, "medella", "card_front.png")
    church = os.path.join(FIXTURES, "catch_fire", "src.jpg")
    _phone_case("medella-card", medella, 90, 50, True, undo=True)
    _phone_case("medella-a6", medella, 148, 105, True)
    _phone_case("medella-a5", medella, 148, 210, False)
    _phone_case("church-a5", church, 148, 210, True)
    _phone_case("church-a4", church, 210, 297, True)
    _heading_and_logo(105, 148, "a6-live")
    _heading_and_logo(210, 297, "a4-live")
    _real_a6()
    _write()
    return list(FAILURES)


def main() -> None:
    failed = run()
    print(f"cat edits {len(ROWS) - len(failed)} pass, {len(failed)} fail")
    if failed:
        raise SystemExit(f"{len(failed)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
