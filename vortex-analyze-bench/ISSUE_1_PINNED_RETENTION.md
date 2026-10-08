# Issue 1 — pinned retention blocks GPU producers

Status: two new attempts rejected and reverted; no retained change or commit.

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

## Measurement policy

SF10, warm buffered, GPU 0, 30 samples each read/full Q5. Initial fresh saved-baseline result: `results/gaps-baseline.json`. Only keep a repeatable full-Q5 improvement without meaningful read regression; confirmation against a contemporaneous baseline for small gains. No profile timings as performance evidence. Commit successful source changes with sign-off; revert unsuccessful attempts only in agent-owned edits.
