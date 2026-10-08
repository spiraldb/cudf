# Issue 6 — FSST metadata D2H and host continuation

Status: two attempts measured and reverted; no retained change.

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
