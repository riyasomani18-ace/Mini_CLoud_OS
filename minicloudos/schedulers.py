"""
Schedulers: WHICH waiting job gets a machine next?

Every scheduler answers three questions:
  order(pending, sim)  the waiting jobs, best candidate first
  quantum(job)         how many ticks a job may run before it can be pushed back to the queue
                       (None = until it finishes)
  backfill             if the best candidate does not fit anywhere, may smaller jobs behind it go first?

The simulator calls these; it never looks inside. That makes it easy to add a new policy: write a
small class and add it to SCHEDULERS at the bottom.
"""


class Scheduler:
    name = "base"
    dynamic = False        # True = the order depends on what is running, so recompute after every placement
    backfill = True

    def __init__(self, backfill=None):
        if backfill is not None:
            self.backfill = backfill

    def order(self, pending, sim):
        raise NotImplementedError

    def quantum(self, job):
        return None

    # hooks (default: do nothing)
    def on_quantum_expired(self, job, sim):
        pass

    def on_tick(self, now, sim):
        pass


def _fifo_key(job):
    return (job.enqueued_at, job.id)


class FCFS(Scheduler):
    """First come, first served. Strict version: if the head of the queue does not fit, EVERYONE waits
    (head-of-line blocking). With backfill=True smaller jobs may jump ahead into the gaps."""
    name = "fcfs"
    backfill = False

    def order(self, pending, sim):
        return sorted(pending, key=lambda j: (j.arrival, j.id))


class FCFSBackfill(FCFS):
    name = "fcfs-bf"
    backfill = True


class SJF(Scheduler):
    """Shortest job first. It needs to know every job's duration in advance, which real clusters rarely do,
    so treat it as the 'best possible' reference. Long jobs can wait a very long time."""
    name = "sjf"

    def order(self, pending, sim):
        return sorted(pending, key=lambda j: (j.remaining, j.enqueued_at, j.id))


class Priority(Scheduler):
    """Most urgent first (priority 0 beats 1 beats 2...). Aging: every `aging` ticks of waiting a job
    moves one level up, so low-priority jobs cannot starve forever. aging=None turns that off."""
    name = "priority"

    def __init__(self, aging=100, backfill=None):
        super().__init__(backfill)
        self.aging = aging

    def effective(self, job, now):
        if self.aging:
            return job.priority - (now - job.enqueued_at) // self.aging
        return job.priority

    def order(self, pending, sim):
        return sorted(pending, key=lambda j: (self.effective(j, sim.now), j.enqueued_at, j.id))


class RoundRobin(Scheduler):
    """Everyone gets a turn: a job runs for at most `q` ticks, then goes to the back of the queue."""
    name = "rr"

    def __init__(self, q=4, backfill=None):
        super().__init__(backfill)
        self.q = q

    def order(self, pending, sim):
        return sorted(pending, key=_fifo_key)

    def quantum(self, job):
        return self.q


class MLFQ(Scheduler):
    """Multi-level feedback queue. New jobs start at the top level with a short time slice. A job that
    uses up its whole slice is demoted to a lower level with a longer slice. So short jobs finish fast,
    and long jobs sink. Every `boost_every` ticks everything goes back to the top, so nobody starves."""
    name = "mlfq"

    def __init__(self, quanta=(2, 4, 8, 16), boost_every=200, backfill=None):
        super().__init__(backfill)
        self.quanta = tuple(quanta)
        self.boost_every = boost_every

    def order(self, pending, sim):
        return sorted(pending, key=lambda j: (j.level, j.enqueued_at, j.id))

    def quantum(self, job):
        return self.quanta[min(job.level, len(self.quanta) - 1)]

    def on_quantum_expired(self, job, sim):
        job.level = min(job.level + 1, len(self.quanta) - 1)

    def on_tick(self, now, sim):
        if self.boost_every and now > 0 and now % self.boost_every == 0:
            for job in sim.active_jobs():
                job.level = 0
                job.slice_used = 0


class DRF(Scheduler):
    """Fair share between tenants, using Dominant Resource Fairness (the idea behind Mesos and YARN).
    A tenant's DOMINANT SHARE is the larger of 'fraction of all cores it holds' and 'fraction of all memory
    it holds'. The next job always comes from the tenant with the smallest dominant share, so one tenant
    cannot crowd out the others. (Without time slices it cannot take anything back, see DRFPreemptive.)"""
    name = "drf"
    dynamic = True

    def dominant_share(self, tenant, sim):
        cpu, mem = sim.tenant_usage(tenant)
        total_cpu, total_mem = sim.total_capacity()
        return max(cpu / total_cpu if total_cpu else 0, mem / total_mem if total_mem else 0)

    def order(self, pending, sim):
        by_tenant = {}
        for job in pending:
            by_tenant.setdefault(job.tenant, []).append(job)
        # equal shares are common (for example right after a preemption), so ties go to whoever has waited longest
        tenants = sorted(by_tenant, key=lambda t: (self.dominant_share(t, sim),
                                                   min(j.enqueued_at for j in by_tenant[t]), t))
        ordered = []
        for tenant in tenants:
            ordered.extend(sorted(by_tenant[tenant], key=_fifo_key))
        return ordered


class DRFPreemptive(DRF):
    """DRF with time slices. Plain DRF cannot take anything back, so whoever arrives first keeps the cluster
    until their jobs finish. With slices, shares are rebalanced every `q` ticks."""
    name = "drf-p"

    def __init__(self, q=8, backfill=None):
        super().__init__(backfill)
        self.q = q

    def quantum(self, job):
        return self.q


# name -> a function that builds a fresh scheduler
SCHEDULERS = {
    "fcfs": FCFS,
    "fcfs-bf": FCFSBackfill,
    "sjf": SJF,
    "priority": Priority,
    "rr": RoundRobin,
    "mlfq": MLFQ,
    "drf": DRF,
    "drf-p": DRFPreemptive,
}


def make_scheduler(name, **options):
    if name not in SCHEDULERS:
        raise ValueError(f"unknown scheduler '{name}'. Choose from: {', '.join(SCHEDULERS)}")
    return SCHEDULERS[name](**options)
