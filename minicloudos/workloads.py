"""
Workloads: realistic-looking streams of jobs, made from a seed so every run is repeatable.

Each generator returns a list of Job objects. Different shapes of work favour different schedulers,
which is the whole point of comparing them.
"""
import random

from .model import Job


def _duration(rng):
    """Heavy tailed: most jobs are short, a few are very long (like real clusters)."""
    r = rng.random()
    if r < 0.75:
        return rng.randint(1, 8)
    if r < 0.95:
        return rng.randint(15, 50)
    return rng.randint(100, 300)


def _resources(rng, max_cpu=8):
    cpu = rng.choices([1, 2, 4, 8], [40, 30, 20, 10])[0]
    cpu = min(cpu, max_cpu)
    mem = min(32, cpu * rng.choice([1, 2, 4]))
    return cpu, mem


def batch(n=150, seed=1):
    """Everything is submitted at once (a nightly batch run). Mixed sizes, three tenants, random priorities."""
    rng = random.Random(seed)
    jobs = []
    for i in range(n):
        cpu, mem = _resources(rng)
        jobs.append(Job(i, 0, _duration(rng), cpu, mem, priority=rng.randint(0, 3),
                        tenant=rng.choice(["team-a", "team-b", "team-c"])))
    return jobs


def web(n=600, seed=1, rate=4.3):
    """A steady stream of small, short jobs arriving at random (about `rate` per tick): a busy but stable cluster."""
    rng = random.Random(seed)
    jobs, clock = [], 0.0
    for i in range(n):
        clock += rng.expovariate(rate)
        cpu = rng.choice([1, 1, 2])
        jobs.append(Job(i, int(clock), rng.randint(1, 10), cpu, cpu * rng.choice([1, 2, 4]),
                        priority=rng.randint(0, 2), tenant=rng.choice(["shop", "blog"])))
    return jobs


def bursty(n=400, seed=1):
    """Quiet periods followed by sudden spikes of arrivals, with a heavy-tailed mix of jobs."""
    rng = random.Random(seed)
    jobs, clock = [], 0.0
    for i in range(n):
        in_burst = int(clock // 50) % 2 == 1                 # alternate 50 quiet ticks, 50 burst ticks
        clock += rng.expovariate(2.5 if in_burst else 0.15)
        cpu, mem = _resources(rng)
        jobs.append(Job(i, int(clock), _duration(rng), cpu, mem, priority=rng.randint(0, 3),
                        tenant=rng.choice(["team-a", "team-b", "team-c"])))
    return jobs


def convoy(n=120, seed=1):
    """A few enormous whole-machine jobs arrive first, then many tiny jobs.
    This is the classic trap: tiny jobs get stuck behind giants unless the scheduler can preempt."""
    rng = random.Random(seed)
    jobs = []
    for i in range(6):
        jobs.append(Job(i, i, rng.randint(150, 250), 8, 32, priority=2, tenant="research"))
    clock = 2.0
    for i in range(6, n):
        clock += rng.uniform(0.5, 3.0)
        cpu = rng.choice([1, 1, 2])
        jobs.append(Job(i, int(clock), rng.randint(1, 5), cpu, cpu * 2, priority=rng.randint(0, 1),
                        tenant=rng.choice(["alice", "bob"])))
    return jobs


def noisy_neighbor(n=240, seed=1):
    """One tenant floods the cluster with big jobs; two small tenants submit modest jobs steadily.
    Fairness-aware schedulers should protect the small tenants."""
    rng = random.Random(seed)
    jobs, jid = [], 0
    greedy = int(n * 0.45)
    for _ in range(greedy):
        jobs.append(Job(jid, rng.randint(0, 20), rng.randint(10, 40), rng.choice([4, 8]),
                        rng.choice([8, 16, 32]), priority=rng.randint(0, 2), tenant="greedy"))
        jid += 1
    small = n - greedy
    for k in range(small):
        cpu = rng.choice([1, 2])
        jobs.append(Job(jid, int(k * 300 / small), rng.randint(3, 10), cpu, cpu * 2,
                        priority=rng.randint(0, 2), tenant=rng.choice(["alice", "bob"])))
        jid += 1
    jobs.sort(key=lambda j: (j.arrival, j.id))
    for new_id, job in enumerate(jobs):
        job.id = new_id
    return jobs


WORKLOADS = {"batch": batch, "web": web, "bursty": bursty, "convoy": convoy, "noisy": noisy_neighbor}


def make_workload(kind, n=None, seed=1):
    if kind not in WORKLOADS:
        raise ValueError(f"unknown workload '{kind}'. Choose from: {', '.join(WORKLOADS)}")
    return WORKLOADS[kind](n, seed) if n else WORKLOADS[kind](seed=seed)
