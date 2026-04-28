"""F2 bug-fix oracle.

F2 tasks are mutable workspaces. The runner copies each task's
``workspace_source`` into an isolated temp directory, lets the agent edit it,
then this oracle runs the task's test commands inside that workspace.
The first local F2 slice intentionally uses small unittest fixtures so the
benchmark can validate the edit/test loop before wiring SWE-bench Lite.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional

from oracle_base import OracleResult


def _repo_root() -> Path:
    """Find the repository root without depending on fixed parent depth."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "benchmark").is_dir() and (parent / "hermes_v-0-10-0").is_dir():
            return parent
    raise RuntimeError("repo root not found from F2 oracle path")


def _resolve_workspace_source(value: object) -> Path | None:
    if not value:
        return None
    source = Path(str(value))
    if not source.is_absolute():
        source = _repo_root() / source
    return source


def _relative_file_bytes(root: Path, subdir: str) -> dict[str, bytes]:
    base = root / subdir
    if not base.exists():
        return {}
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }


def _file_bytes_for_paths(root: Path, paths: list[str]) -> dict[str, bytes | None]:
    files: dict[str, bytes | None] = {}
    for raw_path in paths:
        rel = Path(str(raw_path))
        if rel.is_absolute() or ".." in rel.parts:
            files[str(raw_path)] = None
            continue
        path = root / rel
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if (
                    child.is_file()
                    and "__pycache__" not in child.parts
                    and child.suffix != ".pyc"
                ):
                    files[str(child.relative_to(root))] = child.read_bytes()
        elif path.is_file():
            if "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[str(rel)] = path.read_bytes()
        else:
            files[str(rel)] = None
    return files


def _model_patch_from_workspace(source: Path, workspace: Path, protected_paths: list[str]) -> str:
    """Build a SWE-bench model_patch from tracked implementation changes only.

    The copied workspace keeps the prepared repository's ``.git`` directory,
    with the benchmark test patch already present in the working tree. A git
    diff from the agent workspace therefore captures implementation edits while
    pathspec excludes remove protected tests. Untracked runtime artifacts such
    as ``.pytest_cache`` and compiled extensions are intentionally ignored.
    """
    pathspecs = ["."]
    pathspecs.extend(
        f":(exclude){path}"
        for path in protected_paths
        if path and not Path(path).is_absolute() and ".." not in Path(path).parts
    )
    pathspecs.extend([
        ":(exclude)**/.pytest_cache/**",
        ":(exclude)**/__pycache__/**",
        ":(exclude)**/*.pyc",
        ":(exclude)**/*.so",
        ":(exclude)**/*.o",
    ])
    completed = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "--", *pathspecs],
        cwd=workspace,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-4000:] or "git diff failed")
    return completed.stdout


def _find_resolution(value: object, instance_id: str) -> bool | None:
    if isinstance(value, dict):
        if instance_id in value:
            nested = _find_resolution(value[instance_id], instance_id)
            if nested is not None:
                return nested
        resolved_ids = value.get("resolved_ids")
        if isinstance(resolved_ids, list) and instance_id in {str(item) for item in resolved_ids}:
            return True
        unresolved_ids = value.get("unresolved_ids")
        if isinstance(unresolved_ids, list) and instance_id in {str(item) for item in unresolved_ids}:
            return False
        if value.get("instance_id") == instance_id:
            for key in ("resolved", "passed", "success"):
                if isinstance(value.get(key), bool):
                    return bool(value[key])
        resolved = value.get("resolved")
        if isinstance(resolved, list) and instance_id in {str(item) for item in resolved}:
            return True
        unresolved = value.get("unresolved")
        if isinstance(unresolved, list) and instance_id in {str(item) for item in unresolved}:
            return False
        for child in value.values():
            nested = _find_resolution(child, instance_id)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_resolution(child, instance_id)
            if nested is not None:
                return nested
    return None


