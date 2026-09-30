"""Atomic progress file shared by quick mode, AI rebuild, and the press compile."""

from __future__ import annotations

import json
import os
import time

# Percents stay off 95. Proof is the last in-progress mark. Done is 100.
STAGES = {
    "checking": (8, "Checking the file"),
    "fitting": (12, "Fitting the picture"),
    "reading": (28, "Reading the words"),
    "removing": (46, "Removing the old words"),
    "enlarging": (64, "Enlarging the artwork"),
    "press": (78, "Building the press PDF"),
    "proof": (90, "Making the proof"),
    "done": (100, "Finished"),
}

REBUILD_NOTE = "AI rebuild can take 2–3 minutes."
REBUILD_STAGES = {"reading", "removing", "enlarging"}


def write_progress(path: str, stage: str, note: str = "") -> None:
    """Write one stage. Percent never moves backwards. startedAt stays put."""
    if not path:
        return
    percent, label = STAGES.get(stage, (8, stage))
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    previous = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                previous = loaded
        except Exception:
            previous = {}
    started = previous.get("startedAt") or int(time.time() * 1000)
    if stage != "done" and int(previous.get("percent") or 0) > int(percent):
        percent = int(previous["percent"])
        label = str(previous.get("stage") or label)
        stage = str(previous.get("stageId") or stage)
    kept_note = str(note or "").strip()
    if not kept_note and stage in REBUILD_STAGES:
        kept_note = REBUILD_NOTE
    if not kept_note:
        kept_note = str(previous.get("note") or "")
    payload = {
        "stage": label if stage in STAGES else str(previous.get("stage") or label),
        "stageId": stage if stage in STAGES else str(previous.get("stageId") or stage),
        "percent": int(percent),
        "startedAt": int(started),
        "updatedAt": int(time.time() * 1000),
        "note": kept_note,
    }
    if stage in STAGES:
        payload["stage"] = label
        payload["stageId"] = stage
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temporary, path)


def write_progress_from_env(stage: str, note: str = "") -> None:
    write_progress(os.environ.get("JOB_PROGRESS_FILE") or "", stage, note)
