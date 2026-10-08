# Issue 2 — already-submitted H2D starts late

Status: attribution blocked; no safe optimization identified yet.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 3: no recorded work at 29.641696–30.972256 ms (1.330560 ms). H2D correlation 7591 was submitted at 29.412384–29.482208, before this gap. Concurrent host retention waiting cannot alone explain delayed execution of already-submitted work.

Three preceding stream-29 waits have event generation zero, so this trace cannot establish their dependencies. Inspect device allocation, buffer guards, and copy-stream destruction ordering. Do not remove read/write fences or declare scheduler latency from an API-to-GPU delay. Driver/capture effects remain alternatives.

Next: source/dependency inspection; only benchmark a change with a defensible ordering/lifetime argument. A new capture is justified only if it can provide missing generation/dependency information; repeating the same incomplete capture is not promising. No attempts or gains claimed.

Source inspection confirms cudarc 0.19.9 `CudaSlice::drop` waits on both tracked read/write events on its owning stream before `free_async`. File destinations are owned by the single copy stream, so destruction can insert compute dependencies ahead of later copies. It cannot retroactively delay an earlier submitted copy. Safe retirement isolation needs a dependency API change or a completion-gated ownership queue with shutdown/error handling; removing guards or rewrapping/leaking pointers is not safe. Deferred rather than guessing from missing event generations.
