"""Shared oracle interface.

Each task family ships an Oracle implementation in families/<F*>/oracle.py
that scores a single (task, trajectory) pair into an OracleResult.
"""
from __future__ import annotations

from typing import Optional, Protocol, TypedDict


class OracleResult(TypedDict, total=False):
    passed: Optional[bool]
    detail: dict


class Oracle(Protocol):
    def evaluate(
        self,
        task: dict,
        trajectory: dict,
        workspace_path: Optional[str],
    ) -> OracleResult: ...
