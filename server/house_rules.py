#!/usr/bin/env python3
"""House rules the black cat remembers.

Locked rows are rules already set in this app. They are inserted once and
are never rewritten or deleted. Taught rules can be listed, edited, and deleted.
Finished jobs live in another table and are not part of this store.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3

# Verbatim rules already in the repo. The text is the source line, not a summary.
LOCKED = [
    ("bleed-5mm", "Bleed is always 5 mm.", "seed"),
    ("gs-leash", "NEVER remove or weaken Ghostscript memory leashes.", ".cursor/rules/prepress.mdc"),
    ("gs-buffer", "`BufferSpace` and `MaxBitmap` MUST strictly remain capped at 50MB (`50000000`).", ".cursor/rules/prepress.mdc"),
    ("gs-threads", "`NumRenderingThreads` MUST remain set to 1.", ".cursor/rules/prepress.mdc"),
    (
        "pixel-drift",
        "Bleed: Always use a 1-pixel sampling radius for Edge Replication/Pixel-Drift calculations.",
        ".cursor/rules/prepress.mdc",
    ),
    (
        "dpi-300",
        "DPI: All outputs must have 300 DPI metadata forcefully injected via PIL, PyMuPDF, and Ghostscript (`-dHWResolution=300`).",
        ".cursor/rules/prepress.mdc",
    ),
    (
        "rich-intent",
        "Color Intent: Preserve Rich Black using `-dBlackPtComp=1`, `KPreserve=2`, and Relative Colorimetric rendering intent.",
        ".cursor/rules/prepress.mdc",
    ),
    ("one-layer", "Final PDF outputs MUST be flattened to exactly one single image layer per page.", ".cursor/rules/prepress.mdc"),
    ("ghost-layers", 'Purge all "Ghost Layers" or original vector elements prior to final compilation.', ".cursor/rules/prepress.mdc"),
    (
        "no-crop",
        'The "No Crop Needed" route MUST bypass the UI while correctly populating the backend `crop_box` data structure (full-page dimensions) to prevent "Document Closed" errors.',
        ".cursor/rules/prepress.mdc",
    ),
    (
        "k-only-18",
        "Text below 18pt (inside BT/ET blocks): Always converted to K-only with overprint ON.",
        "server/checks_guide.py",
    ),
    (
        "small-56",
        "Small fill elements (<56pt / ~20mm): Converted to K-only.",
        "server/checks_guide.py",
    ),
    (
        "rich-70",
        "Deep Black (K > 70%): Converted to Press-Safe Rich Black (C40/M30/Y30/K100 = 200% TIC) with Overprint OFF (Knockout).",
        "server/checks_guide.py",
    ),
    (
        "grey-zones",
        "Dark Grey (K 30–69%): Matched K value with Overprint ON. Light Grey (K 5–29%): Matched K value with Overprint OFF (Knockout).",
        "server/checks_guide.py",
    ),
    (
        "dpi-bands",
        "When DPI is between 75 and 299, the engine automatically applies intelligent upscaling to boost resolution to 300+ DPI. Below 75 DPI, the source is too low for enhancement.",
        "server/checks_guide.py",
    ),
    (
        "card-90x50",
        '{ "id": "card-90x50", "label": "Business card 90 × 50", "widthMm": 90, "heightMm": 50 }',
        "shared/quick-print-products.json",
    ),
]


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_repo_root(), rel), encoding="utf-8") as handle:
        return handle.read()


def _assigned_source(rel: str, name: str) -> str:
    text = _read(rel)
    tree = ast.parse(text)
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.get_source_segment(text, node) or ""
    return ""


def _function_source(rel: str, name: str) -> str:
    text = _read(rel)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    return ""


def _ocr_rules() -> str:
    lines = _read("server/text_clearup.py").splitlines(keepends=True)
    chunk: list[str] = []
    grab = False
    for line in lines:
        if "CRITICAL RULES:" in line:
            grab = True
        if grab:
            chunk.append(line)
            if "decorative shapes" in line:
                break
    return "".join(chunk)


def discovered_rules() -> list[tuple[str, str, str]]:
    """Exact copies of rule documents already in the repo. The text is not edited."""
    docs = [
        ("doc-cursorrules", _read(".cursorrules"), ".cursorrules"),
        ("doc-prepress-mdc", _read(".cursor/rules/prepress.mdc"), ".cursor/rules/prepress.mdc"),
        ("doc-agents-md", _read("AGENTS.md"), "AGENTS.md"),
        ("doc-agent-rules", _read("AGENT_RULES.md"), "AGENT_RULES.md"),
        ("doc-replit", _read("replit.md"), "replit.md"),
        ("doc-products", _read("shared/quick-print-products.json"), "shared/quick-print-products.json"),
        ("doc-glitchy-mdc", _assigned_source("server/glitchy_cursor_agent.py", "MDC_CONTENT"), "server/glitchy_cursor_agent.py"),
        ("doc-glitchy-prompt", _function_source("server/glitchy_cursor_agent.py", "build_agent_prompt"), "server/glitchy_cursor_agent.py"),
        ("doc-checks", _assigned_source("server/checks_guide.py", "CHECKS"), "server/checks_guide.py"),
        ("doc-dashboard-copy", _assigned_source("server/checks_guide.py", "DASHBOARD_RULES_COPY"), "server/checks_guide.py"),
        ("doc-safe-zone", _assigned_source("server/checks_guide.py", "ENGINE_SAFE_ZONE_SPEC"), "server/checks_guide.py"),
        ("doc-ocr-critical", _ocr_rules(), "server/text_clearup.py"),
    ]
    rows = []
    for key, text, source in docs:
        if not text:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        rows.append((f"{key}-{digest}", text, source))
    return rows


def _ledger_paths() -> list[str]:
    paths = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "house_rules_ledger.jsonl")]
    override = (os.environ.get("FLYERZ_RULES_LEDGER") or "").strip()
    if override:
        paths.append(os.path.abspath(override))
    else:
        paths.append(os.path.join(_repo_root(), "data", "house-rules-locked.jsonl"))
    return paths


def _read_ledger(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _append_ledger(path: str, known: set[str], row: dict) -> None:
    marker = row["rule_key"] + "\t" + row["text"]
    if marker in known:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    known.add(marker)


def _insert_locked(db: sqlite3.Connection, key: str, text: str, source: str) -> None:
    """Insert once. An existing key or the same text is left exactly as stored."""
    db.execute(
        """
        INSERT OR IGNORE INTO house_rules (rule_key, text, scope, client, locked, source)
        SELECT ?, ?, 'global', '', 1, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM house_rules WHERE rule_key = ? OR text = ?
        )
        """,
        (key, text, source, key, text),
    )


def db_path() -> str:
    raw = (os.environ.get("FLYERZ_DB_PATH") or "").strip()
    if raw:
        return os.path.abspath(raw)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "flyerz.sqlite")


def connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS house_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_key TEXT UNIQUE,
            text TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            client TEXT NOT NULL DEFAULT '',
            locked INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS house_rules_no_delete_locked
        BEFORE DELETE ON house_rules
        WHEN OLD.locked = 1
        BEGIN
            SELECT RAISE(ABORT, 'locked house rule');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS house_rules_no_update_locked
        BEFORE UPDATE OF text, rule_key, locked, scope, client, source ON house_rules
        WHEN OLD.locked = 1
        BEGIN
            SELECT RAISE(ABORT, 'locked house rule');
        END
        """
    )
    ledgers = _ledger_paths()
    for path in ledgers:
        for row in _read_ledger(path):
            _insert_locked(db, str(row["rule_key"]), str(row["text"]), str(row.get("source") or ""))
    for key, text, source in list(LOCKED) + discovered_rules():
        _insert_locked(db, key, text, source)
    durable = ledgers[-1]
    already = {
        str(row["rule_key"]) + "\t" + str(row["text"])
        for row in _read_ledger(durable)
    }
    for row in db.execute(
        "SELECT rule_key, text, source FROM house_rules WHERE locked = 1 ORDER BY id"
    ):
        _append_ledger(durable, already, {
            "rule_key": row["rule_key"],
            "text": row["text"],
            "source": row["source"],
        })
    db.commit()
    return db


