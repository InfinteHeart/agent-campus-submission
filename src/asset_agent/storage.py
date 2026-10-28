"""Transactional drafts, authoritative slot claims, and trusted CLI confirmation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .domain import DomainError, canonical, digest, evaluate, material_snapshot, now, slot_time


class Store:
    def __init__(self, path: Path, schedules: list | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(request_id),
                material_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(request_id, material_id));
            CREATE TABLE IF NOT EXISTS slot_claims(
                channel TEXT NOT NULL, slot TEXT NOT NULL, source TEXT NOT NULL, owner TEXT NOT NULL,
                PRIMARY KEY(channel, slot));
            CREATE TABLE IF NOT EXISTS draft_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL,
                revision INTEGER NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
        """)
        with self.transaction():
            if not self.conn.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                for entry in schedules or []:
                    self.conn.execute("INSERT INTO slot_claims VALUES(?,?,?,?)",
                                      (entry["channel"], slot_time(entry["slot"]), "schedule", entry["owner"]))
                self.conn.execute("INSERT INTO metadata VALUES('seeded','1')")

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    def register(self, request: dict):
        with self.transaction():
            row = self.conn.execute("SELECT fingerprint FROM requests WHERE request_id=?",
                                    (request["request_id"],)).fetchone()
            if row and row["fingerprint"] != digest(request):
                raise DomainError("REQUEST_ID_REUSED", "同一 request_id 的用户要求已变化；请使用新的 request_id")
            self.conn.execute("INSERT OR IGNORE INTO requests VALUES(?,?)",
                              (request["request_id"], digest(request)))

    @staticmethod
    def unpack(row):
        if not row:
            return None
        return {"id": row["id"], "request_id": row["request_id"], "material_id": row["material_id"],
                "revision": row["revision"], "state": row["state"],
                "updated_at": row["updated_at"], **json.loads(row["payload"])}

    def get(self, request_id: str, material_id: str):
        return self.unpack(self.conn.execute("SELECT * FROM drafts WHERE request_id=? AND material_id=?",
                                            (request_id, material_id)).fetchone())

    def get_id(self, draft_id: str):
        return self.unpack(self.conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())

    def occupancy(self, channel: str, slot: str, own_draft: str | None = None):
        row = self.conn.execute("SELECT source,owner FROM slot_claims WHERE channel=? AND slot=?",
                                (channel, slot)).fetchone()
        if row and not (row["source"] == "draft" and row["owner"] == own_draft):
            return dict(row)
        return None

    def _event(self, draft_id, revision, event, payload):
        self.conn.execute("INSERT INTO draft_events(draft_id,revision,event,payload,at) VALUES(?,?,?,?,?)",
                          (draft_id, revision, event, canonical(payload), now()))

    def stale(self, request_id: str, material_id: str, reason: str):
        with self.transaction():
            old = self.get(request_id, material_id)
            if old and old["state"] != "stale":
                self.conn.execute("DELETE FROM slot_claims WHERE source='draft' AND owner=?", (old["id"],))
                self.conn.execute("UPDATE drafts SET state='stale',revision=revision+1,updated_at=? WHERE id=?",
                                  (now(), old["id"]))
                self._event(old["id"], old["revision"] + 1, "invalidated", {"reason": reason})

    def save(self, request_id: str, item: dict, slot: str, snapshot: dict,
             expected_revision: int | None, inject_failure: bool = False) -> tuple[dict, str]:
        # BEGIN IMMEDIATE serializes checking the slot and claiming it.
        with self.transaction():
            old = self.get(request_id, item["material_id"])
            if (old["revision"] if old else None) != expected_revision:
                raise DomainError("STALE_DRAFT", "草稿版本已变化，请重新读取后再保存")
            own = old["id"] if old else None
            if self.occupancy(item["channel"], slot, own):
                raise DomainError("SLOT_CONFLICT", "保存时复查发现时段已占用，请重新查询")
            approved = bool(old and old["state"] == "ready"
                            and old.get("approved_alternative_at") == slot
                            and old["snapshot"] == snapshot["signature"])
            needs_confirmation = slot != item["requested_at"] and not approved
            state = "pending_confirmation" if needs_confirmation else "ready"
            payload = {"channel": item["channel"], "requested_at": item["requested_at"],
                       "requirement_type": item["requirement_type"], "suggested_at": slot,
                       "scheduled_at": None if needs_confirmation else slot,
                       "approved_alternative_at": slot if approved else None,
                       "confirmation_status": "pending" if needs_confirmation else ("confirmed" if approved else "not_required"),
                       "snapshot": snapshot["signature"], "rules_version": snapshot["rules"]["version"],
                       "supplementary_loaded": snapshot["supplementary_loaded"]}
            existing_payload = None
            if old:
                existing_payload = json.loads(self.conn.execute("SELECT payload FROM drafts WHERE id=?", (own,)).fetchone()[0])
            if old and old["state"] == state and existing_payload == payload:
                if inject_failure:
                    raise sqlite3.OperationalError("synthetic persistence failure")
                return old, "existing"
            draft_id, revision = (own, old["revision"] + 1) if old else ("D-" + uuid.uuid4().hex[:12], 1)
            self.conn.execute("DELETE FROM slot_claims WHERE source='draft' AND owner=?", (draft_id,))
            if state == "ready":
                self.conn.execute("INSERT INTO slot_claims VALUES(?,?,?,?)", (item["channel"], slot, "draft", draft_id))
            self.conn.execute("""INSERT INTO drafts VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(request_id,material_id) DO UPDATE SET revision=excluded.revision,
                state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at""",
                              (draft_id, request_id, item["material_id"], revision, state, canonical(payload), now()))
            self._event(draft_id, revision, "saved", payload)
            # Fault is deliberately after writes: tests prove full rollback.
            if inject_failure:
                raise sqlite3.OperationalError("synthetic persistence failure")
            return self.get(request_id, item["material_id"]), "saved"

    def confirm(self, draft_id: str, revision: int, slot: str, data_dir: Path):
        slot = slot_time(slot)
        with self.transaction():
            draft = self.get_id(draft_id)
            if not draft:
                raise DomainError("DRAFT_NOT_FOUND", "草稿不存在")
            if draft["revision"] != revision:
                raise DomainError("STALE_CONFIRMATION", "草稿版本已变化，必须重新查看并确认")
            if draft["state"] != "pending_confirmation" or draft["suggested_at"] != slot:
                raise DomainError("INVALID_CONFIRMATION", "只能确认该版本草稿的具体待确认时段")
            snapshot = material_snapshot(data_dir, draft["material_id"], draft["supplementary_loaded"])
            if snapshot["signature"] != draft["snapshot"] or evaluate(snapshot, draft["channel"])["status"] != "passed":
                raise DomainError("STALE_REVIEW", "素材或规则已变化，必须重新审核并生成提案")
            if self.occupancy(draft["channel"], slot, draft_id):
                raise DomainError("SLOT_CONFLICT", "确认时复查发现该时段已占用，请重新生成提案")
            payload = json.loads(self.conn.execute("SELECT payload FROM drafts WHERE id=?", (draft_id,)).fetchone()[0])
            payload.update(scheduled_at=slot, approved_alternative_at=slot, confirmation_status="confirmed")
            self.conn.execute("INSERT INTO slot_claims VALUES(?,?,?,?)", (draft["channel"], slot, "draft", draft_id))
            self.conn.execute("UPDATE drafts SET state='ready',revision=revision+1,payload=?,updated_at=? WHERE id=?",
                              (canonical(payload), now(), draft_id))
            self._event(draft_id, revision + 1, "user_confirmed", {"slot": slot, "previous_revision": revision})
            return self.get_id(draft_id)

    def replace_schedules(self, schedules: list):
        """Synthetic demo/test fixture mutation; deliberately not an Agent tool."""
        with self.transaction():
            self.conn.execute("DELETE FROM slot_claims WHERE source='schedule'")
            for entry in schedules:
                slot = slot_time(entry["slot"])
                occupied = self.conn.execute("SELECT source,owner FROM slot_claims WHERE channel=? AND slot=?",
                                             (entry["channel"], slot)).fetchone()
                if occupied and occupied["source"] == "draft":
                    old = self.get_id(occupied["owner"])
                    self.conn.execute("DELETE FROM slot_claims WHERE channel=? AND slot=?", (entry["channel"], slot))
                    self.conn.execute("UPDATE drafts SET state='stale',revision=revision+1,updated_at=? WHERE id=?",
                                      (now(), old["id"]))
                    self._event(old["id"], old["revision"] + 1, "schedule_changed", {"slot": slot})
                self.conn.execute("INSERT INTO slot_claims VALUES(?,?,?,?)", (entry["channel"], slot, "schedule", entry["owner"]))
