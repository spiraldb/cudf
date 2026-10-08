# Issue 4 — repeated function lookup enters driver locks

Status: function cache committed as Vortex `93e2931adb`; event-lifecycle attempt rejected and reverted.

[GPU_IDLE_GAPS.md](GPU_IDLE_GAPS.md), rank 8: 41.109632–41.334080 ms, 0.224448 ms without recorded work. The submitter blocks in driver `pthread_rwlock_wrlock`; resolved stack includes `cuModuleGetFunction → CudaModule::load_function → CudaSession::load_function_with_suffixes`. Event record and launch also encounter locks, so lookup is not the entire gap.

`vortex-cuda/src/kernel/mod.rs` caches modules but looks up function handles repeatedly. Candidate: cache functions with unambiguous module/function identity and CUDA-context lifetime, scoped to the existing session loader. Do not cache failures or mix contexts. Alternative: eliminate redundant default launch-strategy event creation only if API semantics permit; keep public event-returning paths and tracing behavior intact.

## Attempt A: session-scoped function cache

Successful `(module_name, function_name)` lookups retain cloned `CudaFunction` handles in the existing session-scoped loader; handles retain their module/context. Failed lookups are not cached. No decoded-data cache or execution/lifetime change.

| Pair         | Baseline read/Q5 ms | Cache read/Q5 ms | Q5 change |
| ------------ | ------------------- | ---------------- | --------: |
| Initial      | 15.776 / 21.051     | 15.795 / 20.762  |    −1.37% |
| Confirmation | 15.731 / 20.979     | 15.798 / 20.716  |    −1.25% |

Additional end-of-session checks:

| Pair                                                          | Baseline read/Q5 ms | Cache read/Q5 ms | Q5 change |
| ------------------------------------------------------------- | ------------------- | ---------------- | --------: |
| Reverse order (`cache-reverse` ran before `baseline-reverse`) | 16.023 / 21.025     | 15.835 / 21.008  |    −0.08% |
| Final (`baseline-final` → `cache-final`)                      | 15.860 / 20.979     | 15.839 / 20.792  |    −0.89% |

Mean of four paired percentage changes: Q5 −0.90%, read −0.19%. All Q5 signs favor the cache but one is effectively zero; magnitude is modest and process variation remains. Initial read changes +0.12%/+0.43%; 30 samples/state/run, identical input/result signatures. Modest repeated full-Q5 improvement, not formal significance or proof of gap closure. Results: `gaps-baseline{,-confirm}.json`, `issue4-function-cache{,-confirm}.json`.

Validation: release Q5 build passed. Targeted `cargo test ... --lib kernel::tests -- --test-threads=1`: one actual CUDA test passed (not ignored), including repeated lookup, failure not cached, and launching a returned handle after loader destruction. Independent read-only review found no blocking correctness issue. Test cardinality alone does not prove the hit path; this is a known test limitation.

## Attempt B: unused strategy-event bracketing

Independent follow-up against the committed cache baseline. Default launch strategy ignores its two per-launch events, but they are still created/recorded/destroyed. Explicit opt-out for that strategy only; custom/tracing strategies retain events by default. Preserve cudarc argument read/write tracking and context binding for external launches. Measured in `results/issue4-default-events.json`: read 15.777 ms (−0.13%), Q5 20.885 ms (+0.82%) versus cache confirmation. Release Q5 build and result signatures passed. No full-Q5 benefit; reverted without further test rounds. Only the function cache remains.
