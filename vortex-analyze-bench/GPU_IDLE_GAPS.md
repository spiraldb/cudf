# Q5: recorded GPU-idle gaps and CPU-side evidence

## Scope and accounting

Offline analysis only: no GPU runs, builds, captures, or source changes. Input:
`cpp/build/vortex-bench-history-20261008/runs/ndsh-scheduler-gaps-20261008/.local/q5-scheduler-01.sqlite`.
SHA-256 matches the capture manifest: `9b67bcd5364e7d3817f46bb26da8075e18214f19f16db0454eb06316bd08a017`.

**All timestamps below are milliseconds on the exported trace-relative clock**, not wall time. Six decimal places preserve integer nanoseconds. Intervals are half-open. For time relative to the first `vortex.read`, subtract **26.930080 ms**.

Recomputed the union of all 547 recorded kernels, copies, and memsets across every recorded stream, rather than ranking kernel-only gaps. All these activities belong to device 0, context 1, PID **520674**, serialized `globalPid=290210436874240`. GPU activities are joined to API rows by **(globalPid, correlationId)**, with API process identity obtained by clearing the low 24 bits of `globalTid`. Those keys are unique; the process has one captured CUDA context. TIDs below are the low 24 bits, not process IDs.

| Envelope | Interval, ms | Duration, ms | All-work union, ms | No recorded work, ms |
|---|---|---:|---:|---:|
| Read: first read start through last read end | 26.930080–51.303456 | 24.373376 | 9.268672 | **15.104704** |
| Decoder: first decoder kernel through last decoder kernel end | 29.057120–49.984192 | 20.927072 | 8.418592 | **12.508480** |

The read envelope contains **218** maximal no-work intervals. Independent raw-endpoint sweeping agrees with the interval union and existing JSON totals. Decoder membership is derived from decoder-named kernels and their streams (30–33); the no-work test still includes **all** streams, including transfer stream 29 and materialization stream 7.

These envelopes overlap: **do not add their times**. CPU waits, API durations, and off-CPU spans below also overlap one another and the GPU gaps. None are measured savings, or proof of physical device idleness outside captured activity.

## Largest no-work intervals

API names: **H** = `cuMemcpyHtoDAsync_v2`; **D** = `cudaMemcpyAsync_v3020`; **K** = `cuLaunchKernel`. Each API succeeded. The operation listed starts at the gap's end; it need not be a kernel, nor need its API have returned before GPU execution starts.

| Rank | Gap, trace-relative ms | Duration, ms | Operation ending gap; stream | Submitter TID; API/correlation | API interval, ms |
|---:|---|---:|---|---|---|
| 1 | **31.435712–33.335776** | **1.900064** | H2D, 8,388,608 B; 29 | 520915; H / 7847 | 33.314944–33.333280 |
| 2 | **26.930080–28.335808** | **1.405728** | H2D, 120 B; 29 | 520917; H / 7394 | 28.320128–28.332992 |
| 3 | **29.641696–30.972256** | **1.330560** | H2D, 2,862,396 B; 29 | 520915; H / 7591 | **29.412384–29.482208** |
| 4 | **28.336608–28.922624** | **0.586016** | H2D, 937,764 B; 29 | 520917; H / 7436 | 28.910336–28.930752 |
| 5 | **45.047104–45.472512** | **0.425408** | H2D, 96 B; 33 | 520916; H / 11593 | 45.458624–45.469888 |
| 6 | **31.058560–31.408736** | **0.350176** | D2D, 6,000,000 B; 7 | 520914; D / 7785 | 31.376288–31.412352 |
| 7 | **33.380512–33.726112** | **0.345600** | H2D, 62,884 B; 29 | 520917; H / 7869 | 33.707840–33.723680 |
| 8 | **41.109632–41.334080** | **0.224448** | `dynamic_dispatch_u64`; 32 | 520916; K / 10262 | 41.283424–41.335456 |
| 9 | **49.984192–50.207744** | **0.223552** | D2D, 5 B; 7 | 520919; D / 13392 | 50.189024–50.206208 |
| 10 | **29.454528–29.639040** | **0.184512** | `bit_unpack_8_5bw_32t`; 31 | 520917; K / 7646 | 29.619680–29.640352 |

