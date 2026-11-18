# Results

Everything here was produced by the code in this repository, on synthetic workloads with fixed random seeds,
on a cluster of 4 machines with 8 cores and 32 GB each unless stated otherwise. Reproduce any table with the command
shown above it. The numbers are exact and repeatable; what they mean is limited by the fact that the workloads are
*synthetic* (see "What these results do not show" at the end).

**How to read the columns.** *response* = ticks until a job first runs. *wait* = total ticks a job spends waiting in the
queue (this is the honest number for preemptive schedulers). *slowdown* = turnaround divided by the job's own duration
(1.0 = never waited). *worst tnt* = the average slowdown of the unluckiest tenant. *useful* = share of the cluster's cores
doing real work (not restoring after a preemption). *fair* = Jain index over tenants' slowdowns (1.0 = every tenant has the
same experience). *preempt* = how often a running job was pushed back to the queue.

## 1. Which scheduler wins depends on the workload

`python -m minicloudos matrix`

### The convoy: tiny jobs stuck behind giants

Six whole-machine jobs of 150 to 250 ticks arrive first, then 114 tiny jobs of 1 to 5 ticks.

```text
=== convoy (120 jobs) ===
scheduler  response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
fcfs          139.3   139.3      206    64.54      71.6      347    87%  0.36       0
fcfs-bf        86.1    86.1      152    39.65      43.1      388    77%  0.38       0
sjf            48.9    48.9      122    22.27      24.2      393    76%  0.42       0
priority       52.7    52.7      114    25.77      29.9      387    78%  0.41       0
rr              1.0     6.8        8     1.55       1.6      359    84%  1.00     250
mlfq            1.1     7.0       11     1.52       1.7      374    80%  0.99     129
drf            51.8    51.8      114    25.04      28.2      393    76%  0.41       0
drf-p           0.4     6.2        6     1.14       1.6      368    82%  0.97     116
  winners: lowest mean slowdown: drf-p; lowest p95 wait: drf-p; fastest first response: drf-p; fairest (Jain index): rr; most useful cpu work: fcfs
```

- **Strict first-come-first-served is the worst by a wide margin:** the average job has a slowdown of 64.5, because tiny
  jobs queue behind giants. The average job waits 139 ticks before it even starts (114 of the 120 jobs are tiny).
- **Preemption is the cure.** Round robin, the multi-level queue and preemptive DRF bring slowdown down to 1.1 to 1.6.
- **Backfilling alone helps, but not enough** (39.7): it lets small jobs use gaps, but the giants still hold every machine.

### A busy but steady stream of short jobs

```text
=== web (600 jobs) ===
scheduler  response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
fcfs            8.4     8.4       16     3.53       3.6      149    93%  1.00       0
fcfs-bf         7.2     7.2       13     3.19       3.2      147    95%  1.00       0
sjf             3.6     3.6       20     1.40       1.4      147    95%  1.00       0
priority        6.6     6.6       31     2.84       3.0      147    95%  1.00       0
rr              5.1     9.0       19     3.08       3.1      146    95%  1.00     485
mlfq            0.0    14.9       60     2.82       2.9      145    96%  1.00     712
drf             7.1     7.1       14     3.15       3.3      148    94%  1.00       0
drf-p           6.8     8.2       17     3.23       3.4      147    95%  1.00     123
  winners: lowest mean slowdown: sjf; lowest p95 wait: fcfs-bf; fastest first response: mlfq; fairest (Jain index): sjf; most useful cpu work: mlfq
```

- Here preemption does **not** help. Shortest-job-first is best (slowdown 1.40), the preemptive schedulers sit around 3, and
  the schedulers without preemption are close behind.
- **A trap in the numbers:** the multi-level queue shows a *response* of 0.0 (every job starts at once) but its jobs *wait*
  14.9 ticks in total, against 7.2 for first-come-first-served with backfill. It gets every job started quickly and then
  keeps pausing them. "Fastest first response" is not the same as "fastest to finish".

### Bursty arrivals with a heavy tail of long jobs

