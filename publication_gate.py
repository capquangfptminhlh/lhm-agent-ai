"""Publication safety primitives for the local LHM Agent OS.

Strict rules:
* AI source-linked publishing requires a machine-recorded passing QA review.
* QA approval is bound to the full SHA-256 of the source text.
* The tool payload must match the source exactly, preventing approval laundering.
* External action approval binds content + channel + action by digest.
* Failed PII checks redact before persisting task results.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from fastapi import HTTPException

REVIEW_ROLES = frozenset({"quality", "compliance"})
PRIVATE_OUTPUT_ROLES = frozenset({
    "research", "strategy", "content", "quality", "compliance", "ads", "analytics"
})
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE = re.compile(r"(?<!\d)(?:\+84|0)[\s.-]?(?:3|5|7|8|9)(?:[\s.-]?\d){8}(?!\d)")
MAX_HANDOFF_CHARS = 20000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def approval_digest(action: str, payload: dict) -> str:
    """Hash ALL bytes of the normalized action, payload and destination."""
    return digest(json.dumps({"action": action, "payload": payload},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def scrub_output(role: str, text: str) -> tuple[str, bool]:
    """Only general-purpose AI drafts forbid personal contact data.

    CRM/lead records have their own permissions and lawful processing rules.
    Do not erase valid CRM contacts with this general-purpose output guard.
    """
    if role not in PRIVATE_OUTPUT_ROLES:
        return text, False
    cleaned = EMAIL.sub("[REDACTED_EMAIL]", text)
    cleaned = PHONE.sub("[REDACTED_PHONE]", cleaned)
    return cleaned, cleaned != text


def verdict_from_output(text: str) -> str:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return "unknown"
    if not isinstance(obj, dict):
        return "unknown"
    verdict = str(obj.get("verdict", "")).strip().lower()
    return verdict if verdict in {"pass", "fail", "needs_revision"} else "unknown"


def prepare_reviews(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS qa_reviews (
            qa_task_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            source_task_id TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            role TEXT NOT NULL,
            verdict TEXT NOT NULL,
            recorded_at INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS ix_qa_reviews_source
            ON qa_reviews(workspace_id, source_task_id);
    """)


def record_review(c: sqlite3.Connection, task: dict, content: str) -> None:
    """Called ONLY after a QA task actually finishes, in the result transaction."""
    if task["role"] not in REVIEW_ROLES or not task.get("depends_on"):
        return
    predecessor = c.execute(
        "SELECT id,workspace_id,status,result FROM tasks WHERE id=? AND workspace_id=?",
        (task["depends_on"], task["workspace_id"]),
    ).fetchone()
    if not predecessor or predecessor["status"] != "completed" or not predecessor["result"]:
        return
    nested = c.execute("SELECT source_task_id FROM qa_reviews WHERE qa_task_id=?",
                       (predecessor["id"],)).fetchone()
    source_id = nested["source_task_id"] if nested else predecessor["id"]
    source = c.execute("SELECT result,status FROM tasks WHERE id=? AND workspace_id=?",
                       (source_id, task["workspace_id"])).fetchone()
    if not source or source["status"] != "completed" or not source["result"]:
        return
    c.execute("""INSERT INTO qa_reviews
      (qa_task_id,workspace_id,source_task_id,source_digest,role,verdict)
      VALUES(?,?,?,?,?,?)
      ON CONFLICT(qa_task_id) DO UPDATE SET source_digest=excluded.source_digest,
        verdict=excluded.verdict, recorded_at=unixepoch()""",
      (task["id"], task["workspace_id"], source_id, digest(source["result"]),
       task["role"], verdict_from_output(content)))


def review_task_passed(c: sqlite3.Connection, task_id: str) -> bool:
    row = c.execute("SELECT verdict FROM qa_reviews WHERE qa_task_id=?", (task_id,)).fetchone()
    return bool(row and row["verdict"] == "pass")


def assert_source_approved(c: sqlite3.Connection, ws: str, action: str, payload: dict) -> None:
    """Every source-linked request requires current review, including draft posts.

    Human-authored jobs without source_task_id still require separate tool approval.
    """
    source_id = payload.get("source_task_id")
    if source_id is None:
        return
    if not isinstance(source_id, str) or not source_id:
        raise HTTPException(422, "source_task_id không hợp lệ")
    source = c.execute("SELECT id,status,result FROM tasks WHERE id=? AND workspace_id=?",
                       (source_id, ws)).fetchone()
    if not source or source["status"] != "completed" or not source["result"]:
        raise HTTPException(409, "Nguồn AI chưa hoàn tất hoặc không tồn tại")
    text = payload.get("content")
    if not isinstance(text, str) or digest(text) != digest(source["result"]):
        raise HTTPException(409, "Nội dung nguồn đã đổi hoặc khác bản được kiểm tra; cần QA lại")
    reviews = c.execute("""SELECT role,verdict,source_digest FROM qa_reviews
        WHERE workspace_id=? AND source_task_id=? ORDER BY rowid""",
        (ws, source_id)).fetchall()
    latest_by_role = {}
    expected = digest(source["result"])
    for review in reviews:
        if review["source_digest"] == expected:
            latest_by_role[review["role"]] = review["verdict"]
    # If a rejection was ever recorded for this content, the same reviewer role
    # must explicitly pass again. A pass in another role cannot override its fail.
    if not latest_by_role or any(v != "pass" for v in latest_by_role.values()):
        raise HTTPException(409, "QA chưa pass cho đúng bản nội dung; chặn xuất bản")
