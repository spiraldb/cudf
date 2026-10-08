# GPU-idle investigation results — 2026-10-08

## Outcome

**One small retained improvement; seven rejected candidates reverted; two issues blocked by insufficient evidence.** No new profiler capture was needed. See [GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md) for the CPU/GPU evidence and the six linked issue reports in README.

Retained Vortex commit: **`93e2931adb` — `perf(cuda): cache function handles within each session`**. No push. The final Q5 executable was rebuilt and exactly matches the measured cache-only binary (SHA-256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`). Source is clean; cuDF's local Vortex-path override remains unchanged.

## Retained function cache: ordinary paired runs

30 samples per workload per process; SF10, Vortex, warm buffered, idle GPU 0. Negative change is faster. These are separate process means, not independent repeated-query claims based on profile timing.

| Pair (baseline → cache result labels)                     | Read before/after ms | Q5 before/after ms | Q5 change |
| --------------------------------------------------------- | -------------------- | ------------------ | --------: |
| `gaps-baseline` → `issue4-function-cache`                 | 15.776 / 15.795      | 21.051 / 20.762    |    −1.37% |
| `gaps-baseline-confirm` → `issue4-function-cache-confirm` | 15.731 / 15.798      | 20.979 / 20.716    |    −1.25% |
| `baseline-reverse` → `cache-reverse` (cache ran first)    | 16.023 / 15.835      | 21.025 / 21.008    |    −0.08% |
| `baseline-final` → `cache-final`                          | 15.860 / 15.839      | 20.979 / 20.792    |    −0.89% |

Mean paired change: **Q5 −0.90%, read −0.19%**. All four Q5 differences favor the cache, but magnitude varies and one is effectively zero. This is a modest measured benefit, not formal statistical significance, a ≥30% result, or proof that specific GPU gaps disappeared. Initial commit followed two favorable pairs and a targeted CUDA test; later reverse-order/final checks are reported rather than hidden.

All cache comparisons have identical axes/signatures: 76,590,427 input rows; 5,287,549,556 fixture bytes; 51,468 Q5 matched rows; five countries. The benchmark also performs reference Q5 and projection validation before timing.

## Attempt ledger

Each candidate was built/measured independently against the base or retained cache. Labels below identify JSON files under `results/`.

| Issue / attempt                                     |              Read ms |  Q5 ms |                               Q5 change vs comparison | Decision                      |
| --------------------------------------------------- | -------------------: | -----: | ----------------------------------------------------: | ----------------------------- |
| 1: detached reclamation (`issue1-detached`)         |               15.789 | 20.921 |                                −0.62% vs initial base | No established gain; reverted |
| 1: size-targeted reclaim (`issue1-targeted`)        |               15.587 | 21.116 |                                +0.31% vs initial base | Reverted                      |
| 4: function cache                                   | See four pairs above |        |                                                       | Retained/committed            |
| 4: unused strategy events (`issue4-default-events`) |               15.777 | 20.885 |                          +0.82% vs cache confirmation | Reverted                      |
| 5: yield each completion (`issue5-yield-each`)      |               15.928 | 21.010 |                          +1.42% vs cache confirmation | Reverted                      |
| 5: refill + bounded yield (`issue5-yield-budget`)   |               15.723 | 20.835 |                          +0.57% vs cache confirmation | Reverted                      |
| 6: direct synchronous D2H (`issue6-direct-sync`)    |               15.691 | 20.914 |                          +0.95% vs cache confirmation | Reverted                      |
| 6: host FSST metadata (`issue6-fsst-host`)          |               15.719 | 20.660 | −0.27% initially; confirmation −1.61%, reverse +0.88% | Not repeatable; reverted      |

Issue 2 (already-submitted copy) remains unattributed: missing event generations prevent a valid dependency conclusion. Destruction can inject compute waits onto the copy stream, but removing fences is unsafe and does not explain a previously submitted copy by itself.

Issue 3 (allocation/driver work) remains open. Source inspection found the cuDF harness already sets the **current pool's release threshold to 8 GiB**. No redundant threshold tweak, new allocator, or production knob was added.

The FSST writer experiment added 6,672 fixture bytes; all other axes/logical result signatures matched. It was run without `--compare`, and the file-size difference was explicitly inspected rather than bypassing the script's strict same-fixture comparison. Newly written files only; no such writer change remains.

## Validation / scope

- Release Q5 builds passed for all measured candidates. FSST attempt initially failed to compile because `Vec::take` needed `into_iter()`; corrected before any benchmark.
- Retained cache: `cargo test --locked -p vortex-cuda -p vortex-cuda-ffi --release --target aarch64-unknown-linux-gnu --target-dir <existing CMake cargo-target> --lib kernel::tests -- --test-threads=1`: **1 CUDA test passed, 0 ignored**; FFI filter selected 0 tests. Repeated lookup, failed lookup not cached, and handle lifetime after loader destruction are covered. Cache-hit cardinality test has a noted limitation in issue 4.
- Independent source review found no blocking cache lifetime/context issue. Full Rust/FFI suites were not run; rejected attempts did not trigger test matrices.
- Offline SQLite interval/API/scheduler audit passed. No new Nsight/perf run, custom runtime instrumentation, or decoded-data cache.
- GPU jobs were sequential with idle checks. No outstanding build/profiler/benchmark jobs remain.

## Historical result: pinned retention/scanner split

**Normal benchmarks, no profiler, 30 samples each for read and full Q5.** SF10 / Vortex / warm / buffered, same settings and matching result signatures.

| Workload | Before ms |  After ms | Change | Sample stdev before / after |
| -------- | --------: | --------: | -----: | --------------------------: |
| Read     | 15.711944 | 15.898282 | +1.19% |               1.48% / 2.11% |
| Full Q5  | 21.147423 | 20.816571 | −1.56% |               1.62% / 2.01% |

**Conclusion:** small mixed point estimates from one before/after process pair, not an established robust win. Q5 was 0.330852 ms lower; read was 0.186338 ms higher. Within-process sample noise does not quantify between-process repeatability. The user interrupted an extended validation workflow between runs, then authorized diagnostic removal, one rebuild and one normal candidate run.

**Historical decision:** split was parked and source restored to `f12cc07e3`. At that time the build-directory binary still contained the split. **That stale-binary condition has since been resolved by the final rebuild described above.** No split change was committed.

Before diagnostic removal, focused checks passed: 28 default-off pinned/session tests, 34 diagnostics-on pinned/recorder/session tests, 19 file-read tests in each feature configuration, and adapter/resource fixture 2/2. After cleanup, the clean candidate compiled and benchmark fixture/reference validation passed; no extra test rounds. Full Rust suite and FFI tests were not run.

Signatures: 76,590,427 input rows; 5,287,549,556 fixture file bytes; 51,468 matched rows; five countries.

Preserved evidence:

- [Before JSON](results/issue1-before.json), [after JSON](results/issue1-after.json), [comparison](results/issue1-comparison.json).
- [Candidate patch](retention-split.patch), relative to Vortex base HEAD.
- Baseline executable SHA256: `6883f6f3a867228ba107e7373eeaf92679d390922c9c4dc4b58638ccff3afff0`.
- Candidate executable SHA256: `c65246a9ec6e816ed252af616cbe722dbac0c621d357ce54aa5ec4e5e54b6373`.
- Full logs, source snapshots and provenance are retained under `cpp/build/vortex-bench-history-20261008/runs/issue1-retention-20261008/`.
