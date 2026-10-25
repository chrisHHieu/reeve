# Running benchmarks

Measure a MODEL over reeve's fixed, minimal harness — a minimal mode
(`bash + editor`, approvals bypassed, deterministic checkers): what varies between runs
is the model, never the harness.

The tasks live in [`benchmarks/tasks/`](benchmarks/tasks) (`<id>/task.json`: a prompt,
setup files, and a checker that never consults a model — `pytest`, `file_equals`,
`file_contains`, `json_matches`). The harness (`harness/observability/bench.py`) runs each task in a
throwaway workspace through the same TurnEngine the app uses.

## Run

```sh
python -m benchmarks.run                                  # every task, app-configured provider
python -m benchmarks.run --model anthropic:claude-sonnet-4 --tasks hello-file,fix-off-by-one
python -m benchmarks.run --max-iterations 10 --out bench-results
```

The provider is your app's own configuration (the local secret store), so a run needs
whatever keys the app already has. Output is a per-task PASS/FAIL line and a summary;
`--out` writes `results.json` for comparing models side by side. Exit code is 0 only
when everything passed.

## Adding a task

Make `benchmarks/tasks/<id>/task.json` with `prompt`, `setup` (relative path → file
contents, written fresh per run) and `check`. A check reads only what the run left on
disk — a task whose pass/fail needs a judgement call is not a benchmark task, it is an
opinion.

The harness itself is covered by `tests/test_bench_harness.py` (scripted provider, no
network); real runs are never invoked by tests.
