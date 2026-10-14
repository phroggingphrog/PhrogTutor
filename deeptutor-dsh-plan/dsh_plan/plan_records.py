"""Versioned plan, approval and handoff records (SQLite, stdlib only)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid

DEFAULT_DB = os.environ.get("DSH_PLAN_DB", "/app/data/user/dsh_plan.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans(
  plan_id TEXT, version INTEGER, project_id TEXT, content TEXT, sha256 TEXT,
  workspace_key TEXT, scope TEXT, state TEXT, source_session_id TEXT, created_at REAL,
  PRIMARY KEY(plan_id, version));
CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY, plan_id TEXT, version INTEGER, sha256 TEXT,
  approver TEXT, permission TEXT, approved_at REAL, state TEXT);
CREATE TABLE IF NOT EXISTS handoffs(
  approval_id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT, workspace_id TEXT,
  state TEXT, last_seq INTEGER, result TEXT, updated_at REAL);
"""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str = DEFAULT_DB):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _one(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def draft(self, project_id, content, workspace_key, scope="", source_session_id="", plan_id=None):
        """Create v1, or a new version (older proposed/approved versions become superseded)."""
        plan_id = plan_id or "plan-" + uuid.uuid4().hex[:12]
        last = self._one("SELECT MAX(version) v FROM plans WHERE plan_id=?", (plan_id,))["v"] or 0
        with self.db:
            self.db.execute("UPDATE plans SET state='superseded' WHERE plan_id=? AND state IN('proposed','approved')", (plan_id,))
            self.db.execute("UPDATE approvals SET state='invalidated' WHERE plan_id=? AND state='active'", (plan_id,))
            self.db.execute(
                "INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?)",
                (plan_id, last + 1, project_id, content, sha256_text(content), workspace_key, scope,
                 "proposed", source_session_id, time.time()))
        return self.plan(plan_id, last + 1)

    def plan(self, plan_id, version=None):
        if version is None:
            return self._one("SELECT * FROM plans WHERE plan_id=? ORDER BY version DESC LIMIT 1", (plan_id,))
        return self._one("SELECT * FROM plans WHERE plan_id=? AND version=?", (plan_id, version))

    def approval(self, approval_id):
        return self._one("SELECT * FROM approvals WHERE approval_id=?", (approval_id,))

    def handoff(self, approval_id):
        return self._one("SELECT * FROM handoffs WHERE approval_id=?", (approval_id,))

    def dump(self, plan_id):
        return json.dumps(self.plan(plan_id), indent=2)
