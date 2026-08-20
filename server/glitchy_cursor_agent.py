"""
Glitchy → Cursor Cloud Agents bridge.

Uses Cloud Agents API v1 (POST https://api.cursor.com/v1/agents).
Requires CURSOR_API_KEY and GITHUB_REPO_URL in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from typing import Any, Optional
from unittest.mock import patch

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

MDC_CONTENT = """---
description: Immutable development rules for prepress, memory safety, and layer integrity
globs: "**/*"
alwaysApply: true
---
# IMMUTABLE DEVELOPMENT RULES
## 1. Memory Safety (8GB Replit Limit)
- NEVER remove or weaken Ghostscript memory leashes.
- `BufferSpace` and `MaxBitmap` MUST strictly remain capped at 50MB.
- `NumRenderingThreads` MUST remain set to 1.
## 2. Prepress Precision & Parameters
- Bleed: Always use a 1-pixel sampling radius for Edge Replication/Pixel-Drift calculations.
- DPI: All outputs must have 300 DPI metadata forcefully injected via PIL, PyMuPDF, and Ghostscript (-dHWResolution=300).
- Color Intent: Preserve Rich Black using -dBlackPtComp=1, KPreserve=2, and Relative Colorimetric rendering intent.
## 3. Layer Integrity
- Final PDF outputs MUST be flattened to exactly one single image layer per page.
- Purge all "Ghost Layers" or original vector elements prior to final compilation.
## 4. "No Crop" Route Handling
- The "No Crop Needed" route MUST bypass the UI while correctly populating the backend crop_box data structure (full-page dimensions) to prevent "Document Closed" errors.
## 5. Zero Regression Policy
- Every new feature or bug fix MUST include a corresponding automated unit/integration test.
- Before completing or proposing any changes, verify that all existing regression tests pass successfully.
"""

CURSOR_AGENTS_URL = "https://api.cursor.com/v1/agents"
PLACEHOLDER_REPOS = {
    "https://github.com/your-org/flyerz-app",
    "https://github.com/your-org/your-repo",
}


def ensure_cursor_rules(project_root: Optional[str] = None) -> str:
    """Write .cursor/rules/prepress.mdc under project_root (default: cwd)."""
    root = project_root or os.getcwd()
    rules_dir = os.path.join(root, ".cursor", "rules")
    os.makedirs(rules_dir, exist_ok=True)
    rule_path = os.path.join(rules_dir, "prepress.mdc")
    with open(rule_path, "w", encoding="utf-8") as f:
        f.write(MDC_CONTENT.strip() + "\n")
    return rule_path


def resolve_crop_box(crop_box: Any, page_state: Optional[dict]) -> Any:
    """Populate full-page crop_box for No Crop routes when crop_box is missing."""
    state = page_state or {}
    if not crop_box and state.get("is_no_crop"):
        return state.get("full_page_dimensions")
    return crop_box


def build_agent_prompt(
    user_feedback: str,
    crop_box: Any = None,
    gs_logs: str = "",
    page_state: Optional[dict] = None,
) -> str:
    crop_box = resolve_crop_box(crop_box, page_state)
    state = page_state or {}
    gs_tail = gs_logs[-2000:] if gs_logs else "No errors logged"
    return f"""
GLITCHY USER BUG REPORT:
User Feedback: "{user_feedback}"

CONTEXT & PREPRESS DATA:
- Active crop_box: {crop_box}
- Ghostscript Logs (Tail): {gs_tail}
- Page State: {state}