def _read_swebench_resolution(eval_root: Path, instance_id: str) -> bool | None:
    candidates = sorted(
        list(eval_root.glob("*.json"))
        + list(eval_root.glob("*.jsonl"))
        + list((eval_root / "evaluation_results").rglob("*.json"))
        + list((eval_root / "evaluation_results").rglob("*.jsonl")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            if path.suffix == ".jsonl":
                for line in path.read_text(errors="replace").splitlines():
                    if not line.strip():
                        continue
                    resolved = _find_resolution(json.loads(line), instance_id)
                    if resolved is not None:
                        return resolved
            else:
                resolved = _find_resolution(json.loads(path.read_text(errors="replace")), instance_id)
                if resolved is not None:
                    return resolved
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _run_command(
    command: list[str],
    cwd: Path,
    timeout: int,
    live_log: bool,
) -> tuple[int, str, str]:
    if not live_log:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout, completed.stderr

    print(f"[phase] swebench_command cwd={cwd}")
    print("[phase] " + " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    tail = deque(maxlen=4000)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            tail.extend(line)
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return returncode, "".join(tail), ""


def _evaluate_swebench_lite(task: dict, workspace: Path, source: Path) -> OracleResult:
    instance_id = str(task.get("instance_id") or "")
    if not instance_id:
        return {"passed": False, "detail": {"reason": "missing instance_id"}}

    protected_paths = [str(path) for path in task.get("protected_paths", [])]
    tests_unchanged = (
        _file_bytes_for_paths(source, protected_paths)
        == _file_bytes_for_paths(workspace, protected_paths)
    )
    try:
        model_patch = _model_patch_from_workspace(source, workspace, protected_paths)
    except Exception as exc:
        return {
            "passed": False,
            "detail": {
                "reason": "model_patch_generation_failed",
                "error": str(exc),
                "tests_unchanged": tests_unchanged,
            },
        }

    if not model_patch.strip():
        return {
            "passed": False,
            "detail": {
                "reason": "empty_model_patch",
                "tests_unchanged": tests_unchanged,
            },
        }

    eval_root = _repo_root() / "benchmark" / ".cache" / "swebench_eval"
    eval_root.mkdir(parents=True, exist_ok=True)
    run_id = f"hfa_{instance_id}_{int(time.time())}"
    predictions_path = eval_root / f"{run_id}_predictions.jsonl"
    predictions_path.write_text(json.dumps({
        "instance_id": instance_id,
        "model_name_or_path": "hfa-hermes-direct",
        "model_patch": model_patch,
    }) + "\n")

    eval_timeout = int(os.getenv("HFA_SWEBENCH_EVAL_TIMEOUT_SEC", "3600"))
    test_timeout = int(task.get("test_timeout_sec") or 1800)
    dataset_name = os.getenv("HFA_SWEBENCH_DATASET_NAME", "SWE-bench/SWE-bench_Lite")
    command = [
        "uv",
        "run",
        "--with",
        "swebench",
        "python",
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset_name,
        "--predictions_path",
        str(predictions_path),
        "--instance_ids",
        instance_id,
        "--max_workers",
        "1",
        "--timeout",
        str(test_timeout),
        "--run_id",
        run_id,
        "--cache_level",
        str(os.getenv("HFA_SWEBENCH_CACHE_LEVEL", "env")),
    ]
    namespace = os.getenv("HFA_SWEBENCH_NAMESPACE")
    if namespace is not None:
        command.extend(["--namespace", namespace])

    live_log = os.getenv("HFA_BENCH_LIVE_LOG") == "1"
    if live_log:
        print(
            f"[phase] swebench_eval_start instance_id={instance_id} "
            f"run_id={run_id} predictions={predictions_path}"
        )

    try:
        returncode, stdout, stderr = _run_command(
            command,
            cwd=eval_root,
            timeout=eval_timeout,
            live_log=live_log,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "passed": False,
            "detail": {
                "backend": "swebench_official",
                "reason": "official_evaluation_timeout",
                "timeout": eval_timeout,
                "test_timeout": test_timeout,
                "predictions_path": str(predictions_path),
                "run_id": run_id,
                "tests_unchanged": tests_unchanged,
                "model_patch_chars": len(model_patch),
                "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            },
        }

    resolved = _read_swebench_resolution(eval_root, instance_id)
    passed = bool(resolved) and tests_unchanged
    if live_log:
        print(
            f"[phase] swebench_eval_done instance_id={instance_id} "
            f"returncode={returncode} resolved={resolved} passed={passed}"
        )
    return {
        "passed": passed,
        "detail": {
            "backend": "swebench_official",
            "command": command,
            "returncode": returncode,
            "resolved": resolved,
            "tests_unchanged": tests_unchanged,
            "dataset_name": dataset_name,
            "test_timeout": test_timeout,
            "predictions_path": str(predictions_path),
            "run_id": run_id,
            "model_patch_chars": len(model_patch),
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
        },
    }


class FamilyOracle:
    def evaluate(
        self,
        task: dict,
        trajectory: dict,
        workspace_path: Optional[str],
    ) -> OracleResult:
        if not workspace_path:
            return {"passed": False, "detail": {"reason": "missing workspace_path"}}

        commands = task.get("test_commands") or []
        if not commands:
            return {"passed": False, "detail": {"reason": "missing test_commands"}}

        workspace = Path(workspace_path)
        source = _resolve_workspace_source(task.get("workspace_source"))
        if task.get("source") == "swebench_lite":
            if source is None:
                return {"passed": False, "detail": {"reason": "missing workspace_source"}}
            return _evaluate_swebench_lite(task, workspace, source)

        tests_unchanged = True
        if source is not None:
            protected_paths = [str(path) for path in task.get("protected_paths", [])]
            if protected_paths:
                tests_unchanged = (
                    _file_bytes_for_paths(source, protected_paths)
                    == _file_bytes_for_paths(workspace, protected_paths)
                )
            else:
                tests_unchanged = (
                    _relative_file_bytes(source, "tests")
                    == _relative_file_bytes(workspace, "tests")
                )

        timeout = int(task.get("test_timeout_sec") or 30)
        results = []
        passed = True
        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace_path,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                ok = completed.returncode == 0
                results.append({
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                    "ok": ok,
                })
                passed = passed and ok
            except subprocess.TimeoutExpired as exc:
                results.append({
                    "command": command,
                    "returncode": None,
                    "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                    "ok": False,
                    "timeout": timeout,
                })
                passed = False

        if not tests_unchanged:
            passed = False

        return {
            "passed": passed,
            "detail": {
                "commands": results,
                "tests_unchanged": tests_unchanged,
            },
        }
