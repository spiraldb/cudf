# Vortex Q5 handoff — 2026-10-09

## Current state

- cuDF: `/home/ubuntu/cudf`, branch `ad/ndsh-vortex-benchmarks`; local CMake build still overrides Vortex with `/home/ubuntu/vortex`.
- Vortex: branch `ad/cuda-gap-investigation`, **clean at HEAD `2db15c2083`**, the approved empty-projection/dead-primitive fix in `vortex-cuda/src/layout.rs`. Parent rollback `7d2dd32046` reverted `7e433fe0f6`, `272f920cb5`, and `a4955b31ac` together; that parent's tree exactly matches function-cache baseline `93e2931adb`. Signed off, not pushed. Ask before committing any further changes. Retirement-stream prototype, Cargo override, worker-driven file uploads and FSST bypass remain reverted.
- Build: `cpp/build/ndsh-review-20260923-115320-on`. **Active Q5 restored from the saved function-cache binary** `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`, SHA256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. Restored after checking GPU idle; hash matches exactly. Build intermediates/other executables were not rebuilt and may still reflect the experiment; explicitly rebuild before using them.
- Native Linux aarch64 GH200, CUDA 13.1 / GCC 13. No outstanding GPU/build/profiling jobs at completion.
- The tracked cuDF Vortex pin was not changed. `VORTEX_INTEGRATION_CONTEXT.md` is user-owned and untouched.

## Current review work — empty projections / dead primitive branch (`2db15c2083`)

User approved implementing review items 2 and 7, then explicitly authorized committing the current changes. Vortex commit **`2db15c2083`** keeps the fix and regression tests together. Eager decoding now accepts only nonempty structs whose fields are primitive; dtype eligibility is checked once in the helper before CUDA execution-context creation. Removed the helper's primitive-decode path and redundant structural branch. Updated tests retain numeric pack/slice decoding coverage and verify empty structs (0 and 8 rows), raw encoded primitives, and mixed/string structs remain unchanged without initializing CUDA.

**14 layout tests passed, 0 failed/ignored**, release build with `_test-harness`, filter `layout::tests`. Initial test compile caught a temporary-borrow error in the simplified assertion; restored the child Arc clone and reran successfully. Pinned formatting and whitespace checks passed. Logs: `results/review-empty-projection-tests{,-fixed}.log`. No Q5 rebuild/benchmark or full suite; active Q5 is still the saved function-cache baseline, not the new layout-fix commit.

Review item 1 (statistics inheriting eager GPU decoding) is deferred to a holistic pruning task: no demonstrated correctness failure and current Q5 does not pass predicates into Vortex. CPU zone-map-only pruning before upload, while keeping exact per-row filtering in cuDF, is a separate task—not implemented here. Discuss each remaining review item first. **Do not commit without asking.**

## Latest decision — bounded staging slots rolled back

User rejected the complexity relative to the modest benefit. Commit **`7d2dd32046`** reverses all three slot commits as a unit, preserving the independently measured function cache. Verified `git diff 93e2931adb HEAD` is empty and working tree is clean; restored matching saved Q5. No new build/test/benchmark run was needed for this exact tree/binary restoration, and none is claimed.

Original commits remain ancestors in history; saved experiment executables/results are intact. Four cleanup passes are preserved in Vortex stash **`72cd1a7ca01fcfd12c6349e44b906c6e9df50f26`**, named `Preserved four staging-slot simplification passes before rollback 20261009` (initially `stash@{0}`). That patch is based on `7e433fe0f6`, not the restored cache baseline; do not apply it directly to current HEAD. No hard reset, new branch or push. The test-hook removal and compute-stream rename discussed afterward were not implemented.

The experiment sections below are historical, including their then-current retention decisions. Discuss the next strategy before new changes or profiling.

## Historical cleanup — four simplification passes (now stashed)

1. `prepare()` uses one local allocation/reuse result, retaining failed-buffer eviction without storing then immediately taking the buffer.
2. Stream batching uses `.fuse()` instead of a manual EOF flag; all length checks and batch limits remain.
3. Replaced custom temporary-file naming/cleanup with workspace `tempfile` as a dev-dependency.
4. Shared the identical memory-reader fixture across three tests; independent non-Tokio setup and every assertion remain.

Five Vortex files changed, net 25 fewer lines. **94 focused release tests passed, 0 failed/ignored**; pinned formatting and whitespace checks passed. Read-only review found no actionable semantic issue. Test log: `results/slots-simplify-tests-20261009.log`. No new Q5 build/benchmark or Clippy run; active/saved Q5 still represents committed `7e433fe0f6`, not the WIP. No commit or push for this cleanup.

## Historical review — FIFO admission and network batching (now reverted)

See [STAGING_SLOTS_REVIEW_20261009.md](STAGING_SLOTS_REVIEW_20261009.md). Commit **`7e433fe0f6`**, signed off, not pushed.

