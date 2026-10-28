"""Input validation, canonical times, and synthetic rule evaluation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def slot_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        dt = dt.astimezone(TZ)
        if dt.minute or dt.second or dt.microsecond:
            raise ValueError("hour-aligned slots required")
        return dt.isoformat(timespec="seconds")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DomainError("INVALID_TIME", "时间必须带时区且对齐整点，例如 2026-10-29T18:00:00+08:00") from exc


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_request(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"request_id", "timezone", "items"}:
        raise DomainError("INVALID_REQUEST", "请求仅允许 request_id、timezone、items")
    if not isinstance(raw.get("request_id"), str) or not 1 <= len(raw["request_id"]) <= 100:
        raise DomainError("INVALID_REQUEST", "request_id 必须是 1–100 字符的字符串")
    if raw.get("timezone") != "Asia/Shanghai":
        raise DomainError("INVALID_REQUEST", "本项目仅支持 Asia/Shanghai")
    if not isinstance(raw.get("items"), list) or not 1 <= len(raw["items"]) <= 5:
        raise DomainError("INVALID_REQUEST", "每次请求必须包含 1–5 项素材")
    items, seen = [], set()
    for item in raw["items"]:
        fields = {"material_id", "channel", "requested_at", "requirement_type"}
        if not isinstance(item, dict) or set(item) != fields:
            raise DomainError("INVALID_REQUEST", f"素材项字段必须是 {sorted(fields)}，不接受模型或输入伪造的确认字段")
        if any(not isinstance(item[k], str) or not item[k].strip() for k in fields):
            raise DomainError("INVALID_REQUEST", "素材项字段必须为非空字符串")
        if item["material_id"] in seen:
            raise DomainError("INVALID_REQUEST", "同一请求不能重复包含同一素材")
        seen.add(item["material_id"])
        if item["requirement_type"] not in {"hard", "preferred"}:
            raise DomainError("INVALID_REQUEST", "requirement_type 必须为 hard 或 preferred")
        items.append({**item, "requested_at": slot_time(item["requested_at"])})
    return {"request_id": raw["request_id"], "timezone": raw["timezone"], "items": items}


def material_snapshot(data_dir: Path, material_id: str, supplementary: bool) -> dict:
    catalog = load_json(data_dir / "materials.json")
    rules = load_json(data_dir / "rules.json")
    base = catalog.get(material_id)
    evidence = load_json(data_dir / "supplementary.json").get(material_id, {}) if supplementary else {}
    material = {**base, **evidence} if base else None
    return {"material": material, "rules": rules, "supplementary_loaded": supplementary,
            "signature": digest({"material": material, "rules": rules})}


def evaluate(snapshot: dict, channel: str) -> dict:
    material, rules = snapshot["material"], snapshot["rules"]
    reasons, missing = [], []
    if material is None:
        return {"status": "needs_info", "missing_fields": ["material"],
                "reasons": [{"rule_id": "MATERIAL_NOT_FOUND", "message": "素材不存在"}],
                "supplementary_available": False, "snapshot": snapshot["signature"]}
    for field in rules["required_fields"]:
        if material.get(field) in (None, "", []):
            missing.append(field)
    blocked = []
    if channel not in rules["channels"]:
        blocked.append({"rule_id": "CHANNEL_NOT_ALLOWED", "message": "不支持该发布渠道"})
    elif channel not in material.get("allowed_channels", []):
        blocked.append({"rule_id": "CHANNEL_MISMATCH", "message": "素材不允许投放到该渠道"})
    for rule in rules["text_rules"]:
        if rule["pattern"] in material.get("text", ""):
            reasons.append({"rule_id": rule["id"], "message": rule["message"], "severity": rule["severity"]})
    if material.get("rights_status") == "denied":
        blocked.append({"rule_id": "RIGHTS_DENIED", "message": "合成授权状态为禁止使用"})
    elif material.get("rights_status") != "cleared":
        if "rights_status" not in missing:
            missing.append("rights_status")
        reasons.append({"rule_id": "RIGHTS_UNVERIFIED", "message": "授权状态未核实，不能仅凭证明编号视为已授权"})
    reasons = blocked + reasons
    status = "passed"
    if blocked or any(r.get("severity") == "block" for r in reasons):
        status = "blocked"
    elif missing:
        status = "needs_info"
    elif reasons:
        status = "needs_review"
    return {"status": status, "missing_fields": missing, "reasons": reasons,
            "supplementary_available": bool(material.get("supplementary_available")) and not snapshot["supplementary_loaded"],
            "rules_version": rules["version"], "snapshot": snapshot["signature"]}