def list_rules(client: str = "") -> list[dict]:
    db = connect()
    try:
        rows = db.execute(
            "SELECT id, rule_key, text, scope, client, locked, source FROM house_rules ORDER BY id"
        ).fetchall()
    finally:
        db.close()
    wanted = (client or "").strip().lower()
    out = []
    for row in rows:
        if wanted and row["scope"] == "client" and row["client"].lower() != wanted:
            continue
        out.append({
            "id": int(row["id"]),
            "rule_key": row["rule_key"],
            "text": row["text"],
            "scope": row["scope"],
            "client": row["client"],
            "locked": bool(row["locked"]),
            "source": row["source"],
        })
    return out


def add_rule(text: str, client: str = "") -> dict:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return {"ok": False, "detail": "Tell me the rule in a sentence."}
    scope = "client" if client else "global"
    db = connect()
    try:
        existing = db.execute(
            "SELECT id, locked FROM house_rules WHERE lower(text) = lower(?)",
            (cleaned,),
        ).fetchone()
        if existing:
            return {"ok": True, "detail": f"That rule is already stored (rule {int(existing['id'])}).", "id": int(existing["id"])}
        cur = db.execute(
            "INSERT INTO house_rules (rule_key, text, scope, client, locked, source) VALUES (NULL, ?, ?, ?, 0, 'chat')",
            (cleaned, scope, client.strip()),
        )
        db.commit()
        rule_id = int(cur.lastrowid)
    finally:
        db.close()
    where = f" for {client.strip()}" if client else ""
    return {"ok": True, "detail": f"Remembered rule {rule_id}{where}: {cleaned}", "id": rule_id}


