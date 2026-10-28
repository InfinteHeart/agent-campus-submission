# 素材审核与发布排期 Agent

基座模型：**DeepSeek Flash (`deepseek-flash`)**。通过原生工具调用实现“模型选择动作 → 执行工具 → 观察结果 → 再次决策”。运行入口为 CLI，业务资料全部合成，**仅保存草稿，没有发布能力**。

## 评审入口

- [需求与成功标准](docs/01-requirements.md)
- [调用流程、动态决策与技术取舍](docs/02-solution.md)
- [实际测试与验收证据](docs/03-evaluation.md)
- [AI 使用说明](docs/04-ai-usage.md)
- [复盘、用时与限制](docs/05-retrospective.md)
- [真实 DeepSeek 验收报告](examples/live/report.json)
- [正常路径 Trace](examples/live/normal/trace-1.jsonl)、[冲突路径 Trace](examples/live/conflict/trace-1.jsonl)
- [离线验收报告](examples/mock/report.json)、[自动测试结果](examples/test-results.txt)

`completed` 表示请求中的每项素材都已经得到有证据支持的处理结果；结果可以是阻断或待补资料，**不代表所有素材均可排期**。`partial_failure` 表示至少一项工具操作失败；`incomplete` 表示上限、模型服务错误或模型主动报告未完成。

## 环境与安装

Python **3.11 或更高版本**。运行和测试均仅依赖标准库，无需下载模型、安装框架或启动数据库服务。

```bash
python3 --version
python3 run.py --help
```

`requirements.txt` 标明无需第三方运行依赖。`run.py` 是零安装入口；也提供 `pyproject.toml` 供需要安装包的环境使用。以下命令均在仓库根目录执行。

## 离线运行（无需密钥）

```bash
python3 run.py demo --mode mock --out runtime/review-mock
python3 -m unittest discover -s tests -v
```

演示器在隔离的临时数据库中运行 15 组场景，自动断言结果，并保存输入、实际输出、Trace 与数据库快照。输出目录必须为空；重复演示请换一个目录。

**Mock 是确定性测试替身，只验证流程与代码约束，不证明真实模型的决策效果。不会在真实模型失败时自动切换到 Mock。**

## 接入 DeepSeek Flash

复制 `.env.example` 为 `.env`，在本地填写：

```dotenv
DEEPSEEK_API_KEY=你的真实密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT=45
```

环境变量优先于 `.env`。也支持用户将单行 `sk-` 格式密钥直接写入 `.env`，推荐使用上面的命名配置。无需将密钥发到聊天、写进代码或提交 Git；`.env` 和数据库默认被忽略。

```bash
# 两条最容易核对的真实模型路径
python3 run.py demo --mode live --cases normal,conflict --out runtime/review-live

# 全部验收场景，包含故障注入和模拟用户确认
python3 run.py demo --mode live --out runtime/review-live-all
```

真实模式会产生付费 API 调用。使用官方 Chat Completions API、原生 function tools、非思考模式；不采集模型内部思维过程。每个动作的 `reason` 是可公开的简短事实依据。调用实现见 `src/asset_agent/model.py`。

接入依据：[DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)、[官方模型名](https://api-docs.deepseek.com/quick_start/pricing/)，核对日期 2026-10-28。

## 单请求与持久化草稿

输入示例：

```json
{
  "request_id": "demo-conflict-001",
  "timezone": "Asia/Shanghai",
  "items": [{
    "material_id": "M01",
    "channel": "wechat",
    "requested_at": "2026-10-29T18:00:00+08:00",
    "requirement_type": "hard"
  }]
}
```

使用已提供的输入执行：

```bash
python3 run.py run --mode live \
  --input examples/live/conflict/input-1.json \
  --db runtime/review.sqlite \
  --output runtime/result.json --trace runtime/trace.jsonl
```

默认资料 `data/schedules.json` 中 18:00 已占用。模型应查询替代时段，并保存待确认提案。输出里的 `draft.record.id`、`revision` 和 `suggested_at` 用于后续确认：

```bash
# 用刚才实际输出的 ID、版本和时段替换占位值
python3 run.py show --db runtime/review.sqlite --draft-id D-实际ID
python3 run.py confirm --db runtime/review.sqlite \
  --draft-id D-实际ID --revision 1 \
  --slot 2026-10-29T19:00:00+08:00
```

`confirm` 是人主动执行的 CLI 命令，不暴露给模型。确认会重新检查素材/规则版本与占用情况，只将草稿转为可排期状态，仍不发布。仓库演示中的确认由验收夹具模拟用户输入，文件中明确标注来源。

同一请求再次运行会复用草稿 ID；输入不变且状态不变时返回 `existing`，不新增版本。相同 `request_id` 改变用户要求会被拒绝，应使用新请求 ID。外部占用变化时会重新查询并更新原草稿，不返回未经复查的旧建议。

## 更换占用时段与故障验证

创建一个排期 JSON，例如将 19:00 设为占用后，导入同一数据库：

```bash
python3 run.py import-schedules --db runtime/review.sqlite \
  --file examples/live/changed_occupancy/schedules-after.json

# 随后重复执行上面的同一 run 命令，观察原草稿 ID 与新版本
```

导入仅替换 `schedule` 来源记录；与新排期冲突的已有可执行草稿会被标为 `stale` 并释放占位，审计事件保留。这是本地合成排期管理入口，不是 Agent 工具。

```bash
python3 run.py run --mode mock \
  --input examples/mock/normal/input-1.json \
  --db runtime/failure.sqlite --simulate-save-failure \
  --output runtime/failure.json --trace runtime/failure-trace.jsonl
```

该故障发生在事务已经写入草稿、占位和事件之后、提交之前，用于证明全部回滚。失败命令返回非零退出码，这是预期行为。

## 状态与边界

- 审核：`passed / needs_info / blocked / needs_review / not_checked`。
- 素材结果：`draft_ready / needs_confirmation / needs_info / blocked / needs_review / unavailable / failed / incomplete`。
- 保存：`saved / existing / failed / not_attempted`，以工具实际回执为准。
- 原始要求 `requested_at` 永不被建议时间覆盖；所有替代时间均需确认，包括 `preferred`。
- 待确认提案不占用正式时段；同批次会避免重复建议。跨请求建议可能重叠，最终确认时重新检查。
- 已按原要求保存或经用户确认的草稿占用时段；事务内复查和数据库唯一约束防止同时占用。
- 默认每请求最多 60 轮/50 次工具调用，每项最多 12 轮；`finish` 也计入工具调用。`--max-steps`、`--max-tool-calls` 和 `--per-item-steps` 可降低上限。
- 仅支持上海时区、固定一小时整点、同一日期 09:00–21:00 的时段（21:00 不含），每请求 1–5 项不同素材。时间比较会规范化时区。
- CLI 是本机用户信任边界，尚无多用户身份认证。规则是合成文本/元数据规则，不包含完整图像、视频或法律审核。

## 提交

题目：素材审核与发布排期 Agent。题干未给出数字题号，提交时填写主办方编号。

GitHub 地址和最终 commit SHA 应由实际推送结果提供；当前仓库的交付状态见 `SUBMISSION.md`。私有仓库需向评审账号授予读取权限。
