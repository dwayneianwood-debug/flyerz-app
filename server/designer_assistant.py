#!/usr/bin/env python3
"""The black cat as a prepress designer.

The chat used to be a keyword list with no model and no key. Gemini
(GEMINI_API_KEY, gemini-3.6-flash, generativelanguage.googleapis.com) is the
provider already used for OCR. A real key may rephrase the sentences below.
A missing or test key stays on these sentences, which only mention checks
that actually ran.
"""

from __future__ import annotations

import json
import os
import urllib.request

from client_file_audit import (
    FONT_ASK,
    apply_vector_fixes,
    audit_pdf,
    repair_cmyk_images,
)

ACTION_LABELS = {
    "add-bleed": "Add 5 mm bleed",
    "extend-bleed": "Extend the bleed to 5 mm",
    "convert-cmyk": "Convert to CMYK",
    "fix-black": "Fix the black",
    "thicken-hairlines": "Thicken hairlines to 0.25 pt",
    "change-size": "Change the size or orientation",
    "print-ready": "Run Print-ready",
    "client-message": "Write the client message",
    "download": "Download the press file",
}


def gemini_key_usable() -> bool:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if len(key) < 20:
        return False
    lowered = key.lower()
    if "test" in lowered or "not-real" in lowered or "not_real" in lowered or lowered.startswith("fake"):
        return False
    return True


def _sentence(check_id: str, audit: dict, extra: dict) -> str | None:
    if check_id == "size":
        row = extra.get("size") or {}
        if not row.get("ran"):
            return None
        return str(row.get("detail") or "I measured the page size.")
    if check_id == "bleed":
        row = extra.get("bleed") or {}
        if not row.get("ran"):
            return None
        return str(row.get("detail") or "I looked at the bleed.")
    if check_id == "fonts":
        fonts = audit.get("fonts") or {}
        if not fonts.get("checked"):
            return None
        if fonts.get("picture"):
            return "This is a picture, so there are no fonts to embed."
        problems = fonts.get("problems") or []
        if not problems:
            type3 = fonts.get("type3") or []
            if type3 and not fonts.get("embedded"):
                return "The only fonts are Type 3, which is fine for press."
            return "The fonts are embedded."
        bits = [f"{row['name']} ({row['reason']})" for row in problems[:6]]
        return "Fonts need a new export: " + ", ".join(bits) + f". {FONT_ASK}"
    if check_id == "hairlines":
        hair = audit.get("hairlines") or {}
        if not hair.get("checked"):
            return None
        if hair.get("picture"):
            return "This is a picture, so there are no vector hairlines."
        count = int(hair.get("count") or 0)
        if count <= 0:
            return "Strokes were checked. None are under 0.25 pt."
        return f"There are {count} hairlines under 0.25 pt. I can thicken them to 0.25 pt."
    if check_id == "black":
        black = audit.get("black") or {}
        if not black.get("checked"):
            return None
        if black.get("picture"):
            return "Black ink inside a picture is checked when the press file is built."
        bits = []
        if black.get("registration"):
            bits.append("registration black")
        if black.get("tacOver"):
            bits.append(f"total ink about {float(black.get('peakTac') or 0):.0f}%")
        if black.get("richSmallText"):
            bits.append("rich black on text under 18 pt")
        if black.get("largeKOnly"):
            bits.append("a large area of 100K black")
        if not bits:
            return "Black was checked. No registration black, and total ink is within 300%."
        return "Black needs a fix: " + ", ".join(bits) + "."
    if check_id == "resolution":
        res = audit.get("resolution") or {}
        if not res.get("checked"):
            return None
        images = res.get("images") or []
        if not images:
            if res.get("worst") is None:
                return "There is no picture large enough to judge for resolution."
            return f"The pictures are sharp enough (about {float(res['worst']):.0f} ppi)."
        bits = [f"{row['name']} at {float(row['ppi']):.0f} ppi" for row in images[:6]]
        return "Resolution is short: " + ", ".join(bits) + ". Press work wants about 300 ppi."
    if check_id == "spots":
        spots = audit.get("spots") or {}
        if not spots.get("checked"):
            return None
        names = spots.get("names") or []
        if not names:
            return "No spot colours."
        return "Spot colours: " + ", ".join(names[:6]) + ". I can convert them to CMYK."
    if check_id == "textCut":
        row = extra.get("textCut") or {}
        if not row.get("ran"):
            return None
        return str(row.get("detail") or "")
    if check_id == "pages":
        if not audit.get("isPdf") and not audit.get("pages"):
            return None
        count = int(audit.get("pages") or 0)
        if count <= 0:
            return None
        word = "page" if count == 1 else "pages"
        return f"The file has {count} {word}."
    if check_id == "qr":
        row = extra.get("qr") or {}
        if not row.get("ran"):
            return None
        return str(row.get("detail") or "")
    return None


