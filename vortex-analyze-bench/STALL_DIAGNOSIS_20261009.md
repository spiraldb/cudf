# GPU stall diagnosis — 2026-10-09

## Decision

**No optimization change.** Latest worker-upload experiment remains reverted. This pass separates observable stalls from profiler-created delays; it does not claim a speedup or complete causal attribution.

The strongest conclusions are:

1. **Most observed no-work time precedes GPU-work submission**, rather than following an already-submitted copy. Changing CUDA streams alone cannot make an unfinished host read ready.
2. **Some substantial gaps coincide with batches of host reads still executing.** Two concrete read-completion → worker wakeup → submitter → H2D sequences are below. This is stronger evidence than guessing from a CUDA timeline, but does not identify the exact transferred buffer.
3. **Profiler interference remains substantial even with a newer compatible Nsight version.** Some large gaps contain sustained CUPTI execution that is not marked in the explicit overhead table.
4. Other observed paths include pinned-pool locking, CUDA-driver locks during callback/event operations, and device allocation. Their ordinary-run contributions remain unquantified. Previous failed optimizations are not evidence that these paths are optimal.

## Baseline and collection

- Vortex branch `ad/cuda-gap-investigation`, clean HEAD `93e2931adb449255ec835191d894b1933ffa05f3`.
- cuDF branch `ad/ndsh-vortex-benchmarks`; CMake local Vortex override unchanged.
- Binary: `cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH`.
- SHA256: `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`.
- No rebuilds, source edits, commits, pushes, or changes to `VORTEX_INTEGRATION_CONTEXT.md`.
- GH200; driver 595.71.05 / CUDA driver 13.2.

Nsight **2026.3.2.476** was downloaded from the already-configured NVIDIA apt repository and extracted locally, not installed system-wide. Its CLI is:

```text
cpp/build/vortex-stall-diagnosis-20261009/opt/nvidia/nsight-systems/2026.3.2/bin/nsys
```

Package SHA256: `544d3455db5d2afcc1c853599c10c0c59f1f8797662bc9e0642a3a6c80139cc9`.

Raw files are under `cpp/build/vortex-stall-diagnosis-20261009/`:

| Prefix | Capture | Full-Q5 profile time |
|---|---|---:|
| `q5-light.{1..6}` | CUDA + NVTX, no CPU sampling/context switches, event-completion tracing off | 23.926–24.726 ms |
| `q5-cpu.{1..3}` | CUDA + NVTX + OSRT, CPU samples, scheduler wakeups, CUDA API backtraces | 32.079–39.006 ms |
| `q5-continuous` | 30 normal samples; CUDA + NVTX, CPU samples, raw scheduler and pread events; **no OSRT** | mean 29.437 ms |

Each has `.nsys-rep` and `.sqlite`; prefix `.log` files preserve exact commands, and `.json` files preserve benchmark output. Ordinary recent Q5 runs were approximately **21 ms**. Profile times are **not throughput comparisons or savings estimates**.

All captures passed input/result-signature checks: 76,590,427 rows; 5,287,549,556 fixture bytes; 51,468 matched rows; 5 countries. GPU idle checked before captures and empty at completion. No orphan job remains.

### Capture limitations

- Driver-version mismatch warnings are gone. Hardware tracing is unsupported on this system, so CUDA tracing remains software-instrumented.
- Light/heavy captures warn of possibly incomplete CUDA/NVTX events. The continuous capture warns of possibly incomplete NVTX events.
- `--cuda-event-trace=false` avoids its documented overhead/false-dependency risk; device event-completion dependencies are consequently unavailable.
- `--profile` forces one execution per state. Repeating the scale-factor axis creates distinct states; each reconstructs local I/O state. File/page cache is warm, but NVBench flushes GPU L2. Profile start/stop costs and fresh threads interfere with thread warming.
- Continuous capture avoids repeated profiler stops and uses normal sample-count execution. Triggering on the first `vortex.read` also captures early fixture validation: total timeline is approximately 100 seconds, not just the final 0.9 seconds of samples. It is not a minimal capture.
- A missing explicit overhead interval does **not** prove profiler-free execution. A recorded API boundary may exclude some wrapper/instrumentation execution.

