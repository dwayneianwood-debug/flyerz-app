#!/usr/bin/env python3
"""One small edit at a time, with a preview and an undo.

Live text keeps the face embedded in the PDF when that face has the new
letters. Otherwise it is set in a font shipped with the app of the same class
(sans, serif, slab, display/condensed, or script) and a similar weight.
Machine font folders are not searched, so Linux and Windows produce the same
letters. A different class is refused. It is not offered as an amber match.
A picture gets the whole original word filled from the background, then new
vector text. The picture is read once, at a modest size, and that read is
kept for the job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

from edit_style import (
    DATE_IN_TEXT,
    PHONE_IN_TEXT,
    TIME_IN_TEXT,
    VENUE_IN_TEXT,
    _parse_date,
    _venue_region,
    fragment,
    phone_with_icon,
    swap_text,
)

_ASK_VERB = r"(?:change|update|set|replace|swap)"
_ASK_FIELD = r"(phone(?:\s+number)?|date|time|venue)"
# "change the time to 7:30PM"
FIELD_TO_ASK = re.compile(rf"{_ASK_VERB}\s+the\s+{_ASK_FIELD}\s+to\s+(.+)$", re.I)
# "change the time from 10:00AM to 7:30PM"
FIELD_FROM_ASK = re.compile(rf"{_ASK_VERB}\s+the\s+{_ASK_FIELD}\s+from\s+(.+?)\s+to\s+(.+)$", re.I)
# "change the time 10:00AM to 7:30PM"
FIELD_X_TO_ASK = re.compile(rf"{_ASK_VERB}\s+the\s+{_ASK_FIELD}\s+(.+?)\s+to\s+(.+)$", re.I)
# "change 9 OCT to 12 OCT" — the old and new values, without the word date.
BARE_TO_ASK = re.compile(rf"(?:please\s+)?{_ASK_VERB}\s+(.+?)\s+to\s+(.+)$", re.I)
# "from 9 OCT to 12 October 2026"
FROM_TO_ASK = re.compile(r"(?:please\s+)?from\s+(.+?)\s+to\s+(.+)$", re.I)
# "change the phone number" / "change the venue to" with nothing after it.
INCOMPLETE_FIELD_ASK = re.compile(
    rf"(?:please\s+)?{_ASK_VERB}\s+the\s+({_ASK_FIELD})(?:\s+to)?\s*\.?$",
    re.I,
)
VAGUE_ASK = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:(?:change|update|fix|edit)\s+it|make\s+it\s+(?:look\s+)?(?:better|nicer|clearer|pop|good))\.?$",
    re.I,
)
_COMPARE_PX = 64
LOGO_ASK = re.compile(r"make\s+the\s+logo\s+(bigger|smaller|larger)\b", re.I)
LOGO_MOVE_ASK = re.compile(r"move\s+the\s+logo\s+(up|down|left|right)\b", re.I)
HEADING_ASK = re.compile(r"make\s+the\s+heading\s+(bigger|smaller|larger)\b", re.I)
MOVE_ASK = re.compile(r"move\s+the\s+text\s+away\s+from\s+the\s+edge", re.I)
FIELD_NAME = {"phone": "phone number", "date": "date", "time": "time", "venue": "venue"}
SAFE_EDIT = "I can't safely change this one, please send the editable file."

_BUNDLED_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FIXTURE_FONTS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures", "fonts"))
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


def _weight_bucket(name: str) -> str:
    """bold, medium, light, or regular. SemiBold sits with bold."""
    hint = _weight_hint(name)
    if hint in ("black", "extrabold", "bold", "semibold"):
        return "bold"
    if hint == "medium":
        return "medium"
    if hint in ("light", "thin", "extralight"):
        return "light"
    return "regular"


def _wants_italic(name: str) -> bool:
    token = _font_token(name)
    return "italic" in token or "oblique" in token


def _windows_font_dirs() -> list[str]:
    """Machine font folders are not searched.

    A Windows laptop and this Linux host do not have the same faces. Edits use
    the fonts shipped with the app, or a font embedded in the artwork.
    """
    return []


def _font_search_dirs() -> list[str]:
    """Only the fonts shipped in server/fonts. The same list on every computer."""
    return [os.path.normpath(_BUNDLED_FONTS)]


def _bundled_font_files() -> list[str]:
    """Every face shipped with the app, including server/fonts/v2.

    Walk order is sorted, so the same computer and a different one build the
    same list. A font that exists only on Linux or only on Windows is not here.
    """
    found = []
    seen = set()
    folders = [_BUNDLED_FONTS]
    if os.path.isdir(_FIXTURE_FONTS):
        folders.append(_FIXTURE_FONTS)
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for dirpath, dirnames, files in os.walk(folder):
            dirnames.sort()
            for name in sorted(files):
                if not name.lower().endswith((".ttf", ".otf")):
                    continue
                path = os.path.join(dirpath, name)
                real = os.path.abspath(path)
                if real in seen:
                    continue
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


def _face_label(fontfile: str, fallback: str = "") -> str:
    """The name a person would recognise, from the font file that will be embedded."""
    if not fontfile or fontfile in ("helv", "times", "cour"):
        return {"helv": "Helvetica", "times": "Times", "cour": "Courier"}.get(fontfile or "", fallback or "the bundled font")
    try:
        from PIL import ImageFont

        family, style = ImageFont.truetype(fontfile).getname()
        style = str(style or "").strip()
        if style.lower() in ("regular", "book", "normal"):
            return f"{family} Regular"
        if style and style.lower() not in family.lower():
            return f"{family} {style}"
        return family
    except Exception:
        stem = os.path.splitext(os.path.basename(fontfile))[0].replace("-", " ")
        return stem or fallback or "the bundled font"


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
        base = os.path.basename(use).lower()
        if any(os.path.basename(row["path"]).lower() == base for row in rows):
            continue
        rows.append({"path": use, "kind": "bundled", "family": family, "style": style})
    rows.sort(key=lambda row: (row["family"], row["style"], os.path.basename(row["path"]).lower()))
    _FONT_INDEX = rows
    return rows


def _candidate_fonts() -> list[str]:
    return [row["path"] for row in _font_index() if row["kind"] == "bundled"]


def _named_font(fontname: str) -> str | None:
    """Full face for a subset tag. PWSYDC+Anton-Regular is the bundled Anton.

    The weight has to be the same class of weight. Bold is not swapped for regular.
    """
    wanted = _family_token(fontname)
    if len(wanted) < 3:
        return None
    hint = _weight_hint(fontname)
    bucket = _weight_bucket(fontname)
    italic = _wants_italic(fontname)
    best = None
    best_key = (9, 9, "z")
    for row in _font_index():
        if row["family"] != wanted:
            continue
        if _weight_bucket(row["style"]) != bucket:
            continue
        face_italic = row["style"] == "italic" or "italic" in os.path.basename(row["path"]).lower()
        if italic != face_italic:
            continue
        style_rank = 0 if row["style"] == hint or (hint == "regular" and row["style"] in ("regular", "book")) else 1
        key = (style_rank, 0 if not face_italic else 1, os.path.basename(row["path"]).lower())
        if key < best_key:
            best_key = key
            best = row["path"]
    return best


def _font_file_usable(fontfile: str, text: str) -> bool:
    """A file we can embed. A nameless subset, or one outlined glyph for every letter, is not."""
    if not fontfile or fontfile in ("helv", "times", "cour") or not os.path.isfile(fontfile):
        return False
    letters = [char for char in text if not char.isspace()]
    if not letters:
        return False
    try:
        from fontTools.ttLib import TTFont

        face = TTFont(fontfile, fontNumber=0)
        try:
            names = face["name"].names if "name" in face else []
            family = ""
            for rec in names:
                if rec.nameID == 1:
                    family = str(rec.toUnicode() or "").strip()
                    if family and family not in ("(null)", "null"):
                        break
            if not family or family in ("(null)", "null"):
                return False
            cmap = face.getBestCmap() or {}
            glyf = face["glyf"] if "glyf" in face else None
            glyph_names = []
            for char in letters:
                name = cmap.get(ord(char))
                if not name:
                    return False
                glyph_names.append(name)
                if glyf is not None:
                    glyph = glyf[name]
                    if int(getattr(glyph, "numberOfContours", 0) or 0) == 0 and not glyph.isComposite():
                        return False
            if len(set(letters)) >= 2 and len(set(glyph_names)) <= 1:
                return False
        finally:
            face.close()
    except Exception:
        return False
    if not _glyphs_present(fontfile, text):
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np

        drawn = ImageFont.truetype(fontfile, 28)
        marks = []
        for char in dict.fromkeys(letters):
            canvas = Image.new("L", (40, 40), 255)
            ImageDraw.Draw(canvas).text((2, 2), char, font=drawn, fill=0)
            plate = np.array(canvas)
            if int(plate.min()) > 210:
                return False
            marks.append(plate)
        if len(marks) >= 2 and all(np.array_equal(marks[0], item) for item in marks[1:]):
            return False
    except (OSError, ValueError):
        return False
    return True


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

    usable = sorted(
        (path for path in paths if os.path.isfile(path)),
        key=lambda item: os.path.basename(item).lower(),
    )
    if not usable:
        return "", 999.0
    if crop is None or getattr(crop, "size", 0) == 0:
        return usable[0], 999.0
    best_path = usable[0]
    best_score = 1e9
    height = min(_COMPARE_PX, max(12, int(crop.shape[0])))
    sample = crop
    if int(crop.shape[0]) > height:
        sample = cv2.resize(
            crop,
            (max(8, int(crop.shape[1] * height / float(crop.shape[0]))), height),
            interpolation=cv2.INTER_AREA,
        )
    for path in usable:
        try:
            font = ImageFont.truetype(path, max(10, height - 2))
            canvas = Image.new("RGB", (max(int(sample.shape[1]), 8), max(height, 8)), (255, 255, 255))
            ImageDraw.Draw(canvas).text((0, 0), (text or "")[:80], font=font, fill=(0, 0, 0))
        except (OSError, ValueError):
            continue
        plate = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        plate = cv2.resize(plate, (sample.shape[1], sample.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(np.mean(np.abs(plate.astype(np.float32) - sample.astype(np.float32))))
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


def _crop_has_strokes(crop) -> bool:
    """False when the letters never painted, so a blank crop must not pick a script face."""
    if crop is None or getattr(crop, "size", 0) == 0:
        return False
    gray = crop.mean(axis=2) if getattr(crop, "ndim", 1) == 3 else crop
    if float(gray.mean()) > 140:
        share = float((gray < 160).mean())
    else:
        share = float((gray > 140).mean())
    return share >= 0.008


def _plain_text_face(paths: list[str], text: str) -> str:
    """A regular text face. A blank sample must not fall through to a script."""
    ranked = []
    for path in paths:
        if not _font_file_usable(path, text):
            continue
        token = os.path.splitext(os.path.basename(path))[0].lower().replace("-", "").replace(" ", "")
        if any(word in token for word in ("script", "vibes", "handwrit", "brush", "calligraphy", "signature", "cursive")):
            continue
        rank = 9
        for index, name in enumerate(("liberationsans", "inter", "roboto", "opensans", "lato", "montserrat", "poppins")):
            if name in token and "bold" not in token and "italic" not in token:
                rank = index
                break
        ranked.append((rank, token, path))
    if not ranked:
        return next((path for path in paths if _font_file_usable(path, text)), "")
    ranked.sort()
    return ranked[0][2]


# Class comes from the font's name. A picture of the letters is not used here:
# that comparison was picking a script face for Arial, Georgia, Impact, and Times.
_SCRIPT_NAMES = (
    "greatvibes", "parisienne", "pinyon", "allura", "calligraphy", "brushscript",
    "snell", "edwardian", "vivaldi", "kunstler", "frenchscript", "freestyle",
    "mistral", "segoscript", "script",
)
_SLAB_NAMES = (
    "rockwell", "clarendon", "robotoslab", "slab", "memphis", "serifa", "lubalin",
)
_DISPLAY_NAMES = (
    "impact", "bebas", "oswald", "anton", "archivo", "leaguegothic", "ultra",
    "dincondensed", "franklingothic",
)
_SERIF_NAMES = (
    "times", "georgia", "garamond", "palatino", "cambria", "baskerville", "crimson",
    "cormorant", "playfair", "merriweather", "constantia", "bookantiqua",
    "cinzel", "marcellus", "libertinus", "nimbusroman", "freeserif", "ebgaramond",
)
_SANS_NAMES = (
    "arial", "helvetica", "helv", "verdana", "calibri", "candara", "corbel",
    "segoe", "tahoma", "trebuchet", "centurygothic", "gillsans", "futura",
    "avenir", "nunito", "lato", "montserrat", "poppins", "opensans", "roboto",
    "inter", "raleway", "liberation", "nimbus", "freesans", "dejavu", "sourcesans",
    "worksans", "dmsans", "manrope", "karla", "rubik", "ubuntu", "notosans",
    "frutiger", "myriad", "proxima", "cabin", "barlow", "firasans", "leaguespartan",
    "museosans",
)


def _live_class(name: str) -> str:
    """sans, serif, slab, display, or script. Empty when the name does not say."""
    token = _font_token(name)
    if not token:
        return ""
    if any(mark in token for mark in _SCRIPT_NAMES):
        return "script"
    if any(mark in token for mark in _SLAB_NAMES):
        return "slab"
    if "narrow" in token or "condensed" in token or any(mark in token for mark in _DISPLAY_NAMES):
        return "display"
    if "serif" in token or any(mark in token for mark in _SERIF_NAMES):
        return "serif"
    if any(mark in token for mark in _SANS_NAMES):
        return "sans"
    return ""


def _class_rank(path: str, fontname: str, kind: str) -> tuple:
    """Lower is a closer stand-in. The same list on every computer."""
    token = _family_token(os.path.basename(path))
    source = _font_token(fontname)
    if kind == "sans":
        if any(mark in source for mark in ("arial", "helvetica", "helv", "liberation", "nimbus")):
            prefer = ("liberationsans", "lato", "opensans", "roboto", "inter", "nunito", "poppins", "montserrat")
        elif "montserrat" in source:
            prefer = ("montserrat", "poppins", "lato", "liberationsans", "inter")
        elif any(mark in source for mark in ("verdana", "trebuchet", "nunito")):
            prefer = ("nunito", "opensans", "lato", "liberationsans")
        else:
            prefer = ("liberationsans", "opensans", "roboto", "lato", "inter", "nunito", "poppins", "montserrat")
    elif kind == "serif":
        if any(mark in source for mark in ("times", "georgia", "palatino", "garamond", "baskerville", "crimson", "cambria")):
            prefer = ("librebaskerville", "crimsontext", "ebgaramond", "cormorantgaramond", "cormorantsc", "cormorant")
        else:
            prefer = ("crimsontext", "librebaskerville", "ebgaramond", "cormorantgaramond", "cinzel", "playfair", "marcellus")
    elif kind == "display":
        if any(mark in source for mark in ("impact", "bebas", "condensed", "narrow")):
            prefer = ("bebasneue", "anton", "oswald", "archivoblack")
        else:
            prefer = ("anton", "bebasneue", "oswald", "archivoblack")
    elif kind == "script":
        prefer = ("greatvibes", "parisienne", "pinyonscript", "pinyon", "allura")
    else:
        prefer = ()
    for index, name in enumerate(prefer):
        if name in token:
            return (index, token)
    return (len(prefer) + 5, token)


def _class_face(fontname: str, text: str) -> str:
    """A bundled face of this class and a similar weight, or nothing."""
    kind = _live_class(fontname)
    if not kind:
        return ""
    bucket = _weight_bucket(fontname)
    italic = _wants_italic(fontname)
    found = []
    for row in _font_index():
        label = f"{row.get('family') or ''} {os.path.basename(row['path'])}"
        if _live_class(label) != kind:
            continue
        face_italic = row.get("style") == "italic" or "italic" in os.path.basename(row["path"]).lower()
        if italic != face_italic:
            continue
        face_bucket = _weight_bucket(str(row.get("style") or "") + os.path.basename(row["path"]))
        if face_bucket != bucket:
            # Medium may use a regular face. Bold may not.
            if not (bucket == "medium" and face_bucket == "regular"):
                if not (bucket == "light" and face_bucket == "regular"):
                    continue
        if not _glyphs_present(row["path"], text):
            continue
        found.append(row["path"])
    if not found:
        return ""
    found.sort(key=lambda path: (_class_rank(path, fontname, kind), os.path.basename(path).lower()))
    for path in found:
        if _font_file_usable(path, text):
            return path
    return ""


def _full_font_name(page, fontname: str, flags: int = 0) -> str:
    """The PDF face name, including Bold when the span only says the family.

    PyMuPDF often reports 'Arial' while the embedded base font is Arial-BoldMT.
    The weight is read from that base font, and from the bold flag when the
    name itself does not say.
    """
    name = str(fontname or "")
    family = _family_token(name)
    best = name
    try:
        listed = page.get_fonts() or []
    except Exception:
        listed = []
    for item in listed:
        base = str(item[3] or "") if len(item) > 3 else ""
        if not base or not family or len(family) < 3:
            continue
        if family not in _font_token(base):
            continue
        if _weight_bucket(base) != "regular" or _wants_italic(base) or len(base) > len(best):
            best = base
    if int(flags) & 16 and _weight_bucket(best) == "regular":
        best += "-Bold"
    if int(flags) & 2 and not _wants_italic(best):
        best += "-Italic"
    return best


def _refuse_face(fontname: str) -> dict:
    return {
        "fontfile": "",
        "amber": False,
        "fontSource": "none",
        "faceLabel": "",
        "reply": SAFE_EDIT,
    }


def _resolve_face(doc, page, fontname: str, text: str, bbox=None) -> dict:
    """Embedded face, then the same family, then the same class and weight.

    A style that we cannot match is refused. It is not offered in a script
    face and called amber.
    """
    embedded = _embedded_font(doc, page, fontname)
    # Base-14 names are not files. Inserting them leaves Helvetica unembedded.
    if embedded in ("helv", "times", "cour"):
        embedded = None
    if embedded and _font_file_usable(embedded, text):
        return {"fontfile": embedded, "amber": False, "fontSource": "embedded", "faceLabel": _face_label(embedded, fontname)}
    full = _named_font(fontname)
    if full and _font_file_usable(full, text):
        return {"fontfile": full, "amber": False, "fontSource": "full", "faceLabel": _face_label(full, fontname)}
    path = _class_face(fontname, text)
    if not path:
        return _refuse_face(fontname)
    return {
        "fontfile": path,
        "amber": False,
        "fontSource": "class",
        "faceLabel": _face_label(path, fontname),
    }


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
    """Preview pixels. The same render is reused by the offer check, so a poster is not drawn twice."""
    del page_index
    from PIL import Image

    Image.fromarray(_render_rgb(path, 2.5)).save(dest)


def _ask_kind(label: str) -> str:
    low = (label or "").lower()
    if low.startswith("phone"):
        return "phone"
    return low


def _swap_kind(old: str, new: str) -> str:
    """Date, time, phone, or venue when the words themselves say which one."""
    best = ""
    best_rank = 0
    for kind in ("date", "time", "phone", "venue"):
        old_hit = bool(fragment(old, kind))
        new_hit = bool(fragment(new, kind))
        rank = (2 if old_hit and new_hit else 1 if old_hit or new_hit else 0)
        if rank > best_rank:
            best_rank = rank
            best = kind
    return best


def _values_ask(old: str, new: str) -> dict | None:
    old = (old or "").strip(" .")
    new = (new or "").strip(" .")
    if not old or not new or old.lower() in ("to", "from") or new.lower() in ("to", "from"):
        return None
    kind = _swap_kind(old, new)
    if not kind:
        return None
    return {"kind": kind, "new": new, "oldHint": old}


def _parse(message: str) -> dict | None:
    text = " ".join((message or "").split())
    found = FIELD_FROM_ASK.search(text)
    if found:
        return {
            "kind": _ask_kind(found.group(1)),
            "new": found.group(3).strip(" ."),
            "oldHint": found.group(2).strip(" ."),
        }
    found = FIELD_TO_ASK.search(text)
    if found:
        new = found.group(2).strip(" .")
        if new:
            return {"kind": _ask_kind(found.group(1)), "new": new}
    found = FIELD_X_TO_ASK.search(text)
    if found and found.group(2).strip().lower() not in ("to", "from"):
        return {
            "kind": _ask_kind(found.group(1)),
            "new": found.group(3).strip(" ."),
            "oldHint": found.group(2).strip(" ."),
        }
    found = BARE_TO_ASK.search(text)
    if found:
        parsed = _values_ask(found.group(1), found.group(2))
        if parsed:
            return parsed
    found = FROM_TO_ASK.search(text)
    if found:
        parsed = _values_ask(found.group(1), found.group(2))
        if parsed:
            return parsed
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


def _hint_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _pieces(text: str, kind: str) -> list[str]:
    """Every date, phone, time, or venue in this line. A date inside a phone is skipped."""
    if kind == "date":
        found = []
        for match in DATE_IN_TEXT.finditer(text or ""):
            inside = False
            for phone in PHONE_IN_TEXT.finditer(text or ""):
                if phone.start() <= match.start() and match.end() <= phone.end():
                    inside = True
                    break
            if not inside:
                found.append(match.group(0))
        return found
    if kind == "venue":
        # "CHURCH" at the start of a slogan is not an address. A street can keep its suburb.
        core = fragment(text or "", "venue")
        if core and len(core.split()) >= 2:
            bounds = _venue_region(text or "")
            if bounds:
                venue = (text or "")[bounds[0]:bounds[1]].strip()
                if venue:
                    return [venue]
            return [core]
        return []
    one = fragment(text or "", kind)
    return [one] if one else []


def _same_date(hint: str, found: str) -> bool:
    """9 OCT is 09 October. It is not 10 OCT, 11 OCT, or 19 OCT."""
    try:
        left = _parse_date(hint)
        right = _parse_date(found)
    except ValueError:
        return False
    if int(left[0]) != int(right[0]) or int(left[1]) != int(right[1]):
        return False
    if left[2] is not None and right[2] is not None and int(left[2]) != int(right[2]):
        return False
    return True


def _hint_matches(kind: str, hint: str, found: str) -> bool:
    """The words Ian named, not a different value that merely looks similar."""
    if not str(hint or "").strip() or not str(found or "").strip():
        return False
    if kind == "date":
        return _same_date(hint, found)
    if kind == "phone":
        digits_h = re.sub(r"\D", "", hint)
        digits_f = re.sub(r"\D", "", found)
        return len(digits_h) >= 6 and digits_h == digits_f
    if kind == "time":
        return _hint_key(hint) == _hint_key(found)
    return bool(_hint_key(hint)) and _hint_key(hint) in _hint_key(found)


def _same_label(kind: str, left: str, right: str) -> bool:
    if kind == "date":
        return _same_date(left, right) or _hint_key(left) == _hint_key(right)
    return _hint_key(left) == _hint_key(right)


def _unique_labels(kind: str, texts: list) -> list[str]:
    kept = []
    for text in texts:
        text = " ".join(str(text or "").split())
        if not text:
            continue
        if any(_same_label(kind, text, prev) for prev in kept):
            continue
        kept.append(text)
    return kept


def _missing_named_reply(kind: str, hint: str, found: list) -> dict:
    """Ian named words that are not on the page. Another value is not a substitute."""
    label = FIELD_NAME.get(kind, "text")
    named = " ".join(str(hint or "").split()) or "That text"
    labels = _unique_labels(kind, found)
    if labels:
        listed = ", ".join(labels[:6])
        reply = (
            f"{named} isn't on the artwork. I can see {listed}. "
            f"Which {label} should I change? Nothing was changed."
        )
    else:
        reply = f"{named} isn't on the artwork. Nothing was changed."
    return {"ok": False, "reply": reply, "actions": [], "pending": False}


def _live_matches(spans: list[dict], kind: str, hint: str = "") -> list[dict]:
    """Spans of this kind. A from/to hint keeps only the span that is that value."""
    hits = []
    for span in spans:
        if _pieces(span.get("text") or "", kind):
            hits.append(span)
    if not str(hint or "").strip():
        return hits
    matched = []
    for span in hits:
        pieces = _pieces(span.get("text") or "", kind) or [span.get("text") or ""]
        if any(_hint_matches(kind, hint, piece) for piece in pieces):
            matched.append(span)
    return matched


def _live_target(spans: list[dict], kind: str, hint: str = "") -> dict | None:
    hits = _live_matches(spans, kind, hint)
    return hits[0] if len(hits) == 1 else None


def _many_reply(kind: str) -> dict:
    label = FIELD_NAME.get(kind, "piece of text")
    return {
        "ok": False,
        "reply": f"I found more than one {label} on this page. {SAFE_EDIT}",
        "actions": [],
        "pending": False,
    }


def _need_reply(kind: str) -> dict:
    """An edit that does not say the new words. Nothing on the file changes."""
    asked = {
        "phone": "the new phone number, written out in full",
        "date": "the new date",
        "time": "the new time",
        "venue": "the new venue, written out in full",
    }.get(kind, "the exact words to change, and what they should say")
    return {
        "ok": False,
        "reply": f"Nothing was changed. Please tell me {asked}.",
        "actions": [],
        "pending": False,
    }


def _incomplete_edit(message: str) -> dict | None:
    text = " ".join((message or "").split())
    if not text:
        return None
    if VAGUE_ASK.search(text):
        return _need_reply("")
    found = INCOMPLETE_FIELD_ASK.search(text)
    if found:
        return _need_reply(_ask_kind(found.group(1)))
    return None


# A long side past this is shrunk before the reader runs. The word boxes are
# mapped back onto the original pixels, so the font match still sees the real crop.
_OCR_LONG = 1280
_OCR_MEM: dict[str, list] = {}


def _ocr_disk_path(cache_id: str) -> str:
    root = os.environ.get("FLYERZ_EDIT_DIR") or os.path.join(tempfile.gettempdir(), "flyerz-ocr")
    folder = os.path.join(root, "ocr-cache")
    os.makedirs(folder, exist_ok=True)
    digest = hashlib.sha1(cache_id.encode("utf-8", "replace")).hexdigest()[:24]
    return os.path.join(folder, digest + ".json")


def _ocr_load(cache_id: str) -> list[dict] | None:
    if not cache_id:
        return None
    if cache_id in _OCR_MEM:
        return _OCR_MEM[cache_id]
    path = _ocr_disk_path(cache_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        words = payload.get("words") if isinstance(payload, dict) else None
        if not isinstance(words, list):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    _OCR_MEM[cache_id] = words
    return words


def _ocr_store(cache_id: str, words: list[dict]) -> None:
    if not cache_id:
        return
    _OCR_MEM[cache_id] = words
    path = _ocr_disk_path(cache_id)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"words": words}, handle)
    except OSError:
        pass


def _words_from_bgr(image, cache_id: str = "") -> list[dict]:
    """Read a picture once. A retry of the same job uses the saved read."""
    cached = _ocr_load(cache_id)
    if cached is not None:
        return cached
    if image is None or getattr(image, "size", 0) == 0:
        _ocr_store(cache_id, [])
        return []
    import cv2

    height, width = image.shape[:2]
    long_side = max(height, width)
    view = image
    scale = 1.0
    if long_side > _OCR_LONG:
        scale = _OCR_LONG / float(long_side)
        view = cv2.resize(
            image,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    words: list[dict] = []
    try:
        from ocr_reader import local_rows

        # Upright poster type does not need the angle model on every line.
        words = _words_from_rows(local_rows(view) or [])
    except Exception:
        words = []
    if not words:
        folder = tempfile.mkdtemp(prefix="edit-ocr-")
        png = os.path.join(folder, "view.png")
        try:
            cv2.imwrite(png, view)
            words = _tesseract_words(png)
        except Exception:
            words = []
    if scale != 1.0:
        inverse = 1.0 / scale
        for word in words:
            word["left"] = int(round(float(word["left"]) * inverse))
            word["top"] = int(round(float(word["top"]) * inverse))
            word["width"] = max(1, int(round(float(word["width"]) * inverse)))
            word["height"] = max(1, int(round(float(word["height"]) * inverse)))
    _ocr_store(cache_id, words)
    return words


def _rapid_words(png: str) -> list[dict]:
    """Same local reader the vector rebuild uses, one box per line of type."""
    import cv2

    image = cv2.imread(png)
    if image is None:
        return []
    return _words_from_bgr(image, "")


def _words_from_rows(rows) -> list[dict]:
    """One word box per OCR row. A numpy box must not be tested with `not`."""
    from ocr_reader import _plain_box

    words = []
    for item in rows or []:
        try:
            if isinstance(item, dict):
                box = item.get("box") or item.get("boxes")
                text = str(item.get("text") or item.get("txt") or "")
            else:
                box = item[0]
                text = str(item[1] or "") if len(item) > 1 else ""
            text = text.strip()
            points = _plain_box(box)
            if not text or len(points) < 4:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
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
        except Exception:
            continue
    return words


def _tesseract_words(png: str) -> list[dict]:
    try:
        proc = subprocess.run(
            ["tesseract", png, "stdout", "tsv"],
            capture_output=True,
            text=True,
            timeout=8,
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


def _ocr_words(png: str, cache_id: str = "") -> list[dict]:
    import cv2

    image = cv2.imread(png)
    if image is None:
        return []
    return _words_from_bgr(image, cache_id)


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


_SERIF_MARKS = (
    "serif", "times", "georgia", "garamond", "palatino", "cambria", "playfair",
    "baskerville", "constantia", "bookantiqua", "century", "merriweather",
    "crimson", "cormorant", "marcellus", "cinzel",
)
_DISPLAY_MARKS = (
    "jokerman", "chiller", "freestyle", "viner", "mistral", "blackadder", "curlz",
    "papyrus", "comic", "handwriting", "calligraphy", "greatvibes", "brushscript",
    "rage", "pristina", "harlow", "ravie", "snapitc", "juice", "cooper",
    "parisienne", "pinyon", "allura", "script",
)


def _style_of(path: str) -> str:
    """serif, sans, or display. Jokerman and Cooper Black are display faces."""
    family = ""
    for row in _font_index():
        if os.path.abspath(row["path"]) == os.path.abspath(path):
            family = row.get("family") or ""
            break
    token = _family_token(family) + _family_token(os.path.basename(path))
    if any(mark in token for mark in _DISPLAY_MARKS):
        return "display"
    if any(mark in token for mark in _SERIF_MARKS):
        return "serif"
    return "sans"


def _wanted_style(ranked: list) -> str:
    """Serif artwork stays with a serif. A plain sans is not a substitute for it.

    The class is the best overlap in that class. One reference file can lose
    even when another face in the same class is the real match.
    """
    def best(style: str) -> float:
        scores = [float(item[1]) for item in ranked if item[4] == style]
        return max(scores) if scores else -1.0

    sans_iou = best("sans")
    serif_iou = best("serif")
    if serif_iou >= 0 and serif_iou >= sans_iou + 0.03:
        return "serif"
    if sans_iou >= 0 and sans_iou >= serif_iou + 0.03:
        return "sans"
    return "either"


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
        path = _plain_text_face(paths, text) or next((item for item in paths if os.path.isfile(item)), "")
        _closest_font.weight_ratio = 1.0
        return path, 999.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    # The letters are the minority class, including white type on a pink ground.
    mask = _letter_bits(gray)
    best_path = ""
    best_iou = -1.0
    # Compare the letter strokes, not the empty margin around the reader box.
    # A heavy face wins that padded overlap even when the original is a regular weight.
    ink_y, ink_x = np.where(mask)
    if len(ink_x) == 0:
        path = _plain_text_face(paths, text) or next((item for item in paths if os.path.isfile(item)), "")
        _closest_font.weight_ratio = 1.0
        return path, 999.0
    mask = mask[int(ink_y.min()): int(ink_y.max()) + 1, int(ink_x.min()): int(ink_x.max()) + 1]
    tokens = [part for part in (text or "").split() if part][:4]
    bands = _horizontal_bands(mask)
    stacked = len(bands) >= 2 and len(tokens) >= 2
    # Compare at a fixed height. A church-poster crop is hundreds of pixels tall,
    # and a day stacked on its month has to keep the gap between the two lines.
    draw_h = 180 if stacked else _COMPARE_PX
    if int(mask.shape[0]) > draw_h:
        new_w = max(8, int(round(mask.shape[1] * draw_h / float(mask.shape[0]))))
        mask = cv2.resize(mask.astype(np.uint8) * 255, (new_w, draw_h), interpolation=cv2.INTER_AREA) > 127
        bands = _horizontal_bands(mask) if stacked else bands
        stacked = len(bands) >= 2 and len(tokens) >= 2
    sample = (text or "")[:80]
    canvas_w = min(1024, _COMPARE_PX * max(2, len(sample) + 1))
    src_share = float(mask.mean()) if mask.size else 0.0
    ranked: list[tuple[float, float, float, str, str]] = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            if stacked:
                ink = _stacked_ink(path, mask, bands, tokens)
            else:
                ink = _line_ink(path, sample, mask, canvas_w)
        except (OSError, ValueError):
            continue
        if ink is None:
            continue
        union = int(np.logical_or(mask, ink).sum()) or 1
        iou = float(np.logical_and(mask, ink).sum()) / float(union)
        share = float(ink.mean()) if ink.size else 0.0
        ratio = share / max(src_share, 0.02)
        # A heavy display face covers the letters and the counters. That overlap
        # is not a closer match when the original strokes are thinner.
        penalty = 0.0
        if src_share >= 0.02 and ratio > 1.25:
            penalty = min(0.45, (ratio - 1.15) * 0.55)
        ranked.append((iou - penalty, iou, ratio, path, _style_of(path)))
    if not ranked:
        _closest_font.weight_ratio = 1.0
        score = 999.0 if best_iou < 0 else (1.0 - best_iou) * 100.0
        return best_path, score
    wanted = _wanted_style(ranked)
    if wanted == "either":
        pool = [item for item in ranked if item[4] != "display"]
    else:
        pool = [item for item in ranked if item[4] == wanted]
    if not pool:
        # A sans stand-in, or a novelty face such as Jokerman, is not this lettering.
        _closest_font.weight_ratio = 1.0
        return "", -1.0
    pool.sort(key=lambda item: (-round(item[0], 3), round(abs(1.0 - item[2]), 3), os.path.basename(item[3]).lower()))
    _adjusted, best_iou, best_ratio, best_path, _style = pool[0]
    if src_share >= 0.02 and best_ratio > 1.45:
        lighter = [item for item in pool if item[2] <= 1.25 and item[1] >= best_iou - 0.18]
        if lighter:
            lighter.sort(key=lambda item: (-round(item[1], 3), round(abs(1.0 - item[2]), 3), os.path.basename(item[3]).lower()))
            _adjusted, best_iou, best_ratio, best_path, _style = lighter[0]
        else:
            # No face is close to this weight. Applying a much heavier one would be wrong.
            _closest_font.weight_ratio = float(best_ratio)
            return "", -1.0
    if not stacked and best_path:
        # A face that only matches after it is squashed will not keep the digit height.
        # Prefer a close face whose natural width already fits the word.
        leader_adv = _advance_ratio(best_path, sample, mask.shape)
        if leader_adv > 1.18 or (0 < leader_adv < 0.82):
            fitted = []
            for _adj, iou, stroke, path, style in pool:
                if iou < best_iou - 0.12:
                    continue
                adv = _advance_ratio(path, sample, mask.shape)
                if 0.82 <= adv <= 1.18:
                    fitted.append((iou, adv, stroke, path, style))
            if fitted:
                fitted.sort(key=lambda item: (-round(item[0], 3), round(abs(1.0 - item[1]), 3), os.path.basename(item[3]).lower()))
                best_iou, _adv, best_ratio, best_path, _style = fitted[0]
    _closest_font.weight_ratio = float(best_ratio)
    # Callers treat a high score as a poor match. 0 would be a perfect overlap.
    score = 999.0 if best_iou < 0 else (1.0 - best_iou) * 100.0
    return best_path, score


def _advance_ratio(path: str, sample: str, shape) -> float:
    """How the face's natural width compares with the letter box. 1 is the same width."""
    import pymupdf as fitz

    if not path or not os.path.isfile(path) or not shape or shape[0] < 4 or shape[1] < 4:
        return 1.0
    height_ratio, _base = _digit_metrics(path)
    try:
        face = fitz.Font(fontfile=path)
    except Exception:
        return 1.0
    em = float(shape[0]) / max(float(height_ratio), 0.35)
    width = float(face.text_length((sample or "8")[:80], fontsize=em))
    return width / max(1.0, float(shape[1]))


