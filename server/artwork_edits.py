#!/usr/bin/env python3
"""One small edit at a time, with a preview and an undo.

Live text stays in its own font when that face has the new letters. A Canva
subset usually does not: the full face is taken from the bundled font folder
(or C:\\Windows\\Fonts on a laptop) by the name after the subset tag. If that
face is not there, the closest bundled font is used and the reply says amber.
A picture gets only those word pixels inpainted from the background, then new vector text in the closest font.
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

_BUNDLED_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FIXTURE_FONTS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures", "fonts"))
_FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)
_WEIGHT_WORDS = (
    "extralight", "extrabold", "semibold", "demibold", "regular",
    "italic", "medium", "black", "bold", "light", "thin", "book",
)
_FONT_INDEX: list[dict] | None = None


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


def _font_token(name: str) -> str:
    """Family name without a PDF subset tag. ABCDEF+Anton-Regular is Anton."""
    tail = str(name or "").split("+")[-1]
    return re.sub(r"[^a-z0-9]", "", tail.lower())


def _family_token(name: str) -> str:
    token = _font_token(name)
    for word in _WEIGHT_WORDS:
        token = token.replace(word, "")
    return token


def _weight_hint(name: str) -> str:
    token = _font_token(name)
    for word in ("black", "extrabold", "semibold", "bold", "medium", "light", "thin", "italic", "regular", "book"):
        if word in token:
            return word
    return "regular"


def _windows_font_dirs() -> list[str]:
    """Laptop faces live in the Windows font folder. The path is checked when it exists."""
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    folders = [os.path.join(windir, "Fonts")]
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        folders.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return folders


def _bundled_font_files() -> list[str]:
    found = []
    seen = set()
    for folder in (_BUNDLED_FONTS, _FIXTURE_FONTS):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith((".ttf", ".otf")):
                continue
            path = os.path.join(folder, name)
            real = os.path.abspath(path)
            if real in seen:
                continue
            seen.add(real)
            found.append(path)
    for path in _FALLBACK_FONTS:
        real = os.path.abspath(path)
        if os.path.isfile(path) and real not in seen:
            seen.add(real)
            found.append(path)
    return found


def _static_face(path: str) -> str:
    """A variable face whose default master is not regular is instanced at use.

    Raleway ships as the original variable file (the name is reserved). Its
    default master is Thin, so the words are set from a regular-weight instance
    that is built beside the temp folder and is not a second copy in the app.
    """
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return path
    try:
        font = TTFont(path)
    except Exception:
        return path
    if "fvar" not in font:
        font.close()
        return path
    axes = {axis.axisTag: axis for axis in font["fvar"].axes}
    loc = {}
    if "wght" in axes and abs(float(axes["wght"].defaultValue) - 400.0) > 0.5:
        loc["wght"] = 400
    if "wdth" in axes and abs(float(axes["wdth"].defaultValue) - 100.0) > 0.5:
        loc["wdth"] = 100
    if "opsz" in axes and abs(float(axes["opsz"].defaultValue) - 14.0) > 0.5:
        loc["opsz"] = 14
    font.close()
    if not loc:
        return path
    folder = os.path.join(tempfile.gettempdir(), "flyerz-font-static")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.splitext(os.path.basename(path))[0].replace("[", "").replace("]", "") + "-regular.ttf")
    if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
        return dest
    from fontTools.varLib.instancer import instantiateVariableFont

    source = TTFont(path)
    static = instantiateVariableFont(source, loc, inplace=False, updateFontNames=True)
    source.close()
    static.save(dest)
    static.close()
    return dest


def _face_names(path: str) -> tuple[str, str]:
    try:
        from PIL import ImageFont

        family, style = ImageFont.truetype(path).getname()
        return _family_token(family), _weight_hint(f"{style} {family}")
    except Exception:
        stem = os.path.splitext(os.path.basename(path))[0]
        return _family_token(stem), _weight_hint(stem)


def _font_index() -> list[dict]:
    global _FONT_INDEX
    if _FONT_INDEX is not None:
        return _FONT_INDEX
    rows = []
    seen = set()
    for path in _bundled_font_files():
        use = _static_face(path)
        real = os.path.abspath(use)
        if real in seen:
            continue
        seen.add(real)
        family, style = _face_names(use)
        kind = "system" if os.path.abspath(path).startswith("/usr/share/fonts") else "bundled"
        rows.append({"path": use, "kind": kind, "family": family, "style": style})
    for folder in _windows_font_dirs():
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            path = os.path.join(folder, name)
            real = os.path.abspath(path)
            if real in seen or not os.path.isfile(path):
                continue
            seen.add(real)
            family, style = _face_names(path)
            rows.append({"path": path, "kind": "windows", "family": family, "style": style})
    _FONT_INDEX = rows
    return rows


def _candidate_fonts() -> list[str]:
    return [row["path"] for row in _font_index() if row["kind"] in ("bundled", "system")]


def _named_font(fontname: str) -> str | None:
    """Full face for a subset tag. PWSYDC+Anton-Regular is the bundled Anton."""
    wanted = _family_token(fontname)
    if len(wanted) < 3:
        return None
    hint = _weight_hint(fontname)
    best = None
    best_key = (9, 9)
    for row in _font_index():
        if row["family"] != wanted:
            continue
        style_rank = 0 if row["style"] == hint or (hint == "regular" and row["style"] in ("regular", "book")) else 1
        kind_rank = 0 if row["kind"] == "bundled" else 1 if row["kind"] == "windows" else 2
        key = (style_rank, kind_rank)
        if key < best_key:
            best_key = key
            best = row["path"]
    return best


def _glyphs_present(fontfile: str, text: str) -> bool:
    if not fontfile or not text:
        return False
    import pymupdf as fitz

    if fontfile in ("helv", "times", "cour"):
        built = fitz.Font(fontfile)
        return all(char.isspace() or built.has_glyph(ord(char)) for char in text)
    return not _missing_glyphs(fontfile, text)


def _closest_among(crop, text: str, paths: list[str]) -> tuple[str, float]:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    usable = [path for path in paths if os.path.isfile(path)]
    if not usable:
        return "", 999.0
    if crop is None or getattr(crop, "size", 0) == 0:
        return usable[0], 999.0
    best_path = usable[0]
    best_score = 1e9
    height = max(12, crop.shape[0])
    for path in usable:
        try:
            font = ImageFont.truetype(path, max(10, height - 2))
        except Exception:
            continue
        canvas = Image.new("RGB", (max(crop.shape[1], 8), max(height, 8)), (255, 255, 255))
        ImageDraw.Draw(canvas).text((0, 0), text, font=font, fill=(0, 0, 0))
        plate = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        plate = cv2.resize(plate, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(np.mean(np.abs(plate.astype(np.float32) - crop.astype(np.float32))))
        if score < best_score:
            best_score = score
            best_path = path
    return best_path, best_score


def _span_crop(page, bbox) -> object:
    import pymupdf as fitz
    import numpy as np

    rect = fitz.Rect(bbox)
    if rect.width < 1 or rect.height < 1:
        return None
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect & page.rect, alpha=False, colorspace=fitz.csRGB)
    if pix.width < 1 or pix.height < 1:
        return None
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()


def _resolve_face(doc, page, fontname: str, text: str, bbox=None) -> dict:
    """Embedded face, then the full family, then the closest face that has the letters."""
    embedded = _embedded_font(doc, page, fontname)
    if _glyphs_present(embedded or "", text):
        return {"fontfile": embedded, "amber": False, "fontSource": "embedded"}
    full = _named_font(fontname)
    if full and _glyphs_present(full, text):
        return {"fontfile": full, "amber": False, "fontSource": "full"}
    paths = [row["path"] for row in _font_index() if row["kind"] in ("bundled", "system") and _glyphs_present(row["path"], text)]
    if not paths:
        missing = _missing_glyphs(embedded, text) if embedded and embedded not in ("helv", "times", "cour") else [
            char for char in text if not char.isspace()
        ]
        shown = "".join(dict.fromkeys(missing))[:24] or "the new letters"
        return {
            "fontfile": "",
            "amber": False,
            "fontSource": "none",
            "reply": (
                f"The font {fontname or 'on the page'} has no glyph for {shown}, "
                "and none of the installed fonts do either. Nothing was changed."
            ),
        }
    crop = _span_crop(page, bbox) if bbox else None
    path, score = _closest_among(crop, text, paths)
    return {"fontfile": path, "amber": True, "fontSource": "closest", "fontScore": round(score, 1)}


def _span_rgb(page, span) -> tuple[tuple[float, float, float], bool]:
    """RGB of this span, and whether that black has to stay K-only."""
    rgb = _color_tuple(int(span.get("color") or 0))
    text = str(span.get("text") or "").strip()
    try:
        for item in page.get_texttrace() or []:
            chars = item.get("chars") or []
            got = "".join(chr(ch[0]) for ch in chars if ch).strip()
            if got != text:
                continue
            color = item.get("color") or ()
            if len(color) >= 4 and int(item.get("colorspace") or 0) == 4:
                cyan, magenta, yellow, black = (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
                if cyan <= 0.02 and magenta <= 0.02 and yellow <= 0.02 and black >= 0.9:
                    return (0.0, 0.0, 0.0), True
                if cyan <= 0.02 and magenta <= 0.02 and yellow <= 0.02 and black <= 0.02:
                    return (1.0, 1.0, 1.0), False
            if len(color) >= 3:
                rgb = (float(color[0]), float(color[1]), float(color[2]))
            break
    except Exception:
        pass
    channels = [int(round(channel * 255)) for channel in rgb]
    k_only = max(channels) <= 40 and (max(channels) - min(channels)) <= 18
    return rgb, k_only


def _ink_operator(rgb, size: float) -> str:
    """Same text ink as the press file. Near-black stays 100% K. White stays unprinted."""
    red_i = int(round(float(rgb[0]) * 255))
    green_i = int(round(float(rgb[1]) * 255))
    blue_i = int(round(float(rgb[2]) * 255))
    if max(red_i, green_i, blue_i) <= 40 and (max(red_i, green_i, blue_i) - min(red_i, green_i, blue_i)) <= 18 and float(size) < 56:
        return "0 0 0 1 k"
    if min(red_i, green_i, blue_i) >= 250:
        return "0 0 0 0 k"
    cyan = 1.0 - red_i / 255.0
    magenta = 1.0 - green_i / 255.0
    yellow = 1.0 - blue_i / 255.0
    black = min(cyan, magenta, yellow)
    if black >= 0.999:
        cyan = magenta = yellow = 0.0
    else:
        cyan = (cyan - black) / (1.0 - black)
        magenta = (magenta - black) / (1.0 - black)
        yellow = (yellow - black) / (1.0 - black)
    total = (cyan + magenta + yellow + black) * 100.0
    if total > 300.0:
        cmy = (cyan + magenta + yellow) * 100.0
        if cmy > 0:
            scale = max(0.0, (cmy - (total - 300.0)) / cmy)
            cyan *= scale
            magenta *= scale
            yellow *= scale
    return f"{cyan:.4f} {magenta:.4f} {yellow:.4f} {black:.4f} k"


def _embedded_font(doc, page, fontname: str) -> str | None:
    import pymupdf as fitz

    wanted = _font_token(fontname)
    for item in page.get_fonts() or []:
        xref = int(item[0])
        names = [_font_token(part) for part in item[3:]]
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
    """The bundled face whose ink overlaps the word. Lower score is closer.

    A flat colour difference lets a script face win on a pale crop. The overlap
    of the dark (or light) strokes follows the letters.
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    paths = _candidate_fonts()
    if crop is None or getattr(crop, "size", 0) == 0:
        path = next((item for item in paths if os.path.isfile(item)), "")
        return path, 999.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    # The letters are the minority: darker on a light ground, or lighter on a dark one.
    if float(gray.mean()) > 140:
        mask = gray < 160
    else:
        mask = gray > 140
    best_path = ""
    best_iou = -1.0
    draw_h = max(48, int(mask.shape[0]))
    for path in paths:
        if not os.path.isfile(path):
            continue
        font = ImageFont.truetype(path, draw_h)
        canvas = Image.new("L", (draw_h * max(8, len(text) + 1), draw_h + 8), 255)
        ImageDraw.Draw(canvas).text((0, 2), text, font=font, fill=0)
        plate = np.array(canvas)
        cols = np.where(plate.min(axis=0) < 200)[0]
        rows = np.where(plate.min(axis=1) < 200)[0]
        if len(cols) == 0 or len(rows) == 0:
            continue
        glyph = plate[rows.min(): rows.max() + 1, cols.min(): cols.max() + 1]
        glyph = cv2.resize(glyph, (mask.shape[1], mask.shape[0]), interpolation=cv2.INTER_AREA)
        ink = glyph < 170
        union = int(np.logical_or(mask, ink).sum()) or 1
        iou = float(np.logical_and(mask, ink).sum()) / float(union)
        if iou > best_iou:
            best_iou = iou
            best_path = path
    # Callers treat a high score as a poor match. 0 would be a perfect overlap.
    score = 999.0 if best_iou < 0 else (1.0 - best_iou) * 100.0
    return best_path, score


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
                probe = new_text + _stray_chars(parsed["kind"], parsed.get("new") or "")
                face = _resolve_face(doc, page, str(span.get("font") or ""), probe, span.get("bbox"))
                if face.get("fontSource") == "none":
                    return {"ok": False, "reply": face.get("reply") or "Nothing was changed.", "actions": []}
                rgb, k_only = _span_rgb(page, span)
                bbox = list(span["bbox"])
                center = (float(bbox[0]) + float(bbox[2])) / 2.0
                mid = (float(page.rect.x0) + float(page.rect.x1)) / 2.0
                proposal.update({
                    "mode": "live-text",
                    "old": old,
                    "new": new_text,
                    "shownOld": shown_old,
                    "shownNew": shown_new,
                    "bbox": bbox,
                    "origin": list(span.get("origin") or (span["bbox"][0], span["bbox"][3])),
                    "align": "center" if abs(center - mid) <= 14 else "left",
                    "size": float(span.get("size") or 12) * (float(parsed["factor"]) if parsed["kind"] == "heading" else 1.0),
                    "font": str(span.get("font") or ""),
                    "fontfile": face["fontfile"],
                    "fontSource": face.get("fontSource") or "",
                    "color": int(span.get("color") or 0),
                    "rgb": [rgb[0], rgb[1], rgb[2]],
                    "kOnly": k_only,
                    "amber": bool(face.get("amber")),
                    "fontScore": face.get("fontScore"),
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
                face = _resolve_face(doc, page, move.get("font") or "", str(move.get("old") or ""), move.get("bbox"))
                if face.get("fontSource") == "none":
                    return {"ok": False, "reply": face.get("reply") or "Nothing was changed.", "actions": []}
                move["fontfile"] = face["fontfile"]
                move["fontSource"] = face.get("fontSource") or ""
                if face.get("amber"):
                    moved["amber"] = True
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
    if proposal.get("fontSource") == "full":
        face_name = os.path.splitext(os.path.basename(proposal.get("fontfile") or ""))[0].replace("-", " ")
        font_note = f" The new words are set in {face_name}."
    elif proposal.get("mode") == "raster-text" or proposal.get("amber"):
        font_note = " The new words are set as vector type in the closest font."
    elif proposal.get("mode") == "live-text":
        font_note = " The letters stay in the file's own font."
    else:
        font_note = ""
    fit_note = ""
    if proposal.get("shrunk"):
        fit_note = " I made the words smaller so they stay inside the column."
    reply = _preview_sentence(proposal) + font_note + amber + fit_note + " Here is the before and after. I have not applied it yet."
    return {
        "ok": True,
        "reply": reply,
        "actions": [
            {"id": "confirm-edit", "label": "Apply this change"},
            {"id": "cancel-edit", "label": "Leave it as it is"},
        ],
        "previewSides": ["before", "after"],
        "pending": True,
        "editBox": proposal.get("guardBox") or proposal.get("editBox"),
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


def _phone_run(group: list[dict]) -> dict | None:
    """The digit boxes of a phone number, without an icon read as empty brackets."""
    ordered = sorted(group, key=lambda item: item["left"])
    digit_at = [index for index, item in enumerate(ordered) if re.search(r"\d", str(item.get("text") or ""))]
    if not digit_at:
        return None
    chosen = []
    for item in ordered[digit_at[0]:digit_at[-1] + 1]:
        text = str(item.get("text") or "")
        if re.search(r"\d", text):
            chosen.append(item)
            continue
        if re.fullmatch(r"[\s().+\-]*", text) and not re.fullmatch(r"\(\s*\)", text.strip()):
            chosen.append(item)
    if not chosen:
        return None
    joined = " ".join(str(item.get("text") or "") for item in chosen)
    return {
        "text": joined,
        "left": chosen[0]["left"],
        "top": min(item["top"] for item in chosen),
        "width": max(item["left"] + item["width"] for item in chosen) - chosen[0]["left"],
        "height": max(item["top"] + item["height"] for item in chosen) - min(item["top"] for item in chosen),
    }


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
            text = fragment(word.get("text") or "", parsed["kind"]) or word["text"]
            return {
                "text": text,
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
        if parsed["kind"] == "phone":
            candidate = _phone_run(group)
            if candidate is None or not fragment(candidate["text"], "phone"):
                continue
        else:
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


def _recolor_new_text(page, before: list, operator: str) -> None:
    """The new words only. The rest of the page keeps the colours it already had."""
    import re

    doc = page.parent
    fresh = [xref for xref in (page.get_contents() or []) if xref not in before]
    color_re = re.compile(r"[0-9]*\.?[0-9]+ [0-9]*\.?[0-9]+ [0-9]*\.?[0-9]+ rg\b")
    for xref in fresh:
        try:
            data = doc.xref_stream(int(xref))
        except Exception:
            continue
        if not data:
            continue
        text = data.decode("latin1", "replace")
        rewritten = color_re.sub(operator, text, count=1)
        if rewritten != text:
            doc.update_stream(int(xref), rewritten.encode("latin1"))


def _install_face(page, fontfile: str) -> str:
    """insert_text ignores a font file unless that face is already on the page."""
    if not fontfile or fontfile in ("helv", "times", "cour"):
        return fontfile or "helv"
    name = "E" + str(len(page.get_fonts() or []) + 1)
    page.insert_font(fontname=name, fontfile=fontfile)
    return name


def _place_font_text(page, point, text: str, size: float, rgb, fontfile: str) -> None:
    before = list(page.get_contents() or [])
    page.insert_text(
        (float(point[0]), float(point[1])),
        text,
        fontsize=float(size),
        fontname=_install_face(page, fontfile),
        color=(float(rgb[0]), float(rgb[1]), float(rgb[2])),
    )
    # Coloured type keeps its RGB. Black becomes 100% K, and white stays unprinted.
    operator = _ink_operator(rgb, size)
    if operator in ("0 0 0 1 k", "0 0 0 0 k"):
        _recolor_new_text(page, before, operator)


def _union_rect(rects: list):
    import pymupdf as fitz

    box = None
    for rect in rects:
        item = fitz.Rect(rect)
        if item.is_empty or item.width < 0.05 or item.height < 0.05:
            continue
        if box is None:
            box = item
        else:
            box = fitz.Rect(min(box.x0, item.x0), min(box.y0, item.y0), max(box.x1, item.x1), max(box.y1, item.y1))
    return box


def _trace_chars(page, old_text: str, loose) -> list:
    """Character quads for this line. One visual line can be several texttrace runs."""
    import pymupdf as fitz

    wanted = " ".join(str(old_text or "").split())
    if not wanted:
        return []
    loose_rect = fitz.Rect(loose) if loose else None
    exact = None
    partial = []
    for item in page.get_texttrace() or []:
        chars = [char for char in (item.get("chars") or []) if char]
        if not chars:
            continue
        text = "".join(chr(char[0]) for char in chars)
        norm = " ".join(text.split())
        if not norm:
            continue
        box = fitz.Rect(item.get("bbox") or chars[0][3])
        if loose_rect:
            center = fitz.Point((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0)
            if not loose_rect.contains(center):
                continue
        if norm == wanted:
            if exact is None or len(chars) > len(exact):
                exact = chars
            continue
        if norm in wanted:
            partial.append((box.x0, box.y0, box, chars))
    if exact:
        return exact
    def _inside(outer, inner) -> bool:
        return (
            outer.x0 <= inner.x0 + 0.2
            and outer.y0 <= inner.y0 + 0.2
            and outer.x1 >= inner.x1 - 0.2
            and outer.y1 >= inner.y1 - 0.2
            and (outer.width > inner.width + 0.4 or outer.height > inner.height + 0.4)
        )

    partial.sort(key=lambda item: len(item[3]), reverse=True)
    kept = []
    for _x, _y, box, group in partial:
        if any(_inside(other, box) for other, _chars in kept):
            continue
        kept.append((box, group))
    kept.sort(key=lambda item: (item[0].y0, item[0].x0))
    chars = []
    for _box, group in kept:
        chars.extend(group)
    return chars


def _other_span_rects(page, old_text: str) -> list:
    import pymupdf as fitz

    wanted = " ".join(str(old_text or "").split())
    boxes = []
    for block in (page.get_text("dict") or {}).get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = " ".join(str(span.get("text") or "").split())
                if not text or text == wanted:
                    continue
                boxes.append(fitz.Rect(span["bbox"]))
    return boxes


def _clear_of(rect, others, gap: float = 0.5):
    """Pull a box off every neighbouring line. A touch deletes that whole glyph.

    A line above or below is cut on the vertical edge. Its em-box is often
    wider than the words and sits to one side, and a sideways cut then misses
    the first glyph of the line being replaced.
    """
    import pymupdf as fitz

    if rect is None:
        return None
    current = fitz.Rect(rect)
    for _ in range(8):
        hit = False
        for other in others:
            overlap = current & other
            if overlap.is_empty or overlap.width < 0.05 or overlap.height < 0.05:
                continue
            other_y = (other.y0 + other.y1) / 2.0
            other_x = (other.x0 + other.x1) / 2.0
            own_x = (current.x0 + current.x1) / 2.0
            if other.y1 <= current.y0 + 0.2 or other_y < current.y0:
                current.y0 = other.y1 + gap
            elif other.y0 >= current.y1 - 0.2 or other_y > current.y1:
                current.y1 = other.y0 - gap
            elif other_x < own_x:
                current.x0 = other.x1 + gap
            else:
                current.x1 = other.x0 - gap
            hit = True
            if current.y1 - current.y0 < 0.35 or current.x1 - current.x0 < 0.25:
                return None
        if not hit:
            break
    if current.y1 - current.y0 < 0.35 or current.x1 - current.x0 < 0.25:
        return None
    return current


def _redact_hits(pieces, quad) -> bool:
    import pymupdf as fitz

    box = fitz.Rect(quad)
    for piece in pieces or []:
        hit = fitz.Rect(piece) & box
        if hit.width > 0.12 and hit.height > 0.12:
            return True
    return False


def _ink_and_redact(page, old_text: str, loose):
    """The drawn word box, and a slightly smaller box that does not touch other lines."""
    import pymupdf as fitz

    chars = _trace_chars(page, old_text, loose)
    quads = []
    origin = None
    for char in chars:
        if chr(char[0]).isspace():
            continue
        quads.append(fitz.Rect(char[3]))
        if origin is None:
            origin = (float(char[2][0]), float(char[2][1]))
    ink = _union_rect(quads)
    if ink is None and loose:
        ink = fitz.Rect(loose)
    if ink is None:
        return None, None, origin
    others = _other_span_rects(page, old_text)
    inset = 0.35
    shrunk = fitz.Rect(ink.x0 + inset, ink.y0 + inset, ink.x1 - inset, ink.y1 - inset)
    if shrunk.width < 0.4 or shrunk.height < 0.4:
        shrunk = fitz.Rect(ink)
    redact = _clear_of(shrunk, others, 0.5)
    pieces = []
    if isinstance(redact, list):
        pieces.extend(redact)
    elif redact is not None:
        pieces.append(redact)
    # Every old glyph still has to meet a redaction box, or that letter stays.
    for quad in quads:
        if _redact_hits(pieces, quad):
            continue
        piece = fitz.Rect(quad.x0 + 0.15, quad.y0 + 0.15, quad.x1 - 0.15, quad.y1 - 0.15)
        if piece.width < 0.2 or piece.height < 0.2:
            piece = fitz.Rect(quad)
        cleared = _clear_of(piece, others, 0.35)
        if cleared is not None and _redact_hits([cleared], quad):
            pieces.append(cleared)
    return ink, (pieces or None), origin


def _column_rect(page, word):
    """The panel that holds the words. A full-page wash is not a column."""
    import pymupdf as fitz

    if word is None:
        return fitz.Rect(page.rect)
    cx = (word.x0 + word.x1) / 2.0
    cy = (word.y0 + word.y1) / 2.0
    best = None
    page_box = fitz.Rect(page.rect)
    for drawing in page.get_drawings() or []:
        if not drawing.get("fill"):
            continue
        rect = fitz.Rect(drawing.get("rect") or page_box)
        if rect.width < 8 or rect.height < 8:
            continue
        if rect.width > page_box.width * 0.92 and rect.height > page_box.height * 0.92:
            continue
        if rect.x0 - 1 <= cx <= rect.x1 + 1 and rect.y0 - 1 <= cy <= rect.y1 + 1:
            if best is None or rect.width < best.width:
                best = rect
    return best or page_box


def _fit_on_line(page, text: str, old_text: str, size: float, fontfile: str, align: str, origin, ink) -> tuple:
    """Keep the baseline. A longer line that would leave its column is set smaller."""
    import pymupdf as fitz

    if fontfile in ("helv", "times", "cour"):
        face = fitz.Font(fontfile)
    elif fontfile:
        face = fitz.Font(fontfile=fontfile)
    else:
        face = fitz.Font("helv")
    size = float(size or 12)
    width = float(face.text_length(text, fontsize=size))
    old_width = float(face.text_length(old_text or text, fontsize=size))
    column = _column_rect(page, ink or fitz.Rect(page.rect))
    pad = 3.0
    origin_x = float(origin[0])
    origin_y = float(origin[1])
    if align == "center" and ink is not None:
        room = max(4.0, column.width - 2 * pad)
    else:
        room = max(4.0, (column.x1 - pad) - origin_x)
    shrunk = False
    if width > old_width + 0.4 and width > room:
        size = max(4.0, size * (room / width) * 0.98)
        width = float(face.text_length(text, fontsize=size))
        shrunk = True
    if align == "center" and ink is not None:
        center = (ink.x0 + ink.x1) / 2.0
        origin_x = center - width / 2.0
    return (origin_x, origin_y), size, shrunk, width


def _replace_span(page, proposal: dict) -> None:
    import pymupdf as fitz

    old_text = str(proposal.get("old") or "")
    loose = proposal.get("bbox")
    ink, redact, trace_origin = _ink_and_redact(page, old_text, loose)
    if ink is None and loose:
        ink = fitz.Rect(loose)
    origin = trace_origin or proposal.get("origin") or (ink.x0 if ink else 0, ink.y1 if ink else 0)
    asked = proposal.get("origin")
    if asked and len(asked) >= 2 and trace_origin:
        # A move hands in a shifted point. An in-place edit keeps the drawn baseline.
        if abs(float(asked[0]) - float(trace_origin[0])) > 0.6 or abs(float(asked[1]) - float(trace_origin[1])) > 0.6:
            origin = (float(asked[0]), float(asked[1]))
    size = float(proposal.get("size") or 12)
    text = str(proposal.get("new") or "")
    rgb = proposal.get("rgb")
    if not rgb or len(rgb) < 3:
        rgb = list(_color_tuple(int(proposal.get("color") or 0)))
    fontfile = proposal.get("fontfile") or ""
    origin, size, shrunk, width = _fit_on_line(
        page, text, old_text, size, fontfile, str(proposal.get("align") or ""), origin, ink,
    )
    proposal["shrunk"] = bool(proposal.get("shrunk") or shrunk)
    proposal["size"] = size
    if ink is not None:
        placed = fitz.Rect(origin[0], ink.y0, origin[0] + width, ink.y1)
        guard = fitz.Rect(min(ink.x0, placed.x0), ink.y0, max(ink.x1, placed.x1), ink.y1)
        proposal["editBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
        proposal["guardBox"] = [guard.x0, guard.y0, guard.x1, guard.y1]
    if isinstance(redact, list):
        for piece in redact:
            page.add_redact_annot(piece)
    elif redact is not None:
        page.add_redact_annot(redact)
    else:
        return
    # No fill. Images and line art stay, so a coloured panel is not painted over.
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
    )
    _place_font_text(page, origin, text, size, rgb, fontfile)


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


def _raster_ink(image, left: int, top: int, width: int, height: int):
    """The strokes of this word, grown from the reader box to the whole glyph."""
    import cv2
    import numpy as np

    height_px, width_px = image.shape[:2]
    grow_y = max(4, int(round(height * 1.5)))
    grow_x = max(2, int(round(width * 0.04)))
    win_y0, win_y1 = max(0, top - grow_y), min(height_px, top + height + grow_y)
    win_x0, win_x1 = max(0, left - grow_x), min(width_px, left + width + grow_x)
    window = image[win_y0:win_y1, win_x0:win_x1]
    if window.size == 0:
        return None
    frame = max(2, min(grow_y, 6))
    ring = np.concatenate([
        window[:frame].reshape(-1, 3),
        window[-frame:].reshape(-1, 3),
    ]).astype(np.float32)
    background = np.median(ring, axis=0)
    distance = np.linalg.norm(window.astype(np.float32) - background, axis=2)
    foreground = (distance > 48.0).astype(np.uint8)
    count, labels = cv2.connectedComponents(foreground)
    keep = np.zeros(foreground.shape, np.uint8)
    box_x0, box_y0 = left - win_x0, top - win_y0
    box_x1, box_y1 = box_x0 + width, box_y0 + height
    for index in range(1, count):
        ys, xs = np.where(labels == index)
        if ys.size < 4:
            continue
        if xs.max() < box_x0 or xs.min() >= box_x1 or ys.max() < box_y0 or ys.min() >= box_y1:
            continue
        center_y = float(ys.mean())
        if center_y < box_y0 - height * 0.35 or center_y > box_y1 + height * 0.35:
            continue
        keep[ys, xs] = 255
    if int(keep.sum()) < 255:
        keep[box_y0:box_y1, box_x0:box_x1] = 255
    mask = cv2.dilate(keep, np.ones((3, 3), np.uint8), iterations=1)
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return None
    return {
        "window": window,
        "mask": mask,
        "win": (win_x0, win_y0, win_x1, win_y1),
        "bounds": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
        "background": background,
        "color": np.median(window[keep > 0].astype(np.float32), axis=0) if np.any(keep) else background,
    }


def _paint_raster_text(doc, page, proposal: dict) -> None:
    """Inpaint only the word's strokes, then set the new words on that background."""
    import cv2
    import numpy as np
    import pymupdf as fitz

    scale = float(proposal.get("renderScale") or 2.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
    left, top, width, height = [int(item) for item in proposal["pixelBox"]]
    found = _raster_ink(image, left, top, width, height)
    if found is None:
        return
    window = found["window"]
    painted = cv2.inpaint(window, found["mask"], 3, cv2.INPAINT_TELEA)
    x0, y0, x1, y1 = found["bounds"]
    patch = painted[y0:y1, x0:x1]
    win_x0, win_y0, _win_x1, _win_y1 = found["win"]
    overlay = fitz.Pixmap(fitz.csRGB, patch.shape[1], patch.shape[0], np.ascontiguousarray(patch).tobytes(), 0)
    clip = fitz.Rect((win_x0 + x0) / scale, (win_y0 + y0) / scale, (win_x0 + x1) / scale, (win_y0 + y1) / scale)
    page.insert_image(clip, pixmap=overlay, overlay=True)
    ink = fitz.Rect(clip)
    fontfile = proposal.get("fontfile") or ((_candidate_fonts() or [""])[0])
    size = max(5.0, ink.height * 0.92) * float(proposal.get("sizeFactor") or 1.0)
    origin_y = ink.y1 - ink.height * 0.12
    center_x = (ink.x0 + ink.x1) / 2.0
    page_mid = (page.rect.x0 + page.rect.x1) / 2.0
    align = "center" if abs(center_x - page_mid) <= 14 else "left"
    origin, size, shrunk, text_width = _fit_on_line(
        page,
        str(proposal.get("new") or ""),
        str(proposal.get("old") or ""),
        size,
        fontfile,
        align,
        (ink.x0, origin_y),
        ink,
    )
    proposal["shrunk"] = bool(shrunk)
    proposal["editBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
    placed = fitz.Rect(origin[0], ink.y0, origin[0] + text_width, ink.y1)
    guard = fitz.Rect(min(ink.x0, placed.x0), ink.y0, max(ink.x1, placed.x1), ink.y1)
    proposal["guardBox"] = [guard.x0, guard.y0, guard.x1, guard.y1]
    # The reply uses the guard, which includes a longer replacement on the same line.
    color = found["color"]
    rgb = (float(color[0]) / 255.0, float(color[1]) / 255.0, float(color[2]) / 255.0)
    _place_font_text(page, origin, str(proposal.get("new") or ""), size, rgb, fontfile)


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


def _compact_ocr(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _render_rgb(path: str, scale: float = 2.5):
    import pymupdf as fitz
    import numpy as np

    doc = fitz.open(path)
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
        return image
    finally:
        doc.close()


def _tesseract(image, psm: str, tsv: bool) -> str:
    import cv2

    folder = tempfile.mkdtemp(prefix="edit-gate-")
    path = os.path.join(folder, "page.png")
    cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    cmd = ["tesseract", path, "stdout", "--psm", psm]
    if tsv:
        cmd.append("tsv")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=40, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _tsv_words(image) -> list[tuple[int, int, int, int, str]]:
    """Each word with its box. Sparse mode, so one edited line does not reshuffle the page."""
    words = []
    for line in _tesseract(image, "11", True).splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or not parts[11].strip():
            continue
        try:
            words.append((int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]), parts[11].strip()))
        except ValueError:
            continue
    return words


def _read_as(text: str, digits: bool) -> str:
    compact = _compact_ocr(text)
    if digits:
        compact = compact.replace("O", "0").replace("I", "1").replace("L", "1")
    return compact


def visual_gates(before_pdf: str, after_pdf: str, edit_box, old_text: str, new_text: str) -> list[str]:
    """Three gates. A neighbour deleted, a flat block, or a missing new word fails the case.

    (a) Outside a small margin of the old word box, the page pixels stay put.
    (b) OCR of that region reads the new words.
    (c) OCR of the whole page changes only by those words.
    """
    import numpy as np
    from collections import Counter

    problems = []
    if not edit_box or len(edit_box) < 4:
        return ["no word box"]
    if not os.path.isfile(before_pdf) or not os.path.isfile(after_pdf):
        return ["missing preview pdf"]
    scale = 2.5
    before = _render_rgb(before_pdf, scale)
    after = _render_rgb(after_pdf, scale)
    if before.shape != after.shape:
        return ["page size changed"]
    margin = 1.25 * scale
    x0 = max(0, int(np.floor(float(edit_box[0]) * scale - margin)))
    y0 = max(0, int(np.floor(float(edit_box[1]) * scale - margin)))
    x1 = min(before.shape[1], int(np.ceil(float(edit_box[2]) * scale + margin)))
    y1 = min(before.shape[0], int(np.ceil(float(edit_box[3]) * scale + margin)))
    delta = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2)
    outside = delta.copy()
    outside[y0:y1, x0:x1] = 0
    changed = int((outside > 12).sum())
    if changed > 40:
        problems.append(f"pixels outside the word {changed}")
    pad = int(round(3 * scale))
    crop = after[max(0, y0 - pad): min(after.shape[0], y1 + pad), max(0, x0 - pad): min(after.shape[1], x1 + pad)]
    digits = sum(ch.isdigit() for ch in (new_text or "")) >= max(1, int(0.6 * len(re.sub(r"\s", "", new_text or "x"))))
    wanted = _read_as(new_text, digits)
    old_compact = _read_as(old_text, digits)
    before_words = _tsv_words(before)
    after_words = _tsv_words(after)
    readings = []
    if crop.size:
        import cv2
        bigger = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        readings.append(" ".join(item[4] for item in _tsv_words(bigger)))
        readings.append(_tesseract(bigger, "6", False))
        readings.append(_tesseract(bigger, "7", False))
    inside = []
    for left, top, width, height, text in after_words:
        cx = left + width / 2.0
        cy = top + height / 2.0
        if x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4:
            inside.append(text)
    readings.append(" ".join(inside))
    compacts = [_read_as(text, digits) for text in readings if text and text.strip()]
    if wanted and not any(wanted in item for item in compacts):
        problems.append("region OCR missed " + (new_text or "")[:40])
    if old_compact and old_compact != wanted and any(old_compact in item for item in compacts):
        problems.append("region OCR still has the old words")

    def outside(words) -> list[str]:
        found = []
        for left, top, width, height, text in words:
            cx = left + width / 2.0
            cy = top + height / 2.0
            if x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4:
                continue
            token = _compact_ocr(text)
            if token:
                found.append(token)
        return found

    before_out = Counter(outside(before_words))
    after_out = Counter(outside(after_words))
    if before_out != after_out:
        problems.append("page OCR changed more than the words")
    return problems


def gate_line(problems: list[str]) -> str:
    """Pass or fail for the pixel gate, the region read, and the rest of the page."""
    blob = " ".join(problems or [])
    structural = "no word box" in blob or "missing preview" in blob
    pixel = structural or "pixels outside" in blob or "page size changed" in blob
    region = structural or "region OCR" in blob
    page = structural or "page OCR" in blob or "page size changed" in blob
    return "a={0} b={1} c={2}".format(
        "fail" if pixel else "pass",
        "fail" if region else "pass",
        "fail" if page else "pass",
    )


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
