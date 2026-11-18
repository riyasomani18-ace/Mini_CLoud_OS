"""
Metrics: how good was a schedule?

  wait         total ticks spent waiting in the queue (turnaround minus time on a machine)
  response     ticks from submission until the job FIRST ran (how soon you see something happen)
  turnaround   ticks from submission until it finished
  slowdown     turnaround / the job's own duration (1.0 = never waited; 10 = took 10x longer than needed)
  utilization  share of the machines' cores/memory that was allocated to running jobs
  useful cpu   share of the cores doing REAL work (allocated cores minus time spent restoring after a preemption)
  fairness     Jain's index over tenants' average slowdowns: 1.0 = every tenant has the same experience.
               (DRF aims at a different goal, equal SHARES of the cluster, so the two can disagree.)
  worst tenant the average slowdown of the unluckiest tenant
"""
from math import ceil


def percentile(values, p):
    """Nearest-rank percentile (p in 0..100)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, ceil(p / 100 * len(ordered)) - 1))
    return float(ordered[k])


def mean(values):
    return sum(values) / len(values) if values else 0.0


def jain_index(values):
    """Jain's fairness index: (sum x)^2 / (n * sum x^2). 1.0 = perfectly equal."""
    values = [v for v in values if v is not None]
    if not values:
        return 1.0
    total_sq = sum(v * v for v in values)
    return (sum(values) ** 2) / (len(values) * total_sq) if total_sq else 1.0


def per_tenant(jobs):
    out = {}
    for tenant in sorted({j.tenant for j in jobs}):
        mine = [j for j in jobs if j.tenant == tenant]
        out[tenant] = {"jobs": len(mine),
                       "mean_wait": mean([j.wait for j in mine]),
                       "mean_response": mean([j.response for j in mine]),
                       "mean_slowdown": mean([j.slowdown for j in mine])}
    return out


def summarize(jobs, counters):
    waits = [j.wait for j in jobs]
    responses = [j.response for j in jobs]
    turns = [j.turnaround for j in jobs]
    slows = [j.slowdown for j in jobs]
    tenants = per_tenant(jobs)
    makespan = max((j.finish for j in jobs), default=0)
    cap_cpu, cap_mem = counters["cap_cpu_ticks"], counters["cap_mem_ticks"]
    return {
        "jobs": len(jobs),
        "makespan": makespan,
        "mean_wait": mean(waits),
        "p95_wait": percentile(waits, 95),
        "max_wait": max(waits, default=0),
        "mean_response": mean(responses),
        "p95_response": percentile(responses, 95),
        "mean_turnaround": mean(turns),
        "p95_turnaround": percentile(turns, 95),
        "mean_slowdown": mean(slows),
        "p95_slowdown": percentile(slows, 95),
        "cpu_utilization": counters["used_cpu_ticks"] / cap_cpu if cap_cpu else 0.0,
        "cpu_useful": counters["work_cpu_ticks"] / cap_cpu if cap_cpu else 0.0,
        "mem_utilization": counters["used_mem_ticks"] / cap_mem if cap_mem else 0.0,
        "worst_tenant_slowdown": max((t["mean_slowdown"] for t in tenants.values()), default=0.0),
        "fairness": jain_index([1 / t["mean_slowdown"] for t in tenants.values() if t["mean_slowdown"]]),
        "preemptions": sum(j.preemptions for j in jobs),
        "node_ticks": counters["node_ticks"],
        "throughput": len(jobs) / makespan if makespan else 0.0,
        "tenants": tenants,
    }
