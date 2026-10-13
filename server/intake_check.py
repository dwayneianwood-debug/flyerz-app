"""Fast read of Ian's checklist before any fixing starts.

The 25-point list and E1–E9 stay as they are. This only labels each row
green, amber, or red so the screen can stop on red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


OFFICE = {".doc", ".docx", ".ppt", ".pptx"}


def severity_for(status: str) -> str:
    if status == "failed":
        return "red"
    if status == "warning":
        return "amber"
    return "green"


def _item(row: dict) -> dict:
    status = str((row or {}).get("status") or "")
    num = str(row.get("num") or "")
    name = str(row.get("name") or "Check")
    detail = str(row.get("detail") or row.get("message") or "")
    item_id = str(row.get("id") or num or name).replace(" ", "-")
    return {
        "id": item_id,
        "num": num,
        "name": name,
        "detail": detail,
        "status": status,
        "severity": severity_for(status),
    }


def _office_item() -> dict:
    return {
        "id": "office",
        "num": "",
        "name": "File type",
        "detail": "Word and PowerPoint files cannot be printed as they are. Please send a PDF, JPG, or PNG.",
        "status": "failed",
        "severity": "red",
    }


def assess_intake(path: str, trim_w: float, trim_h: float) -> dict:
    ext = os.path.splitext(path or "")[1].lower()
    if ext in OFFICE:
        items = [_office_item()]
    else:
        items = []
        try:
            from twenty_five import assess

            report = assess(path, trim_w, trim_h)
            for row in (report or {}).get("checks") or []:
                if isinstance(row, dict):
                    items.append(_item(row))
        except Exception as exc:
            items.append({
                "id": "read",
                "num": "",
                "name": "File",
                "detail": f"The file could not be checked ({str(exc)[:140]}).",
                "status": "failed",
                "severity": "red",
            })
        try:
            from extra_checks import assess_extras

            extra = assess_extras(
                path,
                "",
                {"widthMm": trim_w, "heightMm": trim_h, "explicitSize": True},
                apply=False,
            )
            for row in (extra or {}).get("checks") or []:
                if isinstance(row, dict):
                    items.append(_item(row))
        except Exception:
            pass
    red = sum(1 for item in items if item["severity"] == "red")
    amber = sum(1 for item in items if item["severity"] == "amber")
    green = sum(1 for item in items if item["severity"] == "green")
    return {"items": items, "red": red, "amber": amber, "green": green}


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick check before print-ready")
    parser.add_argument("--input", required=True)
    parser.add_argument("--trim-w", type=float, default=148)
    parser.add_argument("--trim-h", type=float, default=210)
    args = parser.parse_args()
    report = assess_intake(args.input, args.trim_w, args.trim_h)
    sys.stdout.write(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