```text
=== bursty (400 jobs) ===
scheduler  response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
fcfs          222.0   222.0      530    59.76      71.3     1101    58%  0.99       0
fcfs-bf        77.5    77.5      287    22.60      29.0      802    79%  0.96       0
sjf            35.4    35.4      320     8.35      10.7      915    70%  0.97       0
priority       63.6    63.6      412    22.28      24.6      862    74%  0.99       0
rr             10.3    40.0      151     5.23       5.7      762    84%  1.00    1455
mlfq            6.6    34.6      149     4.33       5.5      831    77%  0.93     853
drf            65.8    65.8      425    20.66      30.0      909    70%  0.78       0
drf-p          26.1    37.9      153     9.03      15.8      864    74%  0.84     584
  winners: lowest mean slowdown: mlfq; lowest p95 wait: mlfq; fastest first response: mlfq; fairest (Jain index): rr; most useful cpu work: rr
```

- The multi-level queue is best (slowdown 4.33, against 59.8 for strict FCFS and 8.35 for shortest-job-first).
- Shortest-job-first is the *reference*, not a contender: it needs to know every job's duration in advance, which real
  clusters rarely do. Among schedulers that need no such knowledge, the multi-level queue and round robin win here.

### Everything submitted at once (a nightly batch)

```text
=== batch (150 jobs) ===
scheduler  response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
fcfs          106.9   106.9      193    28.25      34.5      423    49%  0.95       0
fcfs-bf        66.0    66.0      149    16.88      20.2      394    53%  0.96       0
sjf            25.7    25.7       89     3.00       3.1      367    57%  1.00       0
priority       57.6    57.6      160    16.10      21.6      383    54%  0.96       0
rr             17.1    48.8      115     7.42       8.1      377    55%  0.99     356
mlfq           10.2    47.5      118     7.52       7.8      374    56%  1.00     257
drf            56.0    56.0      189    14.49      17.0      340    61%  0.93       0
drf-p          30.5    38.7      136     8.50      10.0      329    63%  0.95     142
  winners: lowest mean slowdown: sjf; lowest p95 wait: sjf; fastest first response: mlfq; fairest (Jain index): sjf; most useful cpu work: drf-p
```

### One tenant floods the cluster

One tenant (45% of the jobs) submits big, long jobs; two small tenants submit modest jobs steadily.

```text
=== noisy (240 jobs) ===
scheduler  response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
fcfs          365.2   365.2      553    51.29      82.3      642    83%  0.56       0
fcfs-bf       187.9   187.9      454    21.07      28.9      585    91%  0.85       0
sjf            95.1    95.1      449     4.69       9.1      607    87%  0.74       0
priority      121.1   121.1      487     8.27      13.4      597    89%  0.82       0
rr             49.3   239.8      513    18.12      18.7      574    92%  1.00     734
mlfq            7.4   205.8      516    11.81      18.4      578    92%  0.84     809
drf           115.0   115.0      467     6.57      13.3      601    88%  0.72       0
drf-p          44.1   183.2      528     8.79      18.2      588    90%  0.70     301
  winners: lowest mean slowdown: sjf; lowest p95 wait: sjf; fastest first response: mlfq; fairest (Jain index): rr; most useful cpu work: rr
```

- **Strict FCFS is brutal to the small tenants** (the unluckiest tenant averages a slowdown of 82.3). Anything smarter helps a lot.
- **Plain DRF (fair sharing, no preemption) cuts the unluckiest tenant's slowdown from 82.3 to 13.3.** It beat its own
  preemptive variant (18.2) here, which is a reminder that adding preemption is not automatically better.
- **"Fair" has more than one meaning.** DRF aims at equal *shares of the cluster*; the Jain column measures equal *experience*.
  On the bursty workload DRF scores only 0.78 on the experience measure, because one team simply submits much more work, so
  an equal share still means a longer wait for that team. Both definitions are legitimate; they just answer different questions.

## 2. Time slices: responsiveness against wasted effort

Every time a preempted job resumes it must restore its state, which costs 1 tick here. A slice of 1 tick means every resume
wastes half the slice.

`python -m minicloudos quantum --workload web` and `--workload bursty`

```text
Round Robin on the web workload; every resume after a preemption costs 1 tick(s)

time slice response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
q=1            12.6    77.6      153    15.88      16.0      258    54%  1.00    2680
q=2            12.2    38.2       83     8.89       9.0      195    71%  1.00    1198
q=4            11.0    20.3       45     5.70       5.8      165    84%  1.00     485
q=8             9.1    10.9       24     3.97       4.0      150    93%  1.00     124
q=16            7.2     7.2       13     3.19       3.2      147    95%  1.00       0
q=32            7.2     7.2       13     3.19       3.2      147    95%  1.00       0
```