def update_rule(rule_id: int, text: str) -> dict:
    cleaned = " ".join((text or "").split())
    db = connect()
    try:
        row = db.execute("SELECT locked FROM house_rules WHERE id = ?", (int(rule_id),)).fetchone()
        if not row:
            return {"ok": False, "detail": f"There is no rule {rule_id}."}
        if row["locked"]:
            return {"ok": False, "detail": f"Rule {rule_id} is part of the press setup. I can't change it."}
        db.execute("UPDATE house_rules SET text = ? WHERE id = ?", (cleaned, int(rule_id)))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "detail": f"Rule {rule_id} now says: {cleaned}"}


def delete_rule(rule_id: int) -> dict:
    db = connect()
    try:
        row = db.execute("SELECT locked, text FROM house_rules WHERE id = ?", (int(rule_id),)).fetchone()
        if not row:
            return {"ok": False, "detail": f"There is no rule {rule_id}."}
        if row["locked"]:
            return {"ok": False, "detail": f"Rule {rule_id} is part of the press setup. I can't delete it."}
        db.execute("DELETE FROM house_rules WHERE id = ?", (int(rule_id),))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "detail": f"Forgot rule {rule_id}."}


def _format(rules: list[dict]) -> str:
    if not rules:
        return "I have no house rules stored."
    lines = ["House rules:"]
    for row in rules:
        mark = " (kept)" if row["locked"] else ""
        who = f" [{row['client']}]" if row["client"] else ""
        lines.append(f"{row['id']}. {row['text']}{who}{mark}")
    return "\n".join(lines)


_TEACH = re.compile(
    r"^(?:remember|learn|new rule|rule)\b[:\s]*(?:that\s+)?(.+)$",
    re.I,
)
_CLIENT = re.compile(r"^client\s+([A-Za-z0-9 .'-]+?)\s+(?:wants|uses|is|card\b)\s+(.+)$", re.I)
_DELETE = re.compile(r"^(?:forget|delete|remove)\s+(?:rule\s+)?(\d+)\b", re.I)
_EDIT = re.compile(r"^(?:change|edit|update)\s+rule\s+(\d+)\s+(?:to|says)\s+(.+)$", re.I)
_LIST = re.compile(r"^(?:list|show|what are)(?: the)? (?:house )?rules\b", re.I)


