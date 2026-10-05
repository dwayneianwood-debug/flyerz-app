"""Rewrite a date, phone, time, or venue in the form already on the artwork.

Ian types the new value in his own words. The letters on the page keep the
original order, separators, month length, and capitals.
"""

from __future__ import annotations

import calendar
import re

WEEKDAYS = (
    "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    "Mon|Tues|Tue|Wed|Thurs|Thu|Fri|Sat|Sun"
)
MONTHS = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)
ORDINAL = r"(?:st|nd|rd|th)"

DATE_IN_TEXT = re.compile(
    rf"(?:(?P<week>{WEEKDAYS})\s+)?"
    rf"(?:"
    rf"(?P<d1>\d{{1,2}})(?P<ord1>{ORDINAL})?\s+(?P<mon1>{MONTHS})(?:\s+(?P<y1>\d{{2,4}}))?"
    rf"|(?P<mon2>{MONTHS})\s+(?P<d2>\d{{1,2}})(?P<ord2>{ORDINAL})?(?P<comma>,)?(?:\s+(?P<y2>\d{{2,4}}))?"
    rf"|(?P<y3>\d{{4}})-(?P<m3>\d{{2}})-(?P<d3>\d{{2}})"
    rf"|(?P<a>\d{{1,2}})(?P<sep>[/-])(?P<b>\d{{1,2}})(?:(?P<sep2>[/-])(?P<y4>\d{{2,4}}))?"
    rf")",
    re.I,
)
PHONE_IN_TEXT = re.compile(
    r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]*)?\d(?:[\d\s().-]{5,})\d"
)
TIME_IN_TEXT = re.compile(
    r"\b(?:\d{1,2}[:.h]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b",
    re.I,
)
VENUE_IN_TEXT = re.compile(
    r"\b(?:\d{1,4}\s+)?(?:[A-Za-z][\w''.-]*\s+){0,5}"
    r"(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|Drive|Dr\.|Hall|Church|Centre|Center|Venue|Stadium|Park|School)\b",
    re.I,
)

_MONTH_NUMBER = {}
for _index in range(1, 13):
    _MONTH_NUMBER[calendar.month_name[_index].lower()] = _index
    _MONTH_NUMBER[calendar.month_abbr[_index].lower()] = _index
_MONTH_NUMBER["sept"] = 9


# An icon beside a phone number is often read as "()" or "®)". It is not part of the number.
_STRAY_PHONE = re.compile(r"(?:[®©™]\s*\)|\(\s*\))")


