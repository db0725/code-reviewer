# Code Review Agent · 本地审查工作台

选择一组 Python 代码变更，让 Agent 按需查阅相关代码，输出带代码证据的审查报告。本地服务使用 Python 3.9+ 标准库，网页使用原生 HTML、CSS 和 JavaScript，模型动作用 Pydantic 校验。

代码阅读从 [代码阅读指南](docs/CODE_GUIDE.md) 开始。测试入口、验证范围和限制见 [测试目录](tests/README.md) 与 [验证记录](tests/VALIDATION.md)。

秋招面试准备可参考 [面试学习手册](docs/INTERVIEW_STUDY_GUIDE.md)，先了解基础知识、实现机制、设计取舍和实验指标，再练习常见追问与自测。

2026-11-11 前端更新了输入选择、预览后收起设置、键盘标签切换和窄屏布局，设计依据与截图验证见 [界面改版记录](tests/UI_REDESIGN_RESULTS.md)。

拖动两侧分隔线可调宽，拖动工作区底边可调高。标题旁能收起说明，顶部能隐藏文件栏、审查栏或进入“专注代码”。按 Escape 退出专注，“重置布局”恢复默认；布局偏好保存在本机。

各项改动对 token 用量和审查完成情况的影响，以及指标的适用范围，见 [新版本优化与指标解读](docs/OPTIMIZATION_DETAILS.md)。

[项目复盘与错误修复记录](docs/PROJECT_RETROSPECTIVE.md) 保留了开发中遇到的问题、原因、修复办法和实验结果。

2026-10-25 接入本地 Git 网页选择、版本预览和真实审查，验证范围见 [网页接入记录](tests/WEB_GIT_VALIDATION.md)。此前的命令行初审与复核见 [本地实测报告](tests/DKNET_LOCAL_RESULTS.md)。

2026-10-26 扩大仓库读取预算后，审查了当时 FunASR 最近两个提交。累计输入约 1,134 万 token，50 次调用后达到上限，未完成审查。配置、耗时和用量说明见 [FunASR 实测记录](tests/FUNASR_RESULTS.md)。

## 目录

```text
code-review-demo/
├─ server.py             本地服务与实时事件
├─ review.py             模型请求、只读工具、Agent 循环、证据检查
├─ managed_review.py     仓库分批审查、预算控制与结果合并
├─ verification.py       候选复核、调用与状态反证、三类结论
├─ retrieval.py          变更计划、BM25/AST检索、证据台账
├─ schemas.py            Pydantic 动作与报告结构
├─ git_input.py          Git 提交快照与加载范围
├─ resource_policy.py    项目说明、资源状态、排除规则
├─ examples.py           网页内置示例
├─ replay.py             网页离线回放功能
├─ review_git.py         命令行审查入口
├─ web/                  网页 HTML、CSS、JavaScript
├─ docs/                 使用说明与代码阅读指南
├─ tests/                全部主项目测试、评估工具、验证记录
│  └─ results/           本机实测 JSON、报告、事件、截图（不提交）
├─ start.cmd / start.ps1  启动入口
├─ setup.ps1             依赖安装
└─ requirements.txt      依赖版本
```

主目录只放运行、启动和配置文件。`replay.py` 提供网页离线演示，属于运行代码；评估答案和测试脚本放在 `tests/`。相邻的 `review-agent-bench` 是独立的基准测试 Git 仓库，未迁入本项目。

本地 Git 默认分批审查，每批使用独立上下文，通过按行读取、BM25 片段检索和 AST 定义定位补充代码，并记录各批结果与未完成范围。在50次调用、300秒上限之外，增加了600,000累计输入token预算和64,000字节请求大小预检。预检采用保守估算，实际消耗按接口返回统计，缺失用量标为未知。第一轮优化及后续评测、工程化安排见 [项目路线](docs/PROJECT_ROADMAP.md)。

仓库审查流程为“发现候选 → 取回调用与旧版证据 → 复核 → 展示支持、反证和待核实”。发现与复核共用总预算，无法复核的候选也会保留。模型支持仍是静态判断，不代表已运行验证。详见 [候选复核设计](docs/CANDIDATE_VERIFICATION.md) 和 [新增独立合成评测](tests/INDEPENDENT_EVALUATION.md)。

