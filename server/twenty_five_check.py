#!/usr/bin/env python3
"""The 25-point list reports only checks that ran."""

from __future__ import annotations

import os
import tempfile

from client_file_audit_check import _press_problems
from twenty_five import POINTS, assess

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> None:
    folder = tempfile.mkdtemp(prefix="twenty-")
    path = os.path.join(folder, "bad.pdf")
    _press_problems(path)
    report = assess(path, 148, 210)
    rows = report["checks"]
    check("count", len(rows) == 25 and len(POINTS) == 25, str(len(rows)))
    check("order", [row["num"] for row in rows] == [num for num, _name in POINTS], str([row["num"] for row in rows]))
    by_num = {row["num"]: row for row in rows}
    fonts = by_num["4"]
    check("font-failed", fonts["status"] == "failed" and fonts["pass"] is False and "Helvetica" in fonts["detail"], fonts["detail"])
    hair = by_num["2h"]
    check("hair-honest", hair["status"] == "warning" and "not thickened" in hair["detail"], hair["detail"])
    black = by_num["2b"]
    check("black-warning", black["status"] == "warning" and "registration" in black["detail"], black["detail"])
    spots = by_num["16"]
    check("spots-named", "Pantone-123" in spots["detail"] and spots["pass"] is False, spots["detail"])
    resolution = by_num["3"]
    check("dpi-ran", resolution["status"] in ("passed", "warning", "failed"), resolution["detail"])
    for row in rows:
        if row["pass"]:
            check(f"claimed-{row['num']}", row["status"] in ("passed", "auto") and "did not run" not in row["detail"].lower(), row["detail"])
        if row["status"] == "skipped":
            check(f"skip-{row['num']}", row["pass"] is False, row["detail"])
    missing = assess(os.path.join(folder, "gone.pdf"))
    check("missing-file", len(missing["checks"]) == 25 and all(row["status"] == "skipped" and row["pass"] is False for row in missing["checks"]))
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("twenty five checks passed")


if __name__ == "__main__":
    main()
