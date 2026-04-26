# Hermes Harness Taxonomy

这份文档把 Hermes Agent 中与 harness 相关的机制，按三个维度重新归纳：

- `Observation Shaping`
- `Action Gating`
- `Transition Control`

这个分类比单纯说“动作空间约束”更准确，因为 Hermes 不只限制 agent 能做什么，还会塑造 agent 能看到什么，以及动作执行后系统如何转移。

## 总结

- `Observation Shaping`
  决定 agent 在当前轮看到的上下文、工具描述和会话环境。
- `Action Gating`
  决定 agent 当前可选的动作集合，以及某个动作能否真正落地执行。
- `Transition Control`
  决定一次调用、一次工具执行、一个 turn、一个 session 如何推进、重试、终止和清理。

## Observation Shaping

| 名称 | 影响 agent 行为 | 关键文件和行号 |
|---|---|---|
| `get_tool_definitions` | 按 toolset 过滤并重写工具 schema，让模型只看到当前会话真实可用的工具；还会基于可用工具重建 `execute_code` 和 `browser_navigate` 的描述，避免模型幻觉调用不存在的工具。 | [model_tools.py](/home/nan/c_project/hfa/hermes_v-0-10-0/model_tools.py:196), [model_tools.py](/home/nan/c_project/hfa/hermes_v-0-10-0/model_tools.py:263) |
| `pre_llm_call` 插件上下文注入 | 每轮把插件返回的 `context` 追加到当前用户消息，而不是 system prompt。这样既能给模型补充额外上下文，又不破坏 system prompt 前缀缓存。 | [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8448), [hermes_cli/plugins.py](/home/nan/c_project/hfa/hermes_v-0-10-0/hermes_cli/plugins.py:632) |
| Context engine 压缩 / 更新 | 在预检阈值前后压缩消息历史，并在 API 返回 usage 后更新 token 状态；切模型或 fallback 时同步更新 context window，直接改变后续轮次模型能看到的上下文形态。 | [agent/context_engine.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/context_engine.py:12), [agent/context_engine.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/context_engine.py:129), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8380), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:9319), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:6033) |
| Memory prefetch / provider tools | memory manager 会汇总 provider 的 system prompt block、turn 前 `prefetch()`、turn 后 `sync_turn()` 和 `queue_prefetch()`；预取结果在 API 调用时被封装成 fenced memory block 注入当前用户消息。 | [agent/memory_manager.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/memory_manager.py:157), [agent/memory_manager.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/memory_manager.py:178), [agent/memory_provider.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/memory_provider.py:83), [agent/memory_provider.py](/home/nan/c_project/hfa/hermes_v-0-10-0/agent/memory_provider.py:92), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:11264) |
| Session context prompt 注入 | 网关把 platform、chat、thread、user 等会话信息拼成 system prompt 段，并可做 PII redaction；这改变了模型对“当前会话是谁、在哪、属于哪个线程”的认知。 | [gateway/session.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/session.py:186), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:3476), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:3492) |

### 说明

这一类机制不一定直接禁止动作，但会改变模型看到的状态表示，所以会间接改变策略分布。

## Action Gating

| 名称 | 影响 agent 行为 | 关键文件和行号 |
|---|---|---|
| `pre_tool_call` 阻断钩子 | 工具真正 dispatch 前先跑 `pre_tool_call`；只要 hook 返回 `{"action": "block", "message": "..."}` 就直接把工具调用变成错误返回。`skip_pre_tool_call_hook=True` 只保留观察，不再重复阻断。 | [hermes_cli/plugins.py](/home/nan/c_project/hfa/hermes_v-0-10-0/hermes_cli/plugins.py:743), [model_tools.py](/home/nan/c_project/hfa/hermes_v-0-10-0/model_tools.py:454), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:7269), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:7670) |
| 危险命令 approval 栈 | `terminal_tool` 先做模式匹配和安全检查；命中时会返回 `blocked` 或 `approval_required`。真正的用户决策由 CLI 或 gateway 的 approval callback 给出 `once`、`session`、`always`、`deny`。 | [tools/approval.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/approval.py:75), [tools/approval.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/approval.py:233), [tools/terminal_tool.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/terminal_tool.py:1286), [hermes_cli/callbacks.py](/home/nan/c_project/hfa/hermes_v-0-10-0/hermes_cli/callbacks.py:186), [acp_adapter/permissions.py](/home/nan/c_project/hfa/hermes_v-0-10-0/acp_adapter/permissions.py:26) |
| `clarify` 工具 / 回调 | 当任务歧义或需要用户决策时，`clarify_tool` 会同步等待 UI callback 的答案；超时后返回“让模型自行判断”的文本，从而阻止模型继续盲走。 | [tools/clarify_tool.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/clarify_tool.py:23), [tools/clarify_tool.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/clarify_tool.py:87), [hermes_cli/callbacks.py](/home/nan/c_project/hfa/hermes_v-0-10-0/hermes_cli/callbacks.py:18) |
| loop-owned tools | `todo`、`memory`、`session_search`、`delegate_task` 不允许直接走通用 registry dispatch，必须由 agent loop 接手；这把这些工具从“普通工具”提升成 loop 级别的专属动作。 | [model_tools.py](/home/nan/c_project/hfa/hermes_v-0-10-0/model_tools.py:322), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:7281) |