def actions_for(audit: dict, extra: dict) -> list[dict]:
    actions = []

    def add(action_id: str) -> None:
        if any(item["id"] == action_id for item in actions):
            return
        actions.append({"id": action_id, "label": ACTION_LABELS[action_id]})

    bleed = extra.get("bleed") or {}
    if bleed.get("ran") and not bleed.get("ok"):
        add("extend-bleed" if bleed.get("partial") else "add-bleed")
    if (audit.get("spots") or {}).get("names"):
        add("convert-cmyk")
    black = audit.get("black") or {}
    if black.get("checked") and (black.get("registration") or black.get("tacOver") or black.get("richSmallText") or black.get("largeKOnly")):
        add("fix-black")
    hair = audit.get("hairlines") or {}
    if hair.get("checked") and int(hair.get("count") or 0) > 0:
        add("thicken-hairlines")
    size = extra.get("size") or {}
    if size.get("ran") and not size.get("ok"):
        add("change-size")
    add("print-ready")
    add("client-message")
    add("download")
    return actions


def reply_from_audit(audit: dict, extra: dict | None = None) -> tuple[str, list[dict]]:
    extra = extra or {
        "size": audit.get("size") or {},
        "bleed": audit.get("bleed") or {},
        "textCut": audit.get("textCut") or {},
        "qr": audit.get("qr") or {},
    }
    order = ("size", "bleed", "fonts", "hairlines", "black", "resolution", "spots", "textCut", "pages", "qr")
    lines = ["I checked this file."]
    for check_id in order:
        sentence = _sentence(check_id, audit, extra)
        if sentence:
            lines.append(sentence)
    actions = actions_for(audit, extra)
    if actions:
        labels = ", ".join(item["label"] for item in actions)
        lines.append("I can do these: " + labels + ". Tell me which, or press a button.")
    reply = " ".join(lines)
    polished = _polish(reply)
    return polished or reply, actions


def _polish(reply: str) -> str:
    if not gemini_key_usable():
        return ""
    try:
        from gemini_api import gemini_generate_content_url
    except Exception:
        return ""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    url = gemini_generate_content_url(key)
    prompt = (
        "Rephrase this prepress note in plain English. Keep every fact. "
        "Do not add a check, a measurement, or a result that is not already in the note. "
        "Do not say a check passed if the note does not say it ran.\n\n"
        + reply
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600},
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""
    cleaned = str(text or "").strip()
    if not cleaned or len(cleaned) > 4000:
        return ""
    return cleaned


def _page_boxes(path: str) -> tuple[float, float, float, float]:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        media = page.mediabox
        trim = page.trimbox if page.trimbox.width > 2 else media
        return (
            float(trim.width) * 25.4 / 72.0,
            float(trim.height) * 25.4 / 72.0,
            float(media.width) * 25.4 / 72.0,
            float(media.height) * 25.4 / 72.0,
        )
    finally:
        doc.close()


def _bleed_check(path: str) -> dict:
    if not str(path).lower().endswith(".pdf"):
        return {"ran": True, "ok": False, "partial": False, "detail": "A picture has no bleed box. I can add 5 mm when building the press file."}
    try:
        trim_w, trim_h, media_w, media_h = _page_boxes(path)
    except Exception as exc:
        return {"ran": False, "ok": False, "detail": str(exc)[:120]}
    inset_x = (media_w - trim_w) / 2.0
    inset_y = (media_h - trim_h) / 2.0
    inset = min(inset_x, inset_y)
    if inset >= 4.6:
        return {"ran": True, "ok": True, "partial": False, "detail": f"Bleed is about {inset:.1f} mm, which covers the 5 mm we need."}
    if inset >= 0.4:
        return {"ran": True, "ok": False, "partial": True, "detail": f"Bleed is only about {inset:.1f} mm. It should be 5 mm."}
    return {"ran": True, "ok": False, "partial": False, "detail": "There is no bleed past the trim."}


