#!/usr/bin/env python3
"""A large job is sampled. A normal page keeps the 1.35 proof zoom."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from large_job import LARGE_JOB_BYTES, is_large_job, proof_zoom


def main() -> int:
    failures = []
    small = proof_zoom(433.5, 8_000_000)
    if abs(small - 1.35) > 1e-9:
        failures.append(f"A6 proof zoom {small}")
    wide = proof_zoom(2400, 2_000_000)
    if abs(wide - 1.35) > 1e-9:
        failures.append(f"normal long page {wide}")
    huge = proof_zoom(2400, 200 * 1024 * 1024)
    if huge * 2400 > 1200.01:
        failures.append(f"200 MB zoom still full size {huge}")
    if not is_large_job(200 * 1024 * 1024) or is_large_job(LARGE_JOB_BYTES - 1):
        failures.append("size gate")

    import pymupdf as fitz

    from extra_checks import _sampled_ink

    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(page.rect, color=(0, 0, 0, 1), fill=(0, 0, 0, 1), width=0)
    facts = {"max_tac": 0.0, "k90": 0, "k_only": 0}
    _sampled_ink(page, proof_zoom(300, 200 * 1024 * 1024), facts)
    doc.close()
    if facts["k_only"] <= 0 and facts["max_tac"] <= 0:
        failures.append(f"sample saw no ink {facts}")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(b"%PDF")
        handle.flush()
        os.truncate(handle.name, LARGE_JOB_BYTES + 10)
        from large_job import file_bytes

        if not is_large_job(file_bytes(handle.name)):
            failures.append("truncated file not large")
    if failures:
        print("FAIL " + "; ".join(failures))
        return 1
    print("large job sample pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