## 1. Light traces: recurring submission gaps

Read envelope = first of six `vortex.read` entries to last exit. Active time is the **union** of recorded kernels, copies and memsets across streams, clipped to the envelope. No-work means no such recorded operation, not proof that every GPU engine is physically idle.

| Trace | Read envelope ms | GPU active union ms | No-work ms | Initial no-work ms | Later no-work ms |
|---|---:|---:|---:|---:|---:|
| 1 | 16.920 | 10.389 | 6.531 | 0.905 | 5.626 |
| 2 | 17.178 | 10.956 | 6.222 | 0.899 | 5.322 |
| 3 | 17.148 | 10.264 | 6.884 | 0.934 | 5.950 |
| 4 | 17.341 | 10.576 | 6.765 | 1.057 | 5.709 |
| 5 | 17.404 | 10.783 | 6.621 | 1.011 | 5.610 |
| 6 | 17.314 | 10.622 | 6.693 | 1.132 | 5.561 |

Across pooled later no-work, considering future activity **within the read envelope**:

- **69.13%:** no associated future GPU-work API entered yet.
- **20.73%:** at least one such API in progress, none returned.
- **7.64%:** at least one such API returned.
- **2.50%:** no remaining read-envelope GPU work (trailing cleanup).

These are submission-state categories, not causal or recoverable-time categories. All 3,282 GPU operations across the six full captures match uniquely to successful APIs by process and correlation ID.

The earlier approximately 2 ms queued-copy spike did **not** recur. Largest internal gaps in the six light runs were 0.364–0.636 ms, overwhelmingly before the ending operation's API entry. Of 92 internal gaps ≥100 µs, 65 end in H2D; their combined duration is 15.288 ms across six runs.

Actual CUDA host callbacks are now recorded. Their execution overlaps only **1.90%** of pooled later no-work. There are individual callback-constrained streams: trace 3 has a 217.731 µs callback, followed by an already-submitted same-stream H2D 5.216 µs after callback completion. Some callback correlation IDs are zero; those registrations cannot be assigned by timing alone.

## 2. Heavy CPU traces: useful stacks, distorted durations

Absolute timestamps below are SQLite nanoseconds divided by 1e6.

| Evidence | Observed path | What it establishes |
|---|---|---|
| CPU trace 1, 45.853838–46.289074 ms gap; OSRT chain 172 | `decode_fsst_varbin → copy_to_device → await_stream_callback → cuLaunchHostFunc → libcuda → pthread_rwlock_wrlock` | 255.139 µs in a **driver-internal** lock call, not a Vortex rwlock. Lock holder unknown. |
| CPU trace 1, 43.405298–43.790806; chain 199 | dictionary execution → `CudaSlice`/`CudaEvent` destruction → `cuEventDestroy_v2 → libcuda → pthread_rwlock_wrlock` | Cleanup encounters driver locking. The 363.748 µs lock call begins before the gap. |
| CPU trace 2, 44.720381–45.191330 | Samples in `cuStreamWaitEvent → CUPTI/ToolsInjection → memcpy`, then projection/type construction and allocation | Mixture of instrumentation, application CPU work and driver work, not an application-only gap. |
| CPU trace 3, 43.516790–43.935226; chain 285 | `PooledFileReadAt::read_at → PooledPinnedBuffer::copy_to_device → parking_lot::RawMutex::lock_slow` | Application mutex wait; source's post-copy retention path locks `pool.inflight`. Exact owner and its work not captured. |
| CPU trace 3, 46.924540–47.184415; chains 290/291 | `device_alloc → cuMemAllocAsync → libcuda → ioctl` | Driver allocation work occupies part of the gap; later CUPTI also sampled. Exact ioctl operation unknown. |

Startup gaps are 1.19–1.51 ms in these captures. Samples cover file/footer parsing, zone-map metadata, lazy scan/projection setup, pinned-event reclamation, reads and submission. They are not all disk latency: in trace 2 a footer `pread` takes **5.856 µs**, followed by **249.219 µs profiler TLS allocation** on its worker before waking the caller. Completed-event queries also include instrumentation cost.

