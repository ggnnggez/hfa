#!/usr/bin/env python3
"""Aggregate benchmark JSONL result files.

Reads records emitted by benchmark/run_benchmark.py and summarizes them by
(family, config). Defaults to benchmark/results/*.jsonl.
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from typing import Iterable


DEFAULT_GLOB = "benchmark/results/*.jsonl"


def percentile(values: list[float], pct: float) -> float | None:
    """Return a linearly interpolated percentile for sorted or unsorted values."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def config_name(config_path: str | None) -> str:
    if not config_path:
        return "<unknown>"
    return Path(config_path).stem


def iter_records(paths: Iterable[Path]) -> Iterable[tuple[Path, int, dict]]:
    for path in paths:
        with path.open() as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield path, line_no, json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def collect(paths: Iterable[Path]) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for path, _line_no, rec in iter_records(paths):
        family = rec.get("family") or "<unknown>"
        config = config_name(rec.get("config_path"))
        key = (family, config)
        group = groups.setdefault(
            key,
            {
                "family": family,
                "config": config,
                "files": set(),
                "runs": 0,
                "passes": 0,
                "latency": [],
                "input_tokens": [],
                "output_tokens": [],
                "reasoning_tokens": [],
                "api_calls": [],
                "retry_count": 0,
                "fallback_count": 0,
                "iter_budget_hits": 0,
                "turn_budget_hits": 0,
                "stuck": 0,
                "stop_reasons": Counter(),
            },
        )

        metrics = rec.get("metrics") or {}
        group["files"].add(str(path))
        group["runs"] += 1
        if metrics.get("q_oracle_pass") is True:
            group["passes"] += 1

        latency = metrics.get("l_wall_clock_sec")
        if latency is not None:
            group["latency"].append(float(latency))
        group["input_tokens"].append(float(metrics.get("c_input_tokens", 0) or 0))
        group["output_tokens"].append(float(metrics.get("c_output_tokens", 0) or 0))
        group["reasoning_tokens"].append(float(metrics.get("c_reasoning_tokens", 0) or 0))
        group["api_calls"].append(float(rec.get("api_calls", 0) or 0))
        group["retry_count"] += int(metrics.get("r_retry_count", 0) or 0)
        group["fallback_count"] += int(metrics.get("r_fallback_count", 0) or 0)
        group["iter_budget_hits"] += int(bool(metrics.get("r_iter_budget_hit")))
        group["turn_budget_hits"] += int(bool(metrics.get("r_turn_budget_hit")))
        group["stuck"] += int(bool(metrics.get("r_stuck")))
        group["stop_reasons"][rec.get("stop_reason") or "<missing>"] += 1

    rows = []
    for group in groups.values():
        runs = group["runs"]
        rows.append(
            {
                "family": group["family"],
                "config": group["config"],
                "runs": runs,
                "pass_rate": group["passes"] / runs if runs else 0.0,
                "passes": group["passes"],
                "latency_mean": mean(group["latency"]),
                "latency_p50": percentile(group["latency"], 0.50),
                "latency_p95": percentile(group["latency"], 0.95),
                "latency_max": max(group["latency"]) if group["latency"] else None,
                "input_tokens_mean": mean(group["input_tokens"]),
                "output_tokens_mean": mean(group["output_tokens"]),
                "reasoning_tokens_mean": mean(group["reasoning_tokens"]),
                "api_calls_mean": mean(group["api_calls"]),
                "retry_count": group["retry_count"],
                "fallback_count": group["fallback_count"],
                "iter_budget_hits": group["iter_budget_hits"],
                "turn_budget_hits": group["turn_budget_hits"],
                "stuck": group["stuck"],
                "stop_reasons": dict(sorted(group["stop_reasons"].items())),
                "files": sorted(group["files"]),
            }
        )

    return sorted(rows, key=lambda row: (row["family"], row["config"]))


def fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, dict):
        return ", ".join(f"{key}:{value[key]}" for key in sorted(value))
    return str(value)


def print_table(rows: list[dict]) -> None:
    columns = [
        "family",
        "config",
        "runs",
        "passes",
        "pass_rate",
        "latency_p50",
        "latency_p95",
        "latency_max",
        "input_tokens_mean",
        "output_tokens_mean",
        "api_calls_mean",
        "retry_count",
        "fallback_count",
        "iter_budget_hits",
        "turn_budget_hits",
        "stuck",
        "stop_reasons",
    ]
    rendered = [[fmt(row[col]) for col in columns] for row in rows]
    widths = [
        max([len(column), *(len(record[idx]) for record in rendered)])
        for idx, column in enumerate(columns)
    ]
    print("  ".join(column.ljust(widths[idx]) for idx, column in enumerate(columns)))
    print("  ".join("-" * widths[idx] for idx in range(len(columns))))
    for record in rendered:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(record)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=f"JSONL result files. Defaults to {DEFAULT_GLOB}.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = args.paths or [Path(path) for path in sorted(glob.glob(DEFAULT_GLOB))]
    if not paths:
        raise SystemExit(f"no result files matched {DEFAULT_GLOB}")

    rows = collect(paths)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