def _clean_phone(text: str) -> str:
    cleaned = _STRAY_PHONE.sub(" ", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def fragment(text: str, kind: str) -> str:
    pattern = {"phone": PHONE_IN_TEXT, "date": DATE_IN_TEXT, "time": TIME_IN_TEXT, "venue": VENUE_IN_TEXT}.get(kind)
    if pattern is None:
        return ""
    match = pattern.search(text or "")
    if not match:
        return ""
    found = match.group(0)
    if kind == "phone":
        return _clean_phone(found)
    return found


def restyle(kind: str, original: str, requested: str) -> str:
    if kind == "date":
        return _restyle_date(original, requested)
    if kind == "phone":
        return _restyle_phone(original, requested)
    if kind == "time":
        return _restyle_time(original, requested)
    if kind == "venue":
        return _restyle_venue(original, requested)
    return requested.strip()


def swap_text(text: str, kind: str, requested: str) -> tuple[str, str, str] | None:
    """Return the found words, the styled replacement, and the full line."""
    pattern = {"phone": PHONE_IN_TEXT, "date": DATE_IN_TEXT, "time": TIME_IN_TEXT, "venue": VENUE_IN_TEXT}.get(kind)
    match = pattern.search(text or "") if pattern is not None else None
    if not match:
        return None
    raw = match.group(0)
    found = _clean_phone(raw) if kind == "phone" else raw
    styled = restyle(kind, found, requested)
    if not styled:
        return None
    if kind == "phone":
        styled = _clean_phone(styled)
        leftover = text.replace(raw, " ", 1)
        if not re.search(r"[A-Za-z0-9]", leftover):
            return found, styled, styled
        return found, styled, _clean_phone(text.replace(raw, styled, 1))
    return found, styled, text.replace(raw, styled, 1)


def _like(sample: str, word: str) -> str:
    if sample.isupper():
        return word.upper()
    if sample.islower():
        return word.lower()
    return word[:1].upper() + word[1:].lower() if word else word


def _year_number(token: str) -> int:
    value = int(token)
    if value < 100:
        return 2000 + value
    return value


def _parse_date(text: str) -> tuple[int, int, int | None]:
    match = DATE_IN_TEXT.search(text or "")
    if not match:
        raise ValueError("date")
    if match.group("d1"):
        year = _year_number(match.group("y1")) if match.group("y1") else None
        return int(match.group("d1")), _MONTH_NUMBER[match.group("mon1").lower()], year
    if match.group("d2"):
        year = _year_number(match.group("y2")) if match.group("y2") else None
        return int(match.group("d2")), _MONTH_NUMBER[match.group("mon2").lower()], year
    if match.group("d3"):
        return int(match.group("d3")), int(match.group("m3")), int(match.group("y3"))
    first = int(match.group("a"))
    second = int(match.group("b"))
    year = _year_number(match.group("y4")) if match.group("y4") else None
    # A number above 12 is the day. Otherwise keep day/month, which is how the shop writes 12/10.
    if first > 12 and second <= 12:
        return first, second, year
    if second > 12 and first <= 12:
        return second, first, year
    return first, second, year


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _pad(value: int, token: str) -> str:
    if token.startswith("0") and len(token) >= 2:
        return f"{value:02d}"
    return str(value)


def _month_word(token: str, month: int) -> str:
    full = {calendar.month_name[i].lower() for i in range(1, 13)}
    if token.lower() in full:
        name = calendar.month_name[month]
    elif token.lower() == "sept" and month == 9:
        name = "Sept"
    else:
        name = calendar.month_abbr[month]
    return _like(token, name)


def _restyle_date(original: str, requested: str) -> str:
    day, month, year = _parse_date(requested)
    match = DATE_IN_TEXT.search(original)
    if not match:
        raise ValueError("date")
    if year is None:
        for token in (match.group("y1"), match.group("y2"), match.group("y3"), match.group("y4")):
            if token:
                year = _year_number(token)
                break
    if match.group("d1"):
        day_text = _pad(day, match.group("d1"))
        if match.group("ord1"):
            day_text += _like(match.group("ord1"), _ordinal(day))
        body = f"{day_text} {_month_word(match.group('mon1'), month)}"
        if match.group("y1") and year:
            body += " " + _pad_year(year, match.group("y1"))
        if match.group("week"):
            return f"{_weekday(year or 2026, month, day, match.group('week'))} {body}"
        return body
    if match.group("d2"):
        day_text = _pad(day, match.group("d2"))
        if match.group("ord2"):
            day_text += _like(match.group("ord2"), _ordinal(day))
        body = f"{_month_word(match.group('mon2'), month)} {day_text}"
        if match.group("comma"):
            body += ","
        if match.group("y2") and year:
            body += " " + _pad_year(year, match.group("y2"))
        if match.group("week"):
            return f"{_weekday(year or 2026, month, day, match.group('week'))} {body}"
        return body
    if match.group("d3") and year:
        return f"{year:04d}-{month:02d}-{day:02d}"
    sep = match.group("sep") or "/"
    left_is_month = int(match.group("a")) <= 12 and int(match.group("b")) > 12
    if left_is_month:
        left, right = _pad(month, match.group("a")), _pad(day, match.group("b"))
    else:
        left, right = _pad(day, match.group("a")), _pad(month, match.group("b"))
    body = f"{left}{sep}{right}"
    if match.group("y4") and year:
        body += (match.group("sep2") or sep) + _pad_year(year, match.group("y4"))
    return body


def _pad_year(year: int, token: str) -> str:
    if len(token) <= 2:
        return f"{year % 100:02d}"
    return f"{year:04d}"


def _weekday(year: int, month: int, day: int, sample: str) -> str:
    name = calendar.day_name[calendar.weekday(year, month, day)]
    abbr = calendar.day_abbr[calendar.weekday(year, month, day)]
    full = {calendar.day_name[i].lower() for i in range(7)}
    word = name if sample.lower() in full else abbr
    return _like(sample, word)


def _phone_groups(text: str) -> tuple[int, ...]:
    return tuple(len(part) for part in re.findall(r"\d+", text or ""))


def _house_phone(original: str) -> bool:
    """A grouping the artwork is clearly using on purpose.

    Parentheses, hyphen groups, and the usual 3-3-4 local number stay.
    An odd split such as 3-4-3 does not: the digits keep the grouping Ian typed.
    """
    groups = _phone_groups(original)
    if re.search(r"\(\s*\d", original or ""):
        return True
    letters = re.sub(r"[A-Za-z]", "", original or "")
    if "-" in original and " " not in letters and len(groups) >= 2:
        return True
    return groups in {(3, 3, 4), (4, 3, 3), (3, 4, 4), (2, 3, 4)}


def _restyle_phone(original: str, requested: str) -> str:
    original = _clean_phone(original)
    old_digits = re.sub(r"\D", "", original)
    new_digits = re.sub(r"\D", "", requested)
    if not new_digits:
        raise ValueError("phone")
    typed = requested.strip()
    if not _house_phone(original):
        return typed
    if len(old_digits) == len(new_digits):
        digits = iter(new_digits)
        return "".join(next(digits) if char.isdigit() else char for char in original)
    groups = list(_phone_groups(original))
    sep = "-" if "-" in original and " " not in original else (" " if " " in original else "")
    if sep and groups and sum(groups) == len(new_digits):
        parts = []
        cursor = 0
        for size in groups:
            parts.append(new_digits[cursor: cursor + size])
            cursor += size
        return sep.join(parts)
    return typed


def _parse_time(text: str) -> tuple[int, int]:
    match = re.search(r"(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm)?", text or "", re.I)
    if not match:
        raise ValueError("time")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    marker = (match.group(3) or "").lower()
    if marker == "pm" and hour < 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    elif not marker and hour > 23:
        raise ValueError("time")
    return hour, minute


def _restyle_time(original: str, requested: str) -> str:
    hour, minute = _parse_time(requested)
    marker = re.search(r"(am|pm)", original, re.I)
    clock = re.search(r"(\d{1,2})([:.h])(\d{2})", original, re.I)
    if marker:
        hour12 = hour % 12 or 12
        letters = "pm" if hour >= 12 else "am"
        token = marker.group(1)
        if token.isupper():
            letters = letters.upper()
        elif token[:1].isupper():
            letters = letters.capitalize()
        space = " " if re.search(r"\d\s+(?:am|pm)", original, re.I) else ""
        if clock:
            sep = clock.group(2)
            hour_text = f"{hour12:02d}" if clock.group(1).startswith("0") else str(hour12)
            return f"{hour_text}{sep}{minute:02d}{space}{letters}"
        if minute:
            return f"{hour12}:{minute:02d}{space}{letters}"
        return f"{hour12}{space}{letters}"
    if clock:
        sep = clock.group(2)
        hour_text = f"{hour:02d}" if len(clock.group(1)) == 2 else str(hour)
        if clock.group(1).startswith("0"):
            hour_text = f"{hour:02d}"
        return f"{hour_text}{sep}{minute:02d}"
    raise ValueError("time")


def _restyle_venue(original: str, requested: str) -> str:
    words = requested.strip()
    if not words:
        raise ValueError("venue")
    letters = [char for char in original if char.isalpha()]
    if letters and all(char.isupper() for char in letters):
        return words.upper()
    if letters and all(char.islower() for char in letters):
        return words.lower()
    parts = [part for part in re.split(r"\s+", original) if part[:1].isalpha()]
    if parts and all(part[:1].isupper() for part in parts):
        return " ".join(part[:1].upper() + part[1:] for part in words.split())
    return words
