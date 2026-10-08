#!/usr/bin/env python3
"""Read-only, CPU-only audit of the archived scheduler trace; prints evidence only.

Run from the repository root. Optional arguments select 1-based gap ranks for
same-submitter scheduler/API/OSRT details. No capture, build, or output files.
"""
import collections
import json
import pathlib
import sqlite3
import sys

case = pathlib.Path(__file__).resolve().parents[1] / "cpp/build/vortex-bench-history-20261008/runs/ndsh-scheduler-gaps-20261008"
con = sqlite3.connect((case / ".local/q5-scheduler-01.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
con.execute("PRAGMA query_only=ON")


def query(sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


def union(intervals):
    result = []
    for a, b in sorted(intervals):
        assert b >= a
        if b == a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def emit(label, value):
    print(label, json.dumps(value, separators=(",", ":")))


mask = (1 << 24) - 1
apis = query("SELECT a.rowid id,a.start,a.end,a.globalTid,a.correlationId,a.returnValue,s.value name FROM CUPTI_ACTIVITY_KIND_RUNTIME a JOIN StringIds s ON s.id=a.nameId")
api_index = collections.defaultdict(list)
for api in apis:
    api_index[api["globalTid"] & ~mask, api["correlationId"]].append(api)
assert all(len(v) == 1 for v in api_index.values())
work = []
for kind, table, label in [("kernel", "KERNEL", "shortName"), ("copy", "MEMCPY", "copyKind"), ("memset", "MEMSET", None)]:
    for row in query("SELECT rowid id,* FROM CUPTI_ACTIVITY_KIND_" + table):
        row["kind"] = kind
        row["name"] = (query("SELECT value FROM StringIds WHERE id=?", (row[label],))[0]["value"] if kind == "kernel" else query("SELECT label FROM ENUM_CUDA_MEMCPY_OPER WHERE id=?", (row[label],))[0]["label"] if kind == "copy" else "memset")
        matches = api_index[row["globalPid"], row["correlationId"]]
        assert len(matches) == 1 and matches[0]["returnValue"] == 0
        row["api"] = matches[0]
        work.append(row)
assert len({(r["globalPid"], r["deviceId"], r["contextId"]) for r in work}) == 1
old = json.loads((case / "gpu_gap_analysis.json").read_text())
assert sorted((r["kind"], r["id"], r["start"], r["end"]) for r in work) == sorted((r["kind"], r["id"], r["start"], r["end"]) for r in old["all_GPU_work"])
reads = query("SELECT start,end,globalTid FROM NVTX_EVENTS n LEFT JOIN StringIds s ON s.id=n.textId WHERE coalesce(n.text,s.value)='vortex.read'")
lo, hi = min(r["start"] for r in reads), max(r["end"] for r in reads)
busy = union((max(lo, r["start"]), min(hi, r["end"])) for r in work if r["start"] < hi and r["end"] > lo)
gaps, cursor = [], lo
for a, b in busy:
    if cursor < a:
        gaps.append((cursor, a))
    cursor = b
if cursor < hi:
    gaps.append((cursor, hi))
# Independent endpoint sweep validates the all-work union's idle duration.
points = collections.Counter({lo: 0, hi: 0})
for r in work:
    a, b = max(lo, r["start"]), min(hi, r["end"])
    if a < b:
        points[a] += 1
        points[b] -= 1
active, previous, idle = 0, lo, 0
for t, delta in sorted(points.items()):
    if not active:
        idle += t - previous
    active += delta
    assert active >= 0
    previous = t
assert active == 0 and idle == sum(b-a for a, b in gaps) == old["windows"]["read_envelope"]["no_recorded_work_ns"]
decoder_streams = {r["streamId"] for r in work if r["kind"] == "kernel" and r["name"].startswith(("bit_unpack_", "dict_", "dynamic_dispatch_", "runend_", "fsst_", "sequence_", "arrow_offsets_"))}
decoders = [r for r in work if r["kind"] == "kernel" and r["streamId"] in decoder_streams]
dlo, dhi = min(r["start"] for r in decoders), max(r["end"] for r in decoders)
didle = sum(max(0, min(b, dhi)-max(a, dlo)) for a, b in gaps)
assert didle == old["windows"]["decoder_internal"]["no_recorded_work_ns"]
emit("AUDIT", dict(read_ns=[lo, hi], work_count=len(work), gap_count=len(gaps), idle_ns=idle, busy_ns=sum(b-a for a,b in busy), decoder_ns=[dlo,dhi], decoder_idle_ns=didle, reads=reads))
names = {r["globalTid"]: r["value"] for r in query("SELECT globalTid,value FROM ThreadNames JOIN StringIds ON nameId=id")}
ranks = set(map(int, sys.argv[1:]))
for rank, (a, b) in enumerate(sorted(gaps, key=lambda g: g[1]-g[0], reverse=True), 1):
    if rank > 12 and rank not in ranks:
        continue
    ends = [r for r in work if r["start"] == b]
    def compact(r):
        return {k: r[k] for k in ("kind", "id", "name", "start", "end", "streamId", "correlationId")} | {"bytes": r.get("bytes"), "api": r["api"], "tid": r["api"]["globalTid"] & mask}
    future = [r for r in work if r["start"] >= b]
    emit("GAP", dict(rank=rank, start=a, end=b, duration=b-a, relative_to_read=[a-lo,b-lo], previous=[compact(r) for r in work if r["end"] == a], next=[compact(r) for r in ends], future_work_apis_completed_at_start=sum(r["api"]["end"] <= a for r in future), earliest_future_work_api_start=min((r["api"]["start"] for r in future), default=None)))
    if rank not in ranks:
        continue
    for tid in sorted({r["api"]["globalTid"] for r in ends}):
        emit("THREAD", dict(tid=tid & mask, globalTid=tid, name=names.get(tid)))
        events = query("SELECT start,cpu,isSchedIn,threadState FROM SCHED_EVENTS WHERE globalTid=? ORDER BY start", (tid,))
        segments = []
        for left, right in zip(events, events[1:]):
            if left["start"] < b and right["start"] > a:
                assert left["isSchedIn"] != right["isSchedIn"], (left, right)
                segments.append(dict(start=left["start"], end=right["start"], on_cpu=bool(left["isSchedIn"]), cpu=left["cpu"], threadState=left["threadState"], overlap_ns=min(b,right["start"])-max(a,left["start"])))
        emit("SCHED", segments)
        same = sorted((r for r in apis if r["globalTid"] == tid), key=lambda r:r["start"])
        overlap = [r for r in same if r["start"] < b and r["end"] > a]
        emit("CUDA_COUNTS", dict(collections.Counter(r["name"] for r in overlap)))
        emit("CUDA", overlap)
        osrt = query("SELECT a.rowid id,a.start,a.end,s.value name,a.returnValue,a.callchainId FROM OSRT_API a JOIN StringIds s ON s.id=a.nameId WHERE a.globalTid=? AND a.start<? AND a.end>? ORDER BY a.start", (tid,b,a))
        emit("OSRT", osrt)
        for r in osrt:
            if min(b,r["end"])-max(a,r["start"]) >= 100000:
                emit("STACK", dict(osrt_id=r["id"], frames=query("SELECT c.stackDepth,s.value symbol,m.value module,c.unresolved FROM OSRT_CALLCHAINS c JOIN StringIds s ON s.id=c.symbol JOIN StringIds m ON m.id=c.module WHERE c.id=? ORDER BY c.stackDepth", (r["callchainId"],))))
        emit("PREVIOUS_CUDA", [r for r in same if r["end"] <= a][-2:])
        emit("NVTX", query("SELECT start,end,coalesce(text,value) name FROM NVTX_EVENTS LEFT JOIN StringIds ON textId=id WHERE globalTid=? AND start<? AND end>?", (tid,b,a)))
