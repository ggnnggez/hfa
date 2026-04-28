#!/usr/bin/env python3
"""Prepare a small SWE-bench Lite subset for the F2 benchmark.

Input is a local JSONL export of SWE-bench Lite instances. The script clones
each repo at base_commit, applies test_patch, and appends benchmark tasks that
HermesDirectRunner can run in isolated copied workspaces.

The generated workspaces live under benchmark/workspaces/ and are intentionally
gitignored. Commit only the task JSONL when you want to pin a subset.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE_ROOT = REPO_ROOT / "benchmark" / "workspaces" / "swebench_lite"
DEFAULT_REPO_CACHE = REPO_ROOT / "benchmark" / ".cache" / "swebench_repos"
DEFAULT_OUT = REPO_ROOT / "benchmark" / "families" / "F2_bug_fix" / "tasks_swebench_lite.jsonl"


def run(cmd: list[str], cwd: Path | None = None, input_text: str | None = None) -> None:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"command failed ({completed.returncode}): {' '.join(cmd)}\n"
            f"cwd={cwd}\nstdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value)


def load_instances(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def select_instances(rows: list[dict], instance_ids: set[str], limit: int | None) -> list[dict]:
    if instance_ids:
        selected = [row for row in rows if str(row.get("instance_id")) in instance_ids]
        missing = instance_ids - {str(row.get("instance_id")) for row in selected}
        if missing:
            raise SystemExit(f"missing requested instance_id(s): {', '.join(sorted(missing))}")
        return selected
    return rows[:limit] if limit is not None else rows


def patch_paths(patch_text: str) -> list[str]:
    paths = set()
    for line in patch_text.splitlines():
        if not line.startswith("+++ "):
            continue
        path = line[4:].strip()
        if path == "/dev/null":
            continue
        if path.startswith("b/"):
            path = path[2:]
        paths.add(path)
    return sorted(paths)


def list_field(instance: dict, key: str) -> list[str]:
    value = instance.get(key) or []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                parsed = [stripped]
        value = parsed
    return [str(item) for item in value if str(item).strip()]


def pytest_commands(instance: dict) -> list[str]:
    tests = []
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS", "fail_to_pass", "pass_to_pass"):
        tests.extend(list_field(instance, key))
    if not tests:
        return ["python -m pytest -q"]
    return [f"python -m pytest -q {test}" for test in tests]


def ensure_repo_cache(repo: str, cache_root: Path) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / safe_name(repo)
    if cache_path.exists():
        run(["git", "fetch", "--tags", "--prune"], cwd=cache_path)
        return cache_path
    run(["git", "clone", f"https://github.com/{repo}.git", str(cache_path)])
    return cache_path


def materialize_workspace(instance: dict, workspace_root: Path, repo_cache: Path) -> Path:
    instance_id = str(instance["instance_id"])
    repo = str(instance["repo"])
    base_commit = str(instance["base_commit"])
    workspace = workspace_root / safe_name(instance_id)
    if workspace.exists():
        run(["git", "reset", "--hard"], cwd=workspace)
        run(["git", "clean", "-fdx"], cwd=workspace)
        run(["git", "checkout", base_commit], cwd=workspace)
    else:
        workspace_root.mkdir(parents=True, exist_ok=True)
        cache_path = ensure_repo_cache(repo, repo_cache)
        run(["git", "clone", str(cache_path), str(workspace)])
        run(["git", "checkout", base_commit], cwd=workspace)

    test_patch = str(instance.get("test_patch") or "")
    if test_patch.strip():
        run(["git", "apply", "--whitespace=nowarn", "-"], cwd=workspace, input_text=test_patch)
    return workspace


def task_from_instance(instance: dict, workspace: Path, timeout: int) -> dict:
    instance_id = str(instance["instance_id"])
    workspace = workspace if workspace.is_absolute() else REPO_ROOT / workspace
    protected = patch_paths(str(instance.get("test_patch") or ""))
    prompt = (
        "Fix this SWE-bench Lite bug so the test suite passes. "
        "You are already at the repository root with the benchmark test patch applied. "
        "Do not modify tests. Use the problem statement below, inspect the code, edit the implementation, "
        "and run the relevant pytest commands before your final answer.\n\n"
        f"Instance: {instance_id}\n\n"
        f"Problem statement:\n{instance.get('problem_statement', '').strip()}\n"
    )
    return {
        "id": f"swe_lite__{safe_name(instance_id)}",
        "source": "swebench_lite",
        "instance_id": instance_id,
        "repo": instance.get("repo"),
        "base_commit": instance.get("base_commit"),
        "workspace_source": str(workspace.resolve().relative_to(REPO_ROOT)),
        "prompt": prompt,
        "test_commands": pytest_commands(instance),
        "test_timeout_sec": timeout,
        "protected_paths": protected,
        "fail_to_pass": list_field(instance, "FAIL_TO_PASS") or list_field(instance, "fail_to_pass"),
        "pass_to_pass": list_field(instance, "PASS_TO_PASS") or list_field(instance, "pass_to_pass"),
    }


def write_jsonl(path: Path, rows: Iterable[dict], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode) as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances-jsonl", required=True, type=Path)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--repo-cache", type=Path, default=DEFAULT_REPO_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--test-timeout-sec", type=int, default=600)
    parser.add_argument("--append", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_instances(args.instances_jsonl)
    selected = select_instances(rows, set(args.instance_id), args.limit)
    tasks = []
    for instance in selected:
        workspace = materialize_workspace(instance, args.workspace_root, args.repo_cache)
        tasks.append(task_from_instance(instance, workspace, args.test_timeout_sec))
        print(f"prepared {instance['instance_id']} -> {workspace}")
    write_jsonl(args.out, tasks, append=args.append)
    print(f"wrote {len(tasks)} tasks to {args.out}")


if __name__ == "__main__":
    main()
