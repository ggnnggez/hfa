# Harness Optimization Framework

这份文档把一个核心问题形式化：

> 如何把 Hermes 中的 harness 机制看作可组合、可调参的控制系统，并针对不同任务选择合适的配置，使 agent 在质量、时间、token 成本和风险之间达到更优平衡。

这里沿用前一份文档中的三类控制：

- `Observation Shaping`
- `Action Gating`
- `Transition Control`

## 1. 问题定义

我们可以把一次 agent 运行看成：

- 输入：任务 `x`
- 决策：选择一组 harness 配置 `h`
- 输出：执行 rollout 后得到结果 `y`

其中：

```text
h = (o, a, t)

o ∈ Observation Shaping 配置空间
a ∈ Action Gating 配置空间
t ∈ Transition Control 配置空间
```

目标不是单纯找到一个“全局最优 harness”，而是：

> 对不同类型的任务，学习或设计一个 task-conditioned harness policy。

也就是：

```text
task features -> harness config -> rollout -> outcome
```

## 2. 为什么不能只说“动作空间约束”

如果只把 harness 看作对动作空间的约束，只能覆盖 `Action Gating`。

但 Hermes 实际上同时控制三件事：

1. `Observation Shaping`
   控制 agent 在当前轮看到什么
2. `Action Gating`
   控制 agent 当前能做什么
3. `Transition Control`
   控制动作执行后系统如何推进、重试、终止和清理

因此更准确的说法是：

> Hermes 的 harness 是对 agent runtime 的三层控制系统，而不只是对动作空间的裁剪。

## 3. 三类控制作为配置集合

可以把三类控制看作三个集合，每个具体机制是集合中的元素。

### 3.1 Observation Shaping

示例元素：

- memory prefetch
- context compression
- `pre_llm_call` context injection
- session context prompt injection
- tool schema shaping
- prefill messages

这类元素主要改变模型看到的状态表示。

### 3.2 Action Gating

示例元素：

- toolsets 选择
- `pre_tool_call` block policy
- approval policy
- clarify policy
- loop-owned tools 的开放范围
- delegation 是否允许

这类元素主要裁剪 `A(s)`，即给定状态下 agent 可执行的动作集合。

### 3.3 Transition Control

示例元素：

- iteration budget
- turn budget
- retry policy
- fallback policy
- cleanup policy
- timeout policy
- session finalize/reset policy
- step reporting / streaming strategy

这类元素主要控制 rollout 的深度、资源消耗、状态转移和终止条件。

## 4. 更合理的优化对象

比起“找一个最优元素组合”，更合理的优化对象是：

> 一个由元素选择、参数设置和交互约束共同构成的 harness 配置。

可以写成：

```text
h = {
  selected_elements,
  per_element_parameters,
  interaction_constraints
}
```

所以问题不是纯粹的子集选择问题，而是一个混合优化问题：

- 离散决策：哪些元素启用
- 连续或有序决策：元素参数如何设
- 结构决策：某些元素是否允许同时生效

## 5. 目标函数应该如何定义

你提到的目标包括：

- 完成时间
- 完成质量
- token 消耗

这是对的，但我建议初期不要立刻压成一个单一标量，而是先保留多目标结构。

### 5.1 推荐的基础指标

至少跟踪四类指标：

- `Q`: 任务完成质量
- `L`: 完成时间或 wall-clock latency
- `C`: token 成本
- `R`: 风险或失败率

其中：

#### 质量 `Q`

可以进一步拆成：

- 是否完成任务
- 正确性
- 完整性
- 是否需要人工纠偏
- 是否产生错误副作用

对代码任务，还可以包含：

- 测试是否通过
- 回归是否出现
- 修改是否符合预期范围

#### 时间 `L`

可以包含：

- 总 wall-clock time
- API 调用时间
- tool 执行时间
- 重试或 fallback 导致的额外延迟

#### 成本 `C`

可以包含：

- input tokens
- output tokens
- reasoning tokens
- 估算 API 成本
- tool 调用带来的外部成本

#### 风险 `R`

可以包含：

- error rate
- stuck rate
- unsafe action rate
- approval denial rate
- retry / fallback rate

### 5.2 三种目标定义方式

#### 方式 A: Pareto 优化

```text
maximize Q
minimize L
minimize C
minimize R
```

优点：

- 不需要预先假设权重
- 能直接看到 tradeoff frontier

适合：

- 还不确定业务偏好
- 想先理解系统行为

#### 方式 B: 加权效用函数

```text
U = wq * Q - wl * L - wc * C - wr * R
```

优点：

- 方便排序和自动搜索

问题：

- 权重很难一开始就设对
- 容易把本质上不同的成本强行线性相加

#### 方式 C: 约束优化

```text
maximize Q
subject to:
  L <= L_max
  C <= C_max
  R <= R_max
```

我更推荐把这作为工程上的主路线，因为很多场景里：

