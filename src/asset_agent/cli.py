"""CLI commands; confirmation is accessible to the human, never to the model."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .agent import run_request
from .domain import DomainError, load_json
from .model import DeepSeekModel, MockModel, ModelError, load_env
from .storage import Store


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_model(mode):
    return MockModel() if mode == "mock" else DeepSeekModel()


def parser():
    root = argparse.ArgumentParser(description="素材审核与排期草稿 Agent（不提供发布能力）")
    root.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="运行一份请求")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--mode", choices=["live", "mock"], default="live")
    run.add_argument("--db", type=Path, default=Path("runtime/agent.sqlite"))
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--output", type=Path)
    run.add_argument("--trace", type=Path, default=Path("runtime/trace.jsonl"))
    run.add_argument("--max-steps", type=int, default=60)
    run.add_argument("--max-tool-calls", type=int, default=50)
    run.add_argument("--per-item-steps", type=int, default=12)
    run.add_argument("--simulate-save-failure", action="store_true", help="显式本地故障注入；不属于 Agent 工具")
    confirm = commands.add_parser("confirm", help="用户确认指定版本草稿的替代时段；仍不发布")
    confirm.add_argument("--db", type=Path, default=Path("runtime/agent.sqlite"))
    confirm.add_argument("--data-dir", type=Path, default=Path("data"))
    confirm.add_argument("--draft-id", required=True)
    confirm.add_argument("--revision", type=int, required=True)
    confirm.add_argument("--slot", required=True)
    confirm.add_argument("--output", type=Path)
    show = commands.add_parser("show", help="读取数据库中实际草稿")
    show.add_argument("--db", type=Path, default=Path("runtime/agent.sqlite"))
    show.add_argument("--draft-id", required=True)
    schedules = commands.add_parser("import-schedules", help="导入合成已有排期，替换 schedule 来源记录并使冲突草稿失效")
    schedules.add_argument("--db", type=Path, default=Path("runtime/agent.sqlite"))
    schedules.add_argument("--file", type=Path, required=True)
    demo = commands.add_parser("demo", help="隔离数据库运行并校验验收用例")
    demo.add_argument("--mode", choices=["live", "mock"], default="mock")
    demo.add_argument("--cases", default="all")
    demo.add_argument("--out", type=Path)
    demo.add_argument("--data-dir", type=Path, default=Path("data"))
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    load_env(args.env_file)
    store = None
    try:
        if args.command == "demo":
            from .demo import run_demo
            report = run_demo(args.mode, args.cases, args.out, args.data_dir)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 1
        if args.command == "run":
            request = load_json(args.input)
            model = make_model(args.mode)
            store = Store(args.db, load_json(args.data_dir / "schedules.json"))
            result, _ = run_request(request, model, store, args.data_dir,
                                   max_steps=args.max_steps, max_tool_calls=args.max_tool_calls,
                                   per_item_steps=args.per_item_steps,
                                   save_failures=-1 if args.simulate_save_failure else 0,
                                   trace_path=args.trace)
        else:
            if not args.db.is_file():
                raise DomainError("DB_NOT_FOUND", "数据库不存在，请先运行请求")
            store = Store(args.db)
            if args.command == "confirm":
                result = {"source": "user_cli", "published": False,
                          "draft": store.confirm(args.draft_id, args.revision, args.slot, args.data_dir)}
            elif args.command == "show":
                draft = store.get_id(args.draft_id)
                if not draft:
                    raise DomainError("DRAFT_NOT_FOUND", "草稿不存在")
                result = {"draft": draft, "published": False}
            else:
                schedules = load_json(args.file)
                store.replace_schedules(schedules)
                result = {"imported": len(schedules), "published": False}
        if getattr(args, "output", None):
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("run_status") in {"incomplete", "partial_failure"} else 0
    except (DomainError, ModelError, OSError, ValueError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, DomainError) else type(exc).__name__
        message = str(exc) if isinstance(exc, (DomainError, ModelError)) else "配置、输入或本地存储错误；请检查文件与参数"
        print(json.dumps({"error": code, "message": message, "published": False}, ensure_ascii=False))
        return 2
    finally:
        if store:
            store.close()
