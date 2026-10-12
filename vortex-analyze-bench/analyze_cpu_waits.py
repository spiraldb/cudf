#!/usr/bin/env python3
"""Reconstruct CPU waits inside each thread's own vortex.read ranges (read-only).

Times are exported trace nanoseconds; sums are thread-time, not wall time.
S/D time ends at sched_wakeup and includes any waking-to-wakeup activation.
Only waking events between the paired switch-out and wakeup count as activation;
a waking event from an earlier cycle must not be carried forward.
"""

import argparse
import collections
import json
from pathlib import Path
import sqlite3
import statistics


TID_MASK = (1 << 24) - 1
# Linux sched_switch encoding printed by this trace's ftraceFormat:
# S=1, D=2, T=4, t=8, X=16, Z=32, P=64, I=128; '+' (preempted)=256.
STATE_MASK = 0xFF
PREEMPTED = 0x100
CATEGORIES = ("on_cpu", "S_to_wakeup", "D_to_wakeup", "other_to_wakeup",
              "wakeup_to_run", "runnable_switchout", "unknown")


def state_category(raw):
    state = raw & STATE_MASK
    if raw & ~(STATE_MASK | PREEMPTED):
        return "other_to_wakeup"
    if state == 0:
        return "runnable_switchout"  # R/R+: runnable without needing a wakeup.
    if state & 2:
        return "D_to_wakeup"
    if state & 1:
        return "S_to_wakeup"
    return "other_to_wakeup"


def latency_stats(cycles):
    values = sorted(run - wake for out, waking, wake, run in cycles)
    if not values:
        return None
    # Match the original analysis: order statistic floor(p * (n - 1)).
    return dict(median=statistics.median(values) / 1000,
                p95=values[int(.95 * (len(values) - 1))] / 1000,
                p99=values[int(.99 * (len(values) - 1))] / 1000,
                max=values[-1] / 1000)


def summarize(tid, windows, events):
    times = collections.Counter()
    states = collections.Counter()
    cycles = []
    partial_cycles = 0
    state, last, out, waking, wake = "unknown", 0, None, None, None

    def overlap(a, b):
        return sum(max(0, min(b, y) - max(a, x)) for x, y in windows)

    for timestamp, kind, value in events:
        times[state] += overlap(last, timestamp)
        last = timestamp
        if kind == "out":
            state = state_category(value)
            out, waking, wake = timestamp, None, None
            if any(a <= timestamp < b for a, b in windows):
                states[str(value)] += 1
        elif kind == "waking":
            if out is not None:
                waking = timestamp
        elif kind == "wakeup":
            if state != "on_cpu":
                state = "wakeup_to_run"
                wake = timestamp
        elif kind == "in":
            if wake is not None:
                if out is not None and any(a <= out <= wake <= timestamp <= b
                                           for a, b in windows):
                    cycles.append((out, waking, wake, timestamp))
                elif overlap(wake, timestamp):
                    partial_cycles += 1
            state, out, waking, wake = "on_cpu", None, None, None
    times[state] += overlap(last, windows[-1][1])
    elapsed = sum(b - a for a, b in windows)
    assert sum(times.values()) == elapsed, "scheduler partition lost/duplicated time"
    return {"tid": tid, "windows_ns": windows}, times, states, cycles, partial_cycles


