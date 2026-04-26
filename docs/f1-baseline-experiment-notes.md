# F1 Baseline Experiment Notes

日期：2026-04-25

范围：

- Benchmark family: `F1_code_qa`
- Runner: `hermes_direct`
- Config: `benchmark/configs/baseline.yaml`
- Output: `benchmark/results/f1_baseline.jsonl`
- Model/provider: `kimi-k2.6` / `kimi-coding`

## 1. 结果摘要

本次实验已经形成一条真实可运行的评估链路：

```text
task -> HermesDirectRunner -> AIAgent -> tools -> trajectory -> oracle -> Q/L/C/R metrics
```

当前结果：

```text
tasks: 10
pass: 10/10
stop_reason: completed
runner/runtime errors: 0
retry/fallback/budget/stuck: 0
```

这说明当前最小 benchmark 闭环已经成立，且 `F1_code_qa` 在当前配置下可以稳定产出可用指标。

## 2. 指标观察

### 2.1 质量 Q

`F1_code_qa` 本轮全部通过。

这说明 baseline 对“只读代码库事实问答”有效，但不能直接外推到更复杂任务族。F1 的任务主要是查找源码中的事实，例如：

- `pyproject.toml` 中的版本号
- `run_agent.py` 中的 `IterationBudget`
- `_AGENT_LOOP_TOOLS`
- approval decision 字符串
- context engine 默认配置
- 顶层 release 文件数量

当前更准确的结论是：

> 在当前模型、当前 config、单 seed 条件下，HermesDirectRunner 能让 F1 code QA 全部通过，并稳定采集 Q/L/C/R 指标。

### 2.2 时间 L

单题 wall-clock latency 大致在 `7s-34s`。

较慢任务：

- `f1_07_approval_decisions`
  - latency 约 `34s`
  - API calls: `5`
  - input tokens 约 `10k+`

这说明复杂一点的代码定位任务会触发多轮搜索/读文件，时间和 token 成本都会上升。

### 2.3 成本 C

input tokens 差异较大，从几百到一万以上。

代表性现象：

- 简单文件数量问题只需要较低 token。
- 涉及源码定位和解释的任务会读较大文件，input tokens 明显上升。
- F1 理论上属于低风险、低复杂度任务，但如果工具选择和路径策略不够精确，仍然会产生不小的 token 成本。

这说明后续优化可以重点关注：

- 工具集收窄
- 文件读取范围控制
- 搜索策略
- prompt 对“只输出答案”的约束

### 2.4 风险 R

本轮没有出现：

- retry
- fallback
- iteration budget hit
- turn budget hit
- stuck
- runner error

这说明当前 F1 baseline 的运行稳定性可以作为后续 ablation 的起点。

## 3. 关键修复：runner 工作目录一致性

此前 `f1_10_release_count` 失败，模型最终回答为 `0`。

失败原因不是 oracle 错误，也不是模型完全不会做，而是 runner 工作目录不一致：

- file tools 会读到 `./hermes_v-0-10-0/...`
- terminal 命令实际运行在外层 `/home/nan/c_project/hfa`
- 因此外层目录下执行 `ls RELEASE_v*.md` 会得到 `0`

修复方式：

- 在 `HermesDirectRunner` 中同时设置 `TERMINAL_CWD`
- 并在任务运行期间临时 `chdir(repo_path)`

修复后：

```text
f1_10_release_count final_message: 9
pass: true
```

这个问题说明：

> workspace/sandbox/cwd 不只是运行细节，而是 Transition Control 的一部分。它会直接影响 agent 的观察、动作结果和 oracle 结论。

## 4. stdout 噪音定位

之前终端中会出现类似：

```json
{"success":false,"data":{"reason":"Title must be multi-word (prose-as-title)"},"warnings":["Title must be multi-word (prose-as-title)"]}
```

为定位这类噪音，`HermesDirectRunner` 已增加阶段级 stdout/stderr 捕获。

当前捕获到的稳定输出主要来自：

```text
stage: run_conversation
stream: stdout
message:
Vault: 0 notes
tool progress lines
```

这些输出现在会进入 JSONL 的 `events`，例如：

```json
{
  "type": "captured_output",
  "stage": "run_conversation",
  "stream": "stdout",
  "message": "Vault: 0 notes\n  ┊ 🔎 find ..."
}
```

当前判断：

- 这些输出不影响 metrics。
- 聚合分析时应默认忽略 `captured_output`。
- 如果后续再次出现 `Title must be multi-word`，可以通过 `events.stage` 判断它来自 `agent_init`、`run_conversation` 还是 `agent_close`。

## 5. 当前 baseline 的边界

这次结果可以作为 F1 的 reference baseline，但还不能算稳定结论。

原因：

- 只跑了 1 个 seed。
- 只有 10 个任务。
- 任务主要是确定性代码事实问答。
- 模型存在随机性，此前同一任务曾出现过错误回答。
- 尚未与其他 harness 配置做对比。

因此当前结果应该作为：

> 第一条真实数据链路和初始 reference point。

不应该过度解释为：

> 当前 harness 配置已经最优。

## 6. 下一步建议

### 6.1 多 seed 稳定性

先跑：

```text
seeds_per_task = 3
```

目标：

- 验证 `F1_code_qa` 是否持续 10/10。
- 观察此前不稳定的任务是否仍有波动。
- 得到 pass rate、latency、token 的均值和方差。

### 6.2 结果聚合脚本

需要一个聚合脚本读取 JSONL，按 `(family, config)` 汇总：

- pass rate
- 平均/中位 latency
- 平均/中位 input/output tokens
- 平均 api calls
- retry/fallback/budget/stuck 命中率

这会让后续 ablation 不再靠手工读 JSONL。

### 6.3 第一组 ablation：Action Gating

F1 是只读代码问答，适合先测试 toolset 收窄对质量和成本的影响。

建议比较：

- baseline: `terminal + file`
- `file_only`
- `terminal_only`

关注问题：

- `file_only` 是否能保持质量，同时降低风险和 token？
- `terminal_only` 是否更快，但更容易路径错误或输出不稳定？
- `terminal + file` 是否提供了质量冗余，但成本更高？

### 6.4 后续再扩展到 F4

F4 是高风险终端执行型任务，适合测试 Action Gating 和 approval 策略。

但在跑 F4 前，需要先完成 sandbox/workspace 隔离，否则 setup 和危险命令 probe 会污染外层工作区。

## 7. 当前结论

本轮实验最重要的成果不是 `10/10` 本身，而是：

1. `HermesDirectRunner` 已经能驱动真实 Hermes agent。
2. Q/L/C/R 指标能稳定落到 JSONL。
3. stdout 噪音可以被捕获和定位。
4. 工作目录一致性问题已经暴露并修复。
5. F1 可以作为后续 harness ablation 的低风险测试床。