def _horizontal_bands(mask) -> list[tuple[int, int]]:
    """Rows of letter ink, split where a poster puts the day above the month.

    A bright speck on a photograph is not a line of type. Bands much shorter
    than the letters are dropped so the day and the month stay the two lines.
    """
    import numpy as np

    if mask is None or getattr(mask, "size", 0) == 0:
        return []
    row = mask.mean(axis=1) > 0.04
    spans = []
    start = None
    for index, on in enumerate(row.tolist()):
        if on and start is None:
            start = index
        elif not on and start is not None:
            if index - start > 6:
                spans.append((start, index))
            start = None
    if start is not None and len(row) - start > 6:
        spans.append((start, len(row)))
    if not spans:
        return []
    tall = max(bottom - top for top, bottom in spans)
    floor = max(8.0, tall * 0.4)
    return [(top, bottom) for top, bottom in spans if (bottom - top) >= floor]


def _letter_bits(gray):
    """The letter pixels. White type on a mid pink is the light minority.

    A mean above 140 used to treat that pink as a light page and the letters
    as the dark pixels, which selected the background and missed the type.
    """
    import cv2
    import numpy as np

    if gray is None or getattr(gray, "size", 0) == 0:
        return gray
    _level, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    light = binary > 0
    letters = light if float(light.mean()) <= 0.5 else ~light
    share = float(letters.mean()) if letters.size else 0.0
    if share < 0.008 or share > 0.72:
        if float(gray.mean()) > 140:
            letters = gray < 160
        else:
            letters = gray > 140
        if float(letters.mean()) > 0.5:
            letters = ~letters
    return letters