Rank 2 begins at the read-envelope boundary; rank 9 is after decoder execution but inside the read envelope. Rank 3 is importantly **already submitted**. Rank 10 also overlaps submission of that future copy: classifying only the next kernel's submission would miss this.

## Issue cluster 1 — synchronous pinned-retention mutex blocks a GPU producer

**Priority: inspect first. Concrete producer/executor obstruction, not a demonstrated OS scheduler defect.**

### Facts

- Rank 1 begins when a 1,500,000-byte D2D copy on stream 7 finishes at **31.435712** (correlation 7799, submitter 520914). No captured future kernel/copy/memset API starts until **33.314944**. Thus **1.879232 ms** of this gap precedes the earliest future work API; the final **0.018336 ms** is the H2D API interval, followed by **0.002496 ms** until GPU execution.
- The actual gap-ending submitter, **520915**, is in `futex` **29.560768–33.280704**, OSRT row **84**, callchain **20**. Stack: `parking_lot::RawMutex::lock_slow` → `PooledPinnedBuffer::copy_to_device` → `PooledFileReadAt::read_at` → buffered read results / `ReadDriver` → async task → `CurrentThreadIterator::next` → `device_stream_get_next`.
- Scheduling: out on CPU 3 at **29.562926**, in on CPU 1 at **33.277934**. Within rank 1 this is **1.842222 ms off-CPU**, then **0.057842 ms on-CPU**. The overlapping futex portion is **1.844992 ms**, not additional time.
- After the futex returns, the same thread executes `cuStreamWaitEvent` at **33.302880–33.308576**, **33.309056–33.310016**, and **33.311040–33.314048**, then submits correlation 7847. These short host API intervals are not measurements of GPU wait duration.
- Rank 4 corroborates the pattern on **520917**: `futex` **28.338400–28.856992** (**0.518592 ms**, OSRT 1687 / chain 541), with the same `RawMutex::lock_slow` → `copy_to_device` path. It is off-CPU **28.345998–28.850382** (**0.504384 ms**), then performs allocation/event/wait setup and the next H2D.

### Actionable source inspection; limits

Read-only inspection verified `/home/ubuntu/vortex` is clean at manifest revision `f12cc07e326f5921ad7cff0899490ae57fbc5f4f`:

- `vortex-cuda/src/pinned.rs:388–420`: **submit H2D and record its fence first**, then acquire `pool.inflight` to retain the source. The observed lock is therefore consistent with retention of a **previously submitted transfer**, obstructing subsequent producer progress—not the copy API itself waiting to submit that previous transfer.
- `pinned.rs:198–201,246–264`: `try_get` calls reclamation; reclamation holds `inflight` while binding the CUDA context, querying events, and returning completed buffers to buckets. Inspect this lock scope and synchronous retention from async execution together.
- `vortex-cuda/src/pooled_read_at/file.rs:213–234` performs transfers synchronously as host-read results become available; `vortex-io/src/runtime/current.rs:128–132` drives tasks from `Iterator::next`.

**Hypothesis:** retention/scanning serialization stalls a thread that could otherwise advance submission or poll other tasks. The trace establishes the blocking call and subsequent producer sequence, but not the mutex owner's identity, its exact critical section, availability of the next input, or readiness of another task. Do not infer that removing the mutex would recover 1.900064 ms. Preserve pinned-source lifetime through DMA completion and error handling; this is not a recommendation to blindly reapply the previously parked lock split.

## Issue cluster 2 — already-submitted transfer has unexplained device-start delay

### Facts

Rank 3 starts after `bit_unpack_8_5bw_32t` ends at **29.641696**. Its gap-ending H2D was submitted by **520915** at **29.412384–29.482208**, before the gap. API-end → GPU-start is **1.490048 ms**; the contained all-work-empty gap is **1.330560 ms**, not an additional interval.

The submitter is off-CPU for this entire gap, inside the same OSRT row 84 mutex wait described above, with **no same-thread CUDA API overlapping the gap**. That does **not** establish the cause of an already-submitted copy's delayed execution.

