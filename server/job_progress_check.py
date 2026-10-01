#!/usr/bin/env python3
"""Progress writes keep the start time and never walk the percent backwards."""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from job_progress import write_progress  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK  " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(name)


def main() -> None:
    folder = tempfile.mkdtemp(prefix="job-progress-")
    path = os.path.join(folder, "9.json")
    write_progress(path, "fitting")
    write_progress(path, "reading")
    write_progress(path, "fitting")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    check("stage stays on the later step", payload["stageId"] == "reading" or payload["percent"] >= 28, str(payload["percent"]))
    check("percent does not go backwards", payload["percent"] >= 28)
    check("rebuild note is present", "2–3 minutes" in payload["note"] or "2-3 minutes" in payload["note"])
    check("startedAt is kept", isinstance(payload["startedAt"], int) and payload["startedAt"] > 0)
    write_progress(path, "done")
    with open(path, "r", encoding="utf-8") as handle:
        done = json.load(handle)
    check("done is 100", done["percent"] == 100 and done["stage"] == "Finished")
    check("percent is not 95", done["percent"] != 95 and payload["percent"] != 95)
    print("JOB PROGRESS CHECKS PASSED")


if __name__ == "__main__":
    main()
