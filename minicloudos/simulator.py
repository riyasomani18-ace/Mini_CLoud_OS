"""
The simulator: replays a list of jobs through a scheduler on a cluster, one tick at a time.

What happens in every tick, in this order:
  1. jobs whose arrival time has come join the waiting queue
  2. machines that finished booting become ready
  3. jobs that used up their time slice may be pushed back to the queue (only if someone waiting could use the room)
  4. the autoscaler may add or remove machines
  5. the scheduler hook runs (for example the multi-level queue's periodic boost)
  6. SCHEDULE: waiting jobs are placed on machines (scheduler picks the job, placement picks the machine)
  7. accounting: utilization and cost are measured
  8. every running job does one tick of work (a job that is restoring its state after a preemption does no
     real work and its time slice does not start counting until it has finished restoring)
  9. jobs with no work left finish and release their resources

With check=True, every tick also verifies the rules of the world (no machine ever over-committed,
no job in two places, no work lost). The tests and the stress test run with check=True.
"""
from collections import deque
from dataclasses import dataclass

from .metrics import summarize
from .model import ClusterSpec, Node
from .placement import PLACEMENTS
from .schedulers import make_scheduler


class InvariantError(AssertionError):
    """The simulator broke one of its own rules. Always a bug."""


@dataclass
class Result:
    jobs: list                # the finished jobs (copies; your originals are untouched)
    metrics: dict
    segments: list            # (job_id, node_id, start, end): who ran where, when
    series: dict              # per-tick measurements (queue length, utilization, nodes)
    node_log: list            # (tick, node_id, cpu, mem, state) every time a node changes state
    scheduler: str
    placement: str
    ticks: int


