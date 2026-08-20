"""
Per-job Artwork Intelligence PDF — audit telemetry only (not the static System Intelligence Guide).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime


def _try_build_job_report_html(output_path: str, template_path: str, context: dict) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from xhtml2pdf import pisa

    root = os.path.dirname(template_path)
    env = Environment(
        loader=FileSystemLoader(root),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template(os.path.basename(template_path))

    checks = context.get("checks") or []
    html = tpl.render(
        job_id=context.get("jobId"),
        filename=context.get("filename", ""),
        generated_at=context.get("generatedAt")
        or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        job_status=context.get("jobStatus", ""),
        overall_passed=context.get("overallPassed", False),
        fixes_applied=context.get("fixesApplied", 0),
        checks=checks,
        auto_heal=context.get("autoHealEvent"),
        artwork_size=context.get("artworkSize"),
        original_dpi=context.get("originalDpi"),
        right_safety=context.get("rightSafety"),
        critical_safe_zone=context.get("criticalSafeZone"),
        ai_enhanced=context.get("aiEnhanced"),
    )
    with open(output_path, "wb") as out_f:
        status = pisa.CreatePDF(html, dest=out_f, encoding="utf-8")
    if status.err:
        raise RuntimeError(f"xhtml2pdf reported {status.err} errors")


def build_job_report_pdf(output_path: str, context: dict | None = None) -> dict:
    context = context or {}
    template_path = os.path.join(os.path.dirname(__file__), "reports", "job_report_template.html")
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Missing template: {template_path}")
    _try_build_job_report_html(output_path, template_path, context)
    return {"success": True, "path": output_path, "engine": "xhtml2pdf"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Usage: job_report_pdf.py <output_path> [context_json_path]"}))
        sys.exit(1)
    output_path = sys.argv[1]
    ctx: dict = {}
    if len(sys.argv) > 2 and sys.argv[2]:
        try:
            with open(sys.argv[2], "r", encoding="utf-8-sig") as cf:
                ctx = json.load(cf)
        except Exception as e:
            print(json.dumps({"success": False, "error": f"Bad context JSON: {e}"}))
            sys.exit(1)
    try:
        result = build_job_report_pdf(output_path, ctx)
        print(json.dumps(result))
    except Exception as e:
        import traceback

        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
