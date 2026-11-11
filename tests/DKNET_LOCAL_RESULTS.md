# DKNet 本地仓库真实审查与人工复核

日期：2026-10-25（北京时间）。本次使用已克隆到 `E:\des\folder\项目\DKNet` 的代码，调用真实 DeepSeek 模型，没有使用离线回放。

## 1. 这次审查的是什么

本地仓库只有一次初始提交，工作区干净：`ec79fbe23002412586589f04267a26985338eeb0`。因此本次明确选择 **空快照 → 初始提交**，将首次导入的代码视为新增内容。这不是一次历史修复对比，也不是先前 20 个合成案例的重跑。

新增命令行 Git 读取入口，直接从固定提交的 blob 读取文本，不切换分支、不修改文件、不读取未提交的代码。已加载 15 个代码、说明、依赖和示例数据文件，共 65,224 字节；`.gitignore` 与 `LICENSE` 不在本次审查文本类型白名单，未发送模型。实际程序未运行 DKNet，没有安装或执行其训练依赖。

本轮要求优先报告最多三项能说明触发条件的功能风险，因此不能视为穷尽式审查。

## 2. 实际运行结果

| 项目 | 结果 |
| --- | --- |
| 模型 | deepseek-flash，真实调用 |
| 传输 | Beta 严格函数调用 + Pydantic |
| 任务状态 | completed |
| 模型调用 | 7 次 |
| 模型耗时 | 26.08 秒，不含后续复核 |
| 主动读取文件 | model.py、data.py、train.py、training.py、evaluate.py、geometry.py |
| 结构或证据反馈 | 0 次 |
| 原始报告问题数 | 3 条 |
| 输入 / 输出 token | 257,345 / 1,566 |
| 复核结果 | 前两项有受控复现支持，第三项只部分支持，需要进一步验证 |

原始模型输出和事件没有为人工结论而改写：

- 原始审查报告（本机原始材料：`results/local-review-results/dknet-initial-20261025/REPORT.md`）
- 完整任务与调用记录（本机原始材料：`results/local-review-results/dknet-initial-20261025/report.json`）
- 独立复核结果（本机原始材料：`results/local-review-results/dknet-initial-20261025/verification.json`）

## 3. 三条问题逐项复核

### 3.1 无标注图像无法读取时，无界递归重试

位置：`dknet/data.py:278–280`，`UnlabeledDataset.__getitem__`。

```python
if image_bgr is None:
    fallback_index = np.random.randint(0, len(self.data))
    return self.__getitem__(fallback_index)
```

**成立的条件**：开始实际采样无标注数据后，所有可选图像都无法读取，或随机重试持续选择不可读样本。单个坏文件不一定导致失败，因为可能随机找到有效文件；原始报告的触发条件需要这样收窄。

**复核方法**：从固定提交的 AST 提取原始方法和 `resolve_path`，不修改方法体；用受控 I/O 替身令 `imread` 始终返回 None，数据表只有一个索引，因此每次只能重试同一个不可读样本。

**观察结果**：抛出 `RecursionError`，确认重试没有次数上限。没有读取实际图片、导入完整 DKNet 或运行训练；这是原方法缺失图像分支的受控复现。

**建议**：直接抛出包含路径的异常，或通过有限次数的循环重试，耗尽后明确报错。

### 3.2 无标注样本不足一批时，空加载器使训练失败

位置：`train.py:177–191` 的无标注加载器构造，以及 `dknet/training.py:36–43` 的 `infinite_loader`。

训练代码只检查 `unlabeled_count > 0`，但无标注 DataLoader 使用 `drop_last=True`。默认无标注批大小为 `24 - 8 = 16`；如果只有 1～15 个样本，不能组成完整一批。执行到需要无标注数据的训练步骤时，会向 `infinite_loader` 请求一个不存在的批次。

```python
while True:
    try:
        yield next(iterator)
    except StopIteration:
        iterator = iter(loader)
        yield next(iterator)
```

**复核方法**：从固定提交提取未修改的 `infinite_loader`，给它一个空可迭代对象；另给非空列表作为对照。

**观察结果**：调用者实际看到 `RuntimeError: generator raised StopIteration`，其原因异常为 `StopIteration`。原始模型说“直接向上传播 StopIteration”，异常类型表述不准确，但空迭代器无法恢复的问题成立。非空对照能够输出 `[1, 2, 1, 2]`。