class Simulator:
    def __init__(self, jobs, scheduler="fcfs", placement="best_fit", cluster=None, autoscaler=None,
                 switch_cost=0, check=False, record=True, max_ticks=500_000):
        self.cluster = cluster or ClusterSpec()
        self.scheduler = make_scheduler(scheduler) if isinstance(scheduler, str) else scheduler
        self.placement_name = placement if isinstance(placement, str) else getattr(placement, "__name__", "custom")
        self.place = PLACEMENTS[placement] if isinstance(placement, str) else placement
        self.autoscaler = autoscaler
        self.switch_cost = switch_cost
        self.check = check
        self.record = record
        self.max_ticks = max_ticks

        self.jobs = [j.fresh() for j in jobs]
        if len({j.id for j in self.jobs}) != len(self.jobs):
            raise ValueError("job ids must be unique")
        shapes = list(self.cluster.nodes)
        if autoscaler and autoscaler.max_nodes > len(self.cluster.nodes):      # only if it can actually add machines
            shapes.append(self.cluster.new_node_shape)
        for j in self.jobs:
            if not any(j.cpu <= c and j.mem <= m for c, m in shapes):
                raise ValueError(f"job {j.id} needs {j.cpu} cores / {j.mem} GB, which fits on no machine")

        self.now = 0
        self.pending = []
        self.nodes = []
        self.segments = []
        self.node_log = []
        self.series = {k: [] for k in ("pending", "running", "used_cpu", "cap_cpu", "ready", "booting")}
        self._open = {}                    # job id -> (node id, start tick) of the segment in progress
        self._counters = {"used_cpu_ticks": 0, "cap_cpu_ticks": 0, "used_mem_ticks": 0,
                          "cap_mem_ticks": 0, "node_ticks": 0, "work_cpu_ticks": 0}
        for cpu, mem in self.cluster.nodes:
            self._add_node(cpu, mem, boot_time=0)

    # ------------------------------------------------------------------ views used by schedulers
    def nodes_in_state(self, state):
        return [n for n in self.nodes if n.state == state]

    def running_jobs(self):
        return [j for n in self.nodes_in_state("ready") for j in n.running.values()]

    def active_jobs(self):
        return list(self.pending) + self.running_jobs()

    def tenant_usage(self, tenant):
        jobs = [j for j in self.running_jobs() if j.tenant == tenant]
        return sum(j.cpu for j in jobs), sum(j.mem for j in jobs)

    def total_capacity(self):
        ready = self.nodes_in_state("ready")
        return sum(n.cpu for n in ready), sum(n.mem for n in ready)

    # ------------------------------------------------------------------ nodes and jobs
    def _add_node(self, cpu, mem, boot_time):
        node = Node(id=len(self.nodes), cpu=cpu, mem=mem, state="ready" if boot_time == 0 else "booting",
                    ready_at=self.now + boot_time, idle_since=self.now)
        self.nodes.append(node)
        self.node_log.append((self.now, node.id, cpu, mem, node.state))
        return node

    def _start(self, job, node):
        node.allocate(job)
        job.node = node.id
        job.slice_used = 0
        if job.first_start is None:
            job.first_start = self.now
        self._open[job.id] = (node.id, self.now)

    def _stop(self, job, node):
        node_id, start = self._open.pop(job.id)
        self.segments.append((job.id, node_id, start, self.now))
        node.release(job)
        job.node = None
        if not node.running:
            node.idle_since = self.now

    def _preempt(self, job, node):
        self._stop(job, node)
        job.preemptions += 1
        job.enqueued_at = self.now
        job.slice_used = 0
        job.overhead_left = self.switch_cost      # it must "restore its state" when it runs again
        self.pending.append(job)

    # ------------------------------------------------------------------ the steps of a tick
    def _handle_slices(self):
        for node in self.nodes_in_state("ready"):
            for job in list(node.running.values()):
                q = self.scheduler.quantum(job)
                if q is None or job.slice_used < q:
                    continue
                self.scheduler.on_quantum_expired(job, self)
                # Only push the job back if somebody waiting could use the room it would free.
                useful = any(p.cpu <= node.free_cpu + job.cpu and p.mem <= node.free_mem + job.mem
                             for p in self.pending)
                if useful:
                    self._preempt(job, node)
                else:
                    job.slice_used = 0                # nobody needs the room: carry on with a fresh slice

    def _autoscale(self):
        for action in self.autoscaler.decide(self.now, self):
            if action[0] == "add":
                cpu, mem = self.cluster.new_node_shape
                for _ in range(action[1]):
                    self._add_node(cpu, mem, self.autoscaler.boot_time)
            elif action[0] == "remove":
                node = self.nodes[action[1]]
                if node.state == "ready" and not node.running:
                    node.state = "removed"
                    self.node_log.append((self.now, node.id, node.cpu, node.mem, "removed"))

    def _schedule(self):
        if not self.pending:
            return
        ready = self.nodes_in_state("ready")
        sched = self.scheduler
        while True:
            placed = None
            for job in sched.order(self.pending, self):
                node = self.place(job, ready)
                if node:
                    self._start(job, node)
                    placed = job
                    if sched.dynamic:
                        break                       # the order may have changed: ask again
                elif not sched.backfill:
                    break                           # strict queue: nobody may overtake the blocked head
            if placed is None:
                return
            self.pending = [j for j in self.pending if j.node is None]
            if not sched.dynamic:
                return

    def _account(self):
        ready = self.nodes_in_state("ready")
        c = self._counters
        cap_cpu = sum(n.cpu for n in ready)
        used_cpu = sum(n.used_cpu for n in ready)
        c["cap_cpu_ticks"] += cap_cpu
        c["used_cpu_ticks"] += used_cpu
        c["cap_mem_ticks"] += sum(n.mem for n in ready)
        c["used_mem_ticks"] += sum(n.used_mem for n in ready)
        booting = len(self.nodes_in_state("booting"))
        c["node_ticks"] += len(ready) + booting
        if self.record:
            s = self.series
            s["pending"].append(len(self.pending))
            s["running"].append(sum(len(n.running) for n in ready))
            s["used_cpu"].append(used_cpu)
            s["cap_cpu"].append(cap_cpu)
            s["ready"].append(len(ready))
            s["booting"].append(booting)

    def _run_one_tick(self):
        """Every running job uses one tick. Returns how many of them made real progress."""
        progressed = 0
        for node in self.nodes_in_state("ready"):
            for job in node.running.values():
                job.ran_ticks += 1
                if job.overhead_left > 0:
                    job.overhead_left -= 1            # paying the cost of restoring state: no progress,
                    continue                          # and it does not use up the time slice
                job.remaining -= 1
                job.slice_used += 1
                progressed += 1
                self._counters["work_cpu_ticks"] += job.cpu
        return progressed

    def _complete(self):
        finished = 0
        for node in self.nodes_in_state("ready"):
            for job in list(node.running.values()):
                if job.remaining == 0:
                    job.finish = self.now
                    self._stop(job, node)
                    finished += 1
        return finished

    # ------------------------------------------------------------------ the main loop
    def run(self):
        arrivals = deque(sorted(self.jobs, key=lambda j: (j.arrival, j.id)))
        total, done, stalled, no_progress = len(self.jobs), 0, 0, 0
        while done < total:
            if self.now > self.max_ticks:
                raise RuntimeError(f"simulation did not finish within {self.max_ticks} ticks")
            while arrivals and arrivals[0].arrival <= self.now:                     # 1
                job = arrivals.popleft()
                job.enqueued_at = self.now
                self.pending.append(job)
            for node in self.nodes_in_state("booting"):                              # 2
                if node.ready_at <= self.now:
                    node.state = "ready"
                    node.idle_since = self.now
                    self.node_log.append((self.now, node.id, node.cpu, node.mem, "ready"))
            self._handle_slices()                                                    # 3
            if self.autoscaler:                                                      # 4
                self._autoscale()
            self.scheduler.on_tick(self.now, self)                                   # 5
            self._schedule()                                                         # 6
            if not self.running_jobs() and self.pending and not arrivals and not self.nodes_in_state("booting"):
                stalled += 1
                if stalled > 1000:
                    raise RuntimeError(f"deadlock: job {self.pending[0].id} can never be placed")
            else:
                stalled = 0
            self._account()                                                          # 7
            progressed = self._run_one_tick()                                        # 8
            if progressed or not (self.pending or self.running_jobs()):
                no_progress = 0
            else:
                no_progress += 1
                if no_progress > 5000:
                    raise RuntimeError("no job has made progress for 5000 ticks: the scheduler is thrashing "
                                       "(for example a time slice of 0)")
            self.now += 1
            done += self._complete()                                                 # 9
            if self.check:
                self._check_invariants(arrivals, done)
        counters = dict(self._counters)
        metrics = summarize(self.jobs, counters)
        return Result(jobs=self.jobs, metrics=metrics, segments=self.segments, series=self.series,
                      node_log=self.node_log, scheduler=self.scheduler.name, placement=self.placement_name,
                      ticks=self.now)

    # ------------------------------------------------------------------ self-check
    def _check_invariants(self, arrivals, done):
        def fail(msg):
            raise InvariantError(f"tick {self.now}: {msg}")

        running_ids = set()
        for node in self.nodes:
            if node.state != "ready" and node.running:
                fail(f"node {node.id} is {node.state} but has jobs")
            if sum(j.cpu for j in node.running.values()) != node.used_cpu:
                fail(f"node {node.id} cpu accounting is wrong")
            if sum(j.mem for j in node.running.values()) != node.used_mem:
                fail(f"node {node.id} memory accounting is wrong")
            if node.used_cpu > node.cpu or node.used_mem > node.mem:
                fail(f"node {node.id} is over-committed ({node.used_cpu}/{node.cpu} cores, {node.used_mem}/{node.mem} GB)")
            for job in node.running.values():
                if job.id in running_ids:
                    fail(f"job {job.id} runs on two nodes")
                running_ids.add(job.id)
                if job.node != node.id:
                    fail(f"job {job.id} thinks it is on node {job.node}, but sits on {node.id}")
        pending_ids = {j.id for j in self.pending}
        if len(pending_ids) != len(self.pending):
            fail("a job is in the waiting queue twice")
        if pending_ids & running_ids:
            fail("a job is both waiting and running")
        for job in self.pending:
            if job.node is not None or job.finish is not None:
                fail(f"waiting job {job.id} has a node or a finish time")
        for job in self.jobs:
            if not 0 <= job.remaining <= job.duration:
                fail(f"job {job.id} has {job.remaining} ticks left of {job.duration}")
            if job.finish is not None and (job.remaining != 0 or job.id in running_ids or job.id in pending_ids):
                fail(f"finished job {job.id} is still active")
        if len(pending_ids) + len(running_ids) + len(arrivals) + done != len(self.jobs):
            fail("a job has been lost or duplicated")
