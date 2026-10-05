#!/usr/bin/env python3
"""One small edit at a time, with a preview and an undo.

Live text stays in its own font. If a glyph is missing, nothing is substituted.
A picture gets the old words painted out and new vector text in the closest font,
and that match is amber unless it is exact.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from edit_style import DATE_IN_TEXT, PHONE_IN_TEXT, TIME_IN_TEXT, VENUE_IN_TEXT, fragment, swap_text

PHONE_ASK = re.compile(
    r"(?:change|update|set|replace|swap)\s+the\s+phone(?:\s+number)?\s+to\s+(.+)$",
    re.I,
)
DATE_ASK = re.compile(
    r"(?:change|update|set|replace|swap)\s+the\s+date\s+to\s+(.+)$",
    re.I,
)
TIME_ASK = re.compile(
    r"(?:change|update|set|replace|swap)\s+the\s+time\s+to\s+(.+)$",
    re.I,
)
VENUE_ASK = re.compile(
    r"(?:change|update|set|replace|swap)\s+the\s+venue\s+to\s+(.+)$",
    re.I,
)
LOGO_ASK = re.compile(r"make\s+the\s+logo\s+(bigger|smaller|larger)\b", re.I)
LOGO_MOVE_ASK = re.compile(r"move\s+the\s+logo\s+(up|down|left|right)\b", re.I)
HEADING_ASK = re.compile(r"make\s+the\s+heading\s+(bigger|smaller|larger)\b", re.I)
MOVE_ASK = re.compile(r"move\s+the\s+text\s+away\s+from\s+the\s+edge", re.I)
FIELD_NAME = {"phone": "phone number", "date": "date", "time": "time", "venue": "venue"}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _state_path(key: str) -> str:
    root = os.environ.get("FLYERZ_EDIT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "house_edits"
    )
    os.makedirs(root, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return os.path.join(root, safe + ".json")


def _load(key: str) -> dict:
    path = _state_path(key)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(key: str, state: dict) -> None:
    path = _state_path(key)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def _spans(page) -> list[dict]:
    found = []
    data = page.get_text("dict") or {}
    for block in data.get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = str(span.get("text") or "")
                if not text.strip():
                    continue
                found.append(span)
    return found


def _stray_chars(kind: str, requested: str) -> str:
    """Letters Ian typed that are not part of a date, phone, time, or venue.

    The restyle keeps the original format, so a stray mark would otherwise
    disappear. It is still checked against the font and refused when missing.
    """
    if kind == "phone":
        allowed = set("0123456789 +-().")
    elif kind == "date":
        allowed = set("0123456789 /-.,")
    elif kind == "time":
        allowed = set("0123456789 :.h")
    elif kind == "venue":
        allowed = set(" '-.,&/")
    else:
        return ""
    if kind in ("date", "time", "venue"):
        return "".join(char for char in requested if not char.isalpha() and char not in allowed)
    return "".join(char for char in requested if char not in allowed)


def _missing_glyphs(fontfile: str, text: str) -> list[str]:
    import pymupdf as fitz

    font = fitz.Font(fontfile=fontfile)
    missing = []
    for char in text:
        if char.isspace():
            continue
        if not font.has_glyph(ord(char)):
            missing.append(char)
    return missing


def _embedded_font(doc, page, fontname: str) -> str | None:
    import pymupdf as fitz

    wanted = re.sub(r"[^a-z0-9]", "", ((fontname or "").split("+")[-1]).lower())
    for item in page.get_fonts() or []:
        xref = int(item[0])
        names = [re.sub(r"[^a-z0-9]", "", str(part).lower()) for part in item[3:]]
        names = [name for name in names if name]
        if wanted and not any(wanted == name or name.startswith(wanted) or wanted.startswith(name) for name in names):
            continue
        try:
            extracted = doc.extract_font(xref)
        except Exception:
            continue
        buffer = extracted[3] if extracted and len(extracted) > 3 else None
        if not buffer:
            continue
        folder = tempfile.mkdtemp(prefix="edit-font-")
        path = os.path.join(folder, "face." + (extracted[1] or "ttf"))
        with open(path, "wb") as handle:
            handle.write(buffer)
        try:
            fitz.Font(fontfile=path)
        except Exception:
            continue
        return path
    lowered = (fontname or "").lower()
    base = {"helv": "helv", "Helvetica": "helv", "times": "times", "cour": "cour"}
    for key, built in (("helvetica", "helv"), ("times", "times"), ("courier", "cour"), ("helv", "helv")):
        if key in lowered:
            return built
    return None


def _render_page(path: str, dest: str, page_index: int = 0) -> None:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False, colorspace=fitz.csRGB)
        pix.save(dest)
    finally:
        doc.close()


def _parse(message: str) -> dict | None:
    text = " ".join((message or "").split())
    phone = PHONE_ASK.search(text)
    if phone:
        return {"kind": "phone", "new": phone.group(1).strip(" .")}
    date = DATE_ASK.search(text)
    if date:
        return {"kind": "date", "new": date.group(1).strip(" .")}
    time_ask = TIME_ASK.search(text)
    if time_ask:
        return {"kind": "time", "new": time_ask.group(1).strip(" .")}
    venue = VENUE_ASK.search(text)
    if venue:
        return {"kind": "venue", "new": venue.group(1).strip(" .")}
    logo_move = LOGO_MOVE_ASK.search(text)
    if logo_move:
        return {"kind": "logo", "scale": 1.0, "direction": logo_move.group(1).lower()}
    logo = LOGO_ASK.search(text)
    if logo:
        word = logo.group(1).lower()
        return {"kind": "logo", "scale": 0.8 if word == "smaller" else 1.25, "direction": ""}
    heading = HEADING_ASK.search(text)
    if heading:
        word = heading.group(1).lower()
        return {"kind": "heading", "factor": 0.8 if word == "smaller" else 1.25, "word": "smaller" if word == "smaller" else "bigger"}
    if MOVE_ASK.search(text):
        return {"kind": "move"}
    if re.search(r"shrink", text, re.I) and re.search(r"safe", text, re.I):
        return {"kind": "shrink"}
    return None


def _heading_span(spans: list[dict]) -> dict | None:
    scored = []
    for span in spans:
        text = str(span.get("text") or "").strip()
        if sum(ch.isalpha() for ch in text) < 3:
            continue
        scored.append(span)
    if not scored:
        return None
    return max(scored, key=lambda span: (float(span.get("size") or 0), -float(span["bbox"][1])))


def _live_target(spans: list[dict], kind: str) -> dict | None:
    for span in spans:
        if fragment(span.get("text") or "", kind):
            return span
    return None


def _rapid_words(png: str) -> list[dict]:
    """Same local reader the vector rebuild uses, one box per line of type."""
    import cv2
    from ocr_reader import local_rows

    image = cv2.imread(png)
    if image is None:
        return []
    rows = local_rows(image) or []
    words = []
    for item in rows:
        box = item[0] if item else None
        text = str(item[1] or "").strip() if len(item) > 1 else ""
        if not text or not box or len(box) < 4:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        left, top = min(xs), min(ys)
        width, height = max(xs) - left, max(ys) - top
        if width < 2 or height < 2:
            continue
        words.append({
            "text": text,
            "left": int(round(left)),
            "top": int(round(top)),
            "width": max(1, int(round(width))),
            "height": max(1, int(round(height))),
            "line": ("rapid", str(int(round(top)))),
        })
    return words


def _tesseract_words(png: str) -> list[dict]:
    try:
        proc = subprocess.run(
            ["tesseract", png, "stdout", "tsv"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    words = []
    for line in (proc.stdout or "").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        text = parts[11].strip()
        if not text:
            continue
        try:
            words.append({
                "text": text,
                "left": int(parts[6]),
                "top": int(parts[7]),
                "width": int(parts[8]),
                "height": int(parts[9]),
                "line": (parts[2], parts[3], parts[4]),
            })
        except ValueError:
            continue
    return words


def _ocr_words(png: str) -> list[dict]:
    try:
        found = _rapid_words(png)
    except Exception:
        found = []
    if found:
        return found
    return _tesseract_words(png)


def _line_groups(words: list[dict]) -> list[list[dict]]:
    groups: list[dict] = []
    for word in sorted(words, key=lambda item: (item["top"], item["left"])):
        mid = word["top"] + word["height"] / 2.0
        placed = False
        for group in groups:
            if abs(mid - group["mid"]) <= max(8.0, group["height"] * 0.55):
                group["words"].append(word)
                group["mid"] = (group["mid"] + mid) / 2.0
                group["height"] = max(group["height"], word["height"])
                placed = True
                break
        if not placed:
            groups.append({"mid": mid, "height": word["height"], "words": [word]})
    return [group["words"] for group in groups]


def _closest_font(crop, text: str) -> tuple[str, float]:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    if crop is None or crop.size == 0:
        path = next((item for item in FONT_CANDIDATES if os.path.isfile(item)), "")
        return path, 999.0
    best_path = ""
    best_score = 1e9
    height = max(12, crop.shape[0])
    for path in FONT_CANDIDATES:
        if not os.path.isfile(path):
            continue
        font = ImageFont.truetype(path, max(10, height - 2))
        canvas = Image.new("RGB", (max(crop.shape[1], 8), max(height, 8)), (255, 255, 255))
        ImageDraw.Draw(canvas).text((0, 0), text, font=font, fill=(0, 0, 0))
        plate = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        plate = cv2.resize(plate, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(np.mean(np.abs(plate.astype(np.float32) - crop.astype(np.float32))))
        if score < best_score:
            best_score = score
            best_path = path
    return best_path, best_score


def propose(src: str, message: str, key: str, preview_dir: str) -> dict | None:
    parsed = _parse(message)
    if not parsed:
        return None
    import pymupdf as fitz

    if not os.path.isfile(src):
        return {"ok": False, "reply": "I can't see the file, so I have not changed anything.", "actions": []}
    doc = fitz.open(src)
    try:
        page = doc[0]
        spans = _spans(page)
        proposal = {"kind": parsed["kind"], "page": 0, "live": bool(spans), "src": src}
        if parsed["kind"] in ("phone", "date", "time", "venue", "heading"):
            if parsed["kind"] == "heading":
                span = _heading_span(spans)
            else:
                span = _live_target(spans, parsed["kind"])
            if span:
                old = span["text"]
                shown_old = old
                shown_new = old
                if parsed["kind"] == "heading":
                    new_text = old
                else:
                    try:
                        swapped = swap_text(old, parsed["kind"], parsed["new"])
                    except ValueError:
                        label = FIELD_NAME.get(parsed["kind"], "value")
                        return {
                            "ok": False,
                            "reply": f"I couldn't read that {label}, so I have not changed anything.",
                            "actions": [],
                        }
                    if not swapped:
                        label = FIELD_NAME.get(parsed["kind"], "text")
                        return {
                            "ok": False,
                            "reply": f"I could not find a {label} on the artwork, so I have not changed anything.",
                            "actions": [],
                        }
                    shown_old, shown_new, new_text = swapped
                fontfile = _embedded_font(doc, page, str(span.get("font") or ""))
                if not fontfile:
                    return {
                        "ok": False,
                        "reply": f"The text uses {span.get('font') or 'an unknown font'}, and I don't have that font. I have not substituted another one.",
                        "actions": [],
                    }
                probe = new_text + _stray_chars(parsed["kind"], parsed.get("new") or "")
                if fontfile in ("helv", "times", "cour"):
                    built = fitz.Font(fontfile)
                    missing = [char for char in probe if not char.isspace() and not built.has_glyph(ord(char))]
                else:
                    missing = _missing_glyphs(fontfile, probe)
                if missing:
                    shown = "".join(dict.fromkeys(missing))
                    return {
                        "ok": False,
                        "reply": f"The font {span.get('font')} has no glyph for {shown}. I have not substituted another font, and nothing was changed.",
                        "actions": [],
                    }
                proposal.update({
                    "mode": "live-text",
                    "old": old,
                    "new": new_text,
                    "shownOld": shown_old,
                    "shownNew": shown_new,
                    "bbox": list(span["bbox"]),
                    "origin": list(span.get("origin") or (span["bbox"][0], span["bbox"][3])),
                    "size": float(span.get("size") or 12) * (float(parsed["factor"]) if parsed["kind"] == "heading" else 1.0),
                    "font": str(span.get("font") or ""),
                    "fontfile": fontfile,
                    "color": int(span.get("color") or 0),
                    "amber": False,
                    "sizeFactor": float(parsed.get("factor") or 1),
                    "word": parsed.get("word") or "",
                })
            else:
                raster = _propose_raster(src, parsed, proposal)
                if raster.get("ok") is False:
                    return raster
                proposal = raster["proposal"]
        elif parsed["kind"] == "logo":
            proposal.update(_logo_proposal(page, float(parsed["scale"]), str(parsed.get("direction") or "")))
            if proposal.get("error"):
                return {"ok": False, "reply": proposal["error"], "actions": []}
        elif parsed["kind"] == "shrink":
            proposal.update({"mode": "shrink", "amber": False, "count": 1})
        else:
            moved = _move_proposal(page, spans)
            if moved.get("error"):
                return {"ok": False, "reply": moved["error"], "actions": []}
            for move in moved.get("moves") or []:
                fontfile = _embedded_font(doc, page, move.get("font") or "")
                if not fontfile:
                    return {
                        "ok": False,
                        "reply": f"The text uses {move.get('font') or 'an unknown font'}, and I don't have that font. I have not substituted another one.",
                        "actions": [],
                    }
                move["fontfile"] = fontfile
            proposal.update(moved)
    finally:
        doc.close()

    os.makedirs(preview_dir, exist_ok=True)
    before = os.path.join(preview_dir, "before.png")
    after = os.path.join(preview_dir, "after.png")
    staged = os.path.join(preview_dir, "staged.pdf")
    _render_page(src, before)
    _apply_proposal(src, staged, proposal)
    _render_page(staged, after)
    proposal["staged"] = staged
    _save(key, {"proposal": proposal, "undo": "", "current": src})
    amber = ""
    if proposal.get("amber"):
        amber = f" Amber for the designer: the closest font is {os.path.basename(proposal.get('fontfile') or '')}, and it is not an exact match."
    if proposal.get("mode") == "raster-text":
        font_note = " The new words are set as vector type in the closest font."
    elif proposal.get("mode") == "live-text":
        font_note = " The letters stay in the file's own font."
    else:
        font_note = ""
    reply = _preview_sentence(proposal) + font_note + amber + " Here is the before and after. I have not applied it yet."
    return {
        "ok": True,
        "reply": reply,
        "actions": [
            {"id": "confirm-edit", "label": "Apply this change"},
            {"id": "cancel-edit", "label": "Leave it as it is"},
        ],
        "previewSides": ["before", "after"],
        "pending": True,
    }


def _preview_sentence(proposal: dict) -> str:
    kind = proposal.get("kind")
    shown_old = proposal.get("shownOld") or proposal.get("old")
    shown_new = proposal.get("shownNew") or proposal.get("new")
    if kind == "phone":
        return f"I would change the phone number from {shown_old} to {shown_new}, in the same format."
    if kind == "date":
        return f"I would change the date from {shown_old} to {shown_new}, in the same format."
    if kind == "time":
        return f"I would change the time from {shown_old} to {shown_new}, in the same format."
    if kind == "venue":
        return f"I would change the venue from {shown_old} to {shown_new}, in the same style."
    if kind == "logo" and proposal.get("direction"):
        return f"I would move the logo {proposal.get('direction')}."
    if kind == "logo":
        return f"I would make the logo {proposal.get('scale')} times its current size."
    if kind == "heading":
        return f"I would make the heading \"{proposal.get('old')}\" {proposal.get('word') or 'bigger'}, in the same font."
    if kind == "move":
        return f"I would move {proposal.get('count', 1)} text item(s) in from the edge. The words stay the same."
    if kind == "shrink":
        return "I would shrink the artwork into the safe zone, about 3 mm inside the trim, and show you the preview first."
    return "I would make that one change."


def _logo_proposal(page, scale: float, direction: str = "") -> dict:
    infos = page.get_image_info(xrefs=True) or []
    page_area = float(page.rect.width * page.rect.height) or 1.0
    logos = []
    for info in infos:
        box = info.get("bbox") or (0, 0, 0, 0)
        area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
        if area < 8 or area > page_area * 0.45:
            continue
        logos.append((area, info))
    if not logos:
        return {"error": "I can't see a separate logo on this page, so I have not changed anything."}
    info = min(logos, key=lambda item: item[0])[1]
    box = list(info["bbox"])
    if direction:
        step = 8.0 * 72.0 / 25.4
        dx = {"left": -step, "right": step}.get(direction, 0.0)
        dy = {"up": -step, "down": step}.get(direction, 0.0)
        grown = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
        shift_x = 0.0
        shift_y = 0.0
        if grown[0] < page.rect.x0:
            shift_x = page.rect.x0 - grown[0]
        elif grown[2] > page.rect.x1:
            shift_x = page.rect.x1 - grown[2]
        if grown[1] < page.rect.y0:
            shift_y = page.rect.y0 - grown[1]
        elif grown[3] > page.rect.y1:
            shift_y = page.rect.y1 - grown[3]
        grown = [grown[0] + shift_x, grown[1] + shift_y, grown[2] + shift_x, grown[3] + shift_y]
        if abs(grown[0] - box[0]) < 0.4 and abs(grown[1] - box[1]) < 0.4:
            return {"error": "The logo is already against that edge, so I have not moved it."}
        return {
            "mode": "logo",
            "xref": int(info.get("xref") or 0),
            "bbox": box,
            "grown": grown,
            "scale": 1.0,
            "direction": direction,
            "amber": False,
        }
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    half_w = (box[2] - box[0]) * scale / 2.0
    half_h = (box[3] - box[1]) * scale / 2.0
    grown = [
        max(page.rect.x0, cx - half_w),
        max(page.rect.y0, cy - half_h),
        min(page.rect.x1, cx + half_w),
        min(page.rect.y1, cy + half_h),
    ]
    return {
        "mode": "logo",
        "xref": int(info.get("xref") or 0),
        "bbox": box,
        "grown": grown,
        "scale": scale,
        "direction": "",
        "amber": False,
    }


def _move_proposal(page, spans: list[dict]) -> dict:
    if not spans:
        return {"error": "There is no live text to move. I have not changed the picture."}
    limit = 5.0 * 72.0 / 25.4
    trim = page.trimbox if page.trimbox.width > 2 else page.rect
    moves = []
    for span in spans:
        box = span["bbox"]
        dx = dy = 0.0
        if box[0] < trim.x0 + limit:
            dx = (trim.x0 + limit) - box[0]
        elif box[2] > trim.x1 - limit:
            dx = (trim.x1 - limit) - box[2]
        if box[1] < trim.y0 + limit:
            dy = (trim.y0 + limit) - box[1]
        elif box[3] > trim.y1 - limit:
            dy = (trim.y1 - limit) - box[3]
        if abs(dx) < 0.2 and abs(dy) < 0.2:
            continue
        moves.append({
            "old": span["text"],
            "bbox": list(box),
            "origin": list(span.get("origin") or (box[0], box[3])),
            "dx": dx,
            "dy": dy,
            "size": float(span.get("size") or 12),
            "font": str(span.get("font") or ""),
            "color": int(span.get("color") or 0),
        })
    if not moves:
        return {"error": "The live text is already away from the edge. I have not moved anything."}
    return {"mode": "move", "moves": moves, "count": len(moves), "amber": False}


def _select_hit(words: list[dict], parsed: dict) -> dict | None:
    def boxed(group: list[dict], joined: str) -> dict:
        group.sort(key=lambda item: item["left"])
        return {
            "text": joined,
            "left": group[0]["left"],
            "top": min(item["top"] for item in group),
            "width": max(item["left"] + item["width"] for item in group) - group[0]["left"],
            "height": max(item["top"] + item["height"] for item in group) - min(item["top"] for item in group),
        }

    if parsed["kind"] in ("phone", "date", "time", "venue"):
        singles = [word for word in words if fragment(word.get("text") or "", parsed["kind"])]
        if singles:
            # The number's own box, not a neighbouring line that happens to share its height.
            word = min(singles, key=lambda item: (len(item["text"]), item["width"] * item["height"]))
            return {
                "text": word["text"],
                "left": word["left"],
                "top": word["top"],
                "width": word["width"],
                "height": word["height"],
            }
    hit = None
    if parsed["kind"] == "heading":
        for group in _line_groups(words):
            joined = " ".join(item["text"] for item in sorted(group, key=lambda item: item["left"]))
            if sum(ch.isalpha() for ch in joined) < 3:
                continue
            candidate = boxed(group, joined)
            if hit is None or candidate["height"] > hit["height"]:
                hit = candidate
        return hit
    pattern = {"phone": PHONE_IN_TEXT, "date": DATE_IN_TEXT, "time": TIME_IN_TEXT, "venue": VENUE_IN_TEXT}.get(parsed["kind"])
    if pattern is None:
        return None
    for group in _line_groups(words):
        joined = " ".join(item["text"] for item in sorted(group, key=lambda item: item["left"]))
        if not pattern.search(joined):
            continue
        candidate = boxed(group, joined)
        if hit is None or len(candidate["text"]) > len(hit["text"]):
            hit = candidate
    return hit


def _embedded_hit(doc, page, parsed: dict, render_scale: float) -> dict | None:
    """Read the placed picture at its own pixels, then map the box onto the page render."""
    import cv2

    infos = [info for info in (page.get_image_info(xrefs=True) or []) if info.get("xref")]
    if not infos:
        return None
    info = max(infos, key=lambda item: max(0.0, (item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1])))
    try:
        raw = doc.extract_image(int(info["xref"])) or {}
    except Exception:
        return None
    blob = raw.get("image")
    if not blob:
        return None
    folder = tempfile.mkdtemp(prefix="edit-src-")
    path = os.path.join(folder, "art." + str(raw.get("ext") or "png"))
    with open(path, "wb") as handle:
        handle.write(blob)
    image = cv2.imread(path)
    if image is None:
        return None
    hit = _select_hit(_ocr_words(path), parsed)
    if not hit:
        return None
    height, width = image.shape[:2]
    box = info["bbox"]
    sx = (box[2] - box[0]) / float(width or 1)
    sy = (box[3] - box[1]) / float(height or 1)
    x0 = box[0] + hit["left"] * sx
    y0 = box[1] + hit["top"] * sy
    x1 = box[0] + (hit["left"] + hit["width"]) * sx
    y1 = box[1] + (hit["top"] + hit["height"]) * sy
    crop = image[hit["top"]: hit["top"] + hit["height"], hit["left"]: hit["left"] + hit["width"]]
    return {
        "text": hit["text"],
        "left": int(round(x0 * render_scale)),
        "top": int(round(y0 * render_scale)),
        "width": max(1, int(round((x1 - x0) * render_scale))),
        "height": max(1, int(round((y1 - y0) * render_scale))),
        "crop": crop,
    }


def _propose_raster(src: str, parsed: dict, proposal: dict) -> dict:
    import cv2
    import pymupdf as fitz

    embedded = None
    doc = fitz.open(src)
    try:
        page = doc[0]
        long_pt = max(float(page.rect.width), float(page.rect.height)) or 1.0
        scale = 2.0
        if long_pt * scale < 700:
            scale = min(6.0, 1200.0 / long_pt)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        folder = tempfile.mkdtemp(prefix="edit-ocr-")
        png = os.path.join(folder, "page.png")
        pix.save(png)
        embedded = _embedded_hit(doc, page, parsed, scale)
    finally:
        doc.close()
    if embedded:
        hit = embedded
        crop = embedded.get("crop")
    else:
        hit = _select_hit(_ocr_words(png), parsed)
        image = cv2.imread(png)
        crop = None if hit is None or image is None else image[hit["top"]: hit["top"] + hit["height"], hit["left"]: hit["left"] + hit["width"]]
    if not hit:
        label = "a heading" if parsed["kind"] == "heading" else "a " + FIELD_NAME.get(parsed["kind"], "text")
        return {
            "ok": False,
            "reply": f"I read the picture and could not find {label}, so I have not changed anything.",
            "actions": [],
        }
    shown_old = hit["text"]
    shown_new = hit["text"]
    if parsed["kind"] == "heading":
        new_text = hit["text"]
    else:
        try:
            swapped = swap_text(hit["text"], parsed["kind"], parsed["new"])
        except ValueError:
            label = FIELD_NAME.get(parsed["kind"], "value")
            return {"ok": False, "reply": f"I couldn't read that {label}, so I have not changed anything.", "actions": []}
        if not swapped:
            label = FIELD_NAME.get(parsed["kind"], "text")
            return {"ok": False, "reply": f"I read the picture and could not find a {label}, so I have not changed anything.", "actions": []}
        shown_old, shown_new, new_text = swapped
    fontfile, score = _closest_font(crop, hit["text"])
    if not fontfile:
        return {"ok": False, "reply": "I found the words but I have no font to set them in. Nothing was changed.", "actions": []}
    missing = _missing_glyphs(fontfile, new_text + _stray_chars(parsed["kind"], parsed.get("new") or ""))
    if missing:
        return {
            "ok": False,
            "reply": f"The closest font has no glyph for {''.join(missing)}. I have not substituted a different font.",
            "actions": [],
        }
    proposal.update({
        "mode": "raster-text",
        "old": hit["text"],
        "new": new_text,
        "shownOld": shown_old,
        "shownNew": shown_new,
        "pixelBox": [hit["left"], hit["top"], hit["width"], hit["height"]],
        "renderScale": scale,
        "fontfile": fontfile,
        "amber": score > 12.0,
        "fontScore": round(score, 1),
        "kind": parsed["kind"],
        "sizeFactor": float(parsed.get("factor") or 1),
        "word": parsed.get("word") or "",
    })
    return {"proposal": proposal}


def _shrink_page(doc, page) -> None:
    """Place the whole page about 3 mm inside itself so type clears the trim."""
    import pymupdf as fitz

    inset = 3.0 * 72.0 / 25.4
    inner = fitz.Rect(page.rect.x0 + inset, page.rect.y0 + inset, page.rect.x1 - inset, page.rect.y1 - inset)
    if inner.width < 8 or inner.height < 8:
        return
    copied = fitz.open()
    try:
        copied.insert_pdf(doc, from_page=page.number, to_page=page.number)
        page.clean_contents()
        page.add_redact_annot(page.rect, fill=(1, 1, 1))
        page.apply_redactions(images=2, graphics=2, text=0)
        page.show_pdf_page(inner, copied, 0)
    finally:
        copied.close()


def _apply_proposal(src: str, dest: str, proposal: dict) -> None:
    import pymupdf as fitz

    doc = fitz.open(src)
    try:
        page = doc[0]
        mode = proposal.get("mode")
        if mode == "live-text":
            _replace_span(page, proposal)
        elif mode == "shrink":
            _shrink_page(doc, page)
        elif mode == "move":
            for move in proposal.get("moves") or []:
                _replace_span(page, {
                    **move,
                    "new": move["old"],
                    "origin": [move["origin"][0] + move["dx"], move["origin"][1] + move["dy"]],
                    "fontfile": move.get("fontfile") or "helv",
                })
        elif mode == "logo":
            _resize_logo(doc, page, proposal)
        elif mode == "raster-text":
            _paint_raster_text(doc, page, proposal)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        doc.save(dest, garbage=4, deflate=True)
    finally:
        doc.close()


def _color_tuple(value: int) -> tuple[float, float, float]:
    return ((value >> 16) & 255) / 255.0, ((value >> 8) & 255) / 255.0, (value & 255) / 255.0


def _nearby_fill(page, box) -> tuple[float, float, float]:
    """Median colour just outside the box, so the hole matches the page."""
    import pymupdf as fitz
    import numpy as np

    above = fitz.Rect(box.x0, box.y0 - 3, box.x1, box.y0 - 0.4) & page.rect
    if above.width < 0.4 or above.height < 0.4:
        above = fitz.Rect(box.x0 - 3, box.y0, box.x0 - 0.4, box.y1) & page.rect
    if above.width < 0.4 or above.height < 0.4:
        return (1, 1, 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=above, alpha=False, colorspace=fitz.csRGB)
    samples = np.frombuffer(pix.samples, dtype=np.uint8)
    if samples.size < 3:
        return (1, 1, 1)
    med = np.median(samples.reshape(-1, pix.n)[:, :3].astype(np.float32), axis=0)
    return (float(med[0]) / 255.0, float(med[1]) / 255.0, float(med[2]) / 255.0)


def _replace_span(page, proposal: dict) -> None:
    import pymupdf as fitz

    raw = fitz.Rect(proposal["bbox"])
    box = fitz.Rect(raw.x0 - 0.3, raw.y0 - 0.3, raw.x1 + 0.3, raw.y1 + 0.3) & page.rect
    page.add_redact_annot(box, fill=_nearby_fill(page, raw))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    origin = proposal.get("origin") or (box.x0, box.y1 - 1)
    kwargs = {
        "fontsize": float(proposal.get("size") or 12),
        "color": _color_tuple(int(proposal.get("color") or 0)),
    }
    fontfile = proposal.get("fontfile") or ""
    if fontfile in ("helv", "times", "cour"):
        kwargs["fontname"] = fontfile
    elif fontfile:
        kwargs["fontfile"] = fontfile
    else:
        kwargs["fontname"] = "helv"
    page.insert_text((float(origin[0]), float(origin[1])), str(proposal.get("new") or ""), **kwargs)


def _resize_logo(doc, page, proposal: dict) -> None:
    import pymupdf as fitz

    xref = int(proposal.get("xref") or 0)
    stream = None
    if xref:
        try:
            stream = (doc.extract_image(xref) or {}).get("image")
        except Exception:
            stream = None
    old = fitz.Rect(proposal["bbox"])
    page.add_redact_annot(old, fill=_nearby_fill(page, old))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
    if stream:
        page.insert_image(fitz.Rect(proposal["grown"]), stream=stream)
        return
    page.draw_rect(fitz.Rect(proposal["grown"]), color=(0, 0, 0), fill=(0, 0, 0), width=0)


def _paint_raster_text(doc, page, proposal: dict) -> None:
    import cv2
    import numpy as np
    import pymupdf as fitz

    scale = float(proposal.get("renderScale") or 2.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
    left, top, width, height = [int(item) for item in proposal["pixelBox"]]
    y0, y1 = max(0, top), min(image.shape[0], top + height)
    x0, x1 = max(0, left), min(image.shape[1], left + width)
    border = np.concatenate([
        image[max(0, y0 - 2): y0, x0:x1].reshape(-1, 3),
        image[y1: min(image.shape[0], y1 + 2), x0:x1].reshape(-1, 3),
    ]) if y1 > y0 and x1 > x0 else np.zeros((1, 3), np.uint8)
    fill = np.median(border.astype(np.float32), axis=0) if len(border) else np.array([255, 255, 255])
    image[y0:y1, x0:x1] = np.clip(np.round(fill), 0, 255).astype(np.uint8)
    painted = fitz.Pixmap(fitz.csRGB, pix.w, pix.h, image.tobytes(), 0)
    page.clean_contents()
    # Cover the page image with the painted pixels, then set the new words as live text.
    page.insert_image(page.rect, pixmap=painted, overlay=True)
    origin_x = left / scale
    origin_y = (top + height * 0.85) / scale
    size = max(8.0, (height / scale) * 0.8) * float(proposal.get("sizeFactor") or 1.0)
    page.insert_text(
        (origin_x, origin_y),
        str(proposal.get("new") or ""),
        fontsize=size,
        fontfile=proposal.get("fontfile") or FONT_CANDIDATES[0],
        color=(0, 0, 0),
    )


def confirm(key: str, dest: str) -> dict:
    state = _load(key)
    proposal = state.get("proposal") or {}
    staged = proposal.get("staged") or ""
    if not proposal or not staged or not os.path.isfile(staged):
        return {"ok": False, "reply": "There is no change waiting. Tell me what to edit first.", "actions": []}
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    undo = dest + ".undo.pdf"
    if os.path.isfile(dest):
        shutil.copyfile(dest, undo)
    else:
        shutil.copyfile(proposal.get("src") or staged, undo)
    shutil.copyfile(staged, dest)
    state["undo"] = undo
    state["current"] = dest
    state["proposal"] = {}
    _save(key, state)
    amber = " The font match is amber for the designer." if proposal.get("amber") else ""
    return {
        "ok": True,
        "reply": "Applied. Only that change was made." + amber + " Say undo if you want it back.",
        "actions": [{"id": "undo-edit", "label": "Undo"}],
        "path": dest,
    }


def cancel(key: str) -> dict:
    state = _load(key)
    state["proposal"] = {}
    _save(key, state)
    return {"ok": True, "reply": "Left the file as it was. Nothing was applied.", "actions": []}


def undo(key: str, dest: str) -> dict:
    state = _load(key)
    previous = state.get("undo") or ""
    if not previous or not os.path.isfile(previous):
        return {"ok": False, "reply": "There is nothing to undo.", "actions": []}
    shutil.copyfile(previous, dest)
    state["undo"] = ""
    state["current"] = dest
    _save(key, state)
    return {"ok": True, "reply": "Undone. The file is back to the previous version.", "path": dest, "actions": []}
