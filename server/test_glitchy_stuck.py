"""Regression tests for Glitchy job-page 'its stuck' (queued poll, no-crop crop_box, UI lock)."""

from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


class TestGlitchyStuckFixes(unittest.TestCase):
    def test_job_poll_includes_queued_status(self):
        src = _read(os.path.join("client", "src", "hooks", "use-jobs.ts"))
        self.assertIn("function jobStatusNeedsPolling", src)
        self.assertIn('status === "queued"', src)
        self.assertIn("jobStatusNeedsPolling(status", src)

    def test_queue_position_invalidates_job_query(self):
        src = _read(os.path.join("client", "src", "pages", "job-details.tsx"))
        self.assertIn("queryClient.invalidateQueries({ queryKey: [\"job\", jobId] })", src)
        self.assertIn("MAX_COMPILING_POLLS", src)
        self.assertIn("compilingCountRef", src)

    def test_glitchy_unsticks_after_failed_audit_and_allows_processing_click(self):
        src = _read(os.path.join("client", "src", "components", "glitchy-widget.tsx"))
        self.assertIn("GLITCHY_STUCK_WATCHDOG_MS", src)
        self.assertNotIn('if (processState === "PROCESSING") return;', src)
        self.assertIn('processState === "PROCESSING" || processState === "QUEUED"', src)
        self.assertIn('setProcessState("IDLE")', src)
        self.assertIn("This is taking longer than expected", src)

    def test_no_crop_button_populates_full_page_crop_box(self):
        upload = _read(os.path.join("client", "src", "components", "file-upload.tsx"))
        helper = _read(os.path.join("client", "src", "lib", "full-page-crop.ts"))
        self.assertIn("FULL_PAGE_CROP_BOX", helper)
        self.assertIn("cropWidth: 1", helper)
        self.assertIn("setPendingCropCoords(FULL_PAGE_CROP_BOX)", upload)
        self.assertIn("is_no_crop = true", upload)
        self.assertIn("NO_CROP_FULL_PAGE", upload)

    def test_precompile_timeout_resolves_done_promise(self):
        src = _read(os.path.join("server", "routes.ts"))
        self.assertIn("if (entry.resolveDone) entry.resolveDone();", src)
        self.assertIn("isManualCropActive", src)
        self.assertIn("is_no_crop", src)

    def test_storage_full_page_not_manual_crop(self):
        src = _read(os.path.join("server", "storage.ts"))
        self.assertIn("export function isFullPageNoCrop", src)
        self.assertIn("export function isManualCropActive", src)
        self.assertIn("opts.is_no_crop === true", src)
        self.assertIn("cw === 1 && ch === 1", src)
        self.assertIn("if (isFullPageNoCrop(opts)) return false", src)

    def test_smart_bleed_no_crop_helper(self):
        src = _read(os.path.join("server", "smart_bleed.py"))
        self.assertIn("def _is_no_crop_full_page", src)
        self.assertIn('bleed_opts.get("is_no_crop")', src)
        self.assertIn("if _is_no_crop_full_page(bleed_opts):", src)
        self.assertIn("has_manual_crop = False", src)

    def test_schema_includes_queued(self):
        src = _read(os.path.join("shared", "schema.ts"))
        self.assertIn('"queued"', src)

    def test_status_badge_queued_case(self):
        src = _read(os.path.join("client", "src", "components", "status-badge.tsx"))
        self.assertIn('case "queued"', src)
        self.assertIn("badge-queued", src)

    def test_gs_memory_leashes_untouched(self):
        bleed = _read(os.path.join("server", "smart_bleed.py"))
        self.assertIn("50000000", bleed)
        self.assertIn("NumRenderingThreads", bleed)
        self.assertIn("/HWResolution [300 300]", bleed)


if __name__ == "__main__":
    unittest.main()
