# Changelog

## Unreleased
- `python -m minicloudos ui`: live desktop-style UI (server.py, os_ui.html). demo.py no longer writes dashboard files.

## 1.0.0

First complete release.

- Simulator with jobs, machines, eight schedulers (FCFS, FCFS with backfill, SJF, priority with aging, round robin,
  multi-level feedback queue, DRF, preemptive DRF), three placement strategies and a threshold autoscaler.
- Metrics that separate response time from total waiting time, report useful (not just allocated) cores, per-tenant
  fairness and cost.
- Experiments (matrix, time-slice sweep, autoscaling, placement), a text Gantt chart and a self-contained HTML dashboard
  with a replay slider.
- Verification: hand-calculated tests, a built-in self-check, an independent referee, a randomized stress test, mutation
  checks, and a Node.js test of the dashboard's JavaScript against the simulator.
