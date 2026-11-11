# 测试与验证

本目录集中保存主项目的回归检查、评估脚本、验证文档和本机实验材料。正常启动服务不依赖本目录。下面的命令均在 `code-review-demo` 根目录运行。

## 目录

| 内容 | 用途 |
| --- | --- |
| `test_*.py` | Python 单元与本地 HTTP/SSE 回归检查 |
| `test_web.cjs` | Node DOM 页面状态检查，不代替浏览器布局验证 |
| `ui_visual_check.cjs` | 本机隔离Chrome渲染、键盘、实际回放和受控状态检查，不调用模型 |
| `UI_REDESIGN_RESULTS.md` | 前端改版前后布局、截图及验证限制 |
| `BENCHMARK_INITIAL_RESULTS.md`、`BENCHMARK_STRUCTURED_RESULTS.md` | 独立基准仓库的结果说明副本；原始输出与基准源码未一并发布 |
| `evaluate.py` | 三个内置案例的批量回放或真实模型评估 |
| `probe_structured.py` | 真实模型结构兼容探针，不衡量缺陷识别能力 |
| `verify_dknet_review.py` | 固定 DKNet 函数的受控复核，不执行训练 |
| `VALIDATION.md` | 分阶段验证记录 |
| `DKNET_LOCAL_RESULTS.md`、`WEB_GIT_VALIDATION.md` | 本地仓库与网页接入的实测边界 |
| `FUNASR_RESULTS.md` | 扩大读取上限后，FunASR 最近提交的真实审查用量 |
| `CONTEXT_ENGINE_RESULTS.md` | 分批引擎的实测、失败记录、用量与人工反证 |
| `INDEPENDENT_EVALUATION.md` | 新增12案例的冻结、独立行为验收与复核判别口径 |
| `VERIFICATION_RESULTS.md` | 候选复核、开发失败与首次保留组的实际结果 |
| `evaluate_independent.py` | 给定候选对照与端到端评测，标签留在评测端 |
| `verify_saved_candidate.py` | 单独复核历史候选，不重跑完整仓库 |
| `results/` | 原始 JSON、SSE、Markdown 报告与截图，不提交 |

## 离线回归检查

```powershell
.\.venv\Scripts\python.exe -m unittest -v
node --test tests/test_web.cjs
```

Python 使用 `tests` 包自动发现测试；也可只运行一个模块，例如 `-m unittest tests.test_resource_policy -v`。检查只使用固定响应、本机模拟 HTTP 服务和临时 Git 仓库，不调用 DeepSeek。

需要观察固定回放时：

```powershell
.\.venv\Scripts\python.exe -m tests.evaluate --mode replay --output tests/results/new-replay.json
```

## 真实接口评估（会消耗模型调用）

```powershell
.\.venv\Scripts\python.exe -m tests.evaluate --mode real --output tests/results/new-real.json
.\.venv\Scripts\python.exe -m tests.evaluate --engine managed --mode real --output tests/results/new-managed.json
.\.venv\Scripts\python.exe -m tests.probe_structured --output tests/results/new-probe.json
```

评估脚本用 `-m tests.模块名` 启动，使其正确导入主目录的运行代码。密钥仍从根目录的本机 `.env` 自动加载。请为每次实验使用新的输出文件，保留真实失败，不用回放替代。

DKNet 受控复核与正常仓库审查是两个入口：前者为 `-m tests.verify_dknet_review`，后者仍为根目录 `review_git.py`。复核参数见 [本地实测记录](DKNET_LOCAL_RESULTS.md)。相邻 `../../review-agent-bench` 是独立的冻结基准 Git 仓库，目录位置、冻结案例和历史结果保持原样。

## 本次整理的迁移规则

- 根目录 `test_*.py`、`test_web.cjs` 与三个评估/复核脚本移入本目录。
- `evaluation-*.json`、`structured-probe*.json`、`preview.png` 移入 `results/`。
- `local-review-results/` 原样移入 `results/local-review-results/`。
- 三份验证/实测文档移入本目录，资源使用说明移入 `../docs/`。

迁移时逐文件比较 SHA-256，34 个文件内容均保持一致；之后仅修改脚本导入、输出默认位置、测试路径和文档链接，未改写历史实验 JSON、事件或报告内容。旧记录中保存的当时路径属于历史元数据，按上述规则找到新位置即可。