def handle_message(message: str) -> dict | None:
    """Return a reply when the message is about house rules. Otherwise None."""
    text = " ".join((message or "").split())
    if not text:
        return None
    if _LIST.match(text):
        return {"ok": True, "reply": _format(list_rules()), "actions": []}
    deleted = _DELETE.match(text)
    if deleted:
        done = delete_rule(int(deleted.group(1)))
        return {"ok": done["ok"], "reply": done["detail"], "actions": []}
    edited = _EDIT.match(text)
    if edited:
        done = update_rule(int(edited.group(1)), edited.group(2))
        return {"ok": done["ok"], "reply": done["detail"], "actions": []}
    taught = _TEACH.match(text)
    if taught:
        body = taught.group(1).strip()
        done = add_rule(body, _client_of(body))
        return {"ok": done["ok"], "reply": done["detail"], "actions": []}
    return None


def _client_of(text: str) -> str:
    match = re.search(r"\bclient\s+([A-Za-z0-9][A-Za-z0-9 .'-]{0,40}?)\s+(?:wants|uses|card|is)\b", text, re.I)
    if match:
        return match.group(1).strip()
    named = re.match(r"^([A-Za-z][A-Za-z0-9 .'-]{0,40}?)\s+card\s+is\b", text, re.I)
    if named:
        return named.group(1).strip()
    return ""


def bleed_mm(client: str = "") -> tuple[float, str]:
    """The bleed depth to use, and the rule sentence that set it."""
    chosen = None
    for row in list_rules():
        match = re.search(r"bleed is always\s+([\d.]+)\s*mm", row["text"], re.I)
        if not match:
            continue
        if row["client"] and client and row["client"].lower() != client.lower():
            continue
        if row["client"] and not client:
            continue
        chosen = (float(match.group(1)), row["text"], row["locked"])
        if not row["locked"]:
            break
    if not chosen:
        return 5.0, "Bleed is always 5 mm."
    return chosen[0], chosen[1]


def rich_black_for(client: str) -> tuple[tuple[float, float, float, float] | None, str]:
    if not client:
        return None, ""
    for row in list_rules():
        if row["client"] and row["client"].lower() != client.lower() and client.lower() not in row["text"].lower():
            continue
        if client.lower() not in row["text"].lower() and (not row["client"] or row["client"].lower() != client.lower()):
            continue
        match = re.search(
            r"rich black\s+(\d+(?:\.\d+)?)\s*[/,]\s*(\d+(?:\.\d+)?)\s*[/,]\s*(\d+(?:\.\d+)?)\s*[/,]\s*(\d+(?:\.\d+)?)",
            row["text"],
            re.I,
        )
        if not match:
            continue
        parts = [float(item) for item in match.groups()]
        if max(parts) > 1.5:
            parts = [item / 100.0 for item in parts]
        return tuple(parts), row["text"]  # type: ignore[return-value]
    return None, ""


def menu_rule() -> str:
    for row in list_rules():
        if "menu" in row["text"].lower() and "designer" in row["text"].lower():
            return row["text"]
    return ""


def size_for(client: str) -> tuple[float, float, str] | None:
    if not client:
        return None
    for row in list_rules():
        blob = row["text"]
        if client.lower() not in blob.lower():
            continue
        match = re.search(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", blob, re.I)
        if match and ("card" in blob.lower() or row["client"]):
            return float(match.group(1)), float(match.group(2)), blob
    card = next((row for row in list_rules() if row.get("source") == "shared/quick-print-products.json" or "card-90x50" in row["text"]), None)
    if card and client.lower() in ("medella", "card-90x50", "business card 90 × 50"):
        return 90.0, 50.0, card["text"]
    return None
