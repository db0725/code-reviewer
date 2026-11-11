# 代码阅读指南

## 当前网页仓库入口（2026-11-02）

候选复核接入后，`review_repository()` 是两阶段入口：先为复核预留共享预算，调用 `detect_repository()` 发现，再由 `verification.verify_candidates()` 检查候选。原候选、三项关键判断、支持证据、反证和不确定状态分别保留。取证方法与边界见 [候选复核设计](CANDIDATE_VERIFICATION.md)。

本地 Git 任务现在优先阅读 `managed_review.py` 的 `review_repository()`。它先调用 `retrieval.review_plan()`，再逐批调查、校验并合并。每批使用独立模型实例与历史，`BoundedTools` 负责受限读取与 BM25/AST 检索，`EvidenceLedger` 保存真正展示过的行。新引擎始终列出代码文件、仅作上下文的文档和未完成范围。

`git_input.blob_stream()` 负责有边界的批量 Git 对象读取；没有切分支或运行仓库代码。用量中实际 token 来自模型 `usage`，字节预检只是调用前的保守估算，不能当成实际 token 展示。

下面介绍的 `review.run_review()` 保留为原版小样例和显式对照入口。两种引擎共享模型适配、Schema、资源策略和证据原文检查。网页回放仍使用原来的固定脚本。路线与边界见 [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md)。

建议先理解一次审查如何完成，再看模型接口的兼容细节。各入口文件已补充中文注释，重点解释边界和设计原因。

## 1. 先看整体流程

```text
网页选择变更
  → server.py 接收请求，保存任务并启动后台线程
  → review.py 创建消息历史：提示词 + diff + 文件清单 + 项目说明
  → 模型返回一个动作
      ├─ 工具动作：校验 → 查询内存快照 → 结果加入历史 → 再调用模型
      └─ final：校验问题结构、行号和代码原文 → 生成结果
  → server.py 通过 SSE 推送事件，网页展示报告并提供下载
```

这是显式 Agent 循环。程序没有提前规定先读哪个文件、再搜索哪个函数，模型通过动作选择调查路线；程序负责权限、结构校验、预算和退出条件。

## 2. 从 `review.py` 的 `run_review()` 开始

这是最重要的函数，可以暂时跳过前面的网络适配代码。

- `example`：本次审查输入，包含 `before` 和 `after` 两份文件字典。
- `history`：每轮发给模型的消息历史，工具结果会追加到这里。
- `model(history, deadline)`：调用一次模型，返回一个动作的 JSON。
- `emit(kind, data)`：发布进度；网页服务把它变成事件，命令行把它存成记录。
- `finish(...)`：统一生成完成、失败或达到限制的结果，保留资源记录和使用量。

一轮调用依次做：检查时限、请求模型、解析动作、校验动作、执行工具、回填结果。模型提交 `final` 时不会继续执行工具，而是进入报告校验。默认上限 50 次、总时限 300 秒，单次请求最长 60 秒；重复动作和连续错误可能更早结束。

`completed` 表示交付了通过校验的最终报告。`limited`、`failed` 均表示审查未完成，即使问题列表为空，也不能理解为没有缺陷。

## 3. 看模型动作和报告的约束

`schemas.py` 定义两类内容：四种工具动作，以及最终报告。`action` 决定这一轮应该使用哪种结构；Pydantic 禁止多余字段和隐式类型转换。

远端通过严格函数调用约束输出，本机再检查一次。`review_step` 只是传递动作的协议外壳，真正读取代码的是 `CodeTools`。模型不能直接执行 Python、写文件或运行命令。

`review.py` 的 `check_evidence()` 检查引用是否出现在目标行，`validate_findings()` 检查问题锚点并去重。这些检查可以拦住错误位置和编造的代码原文，不能证明模型解释的逻辑缺陷一定成立。

## 4. 看代码从哪里来

内置示例由 `examples.py` 提供。`get_example()` 返回副本；`diff_rows()` 分别计算旧行号和新行号，用于网页展示与定位。预置答案不放进模型输入。

本地仓库由 `git_input.py` 提供。`resolve_commit()` 固定完整提交编号；`snapshot()` 先筛文件再读取 Git 内容；`load_git_input()` 生成同样的前后快照结构。它不切分支、不读未提交内容、不运行仓库代码。文件类型、单文件大小、总大小和文件数都有边界。

`resource_policy.py` 保存项目说明、资源声明与排除规则；`CodeTools.read_file()` 在查询前应用这些策略。不可用路径按「版本＋路径」记住，重复请求直接返回记录。用户声明数据未提供属于验证限制，本身不应被报告成代码错误。

## 5. 最后看网页与服务如何配合

`server.py` 中的 `Job` 保存一次任务，`ReviewServer` 管理任务和固定预览。开始任务时后台线程调用 `run_review()`，因此网页不必等待模型完成才收到响应。

`Job.emit()` 给事件编号并唤醒等待者；`Handler.stream_events()` 把事件逐个推送给网页。SSE 是单向进度通道，断线重连时可以从已有事件编号继续。

`web/app.js` 中的 `state` 保存页面状态。按 `previewGit()` → `start()` → `connect()` → `renderReport()` 阅读即可跟完主流程。提交或说明修改时，`invalidatePreview()` 禁止继续使用旧预览。刷新后通过保存的任务编号恢复服务内原任务；服务重启后记录会清空。

`replay.py` 支持无密钥时的网页离线演示，是保留的产品功能；它不参与真实模型决策，也不会在真实请求失败后自动启用。

## 6. 哪些文件先不用看

`tests/` 中的回归检查、结构探针、评估脚本与历史记录可以在理解主流程后再读。真实服务不导入这个目录。`DeepSeekModel` 中保留的 `legacy` 和实验性 `responses` 分支用于复现既有对比实验，网页默认只使用严格函数调用，阅读第一遍可跳过其他分支。

启动仍使用根目录 `start.cmd`。测试命令和目录迁移说明见 [tests/README.md](../tests/README.md)，资源设置细节见 [RESOURCE_POLICY.md](RESOURCE_POLICY.md)。
