# Vortex Q5 handoff — 2026-10-08

## Current state

- cuDF: `/home/ubuntu/cudf`, branch `ad/ndsh-vortex-benchmarks`; local CMake build still overrides Vortex with `/home/ubuntu/vortex`.
- Vortex: branch `ad/cuda-gap-investigation`, **clean at `93e2931adb449255ec835191d894b1933ffa05f3`**. This session added only a session-scoped CUDA function cache to base `f12cc07e326f5921ad7cff0899490ae57fbc5f4f`. Commit is signed off, not pushed.
- Build: `cpp/build/ndsh-review-20260923-115320-on`. **Q5 was rebuilt from the final committed source**, and its SHA-256 exactly matches the repeatedly measured cache-only executable: `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. No stale rejected experiment remains in this executable. Other executables were not rebuilt.
- Native Linux aarch64 GH200, CUDA 13.1 / GCC 13. No outstanding GPU/build/profiling jobs at completion.
- The tracked cuDF Vortex pin was not changed. `VORTEX_INTEGRATION_CONTEXT.md` is user-owned and untouched.

## Results

See [RESULTS.md](RESULTS.md) and the six `ISSUE_*.md` files. Eight candidates were assessed across four actionable issues; two other issues are explicitly blocked, not fixed.

- **Retained:** CUDA function cache, Vortex commit `93e2931adb`. Four comparisons show Q5 changes −1.37%, −1.25%, −0.08%, −0.89%; mean paired change **−0.90%**, read **−0.19%**. Small measured benefit, not a major scheduling fix or proof of GPU-gap closure.
- **Reverted:** detached pinned reclamation; size-targeted reclamation; unused default strategy-event bypass; yield-after-every-read; bounded/refill-first yields; callback-free synchronous D2H; host-inlined FSST symbols/lengths.
- **Blocked:** already-submitted copy dependencies have missing event generations; allocation/driver behavior has no defensible surgical fix. The cuDF benchmark **already sets the current CUDA pool retention threshold to 8 GiB**.
- Original ≥30% full-Q5 reduction goal remains unmet. No unproven optimization was bundled with the retained cache.

All ordinary runs: SF10 / warm / buffered / GPU 0, 30 samples each read/full Q5, sequential idle checks. Full Q5 includes reads. Input/result signatures and benchmark reference/projection checks passed. FSST writer experiment increased fixture size by 6,672 bytes; it was compared explicitly on matching logical results and reverted after reverse-order confirmation failed.

Validation of retained change: Q5 release build; one targeted actual CUDA kernel test, covering repeated lookup, failed lookup not cached, and handle lifetime after loader destruction. Full Rust/FFI suites not run. Rejected candidates were not given extra test matrices.

## CPU + GPU evidence

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md) documents the ten largest intervals with no **recorded kernels, copies, or memsets**, their actual gap-ending operation, API submitter, OSRT stacks and observed on/off-CPU slices. Uses the existing scheduler SQLite, **no new capture/perf run/custom instrumentation**.

- Read envelope: 218 intervals, 15.104704 ms without recorded work.
- Decoder envelope: 12.508480 ms without recorded work; nested inside read, not additive.
- Strongest producer evidence: 1.900064 ms gap, next H2D submitter mostly off-CPU inside pinned-retention mutex.
- Different category: 1.330560 ms gap after its ending H2D was already submitted.
- Unknown scheduler states do not establish readiness or wake-to-run latency. Unsupported driver/tracer combination and incomplete-event warnings prevent claims of loss-free capture or guaranteed physical idleness. Profile timings are not savings.

## Workflow

Keep `bench.py` simple. One isolated change, bounded build/measurement, revert failures, confirm small gains with contemporaneous/reverse-order comparisons, commit only retained improvements. Announce reasoning before each attempt. No new decoded cache, production tuning knobs, weakened DMA/device fences, or destructive reset. Raw profiler history remains archived; do not resurrect its execution framework.

Saved clean base: `cpp/build/vortex-bench-history-20261008/runs/issue1-retention-20261008/.local/bin/NDSH_Q05_BASELINE`.
Saved retained cache binary: `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`.
Existing trace: `cpp/build/vortex-bench-history-20261008/runs/ndsh-scheduler-gaps-20261008/.local/q5-scheduler-01.sqlite`.

No automatic next run is in progress. Next substantial scheduling/dependency work needs evidence of task readiness or valid device dependency generations, rather than another blind mutex/yield tweak.