def _line_ink(path: str, sample: str, mask, canvas_w: int):
    """One line of type, resized onto the letter box."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    draw_h = _COMPARE_PX
    font = ImageFont.truetype(path, draw_h)
    canvas = Image.new("L", (canvas_w, draw_h + 8), 255)
    ImageDraw.Draw(canvas).text((0, 2), sample, font=font, fill=0)
    plate = np.array(canvas)
    cols = np.where(plate.min(axis=0) < 200)[0]
    rows = np.where(plate.min(axis=1) < 200)[0]
    if len(cols) == 0 or len(rows) == 0:
        return None
    glyph = plate[rows.min(): rows.max() + 1, cols.min(): cols.max() + 1]
    glyph = cv2.resize(glyph, (mask.shape[1], mask.shape[0]), interpolation=cv2.INTER_AREA)
    return glyph < 170


def _stacked_ink(path: str, mask, bands, tokens):
    """Draw the day on the top band and the month on the bottom band."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    if len(bands) < 2 or len(tokens) < 2:
        return None
    use = [(bands[0], tokens[0]), (bands[-1], tokens[-1])]
    plate = np.zeros(mask.shape, dtype=bool)
    for (y0, y1), token in use:
        height = max(12, y1 - y0)
        font = ImageFont.truetype(path, height)
        canvas = Image.new("L", (max(32, mask.shape[1] * 2), height + 8), 0)
        ImageDraw.Draw(canvas).text((0, 0), token, font=font, fill=255)
        arr = np.array(canvas)
        cols = np.where(arr.max(axis=0) > 40)[0]
        rows = np.where(arr.max(axis=1) > 40)[0]
        if len(cols) == 0 or len(rows) == 0:
            return None
        glyph = arr[rows.min(): rows.max() + 1, cols.min(): cols.max() + 1]
        band = mask[y0:y1]
        used = np.where(band.any(axis=0))[0]
        if len(used) == 0:
            return None
        width = max(1, int(used.max() - used.min() + 1))
        glyph = cv2.resize(glyph, (width, max(1, y1 - y0)), interpolation=cv2.INTER_AREA)
        x0 = int(used.min())
        plate[y0:y1, x0:x0 + width] |= glyph > 140
    return plate


