"""
The referee: re-checks a finished simulation using ONLY what it recorded (job results, run segments, machine log).
It never looks at the simulator's own accounting, so it can catch bugs the simulator's built-in self-check shares.

    from minicloudos.referee import verify_result
    verify_result(result)               # raises RefereeError describing the first rule that was broken
"""
from collections import defaultdict


class RefereeError(AssertionError):
    pass


def verify_result(result, switch_cost=0):
    def need(condition, message):
        if not condition:
            raise RefereeError(message)

    jobs = {j.id: j for j in result.jobs}
    segs_by_job = defaultdict(list)
    for job_id, node_id, start, end in result.segments:
        need(start < end, "empty or backwards segment")
        segs_by_job[job_id].append((start, end, node_id))

    # when was each machine usable, and how big is it?
    shape, usable, since = {}, defaultdict(list), {}
    for tick, node_id, cpu, mem, state in result.node_log:
        shape[node_id] = (cpu, mem)
        if state == "ready":
            since[node_id] = tick
        elif state == "removed":
            usable[node_id].append((since.pop(node_id), tick))
    for node_id, tick in since.items():
        usable[node_id].append((tick, result.ticks))

    for job in result.jobs:
        need(job.finish is not None, f"job {job.id} never finished")
        need(job.first_start >= job.arrival, f"job {job.id} started before it arrived")
        need(job.finish >= job.arrival + job.duration, f"job {job.id} finished faster than its own duration")
        need(job.ran_ticks == job.duration + job.preemptions * switch_cost,
             f"job {job.id}: time on machines is not duration + restore costs")
        segs = sorted(segs_by_job[job.id])
        need(sum(e - s for s, e, _ in segs) == job.ran_ticks, f"job {job.id}: its run segments do not add up")
        need(len(segs) == job.preemptions + 1, f"job {job.id}: wrong number of run segments")
        need(segs[0][0] == job.first_start and segs[-1][1] == job.finish, f"job {job.id}: segments disagree with start/finish")
        for (s1, e1, _), (s2, _, _) in zip(segs, segs[1:]):
            need(e1 <= s2, f"job {job.id} runs twice at once")
        for s, e, node_id in segs:
            need(any(a <= s and e <= b for a, b in usable[node_id]),
                 f"job {job.id} ran on node {node_id} while it was not usable")

    # no machine is ever over-committed (tick by tick, from the segments alone)
    usage = defaultdict(lambda: [0, 0])
    for job_id, node_id, start, end in result.segments:
        for t in range(start, end):
            usage[(node_id, t)][0] += jobs[job_id].cpu
            usage[(node_id, t)][1] += jobs[job_id].mem
    for (node_id, t), (cpu, mem) in usage.items():
        need(cpu <= shape[node_id][0], f"node {node_id} over-committed on cores at tick {t}")
        need(mem <= shape[node_id][1], f"node {node_id} over-committed on memory at tick {t}")

    # allocated core-ticks recomputed from the segments equal what the metrics report
    used = sum(jobs[j].cpu * (e - s) for j, _, s, e in result.segments)
    capacity = sum(result.series["cap_cpu"]) if result.series.get("cap_cpu") else None
    if capacity:
        need(used == round(result.metrics["cpu_utilization"] * capacity), "reported cpu utilization does not match the segments")