- File permit then FIFO pool await; removed passive availability/retry/wake-all mechanism. Accept same-file head-of-line blocking across size classes rather than starvation behind queued pool users.
- Network body slices batched up to 8 MiB / 4,096 pieces before staging acquisition; one blocking job/H2D per batch. No pinned slots held across network awaits. A 16 MiB/8 KiB-chunk regression verifies 2 staging jobs, not 2,048; real network throughput is unmeasured.
- Memory preparation/copy/device allocation/upload in one blocking worker; no unbounded inline fast path. Production generic test-injection closures removed; hooks are `#[cfg(test)]` only. DMA safety/failed-buffer eviction retained.
- **94 focused tests passed**, library Clippy and formatting/whitespace passed. Test Clippy blocked by six existing HashSet violations in untouched session/stream-pool tests. Release Q5 rebuild passed. Full suites/remote-network performance not run.
- Versus `272f920cb5`: read **−0.45%, +0.42%**; Q5 **+0.37%, +0.38%**. Small measured Q5 cost in both orders, not a speedup. Retained as requested correctness/structural repair. Raw `results/slots-fifo-{before,after}{,-reverse}-20261009.json` and logs.
- Earlier `272f920cb5` hardening versus `a4955b31ac`: Q5 −0.12%, −0.42%; treated as neutral, not an added gain. Its passive admission/per-chunk upload/inline memory designs are now superseded. Do not resurrect passive admission or alternating two-semaphore rollback (starvation/livelock).
- Current binary restored, GPU idle. Discuss before another experiment.

## Historical experiment — initial bounded staging slots (subsequently reverted)

User approved a simple bounded reusable-slot experiment after discussing mutex-free bookkeeping and allocation/readiness. See [ISSUE_1_PINNED_RETENTION.md](ISSUE_1_PINNED_RETENTION.md), attempt C. No new profiling.

- Replaced pool scan/bucket mutexes with per-size bounded queues and async semaphore admission, default 256 live slots per class. Lazy allocation; no recycler thread. Upload returns pending fence; next owner waits in existing blocking worker before host mutation. Semaphore/initialization internals and CUDA can still block; do not claim formal lock-freedom.
- Review fixes: explicit `Handle` in memory-reader constructor and CLI call; object-store checkout after `get_opts`; correct class/fence-state assertions; cancellation/one-slot tests; bounded `try_get` scan; stats at allocation attempt; formatting. File-permit-first admission and unconditional memory-worker hop retained deliberately. Same-file cross-class convoy and remote throughput remain limitations, not proven regressions or solved fairness.
- Ordinary 30-sample pairs: read **+0.26%, −0.95%, +0.73%**; Q5 **−1.47%, −2.53%, −0.74%**. Initial pair predates review fixes. **Final reviewed build** reverse/normal pairs average **Q5 −1.64%, read −0.11%**. Strict comparisons/reference checks pass. Modest gain; no proven gap closure or statistical significance.
- Release build passed. **42 focused pinned/read tests passed, none failed/ignored**, including explicit runtime without Tokio and DMA fencing without automatic tracking. First test compile had an invariant-assertion trait error, corrected before successful rerun. Formatting/whitespace passed. Full suite/CLI build not run.
- Commit **`a4955b31ac`**, signed off, not pushed. Current source/executable match retained candidate. Raw `results/slots-{before,after}{,-reverse,-confirm}-20261009.json` and logs. GPU idle at completion. Discuss before another experiment.

## Previous experiment — worker-driven file uploads (reverted)

Moved H2D submission into existing blocking file-read workers for small and chunked reads. Shared large destinations used a mutex only for managed copy submission; source/destination lifetime protections retained. Q5 release build passed; **11 focused file-reader tests passed, none ignored**, including upload progress without repolling the consumer and cancellation/error handling.

Three 30-sample comparisons: read **−0.09%, −1.12%, −0.20%**; Q5 **+0.52%, −1.49%, +0.68%**. All strict comparisons passed. User interrupted a fourth pair and requested reversion plus detailed stall attribution rather than more speculative fixes. The orphan benchmark exited; GPU process list empty before restoration. **Source and executable restored**, no commits. Patch/results: [ISSUE_5_EXECUTOR_PROGRESS.md](ISSUE_5_EXECUTOR_PROGRESS.md). No more experiments or profiling without discussing strategy first.

## Previous experiment — separate file-buffer retirement stream

User approved implementation and benchmarking. A temporary local cudarc API and Vortex wiring moved final file-buffer waits/free to one session-owned stream, keeping allocation/uploads unchanged and retaining allocation readiness and usage fences. Registry untouched. Initial build timed out at 240 seconds; approved retry with a 20-minute limit passed (Cargo 5m55s). Six focused cudarc retirement tests passed, zero failed/ignored; full Vortex/FFI suites were not run.