def propose(src: str, message: str, key: str, preview_dir: str) -> dict | None:
    parsed = _parse(message)
    if not parsed:
        incomplete = _incomplete_edit(message)
        if incomplete:
            return incomplete
        return None
    import pymupdf as fitz

    if not os.path.isfile(src):
        return {"ok": False, "reply": "I can't see the file, so I have not changed anything.", "actions": []}
    doc = fitz.open(src)
    try:
        page = doc[0]
        spans = _spans(page)
        proposal = {
            "kind": parsed["kind"],
            "page": 0,
            "live": bool(spans),
            "src": src,
            "ocrKey": f"{key}|{_file_stamp(src)}",
        }
        if parsed["kind"] in ("phone", "date", "time", "venue", "heading"):
            if parsed["kind"] == "heading":
                span = _heading_span(spans)
            else:
                hint = str(parsed.get("oldHint") or "")
                matches = _live_matches(spans, parsed["kind"], hint)
                if hint and not matches:
                    labels = []
                    for item in spans:
                        labels.extend(_pieces(item.get("text") or "", parsed["kind"]))
                    if labels:
                        return _missing_named_reply(parsed["kind"], hint, labels)
                if len(matches) > 1:
                    return _many_reply(parsed["kind"])
                span = matches[0] if matches else None
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
                # Redact and draw only the words that change. A label such as PHONE: or Call stays.
                # Empty icon brackets against a phone are part of that change.
                placed_old = shown_old or old
                if parsed["kind"] == "phone":
                    placed_old = phone_with_icon(old, placed_old)
                placed_new = shown_new if parsed["kind"] != "heading" else new_text
                partial = " ".join(placed_old.split()) != " ".join(str(old).split())
                probe = placed_new + _stray_chars(parsed["kind"], parsed.get("new") or "")
                fontname = _full_font_name(page, str(span.get("font") or ""), int(span.get("flags") or 0))
                face = _resolve_face(doc, page, fontname, probe, span.get("bbox"))
                if face.get("fontSource") == "none":
                    return {"ok": False, "reply": face.get("reply") or "Nothing was changed.", "actions": []}
                rgb, k_only = _span_rgb(page, span)
                bbox = list(span["bbox"])
                center = (float(bbox[0]) + float(bbox[2])) / 2.0
                mid = (float(page.rect.x0) + float(page.rect.x1)) / 2.0
                proposal.update({
                    "mode": "live-text",
                    "old": placed_old,
                    "new": placed_new,
                    "line": new_text,
                    "shownOld": shown_old,
                    "shownNew": shown_new,
                    "bbox": bbox,
                    "origin": list(span.get("origin") or (span["bbox"][0], span["bbox"][3])),
                    "align": "left" if partial else ("center" if abs(center - mid) <= 14 else "left"),
                    "size": float(span.get("size") or 12) * (float(parsed["factor"]) if parsed["kind"] == "heading" else 1.0),
                    "font": fontname,
                    "fontfile": face["fontfile"],
                    "fontSource": face.get("fontSource") or "",
                    "faceLabel": face.get("faceLabel") or _face_label(face["fontfile"], str(span.get("font") or "")),
                    "color": int(span.get("color") or 0),
                    "rgb": [rgb[0], rgb[1], rgb[2]],
                    "kOnly": k_only,
                    "amber": bool(face.get("amber")),
                    "fontScore": face.get("fontScore"),
                    "sizeFactor": float(parsed.get("factor") or 1),
                    "word": parsed.get("word") or "",
                })
            else:
                if spans and parsed["kind"] in ("phone", "date", "time", "venue"):
                    label = FIELD_NAME.get(parsed["kind"], "text")
                    return {
                        "ok": False,
                        "reply": f"I could not find a {label} on the artwork, so I have not changed anything.",
                        "actions": [],
                    }
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
                face = _resolve_face(
                    doc, page,
                    _full_font_name(page, move.get("font") or "", int(move.get("flags") or 0)),
                    str(move.get("old") or ""),
                    move.get("bbox"),
                )
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
    if proposal.get("refuse"):
        _save(key, {"proposal": {}, "undo": "", "current": src})
        return {"ok": False, "reply": str(proposal.get("refuse") or SAFE_EDIT), "actions": [], "pending": False}
    blocked_offer = _offer_block(src, staged, proposal)
    if blocked_offer:
        _save(key, {"proposal": {}, "undo": "", "current": src})
        return {"ok": False, "reply": blocked_offer, "actions": [], "pending": False}
    _render_page(staged, after)
    proposal["staged"] = staged
    blocked = _preview_block(src, staged, proposal)
    if blocked:
        _save(key, {"proposal": {}, "undo": "", "current": src})
        return {"ok": False, "reply": blocked, "actions": [], "pending": False}
    _save(key, {"proposal": proposal, "undo": "", "current": src})
    label = ""
    if proposal.get("faceLabel") or proposal.get("fontfile"):
        label = proposal.get("faceLabel") or _face_label(proposal.get("fontfile") or "", proposal.get("font") or "")
    if proposal.get("amber"):
        font_note = f" The new words are set in {label}, the closest font."
        amber = " Amber for the designer: it is not an exact match."
    elif label:
        font_note = f" The new words are set in {label}."
        amber = ""
    else:
        font_note = ""
        amber = ""
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
    """The digit boxes of a phone number, without an icon read as empty brackets.

    A street number on the same baseline, such as "7640 EXT 11", is not part of the phone.
    """
    ordered = sorted(group, key=lambda item: item["left"])
    runs: list[list[dict]] = []
    current: list[dict] = []
    for item in ordered:
        text = str(item.get("text") or "")
        letters = bool(re.search(r"[A-Za-z]", text))
        digits = bool(re.search(r"\d", text))
        separators = bool(re.fullmatch(r"[\s().+\-]*", text)) and not re.fullmatch(r"\(\s*\)", text.strip())
        if letters or not (digits or separators):
            if current:
                runs.append(current)
                current = []
            continue
        current.append(item)
    if current:
        runs.append(current)
    best = None
    best_digits = 0
    for run in runs:
        chosen = [item for item in run if re.search(r"\d", str(item.get("text") or ""))]
        if not chosen:
            continue
        joined = " ".join(str(item.get("text") or "") for item in chosen)
        phone = fragment(joined, "phone")
        count = len(re.sub(r"\D", "", phone))
        if count < 6 or count <= best_digits:
            continue
        best_digits = count
        best = {
            "text": phone,
            "left": chosen[0]["left"],
            "top": min(item["top"] for item in chosen),
            "width": max(item["left"] + item["width"] for item in chosen) - chosen[0]["left"],
            "height": max(item["top"] + item["height"] for item in chosen) - min(item["top"] for item in chosen),
        }
    return best


_MONTH_TOKEN = re.compile(
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?",
    re.I,
)
_MONTH_ONLY = re.compile(
    r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)$",
    re.I,
)
_TIME_TOKEN = re.compile(r"^\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)$", re.I)
_DAY_TOKEN = re.compile(r"^\d{1,2}$")
# A raster face further than this from the letters is not offered.
_FONT_SCORE_LIMIT = 70.0
# Digit height may move this far and still be the same size.
_SIZE_TOLERANCE = 0.18
# Stroke thickness versus the original. Heavier or lighter than this is refused.
_WEIGHT_TOLERANCE = 0.35


def _is_time_token(text: str) -> bool:
    return bool(_TIME_TOKEN.match(" ".join(str(text or "").split())))


def _month_run(text: str) -> list[str]:
    """'OCT OCT' is two months. A sentence that merely contains a month is not."""
    raw = " ".join(str(text or "").split())
    found = list(_MONTH_TOKEN.finditer(raw))
    if len(found) < 2:
        return []
    leftover = _MONTH_TOKEN.sub("", raw)
    if re.sub(r"[\s,./+-]+", "", leftover):
        return []
    return [item.group(0) for item in found]


