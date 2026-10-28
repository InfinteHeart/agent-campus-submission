# 运行材料索引

`live/`：实际 DeepSeek Flash 决策，业务工具与数据为本地合成；`mock/`：离线流程验证，不能作为真实决策证据。两个目录均有 15 组场景、18 次运行及自动验收报告。

每个场景提供 `input-N.json`、`output-N.json`、`trace-N.jsonl`、`database-N.json`（运行后的实际数据库快照）、初始排期及 `verification.json`。重复和变更场景的 N=1、2 在同一数据库执行。演示数据库是临时数据库，不提交二进制数据库；数据库快照由查询实际表生成。

| 场景 | 真实运行 | 离线运行 |
| --- | --- | --- |
| 正常排期 | [live/normal](live/normal) | [mock/normal](mock/normal) |
| 缺资料且补查失败 | [live/missing](live/missing) | [mock/missing](mock/missing) |
| 规则阻断 | [live/risk](live/risk) | [mock/risk](mock/risk) |
| 时段冲突 | [live/conflict](live/conflict) | [mock/conflict](mock/conflict) |
| 重复提交 | [live/duplicate](live/duplicate) | [mock/duplicate](mock/duplicate) |
| 保存失败 | [live/save_failure](live/save_failure) | [mock/save_failure](mock/save_failure) |
| 交换占用并重复提交 | [live/changed_occupancy](live/changed_occupancy) | [mock/changed_occupancy](mock/changed_occupancy) |
| 补查后通过 | [live/supplement](live/supplement) | [mock/supplement](mock/supplement) |
| 待人工核实 | [live/manual_review](live/manual_review) | [mock/manual_review](mock/manual_review) |
| 当日无时段 | [live/no_slots](live/no_slots) | [mock/no_slots](mock/no_slots) |
| 同批次互斥建议 | [live/batch](live/batch) | [mock/batch](mock/batch) |
| 用户确认后重试 | [live/confirmation](live/confirmation) | [mock/confirmation](mock/confirmation) |
| 确认时发生冲突 | [live/confirmation_conflict](live/confirmation_conflict) | [mock/confirmation_conflict](mock/confirmation_conflict) |
| 模型决策上限 | [live/step_limit](live/step_limit) | [mock/step_limit](mock/step_limit) |
| 工具调用上限 | [live/tool_limit](live/tool_limit) | [mock/tool_limit](mock/tool_limit) |

`user-confirmation.json` 和 `confirmation-attempt.json` 的确认由验收夹具模拟人类调用 CLI，明确标记来源；不是模型获得了确认权限，也不是一次真实业务用户审批。

`test-results.txt` 为实际 `unittest` 输出。`failures/initial-live` 保留候选偏好未满足的初次真实运行；`failures/network-blocked` 保留首次沙箱网络失败。它们不计入最终通过率。

Trace 的 provider 字段包含实际返回模型名、响应 ID 和 token 用量，未记录密钥、认证请求头或内部思维过程。`evidence-manifest.json` 记录源码与证据文件 SHA-256，供核对文件完整性，不代表模型结果具有统计普遍性。
