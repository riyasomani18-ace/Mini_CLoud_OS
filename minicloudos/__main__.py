"""
Command line:   python -m minicloudos <command> [options]

  run        one workload, one scheduler: print the metrics
  compare    one workload through every scheduler: a table
  matrix     every workload x every scheduler, with the winners
  quantum    Round Robin time-slice sweep (responsiveness versus switching cost)
  autoscale  small fixed cluster vs large fixed cluster vs autoscaled
  placement  first-fit vs best-fit vs worst-fit on an autoscaled cluster
  gantt      a text picture of a small run
  dashboard  a self-contained HTML page: comparison table, replay with a time slider, charts
  ui         the desktop-style app in your browser (live, writes no files)
"""
import argparse
import sys

from . import experiments as ex
from .model import ClusterSpec
from .schedulers import SCHEDULERS
from .simulator import Simulator
from .workloads import WORKLOADS, make_workload


def _parser():
    p = argparse.ArgumentParser(prog="minicloudos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(s, scheduler=False):
        s.add_argument("--workload", choices=list(WORKLOADS), default="bursty")
        s.add_argument("--jobs", type=int, help="number of jobs (default depends on the workload)")
        s.add_argument("--seed", type=int, default=1)
        s.add_argument("--nodes", type=int, default=4, help="machines in the cluster (default 4, each 8 cores / 32 GB)")
        s.add_argument("--switch-cost", type=int, default=0, help="ticks lost each time a preempted job resumes")
        if scheduler:
            s.add_argument("--scheduler", choices=list(SCHEDULERS), default="mlfq")
            s.add_argument("--placement", choices=["first_fit", "best_fit", "worst_fit"], default="best_fit")
    common(sub.add_parser("run", help="one workload, one scheduler"), scheduler=True)
    common(sub.add_parser("compare", help="one workload through every scheduler"))
    m = sub.add_parser("matrix", help="every workload x every scheduler")
    m.add_argument("--seed", type=int, default=1)
    q = sub.add_parser("quantum", help="Round Robin time-slice sweep")
    q.add_argument("--workload", choices=list(WORKLOADS), default="web")
    q.add_argument("--switch-cost", type=int, default=1)
    q.add_argument("--seed", type=int, default=1)
    a = sub.add_parser("autoscale", help="fixed vs autoscaled cluster")
    a.add_argument("--workload", choices=list(WORKLOADS), default="bursty")
    a.add_argument("--seed", type=int, default=1)
    pl = sub.add_parser("placement", help="first-fit vs best-fit vs worst-fit")
    pl.add_argument("--workload", choices=list(WORKLOADS), default="bursty")
    pl.add_argument("--seed", type=int, default=1)
    common(sub.add_parser("gantt", help="a text picture of a small run"), scheduler=True)
    d = common(sub.add_parser("dashboard", help="write a self-contained HTML dashboard"))
    d = sub.choices["dashboard"]
    d.add_argument("--out", default="dashboard.html", help="file to write (default dashboard.html)")
    d.add_argument("--autoscale", action="store_true", help="use an autoscaled cluster (1 to 8 machines)")
    u = sub.add_parser("ui", help="open the desktop-style UI in your browser (no files written)")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="do not open a browser tab automatically")
    return p


def _run(args):
    jobs = make_workload(args.workload, n=args.jobs, seed=args.seed)
    cluster = ClusterSpec.uniform(args.nodes)
    result = Simulator(jobs, args.scheduler, args.placement, cluster=cluster, switch_cost=args.switch_cost,
                       check=True, record=False).run()
    m = result.metrics
    print(f"{args.workload} workload, {m['jobs']} jobs, {args.nodes} machines, scheduler={args.scheduler}, placement={args.placement}")
    print(ex.format_table([(args.scheduler, m)]))
    print("\nper tenant:")
    for tenant, t in m["tenants"].items():
        print(f"  {tenant:<10s} {t['jobs']:4d} jobs   mean wait {t['mean_wait']:7.1f}   mean slowdown {t['mean_slowdown']:6.2f}")
    return 0


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        argv = ["ui"]                       # no arguments (for example a double-click): open the desktop UI
    args = _parser().parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "compare":
        rows = ex.compare(args.workload, n=args.jobs, seed=args.seed, cluster=ClusterSpec.uniform(args.nodes),
                          switch_cost=args.switch_cost, check=True)
        print(f"{args.workload} workload, {rows[0][1]['jobs']} jobs, {args.nodes} machines\n")
        print(ex.format_table(rows))
        print("\nwinners:")
        for what, who in ex.winners(rows).items():
            print(f"  {what:<26s} {who}")
        return 0
    if args.cmd == "matrix":
        for kind, rows in ex.matrix(seed=args.seed, check=True).items():
            print(f"\n=== {kind} ({rows[0][1]['jobs']} jobs) ===")
            print(ex.format_table(rows))
            print("  winners: " + "; ".join(f"{k}: {v}" for k, v in ex.winners(rows).items()))
        return 0
    if args.cmd == "quantum":
        print(f"Round Robin on the {args.workload} workload; every resume after a preemption costs {args.switch_cost} tick(s)\n")
        print(ex.format_table(ex.quantum_sweep(args.workload, switch_cost=args.switch_cost, seed=args.seed), "time slice"))
        return 0
    if args.cmd == "autoscale":
        print(f"MLFQ on the {args.workload} workload: cost (node-ticks = machine-ticks paid for) versus waiting\n")
        print(ex.format_cost_table(ex.autoscaling_comparison(args.workload, seed=args.seed)))
        return 0
    if args.cmd == "placement":
        print(f"First-come-first-served with backfill on an autoscaled cluster, {args.workload} workload\n")
        print(ex.format_cost_table(ex.placement_comparison(args.workload, seed=args.seed), "placement"))
        return 0
    if args.cmd == "gantt":
        jobs = make_workload(args.workload, n=args.jobs or 16, seed=args.seed)
        result = Simulator(jobs, args.scheduler, args.placement, cluster=ClusterSpec.uniform(args.nodes),
                           switch_cost=args.switch_cost, check=True).run()
        print(ex.gantt(result))
        return 0
    if args.cmd == "ui":
        from .server import serve
        return serve(args.port, open_browser=not args.no_browser)
    if args.cmd == "dashboard":
        from .dashboard import write_dashboard
        size = write_dashboard(args.out, workload=args.workload, seed=args.seed, nodes=args.nodes, jobs=args.jobs,
                               switch_cost=args.switch_cost, autoscale=args.autoscale)
        print(f"wrote {args.out} ({size // 1024} KB). Open it in any web browser; it needs no internet.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
