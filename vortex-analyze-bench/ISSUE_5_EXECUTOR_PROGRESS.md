# Issue 5 — read completion / executor progress

Status: two scheduling attempts measured, rejected and reverted. No proven readiness defect.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 7: 33.380512–33.726112 ms, 0.345600 ms without recorded work. Gap-ending H2D submitter is parked in `CurrentThreadIterator::next` for 0.258798 ms of the gap. Input completion and task-readiness times are absent. Startup gap rank 2 additionally includes scan opening, I/O and allocation; it is not one scheduler delay.

`ReadDriver::poll_next` resolves a request and returns a ready item; its collecting task can immediately poll more results. Synchronous transfer/setup executes during async polling. Shared executor means caller TID is not input/task identity.

Candidate after confirmed producer obstructions: bounded cooperative yielding after resolving reads, preserving refill/cancellation behavior. Alternative only if justified: change completion delivery locality, not blindly add workers (two workers previously showed no benefit). Benchmark each separately; do not claim scheduling causality from a speedup alone.

## Attempts against function-cache baseline `93e2931adb`

| Attempt / result file                                                                 | Read ms |  Q5 ms | Read/Q5 change  |
| ------------------------------------------------------------------------------------- | ------: | -----: | --------------- |
| Yield after every resolved read (`issue5-yield-each`)                                 |  15.928 | 21.010 | +0.83% / +1.42% |
| Refill slots, yield after eight consecutive ready completions (`issue5-yield-budget`) |  15.723 | 20.835 | −0.47% / +0.57% |

30 samples/state, release build passed and all input/result signatures matched. Neither improved full Q5; both reverted without further tests/repeats. This does not prove scheduling is optimal, but does reject these fairness changes for the measured workload. No extra background-worker experiment or custom instrumentation.
