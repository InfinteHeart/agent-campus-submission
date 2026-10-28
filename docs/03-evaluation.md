# 测试、实际结果与限制

## 当前版本与执行方式

- 日期：2026-10-28，Asia/Shanghai。
- 环境：Linux，Python 3.11.15，LangGraph 1.2.18。
- 自动测试：.venv/bin/python -m unittest discover -s tests -v。
- 离线矩阵：.venv/bin/python run.py demo --mode mock --out runtime/新的空目录。
- 真实必验点：.venv/bin/python run.py demo --mode live --cases normal,missing,risk,conflict,duplicate,save_failure --out runtime/新的空目录。
- 真实调用使用本地 .env 的 DeepSeek 密钥；Trace 记录实际返回的模型字段、响应 ID、工具动作及观察，不记录密钥或内部思维过程。

## 结果

[33 项自动测试](../examples/test-results.txt)全部通过。[离线矩阵](../examples/mock/report.json)为 15/15 组、18 次运行通过；Mock 只验证流程和确定性约束。[真实模型必验点矩阵](../examples/live/report.json)为 6/6 组、7 次运行通过。矩阵“通过”表示结果符合场景预期，不表示每次请求都成功排期。

| 验收点 | 实际结果与证据 |
| --- | --- |
| 正常排期 | M01 审核通过，18:00 空闲并保存 ready 草稿；[输出](../examples/live/normal/output-1.json)、[Trace](../examples/live/normal/trace-1.jsonl) |
| 信息缺失 | M02 补查后仍缺授权资料，返回 needs_info，没有草稿；[输出](../examples/live/missing/output-1.json)、[Trace](../examples/live/missing/trace-1.jsonl) |
| 规则风险 | M03 命中 R001，返回 blocked，没有草稿；[输出](../examples/live/risk/output-1.json)、[Trace](../examples/live/risk/trace-1.jsonl) |
| 时段冲突 | 18:00 占用后模型查当日候选，建议 19:00；原硬性要求保留，scheduled_at 为空；[输出](../examples/live/conflict/output-1.json)、[Trace](../examples/live/conflict/trace-1.jsonl) |
| 重复提交 | 第二次返回 existing，草稿 ID 和版本未变，数据库只有一份；[第二次输出](../examples/live/duplicate/output-2.json)、[数据库快照](../examples/live/duplicate/database-2.json) |
| 保存失败 | 两次写入失败后返回 failed，run_status 为 partial_failure，草稿、占位和事件全部回滚；[Trace](../examples/live/save_failure/trace-1.jsonl)、[数据库快照](../examples/live/save_failure/database-1.json) |

两条真实 ReAct 路径的区别可由 Trace 直接核对：

1. 正常：read_material → check_rules(passed) → query_slots(requested，空闲) → save_draft → finish(draft_ready)。
2. 冲突：read_material → check_rules(passed) → query_slots(requested，已占用) → 模型选择 query_slots(day) → save_draft(19:00) → finish(needs_confirmation)。

Trace 的 graph_node 字段标出记录动作的 LangGraph 节点。模型选择业务工具；图的条件边只控制预算、错误和结束，不把 19:00 写死为答案。

## 额外边界验证

- [授权状态未知但证明编号非空](../examples/live/rights-unverified/verification.json)：当前 LangGraph 真实运行返回 needs_info 和 RIGHTS_UNVERIFIED，未保存草稿；[输入](../examples/live/rights-unverified/input.json)、[Trace](../examples/live/rights-unverified/trace.jsonl)、[输出](../examples/live/rights-unverified/output.json)。
- [18:00 与 19:00 同时占用](../examples/live/two-slots-occupied/verification.json)：模型观察当日结果后改为建议 17:00，仍待用户确认且无正式占位；[排期](../examples/live/two-slots-occupied/data/schedules.json)、[Trace](../examples/live/two-slots-occupied/trace.jsonl)、[输出](../examples/live/two-slots-occupied/output.json)。
- [离线完整矩阵](../examples/mock/report.json)另覆盖占用变化、补查后通过、人工核实、无时段、批量、确认及确认冲突、决策和工具调用上限。确认由验收夹具模拟用户调用独立入口，不代表实际业务批准。

## 失败与限制

保存失败用例的 partial_failure、上限用例的 incomplete 都是预期结果。模型服务失败不会自动切换为 Mock；请求会保留 model_error。全部输出均为 published=false。

真实运行只覆盖有限的合成样本；不能证明模型对所有素材、提示或未来模型版本都稳定。规则仅检查配置的文本和元数据，未分析真实图片、视频或法律风险。待确认提案不预占时段，用户确认时必须重新检查。当前只支持上海时区、固定一小时整点、同日时段和本机 CLI 信任边界。
