"""Direct in-process Hermes runner.

This runner is the first real bridge between the benchmark harness and the
checked-out Hermes Agent codebase. It intentionally starts with the F1/F4
middle-format needs: final answer, tool calls, timing, usage, and stop reason.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Iterator

from runner import RunResult


BENCHMARK_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = BENCHMARK_ROOT.parent
HERMES_ROOT = WORKSPACE_ROOT / "hermes_v-0-10-0"


@contextmanager
def _temporary_env(updates: dict[str, str]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            os.environ[key] = value
        yield
    finally:
        for key, old in old_values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


@contextmanager
def _temporary_cwd(path: Path) -> Iterator[None]:
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _ensure_hermes_import_path() -> None:
    root = str(HERMES_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _normalize_str_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _filter_tool_definitions(
    tool_defs: list[dict],
    allow_tools: list[str] | None,
    deny_tools: list[str] | None,
) -> list[dict]:
    allow = set(allow_tools or [])
    deny = set(deny_tools or [])
    filtered: list[dict] = []
    for tool_def in tool_defs:
        name = tool_def.get("function", {}).get("name")
        if not name:
            continue
        if allow and name not in allow:
            continue
        if name in deny:
            continue
        filtered.append(tool_def)
    return filtered


def _action_gate_policy(action: dict) -> dict:
    deny_tools = set(_normalize_str_list(action.get("deny_tools")) or [])
    if action.get("delegation_allowed") is False:
        deny_tools.add("delegate_task")
    if action.get("clarify_enabled") is False:
        deny_tools.add("clarify")
    if action.get("disable_high_risk_tools"):
        deny_tools.update({"patch", "write_file", "terminal", "process"})

    return {
        "enabled_toolsets": _normalize_str_list(action.get("enabled_toolsets")),
        "disabled_toolsets": _normalize_str_list(action.get("disabled_toolsets")),
        "allow_tools": _normalize_str_list(action.get("allow_tools")),
        "deny_tools": sorted(deny_tools),
    }


def _execution_runtime(execution: dict) -> dict:
    api_key_env = str(execution.get("api_key_env") or "HERMES_BENCH_API_KEY")

    values: dict[str, str] = {}
    sources: dict[str, str | None] = {}
    for name, env_name in (
        ("model", "HERMES_BENCH_MODEL"),
        ("base_url", "HERMES_BENCH_BASE_URL"),
        ("provider", "HERMES_BENCH_PROVIDER"),
    ):
        if execution.get(name):
            values[name] = str(execution[name])
            sources[name] = f"config.execution.{name}"
        elif os.environ.get(env_name):
            values[name] = str(os.environ[env_name])
            sources[name] = f"env.{env_name}"
        else:
            values[name] = ""
            sources[name] = None

    if execution.get("api_key"):
        values["api_key"] = str(execution["api_key"])
        sources["api_key"] = "config.execution.api_key"
    elif os.environ.get(api_key_env):
        values["api_key"] = str(os.environ[api_key_env])
        sources["api_key"] = f"env.{api_key_env}"
    elif os.environ.get("HERMES_BENCH_API_KEY"):
        values["api_key"] = str(os.environ["HERMES_BENCH_API_KEY"])
        sources["api_key"] = "env.HERMES_BENCH_API_KEY"
    else:
        values["api_key"] = ""
        sources["api_key"] = None

    strict_runtime = execution.get("strict_runtime")
    if strict_runtime is None:
        strict_runtime = True

    return {
        **values,
        "api_key_env": api_key_env,
        "strict_runtime": bool(strict_runtime),
        "sources": sources,
    }


@contextmanager
def _temporary_tool_schema_gate(
    run_agent_module: object,
    policy: dict,
    concurrent_tool_execution: bool,
) -> Iterator[None]:
    """Filter Hermes tool schemas for this benchmark run only.

    Hermes already supports toolset-level exposure. The benchmark harness adds
    per-tool allow/deny filtering by wrapping the schema provider imported by
    run_agent.py, without modifying Hermes source.
    """
    import model_tools

    original_model_tools = model_tools.get_tool_definitions
    original_run_agent = getattr(run_agent_module, "get_tool_definitions")
    original_parallelizer = getattr(run_agent_module, "_should_parallelize_tool_batch", None)

    allow_tools = policy.get("allow_tools")
    deny_tools = policy.get("deny_tools")

    def gated_get_tool_definitions(*args, **kwargs):
        tool_defs = original_model_tools(*args, **kwargs)
        return _filter_tool_definitions(tool_defs, allow_tools, deny_tools)

    try:
        model_tools.get_tool_definitions = gated_get_tool_definitions
        setattr(run_agent_module, "get_tool_definitions", gated_get_tool_definitions)
        if not concurrent_tool_execution and original_parallelizer is not None:
            setattr(run_agent_module, "_should_parallelize_tool_batch", lambda tool_calls: False)
        yield
    finally:
        model_tools.get_tool_definitions = original_model_tools
        setattr(run_agent_module, "get_tool_definitions", original_run_agent)
        if original_parallelizer is not None:
            setattr(run_agent_module, "_should_parallelize_tool_batch", original_parallelizer)


@contextmanager
def _capture_stdio() -> Iterator[tuple[StringIO, StringIO]]:
    stdout = StringIO()
    stderr = StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = stdout
        sys.stderr = stderr
        yield stdout, stderr
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _record_captured_output(
    events: list[dict],
    stage: str,
    captured: tuple[StringIO, StringIO],
) -> None:
    stdout, stderr = captured
    for stream_name, value in (
        ("stdout", stdout.getvalue()),
        ("stderr", stderr.getvalue()),
    ):
        text = value.strip()
        if not text:
            continue
        events.append({
            "type": "captured_output",
            "stage": stage,
            "stream": stream_name,
            "message": text[-4000:],
        })


def _parse_tool_args(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_tool_calls(messages: list[dict]) -> list[dict]:
    results_by_id: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            results_by_id[str(msg["tool_call_id"])] = str(msg.get("content", ""))

    calls: list[dict] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            call_id = str(call.get("id", ""))
            calls.append({
                "tool": fn.get("name", ""),
                "args": _parse_tool_args(fn.get("arguments")),
                "result": results_by_id.get(call_id, ""),
                "tool_call_id": call_id,
            })
    return calls


def _stop_reason(result: dict) -> str:
    if result.get("interrupted"):
        return "interrupted"
    if result.get("completed"):
        return "completed"
    if result.get("failed"):
        return "failed"
    if result.get("partial"):
        return "partial"
    return "unknown"


class HermesDirectRunner:
    """Run benchmark tasks by instantiating Hermes ``AIAgent`` directly."""

    def run(self, task: dict, harness_config: dict) -> RunResult:
        execution = harness_config.get("execution", {}) or {}
        action = harness_config.get("action_gating", {}) or {}
        transition = harness_config.get("transition_control", {}) or {}

        gate_policy = _action_gate_policy(action)
        runtime = _execution_runtime(execution)

        repo_root = task.get("repo_root") or str(HERMES_ROOT)
        repo_path = Path(repo_root)
        if not repo_path.is_absolute():
            repo_path = WORKSPACE_ROOT / repo_path

        model = runtime["model"]
        base_url = runtime["base_url"]
        api_key = runtime["api_key"]
        provider = runtime["provider"]

        agent = None
        started_at = time.time()
        messages: list[dict] = []
        final_message = ""
        events: list[dict] = []
        result: dict = {}

        events.append({
            "type": "action_gate_policy",
            "policy": gate_policy,
            "concurrent_tool_execution": bool(transition.get("concurrent_tool_execution")),
        })
        events.append({
            "type": "runtime_requested",
            "model": model,
            "provider": provider,
            "base_url": base_url,
            "api_key_present": bool(api_key),
            "api_key_source": runtime["sources"].get("api_key"),
            "sources": {k: v for k, v in runtime["sources"].items() if k != "api_key"},
            "strict_runtime": runtime["strict_runtime"],
        })

        if runtime["strict_runtime"] and base_url and not api_key:
            events.append({
                "type": "runtime_config_error",
                "message": (
                    "Explicit execution.base_url requires an explicit API key in "
                    "execution.api_key or the configured api_key_env. This prevents "
                    "Hermes from falling back to a local provider config with a "
                    "different endpoint."
                ),
                "api_key_env": runtime["api_key_env"],
            })
            ended_at = time.time()
            trajectory = {
                "started_at": started_at,
                "ended_at": ended_at,
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
                "events": events,
                "stop_reason": "runner_error",
                "messages": [],
                "tool_calls": [],
                "final_message": "[runner_error] missing explicit API key for configured base_url",
                "api_calls": 0,
                "model": model,
                "provider": provider,
                "base_url": base_url,
                "runtime": {
                    "requested_model": model,
                    "requested_provider": provider,
                    "requested_base_url": base_url,
                    "effective_model": model,
                    "effective_provider": provider,
                    "effective_base_url": base_url,
                    "api_key_present": False,
                    "api_key_source": None,
                },
            }
            return RunResult(trajectory=trajectory, workspace_path=str(repo_path))

        with _temporary_env({
            "TERMINAL_CWD": str(repo_path),
            "HERMES_QUIET": "1",
        }), _temporary_cwd(repo_path):
            try:
                _ensure_hermes_import_path()
                import run_agent
                from run_agent import AIAgent

                with _temporary_tool_schema_gate(
                    run_agent,
                    gate_policy,
                    bool(transition.get("concurrent_tool_execution")),
                ):
                    with _capture_stdio() as captured:
                        agent = AIAgent(
                            base_url=base_url,
                            api_key=api_key,
                            provider=provider,
                            model=model,
                            max_iterations=int(transition.get("max_iterations") or 30),
                            enabled_toolsets=gate_policy["enabled_toolsets"],
                            disabled_toolsets=gate_policy["disabled_toolsets"],
                            quiet_mode=True,
                            verbose_logging=False,
                            tool_delay=0.0,
                            skip_context_files=True,
                            skip_memory=True,
                            persist_session=False,
                            session_id=f"bench-{task.get('id', 'task')}-{uuid.uuid4().hex[:8]}",
                            platform="benchmark",
                        )
                    _record_captured_output(events, "agent_init", captured)
                    events.append({
                        "type": "action_gate_exposure",
                        "enabled_tools": sorted(getattr(agent, "valid_tool_names", set())),
                    })
                    events.append({
                        "type": "runtime_effective",
                        "model": getattr(agent, "model", model),
                        "provider": getattr(agent, "provider", provider),
                        "base_url": getattr(agent, "base_url", base_url),
                        "api_mode": getattr(agent, "api_mode", ""),
                        "api_key_present": bool(getattr(agent, "api_key", "")),
                        "api_key_source": runtime["sources"].get("api_key"),
                    })

                    with _capture_stdio() as captured:
                        result = agent.run_conversation(
                            task.get("prompt", ""),
                            task_id=f"bench-{task.get('id', 'task')}",
                        )
                    _record_captured_output(events, "run_conversation", captured)

                    messages = result.get("messages") or []
                    final_message = result.get("final_response") or ""
                    if result.get("failed"):
                        events.append({
                            "type": "agent_failed",
                            "message": str(result.get("error") or final_message or "unknown failure"),
                        })
            except Exception as exc:
                events.append({"type": "runner_error", "message": str(exc)})
                final_message = f"[runner_error] {exc}"
            finally:
                if agent is not None:
                    try:
                        with _capture_stdio() as captured:
                            agent.close()
                        _record_captured_output(events, "agent_close", captured)
                    except Exception as exc:
                        events.append({"type": "cleanup_error", "message": str(exc)})

        ended_at = time.time()
        usage = {
            "input_tokens": int(result.get("input_tokens", 0) or 0),
            "output_tokens": int(result.get("output_tokens", 0) or 0),
            "reasoning_tokens": int(result.get("reasoning_tokens", 0) or 0),
        }
        trajectory = {
            "started_at": started_at,
            "ended_at": ended_at,
            "usage": usage,
            "events": events,
            "stop_reason": "runner_error" if any(e.get("type") == "runner_error" for e in events) else _stop_reason(result),
            "messages": messages,
            "tool_calls": _extract_tool_calls(messages),
            "final_message": final_message,
            "api_calls": result.get("api_calls", 0),
            "model": result.get("model") or model,
            "provider": result.get("provider") or provider,
            "base_url": getattr(agent, "base_url", base_url) if agent is not None else base_url,
            "runtime": {
                "requested_model": model,
                "requested_provider": provider,
                "requested_base_url": base_url,
                "effective_model": result.get("model") or (getattr(agent, "model", model) if agent is not None else model),
                "effective_provider": result.get("provider") or (getattr(agent, "provider", provider) if agent is not None else provider),
                "effective_base_url": getattr(agent, "base_url", base_url) if agent is not None else base_url,
                "api_key_present": bool(getattr(agent, "api_key", api_key) if agent is not None else api_key),
                "api_key_source": runtime["sources"].get("api_key"),
            },
        }
        return RunResult(trajectory=trajectory, workspace_path=str(repo_path))