新增保留组的8例中，增强证据复核8/8匹配预期；局部证据条件下2/8作出判别、4例不确定、2例被校验拒绝。端到端审查报告了4个与验收匹配的预置缺陷，4个正常改动无告警。历史 FunASR 候选仍待核实。样本范围与失败记录见 [复核与新增评测结果](tests/VERIFICATION_RESULTS.md)，这些数字不能作为通用准确率。

2026-11-02 对固定 FunASR 提交复测，8/8批交付，25次调用，输入192,774 token，比历史记录少98.30%，耗时仍约五分钟。唯一候选未获静态反证复核确认，不计为已确认缺陷。两次运行的范围有变化，首轮失败和结果限制见 [分批引擎实测](tests/CONTEXT_ENGINE_RESULTS.md)。

## 启动

Windows 双击 `start.cmd` 启动。首次会创建项目专用 `.venv`，联网安装 `requirements.txt` 中锁定的 Pydantic 及其依赖，后续复用该环境。服务自动读取项目目录中的本机 `.env` 配置，无需重复输入已配置的密钥。保持窗口打开，浏览器访问 **http://127.0.0.1:8765**；按 Ctrl+C 停止。

也可以在当前目录运行：

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe server.py
```

依赖环境与已有的 `agent-harness` 分开，运行时不导入或依赖它。请使用已安装 Pydantic 的项目环境，不要直接使用缺少依赖的系统 Python。

### 真实模型

本机的 DeepSeek 密钥已放在 `.env`，启动时自动读取；`.gitignore` 会排除该文件。配置优先级是：显式参数 → 环境变量 → 本机 `.env` → 默认值。接口和模型默认 `https://api.deepseek.com`、`deepseek-flash`，可在启动终端覆盖：

```powershell
$env:OPENAI_BASE_URL = 'https://api.deepseek.com'
$env:OPENAI_MODEL = 'deepseek-flash'
.\start.ps1
```

`.env.example` 是可分享的配置模板；真实 `.env` 只留在本机，按 `KEY=VALUE` 读取，支持单引号或双引号包裹的值，不展开变量或执行命令。修改配置后需重启服务，换电脑需重新配置密钥。密钥不要写入代码、截图或提交记录；浏览器不接收密钥、接口地址和模型配置，服务日志也不打印上游响应原文。