没有执行真实 Torch DataLoader；样本数、批大小与 `drop_last` 的条件通过源码核对，辅助函数的空迭代行为通过原函数复现。默认配置下还需训练确实执行到取无标注批次的阶段，不能说程序一启动就必然失败。

**建议**：检查 `len(unlabeled_loader)`，批次数为 0 时明确提示或跳过半监督步骤；同时让辅助函数对空迭代器给出明确错误。不要只判断数据表行数非零。

### 3.3 评估配置与 checkpoint 不一致时，未检查加载结果

位置：`evaluate.py:139`。

```python
model.load_state_dict(state_dict, strict=False)
```

**能确认的事实**：调用没有检查返回的 `missing_keys`、`unexpected_keys`。PyTorch 官方接口提供这些信息，可用于核对实际加载情况。[PyTorch load_state_dict 文档](https://docs.pytorch.org/docs/2.14/generated/torch.nn.Module.html#torch.nn.Module.load_state_dict)

**原始解释的问题**：不能把所有配置不一致都描述成“对应参数保持随机初始化”。例如，本仓库的 BN→GN 替换保持同一个子模块名称，仿射 `weight`、`bias` 的名称不因此全部改变；归一化行为与缓冲区差异，应与参数缺失区分。KCA/WKG 模块增减、不同方向的配置变化也需要分别核对。

**当前判断**：这是值得检查的配置一致性风险，但本次没有 Torch、checkpoint 和实际加载实验，只能判为“部分支持，待进一步验证”，不作为第三个完全复现的缺陷。

**建议**：检查不兼容键结果；将训练配置与 checkpoint 一起保存，并在评估时核对结构配置。要验证具体后果，需另做最小模型加载实验。

## 4. 这次说明了什么

本地 Git 代码已能进入原有 Agent 审查循环；模型会跨文件调查，且此次没有格式错误。前两项找到了可检验的运行边界问题，第三项暴露了“证据真实但解释过于概括”的局限。

不能将“3 条报告中 2 条有受控复现支持”直接作为准确率：这是一次无完整真值标签、限制报告数量的初审，也没有枚举未发现的问题。

输入量仍偏高：约 65 KB 的初次导入，在 7 轮完整历史和重复读文件下累计使用 257,345 输入 token。不能将它解释为单次文件大小；也没有估算金额。本次没有同时跑 diff 基线，因此不能声称 Agent 在本地仓库审查上比基线更准确或更省钱。

原始 summary 还混入了已经排除的假设，表达较冗长；结构化输出保证字段可解析，并未自动保证结论简洁、准确。人工复核结论与原始输出分别保存。

## 5. 实现与验证范围

- 新增 `git_input.py`：只读提交快照、文件清单和 blob 校验信息。
- 新增 `review_git.py`：初始导入或两个提交的命令行审查，自动读取已有密钥，保存结果，拒绝覆盖。
- 新增 `verify_dknet_review.py`：上述受控分支复现，不导入训练依赖。
- 新增 7 项本地 Git 测试：验证固定版本、脏工作区不被读取、查询工具衔接、排除项、非法提交、预算与删除文件限制。
- 本轮回归：Demo 54 项 + 原测试集 29 项，共 83 项通过。最初在受限环境创建临时 Git 测试仓库失败，换到允许该测试的执行环境后通过；没有改动 DKNet 来让测试通过。
- 两个提交的读取在独立临时仓库中验证；DKNet 只有一个提交，本次没有假称完成其真实历史修改对比。
- 网页仍选择内置示例，本次新增的是命令行入口，没有宣称网页已经支持选择本地仓库。
- 目前只接受有限类型的 UTF-8 普通文本；超过预算会停止。删除文件的证据锚定尚未支持，会明确拒绝。
- DKNet 分支、HEAD 和工作区保持原样，没有应用报告中的修复。

## 6. 复现命令

在 `code-review-demo` 目录运行，输出目录需使用新名称：

```powershell
.\.venv\Scripts\python.exe review_git.py --repo ..\DKNet --initial --head HEAD --output tests/results/local-review-results/dknet-next-run
```

已有两个提交的仓库可以显式选择前后版本：

```powershell
.\.venv\Scripts\python.exe review_git.py --repo "仓库路径" --base "修改前提交" --head "修改后提交" --output tests/results/local-review-results/new-comparison
```

重做受控复核时，也需使用新的输出文件：

```powershell
.\.venv\Scripts\python.exe -m tests.verify_dknet_review --repo ..\DKNet --commit ec79fbe23002412586589f04267a26985338eeb0 --output tests/results/local-review-results/verification-next.json
```
