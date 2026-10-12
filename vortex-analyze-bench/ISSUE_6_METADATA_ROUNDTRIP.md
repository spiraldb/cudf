# Issue 6 — FSST metadata D2H and host continuation

Status: three attempts measured and reverted; no retained change.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 6: 31.058560–31.408736 ms, 0.350176 ms without recorded GPU work. A 19-byte D2H has completed; submitter TID 520914 then parks in `CudaDeviceBuffer::copy_to_host_sync → FSST deserialization`, including 0.115904 ms off-CPU. Arrow import/materialization follows. Trace does not locate callback execution or prove why continuation is delayed.

Inspect whether CPU-required metadata can remain host-resident rather than taking a GPU round trip, while preserving projection, session/dictionary isolation and buffer ownership. Alternative: narrowly change synchronous completion waiting if evidence and CUDA semantics justify it; never spin indiscriminately or remove completion fencing. ## Attempt A — direct synchronization for synchronous D2H

Replaced `block_on(copy_to_host(...))`/host callback with direct stream synchronization, retaining managed source read guards and leaking DMA endpoints conservatively on unknown completion. `issue6-direct-sync.json`: read 15.691 ms (−0.68%), Q5 20.914 ms (+0.95%) against cache confirmation. No full-Q5 improvement; reverted.

## Attempt B — host-inline FSST symbol metadata

Extended existing CUDA-flat host-buffer overrides to include FSST local buffers 0/1 (symbols/lengths), not compressed codes. This addresses new writes only; fallback for old files would remain. No decoded-data cache. The initial build required correcting `array.buffers().take` to `.into_iter().take`; subsequent release build passed.

| Pair                                                                             | Cache-only read/Q5 ms | Metadata read/Q5 ms | Q5 change |
| -------------------------------------------------------------------------------- | --------------------- | ------------------- | --------: |
| Initial (`issue4-function-cache-confirm` / `issue6-fsst-host`)                   | 15.798 / 20.716       | 15.719 / 20.660     |    −0.27% |
| Confirmation (`issue6-before-confirm` / `issue6-fsst-host-confirm`)              | 15.775 / 21.187       | 15.785 / 20.847     |    −1.61% |
| Reverse order (`cache-reverse` / `issue6-fsst-host-reverse`; metadata ran first) | 15.835 / 21.008       | 15.651 / 21.192     |    +0.88% |

Fixture bytes changed **5,287,549,556 → 5,287,556,228 (+6,672)**. Explicitly checked equal axes and all non-file-size signatures; benchmark reference/projection validation passed. The strict `bench.py --compare` guard was not weakened. 30 samples per workload/process, no profiler.

Small/inconsistent Q5 changes fail the retention criterion. **Reverted**; no metadata format or file rewrite requirement remains. Neither candidate was given extra test matrices after rejection.

## Attempt C — device-native FSST symbol metadata, unchecked benchmark (2026-10-09)

User authorized bypassing CPU validation/readbacks on known-valid fixtures to measure the benefit before investing in a production design. Based on Vortex `93e2931adb`; no stream, kernel, or file-format changes.

- Current-format deserialization retained original device-backed symbols and symbol-length handles. It checked their sizes but bypassed content validation, including subsequent array validation.
- Both CUDA FSST decode paths consumed these handles directly using existing managed device views, avoiding the symbol metadata D2H → host-table construction → H2D path. Host metadata continued to use the original conversion; CPU access lazily materialized device metadata.
- This did **not** eliminate all GPU readbacks: Arrow output-size/status readbacks and the VarBinView path's uncompressed-length readback remained. No claim of a globally readback-free decoder.
- Experimental source touched only `encodings/fsst/src/array.rs` and `vortex-cuda/src/kernel/encodings/fsst.rs`.

30 samples per workload/process, SF10, warm buffered, idle checks, sequential GPU jobs, no profiler:

| Pair                 | Baseline read / Q5 ms | Bypass read / Q5 ms | Read change | Q5 change |
| -------------------- | --------------------: | ------------------: | ----------: | --------: |
| Baseline then bypass |       15.797 / 20.877 |     15.800 / 21.299 |      +0.02% |    +2.02% |
| Bypass then baseline |       15.956 / 20.844 |     15.758 / 21.057 |      −1.24% |    +1.02% |

Results: `results/fsst-device-{before,after}-20261009.json` and `results/fsst-device-{before,after}-reverse-20261009.json`. Strict comparison passed for both pairs, including unchanged fixture bytes and result signatures. Release Q5 build and benchmark reference/projection checks passed. Added focused mock-device and CUDA tests are preserved in the patch but **were not run** after the candidate failed the performance criterion. No trace was taken to count removed calls; removal is established from the changed source path, not a measured transfer-count comparison.

**Decision: rejected and reverted.** Full Q5 was slower in both comparisons; read-only results were inconsistent. This does not prove FSST readbacks never matter for other workloads or establish the cause of the slowdown. It provides no evidence for retaining this bypass on Q5.

Archived experiment: [fsst-device-metadata-bypass.patch](fsst-device-metadata-bypass.patch). **Benchmark-only; it weakens validation and must not be treated as a production fix.** No commit made.

Vortex source restored to clean `93e2931adb`. Q5 executable restored from the saved, byte-identical baseline (not rebuilt again); SHA-256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. Build intermediates may still contain the rejected candidate until the next explicit CMake/Cargo build. Candidate executable SHA-256 was `dfc4ccaee60a38f60352b6d94a0318c4646280f3b781c7b2ebc65f68a0ef06d5`.
