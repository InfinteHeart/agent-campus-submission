# 方案与技术取舍

## 调用流程

`CLI → 校验请求 → 登记不可变请求指纹 → 为每项素材建立模型上下文 → DeepSeek 选择一个工具 → 参数/业务校验 → 执行 → 将真实观察交回模型 → 下一轮或 finish → 按工具状态组装结果`。

模型实现 `decide(messages, tool_schemas)`；真实模式通过 HTTPS POST `/chat/completions`，默认 model=`deepseek-flash`，thinking disabled，tool_choice required，parallel_tool_calls false。每轮只允许一个原生工具调用。工具调用 ID 原样用于后续 tool 消息，未在对话中插入伪造的模型动作。

依据：[官方工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[官方参数](https://api-docs.deepseek.com/api/create-chat-completion/)，2026-10-28 核对。不使用 Beta strict 功能，参数在本地显式校验。不采集 reasoning_content，只记录动作参数中一句公开事实依据。

## 动态决策

四个业务工具为 `read_material(section)`、`check_rules()`、`query_slots(scope)`、`save_draft(slot)`，另有 `finish(outcome)`。素材 ID、request_id 和身份由当前执行上下文绑定，不能由模型改写。

正常路径：读资料、审规则、查原时段、保存、结束。冲突路径：原时段返回占用后，模型选择扩大查询，再选可用候选、保存待确认提案、结束。缺资料路径：模型根据规则返回的 supplementary_available 选择补查，之后重新审核，仍缺失则请求信息。

`ranked_available_slots` 是工具按距离和等距离较晚优先计算的候选排序。它不自动触发查询或保存，也不赋予确认权限；动作和候选选择仍由模型生成。初次真实测试发现模型忽略等距离向后偏好，因此补充该字段，失败记录与改进见评估文档。

## 信任与状态

素材正文仅作为数据送入模型；系统提示明确不执行资料中的指令。工具白名单及严格参数集拒绝模型提供 approved 等越权字段。CLI 输入也不接受自带审批字段。

规则检查使用可信资料和内部规则；保存再次检查资料签名与规则结果。`finish` 的结果必须被实际工具观察支持。最终输出由代码组装，不采用模型自由文本作为保存成功的依据。

hard/preferred 均保留 requested_at，任何替代时间均 pending_confirmation，scheduled_at=null。用户独立执行 confirm，提供草稿 ID、精确版本和具体时段；确认前重新检查规则签名和时段。经确认的同一提案可以在后续重试中保留确认；资料变化则不复用旧确认。

## 草稿与事务

SQLite 表：requests（原请求指纹）、drafts（唯一请求/素材、版本和提案）、slot_claims（正式排期/ready 草稿占位）、draft_events（保存/确认/失效事件）。

- `UNIQUE(request_id, material_id)` 防重复，输入发生变化则拒绝复用请求 ID。
- `PRIMARY KEY(channel, slot)` 防占位重复；`BEGIN IMMEDIATE` 内完成检查与写入，两个并发进程不能同时获得同一槽位。
- 同一草稿查询排期时排除自己的占位；不会因重试与自己冲突。
- 原样保存返回 existing；外部状态变化更新同一 ID、递增版本。
- 失败在事务内回滚草稿、占位与事件。调用失败不意味着删除历史成功记录；输出明确本次保存结果。
- 待确认草稿不占正式时段，同批次候选由临时集合排他，跨请求提案在确认时仲裁。
- 导入变更排期会使冲突 ready 草稿失效，保留事件；该管理能力不暴露给模型。

SQLite 事务依据：[Python sqlite3](https://docs.python.org/3/library/sqlite3.html)、[SQLite UPSERT](https://www.sqlite.org/lang_upsert.html)。

## 上限与失败

默认每请求 60 次模型决策/50 次工具调用，每项 12 轮；finish、无效动作和失败动作均计数。最多两次保存尝试。模型调用超时默认 45 秒。达到上限输出 incomplete 和具体 stop_reason，不抹掉已经成功保存的状态。

模型不可用或响应无效时不切换 Mock，运行记录保留 model_error。离线 Mock 仅用于流程验证。业务工具异常返回结构化错误，模型选择重试或失败结束。

## 取舍

采用 Python 标准库和原生循环，运行/测试无第三方依赖，也无框架职责与版本需要额外解释。单 Agent 和 CLI 足以展示核心循环；业务工具本地实现便于可控故障注入与复现。代价是没有框架的持久会话、复杂调度和分布式能力，均非本题要求。