def _size_check(path: str, trim_w: float | None, trim_h: float | None) -> dict:
    try:
        if str(path).lower().endswith(".pdf"):
            page_w, page_h, _mw, _mh = _page_boxes(path)
        else:
            return {"ran": True, "ok": True, "detail": "This is a picture. Size is decided when it is fitted to the order."}
    except Exception as exc:
        return {"ran": False, "ok": False, "detail": str(exc)[:120]}
    measured = f"The trim is {page_w:.0f} x {page_h:.0f} mm."
    if not trim_w or not trim_h:
        return {"ran": True, "ok": True, "detail": measured + " No order size was saved, so I did not call it a mismatch."}
    same = abs(page_w - trim_w) < 2.5 and abs(page_h - trim_h) < 2.5
    turned = abs(page_w - trim_h) < 2.5 and abs(page_h - trim_w) < 2.5
    if same:
        return {"ran": True, "ok": True, "detail": f"{measured} That matches the order ({trim_w:.0f} x {trim_h:.0f} mm)."}
    if turned:
        return {"ran": True, "ok": False, "detail": f"{measured} The order is {trim_w:.0f} x {trim_h:.0f} mm, so the page is turned the other way."}
    return {"ran": True, "ok": False, "detail": f"{measured} The order is {trim_w:.0f} x {trim_h:.0f} mm, so the size does not match."}


def _text_cut(path: str, trim_w: float | None, trim_h: float | None) -> dict:
    if not str(path).lower().endswith(".pdf"):
        return {"ran": False, "ok": False, "detail": ""}
    try:
        from green_gate import _pdf_text_edge

        width = float(trim_w or 148)
        height = float(trim_h or 210)
        over, near = _pdf_text_edge(path, width, height)
    except Exception:
        return {"ran": False, "ok": False, "detail": ""}
    if over:
        return {"ran": True, "ok": False, "detail": f"Text sits on the cut ({over[0]}). Move it at least 3 mm inside."}
    if near:
        return {"ran": True, "ok": False, "detail": f"Text is within 3 mm of the trim ({near[0]})."}
    return {"ran": True, "ok": True, "detail": "Text is inside the safe area."}


def _qr(path: str) -> dict:
    try:
        import cv2
        import numpy as np
        import pymupdf as fitz

        doc = fitz.open(path)
        found = []
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                gray = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2GRAY)
                value, _points, _straight = cv2.QRCodeDetector().detectAndDecode(gray)
                if value:
                    found.append(str(value)[:80])
        finally:
            doc.close()
    except Exception:
        return {"ran": False, "ok": False, "detail": ""}
    if not found:
        return {"ran": True, "ok": True, "detail": "I looked for a QR code. There isn't one on this file."}
    return {"ran": True, "ok": True, "detail": "The QR code reads: " + found[0]}


def inspect_artwork(path: str, trim_w: float | None = None, trim_h: float | None = None) -> dict:
    audit = audit_pdf(path, trim_w, trim_h)
    extra = {
        "size": _size_check(path, trim_w, trim_h),
        "bleed": _bleed_check(path),
        "textCut": _text_cut(path, trim_w, trim_h),
        "qr": _qr(path),
    }
    reply, actions = reply_from_audit(audit, extra)
    return {"audit": audit, "extra": extra, "reply": reply, "actions": actions, "provider": "gemini" if gemini_key_usable() else "rules"}


def client_message_text(path: str, trim_w: float | None, trim_h: float | None) -> str:
    from client_file_audit import combined_client_message

    audit = audit_pdf(path, trim_w, trim_h)
    extra_lines = []
    cut = _text_cut(path, trim_w, trim_h)
    if cut.get("ran") and not cut.get("ok"):
        extra_lines.append(cut["detail"])
    size = _size_check(path, trim_w, trim_h)
    if size.get("ran") and not size.get("ok"):
        extra_lines.append(size["detail"])
    bleed = _bleed_check(path)
    if bleed.get("ran") and not bleed.get("ok"):
        extra_lines.append(bleed["detail"])
    note = combined_client_message(audit, extra_lines)
    return note or "I checked the file. There is nothing I need the client to change."


