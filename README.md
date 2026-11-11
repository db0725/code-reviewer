# Code Review Agent · 本地审查工作台

Python 3.9+，标准库本地服务与原生网页，使用 Pydantic 约束模型动作。选择一组 Python 变更，观察 Agent 自主查阅相关代码，再查看包含代码证据的审查报告。

第一次阅读代码可从 [代码阅读指南](docs/CODE_GUIDE.md) 开始。自动检查与真实验证限制见 [测试目录](tests/README.md) 和 [验证记录](tests/VALIDATION.md)。

准备秋招面试、希望先不看代码理解项目，可先阅读 [面试学习手册](docs/INTERVIEW_STUDY_GUIDE.md)：包含基础知识、进阶机制、设计取舍、真实指标、常见追问和分层学习自测。

2026-11-11 工作台前端已调整为以代码与证据阅读为中心的布局，支持紧凑输入选择、预览后收起设置、键盘标签切换和窄屏阅读。设计依据与实际截图验证见 [界面改版记录](tests/UI_REDESIGN_RESULTS.md)。

阅读空间可自行调整：拖动两侧分隔线改变宽度，拖动工作区底边改变高度；标题旁可以收起说明，顶部可隐藏文件/审查栏或进入“专注代码”。Escape退出专注，“重置布局”恢复默认，尺寸偏好在本机保留。

新版本具体改了哪些机制、为什么影响token与完成情况，以及指标的解释边界，见 [新版本优化与指标解读](docs/OPTIMIZATION_DETAILS.md)。

项目截至当前的错误、原因、修复办法与实验效果见 [项目复盘与错误修复记录](docs/PROJECT_RETROSPECTIVE.md)。

2026-10-25 已接入本地 Git 网页选择、版本预览和真实审查，验证范围见 [网页接入记录](tests/WEB_GIT_VALIDATION.md)。此前命令行初审与复核见 [本地实测报告](tests/DKNET_LOCAL_RESULTS.md)。

2026-10-26 已直接扩大仓库读取预算，并对 FunASR 最近两个提交进行真实审查。50 次调用达到上限，累计输入约 1,134 万 token，审查未完成；配置、耗时与用量边界见 [FunASR 实测记录](tests/FUNASR_RESULTS.md)。

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

主目录只保留运行、启动和配置文件。`replay.py` 是网页的离线演示功能，仍属于运行代码；评估答案和测试脚本放在 `tests/`。相邻的 `review-agent-bench` 是独立的基准测试 Git 仓库，保留原位置。

本地 Git 默认使用分批引擎：每批独立上下文，按行读取、BM25片段检索和AST定义定位，保留批次交付与未完成范围。调用上限仍为50次、300秒，并增加600,000累计输入token的保守预检保护和64,000字节的请求预检保护。实际消耗以接口返回为准；缺失用量明确标为未知。第一轮优化、后续评测与工程化路线见 [项目路线](docs/PROJECT_ROADMAP.md)。

仓库默认流程现为“发现候选 → 取回调用与旧版证据 → 复核 → 展示支持、反证和待核实”。两阶段共用总预算，模型支持不等于运行证明；无法复核的候选不会丢弃。详见 [候选复核设计](docs/CANDIDATE_VERIFICATION.md) 和 [新增独立合成评测](tests/INDEPENDENT_EVALUATION.md)。

本轮新增保留组8例中，增强证据复核8/8匹配预期，局部证据条件2/8判别、4不确定、2被校验拒绝；端到端4个预置缺陷报告与验收匹配、4个正常改动无告警。历史FunASR候选保留为待核实。样本和失败边界见 [复核与新增评测结果](tests/VERIFICATION_RESULTS.md)，不能将这些数字表述为通用准确率。

2026-11-02 固定FunASR提交复测：8/8批交付，25次调用，输入192,774 token；较历史记录减少98.30%，审查耗时仍约五分钟。唯一候选未通过静态反证复核，不计为已确认缺陷。范围变化、首轮失败与结果限制见 [分批引擎实测](tests/CONTEXT_ENGINE_RESULTS.md)。

## 启动

Windows 双击 `start.cmd` 即可启动，不需要再次输入密钥。首次启动会创建项目专用 `.venv`，联网安装 `requirements.txt` 中锁定的 Pydantic 及其依赖；后续直接使用这个环境。服务自动读取项目目录中的本机 `.env` 配置。窗口保持打开，浏览器访问 **http://127.0.0.1:8765**。按 Ctrl+C 停止。

也可以在当前目录运行：

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe server.py
```

依赖安装与已有的 `agent-harness` 分开，新项目运行时不导入或依赖它。不要使用没有安装 Pydantic 的系统 Python 运行本项目。

### 真实模型

本机已有的 DeepSeek 密钥已配置到 `.env`，该文件被 `.gitignore` 排除，启动时自动读取。读取优先级为：显式参数 → 环境变量 → 本机 `.env` → 默认值。接口与模型默认 `https://api.deepseek.com`、`deepseek-flash`，可在启动终端覆盖：

