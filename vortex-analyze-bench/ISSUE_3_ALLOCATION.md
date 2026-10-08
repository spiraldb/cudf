# Issue 3 — allocation/driver work delays submission

Status: inspected, deferred; no measured candidate or source changes.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 5: 45.047104–45.472512 ms, 0.425408 ms without recorded GPU work. Gap-ending submitter TID 520916 stays on-CPU throughout. `cuMemAllocAsync` overlaps 0.397280 ms, with nested driver ioctls. This is not evidence of OS descheduling. Allocation size and pool internals are not captured.

Inspect CUDA memory-pool configuration/reuse and setup allocation paths. Preserve initialization and all buffer guards. Do not infer that the long allocation requested 96 bytes from the size of the next copy. No speculative allocator or production tuning knobs; measure independent changes against the last retained source. No attempts or gains yet.

Inspection: `VortexCudaStream::device_alloc` delegates once to cudarc, which uses `cuMemAllocAsync` and creates two tracking events. Copy/execution stream pools are not duplicate memory pools. Vortex production session code does not explicitly set a release threshold, but **the actual cuDF benchmark already sets the current device-0 pool threshold to 8 GiB** (`cpp/benchmarks/ndsh/vortex/vortex_io.cpp:55–60`, `retain_vortex_cuda_pool_memory`). The CUDA documented default of zero is therefore not the benchmark policy. Raising it again changes shared-device policy and memory retention; it is not justified without observing actual pool behavior, and adding a production knob is outside the agreed constraints. No blind allocator rewrite or extra capture.
