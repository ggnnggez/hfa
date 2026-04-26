"""F4 high-risk shell-execution oracle.

Tasks ship their success_checks and safety_checks inline; evaluation uses
the shared CheckDrivenOracle. The check vocabulary is documented in
checks.py.

Task shape:
    {
      "id": "f4_xxx",
      "prompt": "...",
      "setup": ["shell cmd", ...],          # run before agent starts
      "success_checks": [ {type: ...}, ...],
      "safety_checks":  [ {type: ...}, ...]
    }
"""
from check_oracle import CheckDrivenOracle as FamilyOracle

__all__ = ["FamilyOracle"]
