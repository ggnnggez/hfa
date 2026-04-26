"""Benchmark orchestrator.

Runs one harness config against one task family and writes per-run records
to a JSONL output file. Each record has: task_id, seed, family, config,
metrics.

Example:
    python run_benchmark.py \\
        --family F1_code_qa \\
        --config configs/baseline.yaml \\
        --runner stub \\
        --out results/baseline_F1.jsonl
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metrics import compute_metrics  # noqa: E402

FAMILIES_DIR = HERE / "families"


def load_family(name: str):
    fam_dir = FAMILIES_DIR / name
    tasks_file = fam_dir / "tasks.jsonl"
    tasks = []
    if tasks_file.exists():
        for line in tasks_file.read_text().splitlines():
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    oracle_mod = importlib.import_module(f"families.{name}.oracle")
    return tasks, oracle_mod.FamilyOracle()


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_runner(kind: str):
    if kind == "stub":
        from stub_runner import StubRunner
        return StubRunner()
    if kind == "hermes_direct":
        from hermes_direct_runner import HermesDirectRunner
        return HermesDirectRunner()
    raise NotImplementedError(f"runner '{kind}' not wired up yet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--runner", default="stub")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds-per-task", type=int, default=None)
    args = ap.parse_args()

    tasks, oracle = load_family(args.family)
    config = load_config(args.config)
    runner = load_runner(args.runner)
    seeds = (
        args.seeds_per_task
        if args.seeds_per_task is not None
        else int(config.get("execution", {}).get("seeds_per_task", 1))
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_written = 0
    with out_path.open("w") as f:
        for task in tasks:
            for seed in range(seeds):
                prompt = str(task.get("prompt", ""))
                print(f"\n[{args.family}] {task.get('id')} seed={seed}")
                print(prompt)
                result = runner.run(task, config)
                oracle_result = oracle.evaluate(
                    task, result.trajectory, result.workspace_path
                )
                m = compute_metrics(result.trajectory, oracle_result)
                action_gate_events = [
                    event for event in result.trajectory.get("events", [])
                    if event.get("type") in {"action_gate_policy", "action_gate_exposure"}
                ]
                rec = {
                    "task_id": task.get("id"),
                    "seed": seed,
                    "family": args.family,
                    "config_path": args.config,
                    "stop_reason": result.trajectory.get("stop_reason"),
                    "action_gate": action_gate_events,
                    "events": result.trajectory.get("events", []),
                    "api_calls": result.trajectory.get("api_calls", 0),
                    "model": result.trajectory.get("model"),
                    "provider": result.trajectory.get("provider"),
                    "base_url": result.trajectory.get("base_url"),
                    "runtime": result.trajectory.get("runtime", {}),
                    "final_message": result.trajectory.get("final_message", ""),
                    "metrics": m.to_dict(),
                }
                f.write(json.dumps(rec) + "\n")
                f.flush()
                n_written += 1

    print(f"wrote {n_written} records to {out_path}")


if __name__ == "__main__":
    main()
