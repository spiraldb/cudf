# Vortex Q5 benchmarking

One script. No tracing, automatic builds, patching, manifests, or artifact-restoration setup.

## Run

From the cuDF repository root, with the intended Q5 executable already built:

```sh
python3 -B vortex-analyze-bench/bench.py \
  --binary cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH \
  --label before

# Make one agreed change and rebuild Q5, then:
python3 -B vortex-analyze-bench/bench.py \
  --binary cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH \
  --label after --compare vortex-analyze-bench/results/before.json
```

Defaults: **SF10, Vortex, warm cache, buffered I/O, GPU 0, 30 samples each for read and full Q5**. The script refuses a busy GPU or existing output names. It saves only `results/<label>.json` and `.log`, prints means/sample noise, and checks settings/input/result signatures when comparing. A whole process is limited to 210 seconds, including fixture setup. No automatic retries or extra runs.

Use `--samples` only if a different budget is agreed. Full Q5 already includes reads; never add read-only time to it. A small single-pair difference is not a proven improvement.

## Current executable

Vortex source is clean at **`93e2931adb`** (session-scoped function cache). The build-directory Q5 executable was deliberately rebuilt from that final source and is byte-identical to the repeatedly measured cache-only executable. No rejected experiment remains in it. For future source experiments, rebuild explicitly:

```sh
cmake --build cpp/build/ndsh-review-20260923-115320-on \
  --target NDSH_Q05_NVBENCH --parallel 8
```

Alternatively, the saved pre-split baseline requires no rebuild:

```sh
python3 -B vortex-analyze-bench/bench.py \
  --binary cpp/build/vortex-bench-history-20261008/runs/issue1-retention-20261008/.local/bin/NDSH_Q05_BASELINE \
  --label saved-baseline
```

Announce the hypothesis before each isolated attempt; GPU work must remain sequential. Reject non-improvements and confirm small gains against contemporaneous/reverse-order runs before retaining them.

## Notes and history

- [RESULTS.md](RESULTS.md): experiment decisions, four retained-cache comparisons, and historical result.
- [GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md): CPU/GPU-correlated gap inventory and trace limitations.
- Issues: [1 pinned retention](ISSUE_1_PINNED_RETENTION.md), [2 queued copy](ISSUE_2_QUEUED_COPY.md), [3 allocation](ISSUE_3_ALLOCATION.md), [4 function lookup/events](ISSUE_4_FUNCTION_LOOKUP.md), [5 executor progress](ISSUE_5_EXECUTOR_PROGRESS.md), [6 metadata roundtrip](ISSUE_6_METADATA_ROUNDTRIP.md).
- `analyze_idle_gaps.py`: optional read-only SQLite audit, e.g. `python3 -B vortex-analyze-bench/analyze_idle_gaps.py 1 3 8`. Uses the existing local archive; no profiling or GPU execution.
- [CONTEXT.md](CONTEXT.md): compact handoff and remaining scheduling question.
- `results/issue1-{before,after}.json`: preserved raw benchmark output.
- [retention-split.patch](retention-split.patch): parked candidate, relative to Vortex `f12cc07e3`.

The old tracing scripts, reports, manifests, recordings and saved executables were **moved, not deleted**, to `cpp/build/vortex-bench-history-20261008/`. That local history is ignored by Git and is not a supported execution framework. The script above does not import or depend on it; only the optional saved-baseline command refers to a historical executable. The separate offline audit reads the historical trace/analysis JSON; it does not run archived scripts.
