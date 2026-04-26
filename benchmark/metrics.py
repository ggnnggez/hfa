"""Q / L / C / R metric extraction.

Schema note: runners MUST emit trajectories with this middle-format:
    started_at, ended_at   : float epoch seconds
    usage                  : {input_tokens, output_tokens, reasoning_tokens}
    events                 : list of {type: "retry"|"fallback"|..., ...}
    stop_reason            : "iteration_budget_exhausted" | "turn_budget_exhausted"
                           | "completed" | ...
    messages               : raw message list (optional)
    tool_calls             : list of {tool, args, result}  (used by oracles)
    final_message          : str, last assistant text       (used by oracles)

The Hermes-native trajectory format (from/value pairs with tool_call XML) is
translated into this schema inside each Runner implementation, not here.
Keeping the transform at the Runner boundary isolates the benchmark layer
from Hermes internals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class RunMetrics:
    q_oracle_pass: Optional[bool]
    q_detail: dict = field(default_factory=dict)

    l_wall_clock_sec: Optional[float] = None

    c_input_tokens: int = 0
    c_output_tokens: int = 0
    c_reasoning_tokens: int = 0

    r_retry_count: int = 0
    r_fallback_count: int = 0
    r_iter_budget_hit: bool = False
    r_turn_budget_hit: bool = False
    r_stuck: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_cost(trajectory: dict) -> tuple[int, int, int]:
    usage = trajectory.get("usage") or {}
    return (
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
        int(usage.get("reasoning_tokens", 0) or 0),
    )


def _extract_latency(trajectory: dict) -> Optional[float]:
    started = trajectory.get("started_at")
    ended = trajectory.get("ended_at")
    if started is None or ended is None:
        return None
    return float(ended) - float(started)


def _extract_risk(trajectory: dict) -> dict:
    events = trajectory.get("events") or []
    stop = trajectory.get("stop_reason")
    return {
        "retry_count": sum(1 for e in events if e.get("type") == "retry"),
        "fallback_count": sum(1 for e in events if e.get("type") == "fallback"),
        "iter_budget_hit": stop == "iteration_budget_exhausted",
        "turn_budget_hit": stop == "turn_budget_exhausted",
    }


def compute_metrics(trajectory: dict, oracle_result: dict) -> RunMetrics:
    c_in, c_out, c_reason = _extract_cost(trajectory)
    risk = _extract_risk(trajectory)
    passed = oracle_result.get("passed")
    return RunMetrics(
        q_oracle_pass=passed,
        q_detail=oracle_result.get("detail", {}) or {},
        l_wall_clock_sec=_extract_latency(trajectory),
        c_input_tokens=c_in,
        c_output_tokens=c_out,
        c_reasoning_tokens=c_reason,
        r_retry_count=risk["retry_count"],
        r_fallback_count=risk["fallback_count"],
        r_iter_budget_hit=risk["iter_budget_hit"],
        r_turn_budget_hit=risk["turn_budget_hit"],
        r_stuck=risk["iter_budget_hit"] and passed is not True,
    )
