"""
Experiments: run the same workload through several schedulers (or settings) and compare the results.
Every function returns plain data; the format_* helpers turn it into tables.
"""
from .autoscaler import ThresholdAutoscaler
from .model import ClusterSpec
from .schedulers import RoundRobin, SCHEDULERS
from .simulator import Simulator
from .workloads import WORKLOADS, make_workload

DEFAULT_CLUSTER = ClusterSpec.uniform(4, 8, 32)       # 4 machines, 8 cores and 32 GB each

COLUMNS = [("mean_response", "response", "{:8.1f}"), ("mean_wait", "wait", "{:7.1f}"),
           ("p95_wait", "p95 wait", "{:8.0f}"), ("mean_slowdown", "slowdown", "{:8.2f}"),
           ("worst_tenant_slowdown", "worst tnt", "{:9.1f}"), ("makespan", "makespan", "{:8d}"),
           ("cpu_useful", "useful", "{:6.0%}"), ("fairness", "fair", "{:5.2f}"), ("preemptions", "preempt", "{:7d}")]


def compare(kind, n=None, seed=1, cluster=None, schedulers=None, placement="best_fit", switch_cost=0, check=False):
    """Run one workload through several schedulers. Returns [(scheduler name, metrics dict), ...]."""
    jobs = make_workload(kind, n=n, seed=seed)
    rows = []
    for name in (schedulers or SCHEDULERS):
        sim = Simulator(jobs, name, placement, cluster=cluster or DEFAULT_CLUSTER,
                        switch_cost=switch_cost, check=check, record=False)
        rows.append((name, sim.run().metrics))
    return rows


def format_table(rows, first_header="scheduler"):
    head = f"{first_header:<10s}" + "".join(f" {h:>{len(f.format(0))}s}" for _, h, f in COLUMNS)
    lines = [head, "-" * len(head)]
    for name, m in rows:
        lines.append(f"{name:<10s}" + "".join(" " + f.format(m[key]) for key, _, f in COLUMNS))
    return "\n".join(lines)


def winners(rows):
    """Best scheduler for the things people care about."""
    best = lambda key, low=True: (min if low else max)(rows, key=lambda r: r[1][key])[0]
    return {"lowest mean slowdown": best("mean_slowdown"), "lowest p95 wait": best("p95_wait"),
            "fastest first response": best("mean_response"),
            "fairest (Jain index)": best("fairness", low=False),
            "most useful cpu work": best("cpu_useful", low=False)}


def matrix(seed=1, cluster=None, workloads=None, check=False):
    """Every workload x every scheduler. Returns {workload: rows}."""
    return {kind: compare(kind, seed=seed, cluster=cluster, check=check) for kind in (workloads or WORKLOADS)}


def quantum_sweep(kind="web", quanta=(1, 2, 4, 8, 16, 32), switch_cost=1, seed=1, cluster=None):
    """Round Robin with different time slices, when every switch costs time. Small slices are responsive but
    waste time switching; big slices behave like first-come-first-served."""
    jobs = make_workload(kind, seed=seed)
    rows = []
    for q in quanta:
        r = Simulator(jobs, RoundRobin(q=q), "best_fit", cluster=cluster or DEFAULT_CLUSTER,
                      switch_cost=switch_cost, record=False).run()
        rows.append((f"q={q}", r.metrics))
    return rows


def autoscaling_comparison(kind="bursty", seed=1, switch_cost=0):
    """A small fixed cluster, a large fixed cluster, and an autoscaled one: cost versus waiting time."""
    jobs = make_workload(kind, seed=seed)
    setups = {
        "fixed 2 nodes": (ClusterSpec.uniform(2), None),
        "fixed 8 nodes": (ClusterSpec.uniform(8), None),
        "autoscale 1-8": (ClusterSpec.uniform(1), ThresholdAutoscaler(min_nodes=1, max_nodes=8, boot_time=5,
                                                                      check_every=5, idle_ticks=30)),
    }
    rows = []
    for label, (cluster, scaler) in setups.items():
        r = Simulator(jobs, "mlfq", "best_fit", cluster=cluster, autoscaler=scaler,
                      switch_cost=switch_cost, record=False).run()
        rows.append((label, r.metrics))
    return rows


def placement_comparison(kind="bursty", seed=1):
    """First-fit, best-fit and worst-fit on an autoscaled cluster: tighter packing lets idle machines be released."""
    jobs = make_workload(kind, seed=seed)
    rows = []
    for placement in ("first_fit", "best_fit", "worst_fit"):
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=8, boot_time=5, check_every=5, idle_ticks=30)
        r = Simulator(jobs, "fcfs-bf", placement, cluster=ClusterSpec.uniform(1), autoscaler=scaler, record=False).run()
        rows.append((placement, r.metrics))
    return rows


COST_COLUMNS = [("mean_wait", "wait", "{:7.1f}"), ("p95_wait", "p95 wait", "{:8.0f}"),
                ("makespan", "makespan", "{:8d}"), ("cpu_useful", "useful", "{:6.0%}"),
                ("node_ticks", "node-ticks", "{:10d}")]


def format_cost_table(rows, first_header="setup"):
    head = f"{first_header:<14s}" + "".join(f" {h:>{len(f.format(0))}s}" for _, h, f in COST_COLUMNS)
    lines = [head, "-" * len(head)]
    for name, m in rows:
        lines.append(f"{name:<14s}" + "".join(" " + f.format(m[key]) for key, _, f in COST_COLUMNS))
    return "\n".join(lines)


def gantt(result, max_jobs=24, width=100):
    """A text picture of who ran when. One row per job. '.' = waiting, a letter = running on that machine
    (A = machine 0, B = machine 1, ...), blank = not yet submitted or already finished.
    When one column covers several ticks it shows where the job ran during that stretch, so short jobs stay visible."""
    jobs = sorted(result.jobs, key=lambda j: (j.arrival, j.id))[:max_jobs]
    horizon = max(j.finish for j in jobs)
    step = max(1, -(-horizon // width))                      # ticks per column
    by_job = {}
    for job_id, node_id, start, end in result.segments:
        by_job.setdefault(job_id, []).append((start, end, node_id))
    lines = [f"1 column = {step} tick(s)   '.' waiting   letter = running on machine A, B, C...   (first {len(jobs)} jobs)"]
    for job in jobs:
        row = []
        for col in range(0, horizon, step):
            lo, hi = col, col + step                         # this column covers ticks lo .. hi-1
            if hi <= job.arrival or lo >= job.finish:
                row.append(" ")
                continue
            node = next((n for s, e, n in by_job.get(job.id, ()) if s < hi and e > lo), None)
            row.append("." if node is None else chr(ord("A") + node % 26))
        lines.append(f"job {job.id:<3d} {job.tenant[:7]:<7s} |{''.join(row)}|")
    return "\n".join(lines)