Two 30-sample comparisons: read **−0.52%, −0.11%**; Q5 **−0.79%, +0.48%**. Strict fixture/settings/result comparisons and benchmark reference/projection checks passed. **Rejected/reverted: no repeatable Q5 gain.** Cargo files restored, local dependency no longer referenced, baseline executable restored byte-for-byte. No profiler or commits. Patches/results: [ISSUE_2_QUEUED_COPY.md](ISSUE_2_QUEUED_COPY.md). Discuss the next strategy before further work.

## Previous experiment — unchecked device-native FSST metadata (2026-10-09)

User explicitly authorized bypassing FSST metadata readbacks/CPU validation for benchmarking, following read-only investigation of separate readback streams. The stream redesign was not implemented.

Retained device-backed symbols/lengths at deserialization and consumed them directly in both CUDA FSST decode paths. CPU fallback materialized lazily. No file-format changes; output-size/status and VarBinView length readbacks remained. Two ordinary 30-sample comparisons: Q5 **+2.02%, +1.02%** (slower); read **+0.02%, −1.24%**. Inputs and result signatures matched. Release build and benchmark reference/projection checks passed; added focused tests were not run after performance rejection. No profiler used.

**Rejected/reverted**, no commit. Vortex is clean; baseline executable restored as above. Details and archived benchmark-only patch: [ISSUE_6_METADATA_ROUNDTRIP.md](ISSUE_6_METADATA_ROUNDTRIP.md), [fsst-device-metadata-bypass.patch](fsst-device-metadata-bypass.patch). Do not retain the unchecked path for production. Discuss next strategy before further changes/runs.

## Latest diagnosis after worker-upload reversion (2026-10-09)

See [STALL_DIAGNOSIS_20261009.md](STALL_DIAGNOSIS_20261009.md) for commands, timestamps, source paths and limitations. User approved diagnosis, not another optimization. **Discuss before further captures or implementation.**

- Locally extracted Nsight 2026.3.2 under `cpp/build/vortex-stall-diagnosis-20261009/`; no system installation or driver change. Driver-mismatch warning gone; software tracing and possible incomplete-event warnings remain.
- Six light CUDA/NVTX traces (`q5-light.{1..6}`): read envelope 16.920–17.404 ms, no recorded GPU work 6.222–6.884 ms. Most later no-work precedes GPU-work API entry; only 7.64% has some future read-work API already returned. Old approximately 2 ms queued-copy spike absent.
- Three CPU-detail traces (`q5-cpu.{1..3}`): stacks establish pinned-retention locking, driver locks in callback registration/event destruction, and allocation work. Heavy profiler interference prevents ordinary-run duration attribution.
- One continuous capture (`q5-continuous`), no OSRT or repeated profiler stops: 30 normal samples, mean full Q5 29.437 ms versus ordinary approximately 21 ms. Last 30 six-read clusters selected; earlier fixture validation/warmup excluded. Capture spans approximately 100 seconds because trigger also matched early validation.
- Two concrete internal gaps, 1.337/1.309 ms: batches of host preads still executing; first read completion followed by an exact worker→submitter wake and H2D. Same fd/offset recurs. Readers remain scheduled during pread; this includes buffered CPU staging copies, not necessarily disk waiting. Exact pread-buffer→H2D identity is still missing.
- A separate 1.682 ms event-operation gap contains sustained CUPTI samples despite no explicit profiler-overhead record. Another 1.128 ms sleeping gap has unknown dependency; do not label it pinned-pool or disk waiting from names alone.
- Input/result signatures passed for all captures. Vortex remains clean at `93e2931adb`; baseline binary hash unchanged. No rebuilds/source edits/commits/pushes. GPU process list empty at completion.
- Next discussion: host-read/staging readiness is better supported than another stream-only redesign. Either add minimal request identity if necessary, or agree on one general read-pipeline hypothesis and benchmark normally. Do not claim captured idle time is recoverable savings.

## Earlier analysis (2026-10-09)

User explicitly requested one further analysis capture, with strategy discussed first. See [ANALYSIS_20261009.md](ANALYSIS_20261009.md). No source/optimization changes, benchmark comparison, commit or push in this analysis pass.

