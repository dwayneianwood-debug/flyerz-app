#!/usr/bin/env python3
"""Small edits and house rules for the black cat."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="part3-")
os.environ["FLYERZ_DB_PATH"] = os.path.join(ROOT, "rules.sqlite")
os.environ["FLYERZ_EDIT_DIR"] = os.path.join(ROOT, "edits")
os.environ["FLYERZ_RULES_LEDGER"] = os.path.join(ROOT, "rules.jsonl")

from artwork_edits import confirm, propose, undo
from designer_assistant import _bleed_check
from client_file_audit import RICH, _black_replacement
from house_rules import add_rule, bleed_mm, connect, delete_rule, list_rules, rich_black_for, size_for, update_rule


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(f"{name} — {detail}")
    print(f"PASS {name}")


def _sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _live_pdf(path: str) -> None:
    import pymupdf as fitz

    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    doc = fitz.open()
    page = doc.new_page(width=420, height=300)
    page.draw_rect(fitz.Rect(0, 0, 420, 300), color=(0.1, 0.3, 0.7), fill=(0.1, 0.3, 0.7))
    page.insert_text((36, 80), "Call 082 111 1111", fontsize=16, fontfile=font, fontname="DEJAVU", color=(1, 1, 1))
    page.insert_text((36, 140), "Keep me", fontsize=16, fontfile=font, fontname="DEJAVU", color=(1, 1, 1))
    page.insert_text((36, 200), "Open 5 March", fontsize=16, fontfile=font, fontname="DEJAVU", color=(1, 1, 1))
    doc.save(path)
    doc.close()


def _text(path: str) -> str:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        return doc[0].get_text("text")
    finally:
        doc.close()


def test_rules() -> None:
    rules = list_rules()
    texts = [row["text"] for row in rules]
    check("seed-bleed", "Bleed is always 5 mm." in texts, str(texts[:1]))
    bleed = next(row for row in rules if row["text"] == "Bleed is always 5 mm.")
    check("seed-locked", bleed["locked"] and bleed["scope"] == "global")
    refused = delete_rule(bleed["id"])
    check("locked-delete", refused["ok"] is False and "can't delete" in refused["detail"])
    rewritten = update_rule(bleed["id"], "Bleed is always 1 mm.")
    check("locked-edit", rewritten["ok"] is False)
    again = next(row["text"] for row in list_rules() if row["id"] == bleed["id"])
    check("locked-untouched", again == "Bleed is always 5 mm.")
    mm, sentence = bleed_mm()
    check("bleed-applied", mm == 5.0 and sentence == "Bleed is always 5 mm.", sentence)
    taught = add_rule("client Medella wants rich black 60/40/40/100", "Medella")
    check("teach", taught["ok"] is True, str(taught))
    ink, rule = rich_black_for("Medella")
    check("rich-black-rule", ink == (0.60, 0.40, 0.40, 1.0) and "60/40/40/100" in rule, str(ink))
    card = add_rule("Medella card is 90x50", "Medella")
    sized = size_for("Medella")
    check("card-size", sized is not None and sized[0] == 90 and sized[1] == 50, str(sized))
    edited = update_rule(card["id"], "Medella card is 85x55")
    check("edit-taught", edited["ok"] is True and size_for("Medella")[0] == 85)
    update_rule(card["id"], "Medella card is 90x50")
    removed = delete_rule(taught["id"])
    check("delete-taught", removed["ok"] is True and rich_black_for("Medella")[0] is None)
    db = connect()
    db.execute("CREATE TABLE IF NOT EXISTS file_jobs (id INTEGER PRIMARY KEY, filename TEXT)")
    db.execute("INSERT INTO file_jobs (filename) VALUES ('finished.pdf')")
    db.execute("DELETE FROM file_jobs")
    db.commit()
    kept = db.execute("SELECT COUNT(*) FROM house_rules").fetchone()[0]
    db.close()
    check("jobs-do-not-drop-rules", kept >= 16, str(kept))
    fresh = sqlite3.connect(os.environ["FLYERZ_DB_PATH"])
    before = fresh.execute("SELECT text FROM house_rules WHERE rule_key = 'bleed-5mm'").fetchone()[0]
    fresh.close()
    connect()
    after = next(row["text"] for row in list_rules() if row.get("source") == "seed" or "5 mm" in row["text"])
    check("reconnect-does-not-rewrite", before == after == "Bleed is always 5 mm.")
    import pymupdf as fitz
    blank = os.path.join(ROOT, "blank.pdf")
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc.save(blank)
    doc.close()
    named = _bleed_check(blank)
    check("bleed-says-rule", named.get("ran") and "Applied rule: Bleed is always 5 mm." in named.get("detail", ""), named.get("detail", ""))
    docs = {row["source"]: row["text"] for row in list_rules()}
    cursorrules = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cursorrules"), encoding="utf-8").read()
    check("cursorrules-verbatim", cursorrules in docs.values(), "missing .cursorrules")
    check("prepress-file", any(row["source"] == ".cursor/rules/prepress.mdc" and row["locked"] and "Zero Regression Policy" in row["text"] for row in list_rules()))
    check("products-file", any(row["source"] == "shared/quick-print-products.json" and "card-90x55" in row["text"] for row in list_rules()))
    check("checks-file", any(row["source"] == "server/checks_guide.py" and row["text"].startswith("CHECKS = ") for row in list_rules()))
    db = connect()
    try:
        db.execute("DELETE FROM house_rules WHERE rule_key = 'bleed-5mm'")
        db.commit()
        deleted = True
    except sqlite3.IntegrityError:
        deleted = False
    db.close()
    check("sql-cannot-delete-locked", deleted is False)
    db = connect()
    try:
        db.execute("UPDATE house_rules SET text = 'rewritten' WHERE rule_key = 'bleed-5mm'")
        db.commit()
        rewritten_sql = True
    except sqlite3.IntegrityError:
        rewritten_sql = False
    db.close()
    check("sql-cannot-rewrite-locked", rewritten_sql is False)
    still = next(row["text"] for row in list_rules() if row["source"] == "seed")
    check("bleed-text-unchanged", still == "Bleed is always 5 mm.")
    ledger = os.environ["FLYERZ_RULES_LEDGER"]
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "rule_key": "ledger-only-rule",
            "text": "always send menu jobs to the designer",
            "source": "ledger",
        }) + "\n")
    db = connect()
    db.execute("DROP TABLE house_rules")
    db.commit()
    db.close()
    restored = {row["rule_key"]: row["text"] for row in list_rules()}
    check("ledger-restores-rule", restored.get("ledger-only-rule") == "always send menu jobs to the designer")
    check("ledger-restores-bleed", "Bleed is always 5 mm." in restored.values())


def test_black_ink_stays_default() -> None:
    custom = _black_replacement(0, 0, 0, 1, False, True, False, ink=(0.60, 0.40, 0.40, 1.0))
    default = _black_replacement(0, 0, 0, 1, False, True, False)
    check("custom-rich", custom == (0.60, 0.40, 0.40, 1.0, False), str(custom))
    check("default-rich", default == (0.40, 0.30, 0.30, 1.0, False) and RICH == (0.40, 0.30, 0.30, 1.0), str(default))


def test_live_edit() -> None:
    src = os.path.join(ROOT, "live.pdf")
    dest = os.path.join(ROOT, "live-out.pdf")
    preview = os.path.join(ROOT, "preview-live")
    _live_pdf(src)
    original = _sha(src)
    proposed = propose(src, "change the phone number to 082 123 4567", "job-live", preview)
    check("propose-phone", bool(proposed and proposed.get("pending")), str(proposed))
    check("source-untouched", _sha(src) == original)
    check("previews", os.path.isfile(os.path.join(preview, "before.png")) and os.path.isfile(os.path.join(preview, "after.png")))
    staged = _text(os.path.join(preview, "staged.pdf"))
    check("staged-phone", "082 123 4567" in staged and "082 111 1111" not in staged and "Keep me" in staged, staged)
    check("not-applied-yet", not os.path.isfile(dest))
    done = confirm("job-live", dest)
    check("confirm", done.get("ok") is True and "082 123 4567" in _text(dest), done.get("reply", ""))
    check("confirm-leaves-source", _sha(src) == original)
    undone = undo("job-live", dest)
    check("undo", undone.get("ok") is True and "082 111 1111" in _text(dest), _text(dest))
    dated = propose(src, "swap the date to 12 October", "job-date", os.path.join(ROOT, "preview-date"))
    staged_date = _text(os.path.join(ROOT, "preview-date", "staged.pdf"))
    check("date", dated.get("pending") is True and "12 October" in staged_date and "Keep me" in staged_date, staged_date)
    missing = propose(src, "change the phone number to 082 123 4567\u0378", "job-missing", os.path.join(ROOT, "preview-miss"))
    check("missing-glyph", missing.get("ok") is False and "glyph" in (missing.get("reply") or "").lower(), missing.get("reply", ""))
    check("missing-no-write", _sha(src) == original and not os.path.isfile(os.path.join(ROOT, "preview-miss", "staged.pdf")))


def test_logo_and_move() -> None:
    import pymupdf as fitz
    from PIL import Image

    src = os.path.join(ROOT, "logo.pdf")
    image = os.path.join(ROOT, "logo.png")
    Image.new("RGB", (40, 40), (200, 20, 20)).save(image)
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_image(fitz.Rect(40, 40, 80, 80), filename=image)
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    page.insert_text((4, 20), "Edge", fontsize=12, fontfile=font, fontname="DEJAVU", color=(0, 0, 0))
    page.insert_text((180, 180), "Centre", fontsize=12, fontfile=font, fontname="DEJAVU", color=(0, 0, 0))
    doc.save(src)
    doc.close()
    preview = os.path.join(ROOT, "preview-logo")
    proposed = propose(src, "make the logo bigger", "job-logo", preview)
    check("logo-propose", proposed.get("pending") is True, proposed.get("reply", ""))
    grown = fitz.open(os.path.join(preview, "staged.pdf"))
    try:
        infos = grown[0].get_image_info()
        box = infos[0]["bbox"]
        width = box[2] - box[0]
        text = grown[0].get_text("text")
    finally:
        grown.close()
    check("logo-bigger", width > 45, str(width))
    check("logo-text-kept", "Edge" in text and "Centre" in text, text)
    moved = propose(src, "move the text away from the edge", "job-move", os.path.join(ROOT, "preview-move"))
    check("move-propose", moved.get("pending") is True, moved.get("reply", ""))
    moved_doc = fitz.open(os.path.join(ROOT, "preview-move", "staged.pdf"))
    try:
        spans = []
        for block in (moved_doc[0].get_text("dict").get("blocks") or []):
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    spans.append(span)
        edge = next(span for span in spans if "Edge" in span["text"])
        centre = next(span for span in spans if "Centre" in span["text"])
    finally:
        moved_doc.close()
    limit = 5.0 * 72.0 / 25.4
    check("moved-in", edge["bbox"][0] >= limit - 1.0, str(edge["bbox"]))
    check("centre-stays", abs(centre["bbox"][0] - 180) < 2, str(centre["bbox"]))


def test_raster() -> None:
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont

    src = os.path.join(ROOT, "raster.pdf")
    png = os.path.join(ROOT, "raster.png")
    plate = Image.new("RGB", (800, 400), (255, 255, 255))
    draw = ImageDraw.Draw(plate)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42)
    draw.text((40, 80), "Call 082 555 0199", font=font, fill=(0, 0, 0))
    plate.save(png)
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_image(page.rect, filename=png)
    doc.save(src)
    doc.close()
    proposed = propose(src, "change the phone number to 082 123 4567", "job-raster", os.path.join(ROOT, "preview-raster"))
    check("raster-propose", proposed.get("pending") is True and "555" in (proposed.get("reply") or ""), proposed.get("reply", ""))
    check("raster-amber", "Amber" in (proposed.get("reply") or ""), proposed.get("reply", ""))
    staged = _text(os.path.join(ROOT, "preview-raster", "staged.pdf"))
    check("raster-new-text", "082 123 4567" in staged and "555" not in staged, staged)


def test_yes_confirms_edit() -> None:
    src = os.path.join(ROOT, "yes.pdf")
    dest = os.path.join(ROOT, "yes-out.pdf")
    _live_pdf(src)
    original = _sha(src)
    script = os.path.join(os.path.dirname(__file__), "designer_assistant.py")
    env = os.environ.copy()
    preview = os.path.join(ROOT, "preview-yes")
    first = subprocess.run(
        [sys.executable, script, "--input", src, "--message", "change the phone number to 082 123 4567",
         "--output", dest, "--job-id", "yes-job", "--preview-dir", preview],
        capture_output=True, text=True, env=env, check=False,
    )
    check("cli-propose", first.returncode == 0 and "not applied" in first.stdout.lower(), first.stderr[-400:])
    check("cli-no-dest", not os.path.isfile(dest))
    second = subprocess.run(
        [sys.executable, script, "--input", src, "--message", "yes", "--output", dest, "--job-id", "yes-job"],
        capture_output=True, text=True, env=env, check=False,
    )
    check("yes-applies", second.returncode == 0 and os.path.isfile(dest) and "082 123 4567" in _text(dest), second.stdout[-400:])
    check("yes-leaves-source", _sha(src) == original)


def main() -> None:
    test_rules()
    test_black_ink_stays_default()
    test_live_edit()
    test_logo_and_move()
    test_raster()
    test_yes_confirms_edit()
    shutil.rmtree(ROOT, ignore_errors=True)
    print("PART3 OK")


if __name__ == "__main__":
    main()
