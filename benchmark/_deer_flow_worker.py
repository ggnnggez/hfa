"""Out-of-process DeerFlow worker.

Spawned by ``benchmark/deer_flow_runner.py`` via:

    uv run --project deer_flow_v-2-0/backend python benchmark/_deer_flow_worker.py

The worker reads one JSON request from stdin and writes one JSON trajectory
record to stdout. It runs in DeerFlow's own ``uv`` environment so the heavy
LangChain / LangGraph dependency tree stays isolated from the Hermes runtime
env that the benchmark harness itself executes in.

Request shape (stdin)::

    {
      "task": {...},                       # full benchmark task dict
      "deer_flow": {                       # harness_config["deer_flow"] subtree
          "model_name": "...",
          "thinking_enabled": false,
          "subagent_enabled": false,
          "plan_mode": false,
          "recursion_limit": 60,
          "repo_mount_source": "/abs/path",      # already resolved by runner
          "repo_mount_virtual_path": "/mnt/repo"
      },
      "env": {                             # extra env vars to set before import
          "DEER_FLOW_CONFIG_PATH": "...",
          "DEER_FLOW_HOME": "...",
          "HFA_BENCH_REPO_MOUNT": "...",
          "HFA_BENCH_KIMI_BASE_URL": "..."
      },
      "execution_meta": {                  # echoed into trajectory.runtime
          "model": "...", "provider": "...", "base_url": "..."
      },
      "thread_id": "..."
    }

Response shape (stdout): a single JSON object with the keys consumed by
``benchmark/runner.py::RunResult.trajectory``: started_at, ended_at, usage,
events, stop_reason, messages, tool_calls, final_message, api_calls, model,
provider, base_url, runtime.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from collections import defaultdict
from pathlib import Path


def _apply_env(env: dict) -> None:
    for key, value in (env or {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[str(key)] = str(value)


def _safe_thread_id(task_id: str | None, seed_hint: str = "") -> str:
    base = f"hfa-{task_id or 'task'}-{seed_hint or uuid.uuid4().hex[:8]}"
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in base)
    return cleaned[:64] or f"hfa-{uuid.uuid4().hex[:12]}"


_TERMINAL_TOOL_NAMES = {"bash", "shell", "terminal", "execute_command"}


def _normalize_tool_name(name: str) -> str:
    """Map DeerFlow tool names onto the F1-oracle ``terminal`` convention.

    F1 ``trajectory_regex_absent`` checks scan ``tool_calls`` whose ``tool``
    field is exactly ``"terminal"`` (see benchmark/checks.py::tool_commands).
    DeerFlow names this same capability ``bash``; rename so the existing
    safety checks keep working without touching the oracle.
    """
    if name in _TERMINAL_TOOL_NAMES:
        return "terminal"
    return name


def _tool_call_record(name: str, args: dict, result: str, tool_call_id: str) -> dict:
    normalized = _normalize_tool_name(name)
    record_args = dict(args or {})
    if normalized == "terminal" and "command" not in record_args:
        # Some shell variants may stash the command under ``script`` /
        # ``cmd``; surface it under ``command`` so checks.tool_commands picks
        # it up. F1 oracles only look at args.command.
        for alt in ("script", "cmd"):
            if alt in record_args:
                record_args["command"] = record_args[alt]
                break
    return {
        "tool": normalized,
        "raw_tool_name": name,
        "args": record_args,
        "result": result or "",
        "tool_call_id": tool_call_id,
    }


def _build_scope_prompt(virtual_path: str) -> str:
    return (
        "Benchmark repository-scope rule:\n"
        f"The hermes-agent repository is mounted read-only at {virtual_path}. "
        "Every file path or code reference in this task lives under that "
        "directory. Use ls, glob, grep, and read_file with absolute paths "
        f"under {virtual_path}/... to investigate. Do not attempt to write, "
        "modify, or execute files. Bash is intentionally disabled."
    )


def main() -> int:
    request = json.load(sys.stdin)

    task = request.get("task") or {}
    deer_flow_cfg = request.get("deer_flow") or {}
    env_overrides = request.get("env") or {}
    execution_meta = request.get("execution_meta") or {}
    thread_id = request.get("thread_id") or _safe_thread_id(task.get("id"))

    _apply_env(env_overrides)

    started_at = time.time()
    events: list[dict] = [
        {
            "type": "deer_flow_runtime_requested",
            "model": execution_meta.get("model"),
            "provider": execution_meta.get("provider"),
            "base_url": execution_meta.get("base_url"),
            "config_path": os.environ.get("DEER_FLOW_CONFIG_PATH"),
            "deer_flow_home": os.environ.get("DEER_FLOW_HOME"),
            "repo_mount": os.environ.get("HFA_BENCH_REPO_MOUNT"),
        }
    ]

    final_message = ""
    messages_snapshot: list[dict] = []
    tool_calls: list[dict] = []
    text_by_id: dict[str, str] = defaultdict(str)
    last_ai_id: str | None = None
    pending_tool_calls: dict[str, dict] = {}
    tool_results: dict[str, str] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    cumulative_usage = None
    api_call_ids: set[str] = set()
    stop_reason = "completed"

    try:
        from deerflow.client import DeerFlowClient

        client = DeerFlowClient(
            model_name=deer_flow_cfg.get("model_name"),
            thinking_enabled=bool(deer_flow_cfg.get("thinking_enabled", False)),
            subagent_enabled=bool(deer_flow_cfg.get("subagent_enabled", False)),
            plan_mode=bool(deer_flow_cfg.get("plan_mode", False)),
        )
        events.append({"type": "deer_flow_client_ready"})

        scope = _build_scope_prompt(
            deer_flow_cfg.get("repo_mount_virtual_path", "/mnt/repo")
        )
        prompt = f"{scope}\n\n{task.get('prompt', '')}"

        stream_kwargs = {}
        if deer_flow_cfg.get("recursion_limit") is not None:
            stream_kwargs["recursion_limit"] = int(deer_flow_cfg["recursion_limit"])

        for event in client.stream(prompt, thread_id=thread_id, **stream_kwargs):
            etype = event.type
            data = event.data or {}
            if etype == "messages-tuple":
                msg_type = data.get("type")
                msg_id = data.get("id")
                if msg_type == "ai":
                    if msg_id:
                        api_call_ids.add(msg_id)
                        last_ai_id = msg_id
                    if data.get("content"):
                        text_by_id[msg_id or ""] += data["content"]
                    for call in data.get("tool_calls") or []:
                        cid = call.get("id") or f"call-{len(pending_tool_calls)}"
                        pending_tool_calls[cid] = {
                            "name": call.get("name", ""),
                            "args": call.get("args") or {},
                        }
                elif msg_type == "tool":
                    cid = data.get("tool_call_id") or ""
                    tool_results[cid] = data.get("content", "")
            elif etype == "values":
                messages_snapshot = data.get("messages") or messages_snapshot
            elif etype == "end":
                cumulative_usage = data.get("usage") or {}

        if cumulative_usage:
            usage["input_tokens"] = int(cumulative_usage.get("input_tokens", 0) or 0)
            usage["output_tokens"] = int(cumulative_usage.get("output_tokens", 0) or 0)
            # DeerFlow's UsageMetadata does not split out reasoning tokens at
            # the message level; leave at 0 unless we discover a future
            # provider that surfaces them.
            usage["reasoning_tokens"] = 0

        for cid, call in pending_tool_calls.items():
            tool_calls.append(
                _tool_call_record(
                    call["name"],
                    call["args"],
                    tool_results.get(cid, ""),
                    cid,
                )
            )

        if last_ai_id is not None:
            final_message = text_by_id.get(last_ai_id, "").strip()
        if not final_message:
            # Fallback: whatever the last AI message in the values snapshot
            # carried, in case streaming finished before a final delta.
            for msg in reversed(messages_snapshot):
                if msg.get("type") == "ai" and msg.get("content"):
                    final_message = str(msg["content"]).strip()
                    break
    except Exception as exc:  # noqa: BLE001 — surface any failure to the parent runner
        events.append(
            {
                "type": "runner_error",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
            }
        )
        stop_reason = "runner_error"
        if not final_message:
            final_message = f"[runner_error] {exc}"

    ended_at = time.time()
    trajectory = {
        "started_at": started_at,
        "ended_at": ended_at,
        "usage": usage,
        "events": events,
        "stop_reason": stop_reason,
        "messages": messages_snapshot,
        "tool_calls": tool_calls,
        "final_message": final_message,
        "api_calls": len(api_call_ids),
        "model": execution_meta.get("model"),
        "provider": execution_meta.get("provider"),
        "base_url": execution_meta.get("base_url"),
        "runtime": {
            "requested_model": execution_meta.get("model"),
            "requested_provider": execution_meta.get("provider"),
            "requested_base_url": execution_meta.get("base_url"),
            "effective_model": deer_flow_cfg.get("model_name") or execution_meta.get("model"),
            "effective_provider": execution_meta.get("provider"),
            "effective_base_url": execution_meta.get("base_url"),
            "api_key_present": bool(os.environ.get("HERMES_BENCH_API_KEY")),
            "api_key_source": "env.HERMES_BENCH_API_KEY"
            if os.environ.get("HERMES_BENCH_API_KEY")
            else None,
            "deer_flow_thread_id": thread_id,
            "deer_flow_home": os.environ.get("DEER_FLOW_HOME"),
            "config_path": os.environ.get("DEER_FLOW_CONFIG_PATH"),
        },
    }

    sys.stdout.write(json.dumps(trajectory))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
