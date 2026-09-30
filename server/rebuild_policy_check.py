#!/usr/bin/env python3
"""Opt-in retype refuses crowded, decorative, overlapping, or damaged results."""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rebuild_policy import (  # noqa: E402
    design_damage_reason,
    looks_decorative,
    retype_refusal,
    text_line_count,
)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def _block(text: str, x: float, y: float, w: float, h: float) -> dict:
    return {"text": text, "bbox": [x, y, w, h]}


def _sans_word(path_shape=(80, 220)) -> np.ndarray:
    img = np.full((path_shape[0], path_shape[1], 3), 255, np.uint8)
    cv2.putText(img, "SALE", (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    return img


def main() -> None:
    one = [_block("SALE", 0.1, 0.2, 0.6, 0.3)]
    check("one line is allowed", retype_refusal(one, _sans_word()) == [])
    check("sans word is not decorative", not looks_decorative(_sans_word(), one))
    many = [_block(f"Line {i}", 0.1, 0.02 * i, 0.5, 0.015) for i in range(20)]
    refused = retype_refusal(many, None)
    check("twenty lines are refused", any("15" in line for line in refused), " | ".join(refused))
    check("line count", text_line_count(many) == 20)
    overlap = [
        _block("ONE", 0.1, 0.1, 0.5, 0.2),
        _block("TWO", 0.2, 0.15, 0.5, 0.2),
    ]
    check("overlap is refused", any("overlap" in line.lower() for line in retype_refusal(overlap, None)))
    original = _sans_word((120, 240))
    clean = original.copy()
    check("matching picture is not damage", design_damage_reason(original, clean, one) == "")
    blotched = original.copy()
    blotched[:] = (30, 90, 200)
    reason = design_damage_reason(original, blotched, one)
    check("blotched picture is discarded", "discarded" in reason.lower(), reason)
    lost = original.copy()
    lost[:, :] = 255
    lost_reason = design_damage_reason(original, lost, [_block("SALE", 0.05, 0.15, 0.9, 0.7)])
    check("missing letters are discarded", "lost" in lost_reason.lower() or "discarded" in lost_reason.lower(), lost_reason)
    print("REBUILD POLICY CHECKS PASSED")


if __name__ == "__main__":
    main()