```powershell
$env:OPENAI_BASE_URL = 'https://api.deepseek.com'
$env:OPENAI_MODEL = 'deepseek-flash'
.\start.ps1
```

`.env.example` 是可分享的配置模板；真实 `.env` 只保留在本机，按 `KEY=VALUE` 读取，支持包裹值的单引号或双引号，不做变量展开或命令执行。配置变更后重启服务。请勿将真实密钥写入代码、截图或提交记录。浏览器不接收密钥、接口地址和模型配置，服务日志不打印上游响应原文。项目复制到其他电脑后需要单独配置密钥。

真实审查默认使用 DeepSeek `/beta/chat/completions` 的严格函数调用，函数 `review_step` 显式开启 `strict=true`，关闭思考。参数的 JSON Schema 来源于 Pydantic 模型；响应仍由本机 Pydantic 严格验证后执行。其他兼容服务需要支持这一接口与参数，不会自动降级。[DeepSeek 严格函数调用文档](https://api-docs.deepseek.com/guides/tool_calls/)

保留 `DeepSeekModel(settings, protocol="legacy")` 供实验明确选择原 `/chat/completions` JSON Mode；网页默认使用结构化输出。

实验性的 `protocol="responses"` 仅用于复现中间试验。该通道在当前真实多轮测试中返回过非 JSON 工具标记，因此没有选为默认实现。

## 如何演示

1. 选择「会员结算逻辑调整」「用户查询接口迁移」或「购物车汇总函数重构」。
2. 点击「开始真实审查」，观察模型选择搜索、读文件和查定义的过程。
3. 在工具记录中展开返回结果；审查结束后查看问题的触发条件、影响及修复建议。
4. 点击问题位置或关联文件位置，跳转到修改后代码的证据行。
5. 下载 Markdown 报告。切换「执行轨迹」仍可回看完整记录；刷新页面可恢复当前服务内最近一次任务。

无密钥时可以明确选择「离线回放」。回放是固定脚本和预设结论，仅用于观察控制流程及界面，**不代表真实模型能力，也不会作为真实模式失败后的自动替代**。

### 审查本地 Git 仓库

左侧切换「本地仓库」，填写路径，点击「读取仓库」。选择两个提交，或为只有一个提交的仓库选择「首次导入」，再点击「预览代码变更」。确认提交编号和范围后，开始真实审查，现有轨迹、证据跳转和下载功能复用。

数据集无需导入：只读取已提交的代码和少量说明，不扫描未入库的数据目录；CSV、权重及数据目录里的非代码内容均排除。预览中可查看实际排除条目。版本改变后需重新预览；刷新任务时使用原快照恢复。浏览器自动点击验证受当前工具连接限制，详见网页接入记录。

预览前可展开「项目说明与资源设置」，填写背景说明，选择数据集与权重是否提供，并逐行添加排除路径。不可用资源会记录原因，相同版本路径再次请求时直接使用缓存；持续重复会停止并保留未完成记录。设置随预览固定，修改后需重新预览。声明「已提供」仍只做静态代码检查，报告标明数据与权重未经验证。使用方法、恢复规则与本轮真实试验限制见 [资源机制说明](docs/RESOURCE_POLICY.md)。

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

工具只读取示例的内存快照，不能访问宿主文件系统、写文件或执行代码。AST 定义检索不等于完整的类型分析或跨文件符号解析。源代码和注释在提示词中标记为不可信数据；这并不保证模型完全抵御提示注入。

### 输出稳定性

模型每次调用一次 `review_step`，参数为 `{"step":{...一个动作...}}`，不附加普通文本。这个函数只负责传输决策，实际读取代码仍由原有工具白名单执行。工具动作与最终报告通过 `action` 区分，禁止多余字段；只看 diff 的基线只允许最终报告。服务端接收结构、动作分支、字段类型及枚举约束，本地 Pydantic 继续检查非空文本、长度、正整数行号等限制，不做字符串转数字之类的隐式转换。

DeepSeek 严格模式不接受没有属性的对象，因此 `get_diff` 在传输中使用 `{"step":{"action":"get_diff"}}`，本地恢复为空参数；其他工具保留 `arguments`。`read_file.version` 在远端 Schema 中为必填。结构化返回不会跳过代码证据、路径和调用预算检查。额外说明、多个 JSON、拒绝响应、截断输出都会明确失败或反馈，不截取其中一段当作成功。

多轮请求保留模型生成的原生调用 ID，将实际工具结果配对为 `role=tool` 消息。多个并行调用全部拒绝，不擅自选择第一个；格式问题在三次连续失败和 50 轮预算内反馈，拒绝或截断则停止。严格模式与本地校验仍不能保证上游永不违约，实际稳定性以复测记录为准。

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

任务状态为 `running`、`completed`、`failed`、`limited`。最多保留 30 次任务，重启服务后清空；同一时间仅运行一个审查任务，不做跨重启任务恢复。

### 可靠性边界

- 最多 50 次模型调用，包含格式错误后的重试；单次最长 60 秒，任务总时限 5 分钟（300 秒）。次数和时间任一上限先达到即停止。
- 连续三次格式错误、连续三次相同动作或连续三次工具失败会停止。
- 相同版本的不可用资源不会重复查找；即使穿插其他动作，第三次请求同一不可用资源也会停止并保留记录。
- 超时、接口报错及输出不完整时不执行该动作，保留已有轨迹，不自动调用回放。
- `completed` 表示模型交付了通过结构校验的最终报告；`failed/limited` 不能解释为「没有问题」。
- Pydantic 验证结构后，另行确认文件、行号、证据原文及重复项，**不保证缺陷语义成立**。不执行测试，不修改代码，不承诺发现所有缺陷。
- 内置示例较小，只能作为演示和初始回归集，不能据此宣称通用代码审查准确率。
- 服务用于个人本地演示，不是多用户生产部署。端口占用可用 `.\start.ps1 -Port 8766` 切换。

## 验证与评估

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe -m tests.evaluate --mode replay --output tests/results/new-replay.json
node --test tests/test_web.cjs
```

以上均为离线检查，不调用真实模型。需要真实评估时，使用 `-m tests.evaluate --mode real --output tests/results/new-real.json`；结构探针使用 `-m tests.probe_structured --output tests/results/new-probe.json`。这些命令会请求真实接口，详细入口见 [tests/README.md](tests/README.md)。

测试包含 diff 映射、代码检索、AST 定位、路径边界、错误回填、证据纠错、去重、退出条件、HTTP/SSE、下载、上游接口适配以及真实模式失败不会回放。

`tests/probe_structured.py` 对真实接口检查四种工具动作、最终问题报告和只看 diff 的最终报告，共六种结构；只验证结构，不执行工具，也不评估缺陷识别能力。DKNet 的多轮开发集复测见 [基准复测记录](tests/BENCHMARK_STRUCTURED_RESULTS.md)。

批量真实评估自动读取本机 `.env`，并允许环境变量覆盖。缺少密钥时明确记录 `skipped`，不发送请求，退出码为 2。其他失败也以非零状态结束。结果包含独立验收依据、工具记录、调用轮数和模型返回的 token 使用量；缺陷正确与否需要人工复核，不自动把命中行号当成正确答案。不估算未经定价配置的费用。

三个独立验收点：会员折扣计算的单位一致性、用户查询未命中的返回值处理、整数金额契约下重构的行为保持。后续可以扩展独立测试集，比较「只看 diff」「按需获取上下文」「附加缺陷验证」等版本。

## DKNet 真实代码测试集

相邻目录 `../review-agent-bench` 包含固定版本的 DKNet 代码、20 个合成修改（10 个缺陷、10 个正常）及独立验收测试。它使用本项目的同一模型接口和审查循环，运行 Agent 与只看 diff 的对照实验。入口和边界见该目录的 README；这批案例不表示上游仓库已有这些缺陷。

## 本地 Git 命令行审查

读取已提交的固定版本，不读取或修改未提交的源文件。已有两个提交时，在项目目录运行：

```powershell
.\.venv\Scripts\python.exe review_git.py --repo "本地仓库路径" --base "旧提交" --head "新提交" --output tests/results/local-review-results/new-run
```

只有初始提交时，可以显式使用 `--initial --head HEAD`，将空快照与该提交比较；这属于新增代码初审，不是历史修复验证。密钥仍自动读取，结果包含原始事件、提交编号、文本覆盖范围及 Markdown 报告，同名目录禁止覆盖。

可追加 `--notes "数据与权重需自行准备" --dataset-status not_provided --weights-status not_provided --exclude "data/"`；`--exclude` 可重复，状态也可选 `provided` 或 `unknown`。网页与命令行使用相同的资源机制。

第一版仅加载 `.py/.md/.rst/.txt/.sh` 的 UTF-8 普通文件，跳过 `.env`、符号链接和不支持的类型；CSV 等数据文件、数据及训练产物目录的非 Python 文件默认排除。每个快照最多 300 个文件、总计 2,000,000 字节，单文件最多 256,000 字节，超限明确停止。预算常量集中在 `git_input.py`；这些是本地读取上限，不等于单次模型上下文大小。暂不支持删除文件的证据锚定。网页和命令行使用同一读取策略，均不执行项目代码。