Immediately preceding the copy on stream 29 are `cuStreamWaitEvent` correlations **7586 / 7588 / 7590**, at **29.409728–29.411328**, **29.411488–29.411680**, and **29.411840–29.412288**. Their event IDs are **958 / 2095 / 2096**, all with **eventSyncId=0**. No valid event-generation match establishes a dependency here. The prior recorded stream-29 copy ends at **29.454528**; that is only a completion lower bound, not a proof that the copy was runnable.

**Action/hypotheses:** inspect copy-stream ordering, buffer event guards, and allocation/driver interactions before blaming the CPU scheduler. Device dependency, driver submission behavior, and incomplete capture remain alternatives. Do not turn zero-generation waits into proven blocking edges or interpret the API-to-GPU interval as pure launch overhead.

## Issue cluster 3 — allocation API and driver work occupy an empty interval

**Rank 5, TID 520916:** the preceding 8-byte memset ends at **45.047104**. The thread remains on CPU 5 throughout the entire **0.425408 ms** gap (scheduled-in interval **44.766255–45.537135**).

Its `cuMemAllocAsync`, correlation **11575**, runs **44.986880–45.444384**, **0.457504 ms** total, overlapping **0.397280 ms** of the gap. Six same-thread `ioctl` calls overlap the gap with union **0.317312 ms**; for example OSRT 1545 / chain 494 is **45.235424–45.350016** and resolves into `libcuda.so.595.71.05`. After allocation, short event creation, another allocation, and stream-wait APIs precede H2D correlation 11593.

**Action:** inspect the allocation path supplying this decoder's small setup buffers, allocation reuse, and whether driver allocation work can be kept off the submission path. Pool growth/mapping is a **hypothesis**: the exported API row does not give allocation size or identify the allocator's internal operation. The 96-byte transfer is not evidence that the long allocation requested 96 bytes. This gap is not explained by descheduling the submitter.

## Issue cluster 4 — function lookup and event lifecycle encounter driver locks

**Rank 8, TID 520916:** `pthread_rwlock_wrlock` **41.062112–41.231808**, OSRT **1509**, chain **483**, overlaps **0.122176 ms** of the gap. Its stack identifies `libcuda` → **`cuModuleGetFunction`** → `CudaModule::load_function` → `CudaSession::load_function_with_suffixes` → `MaterializedPlan::execute`.

Same-thread off-CPU portions inside the gap are **41.109632–41.147343**, **41.149519–41.230991**, **41.256015–41.276623**, and **41.286767–41.315151**: union **0.168175 ms**. Additional `pthread_rwlock_rdlock` calls occur at **41.254368–41.277440** (inside `cuEventRecord`, correlation 10259) and **41.285344–41.316000** (inside gap-ending `cuLaunchKernel`, correlation 10262). These overlap the off-CPU/API intervals; do not add them.

**Action:** `/home/ubuntu/vortex/vortex-cuda/src/kernel/mod.rs:236–248` caches modules but calls `module.load_function(&kernel_name)` on each lookup. Inspect context/session-scoped function-handle reuse to avoid repeated driver lookup; preserve context lifetime and kernel-name identity. This is a concrete inspection target, **not measured cache savings** or proof that lookup was the only obstruction.

Related evidence, kept distinct from the lookup: rank 10 has `cuEventCreate` **29.508800–29.599840**, correlation **7609**, containing `pthread_rwlock_wrlock` **29.509312–29.595008** on 520917. Rank 9's submitter 520919 executes four `cuMemFreeAsync` and nine overlapping `cuEventDestroy_v2` API records before materialization; three same-thread write-lock calls at **50.009120–50.040704**, **50.065376–50.091104**, and **50.096192–50.137344** overlap its **0.088928 ms** off-CPU union. Inspect event churn/reclamation, but do not equate host locks with device dependency waits.

## Issue cluster 5 — producer parking and synchronous metadata round trip

These are distinct follow-ups, not evidence that every parked thread has ready work.

