# 提交信息

- 题目：素材审核与发布排期 Agent。
- 数字题号：题干未提供，请按主办方编号填写。
- 基座模型：DeepSeek Flash，API 模型名 `deepseek-flash`。
- 本地项目：当前仓库根目录。
- GitHub URL：https://github.com/edward200112/agent-campus-submission
- 最终 commit SHA：以 `git rev-parse HEAD` 的实际结果及最终交付消息为准。
- 仓库可见性：已通过 GitHub 连接器核实为 public，评审无需额外读取授权。

代码、合成资料、测试、离线与真实运行材料均在同一仓库。`.env` 和运行数据库被忽略，不能提交密钥。

评审先阅读 README、docs/02-solution.md 和 docs/03-evaluation.md；当前版本使用 LangGraph 1.2.18，先按 README 安装依赖。实际验证命令：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python run.py demo --mode mock --out runtime/reviewer-offline
.venv/bin/python run.py demo --mode live --cases normal,conflict --out runtime/reviewer-live
git rev-parse HEAD
```
