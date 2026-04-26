"""Stub runner — canned trajectory, used to validate skeleton wiring.

Replace with HermesDirectRunner / MiniSWERunner once integration begins.
"""
from __future__ import annotations

from runner import RunResult


class StubRunner:
    def run(self, task: dict, harness_config: dict) -> RunResult:
        trajectory = {
            "started_at": 0.0,
            "ended_at": 0.0,
            "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
            "events": [],
            "stop_reason": "stub",
            "messages": [],
        }
        return RunResult(trajectory=trajectory, workspace_path=None)
