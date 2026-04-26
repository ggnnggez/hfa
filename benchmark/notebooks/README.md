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
jupyter lab benchmark/notebooks/f1_harness_ablation_viz.ipynb
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
```

Additional ablation outputs such as `f1_terminal_only.jsonl` will be picked up
automatically if their filename starts with `f1_` and ends with `.jsonl`.
