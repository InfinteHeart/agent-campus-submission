# AI 辅助内容、个人贡献与验证

## AI 使用范围

本项目使用 Codex 辅助需求拆解、合成资料设计、Python 代码、LangGraph 节点编排、DeepSeek HTTP 适配、SQLite 草稿事务、自动测试、真实模型运行、证据整理及文档。业务 Agent 在 live 模式下的逐轮工具选择由 DeepSeek Flash 返回；Codex 的项目开发与业务 Agent 的运行是两件不同的事。离线 Mock 仅用于流程测试，没有被写成真实模型决策。

提交者提供试卷要求，选择“素材审核与发布排期”题，要求将暂定的原生循环迁移为 LangGraph 工作流，并在本地配置 DeepSeek 密钥。技术决策中的“明确节点编排、使用 LangGraph”由提交者提出；五节点实现、授权规则修复及本轮测试代码由 Codex 完成。当前没有证据表明提交者手工编写或修改了代码，因此不将 AI 代码写成个人手写代码。

## 实际修改与验证

1. Codex 对审核规则做反例检查，发现“证明编号非空但授权状态未知”会被误判为通过。修改后只有授权状态明确为 cleared 才能通过；新增自动测试和[真实模型边界证据](../examples/live/rights-unverified/verification.json)。
2. Codex 根据提交者设计方案将控制流迁移为 LangGraph 1.2.18 的 StateGraph，包含 check_budget、model_decide、validate_action、execute_tool、record_observation 五个节点。工具、确认边界和 SQLite 事务继续由业务代码负责。当前[真实必验点报告](../examples/live/report.json)为 6/6，[离线报告](../examples/mock/report.json)为 15/15，[自动测试](../examples/test-results.txt)为 33 项通过。
3. 提交者在迁移前曾使用本地密钥亲自重跑“双时段占用”场景，提供输出并判断模型改选 17:00、未擅自落实改时。该个人验证发生在旧控制流版本，不列作当前 LangGraph 版本的运行证据。当前版本的[同场景证据](../examples/live/two-slots-occupied/verification.json)由 Codex 重新运行。
4. 提交者在当前 LangGraph 版本中亲自在终端执行 `.venv/bin/python run.py --help` 和 `.venv/bin/python run.py demo --mode live --cases normal,conflict --out runtime/my-langgraph-review`，查看报告及 normal、conflict 各自的输出和 Trace，并报告四个文件符合预期。[本次个人复核报告](../examples/personal-review/report.json)显示真实模型运行 2/2 通过：normal 为 5 步，conflict 为 6 步。提交者核对正常路径保存 18:00 草稿、冲突路径保留原要求并等待确认，以及 Trace 中的工具调用顺序。Codex 根据提交者反馈整理并保存这次运行证据；没有将此项写作提交者手工修改代码。
5. 提交者随后亲自在终端执行 `.venv/bin/python -m unittest discover -s tests -v`，将完整终端输出提供给 Codex；本次运行 33 项测试，耗时 3.845 秒，结果为 `OK`。这是当前 LangGraph 版本的个人自动测试复核，与前述 Codex 生成的[测试结果文件](../examples/test-results.txt)分开记录。

## 提交前个人核对

提交者已经完成上述两条 live 路径的亲自运行与输出、Trace 核对，并亲自运行 33 项自动测试。提交者估计亲自投入约 2 小时；尚未记录由提交者手工完成的代码修改，不能将 Codex 完成的代码修改写成个人操作。

本文件记录 AI 辅助范围和实际贡献，不提交聊天逐字稿、真实密钥、认证头或模型内部思维过程。
