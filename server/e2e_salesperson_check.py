#!/usr/bin/env python3
"""Salesperson path: the running server's HTTP API and the built job page."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:5000")
FAILURES = []


def fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"FAIL {name} | {detail}", flush=True)


def ok(name: str, detail: str = "") -> None:
    print(f"ok  {name} | {detail}", flush=True)


def request(method: str, url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----flyerzE2E"
    chunks = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    for key, (filename, payload, mime) in files.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n".encode()
        )
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def wait_server() -> None:
    deadline = time.perf_counter() + 90
    while time.perf_counter() < deadline:
        try:
            status, _headers, _body = request("GET", f"{BASE}/api/quick-print/settings", timeout=3)
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("the server did not answer")


def poll_job(job_id: int, timeout: float) -> dict:
    deadline = time.perf_counter() + timeout
    last = {}
    while time.perf_counter() < deadline:
        status, _headers, body = request("GET", f"{BASE}/api/jobs/{job_id}", timeout=20)
        if status != 200:
            time.sleep(0.4)
            continue
        last = json.loads(body.decode())
        if last.get("status") in ("complete", "failed"):
            return last
        time.sleep(0.4)
    raise TimeoutError(f"job {job_id} stayed {last.get('status')}")


def seam_report(press_bytes: bytes) -> list:
    from press_ready_engine import edge_seam_delta_e

    folder = tempfile.mkdtemp(prefix="e2e-press-")
    path = os.path.join(folder, "press.pdf")
    with open(path, "wb") as handle:
        handle.write(press_bytes)
    page_w, page_h = 433.5 * 25.4 / 72.0, 312.0 * 25.4 / 72.0
    seam_x = (5.0 - (page_w - 148.0) / 2.0) * 72.0 / 25.4
    seam_y = (5.0 - (page_h - 105.0) / 2.0) * 72.0 / 25.4
    return edge_seam_delta_e(path, seam_x, seam_y)


def assert_seams(name: str, rows: list) -> None:
    if len(rows) != 2:
        fail(name, f"{len(rows)} pages")
        return
    worst = 0.0
    parts = []
    for row in rows:
        edges = []
        for edge in ("left", "right", "top", "bottom"):
            value = row.get(edge)
            edges.append(99.0 if value is None else float(value))
        corners = [99.0 if value is None else float(value) for value in (row.get("corners") or {}).values()]
        worst = max([worst, *edges, *corners])
        parts.append(
            "p{page} L{left:.2f} R{right:.2f} T{top:.2f} B{bottom:.2f}".format(
                page=row.get("page"),
                left=edges[0],
                right=edges[1],
                top=edges[2],
                bottom=edges[3],
            )
        )
    detail = " ".join(parts)
    if worst >= 3.0:
        fail(name, f"dE {worst:.2f} {detail}")
    else:
        ok(name, f"dE {worst:.2f} {detail}")


def quick_print(pdf: bytes, product_id: str) -> dict:
    payload, content_type = multipart(
        {"productId": product_id},
        {"files": ("canva-a6.pdf", pdf, "application/pdf")},
    )
    status, _headers, body = request(
        "POST",
        f"{BASE}/api/quick-print",
        payload,
        {"Content-Type": content_type},
        timeout=60,
    )
    if status != 202:
        fail(f"quick-{product_id}-accept", f"HTTP {status} {body[:180]!r}")
        return {}
    created = json.loads(body.decode())
    job_id = int(created["jobs"][0]["id"])
    job = poll_job(job_id, 120)
    audit = job.get("auditResults") or {}
    quick = audit.get("quickPrint") or {}
    light = str(quick.get("light") or "")
    product = str(quick.get("productId") or "")
    kept = bool(quick.get("existingBleedKept"))
    decisions = " ".join(str(item) for item in (quick.get("decisions") or []))
    problems = []
    if light == "red":
        problems.append("red")
    if product != "a6-landscape":
        problems.append(f"product {product}")
    if not kept:
        problems.append("existingBleedKept false")
    if "kept" not in decisions.lower():
        problems.append("message does not say kept")
    if "already had 5 mm" in decisions.lower():
        problems.append("claimed a full 5 mm")
    width = float(quick.get("mediaWidthMm") or 0)
    height = float(quick.get("mediaHeightMm") or 0)
    if abs(width - 158) > 1.5 or abs(height - 115) > 1.5:
        problems.append(f"media {width:.1f}x{height:.1f}")
    label = f"quick-{product_id}"
    if problems:
        fail(label, "; ".join(problems))
    else:
        ok(label, f"{light} {product} {width:.1f}x{height:.1f} kept")
    status, _headers, press = request("GET", f"{BASE}/api/jobs/{job_id}/download/press-ready", timeout=60)
    if status != 200 or not press.startswith(b"%PDF"):
        fail(f"{label}-press", f"HTTP {status} {len(press)} bytes")
        return job
    assert_seams(f"{label}-seam", seam_report(press))
    return job


def ring(path: str):
    import cv2
    import numpy as np

    image = cv2.imread(path)
    if image is None:
        return None
    band = max(2, image.shape[0] // 30)
    return np.concatenate([
        image[:band].reshape(-1),
        image[-band:].reshape(-1),
        image[:, :band].reshape(-1),
        image[:, -band:].reshape(-1),
    ]).astype(np.int16)


def manual(pdf: bytes) -> None:
    options = json.dumps({"targetWidth": 148, "targetHeight": 105, "adjustableBleedSize": 5})
    payload, content_type = multipart(
        {"bleedOptions": options, "targetWidthMm": "148", "targetHeightMm": "105"},
        {"file": ("canva-a6.pdf", pdf, "application/pdf")},
    )
    status, _headers, body = request(
        "POST",
        f"{BASE}/api/jobs/upload",
        payload,
        {"Content-Type": content_type},
        timeout=120,
    )
    if status not in (200, 201):
        fail("upload", f"HTTP {status} {body[:200]!r}")
        return
    uploaded = json.loads(body.decode())
    job_id = int(uploaded.get("jobId") or uploaded.get("id"))
    notice_status, _headers, notice_body = request(
        "GET",
        f"{BASE}/api/jobs/{job_id}/cover-crop-notice?trimW=148&trimH=105",
        timeout=60,
    )
    notice = json.loads(notice_body.decode() or b"{}")
    notice_text = json.dumps(notice)
    if notice_status != 200 or "Could not read artwork" in notice_text or notice.get("success") is False:
        fail("cover-notice", notice_text[:240])
    else:
        ok("cover-notice", f"cropped {notice.get('cropped')}")
    process_body = json.dumps({
        "bleedOptions": {"targetWidth": 148, "targetHeight": 105, "adjustableBleedSize": 5},
        "targetWidthMm": "148",
        "targetHeightMm": "105",
    }).encode()
    status, _headers, body = request(
        "POST",
        f"{BASE}/api/jobs/{job_id}/process",
        process_body,
        {"Content-Type": "application/json"},
        timeout=600,
    )
    if status != 200:
        fail("fix-everything", f"HTTP {status} {body[:240]!r}")
        return
    job = poll_job(job_id, 30)
    audit = job.get("auditResults") or {}
    pages = audit.get("bleedVariantPages") or {}
    stretch_pages = pages.get("stretch") or []
    if not audit.get("autoFixApplied"):
        fail("fix-persisted", "autoFixApplied is false")
    else:
        ok("fix-persisted", "autoFixApplied")
    if len(stretch_pages) < 2 or int(audit.get("pageCount") or 0) < 2:
        fail("style-pages", f"pageCount {audit.get('pageCount')} stretch {len(stretch_pages)}")
    else:
        ok("style-pages", f"pageCount {audit.get('pageCount')} stretch {len(stretch_pages)}")
    saved = {}
    folder = tempfile.mkdtemp(prefix="e2e-styles-")
    for method in ("stretch", "gradient_extrapolate", "frequency_separated"):
        status, _headers, image = request(
            "GET",
            f"{BASE}/api/jobs/{job_id}/bleed-variant/{method}?page=1",
            timeout=60,
        )
        path = os.path.join(folder, f"{method}.png")
        with open(path, "wb") as handle:
            handle.write(image)
        saved[method] = path
        if status != 200 or not image:
            fail(f"style-page2-{method}", f"HTTP {status} {len(image)} bytes")
        else:
            ok(f"style-page2-{method}", f"{len(image)} bytes")
    import numpy as np

    bands = {name: ring(path) for name, path in saved.items()}
    raw = {name: open(path, "rb").read() for name, path in saved.items()}
    if any(item is None for item in bands.values()):
        fail("styles-differ", "a style image could not be read")
    else:
        pairs = (
            ("stretch", "gradient_extrapolate"),
            ("stretch", "frequency_separated"),
            ("gradient_extrapolate", "frequency_separated"),
        )
        gaps = [float(np.mean(np.abs(bands[left] - bands[right]))) for left, right in pairs]
        same = [left == right for left, right in ((raw["stretch"], raw["gradient_extrapolate"]), (raw["stretch"], raw["frequency_separated"]), (raw["gradient_extrapolate"], raw["frequency_separated"]))]
        if any(same):
            fail("styles-differ", "styles returned the same file")
        else:
            ok("styles-differ", " ".join(f"{gap:.1f}" for gap in gaps))
    drive_ui(job_id)


def drive_ui(job_id: int) -> None:
    chrome = "/usr/local/bin/google-chrome"
    if not os.path.isfile(chrome):
        fail("ui", "chrome is not installed")
        return
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--remote-debugging-port=9222",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--user-data-dir=/tmp/chrome-e2e-salesperson",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            try:
                request("GET", "http://127.0.0.1:9222/json/version", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        script = os.path.join(os.path.dirname(__file__), "e2e_ui_drive.mjs")
        completed = subprocess.run(
            ["node", script, str(job_id), "expect-done", BASE, "9222"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        print(completed.stdout.strip(), flush=True)
        if completed.returncode != 0:
            fail("ui", (completed.stderr or completed.stdout)[:300])
            return
        try:
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        except Exception:
            fail("ui", completed.stdout[:300])
            return
        state = json.loads(report.get("state") or "{}")
        style = json.loads(report.get("style") or "{}")
        if not state.get("ready") or state.get("fix") or state.get("coverError") or not style.get("page2"):
            fail("ui", json.dumps({"state": state, "style": style}))
        else:
            ok("ui", "page 2 style stays after reload and Fix Everything does not ask again")
    finally:
        proc.terminate()


def main() -> None:
    from customer_files_check import _canva_source_ok, _write_canva

    wait_server()
    folder = tempfile.mkdtemp(prefix="e2e-canva-")
    src = os.path.join(folder, "canva-a6.pdf")
    _write_canva(src)
    built = _canva_source_ok(src)
    if built:
        fail("canva-source", built)
    else:
        ok("canva-source", "2 pages, equal boxes, Anton, soft mask")
    with open(src, "rb") as handle:
        pdf = handle.read()
    quick_print(pdf, "auto")
    quick_print(pdf, "a6-landscape")
    manual(pdf)
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    print("salesperson checks passed")


if __name__ == "__main__":
    main()
