# Issue 1 — pinned retention blocks GPU producers

Status: **bounded staging slots reverted** by `7d2dd32046` (2026-10-09), including initial commit `a4955b31ac` and hardening through `7e433fe0f6`. User judged the complexity unjustified by the modest gain. Source and active Q5 are back to function-cache baseline `93e2931adb`. Earlier detached/targeted reclamation attempts remain rejected. This issue remains unresolved.

Historical details: [staging-slot review report](STAGING_SLOTS_REVIEW_20261009.md). Before rollback, file admission is file-permit then FIFO pool await (accepted same-file head-of-line tradeoff); passive availability/retry/wake-all code is removed. Network slices batch up to 8 MiB / 4,096 pieces before staging acquisition, without holding pinned leases across network awaits. Memory preparation/copy/upload stays on a blocking worker. **94 focused tests passed.** Latest Q5 versus `272f920cb5`: **+0.37%, +0.38% slower**; retained as requested correctness repair, not added speedup. The initial optimization measurements below are historical, not a fresh cache-only comparison of this exact build.

## CPU/GPU evidence

See [GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), ranks 1 and 4. No recorded GPU work at 31.435712–33.335776 ms (1.900064 ms). The next H2D submitter is off-CPU for 1.842222 ms, inside `parking_lot::RawMutex::lock_slow → PooledPinnedBuffer::copy_to_device`. Retention occurs after an earlier H2D, preventing that thread from progressing to subsequent submissions. A second 0.586016 ms gap has the same blocking path.

`vortex-cuda/src/pinned.rs` holds `inflight` through context binding, all event queries, and bucket returns. Ownership of the contended mutex and readiness of other tasks are unknown. Profiled gap duration is not recoverable time.

## Attempts

- Historical: opportunistic/skip reclamation regressed; incoming/scanner lock split produced mixed +1.19% read / −1.56% Q5. Both reverted. Do not repeat unchanged.
- A: detach the reclamation batch under a short lock, then scan outside it; merge unfinished buffers back under a short lock. No serialized scanner lock. Preserve each completion event and conservative source ownership. Risk: concurrent getters may allocate while another batch is detached.
- B: from clean baseline, query only the requested size class and reclaim at most one completed buffer. Preserved DMA fences; reduced eager work without detached batches.

| Unprofiled 30-sample run | Read ms |  Q5 ms | Q5 vs baseline |
| ------------------------ | ------: | -----: | -------------: |
| `gaps-baseline`          |  15.776 | 21.051 |              — |
| `issue1-detached` (A)    |  15.789 | 20.921 |         −0.62% |
| `issue1-targeted` (B)    |  15.587 | 21.116 |         +0.31% |

A's sub-percent Q5 change with no read benefit does not establish a gain. B regressed Q5. All input/result signatures matched. Both builds passed; rejected candidates were not given additional test/repeat rounds. The contention is real in the trace but these changes have not demonstrated ordinary-Q5 savings.

## Attempt C — bounded reusable staging slots (initially retained, then reverted, 2026-10-09)

Vortex commit **`a4955b31ac` — `perf(cuda): reuse bounded pinned staging slots`**, signed off, not pushed. Parent `93e2931adb` retains the function cache. No profiling for this experiment.

Initial implementation (later review changes are linked above):

- Per-size-class bounded `crossbeam_queue::ArrayQueue` plus async semaphore admission; size classes initialized lazily. No shared `inflight` scan or bucket mutex, no recycler thread or per-copy callback.
- Allocate only when a checked-out slot has no buffer. Default bound is **256 live slots per class**, not a global byte budget; the existing `with_limits` argument now limits live slots, rather than only cached completed buffers.
- Upload returns the buffer with its completion fence still pending. Its next owner prepares it on an existing blocking worker before host mutation. Slot ownership, allocation and DMA readiness are distinct.
- File readers acquire their file permit before a slot, both before spawning the blocking job. This avoids occupying workers with admission waits and reserving shared buffers behind file admission, but can delay small reads behind same-file reads waiting on a saturated class. This is a documented tradeoff, not solved cross-class fairness.
- Object-store `get_opts` completes before slot acquisition. Streaming payloads still hold a slot while consuming the response. Memory readers now take an explicit runtime `Handle`; the CLI call site is updated, and no ambient runtime lookup is required.
- Existing destination guards, source completion events, conservative error fencing/leak behavior, and synchronous pool teardown remain. This is **not** a guarantee of formal lock-freedom: semaphore/initialization internals, allocation, CUDA calls and exceptional/teardown synchronization can block.

### Ordinary benchmarks

SF10/Vortex/warm/buffered/GPU 0, **30 samples each for read and full Q5**, sequential idle checks. Numbers are `bench.py` host-timer process means; negative change is faster. All strict settings/input/result comparisons passed.

| Pair                              | Read before → after ms | Read change | Q5 before → after ms | Q5 change |
| --------------------------------- | ---------------------: | ----------: | -------------------: | --------: |
| Initial prototype                 |        15.866 → 15.907 |      +0.26% |      21.109 → 20.800 |    −1.47% |
| Reviewed version, candidate first |        15.819 → 15.668 |      −0.95% |      21.291 → 20.751 |    −2.53% |
| Reviewed version, baseline first  |        15.843 → 15.959 |      +0.73% |      21.008 → 20.852 |    −0.74% |

