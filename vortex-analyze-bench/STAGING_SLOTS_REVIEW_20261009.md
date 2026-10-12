# Bounded staging slots: review hardening — 2026-10-09

## Final decision: entire experiment reverted

User rejected the complexity relative to the modest measured benefit. **`7d2dd32046`** reverts `7e433fe0f6`, `272f920cb5`, and `a4955b31ac` together; source tree exactly matches retained function-cache baseline `93e2931adb`. Working tree clean, signed off, not pushed. Active Q5 restored from `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008`, verified SHA256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`.

The four subsequent simplification passes are preserved in stash `72cd1a7ca01fcfd12c6349e44b906c6e9df50f26`, based on `7e433fe0f6`. Original commits, saved binaries and results remain intact. No hard reset or new profiling/build/test/benchmark run. All sections below describe historical states and decisions, not currently applied changes.

## Historical review: FIFO admission and batched uploads

**Reviewed Vortex commit: `7e433fe0f6`**, on `ad/cuda-gap-investigation`, signed off and not pushed. Source was clean. This supersedes the passive admission, per-network-chunk staging, and inline memory fast path described in the historical round below.

- **File admission:** plain `await file permit → await pool slot → blocking prepare/read`. All reader kinds queue on the pool semaphore. Removed `wait_available`, `Notify`, wake-all release handling, retry admission, and redundant closure checks. FIFO means semaphore acquisition order, not global request order. Same-file head-of-line blocking across size classes is an explicit accepted tradeoff.
- **Network batching:** collect zero-copy `Bytes` slices up to 8 MiB / 4,096 pieces before acquiring staging. One worker job and H2D per batch; flush the tail at EOF. No pinned lease across network waits. The regression for 16 MiB delivered in 8 KiB chunks verifies **2 staging jobs instead of 2,048**. This is a structural test, not a network-throughput measurement. Batching delays the first upload; slices may retain larger backing allocations, so 8 MiB is not a total network-memory bound. Added an explicit dependency on the already-locked `bytes` package.
- **Memory reads:** removed the unbounded inline fast path. Preparation, CPU copy, device allocation and upload submission run together on one blocking worker, including cached buffers; zero-length allocation is also offloaded. This does not move every CUDA operation in other reader types off async workers.
- **Test injection:** removed production generic `try_get_with` / `prepare_with`; fault/query hooks are pool-local and `#[cfg(test)]` only. Preserved failed-buffer eviction and conservative DMA lifetime guards.

### Latest validation

- Release Q5 rebuild passed (20-minute limit; Cargo finished in 1m42s).
- **94 focused release tests passed, 0 failed/ignored**, covering FIFO across reader kinds, queued/assigned cancellation, accepted same-file head-of-line behavior, DMA fencing, batching boundaries/short/long/error streams, slow-body slot availability, and memory worker execution. Log: `results/slots-fifo-tests-20261009.log`.
- Library Clippy passed with `--no-deps --lib --features _test-harness -- -D warnings`. Test Clippy remains blocked by six pre-existing disallowed `std::collections::HashSet` uses in untouched `session.rs` and `stream_pool.rs`. Logs: `results/slots-fifo-clippy-{lib,tests}-20261009.log`.
- Pinned `nightly-2026-09-10` formatting check and `git diff --check` passed. Independent read-only review found no actionable correctness finding; this is not a proof of fairness or fault recovery.
- Full suites, real remote-network throughput and actual filesystem O_DIRECT execution were not validated. No profiling.

### Latest ordinary benchmark

Baseline: `272f920cb5`, not cache-only or the initial slots commit. SF10/Vortex/warm/buffered/GPU 0; 30 samples each read/full Q5 per run. Sequential GPU runs with idle checks. All settings/input/result-signature comparisons passed.

| Pair            | Read before → after ms | Read change |  Q5 before → after ms | Q5 change |
| --------------- | ---------------------: | ----------: | --------------------: | --------: |
| Baseline first  |  15.805227 → 15.734683 |      −0.45% | 20.734645 → 20.812059 |    +0.37% |
| Candidate first |  15.758506 → 15.824562 |      +0.42% | 20.878193 → 20.958421 |    +0.38% |

