"""Native DeepSeek tool calling plus explicitly offline observation-based Mock."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .domain import canonical


class ModelError(RuntimeError):
    pass


def load_env(path: Path):
    """Small literal KEY=VALUE loader. Never execute shell or print secrets."""
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    meaningful = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    if len(meaningful) == 1 and meaningful[0].startswith("sk-") and "=" not in meaningful[0]:
        os.environ.setdefault("DEEPSEEK_API_KEY", meaningful[0])
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if sep and key.strip().startswith("DEEPSEEK_"):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


class DeepSeekModel:
    mode = "live"

    def __init__(self, api_key=None, base_url=None, model=None, timeout=None, opener=None):
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.name = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.timeout = float(timeout if timeout is not None else os.getenv("DEEPSEEK_TIMEOUT", "45"))
        if not self.api_key or self.api_key.startswith("your-"):
            raise ModelError("MISSING_API_KEY: 请在 .env 或环境变量配置 DEEPSEEK_API_KEY；离线运行显式使用 --mode mock")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("INVALID_BASE_URL: 模型服务必须使用无内嵌凭据的 HTTPS URL")
        if not 1 <= self.timeout <= 120:
            raise ModelError("INVALID_TIMEOUT: DEEPSEEK_TIMEOUT 必须在 1–120 秒之间")
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def decide(self, messages: list, schemas: list) -> dict:
        payload = {"model": self.name, "messages": messages, "tools": schemas,
                   "tool_choice": "required", "parallel_tool_calls": False,
                   "thinking": {"type": "disabled"}, "temperature": 0, "max_tokens": 1200,
                   "stream": False}
        request = urllib.request.Request(self.base_url + "/chat/completions",
                    data=canonical(payload).encode(),
                    headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        self.calls += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ModelError("MODEL_RESPONSE_TOO_LARGE")
            raw = json.loads(body)
            choice = raw["choices"][0]
            message = choice["message"]
            calls = message.get("tool_calls", [])
            if choice.get("finish_reason") == "length" or len(calls) != 1:
                raise ModelError("INVALID_MODEL_RESPONSE: 每轮必须返回恰好一个完整工具调用")
            call = calls[0]
            if call.get("type") != "function" or not isinstance(call.get("id"), str):
                raise ModelError("INVALID_MODEL_RESPONSE: 工具调用格式无效")
            # Preserve the provider's actual native call, never synthesize one.
            # Non-thinking mode deliberately avoids collecting chain of thought.
            assistant = {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
            return {"assistant": assistant,
                    "metadata": {"mode": "live", "requested_model": self.name,
                                 "returned_model": raw.get("model"), "response_id": raw.get("id"),
                                 "usage": raw.get("usage", {})}}
        except urllib.error.HTTPError as exc:
            # Never expose upstream bodies/headers which may include credentials.
            raise ModelError(f"MODEL_HTTP_{exc.code}: 模型请求失败，未切换 Mock") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ModelError(f"MODEL_CONNECTION_ERROR: {type(exc).__name__}；未切换 Mock") from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError(f"INVALID_MODEL_RESPONSE: {type(exc).__name__}") from None


class MockModel:
    """Deterministic test double, NOT proof of real model reasoning."""

    mode = "mock"
    name = "offline-observation-mock"

    def __init__(self):
        self.calls = 0

    def decide(self, messages: list, schemas: list) -> dict:
        self.calls += 1
        context = json.loads(messages[1]["content"])
        seen, history = {}, []
        call_names = {}
        for message in messages:
            for call in message.get("tool_calls", []):
                call_names[call["id"]] = call["function"]["name"]
            if message["role"] == "tool":
                obs = json.loads(message["content"])
                name = call_names[message["tool_call_id"]]
                seen[name] = obs
                history.append((name, obs))
        action, args = self._next(context, seen, history)
        return {"assistant": {"role": "assistant", "content": None,
                    "tool_calls": [{"id": f"mock-{self.calls}", "type": "function", "function": {
                        "name": action, "arguments": canonical(args)}}]},
                "metadata": {"mode": "mock", "requested_model": self.name,
                             "returned_model": self.name, "usage": {}}}

    @staticmethod
    def _next(context, seen, history):
        def action(name, reason, **kwargs):
            return name, {**kwargs, "reason": reason}
        if "read_material" not in seen:
            return action("read_material", "读取当前素材的基础资料", section="base")
        if history[-1][0] == "read_material":
            return action("check_rules", "根据最新读取资料检查规则")
        review = seen.get("check_rules", {})
        if review.get("status") == "needs_info":
            if review.get("supplementary_available"):
                return action("read_material", "缺失证明，补查资料后重新审核", section="supplementary")
            return action("finish", "补查后仍缺必要资料，等待用户补充", outcome="needs_info")
        if review.get("status") in {"blocked", "needs_review"}:
            return action("finish", "规则检查结果不允许自动排期", outcome=review["status"])
        saved = seen.get("save_draft")
        if saved:
            if saved["ok"]:
                outcome = "needs_confirmation" if saved["draft"]["state"] == "pending_confirmation" else "draft_ready"
                return action("finish", "根据实际保存回执结束；仅保存草稿", outcome=outcome)
            if saved.get("retryable"):
                # The prior slot comes from observed queries, not a canned trace.
                slot = MockModel._choose_slot(context, seen["query_slots"])
                return action("save_draft", "上次写入已回滚，按同一请求有限重试", slot=slot)
            return action("finish", "工具明确保存失败，不能报告成功", outcome="failed")
        slots = seen.get("query_slots")
        if not slots:
            return action("query_slots", "审核通过，检查用户要求的时段", scope="requested")
        if not slots.get("ok"):
            return action("finish", "时段工具失败，无法完成排期", outcome="failed")
        if not any(s["available"] for s in slots["slots"]):
            if slots["scope"] == "requested":
                return action("query_slots", "要求时段已占用，查询当日替代时段", scope="day")
            return action("finish", "当日无可用时段，等待用户提供其他日期", outcome="unavailable")
        return action("save_draft", "保存已查询可用的提案；改时由工具标记待确认", slot=MockModel._choose_slot(context, slots))

    @staticmethod
    def _choose_slot(context, observation):
        requested = datetime.fromisoformat(context["item"]["requested_at"])
        candidates = [s["slot"] for s in observation["slots"] if s["available"]]
        return min(candidates, key=lambda s: (abs((datetime.fromisoformat(s) - requested).total_seconds()), s < requested.isoformat()))
