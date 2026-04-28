"""F2 bug-fix oracle.

F2 tasks are mutable workspaces. The runner copies each task's
``workspace_source`` into an isolated temp directory, lets the agent edit it,
then this oracle runs the task's test commands inside that workspace.
The first local F2 slice intentionally uses small unittest fixtures so the
benchmark can validate the edit/test loop before wiring SWE-bench Lite.
"""
from __future__ import annotations

import subprocess
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
        tests_unchanged = True
        if source is not None:
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
