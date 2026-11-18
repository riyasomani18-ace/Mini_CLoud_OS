# Architecture

MiniCloudOS is a discrete-event-style simulator: it replays a list of jobs through a scheduler on a cluster, one tick
at a time, and measures how good the result was. This document explains the model, each piece, and how the whole thing
is verified.

## The world

- **Job**: arrives at a tick, needs some **cores** and some **memory (GB)** for a number of **ticks of work**, belongs to a
  **tenant**, has a **priority**. While running it holds its cores and memory for the whole time it is on a machine.
- **Node (machine)**: a fixed number of cores and GB. States: `booting` (not usable yet, but paid for), `ready`, `removed`.
- **Cluster**: the machines you start with, and the shape of any machine the autoscaler adds.
- **Time**: whole ticks. A job placed at tick *t* runs during [*t*, *t*+1).

## One tick (see `simulator.py`)

1. Jobs whose arrival time has come join the waiting queue.
2. Machines that finished booting become ready.
3. Jobs that used up their time slice may be pushed back to the queue (only if somebody waiting could use the room).
4. The autoscaler may add or remove machines.
5. The scheduler's per-tick hook runs (for example the multi-level queue's periodic boost).
6. **Schedule:** the scheduler orders the waiting jobs, and the placement strategy picks a machine for each one that fits.
7. Accounting: allocated cores, useful cores, memory and cost are measured; per-tick series are recorded.
8. Every running job does one tick of work. A job that is restoring its state after a preemption does no work, and its time
   slice does not start counting until the restore is done.
9. Jobs with no work left finish and release their resources.

## Schedulers (`schedulers.py`)

A scheduler answers three questions: `order(pending)` (best candidate first), `quantum(job)` (how long it may run before it
can be pushed back; `None` = until it finishes), and `backfill` (if the best candidate does not fit, may smaller jobs overtake?).

| Name | Idea | Preempts? | Watch out for |
|---|---|---|---|
| `fcfs` | First come, first served; if the head of the queue does not fit, everybody waits | no | head-of-line blocking: tiny jobs stuck behind giants |
| `fcfs-bf` | FCFS, but smaller jobs may fill gaps | no | big jobs can be overtaken repeatedly |
| `sjf` | Shortest job first | no | needs the true duration in advance (a reference, not a realistic scheduler); long jobs starve |
| `priority` | Most urgent first, with aging so low priority cannot starve | no | priorities are only as good as the people setting them |
| `rr` | Round robin with a time slice | yes | tiny slices waste time restoring; huge slices behave like FCFS |
| `mlfq` | Multi-level feedback queue: new jobs get short slices, jobs that use a whole slice sink to longer ones, periodic boost | yes | favours short jobs; many pauses on steady workloads |
| `drf` | Dominant Resource Fairness across tenants: next job comes from the tenant with the smallest dominant share | no | cannot take anything back, so first arrivals keep the cluster |
| `drf-p` | DRF with time slices | yes | ties after a preemption must go to whoever waited longest (see below) |

To add a scheduler, write a small class and add it to `SCHEDULERS`. The simulator never looks inside it.

**A preemption is only done when it is useful:** a job whose slice expired is pushed back only if some waiting job would fit
into the room it frees. Otherwise it simply carries on with a fresh slice. This avoids pointless switches.

## Placement (`placement.py`)

Which machine does a job go to? `first_fit` (first machine with room), `best_fit` (the one left with the least free space),
`worst_fit` (the one left with the most). The scheduler picks *which job*, placement picks *which machine*.

## Autoscaler (`autoscaler.py`)

- **Scale up:** wait jobs are *pretended* onto the free space of running machines and onto machines that are still booting,
  biggest job first; every job that fits nowhere opens one virtual new machine. That number (capped by `max_nodes`) is added.
  Deciding per job matters: ten free cores spread over three machines do not help a job that needs eight.
- **Scale down:** when nothing is waiting and a machine has been idle for `idle_ticks`, it is removed, but only machines of the
  autoscaler's own shape (it can always add one back). A machine of another shape, such as the one big machine you started
  with, is pinned: removing it could leave a big job with nowhere to run, ever.
- Adding a machine takes `boot_time` ticks, and a booting machine costs money.

## Metrics (`metrics.py`)

- **response**: ticks from submission to the first run. **wait**: total ticks in the queue (turnaround minus time on a machine).
  For a never-preempted job these are equal. For preemptive schedulers *wait* is the honest number: *response* only measures how
  fast a job is first touched.
- **slowdown**: turnaround divided by the job's own duration. **useful cpu**: cores doing real work, not restoring.
- **fairness**: Jain index over the tenants' slowdowns (equal *experience*). DRF aims at equal *shares*, which is a different goal.
- **cost**: node-ticks (machines ready or booting, per tick).

## How it is verified

| Layer | What it does |
|---|---|
| Hand-calculated tests | Small scenarios whose exact answers were worked out on paper (for example round robin with a restore cost finishing at 7 and 10) |
| Built-in self-check (`check=True`) | After every tick: no machine over-committed, no job in two places, none lost or duplicated, accounting consistent |
| Independent referee (`referee.py`) | Re-verifies a finished run from the recorded run segments and machine log alone, so it can catch bugs the self-check shares |
| Stress test (`stress_test.py`) | Thousands of fully random scenarios (cluster shapes, workloads, scheduler settings, placement, restore cost, autoscaler), each run with the self-check, the referee, and extra properties, with the seed printed on failure |
| Mutation checks (`mutation_check.py`) | The code is deliberately broken in 38 ways (a wrong comparison, a missing release, a flipped sort, a broken dashboard control) to confirm the tests notice; gaps found this way were closed |
| Dashboard tests | The page's JavaScript is run in Node.js against a fake browser, every control is driven, and the page's own counts of waiting jobs, busy cores and machines are compared with the simulator's per-tick records |

### What the testing found (and what changed)

- **A flattering metric.** *Wait* originally meant "time until first run", which made preemptive schedulers look excellent
  (a response of 0.0) while their jobs actually waited much longer. It is now total queue time, and *response* is reported separately.
- **A fairness scheduler that could starve a newcomer.** After a preemption both tenants briefly hold nothing, and breaking the
  tie by tenant *name* handed the machine straight back. Ties now go to whoever has waited longest.
- **A livelock.** With a time slice no longer than the restore cost, a resumed job spent its whole slice restoring, was pushed
  back, and never advanced. The slice now counts only useful work, and a watchdog stops the run with a clear error if no job
  makes progress for 5,000 ticks.
- **Three autoscaler flaws, all found by the stress test:** scale-up compared total free cores instead of per-job fit (ignoring
  memory and fragmentation); scale-down could remove the only machine big enough for a later job; and validation accepted a
  job that fitted only a machine the autoscaler was no longer allowed to add.
- **A wrong hand calculation of mine** (a cost figure in a test): the engine was right and the arithmetic was wrong. The test
  was corrected after tracing the timeline tick by tick, not by copying the engine's number.

## Design decisions and limits

- **Synthetic workloads** with fixed seeds, so every result is exactly repeatable. They are plausible shapes (heavy-tailed
  durations, Poisson and bursty arrivals), not measurements of a real cluster.
- **Two resources** (cores, memory). No network, disk, GPUs, job dependencies, machine failures or machines of different speeds.
- **Preemption is checkpoint and restore with a fixed cost**, not live migration or partial progress.
- **Pure standard library** and one module per concern, so the code can be read top to bottom.
- The simulator is deterministic: the same input always gives the same result, which the stress test checks.
