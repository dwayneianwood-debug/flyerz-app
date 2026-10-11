"""Sampling for a large upload.

A 200 MB picture is checked from a small render of the press plate.
The full image is not decoded again for the proof or the ink sample.
"""

from __future__ import annotations

LARGE_JOB_BYTES = 80 * 1024 * 1024
PROOF_ZOOM = 1.35
SAMPLE_LONG_PX = 1200.0


def is_large_job(file_bytes: int) -> bool:
    return int(file_bytes or 0) >= LARGE_JOB_BYTES


def proof_zoom(long_pt: float, file_bytes: int) -> float:
    """1.35 on a normal file. A large file stays near 1200 px on the long edge."""
    zoom = PROOF_ZOOM
    long_pt = max(float(long_pt or 0), 1.0)
    if not is_large_job(file_bytes):
        return zoom
    return min(zoom, SAMPLE_LONG_PX / long_pt)


def file_bytes(path: str) -> int:
    import os

    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0