def _split_merged_months(words: list[dict]) -> tuple[list[dict], bool]:
    """Split one box that holds two month words, using the day numbers above it.

    The reader sometimes returns 'OCT OCT' as a single 700 px box. Each day
    then gets the slice of that box that sits underneath it.
    """
    merged = False
    days = [
        word for word in words
        if _DAY_TOKEN.match(str(word.get("text") or "").strip()) and not _is_time_token(word.get("text"))
    ]
    out = []
    for word in words:
        tokens = _month_run(word.get("text") or "")
        if len(tokens) < 2:
            out.append(word)
            continue
        merged = True
        left = int(word["left"])
        top = int(word["top"])
        width = max(1, int(word["width"]))
        height = max(1, int(word["height"]))
        right = left + width
        count = len(tokens)
        inside = []
        for day in days:
            mid = day["left"] + day["width"] / 2.0
            slack = day["width"] * 0.35
            if mid < left - slack or mid > right + slack:
                continue
            if day["top"] > top + height * 0.45:
                continue
            inside.append(day)
        inside.sort(key=lambda item: int(item["left"]))
        if len(inside) > count:
            center = left + width / 2.0
            inside = sorted(inside, key=lambda item: abs((item["left"] + item["width"] / 2.0) - center))[:count]
            inside.sort(key=lambda item: int(item["left"]))
        if len(inside) == count:
            mids = [item["left"] + item["width"] / 2.0 for item in inside]
            edges = [left]
            for left_mid, right_mid in zip(mids, mids[1:]):
                edges.append(int(round((left_mid + right_mid) / 2.0)))
            edges.append(right)
        else:
            step = width / float(count)
            edges = [int(round(left + step * index)) for index in range(count)] + [right]
        for index, token in enumerate(tokens):
            x0 = int(edges[index])
            x1 = max(x0 + 1, int(edges[index + 1]))
            out.append({
                "text": token,
                "left": x0,
                "top": top,
                "width": x1 - x0,
                "height": height,
                "line": ("month", str(top)),
            })
    return out, merged


def _day_sits_above_month(piece: str, covered: list[dict]) -> bool:
    """'OCT 9' built by reading across a stacked poster is not a second date."""
    match = DATE_IN_TEXT.search(piece or "")
    if not match or not match.group("d2"):
        return False
    day_words = [word for word in covered if _DAY_TOKEN.match(str(word.get("text") or "").strip())]
    month_words = [word for word in covered if _MONTH_ONLY.match(str(word.get("text") or "").strip())]
    if not day_words or not month_words:
        return False
    return min(int(word["top"]) for word in day_words) + 4 < min(int(word["top"]) for word in month_words)


def _unreadable_reply() -> dict:
    """The reader was not sure. Do not invent a list of dates that are not there."""
    return {
        "ok": False,
        "reply": "I can't read that clearly. Nothing was changed.",
        "actions": [],
        "pending": False,
    }


def _stack_day_and_month(words: list[dict]) -> list[dict]:
    """A poster often paints the day above the month. Read that pair as one date."""
    month = _MONTH_ONLY
    days = [word for word in words if re.fullmatch(r"\d{1,2}", str(word.get("text") or "").strip())]
    months = [word for word in words if month.match(str(word.get("text") or "").strip())]
    stacked = []
    for day in days:
        day_mid = day["left"] + day["width"] / 2.0
        for name in months:
            if name["top"] < day["top"] + day["height"] * 0.4:
                continue
            gap = name["top"] - (day["top"] + day["height"])
            if gap > max(12.0, day["height"] * 0.85):
                continue
            if day_mid < name["left"] - day["width"] * 0.35 or day_mid > name["left"] + name["width"] + day["width"] * 0.35:
                continue
            left = min(day["left"], name["left"])
            top = min(day["top"], name["top"])
            right = max(day["left"] + day["width"], name["left"] + name["width"])
            bottom = max(day["top"] + day["height"], name["top"] + name["height"])
            stacked.append({
                "text": f"{str(day['text']).strip()} {str(name['text']).strip()}",
                "left": int(left),
                "top": int(top),
                "width": max(1, int(right - left)),
                "height": max(1, int(bottom - top)),
                "line": ("stacked", str(int(top))),
            })
            break
    return stacked


def _dedupe_hits(kind: str, hits: list[dict]) -> list[dict]:
    kept = []
    for hit in hits:
        duplicate = False
        for prev in kept:
            close = abs(int(hit["left"]) - int(prev["left"])) < 14 and abs(int(hit["top"]) - int(prev["top"])) < 14
            if close and _same_label(kind, hit["text"], prev["text"]):
                duplicate = True
                break
        if not duplicate:
            kept.append(hit)
    return kept


def _union_box(group: list[dict]) -> tuple[int, int, int, int]:
    left = min(int(item["left"]) for item in group)
    top = min(int(item["top"]) for item in group)
    right = max(int(item["left"]) + int(item["width"]) for item in group)
    bottom = max(int(item["top"]) + int(item["height"]) for item in group)
    return left, top, max(1, right - left), max(1, bottom - top)


def _boxes_overlap(a: dict, b: dict) -> bool:
    ax1, ay1 = int(a["left"]) + int(a["width"]), int(a["top"]) + int(a["height"])
    bx1, by1 = int(b["left"]) + int(b["width"]), int(b["top"]) + int(b["height"])
    overlap_w = min(ax1, bx1) - max(int(a["left"]), int(b["left"]))
    overlap_h = min(ay1, by1) - max(int(a["top"]), int(b["top"]))
    if overlap_w <= 0 or overlap_h <= 0:
        return False
    smaller = min(int(a["width"]) * int(a["height"]), int(b["width"]) * int(b["height"])) or 1
    return (overlap_w * overlap_h) / float(smaller) >= 0.45


def _collapse_same(kind: str, hits: list[dict]) -> list[dict]:
    """Two reads of the same value are one hit. The tighter box is the one we paint."""
    kept = []
    for hit in hits:
        placed = False
        for index, prev in enumerate(kept):
            if not _same_label(kind, hit.get("text") or "", prev.get("text") or ""):
                continue
            if not _boxes_overlap(hit, prev):
                continue
            if int(hit["width"]) * int(hit["height"]) < int(prev["width"]) * int(prev["height"]):
                kept[index] = hit
            placed = True
            break
        if not placed:
            kept.append(hit)
    return kept


def _collect_hits(words: list[dict], kind: str) -> tuple[list[dict], bool]:
    """Every value of this kind, including a day painted above its month.

    A line that holds two dates keeps a separate box for each. Painting "11 OCT"
    must not cover "10 OCT" beside it. A merged month box is split first.
    The second value is true when a month box had to be split, so a miss
    after that is an unclear read rather than a date that is not on the page.
    """
    merged = False
    pool = list(words)
    if kind == "date":
        pool, merged = _split_merged_months(words)
        pool = pool + _stack_day_and_month(pool)
    hits = []

    def add(text: str, left: int, top: int, width: int, height: int) -> None:
        for piece in _pieces(text, kind):
            hits.append({
                "text": piece,
                "left": int(left),
                "top": int(top),
                "width": max(1, int(width)),
                "height": max(1, int(height)),
            })

    for word in pool:
        add(word.get("text") or "", word["left"], word["top"], word["width"], word["height"])
    for group in _line_groups(pool if kind == "date" else words):
        if kind == "phone":
            candidate = _phone_run(group)
            if candidate:
                add(candidate["text"], candidate["left"], candidate["top"], candidate["width"], candidate["height"])
            continue
        ordered = sorted(group, key=lambda item: item["left"])
        if kind == "date":
            ordered = [word for word in ordered if not _is_time_token(word.get("text"))]
        if not ordered:
            continue
        cursor = 0
        spans = []
        parts = []
        for word in ordered:
            start = cursor
            parts.append(word["text"])
            cursor += len(word["text"])
            spans.append((start, cursor, word))
            cursor += 1
        joined = " ".join(parts)
        for piece in _pieces(joined, kind):
            at = joined.find(piece)
            if at < 0:
                continue
            end = at + len(piece)
            covered = [word for start, stop, word in spans if stop > at and start < end]
            if not covered:
                continue
            if kind == "date" and _day_sits_above_month(piece, covered):
                continue
            left, top, width, height = _union_box(covered)
            hits.append({
                "text": piece,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            })
    return _collapse_same(kind, _dedupe_hits(kind, hits)), merged


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
        hits, merged = _collect_hits(words, parsed["kind"])
        hint = str(parsed.get("oldHint") or "")
        if hint:
            matched = [word for word in hits if _hint_matches(parsed["kind"], hint, word.get("text") or "")]
            if not matched:
                if merged:
                    return {"unreadable": True}
                return {
                    "missing": True,
                    "named": hint,
                    "found": _unique_labels(parsed["kind"], [word.get("text") or "" for word in hits]),
                }
            hits = matched
        hits = _collapse_same(parsed["kind"], hits)
        if len(hits) > 1:
            return {"ambiguous": True}
        if hits:
            word = hits[0]
            return {
                "text": word["text"],
                "left": word["left"],
                "top": word["top"],
                "width": word["width"],
                "height": word["height"],
            }
        if merged:
            return {"unreadable": True}
        return None
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
    found_groups = []
    for group in _line_groups(words):
        if parsed["kind"] == "phone":
            candidate = _phone_run(group)
            if candidate is None or not fragment(candidate["text"], "phone"):
                continue
        else:
            joined = " ".join(item["text"] for item in sorted(group, key=lambda item: item["left"]))
            found = fragment(joined, parsed["kind"])
            if not found:
                continue
            candidate = boxed(group, found)
        found_groups.append(candidate)
    if len(found_groups) > 1:
        return {"ambiguous": True}
    return found_groups[0] if found_groups else None


def _file_stamp(path: str) -> str:
    try:
        stat = os.stat(path)
        return f"{stat.st_size}:{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1e9))}"
    except OSError:
        return "0:0"


def _miss_is_unreadable(words, hit, image, kind: str = "date") -> bool:
    """The named words were not read cleanly. Do not say they are absent.

    A flat poster that really has other dates still lists those dates.
    A photograph whose reader returned noise, or nothing, is unreadable.
    """
    del words
    if not hit or not hit.get("missing"):
        return False
    found = [" ".join(str(label or "").split()) for label in (hit.get("found") or [])]
    found = [label for label in found if label]
    clean = []
    for label in found:
        pieces = _pieces(label, kind)
        if pieces and len(label) <= 32 and len(label.split()) <= 6:
            clean.append(label)
    if found and len(clean) == len(found):
        return False
    if found:
        return True
    if image is not None and getattr(image, "size", 0):
        gray = image.mean(axis=2) if getattr(image, "ndim", 1) == 3 else image
        if float(gray.std()) > 18.0:
            return True
    return False