**Q5 is about 0.4% slower in both comparisons; read is mixed. No added speedup is claimed.** Retained as the requested correctness/structural repair, not as a winning optimization. Two pairs do not establish statistical significance, and Q5 does not measure the remote-network batching benefit. Raw JSON/log prefixes: `results/slots-fifo-{before,after}{,-reverse}-20261009`.

Current executable: `cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH`, restored after baseline comparison; identical saved copy: `cpp/build/NDSH_Q05_BOUNDED_SLOTS_FIFO_20261009`. SHA256: `9a4410607a0b9f4c81c6b2c88953b3c0f323cc4603956ad9835f4efb6878bd24`. Earlier saved executables remain intact. Local CMake override is unchanged; user-owned `VORTEX_INTEGRATION_CONTEXT.md` was not touched. Discuss the next strategy before another experiment.

---

## Historical review round: `272f920cb5`

The sections below describe that earlier commit, not the current admission/batching/memory paths.

**Retained follow-up:** Vortex commit `272f920cb5` (`fix(cuda): harden staging ownership and reader admission`), on top of `a4955b31ac`. Signed off, not pushed. User requested a proper resolution of the second review, not just the two easiest fixes.

This is correctness/API/progress hardening of the retained slot design, **not a newly demonstrated throughput optimization**. No profiler run, new tuning knobs, background recycler, or weakened CUDA lifetime guards.

## Review findings and resolution

| Finding                                                         | Resolution                                                                                                                                                                                                                                                                                                                                             |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Failed preparation recycles a bad buffer                        | Remove allocation ownership from the lease before the completion wait. Failure destroys/evicts it rather than requeueing it. Existing destructor fencing remains: unknown DMA completion must never permit an unsafe free. Failure injection verifies permit return, eviction and replacement without intentionally faulting the GPU context.          |
| Direct-I/O staging length unchecked                             | Validate the actual slice against the widened physical read length before issuing I/O. Buffered-file backend also validates its length. Regression deliberately supplies the wrong size.                                                                                                                                                               |
| Concurrent `try_get` can cause an extra allocation              | Allowed within the live-slot bound, now documented and covered by concurrent ownership/conservation tests. No global allocation lock added merely to guarantee maximal reuse. The bound excludes allocations deliberately leaked after unrecoverable CUDA failures.                                                                                    |
| Slow object-store body retains scarce pinned memory             | Stage only bytes already received, in pieces ≤8 MiB, into one managed device destination. Each staging lease returns before awaiting more network data. No whole-body host accumulation. Tests cover delayed bodies with a one-slot pool, short/long/error bodies, and cancellation. Device destination memory remains live while the body is pending. |
| File admission occupies permits while waiting for pool capacity | Use a **passive** pool-availability hint, then acquire file admission and try to reserve the pool slot. If capacity disappeared, release file admission and retry. Neither resource is reserved while awaiting the other. Strict starvation freedom under perpetual contention is not claimed.                                                         |
| Object-store file payload takes two blocking hops               | Acquire a slot, then prepare and perform `read_exact_at` in one blocking job. Counting-executor test checks one hop and offsets above 4 GiB.                                                                                                                                                                                                           |
| Memory read always takes a blocking hop                         | Already-ready buffers use inline CPU copy/upload via `try_get`; pending/vacant slots retain the explicit-runtime blocking path. Bounds are validated before pool access. Tests count misses and ready hits.                                                                                                                                            |
| Public constructor changed                                      | Kept the explicit `Handle`, avoiding implicit runtime lookup. Documented old/new signatures and runtime-lifetime requirements in `vortex-cuda/README.md`; the CLI call site was already migrated by the parent commit.                                                                                                                                 |
| Duplicated acquisition logic / binding empty queue              | Shared lease construction, permit handling and counters; empty `try_get` avoids CUDA binding. Completion scan stays bounded. Passive waiters are notified after every reservation-release path, including failed/empty fast paths and assigned-acquisition cancellation.                                                                               |
| Duplicate logical length can diverge                            | Truncation updates both fields. Repeated preparation must not expand a truncated buffer; regression covers nonzero and empty truncation.                                                                                                                                                                                                               |

### Important intermediate design rejected before benchmarking

A first two-resource protocol alternated real pool/file semaphore acquisitions and rolled back when the other resource was unavailable. Independent review identified a **three-reader livelock**: fair semaphores can assign released permits to waiting futures that are not yet polled, circulating reservations without starting I/O.