### 说明

这一类最接近狭义的“动作空间约束”。它决定：

- 当前暴露给模型的动作有哪些
- 某个动作是否被策略层阻止
- 某些动作是否必须走特权执行路径

## Transition Control

| 名称 | 影响 agent 行为 | 关键文件和行号 |
|---|---|---|
| `step_callback` | 每次迭代把上一步的 tool 名称和结果送到 gateway 或 ACP hooks，用于 UI、外部事件流和 progress tracking；它不改模型输入，但决定外部系统如何感知每一步。 | [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8561), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:8407), [acp_adapter/events.py](/home/nan/c_project/hfa/hermes_v-0-10-0/acp_adapter/events.py:117), [acp_adapter/server.py](/home/nan/c_project/hfa/hermes_v-0-10-0/acp_adapter/server.py:397) |
| `pre_api_request` / `post_api_request` | 围绕每次模型请求发生命周期 hook，只传 request 或 response 元数据；核心 agent 代码不读取返回值，所以这是纯调度和观测边界。 | [hermes_cli/plugins.py](/home/nan/c_project/hfa/hermes_v-0-10-0/hermes_cli/plugins.py:54), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8824), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:10331) |
| Budget | `IterationBudget` 限制回合数，`tool_result_storage` 的 `turn_budget` 再限制单回合工具结果总字符数；超预算时会提前停循环、回退、截断或落盘。 | [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:170), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8534), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8555), [tools/budget_config.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/budget_config.py:23), [tools/tool_result_storage.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/tool_result_storage.py:175) |
| fallback / retry | 遇到空响应、4xx、transport error、length/truncation 等时，先按本地 retry 处理，再按 fallback chain 切 provider 或 model；成功后再恢复 primary runtime。 | [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:5911), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:6135), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:8767), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:9439), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:10081), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:4699) |
| cleanup | `AIAgent.close()` 和 CLI exit 的清理路径是幂等的，会释放进程、sandbox、browser、child agents 和 HTTP 客户端；这防止资源泄漏和孤儿进程。 | [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:3231), [cli.py](/home/nan/c_project/hfa/hermes_v-0-10-0/cli.py:611), [cli.py](/home/nan/c_project/hfa/hermes_v-0-10-0/cli.py:9840), [tools/terminal_tool.py](/home/nan/c_project/hfa/hermes_v-0-10-0/tools/terminal_tool.py:939) |
| session hooks | `on_session_finalize`、`on_session_reset`、`on_session_end` 用于 flush、reset、cleanup 和持久化，控制真实会话边界和 turn 收尾。 | [gateway/hooks.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/hooks.py:9), [cli.py](/home/nan/c_project/hfa/hermes_v-0-10-0/cli.py:4094), [cli.py](/home/nan/c_project/hfa/hermes_v-0-10-0/cli.py:638), [run_agent.py](/home/nan/c_project/hfa/hermes_v-0-10-0/run_agent.py:11290), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:4416) |
| gateway wrapper / session bridge | 网关用 `contextvars` 和 `copy_context` 保证每条消息的 session state 不互相覆盖，并把 `step`、`permission` 这类同步回调桥到 async 层；这是整个消息到 agent 的包装层。 | [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:7344), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:8407), [gateway/run.py](/home/nan/c_project/hfa/hermes_v-0-10-0/gateway/run.py:8657), [acp_adapter/permissions.py](/home/nan/c_project/hfa/hermes_v-0-10-0/acp_adapter/permissions.py:26), [acp_adapter/server.py](/home/nan/c_project/hfa/hermes_v-0-10-0/acp_adapter/server.py:394) |

### 说明

这一类机制主要不在“裁剪动作集合”，而是在控制：

- 一个 turn 最多能推进几步
- 工具输出如何进入下一轮状态
- 出错时如何 retry / fallback
- 资源何时清理
- session 何时 finalize、reset、结束

## 为什么这比“动作空间约束”更准确

如果只说 harness 是“对动作空间的约束”，只能覆盖 `Action Gating`。

但 Hermes 实际上同时做了三件事：

1. 塑造观测
   - 例如 `pre_llm_call`、memory prefetch、context engine
2. 裁剪动作
   - 例如 `pre_tool_call`、approval、loop-owned tools
3. 控制状态转移
   - 例如 budget、retry、cleanup、session hooks

所以更准确的说法是：

> Hermes 的 harness 是对 agent runtime 的三层控制系统：塑造观测、裁剪动作、控制转移。

其中只有 `Action Gating` 是狭义动作空间约束；`Observation Shaping` 和 `Transition Control` 也会强烈影响 agent 行为，但它们作用的不是动作集合本身。