## 3. Continuous normal-sample capture

Selected the **last 30 overlapping six-read clusters**: 98410.029399–99287.837997 ms. Earlier validation/warmup clusters excluded. NVTX has no explicit sample-number tags; ordering, six-input grouping and the 30-sample benchmark result establish this selection.

| Per-sample read accounting | Mean ms | Median ms |
|---|---:|---:|
| Read envelope | 20.995 | 20.705 |
| GPU active union | 9.342 | 9.349 |
| Initial no-work | 1.467 | 1.536 |
| Internal no-work | 10.005 | 9.787 |
| Trailing no-work | 0.181 | 0.180 |

Internal no-work: mean **6.630 ms** before any future read-work API entry, **2.919 ms** with APIs in progress and none returned, **0.456 ms** with some future API already returned. All selected GPU operations match uniquely to submission APIs; accounting partitions pass. These larger gaps must **not** replace the light-trace numbers as estimates of ordinary behavior.

Explicit profiler overhead overlaps **56.192 ms of internal no-work across 30 samples**. Callback execution overlaps 1.573 ms. Neither sum is a subtractable overhead/savings estimate.

### A. Concrete host-read completion sequence (sample 25)

Gap: **99141.813299–99143.150306 ms**, duration **1.337007 ms**.

```text
CPU worker 620236:  pread(8 MiB) ==================================|
                    99141.272280                         99142.993740
GPU:                       last H2D ends |..... no recorded work .....| next H2D
                                   99141.813299                 99143.150306
Submitter 620329:             event bookkeeping → sleep → wake/run → H2D API
                                     asleep 99141.978496
                                           waking 99143.008236
                                              run 99143.030924
                                                   API 99143.117755
```

- Many same-fd reads overlap the gap. The first observed completion after its start is worker **620236**, fd 65, offset **1,235,798,896**, 8 MiB successfully returned.
- Worker remains scheduled throughout its **1.721460 ms pread**; no switch-out inside the syscall.
- That worker emits `sched_waking` for submitter 620329 **14.496 µs after read exit**.
- Submitter runs **22.688 µs after waking** and enters H2D **86.831 µs later**. Waking→running includes activation, not just run-queue delay.
- **1.180441 ms of the gap elapses before that first read completion.**

**Interpretation:** strong evidence of host-read readiness gating progress, not a millisecond CUDA queue wait or OS runnable starvation. Buffered pread includes the CPU copy from cached pages into pinned staging memory. Staying on CPU rules out a long descheduling interval for this reader, but does not identify every instruction or prove zero storage-related work.

**Identity limit:** the recorded wake is exact, but CUPTI records here do not link the H2D source pointer to this pread buffer. Shared executor progress can service another request. Therefore this is not proof that the very next H2D transfers exactly that buffer.

### B. Same read offset and wake pattern recurs (sample 1)

Gap: **98422.821191–98424.130486 ms**, duration **1.309295 ms**.

- Worker **620113** reads fd 65, the same offset **1,235,798,896**, 8 MiB: **98422.119384–98423.758058**.
- It remains scheduled throughout pread, then wakes submitter **620176** at **98423.774634**; submitter runs at **98423.802667**.
- Another short sleep/wake occurs before H2D API **98424.095476–98424.130933**.
- **0.936867 ms** elapses from GPU-gap start to the first observed read completion. CUPTI is also sampled during submission preparation near the end.

These two examples identify a repeated input-read wave, not merely a single anomalous CUDA timeline. They do not measure its unprofiled penalty. There are 4,920 paired preads in the measured-window vicinity, 3,990 requesting 8 MiB; observed syscall duration median 1.885 ms, p95 2.425 ms. Calls overlap heavily: **do not sum them or interpret the distribution as query wall time**.

### C. A different large gap is event/profiler serialization (sample 25)

Gap: **99130.720950–99132.402729 ms**, duration **1.681779 ms**.