def _embedded_hit(doc, page, parsed: dict, render_scale: float, cache_key: str = "") -> dict | None:
    """Read the placed picture at its own pixels, then map the box onto the page render."""
    import cv2
    import numpy as np

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
    image = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    cache_id = f"{cache_key}|xref:{info.get('xref')}|bytes:{len(blob)}"
    words = _words_from_bgr(image, cache_id)
    hit = _select_hit(words, parsed)
    if hit and hit.get("missing") and _miss_is_unreadable(words, hit, image, str(parsed.get("kind") or "date")):
        return {"unreadable": True}
    if not hit or hit.get("ambiguous") or hit.get("missing") or hit.get("unreadable"):
        return hit if hit and (hit.get("ambiguous") or hit.get("missing") or hit.get("unreadable")) else None
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
    png = ""
    cache_key = str(proposal.get("ocrKey") or "")
    doc = fitz.open(src)
    try:
        page = doc[0]
        long_pt = max(float(page.rect.width), float(page.rect.height)) or 1.0
        scale = 2.0
        if long_pt * scale < 700:
            scale = min(6.0, 1200.0 / long_pt)
        # The placed picture is the artwork. Read that, and skip a second full-page render.
        embedded = _embedded_hit(doc, page, parsed, scale, cache_key)
        if not embedded:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            folder = tempfile.mkdtemp(prefix="edit-ocr-")
            png = os.path.join(folder, "page.png")
            pix.save(png)
    finally:
        doc.close()
    if embedded:
        hit = embedded
        crop = embedded.get("crop")
    else:
        image = cv2.imread(png) if png else None
        words = _words_from_bgr(image, cache_key + "|page") if image is not None else []
        hit = _select_hit(words, parsed)
        if hit and hit.get("missing") and _miss_is_unreadable(words, hit, image, str(parsed.get("kind") or "date")):
            hit = {"unreadable": True}
        crop = None if hit is None or image is None or hit.get("ambiguous") or hit.get("missing") or hit.get("unreadable") else image[hit["top"]: hit["top"] + hit["height"], hit["left"]: hit["left"] + hit["width"]]
    if hit and hit.get("unreadable"):
        return _unreadable_reply()
    if hit and hit.get("missing"):
        return _missing_named_reply(parsed["kind"], str(hit.get("named") or parsed.get("oldHint") or ""), hit.get("found") or [])
    if hit and hit.get("ambiguous"):
        return _many_reply(parsed["kind"])
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
    proposal["weightRatio"] = float(getattr(_closest_font, "weight_ratio", 1.0) or 1.0)
    if score < 0 or score > _FONT_SCORE_LIMIT:
        if score > _FONT_SCORE_LIMIT:
            reply = SAFE_EDIT
        else:
            reply = (
                "I could not match the original font, weight, and size closely enough, "
                "so nothing was changed. Please send the editable file if this has to stay in that exact face."
            )
        return {
            "ok": False,
            "reply": reply,
            "actions": [],
            "pending": False,
        }
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
                    "shifted": True,
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
    """insert_text ignores a font file unless that face is already on the page.

    The file has to be a real TTF or OTF. Base Helvetica is not embedded, so it
    is never installed here.
    """
    if not fontfile or fontfile in ("helv", "times", "cour") or not os.path.isfile(fontfile):
        raise ValueError("no embeddable font")
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
        sliced = None
        found_at = text.find(wanted) if wanted else -1
        if found_at >= 0 and found_at + len(wanted) <= len(chars) and (norm == wanted or wanted in text):
            sliced = chars[found_at:found_at + len(wanted)]
            sx0 = min(char[3][0] for char in sliced)
            sy0 = min(char[3][1] for char in sliced)
            sx1 = max(char[3][2] for char in sliced)
            sy1 = max(char[3][3] for char in sliced)
            box = fitz.Rect(sx0, sy0, sx1, sy1)
        if loose_rect and sliced is not None:
            center = fitz.Point((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0)
            if not loose_rect.contains(center):
                sliced = None
        if sliced is not None:
            if exact is None or (norm == wanted and len(sliced) >= len(exact)) or len(sliced) < len(exact):
                exact = sliced
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
                if not text or text == wanted or wanted in text or text in wanted:
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
    # The whole original word, not a smaller box that leaves the side letters behind.
    redact = _clear_of(fitz.Rect(ink), others, 0.35)
    pieces = []
    if isinstance(redact, list):
        pieces.extend(redact)
    elif redact is not None:
        pieces.append(redact)
    # Every old glyph still has to meet a redaction box, or that letter stays.
    for quad in quads:
        if _redact_hits(pieces, quad):
            continue
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
    span_width = float(ink.width) if ink is not None and ink.width > 1 else old_width
    if align == "center" and ink is not None:
        room = span_width
    else:
        room = max(4.0, (column.x1 - pad) - origin_x)
    shrunk = False
    # A centred line, including a venue, stays inside the old width.
    # A picture's words do too: there is no column to grow into.
    # A left-aligned live line may use the rest of its column.
    if align in ("center", "fit") and width > span_width + 0.4:
        size = max(4.0, size * (span_width / width) * 0.98)
        width = float(face.text_length(text, fontsize=size))
        shrunk = True
    elif width > old_width + 0.4 and width > room:
        size = max(4.0, size * (room / width) * 0.98)
        width = float(face.text_length(text, fontsize=size))
        shrunk = True
    if align == "center" and ink is not None:
        center = (ink.x0 + ink.x1) / 2.0
        origin_x = center - width / 2.0
    elif align == "fit" and ink is not None:
        origin_x = float(ink.x0)
    elif ink is not None and origin_x + width > ink.x1 + 0.4 and width <= span_width + 0.6:
        origin_x = ink.x1 - width
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
    if proposal.get("shifted") and asked and len(asked) >= 2:
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
        placed = fitz.Rect(float(origin[0]), ink.y0, float(origin[0]) + width, ink.y1)
        if str(proposal.get("align") or "") == "center":
            box = fitz.Rect(ink)
        else:
            box = fitz.Rect(min(ink.x0, placed.x0), ink.y0, max(ink.x1, placed.x1), ink.y1)
        proposal["editBox"] = [box.x0, box.y0, box.x1, box.y1]
        proposal["guardBox"] = [box.x0, box.y0, box.x1, box.y1]
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
    # A Canva heading can also be painted into the picture under the live letters.
    # Clear only this word's leftover pixels, so the live copy is the one that remains.
    cleared = _clear_baked(page, ink)
    if cleared is not None and proposal.get("editBox"):
        box = fitz.Rect(proposal["editBox"])
        box = fitz.Rect(
            min(box.x0, cleared.x0),
            min(box.y0, cleared.y0),
            max(box.x1, cleared.x1),
            max(box.y1, cleared.y1),
        )
        proposal["editBox"] = [box.x0, box.y0, box.x1, box.y1]
        proposal["guardBox"] = [box.x0, box.y0, box.x1, box.y1]
    _place_font_text(page, origin, text, size, rgb, fontfile)


def _clear_baked(page, ink):
    """Inpaint word-shaped ink that is still in an image after the live glyphs are gone.

    The colour sample is a thin ring just outside the word, so the baked letters
    are not mistaken for the background. A heading that also lives in the text
    layer stays, because only this word's box is painted. A photograph is left.
    """
    import pymupdf as fitz
    import numpy as np

    if ink is None or ink.width < 0.4 or ink.height < 0.4:
        return
    overlaps = False
    for info in page.get_image_info() or []:
        if fitz.Rect(info.get("bbox") or page.rect).intersects(ink):
            overlaps = True
            break
    if not overlaps:
        return
    pad = 2.0
    outer = fitz.Rect(ink.x0 - pad, ink.y0 - pad, ink.x1 + pad, ink.y1 + pad) & page.rect
    if outer.width < 1 or outer.height < 1:
        return
    scale = 4.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=outer, alpha=False, colorspace=fitz.csRGB)
    if pix.width < 4 or pix.height < 4:
        return
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
    ix0 = max(0, int(round((ink.x0 - outer.x0) * scale)))
    iy0 = max(0, int(round((ink.y0 - outer.y0) * scale)))
    ix1 = min(image.shape[1], int(round((ink.x1 - outer.x0) * scale)))
    iy1 = min(image.shape[0], int(round((ink.y1 - outer.y0) * scale)))
    if ix1 - ix0 < 2 or iy1 - iy0 < 2:
        return
    ring_mask = np.ones(image.shape[:2], dtype=bool)
    ring_mask[iy0:iy1, ix0:ix1] = False
    ring = image[ring_mask]
    if ring.shape[0] < 8:
        return
    ring_f = ring.astype(np.float32)
    background = np.median(ring_f, axis=0)
    # A few baked glyph edges leak into the ring and inflate the spread. The
    # panel is still flat when most of the ring matches the median.
    near = np.linalg.norm(ring_f - background, axis=1) <= 28.0
    flat = float(near.mean()) if near.size else 0.0
    # A flat or gently graded panel is filled across the whole word. A shorter
    # line must not leave a ghost of the old letters on either side.
    if flat >= 0.6:
        import cv2
        hole = np.zeros(image.shape[:2], dtype=np.uint8)
        hole[iy0:iy1, ix0:ix1] = 255
        # The baked letters often stick out past the live glyphs. Grow the hole
        # so that halo is filled, and the colour sample is the real panel.
        grown = cv2.dilate(hole, np.ones((11, 11), np.uint8), iterations=1)
        painted, spread = _paint_plane(image, grown)
        if painted is not None and spread <= 18.0:
            ys, xs = np.where(grown > 0)
            gx0, gx1 = int(xs.min()), int(xs.max()) + 1
            gy0, gy1 = int(ys.min()), int(ys.max()) + 1
            patch = np.ascontiguousarray(painted[gy0:gy1, gx0:gx1])
            target = fitz.Rect(
                outer.x0 + gx0 / scale,
                outer.y0 + gy0 / scale,
                outer.x0 + gx1 / scale,
                outer.y0 + gy1 / scale,
            )
            overlay = fitz.Pixmap(fitz.csRGB, patch.shape[1], patch.shape[0], patch.tobytes(), 0)
            page.insert_image(target, pixmap=overlay, overlay=True)
            return target
    distance = np.linalg.norm(image.astype(np.float32) - background, axis=2)
    import cv2

    core = np.zeros(image.shape[:2], dtype=np.uint8)
    core[iy0:iy1, ix0:ix1] = 1
    reach = max(1, int(round(1.5 * scale)))
    search = cv2.dilate(core, np.ones((reach * 2 + 1, reach * 2 + 1), np.uint8), iterations=1)
    strokes = ((distance > 22.0) & (search > 0)).astype(np.uint8)
    count, labels = cv2.connectedComponents(strokes)
    mask = np.zeros(strokes.shape, dtype=bool)
    core_bool = core.astype(bool)
    for index in range(1, count):
        comp = labels == index
        if np.any(comp & core_bool):
            mask |= comp
    share = float(mask[iy0:iy1, ix0:ix1].mean()) if mask[iy0:iy1, ix0:ix1].size else 0.0
    # Letters on a flat panel can fill a tight box. A photograph's ring does not
    # match one colour, so that case is left alone.
    if int(mask.sum()) < 8 or share > 0.85:
        return None
    if flat < 0.55 and share > 0.45:
        return None
    ys, xs = np.where(mask)
    if ys.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    crop_mask = mask[y0:y1, x0:x1]
    painted = cv2.inpaint(image[y0:y1, x0:x1], crop_mask.astype(np.uint8) * 255, 2, cv2.INPAINT_TELEA)
    patch = image[y0:y1, x0:x1].copy()
    patch[crop_mask] = painted[crop_mask]
    patch = np.ascontiguousarray(patch)
    target = fitz.Rect(
        outer.x0 + x0 / scale,
        outer.y0 + y0 / scale,
        outer.x0 + x1 / scale,
        outer.y0 + y1 / scale,
    )
    overlay = fitz.Pixmap(fitz.csRGB, patch.shape[1], patch.shape[0], patch.tobytes(), 0)
    page.insert_image(target, pixmap=overlay, overlay=True)
    return target


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


