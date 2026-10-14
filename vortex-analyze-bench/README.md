# Vortex Q5 benchmarking

Current state, latest Vortex vs Parquet numbers, insights and next steps:
[STATUS_20261014.md](STATUS_20261014.md).

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

Rebuild explicitly before measuring a source change:

```sh
cmake --build cpp/build/ndsh-review-20260923-115320-on \
  --target NDSH_Q05_NVBENCH --parallel 8
```

## Other files

- `pipeline_timeline.py`, `analyze_cpu_waits.py`, `analyze_idle_gaps.py`, `capture_cpu_gpu.py`:
  read-only offline analyses of Nsight Systems SQLite exports, from the earlier GPU idle-gap
  investigation.
- `*.patch`: archived, rejected experiments from that investigation (retention split,
  retirement streams, worker uploads, FSST metadata bypass).
