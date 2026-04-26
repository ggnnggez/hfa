"""F3 multi-file refactor oracle (SWE-bench Full, long-trajectory subset).

Same shape as F2 but selected for diffs that touch multiple files. Oracle
still hinges on FAIL_TO_PASS / PASS_TO_PASS. Not yet implemented.
"""
from __future__ import annotations

from typing import Optional

from oracle_base import OracleResult


class FamilyOracle:
    def evaluate(
        self,
        task: dict,
        trajectory: dict,
        workspace_path: Optional[str],
    ) -> OracleResult:
        return {"passed": None, "detail": {"reason": "oracle not implemented"}}
