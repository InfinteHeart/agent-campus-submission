"""LangGraph workflow around a bounded model -> tool -> observation loop."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .domain import canonical, now, validate_request
from .model import ModelError
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, Tools


class ItemState(TypedDict, total=False):
    messages: list[dict]
    steps: int
    tool_calls: int
    item_steps: int
    stop_reason: dict | None
    reply: dict
    assistant: dict
    call_id: str
    action: str
    raw_arguments: str
    arguments: dict | None
    observation: dict
    finished: bool


def build_item_graph(model, tools: Tools, item: dict, record, *, max_steps: int,
                     max_tool_calls: int, per_item_steps: int):
    """Compile one item's explicit workflow; model decisions stay inside model_decide."""

    def check_budget(state: ItemState):
        reason = ("step_limit" if state["steps"] >= max_steps else
                  "tool_call_limit" if state["tool_calls"] >= max_tool_calls else
                  "per_item_step_limit" if state["item_steps"] >= per_item_steps else None)
        if reason is None:
            return {}
        record({"event": "stopped", "graph_node": "check_budget", "material_id": item["material_id"],
                "reason": reason, "step": state["steps"], "at": now(), "mode": model.mode})
        return {"stop_reason": {"material_id": item["material_id"], "reason": reason}}

    def model_decide(state: ItemState):
        steps, item_steps = state["steps"] + 1, state["item_steps"] + 1
        try:
            reply = model.decide(state["messages"], TOOL_SCHEMAS)
            assistant = reply["assistant"]
            calls = assistant.get("tool_calls", [])
            if len(calls) != 1:
                raise ModelError("INVALID_MODEL_RESPONSE: exactly one tool call required")
            call = calls[0]
            function = call["function"]
            action = function["name"]
            raw_arguments = function["arguments"]
            call_id = call["id"]
        except (ModelError, KeyError, TypeError, AttributeError) as exc:
            error = str(exc) if isinstance(exc, ModelError) else "INVALID_MODEL_RESPONSE"
            record({"event": "model_error", "graph_node": "model_decide",
                    "material_id": item["material_id"], "step": steps,
                    "error": error, "mode": model.mode, "at": now()})
            return {"steps": steps, "item_steps": item_steps,
                    "stop_reason": {"material_id": item["material_id"],
                                    "reason": "model_error", "error": error}}
        return {"steps": steps, "item_steps": item_steps, "reply": reply,
                "assistant": assistant, "call_id": call_id, "action": action,
                "raw_arguments": raw_arguments}

    def validate_action(state: ItemState):
        try:
            arguments = json.loads(state["raw_arguments"])
        except (ValueError, TypeError):
            arguments = None
        return {"arguments": arguments, "tool_calls": state["tool_calls"] + 1}

    def execute_tool(state: ItemState):
        observation = tools.execute(state["action"], state["arguments"])
        return {"observation": observation, "finished": tools.finished}

    def record_observation(state: ItemState):
        arguments, observation = state["arguments"], state["observation"]
        record({"event": "action", "graph_node": "record_observation", "step": state["steps"],
                "item_step": state["item_steps"], "material_id": item["material_id"],
                "action": state["action"], "arguments": arguments,
                "decision_summary": arguments.get("reason", "") if isinstance(arguments, dict) else "参数格式无效",
                "observation": observation, "provider": state["reply"].get("metadata", {}), "at": now()})
        return {"messages": [*state["messages"], state["assistant"],
                             {"role": "tool", "tool_call_id": state["call_id"],
                              "content": canonical(observation)}]}

    graph = StateGraph(ItemState)
    graph.add_node("check_budget", check_budget)
    graph.add_node("model_decide", model_decide)
    graph.add_node("validate_action", validate_action)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("record_observation", record_observation)
    graph.add_edge(START, "check_budget")
    graph.add_conditional_edges("check_budget",
                                lambda state: "stop" if state.get("stop_reason") else "continue",
                                {"stop": END, "continue": "model_decide"})
    graph.add_conditional_edges("model_decide",
                                lambda state: "stop" if state.get("stop_reason") else "continue",
                                {"stop": END, "continue": "validate_action"})
    graph.add_edge("validate_action", "execute_tool")
    graph.add_edge("execute_tool", "record_observation")
    graph.add_conditional_edges("record_observation",
                                lambda state: "finished" if state["finished"] else "continue",
                                {"finished": END, "continue": "check_budget"})
    return graph.compile()


def run_request(request, model, store, data_dir: Path, *, max_steps=60, max_tool_calls=50,
                per_item_steps=12, save_failures=0, trace_path: Path | None = None):
    if not all(isinstance(n, int) and n > 0 for n in (max_steps, max_tool_calls, per_item_steps)):
        raise ValueError("执行上限必须为正整数")
    request = validate_request(request)
    store.register(request)
    started, start_clock = now(), time.monotonic()
    events, results, tentative = [], [], {}
    steps = tool_calls = 0
    stop_reasons = []

    def record(event):
        events.append(event)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical(event) + "\n")

    if trace_path:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("", encoding="utf-8")
    for item in request["items"]:
        tools = Tools(store, data_dir, request["request_id"], item, tentative, save_failures)
        context = {"request_id": request["request_id"], "item": item,
                   "current_draft": store.get(request["request_id"], item["material_id"])}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": canonical(context)}]
        graph = build_item_graph(model, tools, item, record, max_steps=max_steps,
                                 max_tool_calls=max_tool_calls, per_item_steps=per_item_steps)
        state = graph.invoke({"messages": messages, "steps": steps,
                              "tool_calls": tool_calls, "item_steps": 0,
                              "stop_reason": None, "finished": False},
                             config={"recursion_limit": max(100, per_item_steps * 6 + 20)})
        steps, tool_calls = state["steps"], state["tool_calls"]
        if state.get("stop_reason"):
            stop_reasons.append(state["stop_reason"])
        if tools.finished and tools.outcome == "incomplete":
            stop_reasons.append({"material_id": item["material_id"], "reason": "model_declared_incomplete"})
        results.append(tools.result())
    status = "incomplete" if stop_reasons else ("partial_failure" if any(r["outcome"] == "failed" for r in results) else "completed")
    output = {"request_id": request["request_id"], "run_status": status,
              "mode": model.mode, "model": model.name,
              "mock_boundary": "离线 Mock 仅验证流程，不证明真实模型决策效果" if model.mode == "mock" else None,
              "started_at": started, "finished_at": now(), "elapsed_seconds": round(time.monotonic() - start_clock, 3),
              "steps": steps, "tool_calls": tool_calls,
              "limits": {"max_steps": max_steps, "max_tool_calls": max_tool_calls, "per_item_steps": per_item_steps},
              "stop_reasons": stop_reasons, "items": results, "published": False}
    return output, events