- Ending submitter **620330** is inside `cuEventCreate` **99130.621263–99132.236321**, sleeping for much of it.
- Sibling application thread **620331** executes `cuEventRecord` **99130.768337–99132.152800**. Five in-gap samples show the Arrow-export event-record path with CUPTI execution; four have CUPTI leaf frames.
- Several other event APIs overlap. The long-sleep waker is another event-record caller, **620328**, not the sampled thread.
- **No explicit `PROFILER_OVERHEAD` interval overlaps this gap.** That table alone would miss the interference.

This is strong event/profiler interaction evidence, but not an identified driver lock owner or a production event-optimization result.

### D. Remaining unexplained sleeping gap (sample 9)

Gap: **98663.083109–98664.210866 ms**, duration **1.127757 ms**, ends in 8 MiB H2D.

Submitter 620226 sleeps; worker 620110 wakes it at **98664.122802**. Worker 620110 was itself sleeping until woken by worker 620107 at **98664.059409**. Its next pread starts **after** waking the submitter. Thus this is **not** the same demonstrated read-exit pattern as A/B. No in-gap application CPU samples identify the blocked primitive. Do not label it disk I/O, pinned-pool locking or task starvation based only on thread names.

## What follows from this—not a new experiment

| Issue | Current status |
|---|---|
| Host staging/read readiness | Strongest new application-side sequence. Inspect read-wave/prefetch structure before changing stream topology. Exact request→transfer identity remains missing. |
| Pinned retention mutex | Confirmed path in heavy trace; prior detached/size-targeted reclamation attempts failed ordinary benchmarks. Not a newly proven fix. |
| CUDA event/callback/allocator work | Confirmed paths, but driver vs profiler cost unresolved. Callback execution itself explains little aggregate no-work. |
| Already-queued copies / stream scheduling | Minority of light-trace no-work; earlier large queued-copy spike absent. Not currently the best-supported dominant cause. |
| CPU scheduler starvation | Selected long waits are sleeping dependencies, not long runnable delays. Async-task readiness is still not measured. |

**Discuss next before implementation or another capture.** If exact ownership is needed, use narrowly scoped read-request/buffer identity rather than another broad, intrusive profile. Otherwise choose one general host-staging/read-pipeline hypothesis and judge it with ordinary before/after benchmarks; the rejected worker-handoff change already showed that moving submission alone was insufficient.

## Reproduction notes

Full commands are the first line of `q5-light.log`, `q5-cpu.log`, and `q5-continuous.log`. Do not overwrite these outputs. Continuous capture used the locally extracted CLI with:

```text
env CUDA_VISIBLE_DEVICES=0 KVIKIO_COMPAT_MODE=ON
    NSYS_NVTX_PROFILER_REGISTER_ONLY=0 TMPDIR=<diagnosis directory>
nsys profile --trace=cuda,nvtx --sample=process-tree --backtrace=fp
    --sampling-period=1000000 --cpuctxsw=process-tree
    --ftrace=sched/sched_switch,sched/sched_wakeup,sched/sched_waking,syscalls/sys_enter_pread64,syscalls/sys_exit_pread64
    --cuda-event-trace=false --capture-range=nvtx
    --nvtx-capture=vortex.read@benchmarks --capture-range-end=none --export=sqlite
```

The benchmark used SF10/Vortex/Q5/warm/buffered, three cold warmup runs, `--cold-max-warmup-walltime -1`, sample-count stopping, min samples 1, target samples 30, timeout 120. No `--profile`. Capture command ran under `sudo -n` and `timeout --signal=TERM --kill-after=10s 240s`.

Offline evidence tables: `NVTX_EVENTS`, CUDA activity/API tables (join process + correlation ID), `CUDA_HOST_CALLBACK`, `PROFILER_OVERHEAD`, `COMPOSITE_EVENTS` + `SAMPLING_CALLCHAINS`, and raw `GENERIC_EVENTS`/`GENERIC_EVENT_DATA`. Scheduler fields are decoded using `GENERIC_EVENT_TYPE_FIELDS`; preads paired by `common_pid` syscall entry/exit. CPU-thread durations and overlapped GPU work are never added as wall time. Existing `pipeline_timeline.py` main assumes profilerStart/OSRT and is **not directly compatible** with every new capture; its interval helpers are reusable. No new tracing framework was added.
