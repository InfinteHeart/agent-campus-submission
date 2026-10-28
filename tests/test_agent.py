from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from asset_agent.agent import run_request
from asset_agent.cli import main
from asset_agent.demo import CASES, run_case, request_for, schedules_for
from asset_agent.domain import DomainError, canonical, load_json, validate_request
from asset_agent.model import DeepSeekModel, MockModel, ModelError, load_env
from asset_agent.storage import Store
from asset_agent.tools import TOOL_SCHEMAS, Tools


class ScriptedModel:
    """Adversarial test double; never used as live decision evidence."""
    mode = "mock"
    name = "adversarial-scripted-test-double"

    def __init__(self, actions):
        self.actions = iter(actions)

    def decide(self, messages, schemas):
        action, args = next(self.actions)
        return {"assistant": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"test-{len(messages)}", "type": "function", "function": {
                "name": action, "arguments": canonical(args)}}]}, "metadata": {"mode": "mock"}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        for source in (ROOT / "data").glob("*.json"):
            (self.data / source.name).write_bytes(source.read_bytes())
        self.store = Store(self.root / "test.sqlite", [])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_agent(self, request=None, **kwargs):
        return run_request(request or request_for("test"), MockModel(), self.store, self.data, **kwargs)

    def prepared_tools(self, request=None, **kwargs):
        req = validate_request(request or request_for("tools"))
        self.store.register(req)
        tools = Tools(self.store, self.data, req["request_id"], req["items"][0], **kwargs)
        for action, args in [("read_material", {"section": "base"}), ("check_rules", {}), ("query_slots", {"scope": "day"})]:
            self.assertTrue(tools.execute(action, {**args, "reason": "测试准备"})["ok"])
        return tools

    def test_all_offline_acceptance_cases(self):
        for case in CASES:
            with self.subTest(case=case):
                report = run_case(case, "mock", self.root / case, self.data)
                self.assertTrue(report["passed"], report)

    def test_material_not_found(self):
        output, _ = self.run_agent(request_for("unknown", "M999"))
        self.assertEqual(output["items"][0]["outcome"], "needs_info")
        self.assertEqual(output["items"][0]["missing_fields"], ["material"])

    def test_no_publish_or_confirmation_tools(self):
        names = {x["function"]["name"] for x in TOOL_SCHEMAS}
        self.assertNotIn("publish", names)
        self.assertNotIn("confirm", names)
        tools = self.prepared_tools()
        for name in ["publish", "confirm", "replace_schedules", "__getattribute__"]:
            self.assertFalse(tools.execute(name, {"reason": "恶意动作"})["ok"])

    def test_cannot_forge_confirmation_argument(self):
        tools = self.prepared_tools()
        result = tools.execute("save_draft", {"slot": "2026-10-29T19:00:00+08:00", "approved": True, "reason": "伪造确认"})
        self.assertEqual(result["code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM drafts").fetchone()[0], 0)

    def test_unapproved_alternative_always_pending(self):
        for requirement in ["hard", "preferred"]:
            with self.subTest(requirement=requirement):
                request = request_for("alternative-" + requirement)
                request["items"][0]["requirement_type"] = requirement
                tools = self.prepared_tools(request)
                obs = tools.execute("save_draft", {"slot": "2026-10-29T19:00:00+08:00", "reason": "选择替代时间"})
                self.assertEqual(obs["draft"]["state"], "pending_confirmation")
                self.assertIsNone(obs["draft"]["scheduled_at"])

    def test_reject_unobserved_slot(self):
        tools = self.prepared_tools()
        result = tools.execute("save_draft", {"slot": "2026-10-30T18:00:00+08:00", "reason": "越出查询范围"})
        self.assertEqual(result["code"], "UNOBSERVED_SLOT")

    def test_cannot_save_before_review(self):
        req = validate_request(request_for("early"))
        self.store.register(req)
        tools = Tools(self.store, self.data, req["request_id"], req["items"][0])
        result = tools.execute("save_draft", {"slot": req["items"][0]["requested_at"], "reason": "跳过审核"})
        self.assertEqual(result["code"], "REVIEW_REQUIRED")

    def test_cannot_claim_success_without_persistence(self):
        tools = self.prepared_tools()
        result = tools.execute("finish", {"outcome": "draft_ready", "reason": "伪报成功"})
        self.assertEqual(result["code"], "UNSUPPORTED_OUTCOME")
        self.assertFalse(tools.finished)

    def test_model_can_recover_from_rejected_action(self):
        actions = [("publish", {"reason": "尝试未授权工具"}),
                   ("read_material", {"section": "base", "reason": "读取"}),
                   ("check_rules", {"reason": "审核"}),
                   ("query_slots", {"scope": "requested", "reason": "查询"}),
                   ("save_draft", {"slot": "2026-10-29T18:00:00+08:00", "reason": "保存"}),
                   ("finish", {"outcome": "draft_ready", "reason": "回执成功"})]
        result, trace = run_request(request_for("recover"), ScriptedModel(actions), self.store, self.data)
        self.assertEqual(trace[0]["observation"]["code"], "INVALID_TOOL")
        self.assertEqual(result["items"][0]["outcome"], "draft_ready")

    def test_persistent_write_failure_rolls_back_all_writes(self):
        output, _ = self.run_agent(save_failures=-1)
        self.assertEqual(output["items"][0]["draft"]["save_status"], "failed")
        for table in ["drafts", "draft_events", "slot_claims"]:
            self.assertEqual(self.store.conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)

    def test_transient_write_failure_recovers_same_request(self):
        output, events = self.run_agent(save_failures=1)
        self.assertEqual(output["items"][0]["outcome"], "draft_ready")
        writes = [e for e in events if e.get("action") == "save_draft"]
        self.assertFalse(writes[0]["observation"]["ok"])
        self.assertTrue(writes[1]["observation"]["ok"])
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)

    def test_request_id_cannot_overwrite_changed_requirements(self):
        req = request_for("reuse")
        self.run_agent(req)
        req["items"][0]["requested_at"] = "2026-10-29T19:00:00+08:00"
        with self.assertRaisesRegex(DomainError, "request_id"):
            self.run_agent(req)

    def test_equivalent_timezone_replay_is_idempotent(self):
        req = request_for("timezone")
        first, _ = self.run_agent(req)
        req["items"][0]["requested_at"] = "2026-10-29T10:00:00Z"
        second, _ = self.run_agent(req)
        self.assertEqual(first["items"][0]["draft"]["record"]["id"], second["items"][0]["draft"]["record"]["id"])

    def test_invalid_request_and_approval_fields_rejected(self):
        for key, value in [("requested_at", "2026-10-29T18:00:00"), ("requested_at", "2026-10-29T18:30:00+08:00"),
                           ("requirement_type", "free"), ("approved", True)]:
            with self.subTest(key=key, value=value):
                req = request_for("invalid")
                req["items"][0][key] = value
                with self.assertRaises(DomainError):
                    validate_request(req)

    def test_save_rechecks_conflict_after_query(self):
        tools = self.prepared_tools()
        self.store.replace_schedules(schedules_for(18))
        result = tools.execute("save_draft", {"slot": "2026-10-29T18:00:00+08:00", "reason": "查询后被抢占"})
        self.assertEqual(result["code"], "SLOT_CONFLICT")

    def test_ranked_candidates_prefer_later_when_distance_ties(self):
        self.store.replace_schedules(schedules_for(18))
        tools = self.prepared_tools()
        observation = tools.execute("query_slots", {"scope": "day", "reason": "检查排序"})
        self.assertEqual(observation["ranked_available_slots"][:2],
                         ["2026-10-29T19:00:00+08:00", "2026-10-29T17:00:00+08:00"])

    def test_save_rechecks_changed_rules(self):
        tools = self.prepared_tools()
        rules = load_json(self.data / "rules.json")
        rules["version"] = "changed"
        (self.data / "rules.json").write_text(json.dumps(rules))
        result = tools.execute("save_draft", {"slot": "2026-10-29T18:00:00+08:00", "reason": "规则已变化"})
        self.assertEqual(result["code"], "STALE_REVIEW")

    def pending_draft(self):
        self.store.replace_schedules(schedules_for(18))
        output, _ = self.run_agent(request_for("confirmation-test"))
        return output["items"][0]["draft"]["record"]

    def test_stale_confirmation_revision_rejected(self):
        draft = self.pending_draft()
        with self.assertRaises(DomainError) as caught:
            self.store.confirm(draft["id"], draft["revision"] + 1, draft["suggested_at"], self.data)
        self.assertEqual(caught.exception.code, "STALE_CONFIRMATION")

    def test_confirmation_of_different_slot_rejected(self):
        draft = self.pending_draft()
        with self.assertRaises(DomainError) as caught:
            self.store.confirm(draft["id"], draft["revision"], "2026-10-29T20:00:00+08:00", self.data)
        self.assertEqual(caught.exception.code, "INVALID_CONFIRMATION")

    def test_confirmation_rechecks_material_snapshot(self):
        draft = self.pending_draft()
        catalog = load_json(self.data / "materials.json")
        catalog["M01"]["text"] = "保证收益"
        (self.data / "materials.json").write_text(json.dumps(catalog))
        with self.assertRaises(DomainError) as caught:
            self.store.confirm(draft["id"], draft["revision"], draft["suggested_at"], self.data)
        self.assertEqual(caught.exception.code, "STALE_REVIEW")

    def test_concurrent_save_only_one_ready_claim(self):
        barrier = threading.Barrier(2)
        db = self.root / "race.sqlite"
        Store(db, []).close()
        def worker(index):
            store = Store(db)
            req = validate_request(request_for(f"race-{index}"))
            store.register(req)
            tools = Tools(store, self.data, req["request_id"], req["items"][0])
            try:
                tools.execute("read_material", {"section": "base", "reason": "读取"})
                tools.execute("check_rules", {"reason": "检查"})
                tools.execute("query_slots", {"scope": "requested", "reason": "同时查询"})
                barrier.wait(timeout=5)
                return tools.execute("save_draft", {"slot": req["items"][0]["requested_at"], "reason": "同时保存"})
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, range(2)))
        self.assertEqual(sum(o["ok"] for o in outcomes), 1)
        self.assertEqual(next(o["code"] for o in outcomes if not o["ok"]), "SLOT_CONFLICT")
        store = Store(db)
        try:
            self.assertEqual(store.conn.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)
        finally:
            store.close()

    def test_batch_failure_does_not_block_other_items(self):
        req = request_for("mixed", "M03")
        req["items"].append({**req["items"][0], "material_id": "M01"})
        output, _ = self.run_agent(req)
        self.assertEqual([i["outcome"] for i in output["items"]], ["blocked", "draft_ready"])

    def test_limit_preserves_saved_draft_but_marks_run_incomplete(self):
        output, _ = self.run_agent(max_steps=4)
        self.assertEqual(output["run_status"], "incomplete")
        self.assertEqual(output["items"][0]["draft"]["save_status"], "saved")
        self.assertEqual(output["items"][0]["outcome"], "incomplete")

    def test_model_failure_returns_incomplete_without_mock_fallback(self):
        class UnavailableModel:
            mode, name = "live", "deepseek-flash"
            def decide(self, messages, schemas):
                raise ModelError("MODEL_HTTP_503: unavailable")
        output, events = run_request(request_for("model-failed"), UnavailableModel(), self.store, self.data)
        self.assertEqual(output["run_status"], "incomplete")
        self.assertEqual(output["mode"], "live")
        self.assertEqual(events[0]["event"], "model_error")
        self.assertEqual(output["items"][0]["draft"]["save_status"], "not_attempted")

    def test_invalid_actions_consume_budget(self):
        actions = [("publish", {"reason": "无效动作"})] * 3
        output, events = run_request(request_for("invalid-budget"), ScriptedModel(actions), self.store, self.data, max_steps=3)
        self.assertEqual(output["steps"], 3)
        self.assertEqual(output["tool_calls"], 3)
        self.assertEqual(output["run_status"], "incomplete")
        self.assertEqual(sum(e.get("event") == "action" for e in events), 3)

    def test_cli_exit_status_and_trace(self):
        request_path = self.root / "input.json"
        request_path.write_text(canonical(request_for("cli")))
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = main(["run", "--mode", "mock", "--input", str(request_path), "--db", str(self.root / "cli.sqlite"),
                         "--data-dir", str(self.data), "--trace", str(self.root / "trace.jsonl")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["mode"], "mock")
        self.assertTrue((self.root / "trace.jsonl").is_file())


class ModelAdapterTests(unittest.TestCase):
    def test_deepseek_flash_native_tool_protocol(self):
        captured = {}
        def opener(request, timeout):
            captured["payload"] = json.loads(request.data)
            captured["url"] = request.full_url
            return io.BytesIO(json.dumps({"id": "provider-response", "model": "deepseek-flash",
                "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "reasoning_content": "must never be collected", "tool_calls": [{"id": "native-call", "type": "function",
                "function": {"name": "read_material", "arguments": "{\"section\":\"base\",\"reason\":\"读取\"}"}}]}}], "usage": {"total_tokens": 42}}).encode())
        model = DeepSeekModel(api_key="synthetic-not-real", opener=opener)
        reply = model.decide([{"role": "user", "content": "test"}], TOOL_SCHEMAS)
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["payload"]["model"], "deepseek-flash")
        self.assertEqual(captured["payload"]["thinking"], {"type": "disabled"})
        self.assertEqual(reply["assistant"]["tool_calls"][0]["id"], "native-call")
        self.assertNotIn("reasoning_content", reply["assistant"])

    def test_missing_key_has_no_silent_mock_fallback(self):
        with self.assertRaisesRegex(ModelError, "MISSING_API_KEY"):
            DeepSeekModel(api_key="")

    def test_http_error_does_not_expose_provider_body(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "secret-body", {}, io.BytesIO(b"synthetic-secret"))
        model = DeepSeekModel(api_key="synthetic-key", opener=opener)
        with self.assertRaisesRegex(ModelError, "MODEL_HTTP_401") as caught:
            model.decide([], TOOL_SCHEMAS)
        self.assertNotIn("secret", str(caught.exception))

    def test_multiple_calls_rejected_before_side_effects(self):
        call = {"id": "one", "type": "function", "function": {"name": "save_draft", "arguments": "{}"}}
        def opener(request, timeout):
            return io.BytesIO(json.dumps({"choices": [{"message": {"tool_calls": [call, call]}}]}).encode())
        with self.assertRaisesRegex(ModelError, "INVALID_MODEL_RESPONSE"):
            DeepSeekModel(api_key="synthetic", opener=opener).decide([], TOOL_SCHEMAS)

    def test_malformed_provider_shapes_return_model_error(self):
        for message in [42, [], {"tool_calls": ["invalid"]}]:
            with self.subTest(message=message):
                def opener(request, timeout):
                    return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())
                with self.assertRaisesRegex(ModelError, "INVALID_MODEL_RESPONSE"):
                    DeepSeekModel(api_key="synthetic", opener=opener).decide([], TOOL_SCHEMAS)

    def test_env_supports_assignment_and_literal_key_without_execution(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / ".env"
            path.write_text('DEEPSEEK_API_KEY = "synthetic-key"\nDEEPSEEK_MODEL=deepseek-flash\n')
            load_env(path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "synthetic-key")
            os.environ.pop("DEEPSEEK_API_KEY")
            path.write_text("sk-synthetic-literal")
            load_env(path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-synthetic-literal")


if __name__ == "__main__":
    unittest.main()
