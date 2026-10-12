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

Vortex is clean at **`2db15c2083`**: the empty-projection/dead-primitive fix in `vortex-cuda/src/layout.rs`, committed with approval and signed off. All 14 layout tests passed, but Q5 has not been rebuilt for this fix. Parent `7d2dd32046` reverts the entire bounded-slot experiment and has the same tree as the retained function-cache baseline **`93e2931adb`**. Ask before further commits. The modest measured slot benefit did not justify the implementation complexity. Experiment commits, saved binaries and results remain preserved; the four uncommitted cleanup passes are saved in a named stash (see [CONTEXT.md](CONTEXT.md)). No hard reset or push.

The current executable was restored from `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`, SHA256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. Source-tree equality and binary hash were checked; no new build, tests or benchmark were run for the rollback. Worker-driven uploads ([issue 5](ISSUE_5_EXECUTOR_PROGRESS.md)), separate retirement streams ([issue 2](ISSUE_2_QUEUED_COPY.md)), and FSST metadata bypass ([issue 6](ISSUE_6_METADATA_ROUNDTRIP.md)) remain reverted. For future source experiments, rebuild explicitly:

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

- [STALL_DIAGNOSIS_20261009.md](STALL_DIAGNOSIS_20261009.md): latest diagnosis after reverting worker uploads. Newer Nsight, six light traces, three CPU-detail traces, and one continuous 30-sample capture. Most observed gaps precede submission; concrete host-read completion/wakeup sequences and unmarked CUPTI interference identified. No optimization changes; discuss next step first.
- [ANALYSIS_20261009.md](ANALYSIS_20261009.md): earlier CPU/GPU capture, whole-query accounting, raw scheduler wakeup analysis, and profiler-interference findings.
- `pipeline_timeline.py --trace <sqlite>` and `analyze_cpu_waits.py <sqlite>`: read-only offline analyses; commands are in that report. `capture_cpu_gpu.py` records the one-shot capture command and refuses existing outputs; discuss any new capture first.

- [RESULTS.md](RESULTS.md): experiment decisions, four retained-cache comparisons, and historical result.
- [GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md): CPU/GPU-correlated gap inventory and trace limitations.
- Issues: [1 pinned retention](ISSUE_1_PINNED_RETENTION.md), [2 queued copy](ISSUE_2_QUEUED_COPY.md), [3 allocation](ISSUE_3_ALLOCATION.md), [4 function lookup/events](ISSUE_4_FUNCTION_LOOKUP.md), [5 executor progress](ISSUE_5_EXECUTOR_PROGRESS.md), [6 metadata roundtrip](ISSUE_6_METADATA_ROUNDTRIP.md).
- `analyze_idle_gaps.py`: optional read-only SQLite audit, e.g. `python3 -B vortex-analyze-bench/analyze_idle_gaps.py 1 3 8`. Uses the existing local archive; no profiling or GPU execution.
- [CONTEXT.md](CONTEXT.md): compact handoff and remaining scheduling question.
- `results/issue1-{before,after}.json`: preserved raw benchmark output.
- [retention-split.patch](retention-split.patch): parked candidate, relative to Vortex `f12cc07e3`.

The old tracing scripts, reports, manifests, recordings and saved executables were **moved, not deleted**, to `cpp/build/vortex-bench-history-20261008/`. That local history is ignored by Git and is not a supported execution framework. The script above does not import or depend on it; only the optional saved-baseline command refers to a historical executable. The separate offline audit reads the historical trace/analysis JSON; it does not run archived scripts.