真实审查默认使用 DeepSeek `/beta/chat/completions` 严格函数调用，为 `review_step` 设置 `strict=true` 并关闭思考。JSON Schema 从 Pydantic 模型生成，响应在本机严格验证后才执行。其他兼容服务也需支持这些接口参数，程序不会自动降级。参见 [DeepSeek 严格函数调用文档](https://api-docs.deepseek.com/guides/tool_calls/)。

实验可通过 `DeepSeekModel(settings, protocol="legacy")` 选择原 `/chat/completions` JSON Mode，网页默认使用结构化输出。

`protocol="responses"` 仅用于复现中间试验：它在真实多轮测试中返回过非 JSON 工具标记，因此未作为默认实现。

## 如何演示

1. 选择「会员结算逻辑调整」「用户查询接口迁移」或「购物车汇总函数重构」。
2. 点击「开始真实审查」，查看模型的搜索、读文件和定义查找记录。
3. 展开工具记录查看返回内容，审查结束后查看问题的触发条件、影响和修复建议。
4. 点击问题位置或关联文件位置，跳转到修改后代码的证据行。
5. 下载 Markdown 报告，或切换「执行轨迹」回看完整记录。刷新页面可恢复当前服务内最近一次任务。

没有密钥时可选择「离线回放」，通过固定脚本和预设结论体验流程与界面。它不代表真实模型能力，也不会在真实审查失败后自动启用。

### 审查本地 Git 仓库

左侧切换「本地仓库」，填写路径并点击「读取仓库」。选择两个提交；只有一个提交时选择「首次导入」。点击「预览代码变更」，确认提交编号和范围后开始审查，同样支持执行轨迹、证据跳转和报告下载。

无需导入数据集。系统只读取已提交的代码和少量说明，不扫描未入库的数据目录；CSV、权重及数据目录中的非代码内容会被排除，预览中可查看条目。修改版本后需重新预览，刷新任务则恢复原快照。浏览器自动点击验证受当前工具连接限制，详见网页接入记录。

预览前可在「项目说明与资源设置」填写背景、声明数据集和权重状态，并逐行添加排除路径。设置随预览固定，修改后需重新预览。不可用资源会记录原因，相同版本和路径再次请求时使用缓存；持续重复会停止，保留未完成记录。即使声明「已提供」，审查仍只检查静态代码，报告会标明数据与权重未经验证。用法、恢复规则和真实试验限制见 [资源机制说明](docs/RESOURCE_POLICY.md)。

## 架构与代码阅读顺序

```text
网页选择示例 → POST 创建任务 → 后台线程启动审查
                                  ↓
                  diff + 文件清单 → 模型 JSON 决策
                                  ↓
                 校验动作 → 只读工具 → 结果回填
                                  ↖ 继续调查
                                  ↓ final
                   校验位置/原文 → 去重 → 报告
                                  ↓
                  SSE 实时事件 / 下载 Markdown
```

- `examples.py`：修改前后快照、diff 和新旧行号映射；不包含预置答案。
- `review.py`：模型适配、工具白名单、显式循环、格式反馈、结束条件、证据检查和报告。
- `schemas.py`：Pydantic 动作与报告模型、服务端兼容 Schema、严格解析和安全错误反馈。
- `resource_policy.py`：项目说明校验、资源分类、排除规则和验证范围报告。
- `server.py`：本地 HTTP 接口、任务管理和可续传 SSE；仅监听 127.0.0.1。
- `web/`：无构建步骤的网页，所有模型内容按文本渲染，不作为 HTML 执行。
- `replay.py`：独立的回放动作与答案，不进入真实模型上下文。
- `tests/evaluate.py`：独立验收依据和三组样例批量评估入口，不由服务导入。

### 工具

| 工具 | 输入 | 行为 |
| --- | --- | --- |
| `get_diff` | 无 | 获取本次 unified diff |
| `read_file` | `path`，可选 `version=before/after` | 获取带行号的文件内容 |
| `search_code` | `query` | 在修改后快照中按字面量搜索，最多 40 条 |
| `find_definition` | `symbol` | 用 Python AST 提取同名函数或类定义 |

工具只读内存快照，不能访问宿主文件系统、写文件或执行代码。AST 检索只提供定义线索，不做完整的类型分析或跨文件符号解析。提示词将源码和注释标为不可信数据，但不能保证模型完全抵御提示注入。

### 输出稳定性

模型每轮调用一次 `review_step`，参数为 `{"step":{...一个动作...}}`，不附加普通文本。函数负责传输决策，宿主通过工具白名单执行代码读取。工具动作与最终报告由 `action` 区分，不接受多余字段；只看 diff 的基线只允许最终报告。服务端约束结构、动作分支、字段类型和枚举，本地 Pydantic 再检查非空文本、长度和正整数行号，不将字符串隐式转成数字。

DeepSeek 严格模式不接受没有属性的对象，因此 `get_diff` 传输为 `{"step":{"action":"get_diff"}}`，本地再恢复空参数；其他工具保留 `arguments`。远端 Schema 要求填写 `read_file.version`。收到结构化响应后，仍需检查代码证据、路径和调用预算。额外说明、多个 JSON、拒绝或截断响应都会触发失败或反馈，不从中截取一段当作成功。

多轮请求保留模型返回的原生调用 ID，将工具结果配对为 `role=tool` 消息。模型一次返回多个并行调用时全部拒绝，不只取第一个。格式问题在连续三次失败和 50 轮预算内反馈纠正，拒绝或截断则停止。严格模式和本地校验无法保证上游始终遵约，稳定性以复测记录为准。

### 接口

| 接口 | 功能 |
| --- | --- |
| `GET /api/config` | 密钥是否配置、调用预算和时限；不返回密钥或接口配置 |
| `GET /api/examples` | 三组样例、文件及 diff 行号 |
| `POST /api/reviews` | 请求体 `{"example_id":"checkout","mode":"real"}`；mode 可为 `replay` |
| `GET /api/reviews/{id}` | 当前任务状态和结果 |
| `GET /api/reviews/{id}/events` | SSE，支持 `Last-Event-ID`，可重放该任务的已有事件 |
| `GET /api/reviews/{id}/report` | Markdown 附件 |
| `POST /api/git/inspect` | 本地仓库信息与最近 30 个提交 |
| `POST /api/git/preview` | 固定版本的代码预览与数据排除范围 |
| `POST /api/git/reviews` | 按预览编号开始真实审查 |
| `GET /api/reviews/{id}/input` | 任务原快照，用于刷新恢复 |

任务状态为 `running`、`completed`、`failed`、`limited`。同一时间只运行一个审查任务，内存最多保留 30 次任务，重启后清空，不支持跨重启恢复。

### 可靠性边界

- 最多 50 次模型调用，计入格式错误后的重试；单次最长 60 秒，总时限 5 分钟（300 秒）。先达到次数或时间上限即停止。
- 连续三次格式错误、连续三次相同动作或连续三次工具失败会停止。
- 相同版本的不可用资源不会重复查找；即使穿插其他动作，第三次请求同一不可用资源也会停止并保留记录。
- 超时、接口报错或输出不完整时拒绝执行该动作，保留已有轨迹，不自动切换回放。
- `completed` 表示模型交付了通过结构校验的最终报告；`failed/limited` 不能解释为「没有问题」。
- Pydantic 校验结构后，还会核对文件、行号、证据原文并去重，但不保证缺陷语义成立。审查不执行测试或修改代码，也不承诺发现所有缺陷。
- 内置示例只适合演示和初始回归，样本较小，不能用于宣称通用代码审查准确率。
- 服务供个人本地演示，不支持多用户生产部署。端口占用时可用 `.\start.ps1 -Port 8766` 切换。

## 验证与评估

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe -m tests.evaluate --mode replay --output tests/results/new-replay.json
node --test tests/test_web.cjs
```

以上检查不调用真实模型。真实评估使用 `-m tests.evaluate --mode real --output tests/results/new-real.json`，结构探针使用 `-m tests.probe_structured --output tests/results/new-probe.json`；这两项会请求真实接口，详细入口见 [tests/README.md](tests/README.md)。

测试包含 diff 映射、代码检索、AST 定位、路径边界、错误回填、证据纠错、去重、退出条件、HTTP/SSE、下载、上游接口适配以及真实模式失败不会回放。

`tests/probe_structured.py` 检查真实接口的四种工具动作、最终问题报告和只看 diff 的最终报告，共六种结构。它不执行工具，也不评估缺陷识别能力。DKNet 多轮开发集的复测见 [基准复测记录](tests/BENCHMARK_STRUCTURED_RESULTS.md)。

批量真实评估从本机 `.env` 读取配置，环境变量可覆盖。缺少密钥时记录 `skipped`，不发送请求，以退出码 2 结束；其他失败也返回非零状态。结果保留独立验收依据、工具记录、调用轮数和接口返回的 token 用量。报告需人工复核，命中行号不自动计为正确缺陷；未配置定价时不估算费用。

三个独立验收点分别是会员折扣的单位一致性、用户查询未命中的返回值处理，以及整数金额契约下重构前后行为是否一致。后续可扩展测试集，对比「只看 diff」「按需获取上下文」「附加缺陷验证」等版本。

## DKNet 真实代码测试集

相邻目录 `../review-agent-bench` 保存固定版本的 DKNet 代码、20 个合成修改（10 个缺陷、10 个正常）和独立验收测试，使用本项目相同的模型接口与审查循环，对比 Agent 和只看 diff 的结果。运行入口和适用范围见该目录 README。缺陷是为实验构造的，不表示上游仓库已有这些问题。

## 本地 Git 命令行审查

命令行读取已提交的固定版本，不读取或修改未提交文件。比较两个提交时，在项目目录运行：

```powershell
.\.venv\Scripts\python.exe review_git.py --repo "本地仓库路径" --base "旧提交" --head "新提交" --output tests/results/local-review-results/new-run
```

只有初始提交时，用 `--initial --head HEAD` 比较空快照和该提交，做新增代码初审，不能据此验证历史修复。密钥自动读取，结果包含原始事件、提交编号、文本覆盖范围和 Markdown 报告；同名输出目录禁止覆盖。

可追加 `--notes "数据与权重需自行准备" --dataset-status not_provided --weights-status not_provided --exclude "data/"`，其中 `--exclude` 可重复，资源状态也可选 `provided` 或 `unknown`。网页与命令行共用资源规则。

第一版只加载 `.py/.md/.rst/.txt/.sh` 的 UTF-8 普通文件，跳过 `.env`、符号链接及其他不支持的类型。CSV 等数据文件，以及数据和训练产物目录中的非 Python 文件默认排除。每个快照最多 300 个文件、合计 2,000,000 字节，单文件最多 256,000 字节，超限即停止。读取预算常量位于 `git_input.py`，它限制的是本地快照，不是单次模型上下文。暂不支持删除文件的证据锚定。网页和命令行共用读取规则，均不执行项目代码。
