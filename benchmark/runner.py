"""Pluggable agent-runner interface.

A Runner executes one (task, harness_config) pair and returns the raw
trajectory + an optional workspace path for post-hoc oracle inspection.

Implementations planned:
    - StubRunner         : canned trajectory, validates skeleton wiring.
    - HermesDirectRunner : invoke AIAgent from hermes_v-0-10-0/run_agent.py
                           in-process, translating harness_config into the
                           runtime knobs (IterationBudget, toolsets, hooks).
    - MiniSWERunner      : shell out to hermes_v-0-10-0/mini_swe_runner.py,
                           ingesting the emitted trajectory file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RunResult:
    trajectory: dict
    workspace_path: Optional[str] = None


class AgentRunner(Protocol):
    def run(self, task: dict, harness_config: dict) -> RunResult: ...
