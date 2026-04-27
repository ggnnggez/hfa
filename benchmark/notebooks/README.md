# Benchmark Visualization Notebooks

Notebook dependencies are intentionally isolated from the Hermes checkout and
from the benchmark runner itself.

## Setup

Use a local virtual environment:

```bash
cd /home/nan/c_project/hfa
python -m venv .venv-viz
. .venv-viz/bin/activate
python -m pip install -r benchmark/requirements-viz.txt
```

Then start Jupyter:

```bash
jupyter lab benchmark/notebooks/f1_action_gating_report.ipynb
```

## Inputs

The notebook reads:

```text
benchmark/results/f1_*.jsonl
```

Current expected files:

```text
benchmark/results/f1_baseline.jsonl
benchmark/results/f1_file_only.jsonl
benchmark/results/f1_terminal_only.jsonl
```

The two-axis report also expects these Transition Control ablation outputs once
they have been run:

```text
benchmark/results/f1_transition_timeout_20.jsonl
benchmark/results/f1_terminal_timeout_20.jsonl
```

Additional ablation outputs will be picked up automatically if their filename
starts with `f1_` and ends with `.jsonl`.

## Reports

```text
f1_action_gating_report.ipynb      # baseline / file_only / terminal_only
f1_two_axis_ablation_report.ipynb  # action gating x terminal timeout
f1_harness_ablation_viz.ipynb      # older pandas/matplotlib exploratory viz
```
