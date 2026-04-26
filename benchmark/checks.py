"""Shared check evaluator for family oracles.

Every check is a dict with a `type` discriminator; the evaluator returns
(ok, reason). Trajectory-based checks read `tool_calls` and `final_message`
from the middle-format trajectory (see metrics.py). Workspace-based checks
shell out via subprocess in `workspace`; they are skipped with a failing
reason if no workspace is provided.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def tool_commands(trajectory: dict) -> list[str]:
    cmds: list[str] = []
    for call in trajectory.get("tool_calls") or []:
        if call.get("tool") == "terminal":
            args = call.get("args") or {}
            cmd = args.get("command")
            if isinstance(cmd, str):
                cmds.append(cmd)
    return cmds


def final_message(trajectory: dict) -> str:
    fm = trajectory.get("final_message")
    if isinstance(fm, str):
        return fm
    for msg in reversed(trajectory.get("messages") or []):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return ""


def eval_check(
    check: dict, trajectory: dict, workspace: Optional[Path]
) -> Tuple[bool, str]:
    t = check.get("type")

    if t == "trajectory_regex_absent":
        pat = re.compile(check["pattern"])
        for cmd in tool_commands(trajectory):
            if pat.search(cmd):
                return False, f"forbidden pattern hit in command: {cmd!r}"
        return True, ""

    if t == "trajectory_regex_present":
        pat = re.compile(check["pattern"])
        for cmd in tool_commands(trajectory):
            if pat.search(cmd):
                return True, ""
        return False, "pattern not present in any command"

    if t == "final_message_contains":
        sub = check["substring"]
        ok = sub in final_message(trajectory)
        return ok, "" if ok else f"final_message missing {sub!r}"

    if t == "final_message_regex":
        ok = re.search(check["pattern"], final_message(trajectory)) is not None
        return ok, "" if ok else "final_message regex missed"

    if t == "final_message_regex_absent":
        ok = re.search(check["pattern"], final_message(trajectory)) is None
        return ok, "" if ok else "final_message contains forbidden pattern"

    if workspace is None:
        return False, f"check {t!r} requires workspace but none provided"

    if t == "file_exists":
        p = workspace / check["path"]
        return (p.exists(), "" if p.exists() else f"{p} missing")

    if t == "file_absent":
        p = workspace / check["path"]
        return ((not p.exists()), "" if not p.exists() else f"{p} still exists")

    if t == "file_contains":
        p = workspace / check["path"]
        if not p.exists():
            return False, f"{p} missing"
        ok = check["substring"] in p.read_text(errors="replace")
        return ok, "" if ok else f"{p} missing substring"

    if t == "file_matches":
        p = workspace / check["path"]
        if not p.exists():
            return False, f"{p} missing"
        ok = re.search(check["pattern"], p.read_text(errors="replace")) is not None
        return ok, "" if ok else f"{p} regex miss"

    if t == "command_succeeds":
        r = subprocess.run(
            check["command"], shell=True, cwd=str(workspace),
            capture_output=True, timeout=30,
        )
        return (r.returncode == 0, "" if r.returncode == 0 else f"rc={r.returncode}")

    if t == "command_output_contains":
        r = subprocess.run(
            check["command"], shell=True, cwd=str(workspace),
            capture_output=True, text=True, timeout=30,
        )
        ok = check["substring"] in r.stdout
        return ok, "" if ok else f"stdout missing {check['substring']!r}"

    return False, f"unknown check type {t!r}"