That round's protocol did **not** queue for a pool permit while seeking file admission. `wait_available` registers a notification before checking capacity, makes no reservation, and returns only a hint. `try_acquire` is the actual reservation. The normal available path skips notification registration. `SlotPermit` releases capacity **before** waking passive waiters; cancellation of an assigned async acquisition also notifies them.

A deterministic three-waiter regression controls polling order and verifies that the file-permit holder progresses. Additional regressions check unrelated size classes on the same file and a file waiter that must not reserve shared staging capacity. No shared CUDA-event scan lock was reintroduced.

## Validation

- Release Q5 build passed, including final lint/documentation cleanup.
- **84 focused release tests passed; 0 failed, 0 ignored.** Filters: `pinned::tests` and `pooled_read_at::`, `--features _test-harness -- --test-threads=1`. Covers the existing DMA/source/destination safeguards plus the new cases above. The successful execution preceded final lint-only test naming/import/conversion cleanup; final tests also type-checked during Clippy apart from the unrelated lints below.
- Initial test compilation found a missing test-only `tempfile` dependency and a prohibited `let _ =` drop. Corrected without adding a dependency; the direct-read validation test uses `/dev/null` and rejects before I/O.
- **Library Clippy passed:** `cargo clippy --no-deps --locked --offline -p vortex-cuda --release --target aarch64-unknown-linux-gnu --target-dir <existing CMake cargo-target> --lib --features _test-harness -- -D warnings`.
- Test Clippy remains blocked by **six existing `std::collections::HashSet` diagnostics** in untouched `vortex-cuda/src/session.rs` and `stream_pool.rs`. No remaining reported lint is in the changed implementation/tests. An earlier dependency-inclusive run stopped at existing `vortex-buffer/src/trusted_len.rs:33` (`clippy::manual_map`). These unrelated files were not modified. Logs: `results/slots-harden-clippy-{lib,tests}-20261009.log`.
- Pinned `nightly-2026-09-10` rustfmt check passed for the six changed Rust files. `git diff --check` passed.
- Independent read-only final review found no remaining actionable correctness/safety/progress defect. This is not a proof of CUDA fault recovery or fairness under sustained load.
- Full Rust/FFI suites, the CLI build, real remote-network performance, and actual filesystem O_DIRECT execution were not validated in this pass.

## Ordinary benchmark check

Baseline is the previously retained **`a4955b31ac` slot build**, not cache-only `93e2931adb`. SF10/Vortex/warm/buffered/GPU 0, 30 samples each read/full Q5, host-timer means from `bench.py`. Sequential runs with idle checks; all settings/input/result-signature comparisons passed.

| Pair                  | Read before → after ms | Read change |  Q5 before → after ms | Q5 change |
| --------------------- | ---------------------: | ----------: | --------------------: | --------: |
| Baseline first        |  15.791429 → 15.798766 |      +0.05% | 20.735481 → 20.709788 |    −0.12% |
| Revised version first |  15.820387 → 15.817943 |      −0.02% | 20.889416 → 20.802714 |    −0.42% |

**Effectively unchanged within run variability; no observed regression, no claim of an additional speedup.** The first pair precedes a lint-only annotation/formatting change; the reverse pair uses the final executable. The original slots-versus-cache measurements remain historical evidence, not a fresh cache comparison of this exact build.

Raw JSON/log prefixes: `results/slots-harden-{before,after}-20261009` and `results/slots-harden-{before,after}-reverse-20261009`.

## Historical final state (`272f920cb5`)

- Vortex: branch `ad/cuda-gap-investigation`, HEAD `272f920cb5`.
- cuDF local Vortex override unchanged. User-owned `VORTEX_INTEGRATION_CONTEXT.md` untouched.
- Active Q5: `cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH`.
- Saved identical executable: `cpp/build/NDSH_Q05_BOUNDED_SLOTS_HARDENED_20261009`.
- SHA256: `aad1e8f08dfbad2a17da1e58a1498f1fc4e19260ee981f9549b29f26ab620957`.
- Earlier saved slots executable `cpp/build/NDSH_Q05_BOUNDED_SLOTS_20261009` and cache-only baseline `cpp/build/NDSH_Q05_FUNCTION_CACHE_20261008` preserved.
- Revised executable restored after baseline comparison. GPU process list empty. No more automatic runs; discuss the next strategy first.