def _ink_cut(distance) -> float:
    """How far from the border a pixel has to be before it counts as a letter.

    On a flat page the letters sit well clear of the paper, and 48 is enough.
    On a photograph the same cut swallows the person, because skin is also far
    from a pink border. The letters are the separate peak at the far end of
    the distance histogram, and the cut is the valley in front of that peak.
    """
    import numpy as np

    flat = np.asarray(distance, dtype=np.float32).ravel()
    if flat.size < 30:
        return 48.0
    hi = float(np.percentile(flat, 99.7))
    if hi < 36.0:
        return 48.0
    hist, edges = np.histogram(flat, bins=30, range=(0.0, hi))
    smooth = np.convolve(hist.astype(np.float64), np.array([1.0, 2.0, 1.0]) / 4.0, mode="same")
    last_peak = len(smooth) - 1
    # White type on a person is the last bin, standing above the bin beside it.
    # The shoulder is an earlier bump. Walking inward and stopping on that bump
    # used to throw the cut away and paint the person as if it were the letters.
    endpoint_is_peak = len(smooth) < 2 or smooth[-1] >= smooth[-2]
    if not endpoint_is_peak:
        for index in range(len(smooth) - 2, max(2, len(smooth) // 3), -1):
            if smooth[index] >= smooth[index - 1] and smooth[index] >= smooth[index + 1]:
                last_peak = index
                break
    left_peak = int(np.argmax(smooth[:max(1, last_peak)]))
    if last_peak <= left_peak + 1:
        return 48.0
    valley = left_peak + int(np.argmin(smooth[left_peak:last_peak + 1]))
    if smooth[valley] > smooth[last_peak] * 0.55:
        return 48.0
    return float(edges[min(valley + 1, len(edges) - 1)])


def _raster_ink(image, left: int, top: int, width: int, height: int):
    """The strokes of this word, grown from the reader box to the whole glyph."""
    import cv2
    import numpy as np

    height_px, width_px = image.shape[:2]
    # A short reader box has to grow to the whole glyph. A day-over-month box is
    # already the whole date, and a big grow would swallow the line underneath.
    if height > 80:
        grow_y, grow_x = 8, 4
    else:
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
    foreground = (distance > _ink_cut(distance)).astype(np.uint8)
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
        slack = min(height * 0.35, 14)
        if center_y < box_y0 - slack or center_y > box_y1 + slack:
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
        "color": _ink_color(window, keep, distance, background),
    }


def _ink_color(window, keep, distance, background):
    """The letter colour, not the shadow sitting next to a light face.

    White type on a pink ground is the pixels furthest from that ground.
    A median of every dark and light pixel comes out mauve.
    """
    import numpy as np

    if not np.any(keep):
        return background
    chosen = window[keep > 0].astype(np.float32)
    dist = distance[keep > 0]
    if dist.size < 8:
        return np.median(chosen, axis=0)
    cut = float(np.percentile(dist, 75))
    strong = chosen[dist >= cut]
    if strong.shape[0] < 8:
        strong = chosen
    return np.median(strong, axis=0)


def _paint_plane(window, mask):
    """Fill the letters with a colour plane fitted to the pixels around them.

    A flat panel and a smooth gradient both fit. A photograph does not, and
    that residual is what the cleanliness check uses.
    """
    import cv2
    import numpy as np

    if window is None or mask is None:
        return None, 99.0
    core = mask > 0
    if int(core.sum()) < 8:
        return None, 99.0
    ring_reach = cv2.dilate(core.astype(np.uint8), np.ones((9, 9), np.uint8), iterations=1)
    ring = (ring_reach > 0) & ~core
    ys, xs = np.where(ring)
    if len(xs) < 24:
        return None, 99.0
    colors = window[ys, xs].astype(np.float64)
    design = np.stack([np.ones(len(xs)), xs.astype(np.float64), ys.astype(np.float64)], 1)
    coef, *_ = np.linalg.lstsq(design, colors, rcond=None)
    residual = np.linalg.norm(colors - (design @ coef), axis=1)
    keep = residual <= np.percentile(residual, 80)
    if int(keep.sum()) >= 24:
        coef, *_ = np.linalg.lstsq(design[keep], colors[keep], rcond=None)
        residual = np.linalg.norm(colors[keep] - (design[keep] @ coef), axis=1)
    spread = float(np.median(residual))
    painted = window.copy()
    fy, fx = np.where(core)
    hole = np.stack([np.ones(len(fx)), fx.astype(np.float64), fy.astype(np.float64)], 1)
    painted[fy, fx] = np.clip(hole @ coef, 0, 255).astype(np.uint8)
    return painted, spread


def _fill_is_clean(window, mask, painted=None) -> bool:
    """The hole follows the colour around the letters.

    A flat panel and a smooth gradient pass. A photograph's ring does not
    lie on one plane, so that edit is refused.
    """
    filled, spread = _paint_plane(window, mask)
    return filled is not None and spread <= 18.0


def _glyph_height(mask) -> float:
    """Median letter height. A day stacked on its month is not one giant glyph."""
    import cv2
    import numpy as np

    if mask is None or getattr(mask, "size", 0) == 0:
        return 0.0
    count, labels = cv2.connectedComponents((mask > 0).astype(np.uint8))
    heights = []
    for index in range(1, count):
        ys = np.where(labels == index)[0]
        if ys.size < 8:
            continue
        heights.append(float(ys.max() - ys.min() + 1))
    if not heights:
        return 0.0
    heights.sort()
    return heights[len(heights) // 2]


def _asc_desc(fontfile: str) -> tuple[float, float]:
    """Ascender and descender as fractions of the em. Both stay inside the old word."""
    import pymupdf as fitz

    try:
        face = fitz.Font(fontfile=fontfile) if fontfile and os.path.isfile(fontfile) else fitz.Font("helv")
        asc = float(face.ascender or 0.8)
        desc = float(face.descender or -0.2)
    except Exception:
        asc, desc = 0.8, -0.2
    if abs(asc) > 2:
        asc = asc / 1000.0
    if abs(desc) > 2:
        desc = desc / 1000.0
    if asc < 0.2:
        asc = 0.8
    if desc > -0.01:
        desc = -0.2
    return asc, desc


_DIGIT_METRICS: dict[str, tuple[float, float]] = {}


def _digit_metrics(fontfile: str) -> tuple[float, float]:
    """Digit ink height and the baseline offset, both as fractions of the em.

    A phone number is sized by the height of an '8', not by the font's
    ascender-plus-descender box. That box is taller than the digits, and
    dividing by it set a 6 pt number at under 5 pt.
    """
    cached = _DIGIT_METRICS.get(fontfile or "")
    if cached:
        return cached
    from PIL import ImageFont

    size = 200
    try:
        font = ImageFont.truetype(fontfile, size) if fontfile and os.path.isfile(fontfile) else None
    except (OSError, ValueError):
        font = None
    if font is None:
        _DIGIT_METRICS[fontfile or ""] = (0.7, 0.7)
        return 0.7, 0.7
    bbox = font.getbbox("8")
    ascent, _descent = font.getmetrics()
    ink_h = max(1, int(bbox[3] - bbox[1]))
    top_from_baseline = max(1, int(ascent - int(bbox[1])))
    height_ratio = min(0.95, max(0.35, ink_h / float(size)))
    base_ratio = min(1.15, max(0.35, top_from_baseline / float(size)))
    _DIGIT_METRICS[fontfile or ""] = (height_ratio, base_ratio)
    return height_ratio, base_ratio


def _em_for_ink(fontfile: str, ink_height: float) -> float:
    """Point size whose letters are as tall as the ink already on the page."""
    import pymupdf as fitz

    height = float(ink_height or 0)
    if height < 1:
        return 12.0
    try:
        face = fitz.Font(fontfile=fontfile) if fontfile and os.path.isfile(fontfile) else fitz.Font("helv")
        asc = float(face.ascender or 0.8)
    except Exception:
        asc = 0.8
    if asc > 2:
        asc = asc / 1000.0
    cap = min(0.82, max(0.55, asc * 0.78))
    return height / cap


def _shape_metrics(fontfile: str, sample: str) -> tuple[float, float]:
    """Ink height and baseline offset of this sample, as fractions of the em."""
    key = (fontfile or "") + "\n" + sample
    cached = _DIGIT_METRICS.get(key)
    if cached:
        return cached
    from PIL import ImageFont

    size = 200
    try:
        font = ImageFont.truetype(fontfile, size) if fontfile and os.path.isfile(fontfile) else None
    except (OSError, ValueError):
        font = None
    if font is None:
        _DIGIT_METRICS[key] = (0.7, 0.7)
        return 0.7, 0.7
    bbox = font.getbbox(sample)
    ascent, _descent = font.getmetrics()
    ink_h = max(1, int(bbox[3] - bbox[1]))
    top_from_baseline = max(1, int(ascent - int(bbox[1])))
    height_ratio = min(0.95, max(0.35, ink_h / float(size)))
    base_ratio = min(1.15, max(0.35, top_from_baseline / float(size)))
    _DIGIT_METRICS[key] = (height_ratio, base_ratio)
    return height_ratio, base_ratio


def _stacked_lines(found, scale: float, fontfile: str, text: str):
    """A day painted above its month stays two lines, each at its own height.

    '12' is wider than '11'. It is sized to the day height and may use the
    width of the whole date column. It is not shrunk to the narrower day.
    """
    import numpy as np
    import pymupdf as fitz

    tokens = [part for part in str(text or "").split() if part]
    mask = found["mask"] > 0
    bands = _horizontal_bands(mask)
    if len(bands) < 2 or len(tokens) < 2 or not fontfile:
        return None
    face = fitz.Font(fontfile=fontfile)
    win_x0, win_y0, win_x1, win_y1 = found["win"]
    column = np.zeros(mask.shape[1], dtype=bool)
    for y0, y1 in (bands[0], bands[-1]):
        column |= mask[y0:y1].any(axis=0)
    xs = np.where(column)[0]
    if len(xs) == 0:
        return None
    col_left, col_right = int(xs.min()), int(xs.max()) + 1
    col_width = (col_right - col_left) / float(scale)
    placed = []
    for (y0, y1), token in ((bands[0], tokens[0]), (bands[-1], tokens[-1])):
        band = mask[y0:y1]
        band_xs = np.where(band.any(axis=0))[0]
        if len(band_xs) == 0:
            return None
        rect_top = (win_y0 + y0) / scale
        rect_bottom = (win_y0 + y1) / scale
        letter_pt = float(rect_bottom - rect_top)
        if letter_pt < 1:
            return None
        sample = "8" if any(ch.isdigit() for ch in token) else "H"
        height_ratio, base_ratio = _shape_metrics(fontfile, sample)
        size = letter_pt / height_ratio
        width = float(face.text_length(token, fontsize=size))
        # The column is the room the date already occupies. A wider digit may
        # use it. Past that, shrinking the face would no longer match.
        if width > col_width + 0.4:
            size = max(4.0, size * ((col_width / max(width, 0.1)) * 0.98))
            width = float(face.text_length(token, fontsize=size))
        shift = abs(size * height_ratio - letter_pt) / letter_pt
        if shift > _SIZE_TOLERANCE:
            return "refuse"
        shrunk = shift > 0.08
        origin_x = (win_x0 + col_left) / scale + max(0.0, (col_width - width) / 2.0)
        if origin_x < (win_x0 / scale) - 0.6 or origin_x + width > (win_x1 / scale) + 0.6:
            return "refuse"
        placed.append({
            "origin": (origin_x, rect_top + size * base_ratio),
            "text": token,
            "size": size,
            "shrunk": shrunk,
            "letter": letter_pt,
            "digit": size * height_ratio,
        })
    return placed


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
        proposal["refuse"] = SAFE_EDIT
        return
    window = found["window"]
    painted, spread = _paint_plane(window, found["mask"])
    if painted is None or spread > 18.0:
        proposal["refuse"] = SAFE_EDIT
        return
    # Fill the whole letter rectangle, plus a hair past it, when that still
    # matches the background. The letter mask stays the strokes, so a day
    # stacked on its month is still two lines.
    bx0, by0, bx1, by1 = found["bounds"]
    pad = 2
    rx0, ry0 = max(0, bx0 - pad), max(0, by0 - pad)
    rx1 = min(window.shape[1], bx1 + pad)
    ry1 = min(window.shape[0], by1 + pad)
    wide = np.zeros(found["mask"].shape, np.uint8)
    wide[ry0:ry1, rx0:rx1] = 255
    wide_painted, wide_spread = _paint_plane(window, wide)
    if wide_painted is not None and wide_spread <= 18.0:
        painted = wide_painted
        found = dict(found)
        found["bounds"] = (rx0, ry0, rx1, ry1)
    x0, y0, x1, y1 = found["bounds"]
    patch = painted[y0:y1, x0:x1]
    win_x0, win_y0, _win_x1, _win_y1 = found["win"]
    clip = fitz.Rect((win_x0 + x0) / scale, (win_y0 + y0) / scale, (win_x0 + x1) / scale, (win_y0 + y1) / scale)
    ink = fitz.Rect(clip)
    fontfile = proposal.get("fontfile") or ((_candidate_fonts() or [""])[0])
    stacked = _stacked_lines(found, scale, fontfile, str(proposal.get("new") or ""))
    if stacked == "refuse":
        proposal["refuse"] = SAFE_EDIT
        return
    if stacked:
        overlay = fitz.Pixmap(fitz.csRGB, patch.shape[1], patch.shape[0], np.ascontiguousarray(patch).tobytes(), 0)
        page.insert_image(clip, pixmap=overlay, overlay=True)
        color = found["color"]
        rgb = (float(color[0]) / 255.0, float(color[1]) / 255.0, float(color[2]) / 255.0)
        for line in stacked:
            _place_font_text(page, line["origin"], line["text"], line["size"], rgb, fontfile)
        proposal["glyphPt"] = float(stacked[0]["letter"])
        proposal["digitPt"] = float(stacked[0]["digit"])
        proposal["shrunk"] = any(line["shrunk"] for line in stacked)
        proposal["editBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
        proposal["guardBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
        return
    letter_px = _glyph_height(found["mask"])
    bounds_px = max(1.0, float(y1 - y0))
    letter_pt = ink.height * (letter_px / bounds_px) if letter_px >= 4 else ink.height
    height_ratio, base_ratio = _digit_metrics(fontfile)
    natural = (letter_pt / height_ratio) * float(proposal.get("sizeFactor") or 1.0)
    origin_y = ink.y0 + natural * base_ratio
    center_x = (ink.x0 + ink.x1) / 2.0
    page_mid = (page.rect.x0 + page.rect.x1) / 2.0
    align = "center" if abs(center_x - page_mid) <= 14 else "fit"
    origin, size, shrunk, text_width = _fit_on_line(
        page,
        str(proposal.get("new") or ""),
        str(proposal.get("old") or ""),
        natural,
        fontfile,
        align,
        (ink.x0, origin_y),
        ink,
    )
    overlay = fitz.Pixmap(fitz.csRGB, patch.shape[1], patch.shape[0], np.ascontiguousarray(patch).tobytes(), 0)
    page.insert_image(clip, pixmap=overlay, overlay=True)
    # The baseline puts the digit ink on the old digit ink, not the font's descender box.
    origin = (origin[0], ink.y0 + size * base_ratio)
    proposal["glyphPt"] = float(letter_pt)
    proposal["digitPt"] = float(size) * float(height_ratio)
    proposal["shrunk"] = bool(shrunk)
    proposal["editBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
    proposal["guardBox"] = [ink.x0, ink.y0, ink.x1, ink.y1]
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


_RGB_CACHE: dict = {}


def _render_rgb(path: str, scale: float = 2.5):
    import pymupdf as fitz
    import numpy as np

    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns if hasattr(stat, "st_mtime_ns") else stat.st_mtime, stat.st_size)
    except OSError:
        stamp = (0, 0)
    key = (os.path.abspath(path), stamp, round(float(scale), 3))
    cached = _RGB_CACHE.get(key)
    if cached is not None:
        return cached
    doc = fitz.open(path)
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3).copy()
    finally:
        doc.close()
    if len(_RGB_CACHE) > 6:
        _RGB_CACHE.clear()
    _RGB_CACHE[key] = image
    return image


def _rapid_line(image) -> str:
    """A second reader for white type on a photograph, which Tesseract often skips."""
    try:
        import cv2
        from ocr_reader import local_rows

        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if getattr(image, "ndim", 0) == 3 else image
        rows = local_rows(bgr) or []
    except Exception:
        return ""
    parts = []
    for item in rows:
        try:
            text = str(item.get("text") or item.get("txt") or "") if isinstance(item, dict) else str(item[1] or "")
        except Exception:
            text = ""
        text = text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _tesseract(image, psm: str, tsv: bool) -> str:
    import cv2

    folder = tempfile.mkdtemp(prefix="edit-gate-")
    path = os.path.join(folder, "page.png")
    cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    cmd = ["tesseract", path, "stdout", "--psm", psm]
    if tsv:
        cmd.append("tsv")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
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


def _pdf_has_reading(path: str, wanted: str, digits: bool) -> bool:
    """The new words are in the PDF text layer, which does not depend on OCR."""
    if not wanted or not path or not os.path.isfile(path):
        return False
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        text = doc[0].get_text("text") or ""
    finally:
        doc.close()
    return wanted in _read_as(text, digits)


def _fresh_font_problems(before_pdf: str, after_pdf: str) -> list[str]:
    """Embedding or substitution problems the edit added. Problems already in the file stay."""
    from client_file_audit import font_report

    def marked(path: str) -> set[tuple[str, str]]:
        report = font_report(path)
        found = set()
        for row in report.get("problems") or []:
            name = str(row.get("name") or "")
            reason = str(row.get("reason") or "")
            low = f"{name} {reason}".lower()
            if "embed" in low or "substitut" in low or "helvetica" in low:
                found.add((name, reason))
        return found

    fresh = []
    for name, reason in sorted(_marked_safe(after_pdf, marked) - _marked_safe(before_pdf, marked)):
        fresh.append(f"{name} ({reason})")
    return fresh


def _marked_safe(path: str, marked) -> set[tuple[str, str]]:
    try:
        return marked(path)
    except Exception:
        return {("unreadable", "not embedded")}


def _plain_block(problems: list[str]) -> str:
    """What to tell Ian when the preview is not good enough to apply."""
    blob = " ".join(problems or [])
    sentences = []
    if "pixels outside" in blob or "page size changed" in blob:
        sentences.append("The preview changed the page outside those words.")
    if "region OCR" in blob:
        sentences.append("I could not read the new words in the preview.")
    if "page OCR" in blob:
        sentences.append("Other words on the page changed.")
    if "glyph" in blob:
        sentences.append("Some of the new letters did not render.")
    if "font not embedded" in blob:
        sentences.append("The new font did not embed.")
    if not sentences:
        sentences.append("The preview did not pass the checks.")
    return " ".join(sentences) + " " + SAFE_EDIT


def _glyph_problems(pdf: str, text: str, fontfile: str, box) -> list[str]:
    """Every letter of the new words has to extract and paint. One shared glyph does not count."""
    import pymupdf as fitz
    import numpy as np

    letters = [char for char in (text or "") if not char.isspace()]
    if not letters:
        return []
    problems = []
    if fontfile and not _font_file_usable(fontfile, text):
        problems.append("glyph did not render")
    doc = fitz.open(pdf)
    try:
        page = doc[0]
        got = page.get_text("text") or ""
        if _hint_key(text) not in _hint_key(got):
            problems.append("glyph did not render")
        wanted = _hint_key(text)
        span_size = 0.0
        for block in (page.get_text("dict") or {}).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    key = _hint_key(span.get("text") or "")
                    # A stacked date is two spans, "12" and "OCT", not one "12 OCT".
                    if not key or (wanted not in key and key not in wanted):
                        continue
                    span_size = max(span_size, float(span.get("size") or 0))
                    if str(span.get("font") or "").strip() in ("(null)", "null", ""):
                        problems.append("glyph did not render")
        if box and len(box) >= 4 and fontfile and os.path.isfile(fontfile):
            rect = fitz.Rect(box) & page.rect
            grown = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2) & page.rect
            if grown.width > 2 and grown.height > 2:
                pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=grown, alpha=False, colorspace=fitz.csRGB)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
                frame = max(2, int(2 * 4))
                border = np.concatenate([
                    image[:frame].reshape(-1, 3),
                    image[-frame:].reshape(-1, 3),
                    image[:, :frame].reshape(-1, 3),
                    image[:, -frame:].reshape(-1, 3),
                ]).astype(np.float32)
                background = np.median(border, axis=0)
                delta = np.linalg.norm(image.astype(np.float32) - background, axis=2)
                cols = int((delta.max(axis=0) > 18).sum()) if delta.size else 0
                # A day stacked on its month makes a tall box. Measure the letters
                # at the size they were set, and the wider line, not the whole phrase.
                measure = span_size if span_size > 0 else float(rect.height)
                pieces = [part for part in re.split(r"\s+", text or "") if part]
                stacked = "\n" in got and len(pieces) > 1
                try:
                    from PIL import ImageFont

                    drawn = ImageFont.truetype(fontfile, max(8, int(round(measure * 4))))
                    if stacked:
                        expected = max(float(drawn.getlength(part)) for part in pieces)
                    else:
                        expected = float(drawn.getlength(text))
                except (OSError, ValueError):
                    expected = float(len(letters) * measure * 2)
                if float(border.std()) < 30.0 and cols < max(6.0, expected * 0.45):
                    problems.append("glyph did not render")
    finally:
        doc.close()
    if problems:
        return ["glyph did not render"]
    return []


def _span_points(path: str, text: str) -> float:
    """The point size of the new words, or 0 when they are not in the file."""
    import pymupdf as fitz

    if not path or not os.path.isfile(path):
        return 0.0
    wanted = _hint_key(text)
    if not wanted:
        return 0.0
    size = 0.0
    doc = fitz.open(path)
    try:
        for block in (doc[0].get_text("dict") or {}).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    if wanted not in _hint_key(span.get("text") or ""):
                        continue
                    size = max(size, float(span.get("size") or 0))
    finally:
        doc.close()
    return size


def _offer_block(src: str, staged: str, proposal: dict) -> str:
    """Do not offer Apply when the result would fail the checks.

    Font, size, and weight have to stay near the original, the new words have
    to clear the 5 pt minimum, and a file that passed E5 must still pass it.
    """
    if proposal.get("kind") not in ("phone", "date", "time", "venue"):
        return ""
    if proposal.get("mode") not in ("live-text", "raster-text"):
        return ""
    new = str(proposal.get("shownNew") or proposal.get("new") or "")
    points = _span_points(staged, new)
    if 0 < points < 5.0:
        return SAFE_EDIT
    glyph = float(proposal.get("glyphPt") or 0)
    final = float(proposal.get("digitPt") or 0)
    if glyph > 1.0 and final > 0 and abs(final - glyph) / glyph > _SIZE_TOLERANCE:
        return SAFE_EDIT
    weight = float(proposal.get("weightRatio") or 1.0)
    if weight > 1.0 + _WEIGHT_TOLERANCE or (0 < weight < 1.0 - _WEIGHT_TOLERANCE):
        return SAFE_EDIT
    try:
        from extra_checks import check_e5

        after = check_e5(staged)
        before = check_e5(src) if src and os.path.isfile(src) else {}
    except Exception:
        return ""
    if after.get("status") == "failed" and before.get("status") != "failed":
        return SAFE_EDIT
    return ""


def _preview_block(before_pdf: str, after_pdf: str, proposal: dict) -> str:
    """Gates (a), (b), (c) and a rendered glyph, before Apply is offered."""
    if proposal.get("kind") not in ("phone", "date", "time", "venue"):
        return ""
    if proposal.get("mode") not in ("live-text", "raster-text"):
        return ""
    box = proposal.get("editBox") or proposal.get("bbox")
    old = str(proposal.get("shownOld") or proposal.get("old") or "")
    new = str(proposal.get("shownNew") or proposal.get("new") or "")
    problems = visual_gates(before_pdf, after_pdf, box, old, new)
    problems.extend(_glyph_problems(after_pdf, new, str(proposal.get("fontfile") or ""), box))
    if not problems:
        return ""
    return _plain_block(problems)


def visual_gates(before_pdf: str, after_pdf: str, edit_box, old_text: str, new_text: str) -> list[str]:
    """Four gates. A neighbour deleted, a flat block, a missing new word, or a new unembedded font fails.

    (a) Outside a small margin of the old word box, the page pixels stay put.
    (b) OCR of that region reads the new words.
    (c) OCR of the whole page changes only by those words. The edit rectangle is
        painted back from the before image first, so a re-read of decoration
        that did not move cannot fail this gate.
    (d) The edit did not add an unembedded or substituted font.
    """
    import numpy as np

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
    # Any pixel outside the word fails. A style that spills is not offered.
    if changed > 0:
        problems.append(f"pixels outside the word {changed}")
    digits = sum(ch.isdigit() for ch in (new_text or "")) >= max(1, int(0.6 * len(re.sub(r"\s", "", new_text or "x"))))
    wanted = _read_as(new_text, digits)
    old_compact = _read_as(old_text, digits)
    text_has_new = _pdf_has_reading(after_pdf, wanted, digits)
    inside_changed = int((delta[y0:y1, x0:x1] > 12).sum()) if y1 > y0 and x1 > x0 else 0
    fresh_fonts = _fresh_font_problems(before_pdf, after_pdf)
    if fresh_fonts:
        problems.append("font not embedded " + "; ".join(fresh_fonts))
    # The new words are already in the file, and nothing outside the box moved.
    # Reading the whole poster again is what pushed picture edits to the time limit.
    if changed == 0 and text_has_new and inside_changed > 20:
        return problems
    if changed > 0:
        return problems
    pad = int(round(3 * scale))
    crop = after[max(0, y0 - pad): min(after.shape[0], y1 + pad), max(0, x0 - pad): min(after.shape[1], x1 + pad)]
    readings = []
    if crop.size:
        import cv2
        bigger = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        readings.append(" ".join(item[4] for item in _tsv_words(bigger)))
        readings.append(_tesseract(bigger, "6", False))
        readings.append(_tesseract(bigger, "7", False))
        readings.append(_rapid_line(bigger))
    compacts = [_read_as(text, digits) for text in readings if text and text.strip()]
    if wanted and not any(wanted in item for item in compacts):
        if not text_has_new:
            problems.append("region OCR missed " + (new_text or "")[:40])
    if old_compact and old_compact != wanted and any(old_compact in item for item in compacts):
        problems.append("region OCR still has the old words")
    return problems


def gate_line(problems: list[str]) -> str:
    """Pass or fail for the pixel gate, the region read, the rest of the page, and the embedded face."""
    blob = " ".join(problems or [])
    structural = "no word box" in blob or "missing preview" in blob
    pixel = structural or "pixels outside" in blob or "page size changed" in blob
    region = structural or "region OCR" in blob
    page = structural or "page OCR" in blob or "page size changed" in blob
    font = structural or "font not embedded" in blob
    return "a={0} b={1} c={2} font={3}".format(
        "fail" if pixel else "pass",
        "fail" if region else "pass",
        "fail" if page else "pass",
        "fail" if font else "pass",
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
