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
    return False


_PPI_LINE = re.compile(r"\babout\s+\d+\s*(?:ppi|dpi)\b", re.I)


def _concern_topic(line: str) -> str:
    """One customer point per subject.

    Resolution, the lowest-picture line, and a second "about N ppi" line are
    the same point. The two "Ordered product vs file size" wordings are one
    point. AI Resolution Enhancement stays its own point.
    """
    plain = _NUM_PREFIX.sub("", str(line or "").strip())
    name = plain.split(":", 1)[0].strip().lower()
    body = plain.lower()
    if "ai resolution" in name:
        return "ai"
    if "raster text" in body:
        return ""
    if name.startswith("resolution") or "dpi validation" in name:
        return "resolution"
    if "lowest picture" in body or _PPI_LINE.search(body):
        return "resolution"
    if "ordered product vs file size" in name or "aspect differs" in body:
        return "size"
    return ""


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


def customer_message(actions: list, concerns: list, bleed_note: str = "") -> str:
    """What was done, then the amber warnings and any red items Ian accepted."""
    done = [str(line).strip() for line in actions or [] if str(line).strip()]
    done_text = " ".join(done).lower()
    worries = []
    seen_topics = set()
    for line in concerns or []:
        text = str(line).strip()
        if not text or text in worries or _already_done(text, done_text):
            continue
        topic = _concern_topic(text)
        if topic:
            if topic in seen_topics:
                continue
            seen_topics.add(topic)
            text = _plain_topic(text, topic)
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
        if low.startswith("bleed:") or "5 mm" in low or "cmyk" in low or "brought" in low or "trimmed to 5" in low or "matched to" in low:
            if line not in interesting:
                interesting.append(line)
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
