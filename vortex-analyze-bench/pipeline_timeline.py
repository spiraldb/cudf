#!/usr/bin/env python3
"""CPU-only, read-only whole-Q5 accounting of a scheduler SQLite.

Print JSON to stdout; --gaps includes EVERY maximal gap and its split classes;
--nvtx includes every bounded NVTX range. No historical framework is imported.
All timestamps/durations are integer nanoseconds; GPU means recorded work only.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "cpp/build/vortex-bench-history-20261008/runs/ndsh-scheduler-gaps-20261008/.local/q5-scheduler-01.sqlite"
MASK = (1 << 24) - 1


def union(intervals):
    result = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def duration(intervals):
    return sum(b - a for a, b in union(intervals))


def intersect(left, right):
    return union((max(a, c), min(b, d)) for a, b in left for c, d in right)


def complement(lo, hi, intervals):
    result, cursor = [], lo
    for a, b in intersect(intervals, [(lo, hi)]):
        if cursor < a:
            result.append([cursor, a])
        cursor = b
    if cursor < hi:
        result.append([cursor, hi])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=TRACE, help="Existing Nsight SQLite export")
    parser.add_argument("--gaps", action="store_true")
    parser.add_argument("--nvtx", action="store_true")
    args = parser.parse_args()
    trace = args.trace.resolve()
    digest = hashlib.sha256(trace.read_bytes()).hexdigest()
    if trace == TRACE.resolve():
        assert digest == "9b67bcd5364e7d3817f46bb26da8075e18214f19f16db0454eb06316bd08a017"
    con = sqlite3.connect(trace.as_uri() + "?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")

    def rows(sql):
        return [dict(r) for r in con.execute(sql)]

    strings = dict(con.execute("SELECT id,value FROM StringIds"))
    apis = rows("SELECT rowid id,* FROM CUPTI_ACTIVITY_KIND_RUNTIME ORDER BY start")
    api_index = collections.defaultdict(list)
    for a in apis:
        a["name"] = strings[a["nameId"]]
        a["tid"] = a["globalTid"] & MASK
        api_index[a["globalTid"] & ~MASK, a["correlationId"]].append(a)
    assert all(len(v) == 1 for v in api_index.values())
    copies = dict(con.execute("SELECT id,label FROM ENUM_CUDA_MEMCPY_OPER"))
    work = []
    for kind in ("KERNEL", "MEMCPY", "MEMSET"):
        for r in rows("SELECT rowid id,* FROM CUPTI_ACTIVITY_KIND_" + kind):
            r["kind"] = kind
            r["name"] = (strings[r["shortName"]] if kind == "KERNEL" else
                         copies[r["copyKind"]] if kind == "MEMCPY" else "memset")
            match = api_index[r["globalPid"], r["correlationId"]]
            assert len(match) == 1 and match[0]["returnValue"] == 0
            r["api"] = match[0]
            assert r["api"]["start"] <= r["start"] < r["end"]
            work.append(r)
    work.sort(key=lambda r: (r["start"], r["kind"], r["id"]))
    assert len({(r["globalPid"], r["deviceId"], r["contextId"]) for r in work}) == 1
    busy = union((r["start"], r["end"]) for r in work)
    nvtx = rows("SELECT n.rowid id,start,end,globalTid,coalesce(text,value) name "
                "FROM NVTX_EVENTS n LEFT JOIN StringIds ON textId=StringIds.id "
                "WHERE end IS NOT NULL ORDER BY start")
    for n in nvtx:
        n["tid"] = n["globalTid"] & MASK
    reads = [n for n in nvtx if n["name"] == "vortex.read"]
    lo, hi = min(r["start"] for r in reads), max(r["end"] for r in reads)
    profiler = next(a for a in apis if a["name"] == "cuProfilerStart")
    main_tid = profiler["tid"]
    timer_events = [a for a in apis if a["tid"] == main_tid and
                    a["name"] == "cudaEventRecord_v3020" and a["start"] > profiler["end"]]
    qlo, qhi = timer_events[0]["start"], timer_events[-1]["end"]
    assert qlo < lo < hi < qhi
    query_start = min(a["start"] for a in apis if a["tid"] == main_tid and a["start"] > hi)
    filters = [n for n in nvtx if n["name"] == "apply_filter"]
    order_end = max(n["end"] for n in nvtx if n["name"] == "apply_orderby")

    def state(t, candidates):
        # Returned API takes precedence, then in-progress, then not-entered.
        if not candidates:
            return "no_future"
        if any(r["api"]["end"] <= t for r in candidates):
            return "returned"
        if any(r["api"]["start"] <= t < r["api"]["end"] for r in candidates):
            return "in_api"
        return "not_entered"

    def compact(r):
        return {k: r[k] for k in ("kind", "id", "name", "start", "end", "streamId", "correlationId")} | {
            "bytes": r.get("bytes"), "api_start": r["api"]["start"],
            "api_end": r["api"]["end"], "api_name": r["api"]["name"], "caller_tid": r["api"]["tid"]}

    # Global future horizon: even a read-window tail considers later query work.
    # Gaps split at all future submission API endpoints, never classified wholesale.
    details = []
    for a, b in complement(qlo, qhi, busy):
        future = [r for r in work if r["start"] >= b]
        nxt = [r for r in future if r["start"] == future[0]["start"]] if future else []
        cuts = sorted({a, b} | {r["api"][k] for r in future for k in ("start", "end")
                               if a < r["api"][k] < b})
        segments = []
        for x, y in zip(cuts, cuts[1:]):
            pair = [state(x, nxt), state(x, future)]
            if segments and segments[-1]["classes"] == pair:
                segments[-1]["end"] = y
            else:
                segments.append(dict(start=x, end=y, classes=pair))
        assert sum(s["end"] - s["start"] for s in segments) == b - a
        details.append(dict(start=a, end=b, next=[compact(r) for r in nxt], segments=segments))

    def account(name, a, b):
        gaps = complement(a, b, busy)
        next_ns, any_ns, cross = (collections.Counter() for _ in range(3))
        for g in details:
            for s in g["segments"]:
                ns = max(0, min(b, s["end"]) - max(a, s["start"]))
                if ns:
                    n, f = s["classes"]
                    next_ns[n] += ns
                    any_ns[f] += ns
                    cross[n + "/" + f] += ns
        idle = duration(gaps)
        active = duration(intersect(busy, [(a, b)]))
        assert sum(next_ns.values()) == sum(any_ns.values()) == idle
        assert active + idle == b - a
        # Independent raw-endpoint count sweep checks interval-union accounting.
        endpoints = collections.Counter({a: 0, b: 0})
        for r in work:
            x, y = max(a, r["start"]), min(b, r["end"])
            if x < y:
                endpoints[x] += 1
                endpoints[y] -= 1
        count, previous, checked_idle = 0, a, 0
        for t, delta in sorted(endpoints.items()):
            if count == 0:
                checked_idle += t - previous
            count += delta
            assert count >= 0
            previous = t
        assert count == 0 and checked_idle == idle
        return dict(name=name, start=a, end=b, duration_ns=b-a, busy_ns=active,
                    idle_ns=idle, gaps=len(gaps), next_ns=dict(next_ns),
                    any_ns=dict(any_ns), cross_ns=dict(cross))

    windows = [account("Q5_timer_API_bracket", qlo, qhi), account("read_envelope", lo, hi),
               account("before_first_read", qlo, lo), account("after_reads_before_query_API", hi, query_start),
               account("query_setup", query_start, filters[0]["start"]),
               account("relational_compute", filters[0]["start"], order_end),
               account("cleanup_and_final_sync", order_end, qhi),
               account("post_read_total", hi, qhi)]
    table_rows = []
    phase_intervals = collections.defaultdict(list)
    for r in reads:
        children = [n for n in nvtx if n["globalTid"] == r["globalTid"] and
                    r["start"] <= n["start"] < n["end"] <= r["end"] and
                    n["name"].startswith("vortex.") and n["id"] != r["id"]]
        phases = []
        for n in children:
            phase_intervals[n["name"]].append((n["start"], n["end"]))
            phases.append(account(n["name"], n["start"], n["end"]) | {"nvtx_id": n["id"]})
        owned_materialize = [w for w in work if w["api"]["globalTid"] == r["globalTid"] and
                            any(n["name"] == "vortex.materialize" and
                                n["start"] <= w["api"]["start"] < n["end"] for n in children)]
        table_rows.append(account("read_tid_" + str(r["tid"]), r["start"], r["end"]) |
                          {"tid": r["tid"], "nvtx_id": r["id"], "phases": phases,
                           "outside_named_phases_ns": r["end"] - r["start"] - duration((n["start"], n["end"]) for n in children),
                           "materialize_submissions": [compact(w) for w in owned_materialize]})
    phase_unions = {name: dict(calls=len(v), sum_ns=sum(b-a for a,b in v), union_ns=duration(v),
                              busy_overlap_ns=duration(intersect(v, busy)),
                              idle_overlap_ns=duration(v)-duration(intersect(v, busy)))
                    for name, v in phase_intervals.items()}
    cutpoints = sorted({lo, hi} | {r[k] for r in reads for k in ("start", "end")})
    concurrency = []
    for a, b in zip(cutpoints, cutpoints[1:]):
        tids = [r["tid"] for r in reads if r["start"] <= a < r["end"]]
        concurrency.append(account("read_concurrency", a, b) | {"active_read_tids": tids})
    operators = [account(n["name"], n["start"], n["end"]) | {"nvtx_id": n["id"]}
                 for n in nvtx if n["name"] in ("apply_filter", "apply_inner_join", "binary_operation", "apply_groupby", "apply_orderby")]
    joins = rows("SELECT a.rowid id,start,end,globalTid,s.value name,callchainId "
                 "FROM OSRT_API a JOIN StringIds s ON a.nameId=s.id WHERE s.value='pthread_join'")
    joins = [j | {"duration_ns": j["end"]-j["start"]} for j in joins if j["globalTid"] & MASK == main_tid]
    streams = []
    for sid in sorted({w["streamId"] for w in work}):
        selected = [w for w in work if w["streamId"] == sid]
        streams.append(dict(stream=sid, count=len(selected), start=min(w["start"] for w in selected),
                            end=max(w["end"] for w in selected),
                            union_ns=duration((w["start"],w["end"]) for w in selected),
                            kinds=dict(collections.Counter(w["kind"] for w in selected))))
    result = dict(sha256=digest, work_count=len(work), work_kinds=dict(collections.Counter(w["kind"] for w in work)),
                  bounded_nvtx_count=len(nvtx), timer_start_api=timer_events[0], timer_stop_api=timer_events[-1],
                  windows=windows, reads=table_rows, phase_unions=phase_unions,
                  read_concurrency=concurrency, operators=operators, main_joins=joins, streams=streams,
                  diagnostics=rows("SELECT text FROM DIAGNOSTIC_EVENT"))
    if args.gaps:
        result["all_gaps"] = details
    if args.nvtx:
        result["all_bounded_nvtx"] = nvtx
    print(json.dumps(result, indent=2))
    con.close()


if __name__ == "__main__":
    main()
