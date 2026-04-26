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

        enabled_toolsets = action.get("enabled_toolsets")
        if not isinstance(enabled_toolsets, list):
            enabled_toolsets = None

        repo_root = task.get("repo_root") or str(HERMES_ROOT)
        repo_path = Path(repo_root)
        if not repo_path.is_absolute():
            repo_path = WORKSPACE_ROOT / repo_path

        model = execution.get("model") or os.environ.get("HERMES_BENCH_MODEL") or ""
        base_url = execution.get("base_url") or os.environ.get("HERMES_BENCH_BASE_URL") or ""
        api_key = execution.get("api_key") or os.environ.get("HERMES_BENCH_API_KEY") or ""
        provider = execution.get("provider") or os.environ.get("HERMES_BENCH_PROVIDER") or ""

        agent = None
        started_at = time.time()
        messages: list[dict] = []
        final_message = ""
        events: list[dict] = []
        result: dict = {}

        with _temporary_env({
            "TERMINAL_CWD": str(repo_path),
            "HERMES_QUIET": "1",
        }), _temporary_cwd(repo_path):
            try:
                _ensure_hermes_import_path()
                from run_agent import AIAgent

                with _capture_stdio() as captured:
                    agent = AIAgent(
                        base_url=base_url,
                        api_key=api_key,
                        provider=provider,
                        model=model,
                        max_iterations=int(transition.get("max_iterations") or 30),
                        enabled_toolsets=enabled_toolsets,
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
        }
        return RunResult(trajectory=trajectory, workspace_path=str(repo_path))
