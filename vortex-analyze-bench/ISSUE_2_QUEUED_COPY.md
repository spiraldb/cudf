# Issue 2 — already-submitted H2D starts late

Status: retirement-stream prototype built, tested, benchmarked, and reverted; no repeatable full-Q5 benefit.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 3: no recorded work at 29.641696–30.972256 ms (1.330560 ms). H2D correlation 7591 was submitted at 29.412384–29.482208, before this gap. Concurrent host retention waiting cannot alone explain delayed execution of already-submitted work.

Three preceding stream-29 waits have event generation zero, so this trace cannot establish their dependencies. Inspect device allocation, buffer guards, and copy-stream destruction ordering. Do not remove read/write fences or declare scheduler latency from an API-to-GPU delay. Driver/capture effects remain alternatives.

Historical next step was source/dependency inspection rather than guessing at missing event generations. The newer capture in [ANALYSIS_20261009.md](ANALYSIS_20261009.md) established some small retirement dependencies but also strong profiler interference in the largest gap. The experiment below targets the source-level dependency, not a proven explanation of that large spike.

Source inspection confirms cudarc 0.19.9 `CudaSlice::drop` waits on both tracked read/write events on its owning stream before `free_async`. File destinations are owned by the single copy stream, so destruction can insert compute dependencies ahead of later copies. It cannot retroactively delay an earlier submitted copy. Safe retirement isolation needs a dependency API change or a completion-gated ownership queue with shutdown/error handling; removing guards or rewrapping/leaking pointers is not safe. Deferred rather than guessing from missing event generations.

## Retirement-stream prototype (2026-10-09, rejected)

User approved a narrow dependency change and ordinary before/after benchmarks. Allocation and H2D remained on the original upload stream; only final dependency waits and async free moved to a single session-owned retirement stream, for file-input buffers only.

- Local cudarc 0.19.9 copy: `cpp/build/vortex-retirement-cudarc`; registry source untouched. Its `CudaSlice::set_retirement_stream` records allocation readiness, retains existing usage events and original operation stream, and redirects final waits/free. Fence failure records an error without freeing an allocation of unknown safety. One extra readiness event per configured allocation.
- Vortex temporarily patched Cargo to that copy. Wiring was in `session.rs`, `file.rs`, `pooled_read_at/file.rs`, and `pinned.rs`; the fake-file test setup was updated too. No raw-pointer ownership shortcuts, background reclamation queue, changed readback stream, or new tuning knob.
- First build timed out at 240 seconds. User approved a 20-minute retry; `cmake --build cpp/build/ndsh-review-20260923-115320-on --target NDSH_Q05_NVBENCH --parallel 8` then **passed**, with Cargo reporting 5m55s.
- **Six focused CUDA retirement tests passed, zero failed/ignored**, using `cargo +1.98.0 test --manifest-path cpp/build/vortex-retirement-cudarc/Cargo.toml --offline --features cuda-12050 --lib test_retirement_stream -- --test-threads=1`. Covers cross-stream use, cancellation before initialization, preserved stream identity, context rejection, missing tracking, and explicit ownership transfer. Full Vortex/FFI and fake-file test suites were not run.

30 samples per workload/process, SF10 warm buffered, sequential GPU jobs with idle checks, no profiler:

| Pair                    | Baseline read / Q5 ms | Retirement read / Q5 ms | Read change | Q5 change |
| ----------------------- | --------------------: | ----------------------: | ----------: | --------: |
| Baseline then candidate |       15.946 / 21.169 |         15.864 / 21.002 |      −0.52% |    −0.79% |
| Candidate then baseline |       15.772 / 21.080 |         15.754 / 21.182 |      −0.11% |    +0.48% |

Results: `results/retirement-{before,after}-20261009.json` and `results/retirement-{before,after}-reverse-20261009.json`. Both strict comparisons passed, including fixture/settings/result signatures; benchmark reference/projection checks passed. No transfer-count or gap-closure profile was captured.

**Decision: rejected/reverted.** The small initial Q5 gain did not repeat in reverse order. This does not establish whether added event overhead, memory-pool reuse dependencies, or a noncritical dependency limited the benefit; no causal claim about the inconsistent result.

Archived patches: [retirement-stream-vortex.patch](retirement-stream-vortex.patch), [retirement-stream-cudarc.patch](retirement-stream-cudarc.patch). The Vortex patch contains the temporary local dependency path; the cudarc patch applies to pristine 0.19.9. No commits or pushes. The ignored local dependency/build artifacts remain for reproduction, but Cargo no longer references them.

Vortex source and Cargo files restored to clean `93e2931adb`. Main Q5 executable restored from the saved baseline, SHA-256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. Candidate SHA-256 was `641400787dfdd97d8b4df1112df4c2e8a0a21d7c8a4bd28958e2ebbe099dce3a`. Intermediate Cargo/FFI artifacts may still contain the rejected build until the next explicit rebuild; only the executable was restored without rebuilding.