def result(metadata, times, states, cycles, partial):
    observed_activation = [(waking - out, wake - waking)
                           for out, waking, wake, run in cycles
                           if waking is not None and out <= waking <= wake]
    ready_sum = sum(run - wake for out, waking, wake, run in cycles)
    accounted = sum(times.values())
    if not partial and not times["unknown"]:
        assert ready_sum == times["wakeup_to_run"], "unpaired runnable interval"
    longest = max(cycles, key=lambda c: c[3] - c[2], default=None)
    return dict(metadata,
                elapsed_ms=round(accounted / 1e6, 6),
                time_ms={k: round(times[k] / 1e6, 6) for k in CATEGORIES if times[k]},
                switchout_raw_states=dict(sorted(states.items())),
                wake_cycles=len(cycles),
                wake_to_run_us=latency_stats(cycles),
                max_wake_to_run_ns=list(longest[2:]) if longest else None,
                waking_between_out_and_wakeup={
                    "cycles": len(observed_activation),
                    "activation_ms": round(sum(x[1] for x in observed_activation) / 1e6, 6)},
                invariants={"partition_ns": accounted,
                            "complete_state_coverage": not times["unknown"],
                            "partial_wake_cycles": partial,
                            "wake_cycle_sum_matches_runnable": ready_sum == times["wakeup_to_run"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    args = parser.parse_args()
    path = args.sqlite_path.resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        reads = list(db.execute("""
            SELECT n.globalTid, n.start, n.end FROM NVTX_EVENTS n
            LEFT JOIN StringIds s ON s.id=n.textId
            WHERE coalesce(n.text,s.value)='vortex.read' ORDER BY n.start
        """))
        if not reads or any(end is None or end <= start for tid, start, end in reads):
            raise ValueError("expected completed, positive-duration vortex.read ranges")
        pids = {(tid >> 24) & TID_MASK for tid, start, end in reads}
        if len(pids) != 1:
            raise ValueError("vortex.read ranges span multiple PIDs; refusing to mix processes")
        windows = collections.defaultdict(list)
        for global_tid, start, end in reads:
            windows[global_tid & TID_MASK].append((start, end))
        for ranges in windows.values():
            if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
                raise ValueError("overlapping own-thread ranges would double-count time")

        schemas = {}
        for type_id, name, fmt in db.execute("""
            SELECT t.typeId,s.value,t.ftraceFormat FROM GENERIC_EVENT_TYPES t
            JOIN StringIds s ON s.id=t.nameId
        """):
            if name in ("sched:sched_switch", "sched:sched_wakeup", "sched:sched_waking"):
                schemas[type_id] = name.split(":sched_")[1]
                if name.endswith("sched_switch") and (not fmt or "0x00000080" not in fmt):
                    raise ValueError("unrecognized sched_switch state encoding")
        if set(schemas.values()) != {"switch", "wakeup", "waking"}:
            raise ValueError("all three raw scheduler event types are required")
        fields = {(t, i): name for t, i, name in db.execute("""
            SELECT f.typeId,f.fieldIdx,s.value FROM GENERIC_EVENT_TYPE_FIELDS f
            JOIN StringIds s ON s.id=f.fieldNameId
        """)}
        payload = collections.defaultdict(dict)
        raw = list(db.execute("""
            SELECT genericEventId,timestamp,typeId FROM GENERIC_EVENTS
            ORDER BY timestamp,genericEventId
        """))
        types = {event_id: type_id for event_id, timestamp, type_id in raw}
        for event_id, index, value in db.execute("""
            SELECT genericEventId,fieldIdx,coalesce(intVal,uintVal) FROM GENERIC_EVENT_DATA
        """):
            if types[event_id] in schemas:
                payload[event_id][fields[types[event_id], index]] = value
        events = collections.defaultdict(list)
        counts = collections.Counter()
        for event_id, timestamp, type_id in raw:
            if type_id not in schemas:
                continue
            kind, data = schemas[type_id], payload[event_id]
            counts[kind] += 1
            changes = ([(data["prev_pid"], "out", data["prev_state"]),
                        (data["next_pid"], "in", None)] if kind == "switch"
                       else [(data["pid"], kind, None)])
            for tid, action, value in changes:
                if tid in windows:
                    events[tid].append((timestamp, action, value))

        per_thread = []
        total_times, total_states = collections.Counter(), collections.Counter()
        total_cycles, total_partial = [], 0
        for tid, ranges in sorted(windows.items()):
            metadata, times, states, cycles, partial = summarize(tid, ranges, events[tid])
            per_thread.append(result(metadata, times, states, cycles, partial))
            total_times.update(times)
            total_states.update(states)
            total_cycles.extend(cycles)
            total_partial += partial
        expected = sum(end - start for tid, start, end in reads)
        assert sum(total_times.values()) == expected
        output = dict(pid=pids.pop(), tid_mask=hex(TID_MASK),
                      envelope_ns=[min(r[1] for r in reads), max(r[2] for r in reads)],
                      raw_event_counts=dict(counts),
                      interpretation={
                          "units": "ms thread-time; latency us; timestamps ns",
                          "state_mask": "0xff; S=1, D=2, preemption flag=0x100",
                          "S_D": "switch-out to wakeup, INCLUDING waking activation; not runnable delay",
                          "R_Rplus": "separate runnable_switchout bucket; no wakeup required",
                          "waking": "only out<=waking<=wakeup; absent brackets are not inferred",
                          "percentiles": "sorted[floor(p*(n-1))]; median via statistics.median"},
                      per_thread=per_thread,
                      aggregate=result({}, total_times, total_states, total_cycles, total_partial))
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