def perform_action(action: str, src: str, dest: str, trim_w: float, trim_h: float) -> dict:
    action = (action or "").strip()
    if action == "client-message":
        return {"ok": True, "detail": client_message_text(src, trim_w, trim_h), "path": ""}
    if action == "download":
        ready = dest if dest and os.path.isfile(dest) else src
        return {"ok": os.path.isfile(ready), "detail": "The file is ready to download." if os.path.isfile(ready) else "There is no press file yet. Run Print-ready first.", "path": ready if os.path.isfile(ready) else ""}
    if action in ("fix-black", "thicken-hairlines", "convert-cmyk"):
        if not str(src).lower().endswith(".pdf"):
            return {"ok": False, "detail": "That fix needs a PDF.", "path": ""}
        fixes = apply_vector_fixes(src, dest)
        if action == "fix-black":
            repair_cmyk_images(dest)
        if action == "convert-cmyk":
            from press_ready_engine import convert_cmyk_keep_text

            cmyk = dest + ".cmyk.pdf"
            try:
                convert_cmyk_keep_text(dest, cmyk, block_font_substitution=False)
                os.replace(cmyk, dest)
            except Exception as exc:
                return {"ok": False, "detail": f"CMYK conversion did not finish ({str(exc)[:140]}).", "path": dest}
        return {"ok": True, "detail": f"Done. {action} changed the file ({fixes}).", "path": dest}
    if action in ("add-bleed", "extend-bleed", "change-size", "print-ready"):
        from press_ready_engine import compile_vector_press

        width, height = float(trim_w), float(trim_h)
        if action == "change-size":
            try:
                page_w, page_h, _mw, _mh = _page_boxes(src)
            except Exception:
                page_w, page_h = width, height
            if abs(page_w - height) < 2.5 and abs(page_h - width) < 2.5:
                width, height = height, width
        if str(src).lower().endswith(".pdf"):
            built = compile_vector_press(src, dest, width, height, 5.0)
            if built.get("used") and os.path.isfile(dest):
                return {"ok": True, "detail": f"The press file is {width:.0f} x {height:.0f} mm with 5 mm bleed.", "path": dest}
        from quick_print import make_print_ready

        folder = os.path.dirname(dest) or "."
        result = make_print_ready(src, folder, width, height, "custom", f"{width:.0f} x {height:.0f} mm", filename=os.path.basename(src))
        press = result.get("pressPath") or ""
        if press and os.path.isfile(press):
            if os.path.abspath(press) != os.path.abspath(dest):
                import shutil
                shutil.copyfile(press, dest)
            light = result.get("light")
            return {"ok": light != "red", "detail": f"Print-ready finished ({light}). {result.get('clientMessage') or ''}".strip(), "path": dest if os.path.isfile(dest) else press}
        return {"ok": False, "detail": result.get("clientMessage") or "Print-ready did not write a file.", "path": ""}
    return {"ok": False, "detail": "I don't know that action.", "path": ""}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--action", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--trim-w", type=float, default=0)
    parser.add_argument("--trim-h", type=float, default=0)
    args = parser.parse_args()
    trim_w = args.trim_w or None
    trim_h = args.trim_h or None
    action = (args.action or "").strip()
    message = (args.message or "").strip().lower()
    if not action and message:
        for key, label in ACTION_LABELS.items():
            if key in message or label.lower() in message:
                action = key
                break
        if message in ("yes", "do it", "please", "go ahead", "ok", "okay"):
            inspected = inspect_artwork(args.input, trim_w, trim_h)
            offered = [item["id"] for item in inspected["actions"] if item["id"] not in ("download", "print-ready", "client-message")]
            action = offered[0] if offered else "print-ready"
    if action:
        dest = args.output or (args.input + ".designer.pdf")
        done = perform_action(action, args.input, dest, float(trim_w or 148), float(trim_h or 210))
        print(json.dumps({
            "success": True,
            "reply": done.get("detail") or "Done.",
            "actions": [],
            "action": action,
            "ok": bool(done.get("ok")),
            "path": done.get("path") or "",
            "provider": "rules",
        }))
        return
    inspected = inspect_artwork(args.input, trim_w, trim_h)
    print(json.dumps({
        "success": True,
        "reply": inspected["reply"],
        "actions": inspected["actions"],
        "provider": inspected["provider"],
        "path": "",
        "ok": True,
    }))


if __name__ == "__main__":
    main()