- 风险不能被 token 成本抵消
- 质量不能轻易被时间抵消
- 有些预算是硬约束，不是软偏好

## 6. 任务应该先分族，再优化

不同任务的最优 harness 结构通常不同，因此不应该直接对“所有任务”共同优化。

更合理的是先定义 task family。

例如：

- 信息检索型任务
- 代码理解型任务
- 代码修复型任务
- 多文件工程改造型任务
- 高风险终端执行型任务
- 长上下文分析型任务
- 高歧义澄清型任务

原因是不同 family 对三类控制的依赖结构不同：

- 检索型任务更依赖 `Observation Shaping`
- 高风险执行型任务更依赖 `Action Gating`
- 长链路任务更依赖 `Transition Control`

## 7. 把每类控制拆成可调 knob

### 7.1 Observation Shaping knobs

- 是否启用 memory prefetch
- prefetch top-k
- context compression threshold
- compression target ratio
- 是否注入 session context
- prefill message 数量
- tool schema 是否收窄

### 7.2 Action Gating knobs

- enabled toolsets
- 是否允许 delegate
- approval 模式
- `pre_tool_call` block 严格度
- clarify 触发阈值
- 是否禁用高风险工具

### 7.3 Transition Control knobs

- `max_iterations`
- turn budget
- tool result budget
- retry 次数
- fallback 触发条件
- timeout
- cleanup 粒度
- 并发工具执行策略

这些 knob 构成实际的配置搜索空间。

## 8. 元素之间的组合关系

这些元素不是独立的，它们之间通常有三类关系。

### 8.1 互补

两个元素一起启用时效果更好。

例如：

- memory prefetch + 更窄的 tool schema
- context compression + 更大的 iteration budget
- clarify + 严格 approval

### 8.2 替代

两个元素都在抑制同一类错误，其中一个足够强时另一个边际收益下降。

例如：

- 强 session context 注入 和 强 memory prefetch
- 更窄 toolsets 和 更强 `pre_tool_call` block

### 8.3 冲突

一个元素会削弱另一个元素的收益。

例如：

- 过强的 context compression 可能削弱 memory prefetch 的价值
- 过严的 action gating 会让更大的 iteration budget 失去意义
- 过宽的工具暴露会放大 retry/fallback 成本

所以真正需要优化的不是单一元素，而是：

> 元素组合结构 + 参数设置 + 元素之间的交互关系。

## 9. 一个更合适的研究目标

与其追求“单一全局最优配置”，更值得优化的是：

> 针对任务特征，自动选择合适 harness 配置的 routing policy。

也就是：

```text
f(task_features) = harness_config
```

例如：

- 短、明确、低风险任务
  - 弱 observation shaping
  - 宽动作集
  - 小 budget

- 长、复杂、代码库级任务
  - 强 observation shaping
  - 中等动作集
  - 大 budget
  - 开启 context compression

- 高风险任务
  - 强 action gating
  - clarify 更积极
  - approval 更严格
  - 禁掉部分高风险动作

这本质上是一个 meta-controller 问题，而不是单次 prompt 调优问题。

## 10. 一个可执行的推进路径

### 第一阶段：建立评估框架

1. 定义 3 到 5 类 task family
2. 每类收集一批代表任务
3. 明确 `Q / L / C / R` 指标
4. 建立可重复执行的 benchmark 流程

### 第二阶段：缩小搜索空间

1. 列出高价值 harness 元素
2. 为每个元素定义少量关键参数
3. 去掉明显冗余或难以稳定测量的 knob

### 第三阶段：做组合实验

1. 单因素 ablation
2. 双因素交互实验
3. 小规模 factorial search
4. 找 Pareto frontier

### 第四阶段：形成经验规则

输出类似：

- 某类任务优先提升 observation shaping
- 某类任务优先收紧 action gating
- 某类任务优先扩大 transition budget

### 第五阶段：学习 routing policy

最终再考虑：

- 规则系统
- 简单分类器
- 上层 controller model

## 11. 一个简化的 formalization

可以先用下面这个形式化框架：

```text
给定任务 x
提取任务特征 φ(x)

选择 harness 配置 h
h = (o, a, t)

执行 rollout:
y = Agent(x; h)

测量结果:
M(y) = (Q, L, C, R)

优化目标:
maximize Q
subject to C <= budget
subject to R <= threshold
then minimize L
```

如果后面确实需要单分数，可以再定义：

```text
U = alpha * Q - beta * L - gamma * C - delta * R
```

但这一步应当发生在指标定义和行为理解稳定之后，而不是一开始。

## 12. 结论

这件事最好的理解方式不是：

> 给三个集合选一个最优子集

而是：

> 对不同 task family，设计或学习一个 harness 配置策略，在质量、时间、成本和风险之间做多目标优化。

所以一个更完整的问题表述是：

> 给定任务特征，如何选择 Observation Shaping、Action Gating 和 Transition Control 的元素组合及其参数，使 agent 在给定预算和风险约束下达到更高质量和更低代价的完成效果。

