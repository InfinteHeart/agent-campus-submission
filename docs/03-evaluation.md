# 测试、实际结果与限制

## 执行环境和命令

- 日期：2026-10-28，Asia/Shanghai。
- 环境：macOS，Python 3.14.6；项目最低要求 Python 3.11，尚未实际运行其他 Python 版本。
- 自动测试：`python3 -m unittest discover -s tests -v`。
- 离线矩阵：`python3 run.py demo --mode mock --out examples/mock`。
- 真实矩阵：`python3 run.py demo --mode live --out examples/live`。
- 用本地 `.env` 配置的密钥连接 DeepSeek 官方 API，返回模型字段为 `deepseek-flash`；没有使用 Mock 替代真实请求。

## 实际结果

32 项自动测试全部通过，其中验收矩阵测试包含 15 个场景子用例。原始输出见 [test-results.txt](../examples/test-results.txt)。

离线矩阵 15/15 通过、18 次运行，见 [Mock 报告](../examples/mock/report.json)。真实矩阵 15/15 通过、18 次运行，见 [live 报告](../examples/live/report.json)。

真实矩阵执行区间为 10:49:00–10:50:31，共 94 次模型决策/工具调用，按逐次输出累计耗时 90.748 秒。Trace 中 API usage.total_tokens 累计 167480（包含各轮历史上下文，不等于独立文本长度；未根据该数字推算账单）。

| 验收 | 预期 | 实际与证据 |
| --- | --- | --- |
| 正常排期 | 原要求空闲，审核通过后保存 | M01 按 18:00 保存，outcome=draft_ready；[输出](../examples/live/normal/output-1.json)、[Trace](../examples/live/normal/trace-1.jsonl) |
| 信息缺失 | 补查后仍缺资料，指出缺失字段 | M02 补查无证明，needs_info，没有草稿；[输出](../examples/live/missing/output-1.json)、[Trace](../examples/live/missing/trace-1.jsonl) |
| 规则风险 | 输出规则并阻断 | M03 命中 R001，blocked，没有草稿；[输出](../examples/live/risk/output-1.json)、[Trace](../examples/live/risk/trace-1.jsonl) |
| 时段冲突 | 不改硬要求，建议替代并待确认 | 18:00 占用后查询 day，建议 19:00，scheduled_at=null；[输出](../examples/live/conflict/output-1.json)、[Trace](../examples/live/conflict/trace-1.jsonl) |
| 重复提交 | 一份草稿，相同 ID/版本 | 两次调用第二次 existing，数据库仅 1 份；[第二次输出](../examples/live/duplicate/output-2.json)、[数据库快照](../examples/live/duplicate/database-2.json) |
| 保存失败 | 明确失败，全部回滚 | 两次写入失败后 failed，草稿/占位/事件均 0；[Trace](../examples/live/save_failure/trace-1.jsonl)、[数据库快照](../examples/live/save_failure/database-1.json) |
| 占用变化 | 再查新状态，更新原草稿 | 原 18 占用，后改为 19 占用；同请求改为原要求 18 可排期，ID 不变、版本递增；[第一次](../examples/live/changed_occupancy/output-1.json)、[第二次](../examples/live/changed_occupancy/output-2.json) |
| 补查可解决 | 模型选择补查，再检查规则 | M04 补查证明后通过并保存；[Trace](../examples/live/supplement/trace-1.jsonl) |
| 需人工核实 | 不自动豁免 | M05 needs_review，无草稿；[输出](../examples/live/manual_review/output-1.json) |
| 全部占用 | 无时段时不编造排期 | unavailable；[输出](../examples/live/no_slots/output-1.json) |
| 批量 | 两项提案互不冲突 | M01/M06 建议不同时间；[输出](../examples/live/batch/output-1.json) |
| 确认边界 | 只有用户入口可确认 | 夹具模拟用户确认 19:00，后续重复运行保留 confirmed；[确认回执](../examples/live/confirmation/user-confirmation.json)、[第二次 Trace](../examples/live/confirmation/trace-2.jsonl) |
| 确认前冲突 | 确认时再查，不占用冲突时间 | 返回 SLOT_CONFLICT；[确认尝试](../examples/live/confirmation_conflict/confirmation-attempt.json) |
| 执行上限 | 明确未完成 | step_limit/tool_call_limit，run_status=incomplete；[决策上限](../examples/live/step_limit/output-1.json)、[工具上限](../examples/live/tool_limit/output-1.json) |

上限用例的 incomplete 和保存失败用例的 partial_failure 是**预期结果**，不能将“矩阵通过”理解为这些请求都成功排期。所有输出 published=false。

## 两条核心真实决策路径

1. 正常：read_material → check_rules(passed) → query_slots(requested，18 空闲) → save_draft(18) → finish(draft_ready)。
2. 冲突：read_material → check_rules(passed) → query_slots(requested，18 占用) → 模型选择 query_slots(day) → save_draft(19，待确认) → finish(needs_confirmation)。

实际模型还在 M02/M04 的基础元数据中直接发现证明缺失，选择先补查再进行规则检查；并非必须重放 Mock 中的固定动作序列。真实 Trace 没有被改写成与 Mock 一样的步骤。

在 confirmation 第二次运行第 6 轮，模型误报 needs_confirmation；运行时发现保存回执已经 confirmed，返回 UNSUPPORTED_OUTCOME。模型第 7 轮改报 draft_ready。这体现了模型观察校验错误后调整下一步，也说明不能信任模型自由声称的最终状态。

## 发现过的失败与改进

1. 第一次请求被网络沙箱阻断，输出 incomplete/model_error，未假装调用成功，见 [网络失败记录](../examples/failures/network-blocked/normal/trace-1.jsonl)。获得网络执行权限后重新运行。
2. 首次真实冲突路径选择 17:00。它遵守待确认边界，但未满足项目定义的“等距离优先向后”偏好，验收断言失败，见 [初次输出](../examples/failures/initial-live/conflict/output-1.json)、[初次 Trace](../examples/failures/initial-live/conflict/trace-1.jsonl)。工具增加 ranked_available_slots，并明确排序含义；随后完整真实矩阵通过，新增候选排序自动测试。

## 自动测试的额外覆盖

拒绝 publish/confirm 等非白名单工具；拒绝伪造 approved 参数；拒绝跳过审核或未查询时段；拒绝虚报保存成功；请求 ID 不能覆盖变化要求；等价时区归一化；保存前规则/占用复查；确认绑定版本/时段/资料签名；两个并发连接抢同一时段仅一个成功；写入后故障全部回滚；瞬时失败有限重试；批次隔离；模型不可用不切换 Mock；非法动作消耗预算；HTTP 错误脱敏；畸形与多工具模型响应安全失败。

## 限制

- 真实矩阵是有限合成样本的一轮运行，不证明所有提示或长期模型行为都可靠；temperature=0 也不保证跨版本完全一致。
- 业务工具使用本地函数和合成资料；规则只覆盖配置的文本/元数据，未验证真实素材理解能力或实际平台发布。
- 确认由验收夹具模拟用户调用 CLI，证明边界机制，不代表真实内容团队已批准。
- SQLite 在本机事务内保证占位一致性；JSON 素材库无跨文件事务，生产环境需要统一版本化数据源。
- 不支持任意时长区间、跨日自动寻找、真实平台占用同步或多用户权限控制。
- 待确认提案不预占时段；其可用性只能截至查询时刻，确认时必须复查。
