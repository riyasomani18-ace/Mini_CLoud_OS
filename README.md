# MiniCloudOS

**A cloud resource scheduler simulator: replay the same jobs through different schedulers, placement strategies and
autoscaling policies, and see which wins, with an interactive browser dashboard.**

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)

Cloud schedulers decide which job runs where and when. Small choices change a lot: the same workload can leave the average
job waiting 139 ticks in the queue or about 6. MiniCloudOS lets you try the classic policies (and add your own) on repeatable workloads,
measures them honestly, and shows the result as a table, a replay and charts.

## The result that matters

Six whole-machine jobs of 150 to 250 ticks arrive first, then 114 tiny jobs. Four machines of 8 cores and 32 GB.
Same jobs, eight schedulers:

```text
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
```

Strict first-come-first-served leaves tiny jobs stuck behind the giants (slowdown 64.5). Anything that can pause a running
job brings it to about 1 to 1.6. But **it is not "preemption always wins"**: on a steady stream of short jobs, preemption
only adds overhead, and shortest-job-first (which needs to know every duration in advance) is best. The full picture, with
five workloads, time-slice and autoscaling experiments, is in [docs/RESULTS.md](docs/RESULTS.md).

`python -m minicloudos compare --workload convoy` reproduces the table.

## The website (desktop UI)

Double-click **`Start MiniCloudOS.bat`** (Windows) or **`start.command`** (macOS), or run `python app.py`. A website opens in your browser
with the whole project in it: Welcome page, Overview, Replay, Compare, Task Manager, a **Terminal** that runs every command
(including the tests, stress test and mutation check), the **Docs**, and Settings. The command `python -m minicloudos ui` does the same.

Opens a desktop-style app in your browser: draggable windows, a dock, and apps for **Overview**, **Replay**, **Compare**,
**Task Manager** (every job, sortable) and **Settings** (themes, accent, density, and the simulation parameters).
It runs simulations live from a small local server (standard library only, localhost only) and **writes no files**.
Preferences are remembered in the browser.

## The dashboard (static file)

```bash
python -m minicloudos dashboard --workload bursty --out dashboard.html
```

Open the file in any browser (no internet or server needed). It has:

- a table comparing every scheduler, with the best value in each column highlighted (click a row to replay it)
- a **replay with a time slider**: each machine, its cores in use, the jobs on it, and the waiting queue
- a chart of cluster load over time, and a bar chart to compare schedulers on any metric

Two ready-made ones are in [examples/](examples): `dashboard_convoy.html` and `dashboard_bursty_autoscale.html`
(an autoscaled cluster: watch machines boot and get removed).

## Quick start

Python 3.10 or newer, nothing to install.

```bash
python demo.py                                  # guided tour of the headline experiments (about 5 seconds)
python demo.py --step                           # same, one section per ENTER press

python -m minicloudos compare --workload convoy        # one workload through every scheduler
python -m minicloudos matrix                           # every workload x every scheduler
python -m minicloudos quantum --workload bursty        # how long should a time slice be?
python -m minicloudos autoscale --workload bursty      # cost against waiting
python -m minicloudos gantt --workload convoy --jobs 14 --nodes 2 --scheduler rr    # a text picture of a run
```

Optional: `pip install .` adds a `minicloudos` command. In **VS Code**, open the folder, install the Python extension and
use the **Run and Debug** drop-down: the demo tour, the tests and the stress test are ready to run.

## What is in it

| | |
|---|---|
| **Schedulers** | `fcfs`, `fcfs-bf` (backfill), `sjf`, `priority` (with aging), `rr` (round robin), `mlfq` (multi-level feedback queue), `drf` (Dominant Resource Fairness), `drf-p` (DRF with time slices) |
| **Placement** | `first_fit`, `best_fit`, `worst_fit` |
| **Autoscaler** | adds machines when waiting jobs fit nowhere (decided per job, not by total free cores), removes idle ones, with a boot delay and a cost |
| **Workloads** | `batch`, `web`, `bursty`, `convoy`, `noisy` (a noisy neighbour), all repeatable from a seed |
| **Metrics** | response and total wait, slowdown, useful cores, per-tenant fairness (Jain index), worst tenant, cost |

To add a scheduler: write a small class (`order`, optionally `quantum`) and add it to `SCHEDULERS`
([how](docs/ARCHITECTURE.md)). The tests and the stress test pick it up automatically.

## How it is verified

Simulators are easy to get subtly wrong, so most of the effort went into checking this one.

- **76 automated tests**, many with answers worked out by hand on paper.
- A **built-in self-check** after every tick (no machine over-committed, no job lost or in two places) and an
  **independent referee** that re-verifies a finished run from its recorded segments alone.
- A **randomized stress test**: 12,000 fully random scenarios (about 423,000 jobs, random machine sizes, schedulers and
  settings, restore costs, autoscaling) all passed. It found three real autoscaler bugs that hand-written tests had missed.
- **Mutation checks**: the code was deliberately broken in 38 different ways; the tests caught all 38
  (`python mutation_check.py`). Where an early version missed one, a test was added.
- The dashboard's JavaScript is run in **Node.js** against a fake browser, and its counts of waiting jobs, busy cores and
  machines are compared with the simulator's own records at hundreds of moments.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) also lists what the testing found, including a metric that flattered
preemptive schedulers and a livelock when the time slice was shorter than the restore cost.

```bash
python -m unittest discover -v -s tests -t .      # the tests (the dashboard ones need Node.js)
python stress_test.py --cases 2000                # random scenarios
python mutation_check.py                          # can the tests actually fail?
```

## Project layout

| Path | Purpose |
|---|---|
| `minicloudos/` | The simulator: model, schedulers, placement, autoscaler, metrics, workloads, experiments, dashboard |
| `tests/` | Unit tests, the dashboard's browser-logic test, an independent referee helper |
| `stress_test.py`, `mutation_check.py` | Random-scenario stress test; deliberate-breakage check of the tests |
| `demo.py` | Guided tour |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) and [results](docs/RESULTS.md) |
| `examples/` | Two ready-made dashboards |

## Limitations

- This is a **simulator**, not a real cloud. The workloads are synthetic (plausible shapes, not measurements of a real
  cluster), so the results say what these policies do on these kinds of work, not how they would perform in production.
- Two resources only (cores and memory). No network, disk, GPUs, job dependencies, machine failures or machines of
  different speeds. Preemption is checkpoint and restore with a fixed cost.
- Shortest-job-first uses the true duration of every job, which real schedulers do not know: it is a reference point.
- Tables show a single seed each; do not read a few percent as a ranking.
- The dashboard has been tested for correct behaviour in a fake browser, but its layout has not been checked on every browser
  and screen size.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Ideas: job dependencies, machine failures, a real-trace loader, a cgroups demo that
limits an actual process on Linux.

## License

MIT, see [LICENSE](LICENSE).