- **Rank 7, TID 520917:** `futex` **30.816800–33.643456**, OSRT **1696**, chain **545**, resolves to condition-variable wait → `parking::Inner::park` → `CurrentThreadIterator::next`. It is off-CPU **30.819598–33.639310**, overlapping **0.258798 ms** of this gap. After returning, allocation **33.684096–33.695680**, event/wait setup, and H2D **33.707840–33.723680** precede GPU restart. Inspect host-read completion → `ReadDriver::poll_next` (`vortex-file/src/segments/source.rs:240–274`, especially result resolution at 263–268) → consumer/executor polling. **Missing:** input completion, task/waker identity, and readiness time. Shared-executor caller TID is not input ownership; ordinary waiting for input remains possible.
- **Rank 2 startup:** the same TID's `vortex.scan_open` is **27.537088–28.192896**. A futex **27.715968–28.031104**, OSRT **1683** / chain **540**, parks inside `vx_cuda_scan_path_arrow_device_stream_projected`; off-CPU **27.717518–28.029646**. Initial allocation is **28.279264–28.310016**, then the first H2D. This is staged startup, not a single 1.405728 ms scheduler delay. The first **0.180910 ms** of the envelope precedes this thread's first observed scheduling transition and is left unclassified.
- **Rank 6, TID 520914:** a **19-byte D2H** finishes at **31.058560** (correlation 7753), but the thread then parks in `futex` **31.079968–31.201376**, OSRT **246** / chain **84**. Stack: `std::thread::park` → **`CudaDeviceBuffer::copy_to_host_sync`** → **FSST deserialization**. Off-CPU is **31.084046–31.199950**, **0.115904 ms**. A `cuLaunchHostFunc` also overlaps the gap; subsequent NVTX phases are Arrow-device import **31.322496–31.347168** and materialization **31.352160–31.435328**. Inspect the FSST metadata D2H/callback completion path and CPU continuation before D2D materialization, rather than attributing the whole gap to a slow copy. The trace does not identify the callback's execution instant or why it took this long to resume.

## Limitations and reproducibility

- Nsight **2025.5.2** explicitly warns that installed CUDA driver **13.2** is unsupported by this build and tracing uses **13.1** libraries. It also warns that CUDA, OSRT, and NVTX events may be incomplete. An additional “No NVTX events collected” diagnostic coexists with actual exported NVTX rows. Treat empty intervals as **no recorded work**, not guaranteed hardware inactivity.
- `SCHED_EVENTS`: 2,848 rows have state **Unknown**, six have **Terminated**. Scheduling transitions support bounded on/off-CPU spans, **not runnable/blocked classification, wake-to-run latency, preemption attribution, CPU saturation, or proof of a missed wakeup**. OSRT stacks supply the stronger blocking-path evidence where present.
- No CPU sampling was captured. OSRT callchains identify particular calls, not all intervening CPU execution. An on-CPU interval is not synonymous with useful application work. Lock owners, exact task/input ownership, and allocation parameters are not established here.
- CUDA event completion tracing was disabled; all **938** exported event completion timestamps are zero. Existing generation-aware analysis matches 669 of 1,044 stream waits; 375 have generation zero. Neither short wait APIs nor predecessor lower bounds locate actual GPU waits. One synchronization record lacks an API match and is not assigned by guesswork.
- One profiled saved-baseline execution, warm cache / buffered I/O / scale factor 10, run as root. No unprofiled speedup, repeatability, or recoverable-wall-time conclusion follows. Read time is part of Q5, not additive to it.

The standalone script [analyze_idle_gaps.py](analyze_idle_gaps.py) opens SQLite with `mode=ro&immutable=1` and `query_only`, independently recomputes the all-work union, checks raw activity identity against the existing JSON, audits API joins, and emits exact nanoseconds, same-thread scheduling/API/OSRT rows, and long-wait callchains. It writes nothing and does not invoke the historical analysis framework.

Validation run from `/home/ubuntu/cudf`:

```sh
python3 vortex-analyze-bench/analyze_idle_gaps.py 1 2 3 4 5 6 7 8 9 10 11
```

Assertions passed, including raw-endpoint/union agreement and decoder/read idle totals. Source inspection was read-only. Only this report and the standalone audit script were created.
