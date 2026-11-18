"""
Stress test: thousands of random scenarios, each checked two independent ways.

Every case is a completely random world: 1-5 machines of mixed sizes, a random stream of jobs, a random
scheduler with random settings, a random placement strategy, a random switching cost, and (sometimes) an
autoscaler with random thresholds. Each case runs with the simulator's built-in self-check on, then the
independent referee re-verifies the result from the recorded run segments alone, and a few extra properties
are checked:

  * every job finishes, no machine is ever over-committed, no job is lost or runs twice (self-check + referee)
  * the makespan is at least the longest job's earliest-possible finish
  * nothing finishes faster than its own duration (slowdown >= 1)
  * non-preemptive schedulers never preempt
  * running the same case twice gives exactly the same answer (determinism)

If a case fails, the seed is printed so you can reproduce it:  python stress_test.py --seed N --cases 1

Run:  python stress_test.py                    500 cases, about 20 seconds
      python stress_test.py --cases 5000       a longer run
"""
import argparse
import random
import sys
import time

from minicloudos import ClusterSpec, Job, SCHEDULERS, Simulator, ThresholdAutoscaler
from minicloudos.placement import PLACEMENTS
from minicloudos.referee import verify_result
from minicloudos.schedulers import DRFPreemptive, MLFQ, Priority, RoundRobin

SHAPES = [(2, 8), (4, 16), (8, 32), (16, 64)]
NON_PREEMPTIVE = {"fcfs", "fcfs-bf", "sjf", "priority", "drf"}


def random_scheduler(rng):
    name = rng.choice(list(SCHEDULERS))
    if name == "rr":
        return name, RoundRobin(q=rng.randint(1, 8))
    if name == "mlfq":
        quanta = sorted(rng.sample(range(1, 40), rng.randint(1, 4)))
        return name, MLFQ(quanta=quanta, boost_every=rng.choice([0, 20, 200]))
    if name == "priority":
        return name, Priority(aging=rng.choice([None, 1, 10, 100]))
    if name == "drf-p":
        return name, DRFPreemptive(q=rng.randint(1, 12))
    return name, SCHEDULERS[name]()


def random_case(seed):
    rng = random.Random(seed)
    nodes = [rng.choice(SHAPES) for _ in range(rng.randint(1, 5))]
    scaler = None
    new_shape = rng.choice(SHAPES)
    if rng.random() < 0.4:
        low = rng.randint(1, len(nodes))
        scaler = ThresholdAutoscaler(min_nodes=low, max_nodes=rng.randint(max(low, len(nodes)), 8),
                                     boot_time=rng.randint(0, 6), check_every=rng.randint(1, 5),
                                     idle_ticks=rng.randint(1, 40))
    cluster = ClusterSpec(nodes=nodes, new_node_shape=new_shape)

    # every job must fit on at least one machine shape that exists (or can be added)
    pool = list(nodes)
    if scaler and scaler.max_nodes > len(nodes):             # the autoscaler can only add a machine if it has room
        pool.append(new_shape)
    biggest_cpu, biggest_mem = max(pool)
    arrival_style = rng.choice(["at_once", "poisson", "waves"])
    duration_style = rng.choice(["short", "heavy", "uniform"])
    tenants = [f"t{i}" for i in range(rng.randint(1, 4))]
    jobs, clock = [], 0.0
    for i in range(rng.randint(1, 70)):
        if arrival_style == "poisson":
            clock += rng.expovariate(rng.choice([0.2, 1.0, 3.0]))
        elif arrival_style == "waves":
            clock += rng.choice([0, 0, 0, rng.randint(20, 60)])
        duration = {"short": rng.randint(1, 6), "uniform": rng.randint(1, 30),
                    "heavy": rng.choice([1, 2, 3, 4, 40, 120])}[duration_style]
        cpu = rng.randint(1, biggest_cpu)
        mem = rng.randint(0, biggest_mem)
        jobs.append(Job(i, int(clock) if arrival_style != "at_once" else 0, duration, cpu, mem,
                        priority=rng.randint(0, 3), tenant=rng.choice(tenants)))
    name, scheduler = random_scheduler(rng)
    return dict(jobs=jobs, cluster=cluster, scaler=scaler, name=name, scheduler=scheduler,
                placement=rng.choice(list(PLACEMENTS)), switch_cost=rng.choice([0, 0, 1, 2]))


def run_case(case):
    sim = Simulator(case["jobs"], case["scheduler"], case["placement"], cluster=case["cluster"],
                    autoscaler=case["scaler"], switch_cost=case["switch_cost"], check=True)
    return sim.run()


def check_case(seed):
    """Returns (result, problem). problem is None when everything held."""
    case = random_case(seed)
    try:
        result = run_case(case)
        verify_result(result, case["switch_cost"])
        if result.metrics["makespan"] < max(j.arrival + j.duration for j in case["jobs"]):
            return result, "makespan is shorter than the longest job's earliest possible finish"
        if any(j.slowdown < 1 - 1e-9 for j in result.jobs):
            return result, "a job finished faster than its own duration"
        if case["name"] in NON_PREEMPTIVE and result.metrics["preemptions"]:
            return result, f"{case['name']} is non-preemptive but preempted {result.metrics['preemptions']} times"
        if seed % 10 == 0:                                   # determinism: the same case again, from scratch
            again = run_case(random_case(seed))
            if again.segments != result.segments or again.metrics != result.metrics:
                return result, "running the same case twice gave different answers"
        return result, None
    except Exception as e:                                    # any crash or broken rule is a finding
        return None, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1, help="first seed (each case uses the next one)")
    args = ap.parse_args()

    started = time.time()
    jobs_total = preempts = scaled = 0
    problems = []
    by_scheduler = {}
    for seed in range(args.seed, args.seed + args.cases):
        result, problem = check_case(seed)
        if problem:
            problems.append((seed, problem))
            if len(problems) >= 5:
                break
            continue
        jobs_total += result.metrics["jobs"]
        preempts += result.metrics["preemptions"]
        scaled += max(result.series["ready"]) > len(random_case(seed)["cluster"].nodes)
        by_scheduler[result.scheduler] = by_scheduler.get(result.scheduler, 0) + 1

    print(f"{args.cases - len(problems)} of {args.cases} random scenarios passed in {time.time() - started:.1f} s")
    print(f"jobs simulated: {jobs_total}   preemptions seen: {preempts}   scenarios where the cluster grew: {scaled}")
    print("cases per scheduler: " + ", ".join(f"{k} {v}" for k, v in sorted(by_scheduler.items())))
    if problems:
        print(f"\nPROBLEMS FOUND ({len(problems)}):")
        for seed, why in problems:
            print(f"  seed {seed}: {why}      reproduce: python stress_test.py --seed {seed} --cases 1")
        return 1
    print("RESULT: no problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
