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
import signal
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metrics import compute_metrics  # noqa: E402

FAMILIES_DIR = HERE / "families"


class BenchmarkRunInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum
        self.signal_name = signal.Signals(signum).name
        super().__init__(self.signal_name)


class SignalController:
    def __init__(self):
        self.current_task: dict | None = None
        self.current_seed: int | None = None
        self.current_started_at: float | None = None
        self._prompting = False
        self._previous_handlers: dict[int, object] = {}

    def install(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def restore(self):
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)

    def set_current(self, task: dict | None, seed: int | None, started_at: float | None):
        self.current_task = task
        self.current_seed = seed
        self.current_started_at = started_at

    def _handle_signal(self, signum: int, _frame):
        signal_name = signal.Signals(signum).name
        if self.current_task is None:
            raise SystemExit(f"received {signal_name}")
        if self._prompting:
            raise BenchmarkRunInterrupted(signum)

        task_id = self.current_task.get("id")
        self._prompting = True
        try:
            sys.__stdout__.write(
                f"\nReceived {signal_name} while task {task_id} seed={self.current_seed} is running.\n"
                "Stop this task and write an interrupted JSONL record? [y/N] "
            )
            sys.__stdout__.flush()
            answer = sys.__stdin__.readline().strip().lower()
        finally:
            self._prompting = False

        if answer in {"y", "yes"}:
            raise BenchmarkRunInterrupted(signum)
        sys.__stdout__.write("Continuing current task.\n")
        sys.__stdout__.flush()


def load_family(name: str, tasks_path: str | None = None):
    fam_dir = FAMILIES_DIR / name
    tasks_file = Path(tasks_path) if tasks_path else fam_dir / "tasks.jsonl"
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


def record_from_result(
    task: dict,
    seed: int,
    family: str,
    config_path: str,
    result,
    oracle_result: dict,
) -> dict:
    action_gate_events = [
        event for event in result.trajectory.get("events", [])
        if event.get("type") in {"action_gate_policy", "action_gate_exposure"}
    ]
    m = compute_metrics(result.trajectory, oracle_result)
    return {
        "task_id": task.get("id"),
        "seed": seed,
        "family": family,
        "config_path": config_path,
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


def interrupted_record(
    task: dict,
    seed: int,
    family: str,
    config_path: str,
    started_at: float | None,
    interruption: BenchmarkRunInterrupted,
) -> dict:
    ended_at = time.time()
    trajectory = {
        "started_at": started_at or ended_at,
        "ended_at": ended_at,
        "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
        "events": [{
            "type": "signal_interrupted",
            "signal": interruption.signal_name,
            "signum": interruption.signum,
            "message": (
                f"Task {task.get('id')} seed={seed} stopped after receiving "
                f"{interruption.signal_name} and user confirmation."
            ),
        }],
        "stop_reason": "signal_interrupted",
        "messages": [],
        "tool_calls": [],
        "final_message": (
            f"[signal_interrupted] task {task.get('id')} seed={seed} stopped "
            f"after receiving {interruption.signal_name}"
        ),
        "api_calls": 0,
        "model": None,
        "provider": None,
        "base_url": None,
        "runtime": {},
    }
    oracle_result = {
        "passed": False,
        "detail": {
            "reason": "signal_interrupted",
            "signal": interruption.signal_name,
            "signum": interruption.signum,
        },
    }
    class _InterruptedResult:
        def __init__(self, trajectory):
            self.trajectory = trajectory

    return record_from_result(
        task,
        seed,
        family,
        config_path,
        _InterruptedResult(trajectory),
        oracle_result,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--runner", default="stub")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds-per-task", type=int, default=None)
    ap.add_argument(
        "--tasks-file",
        default=None,
        help="Optional JSONL task file override for this family.",
    )
    ap.add_argument(
        "--live-log",
        action="store_true",
        help="Stream captured agent and oracle output to the terminal while still recording it.",
    )
    args = ap.parse_args()

    if args.live_log:
        import os
        os.environ["HFA_BENCH_LIVE_LOG"] = "1"

    tasks, oracle = load_family(args.family, args.tasks_file)
    config = load_config(args.config)
    runner = load_runner(args.runner)
    if not tasks:
        source = args.tasks_file or str(FAMILIES_DIR / args.family / "tasks.jsonl")
        raise SystemExit(f"no tasks loaded from {source}")
    seeds = (
        args.seeds_per_task
        if args.seeds_per_task is not None
        else int(config.get("execution", {}).get("seeds_per_task", 1))
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_written = 0
    signals = SignalController()
    signals.install()
    with out_path.open("w") as f:
        try:
            for task in tasks:
                for seed in range(seeds):
                    prompt = str(task.get("prompt", ""))
                    started_at = time.time()
                    signals.set_current(task, seed, started_at)
                    print(f"\n[{args.family}] {task.get('id')} seed={seed}")
                    print(prompt)
                    try:
                        result = runner.run(task, config)
                        oracle_result = oracle.evaluate(
                            task, result.trajectory, result.workspace_path
                        )
                        rec = record_from_result(
                            task, seed, args.family, args.config, result, oracle_result
                        )
                    except BenchmarkRunInterrupted as interruption:
                        rec = interrupted_record(
                            task, seed, args.family, args.config, started_at, interruption
                        )
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                        n_written += 1
                        print(
                            f"stopped after {interruption.signal_name}; wrote interrupted record to {out_path}"
                        )
                        raise SystemExit(130 if interruption.signum == signal.SIGINT else 143)
                    finally:
                        signals.set_current(None, None, None)
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    n_written += 1
        finally:
            signals.restore()

    print(f"wrote {n_written} records to {out_path}")


if __name__ == "__main__":
    main()
