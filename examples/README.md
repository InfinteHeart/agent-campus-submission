# 当前 LangGraph 运行证据

本目录只保留当前 LangGraph 1.2.18 版本生成的样例。业务资料、工具与排期均为本地合成数据。

- [真实 DeepSeek 必验点报告](live/report.json)：6 组场景、7 次请求。
- [离线 Mock 报告](mock/report.json)：15 组场景、18 次请求。Mock 验证工作流与业务约束，不证明真实模型的决策效果。
- [33 项自动测试原始输出](test-results.txt)。
- [源码及证据 SHA-256 清单](evidence-manifest.json)。

| 必验点 | 真实运行 | 离线运行 |
| --- | --- | --- |
| 正常排期 | [live/normal](live/normal) | [mock/normal](mock/normal) |
| 信息缺失 | [live/missing](live/missing) | [mock/missing](mock/missing) |
| 规则风险 | [live/risk](live/risk) | [mock/risk](mock/risk) |
| 时段冲突 | [live/conflict](live/conflict) | [mock/conflict](mock/conflict) |
| 重复提交 | [live/duplicate](live/duplicate) | [mock/duplicate](mock/duplicate) |
| 保存失败 | [live/save_failure](live/save_failure) | [mock/save_failure](mock/save_failure) |

额外真实边界证据：[授权状态未知](live/rights-unverified/verification.json)和[18:00、19:00 同时占用](live/two-slots-occupied/verification.json)。离线矩阵另覆盖占用变化、补查后通过、人工核实、无时段、批量、确认及确认冲突、决策与工具调用上限。

演示场景包含输入、实际输出、Trace、数据库快照、初始排期和断言。重复提交或占用变化场景的第二次运行使用同一临时数据库。二进制数据库不提交，数据库快照由实际查询生成。真实补充场景另提供复现用的完整合成 data 目录。

Trace 的 graph_node 字段标识记录动作的图节点；provider 字段包含服务返回的模型名、响应 ID 和 token 用量。密钥、认证头及模型内部思维过程不在证据中。报告中的 output_dir 保留了生成时的 runtime 路径，文件已复制到本目录供评审查看。