```text
Round Robin on the bursty workload; every resume after a preemption costs 1 tick(s)

time slice response    wait p95 wait slowdown worst tnt makespan useful  fair preempt
-------------------------------------------------------------------------------------
q=1            11.1   154.9      523    13.63      14.1     1222    52%  1.00    6496
q=2            12.6    90.4      343     8.96       9.3      981    65%  1.00    3407
q=4            15.6    63.6      234     7.48       8.4      851    75%  0.99    1538
q=8            26.2    50.2      169     9.00      10.0      841    76%  0.98     552
q=16           43.8    52.7      412    16.95      17.2      860    74%  1.00     239
q=32           44.0    52.9      143    13.68      16.5      797    80%  0.98     112
```

- **Steady short jobs:** the bigger the slice the better; from 16 up nothing is ever preempted and the result equals
  first-come-first-served. With a slice of 1, only 54% of the cluster does real work.
- **Bursty heavy-tailed jobs:** there is a sweet spot, and it depends on what you measure. Average slowdown is best with a
  slice of 4 (7.48), total waiting is best at 8 (50.2 ticks), and time-to-first-run is best with the smallest slice (11.1 at a
  slice of 1). *There is no single best slice.* Both extremes are poor: a slice of 1 wastes its time restoring (slowdown 13.63), while slices of 16 or more let long jobs hold machines (16.95 and 13.68).

## 3. Autoscaling: cost against waiting

The cost is machine-ticks paid for (a machine that is still booting costs money too). A new machine needs 5 ticks to boot.

`python -m minicloudos autoscale --workload bursty` and `--workload web`

```text
MLFQ on the bursty workload: cost (node-ticks = machine-ticks paid for) versus waiting

setup             wait p95 wait makespan useful node-ticks
----------------------------------------------------------
fixed 2 nodes    162.2      596     1383    92%       2766
fixed 8 nodes      2.8       18      579    55%       4632
autoscale 1-8      3.5       17      579    71%       3634
```

```text
MLFQ on the web workload: cost (node-ticks = machine-ticks paid for) versus waiting

setup             wait p95 wait makespan useful node-ticks
----------------------------------------------------------
fixed 2 nodes    108.7      225      281    99%        562
fixed 8 nodes      0.0        0      133    52%       1064
autoscale 1-8      0.5        5      133    58%       1001
```

- On the bursty workload, autoscaling gets nearly the same waiting time as 8 permanent machines (3.5 against 2.8 ticks) for
  **22% less cost** (3,634 against 4,632).
- Two permanent machines are cheapest but a disaster for users (a wait of 162 ticks).
- On the steady web workload the saving is only about 6%, because the load never drops far.

## 4. Placement strategy barely matters here

`python -m minicloudos placement --workload bursty` and `--workload web`

```text
First-come-first-served with backfill on an autoscaled cluster, bursty workload

placement         wait p95 wait makespan useful node-ticks
----------------------------------------------------------
first_fit          8.0       36      584    65%       3938
best_fit           8.0       41      579    66%       3923
worst_fit          7.3       41      580    66%       3895
```

```text
First-come-first-served with backfill on an autoscaled cluster, web workload

placement         wait p95 wait makespan useful node-ticks
----------------------------------------------------------
first_fit          0.5        4      133    58%        996
best_fit           0.5        4      133    58%        996
worst_fit          0.5        4      133    57%       1004
```

First-fit, best-fit and worst-fit end up within about 1% of each other on cost and on waiting. The textbook argument that best-fit
"packs more tightly" does not show up in these workloads. Which scheduler you use and how many machines you run matter far more.

## What these results do not show

- The workloads are **synthetic** (heavy-tailed durations, Poisson and bursty arrivals). They are plausible, not measured from
  a real cluster, so the numbers say what these schedulers do on *these* shapes of work, not how they would do in production.
- One seed per table. The stress test runs thousands of random scenarios for correctness, but the tables above are single
  runs; a different seed gives different numbers, and a close call (for example two schedulers a few percent apart) should not be
  read as a ranking.
- Jobs hold their cores and memory for their whole run and are preempted by a checkpoint-and-restore with a fixed cost.
  There is no network, disk, job dependency, machine failure or heterogeneous speed.
- Shortest-job-first uses information (the true duration) that real schedulers do not have.
