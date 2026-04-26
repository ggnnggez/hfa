"""Reusable check-driven oracle.

For families whose tasks fully describe Q/safety via `success_checks` and
`safety_checks` (F1 and F4 so far), the oracle just runs each check through
the shared evaluator in checks.py. F2/F3 will need more — they must execute
test suites — so they get their own oracles.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from checks import eval_check
from oracle_base import OracleResult


class CheckDrivenOracle:
    def evaluate(
        self,
        task: dict,
        trajectory: dict,
        workspace_path: Optional[str],
    ) -> OracleResult:
        workspace = Path(workspace_path) if workspace_path else None
        detail: dict = {"success": [], "safety": []}
        all_ok = True

        for chk in task.get("success_checks", []):
            ok, reason = eval_check(chk, trajectory, workspace)
            detail["success"].append({"check": chk, "ok": ok, "reason": reason})
            if not ok:
                all_ok = False

        for chk in task.get("safety_checks", []):
            ok, reason = eval_check(chk, trajectory, workspace)
            detail["safety"].append({"check": chk, "ok": ok, "reason": reason})
            if not ok:
                all_ok = False

        return {"passed": all_ok, "detail": detail}
