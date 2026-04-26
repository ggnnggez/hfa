"""F1 code-QA oracle.

Read-only questions about the Hermes codebase. Each task's success_checks
are final_message_{contains,regex,regex_absent} assertions against the
agent's final answer. safety_checks forbid any mutating shell activity,
since this family is meant to be purely investigative.

Task shape:
    {
      "id": "f1_xxx",
      "prompt": "...",
      "repo_root": "hermes_v-0-10-0",       # relative to workspace mount
      "success_checks": [ {type: "final_message_contains", substring: "..."} ... ],
      "safety_checks":  [ {type: "trajectory_regex_absent", pattern: "..."} ... ]
    }
"""
from check_oracle import CheckDrivenOracle as FamilyOracle

__all__ = ["FamilyOracle"]
