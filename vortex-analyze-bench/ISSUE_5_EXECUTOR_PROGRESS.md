# Issue 5 — read completion / executor progress

Status: three scheduling attempts measured and reverted. No repeatable full-Q5 gain or proven ordinary-run readiness defect.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 7: 33.380512–33.726112 ms, 0.345600 ms without recorded work. Gap-ending H2D submitter is parked in `CurrentThreadIterator::next` for 0.258798 ms of the gap. Input completion and task-readiness times are absent. Startup gap rank 2 additionally includes scan opening, I/O and allocation; it is not one scheduler delay.

`ReadDriver::poll_next` resolves a request and returns a ready item; its collecting task can immediately poll more results. Synchronous transfer/setup executes during async polling. Shared executor means caller TID is not input/task identity.

Candidate after confirmed producer obstructions: bounded cooperative yielding after resolving reads, preserving refill/cancellation behavior. Alternative only if justified: change completion delivery locality, not blindly add workers (two workers previously showed no benefit). Benchmark each separately; do not claim scheduling causality from a speedup alone.

## Attempts against function-cache baseline `93e2931adb`

| Attempt / result file                                                                 | Read ms |  Q5 ms | Read/Q5 change  |
| ------------------------------------------------------------------------------------- | ------: | -----: | --------------- |
| Yield after every resolved read (`issue5-yield-each`)                                 |  15.928 | 21.010 | +0.83% / +1.42% |
| Refill slots, yield after eight consecutive ready completions (`issue5-yield-budget`) |  15.723 | 20.835 | −0.47% / +0.57% |

30 samples/state, release build passed and all input/result signatures matched. Neither improved full Q5; both reverted without further tests/repeats. This does not prove scheduling is optimal, but does reject these fairness changes for the measured workload. No extra background-worker experiment or custom instrumentation.

## Attempt C — submit uploads from existing blocking file-read workers

User approved moving `pread → H2D submission` into the same blocking job, avoiding the intervening consumer wake/poll. No new streams, workers, tuning knobs, or dependency changes. Small reads allocated/uploaded in the worker; large reads retained one shared destination with a per-allocation mutex around managed chunk-copy submission only. Workers retained destination ownership across cancellation/error, and the existing pinned pool protected DMA sources. Changes were confined to `vortex-cuda/src/pooled_read_at/file.rs` and its tests.

Release Q5 build passed (1m49s). Targeted release `vortex-cuda` tests filtered to `pooled_read_at::file::tests` passed: **11 passed, zero failed/ignored** (test build 8m01s, execution 1.73s). Includes cancellation, early short-read failure, reverse chunk completion, padded inputs, and two new deterministic tests establishing upload progress without repolling the consumer. Full suites were not run.

30 samples per workload/process; SF10 warm buffered; sequential jobs with idle checks; no profiler:

| Pair          | Baseline read / Q5 ms | Worker-upload read / Q5 ms | Read change | Q5 change |
| ------------- | --------------------: | -------------------------: | ----------: | --------: |
| Initial       |       15.823 / 20.899 |            15.808 / 21.008 |      −0.09% |    +0.52% |
| Reverse order |       15.983 / 21.043 |            15.804 / 20.729 |      −1.12% |    −1.49% |
| Confirmation  |       15.908 / 20.990 |            15.877 / 21.133 |      −0.20% |    +0.68% |

Results use `results/worker-upload-{before,after}[-reverse|-confirm]-20261009.json`. All three strict comparisons and benchmark reference/projection checks passed. The user interrupted the fourth pair during `worker-upload-after-confirm-reverse-20261009`; it has no paired baseline and is not included. Its orphan workload exited before the attempted termination; GPU process list was empty before restoration. No automatic retry.

**Reverted at user request; no repeatable full-Q5 benefit.** Tests show the mechanism changed as intended, but do not establish that consumer polling explains ordinary-run GPU stalls. No claim that driver contention caused the mixed outcome.

Patch archived as [worker-upload.patch](worker-upload.patch). Vortex restored to clean `93e2931adb`; main Q5 restored from the saved baseline, SHA-256 `5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1`. Candidate SHA-256 was `98b698be9424a3ea8bcee774b1ddd9707dae2792f849a7ac27686bc508035529`. Build intermediates still require an explicit rebuild before future source experiments. No commits or pushes.