CRITICAL EXECUTION INSTRUCTIONS:
1. Strictly read and obey .cursor/rules/prepress.mdc.
2. Maintain Ghostscript memory limits: MaxBitmap=50MB, BufferSpace=50MB, NumRenderingThreads=1.
3. Ensure 1-pixel sampling radius for Edge Replication/Pixel-Drift and 300 DPI (-dHWResolution=300).
4. Preserve Rich Black (-dBlackPtComp=1, KPreserve=2, Relative Colorimetric) and flatten to single layer, purging ghost vector layers.
5. Zero Regression Requirement: Write an automated test for this fix and ensure all regression tests pass.
""".strip()


def build_v1_payload(
    prompt_text: str,
    repo_url: str,
    starting_ref: str = "main",
    auto_create_pr: bool = True,
) -> dict:
    """Cloud Agents API v1 create-agent body (repos[], not legacy source{})."""
    return {
        "prompt": {"text": prompt_text},
        "repos": [
            {
                "url": repo_url,
                "startingRef": starting_ref,
            }
        ],
        "autoCreatePR": auto_create_pr,
        "name": "Glitchy prepress fix",
        "mode": "agent",
    }


def trigger_glitchy_background_fix(
    user_feedback: str,
    crop_box: list = None,
    gs_logs: str = "",
    page_state: dict = None,
    *,
    api_key: Optional[str] = None,
    repo_url: Optional[str] = None,
    starting_ref: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Formulate a bug-fix task from Glitchy feedback and create a Cursor Cloud Agent (v1).

    Returns agent/run JSON on success. Raises ValueError for config errors;
    raises requests.HTTPError on API failure.
    """
    if requests is None:
        raise RuntimeError("The 'requests' package is required. pip install requests")

    key = (api_key if api_key is not None else os.getenv("CURSOR_API_KEY") or "").strip()
    repo = (repo_url if repo_url is not None else os.getenv("GITHUB_REPO_URL") or "").strip()
    ref = (starting_ref if starting_ref is not None else os.getenv("GITHUB_REPO_REF") or "main").strip()

    if not key:
        raise ValueError("CURSOR_API_KEY is not set")
    if not repo or repo in PLACEHOLDER_REPOS:
        raise ValueError(
            "GITHUB_REPO_URL must be a real GitHub HTTPS URL (not the placeholder)"
        )

    prompt_text = build_agent_prompt(user_feedback, crop_box, gs_logs, page_state)
    payload = build_v1_payload(prompt_text, repo, starting_ref=ref)

    if dry_run:
        return {"dry_run": True, "payload": payload}

    # Bearer and Basic are both accepted; Bearer matches existing Flyerz env usage.
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    response = requests.post(CURSOR_AGENTS_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def _cli_main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Trigger Cursor Cloud Agent from Glitchy feedback")
    parser.add_argument("--cli", action="store_true", help="Run as CLI (not unit tests)")
    parser.add_argument(
        "payload_json",
        nargs="?",
        help="JSON object with user_feedback and optional crop_box/gs_logs/page_state",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    raw = args.payload_json
    if not raw:
        raw = sys.stdin.read()
    data = json.loads(raw)
    feedback = data.get("user_feedback") or data.get("message") or ""
    if not feedback:
        print(json.dumps({"ok": False, "error": "missing user_feedback/message"}), file=sys.stderr)
        return 2

    try:
        result = trigger_glitchy_background_fix(
            user_feedback=feedback,
            crop_box=data.get("crop_box"),
            gs_logs=data.get("gs_logs") or "",
            page_state=data.get("page_state") or {},
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "result": result}))
    return 0


class TestGlitchyAgentIntegration(unittest.TestCase):
    def test_cursor_rule_file_creation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            rule_path = ensure_cursor_rules(tmp)
            self.assertTrue(os.path.exists(rule_path))
            with open(rule_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("BufferSpace", content)
            self.assertIn("MaxBitmap", content)
            self.assertIn("NumRenderingThreads", content)

    def test_v1_payload_uses_repos_not_source(self):
        payload = build_v1_payload("fix bleed", "https://github.com/acme/flyerz")
        self.assertIn("repos", payload)
        self.assertNotIn("source", payload)
        self.assertEqual(payload["repos"][0]["url"], "https://github.com/acme/flyerz")
        self.assertEqual(payload["repos"][0]["startingRef"], "main")

    @patch("requests.post")
    def test_no_crop_fallback_and_payload_construction(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "agent": {"id": "bc-agent_task_123"},
            "run": {"id": "run_1", "status": "CREATING"},
        }
        page_state = {"is_no_crop": True, "full_page_dimensions": [0, 0, 595, 842]}

        result = trigger_glitchy_background_fix(
            user_feedback="Artwork bleed offset does not look right",
            crop_box=None,
            gs_logs="GS Processing Complete",
            page_state=page_state,
            api_key="cursor_test_key",
            repo_url="https://github.com/acme/flyerz",
        )
        self.assertEqual(result["agent"]["id"], "bc-agent_task_123")

        sent_json = mock_post.call_args[1]["json"]
        sent_text = sent_json["prompt"]["text"]
        self.assertIn("repos", sent_json)
        self.assertNotIn("source", sent_json)
        self.assertIn("[0, 0, 595, 842]", sent_text)
        self.assertIn("Artwork bleed offset does not look right", sent_text)
        self.assertIn(".cursor/rules/prepress.mdc", sent_text)

    def test_resolve_crop_box_no_crop_route(self):
        self.assertEqual(
            resolve_crop_box(None, {"is_no_crop": True, "full_page_dimensions": [0, 0, 595, 842]}),
            [0, 0, 595, 842],
        )
        self.assertEqual(resolve_crop_box([10, 10, 100, 100], {"is_no_crop": True}), [10, 10, 100, 100])
        self.assertIsNone(resolve_crop_box(None, {"page": "/job/208"}))

    def test_rejects_missing_api_key(self):
        with self.assertRaises(ValueError):
            trigger_glitchy_background_fix(
                "x",
                api_key="",
                repo_url="https://github.com/acme/flyerz",
            )

    def test_rejects_placeholder_repo(self):
        with self.assertRaises(ValueError):
            trigger_glitchy_background_fix(
                "x",
                api_key="cursor_test_key",
                repo_url="https://github.com/your-org/flyerz-app",
            )


if __name__ == "__main__":
    if "--cli" in sys.argv:
        raise SystemExit(_cli_main())
    unittest.main()
