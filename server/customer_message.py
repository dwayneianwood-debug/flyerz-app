"""Plain customer note for a finished press file."""

from __future__ import annotations

import re

_NUM_PREFIX = re.compile(r"^(?:[A-Z]\d+|\d+[a-z]?)\.\s+", re.I)


def _already_done(line: str, done_text: str) -> bool:
    """A pre-check line that the press file has already put right."""
    low = line.lower()
    if any(phrase in low for phrase in ("no bleed", "past the trim", "bleed is always 5")) and (
        "bleed:" in done_text or "5 mm" in done_text
    ):
        return True
    if any(phrase in low for phrase in ("cmyk", "is rgb", "rgb.")) and "cmyk" in done_text:
        return True
    if ("border" in low or "keyline" in low) and "thin border" in done_text:
        return True
    return False


_PPI_LINE = re.compile(r"\babout\s+\d+\s*(?:ppi|dpi)\b", re.I)


def _point_key(line: str) -> str:
    """One customer point per subject.

    A numbered line and the plain line with the same name are the same point.
    Resolution, the lowest-picture line, and a second "about N ppi" line are
    the same point. The shape sentence and "Ordered product vs file size" are
    one point. AI Resolution Enhancement stays its own point.
    """
    plain = _NUM_PREFIX.sub("", str(line or "").strip())
    name = plain.split(":", 1)[0].strip().lower()
    body = plain.lower()
    if "ai resolution" in name:
        return "ai"
    if "raster" in name or "soft text" in name or ("raster text" in body and "resolution" not in name):
        return "raster"
    if name.startswith("resolution") or "dpi validation" in name:
        return "resolution"
    if "lowest picture" in body or _PPI_LINE.search(body):
        return "resolution"
    if (
        "ordered product vs file size" in name
        or "aspect differs" in body
        or "different shape" in body
    ):
        return "shape"
    if "border near the cut" in name or "thin border" in body or name.startswith("keyline"):
        return "border"
    if name:
        return name
    return ""


def _concern_topic(line: str) -> str:
    key = _point_key(line)
    if key == "shape":
        return "size"
    return key


def _plain_topic(line: str, topic: str) -> str:
    plain = _NUM_PREFIX.sub("", str(line or "").strip())
    if ":" in plain:
        name, detail = plain.split(":", 1)
        detail = detail.strip()
    else:
        name, detail = "", plain
    if topic == "ai":
        return f"AI Resolution Enhancement: {detail}".rstrip(": ").strip()
    if topic == "resolution":
        if name.strip().lower().startswith("resolution") or "dpi validation" in name.strip().lower():
            return f"Resolution: {detail}".rstrip(": ").strip()
        return plain
    if topic == "size":
        if "ordered product" in name.strip().lower():
            return f"Ordered product vs file size: {detail}".rstrip(": ").strip()
        return f"Ordered product vs file size: {plain}".strip()
    return plain


def _shape_sentence(lines: list) -> str:
    for line in lines or []:
        text = str(line or "").strip()
        if "different shape" in text.lower() and "extended the background" in text.lower():
            return text
    return ""


def customer_message(actions: list, concerns: list, bleed_note: str = "") -> str:
    """What was done, then the amber warnings and any red items Ian accepted."""
    done = [str(line).strip() for line in actions or [] if str(line).strip()]
    done_text = " ".join(done).lower()
    shape_line = _shape_sentence(list(concerns or []) + done)
    worries = []
    seen_topics = set()
    if shape_line:
        # The plain sentence is the size point. The E1 technical line is the same point.
        seen_topics.add("size")
        seen_topics.add("shape")
    for line in concerns or []:
        text = str(line).strip()
        if not text or text in worries or _already_done(text, done_text):
            continue
        if shape_line and text == shape_line:
            continue
        topic = _concern_topic(text)
        key = _point_key(text)
        if key and key in seen_topics:
            continue
        if topic:
            if topic in seen_topics:
                continue
            seen_topics.add(topic)
            text = _plain_topic(text, topic)
            if text in worries:
                continue
        elif key:
            seen_topics.add(key)
            text = _NUM_PREFIX.sub("", text).strip()
            if text in worries:
                continue
        worries.append(text)
    # Keep the note short. The long decision log stays on the card.
    # The per-edge bleed sentence is first, so a longer log cannot crowd it out.
    interesting = []
    note = str(bleed_note or "").strip()
    if note.lower().startswith("bleed:"):
        interesting.append(note)
    for line in done:
        low = line.lower()
        if shape_line and "different shape" in low and line != shape_line:
            continue
        if (
            low.startswith("bleed:")
            or "5 mm" in low
            or "cmyk" in low
            or "brought" in low
            or "trimmed to 5" in low
            or "matched to" in low
            or "different shape" in low
            or "thin border" in low
            or "border near the cut" in low
        ):
            if line not in interesting:
                interesting.append(line)
    if shape_line and shape_line not in interesting:
        interesting.insert(0, shape_line)
    if not interesting:
        interesting = ["The 5 mm bleed was added and the file was prepared for press."]
    did = " ".join(interesting[:6])
    if not worries:
        return (
            "Your artwork has successfully been made print ready. "
            + did
        )
    listed = " ".join(f"Please have a look at this: {line}" for line in worries[:8])
    return (
        "Your artwork has successfully been made print ready, but to ensure you are 100% happy we would recommend checking the points below. "
        + did
        + " "
        + listed
    )
