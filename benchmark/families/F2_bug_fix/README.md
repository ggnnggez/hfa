# F2 Bug Fix Tasks

F2 evaluates mutable bug-fix workflows:

```text
workspace_source -> copied temp workspace -> agent edits code -> oracle runs tests
```

## Local Fixtures

`tasks.jsonl` contains small synthetic Python fixtures used as a smoke test for
the F2 harness. They are intentionally simple and should stay cheap.

## SWE-bench Lite Subset

Use `scripts/prepare_swebench_lite_subset.py` with a local SWE-bench Lite JSONL
export to materialize real repositories under `benchmark/workspaces/` and write
task rows to `tasks_swebench_lite.jsonl`.

Example:

```bash
python scripts/prepare_swebench_lite_subset.py \
  --instances-jsonl /path/to/swe-bench-lite.jsonl \
  --limit 3
```

Then run:

```bash
uv run --project hermes_v-0-10-0 python -u benchmark/run_benchmark.py \
  --family F2_bug_fix \
  --tasks-file benchmark/families/F2_bug_fix/tasks_swebench_lite.jsonl \
  --config benchmark/configs/f2_swebench_lite_baseline.yaml \
  --runner hermes_direct \
  --out benchmark/results/f2_swebench_lite_baseline.jsonl
```

For `source: swebench_lite` tasks, the F2 oracle uses the official SWE-bench
evaluation harness. It converts the edited workspace into a `model_patch`,
excludes protected test files, and runs:

```bash
uv run --with swebench python -m swebench.harness.run_evaluation ...
```

This requires Docker with SWE-bench-compatible image support. Evaluation
artifacts are written under `benchmark/.cache/swebench_eval/`.

The script:

- clones each `repo` at `base_commit`
- applies `test_patch`
- writes task rows with `FAIL_TO_PASS` / `PASS_TO_PASS` pytest commands
- records `protected_paths` from the test patch so the oracle can fail runs that
  modify benchmark tests

Prepared workspaces are regenerable and ignored by git.