Mean paired Q5 change **−1.58%**, read **+0.01%** across all three. The **final reviewed build alone** has two favorable pairs, mean Q5 **−1.64%**, read **−0.11%**. Initial prototype predates the review fixes and is not the identical final binary. Magnitude varies; no formal significance claim, proof of gap closure, or measured allocation-count claim. Retained as a modest repeatable Q5 benefit, not the original ≥30% target.

Raw JSON/log prefixes: `results/slots-{before,after}-20261009`, `results/slots-{before,after}-reverse-20261009`, and `results/slots-{before,after}-confirm-20261009`.

### Review and validation

- Fixed explicit-runtime panic, moved object-store checkout after initial request, corrected wrong-class assertions, checked pending fence state before preparation, and verified the final staging permit is returned after cancellation/chunking.
- `try_get` scans a bounded queue snapshot rather than giving up on the first pending buffer. Allocation miss accounting now occurs at the actual allocation attempt, not a cancelled vacant reservation. Capacity stored in the class; impossible semaphore closure expressed as an invariant.
- Memory reads intentionally retain the blocking-worker hop; no second inline-copy fast path was added. Cross-file/object-store throughput is **not benchmarked**. Constructor change requires callers to supply a `Handle`.
- Release Q5 builds passed before and after review. Focused release tests: **42 passed, 0 failed, 0 ignored** for `pinned::tests` and `pooled_read_at::`, including DMA reuse with automatic tracking disabled, error fences, pool teardown, one-slot backpressure/cancellation, direct-range planning, and explicit-runtime memory reads without ambient Tokio. Full Rust/FFI suite and CLI binary were not built/tested.
- First test compile rejected `vortex_expect` on Tokio's `AcquireError`; corrected the impossible-closure assertion and reran successfully.
- Formatting passed using pinned `nightly-2026-09-10` on the six changed Rust files; `git diff --check` passed.

Initial candidate Q5 binary SHA256: `20fbd4050ef53d7170eb6cebaeaa9eb7e00805c8dbaa4bc29cbf87d24e247ba6`. Saved candidate: `cpp/build/NDSH_Q05_BOUNDED_SLOTS_20261009`. Saved cache-only baseline remains `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`, hash `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`.

## Earlier ownership review — superseded proposal (not implemented as written)

The buffers are **already uniquely owned**: `PooledPinnedBuffer` moves from the reader to upload, then its inner allocation moves into session-owned retention. The shared resources are the pool containers, not the individual buffer contents.

Current lock coupling in `vortex-cuda/src/pinned.rs`:

- Every `try_get` locks `inflight` and scans all completion events before checking the ready bucket.
- That lock spans CUDA context binding/event queries, nested bucket returns, and potentially overflow-buffer destruction. `put` drops overflow outside the bucket lock but **still inside the caller's inflight lock**.
- Upload's post-copy retention needs the same `inflight` lock, so unrelated reclamation can block further submission.

Concrete candidate: **per-size-class ready and pending concurrent queues**, with no dedicated recycler thread.

1. Checkout first pops a ready buffer. On a miss, a reader pops pending candidates and queries their events while exclusively owning them, outside shared locks.
2. Query only a bounded amount of pending work per checkout; return unfinished buffers to pending, reuse completed ones, and retain allocation fallback. Queue emptiness/length is not a reliable readiness claim under concurrency.
3. Upload transfers ownership directly into pending after its completion fence is recorded. Keep the existing error fencing and conservative failure behavior.
4. Bound ready caches with the existing per-class limit; only completed overflow allocations may be destroyed on that reclamation path. Pending retention must not discard a live DMA source when a queue fills.
5. Use fixed power-of-two class indexing (including zero) instead of a shared size-map mutex. Use an established concurrent-queue implementation, not custom unsafe atomics; `crossbeam-queue` is already transitive but would need an explicit dependency.

This avoids a global scan lock and does not detach whole batches behind another scanner. Unlike the rejected targeted scan, event queries happen under exclusive buffer ownership, not the shared inflight mutex. It is **mutex-free pool bookkeeping**, not a claim that queue allocation, CUDA APIs, or the entire pipeline are formally lock-free.

Tradeoffs to discuss before implementing: queue/atomic overhead, extra allocations while another reader privately checks a candidate, pending-scan fairness, and ready-first behavior delaying reclamation. Retain session/context isolation, source/destination guards, cancellation/drop fences, and shutdown safety. Do not simply return a still-pending buffer for host mutation: object-store and in-memory readers also use this pool, and that could move a synchronous GPU wait onto an async executor.

No source changes, builds, tests, or new benchmark/profile runs for this review. Speedup remains unproven; ordinary before/after measurements and rejection policy still apply.

## Measurement policy

SF10, warm buffered, GPU 0, 30 samples each read/full Q5. Initial fresh saved-baseline result: `results/gaps-baseline.json`. Only keep a repeatable full-Q5 improvement without meaningful read regression; confirmation against a contemporaneous baseline for small gains. No profile timings as performance evidence. Commit successful source changes with sign-off; revert unsuccessful attempts only in agent-owned edits.
