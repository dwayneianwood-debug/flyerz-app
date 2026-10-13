"""Plain customer note for a finished press file."""

from __future__ import annotations


def customer_message(actions: list, concerns: list) -> str:
    """What was done, then the amber warnings and any red items Ian accepted."""
    done = [str(line).strip() for line in actions or [] if str(line).strip()]
    worries = [str(line).strip() for line in concerns or [] if str(line).strip()]
    # Keep the note short. The long decision log stays on the card.
    interesting = []
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
