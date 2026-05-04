"""Out-of-process DeerFlow runner.

Uses the same ``AgentRunner`` protocol as ``hermes_direct_runner.py`` but
delegates execution to ``_deer_flow_worker.py`` running in DeerFlow's own
``uv`` environment. The two agents have incompatible LangChain/LangGraph
dependency pins, so they cannot share an interpreter.

Per-call cost: one ``uv run`` startup (~1-2s on a warm uv cache). For a
50-task × 5-seed F1 sweep that's ~5 wall-clock minutes of overhead, which
is acceptable; the alternative — installing both agents into one env — is
far more brittle.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from runner import RunResult


BENCHMARK_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = BENCHMARK_ROOT.parent
DEER_FLOW_ROOT = WORKSPACE_ROOT / "deer_flow_v-2-0"
DEER_FLOW_BACKEND = DEER_FLOW_ROOT / "backend"
WORKER_PATH = BENCHMARK_ROOT / "_deer_flow_worker.py"
DEFAULT_DEER_FLOW_HOME = Path(
    os.getenv("HFA_BENCH_DEER_FLOW_HOME", "/tmp/hfa-bench-deer-flow")
)


def _resolve_repo_mount(deer_flow_cfg: dict) -> Path:
    raw = deer_flow_cfg.get("repo_mount_source") or "hermes_v-0-10-0"
    path = Path(str(raw))
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    return path


def _resolve_app_config_path(deer_flow_cfg: dict) -> Path:
    raw = deer_flow_cfg.get("app_config_path") or "benchmark/configs/deer_flow_app_config.yaml"
    path = Path(str(raw))
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    return path


def _execution_meta(execution: dict) -> dict:
    return {
        "model": execution.get("model"),
        "provider": execution.get("provider"),
        "base_url": execution.get("base_url"),
    }


def _safe_thread_id(task_id: str | None, seed_hint: str) -> str:
    base = f"hfa-{task_id or 'task'}-{seed_hint}"
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in base)
    return cleaned[:64] or f"hfa-{uuid.uuid4().hex[:12]}"


def _failure_trajectory(
    started_at: float,
    execution: dict,
    events: list[dict],
    final_message: str,
    stop_reason: str = "runner_error",
) -> dict:
    return {
        "started_at": started_at,
        "ended_at": time.time(),
        "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
        "events": events,
        "stop_reason": stop_reason,
        "messages": [],
        "tool_calls": [],
        "final_message": final_message,
        "api_calls": 0,
        "model": execution.get("model"),
        "provider": execution.get("provider"),
        "base_url": execution.get("base_url"),
        "runtime": {
            "requested_model": execution.get("model"),
            "requested_provider": execution.get("provider"),
            "requested_base_url": execution.get("base_url"),
            "effective_model": execution.get("model"),
            "effective_provider": execution.get("provider"),
            "effective_base_url": execution.get("base_url"),
            "api_key_present": bool(os.environ.get("HERMES_BENCH_API_KEY")),
            "api_key_source": "env.HERMES_BENCH_API_KEY"
            if os.environ.get("HERMES_BENCH_API_KEY")
            else None,
        },
    }


class DeerFlowRunner:
    """Run benchmark tasks by spawning a DeerFlow worker subprocess."""

    def run(self, task: dict, harness_config: dict) -> RunResult:
        execution = harness_config.get("execution", {}) or {}
        deer_flow_cfg = harness_config.get("deer_flow", {}) or {}
        started_at = time.time()
        events: list[dict] = []

        if not DEER_FLOW_BACKEND.exists():
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": (
                            f"deer_flow backend not found at {DEER_FLOW_BACKEND}. "
                            "Run scripts/fetch_agents.sh deer_flow and "
                            "(cd deer_flow_v-2-0/backend && uv sync)."
                        ),
                    }],
                    "[runner_error] deer_flow not vendored",
                ),
                workspace_path=None,
            )

        if shutil.which("uv") is None:
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": "`uv` is not on PATH; deer_flow runner needs it to launch the worker venv.",
                    }],
                    "[runner_error] uv missing",
                ),
                workspace_path=None,
            )

        repo_mount = _resolve_repo_mount(deer_flow_cfg)
        if not repo_mount.exists():
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": f"repo_mount_source does not exist: {repo_mount}",
                    }],
                    f"[runner_error] missing repo mount {repo_mount}",
                ),
                workspace_path=None,
            )

        app_config_path = _resolve_app_config_path(deer_flow_cfg)
        if not app_config_path.exists():
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": f"deer_flow app_config_path does not exist: {app_config_path}",
                    }],
                    f"[runner_error] missing deer_flow app config at {app_config_path}",
                ),
                workspace_path=None,
            )

        DEFAULT_DEER_FLOW_HOME.mkdir(parents=True, exist_ok=True)

        # Resolve the API key the same way HermesDirectRunner does so a single
        # HERMES_BENCH_API_KEY in the env feeds both runners.
        api_key_env = str(execution.get("api_key_env") or "HERMES_BENCH_API_KEY")
        api_key = (
            os.environ.get(api_key_env)
            or os.environ.get("HERMES_BENCH_API_KEY")
            or ""
        )
        kimi_base_url = (
            execution.get("base_url")
            or os.environ.get("HFA_BENCH_KIMI_BASE_URL")
            or os.environ.get("KIMI_BASE_URL")
            or "https://api.moonshot.cn/v1"
        )

        if execution.get("strict_runtime", True) and not api_key:
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": (
                            f"strict_runtime requires an API key in env {api_key_env} "
                            "(or HERMES_BENCH_API_KEY) for the deer_flow worker."
                        ),
                    }],
                    "[runner_error] missing API key for deer_flow worker",
                ),
                workspace_path=None,
            )

        thread_id = _safe_thread_id(task.get("id"), uuid.uuid4().hex[:8])

        worker_env = {
            "DEER_FLOW_CONFIG_PATH": str(app_config_path),
            "DEER_FLOW_HOME": str(DEFAULT_DEER_FLOW_HOME),
            "HFA_BENCH_REPO_MOUNT": str(repo_mount),
            "HFA_BENCH_KIMI_BASE_URL": kimi_base_url,
            "HERMES_BENCH_API_KEY": api_key,
        }

        request = {
            "task": task,
            "deer_flow": {
                **deer_flow_cfg,
                "repo_mount_source": str(repo_mount),
            },
            "env": worker_env,
            "execution_meta": _execution_meta(execution),
            "thread_id": thread_id,
        }

        events.append({
            "type": "deer_flow_worker_invoke",
            "worker": str(WORKER_PATH),
            "deer_flow_backend": str(DEER_FLOW_BACKEND),
            "thread_id": thread_id,
            "config_path": str(app_config_path),
            "deer_flow_home": str(DEFAULT_DEER_FLOW_HOME),
            "repo_mount": str(repo_mount),
        })

        cmd = [
            "uv",
            "run",
            "--project",
            str(DEER_FLOW_BACKEND),
            "python",
            "-u",
            str(WORKER_PATH),
        ]

        live_log = os.getenv("HFA_BENCH_LIVE_LOG") == "1"
        stderr_target = sys.__stderr__ if live_log else subprocess.PIPE

        try:
            completed = subprocess.run(
                cmd,
                input=json.dumps(request),
                stderr=stderr_target if stderr_target is not subprocess.PIPE else subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(WORKSPACE_ROOT),
                env={**os.environ, **worker_env},
            )
        except FileNotFoundError as exc:
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events + [{
                        "type": "runner_error",
                        "message": f"failed to spawn deer_flow worker: {exc}",
                    }],
                    f"[runner_error] {exc}",
                ),
                workspace_path=str(repo_mount),
            )

        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""

        if completed.returncode != 0:
            events.append({
                "type": "deer_flow_worker_failed",
                "returncode": completed.returncode,
                "stderr_tail": stderr_text[-4000:],
            })
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events,
                    f"[runner_error] deer_flow worker exited {completed.returncode}",
                ),
                workspace_path=str(repo_mount),
            )

        try:
            trajectory = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            events.append({
                "type": "deer_flow_worker_bad_json",
                "stdout_tail": stdout_text[-2000:],
                "stderr_tail": stderr_text[-2000:],
                "error": str(exc),
            })
            return RunResult(
                trajectory=_failure_trajectory(
                    started_at,
                    execution,
                    events,
                    "[runner_error] deer_flow worker returned non-JSON",
                ),
                workspace_path=str(repo_mount),
            )

        # Splice runner-side events in front of worker events so the
        # invocation context (paths, thread id) is preserved next to the
        # worker's own runtime trace.
        worker_events = trajectory.get("events") or []
        trajectory["events"] = events + worker_events
        if stderr_text.strip() and not live_log:
            trajectory["events"].append({
                "type": "deer_flow_worker_stderr",
                "stderr_tail": stderr_text[-2000:],
            })

        # Echo execution metadata back at the runner layer (worker only knows
        # what the request told it).
        trajectory.setdefault("model", execution.get("model"))
        trajectory.setdefault("provider", execution.get("provider"))
        trajectory.setdefault("base_url", execution.get("base_url"))

        return RunResult(trajectory=trajectory, workspace_path=str(repo_mount))