- One current-build Nsight capture added CPU samples, raw scheduler wakeups, file-access events and CUDA event completions. Raw data: `cpp/build/vortex-analysis-20261009/q5-cpu-gpu.{nsys-rep,sqlite}`.
- Read/prepare envelope 25.719 ms of a 33.282 ms instrumented API timer bracket. Lineitem is last, with a 5.389 ms sole-reader tail; code joins all inputs before Q5 operators.
- Raw ftrace now distinguishes blocking from runnable delay: 903 read-thread wake cycles, median wakeup→run 1.248 µs, p95 2.112 µs, maximum 12.928 µs. This does not measure async-task readiness.
- Largest 2.166 ms no-work interval mostly follows the next H2D API's return. Matched device waits do not explain it. A driver-side worker that woke the application spends 1.679 ms in explicit profiler `TLS allocation` overhead, ending 8.096 µs before H2D resumes. Strong instrumentation interference, not proven full causality or subtractable overhead.
- 938 recorded CUDA events have timestamps; 669 nonzero-generation waits match uniquely. Some small gaps have actual outstanding retirement/handoff constraints; 375 gen0 waits remain unmatched.
- Driver/tracer incompatibility and incomplete-event warnings remain. Event tracing can induce false dependencies. CPU samples are sparse (39 during reads) and omit pread workers. Request/buffer identity and exact lock owners remain unavailable.
- Next discussion should address supported/warmed tracing and minimal missing readiness identity, **not another blind optimization**. Do not start another capture or instrumentation without discussing the strategy first.

## Prior optimization results

See [RESULTS.md](RESULTS.md) and the six `ISSUE_*.md` files. Eight candidates were assessed across four actionable issues; two other issues are explicitly blocked, not fixed.

- **Retained:** CUDA function cache, Vortex commit `93e2931adb`. Four comparisons show Q5 changes −1.37%, −1.25%, −0.08%, −0.89%; mean paired change **−0.90%**, read **−0.19%**. Small measured benefit, not a major scheduling fix or proof of GPU-gap closure.
- **Reverted:** detached pinned reclamation; size-targeted reclamation; unused default strategy-event bypass; yield-after-every-read; bounded/refill-first yields; callback-free synchronous D2H; host-inlined FSST symbols/lengths.
- **Blocked:** already-submitted copy dependencies have missing event generations; allocation/driver behavior has no defensible surgical fix. The cuDF benchmark **already sets the current CUDA pool retention threshold to 8 GiB**.
- Original ≥30% full-Q5 reduction goal remains unmet. No unproven optimization was bundled with the retained cache.

All ordinary runs: SF10 / warm / buffered / GPU 0, 30 samples each read/full Q5, sequential idle checks. Full Q5 includes reads. Input/result signatures and benchmark reference/projection checks passed. FSST writer experiment increased fixture size by 6,672 bytes; it was compared explicitly on matching logical results and reverted after reverse-order confirmation failed.

Validation of retained change: Q5 release build; one targeted actual CUDA kernel test, covering repeated lookup, failed lookup not cached, and handle lifetime after loader destruction. Full Rust/FFI suites not run. Rejected candidates were not given extra test matrices.

## Previous capture's CPU + GPU evidence

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md) documents the ten largest intervals with no **recorded kernels, copies, or memsets**, their actual gap-ending operation, API submitter, OSRT stacks and observed on/off-CPU slices. Uses the existing scheduler SQLite, **no new capture/perf run/custom instrumentation**.

- Read envelope: 218 intervals, 15.104704 ms without recorded work.
- Decoder envelope: 12.508480 ms without recorded work; nested inside read, not additive.
- Strongest producer evidence: 1.900064 ms gap, next H2D submitter mostly off-CPU inside pinned-retention mutex.
- Different category: 1.330560 ms gap after its ending H2D was already submitted.
- Unknown scheduler states do not establish readiness or wake-to-run latency. Unsupported driver/tracer combination and incomplete-event warnings prevent claims of loss-free capture or guaranteed physical idleness. Profile timings are not savings.

## Workflow

Keep `bench.py` simple. One isolated change, bounded build/measurement, revert failures, confirm small gains with contemporaneous/reverse-order comparisons, commit only retained improvements. Announce reasoning before each attempt. No new decoded cache, production tuning knobs, weakened DMA/device fences, or destructive reset. Raw profiler history remains archived; do not resurrect its execution framework.

Saved clean base: `cpp/build/vortex-bench-history-20261008/runs/issue1-retention-20261008/.local/bin/NDSH_Q05_BASELINE`.
Saved cache-only baseline binary: `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`.
Saved historical FIFO/batched binary: `cpp/build/NDSH_Q05_BOUNDED_SLOTS_FIFO_20261009`; it is no longer the active executable. Earlier slot/hardening binaries remain saved as `NDSH_Q05_BOUNDED_SLOTS_20261009` and `NDSH_Q05_BOUNDED_SLOTS_HARDENED_20261009` in the same directory.
Existing trace: `cpp/build/vortex-bench-history-20261008/runs/ndsh-scheduler-gaps-20261008/.local/q5-scheduler-01.sqlite`.

No automatic next run is in progress. The latest stall report adds read-completion/wakeup sequences and newer-profiler evidence, but does not fully solve request ownership or ordinary-run causal attribution. Keep the next step evidence-driven and discuss it before execution.
