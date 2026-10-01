"""Chosen press bleed. 5 mm stays the default. Custom sizes stay between 1 and 25 mm."""

DEFAULT_BLEED_MM = 5.0
MIN_BLEED_MM = 1.0
MAX_BLEED_MM = 25.0


def clamp_bleed_mm(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BLEED_MM
    if number != number or number in (float("inf"), float("-inf")):
        return DEFAULT_BLEED_MM
    rounded = round(number, 1)
    if rounded < MIN_BLEED_MM or rounded > MAX_BLEED_MM:
        return DEFAULT_BLEED_MM
    return rounded
