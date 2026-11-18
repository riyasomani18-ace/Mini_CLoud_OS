"""
The things that exist in the simulated cloud: jobs, nodes and the cluster description.

A JOB asks for some CPU cores and some memory, for a number of ticks of work.
A NODE is one machine with a fixed number of cores and amount of memory.
Time is counted in whole ticks (think of one tick as one second).
"""
from dataclasses import dataclass, field, replace


@dataclass
class Job:
    id: int
    arrival: int                  # tick at which the job is submitted
    duration: int                 # ticks of work needed
    cpu: int                      # cores needed (held for the whole time the job is running)
    mem: int                      # memory needed, in GB
    priority: int = 0             # 0 = most urgent (used by the priority scheduler)
    tenant: str = "default"       # who submitted it (used by the fair-share schedulers)

    # --- filled in by the simulator while it runs ---
    remaining: int = field(default=0, repr=False)
    first_start: int | None = field(default=None, repr=False)
    finish: int | None = field(default=None, repr=False)
    node: int | None = field(default=None, repr=False)        # node id while running
    slice_used: int = field(default=0, repr=False)            # ticks used in the current time slice
    level: int = field(default=0, repr=False)                 # queue level (multi-level feedback queue)
    enqueued_at: int = field(default=0, repr=False)           # when it last entered the waiting queue
    overhead_left: int = field(default=0, repr=False)         # ticks of "restoring state" still to pay
    preemptions: int = field(default=0, repr=False)
    ran_ticks: int = field(default=0, repr=False)             # ticks spent on a node (work + overhead)

    def __post_init__(self):
        if self.duration < 1 or self.cpu < 1 or self.mem < 0 or self.arrival < 0:
            raise ValueError(f"invalid job {self.id}: duration/cpu must be >= 1, mem and arrival >= 0")
        self.remaining = self.duration

    def fresh(self):
        """A pristine copy, so the same workload can be replayed through different schedulers."""
        return replace(self, remaining=self.duration, first_start=None, finish=None, node=None,
                       slice_used=0, level=0, enqueued_at=0, overhead_left=0, preemptions=0, ran_ticks=0)

    @property
    def response(self):
        """Ticks between submission and the FIRST moment the job ran."""
        return None if self.first_start is None else self.first_start - self.arrival

    @property
    def wait(self):
        """Total ticks spent waiting in the queue (turnaround minus the time spent on a machine).
        For a job that is never preempted this equals its response time."""
        return None if self.finish is None else (self.finish - self.arrival) - self.ran_ticks

    @property
    def turnaround(self):
        return None if self.finish is None else self.finish - self.arrival

    @property
    def slowdown(self):
        """Turnaround divided by the ideal time (1.0 = never had to wait)."""
        return None if self.finish is None else (self.finish - self.arrival) / self.duration


@dataclass
class Node:
    id: int
    cpu: int
    mem: int
    state: str = "ready"              # "booting" -> "ready" -> "removed"
    ready_at: int = 0
    idle_since: int = 0
    used_cpu: int = 0
    used_mem: int = 0
    running: dict = field(default_factory=dict)   # job id -> Job

    @property
    def free_cpu(self):
        return self.cpu - self.used_cpu

    @property
    def free_mem(self):
        return self.mem - self.used_mem

    def fits(self, job):
        return self.state == "ready" and job.cpu <= self.free_cpu and job.mem <= self.free_mem

    def allocate(self, job):
        assert self.fits(job), "allocate() called with a job that does not fit"
        self.used_cpu += job.cpu
        self.used_mem += job.mem
        self.running[job.id] = job

    def release(self, job):
        del self.running[job.id]
        self.used_cpu -= job.cpu
        self.used_mem -= job.mem


@dataclass
class ClusterSpec:
    """The machines we start with, and the shape of any machine the autoscaler adds."""
    nodes: list = field(default_factory=lambda: [(8, 32)] * 4)    # [(cores, memory GB), ...]
    new_node_shape: tuple = (8, 32)

    @classmethod
    def uniform(cls, count, cpu=8, mem=32):
        return cls(nodes=[(cpu, mem)] * count, new_node_shape=(cpu, mem))
