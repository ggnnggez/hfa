"""F2 single-file bug-fix oracle (SWE-bench Lite).

Task shape (draft, matches SWE-bench instance_id + test plan):
    {
      "id": "astropy__astropy-12345",
      "instance_id": "astropy__astropy-12345",
      "repo": "astropy/astropy",
      "base_commit": "...",
      "fail_to_pass": [...],
      "pass_to_pass": [...]
    }

Oracle runs FAIL_TO_PASS and PASS_TO_PASS tests in the workspace_path and
reports pass/fail. Not yet implemented.
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
