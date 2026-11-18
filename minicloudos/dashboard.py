"""
The dashboard: one self-contained HTML file (no internet needed) that shows a whole experiment.

  * a table comparing every scheduler, with the best value in each column highlighted
  * a replay with a time slider: machines, the cores each is using, the jobs on them, and the waiting queue
  * a chart of cluster load over time, and a bar chart to compare schedulers on any metric

Build one with:   python -m minicloudos dashboard --workload bursty --out dashboard.html
"""
import json
from math import ceil
from pathlib import Path

from .autoscaler import ThresholdAutoscaler
from .model import ClusterSpec
from .schedulers import SCHEDULERS
from .simulator import Simulator
from .workloads import make_workload

HERE = Path(__file__).parent

COLUMNS = [
    {"key": "mean_response", "label": "response", "fmt": "f1", "better": "low",
     "help": "Average ticks from submission until a job first runs"},
    {"key": "mean_wait", "label": "wait", "fmt": "f1", "better": "low",
     "help": "Average total ticks a job spends waiting in the queue"},
    {"key": "p95_wait", "label": "p95 wait", "fmt": "f1", "better": "low",
     "help": "The wait that 95% of jobs stay under"},
    {"key": "mean_slowdown", "label": "slowdown", "fmt": "f2", "better": "low",
     "help": "Turnaround divided by the job's own duration (1.0 = never waited)"},
    {"key": "worst_tenant_slowdown", "label": "worst tenant", "fmt": "f1", "better": "low",
     "help": "Average slowdown of the unluckiest tenant"},
    {"key": "makespan", "label": "makespan", "fmt": "int", "better": "low",
     "help": "Ticks until the last job finished"},
    {"key": "cpu_useful", "label": "useful cpu", "fmt": "pct", "better": "high",
     "help": "Share of the cluster's cores doing real work (not restoring after a preemption)"},
    {"key": "fairness", "label": "fairness", "fmt": "f2", "better": "high",
     "help": "Jain index over tenants' slowdowns: 1.0 = every tenant has the same experience"},
    {"key": "preemptions", "label": "preemptions", "fmt": "int", "better": None,
     "help": "How many times a running job was pushed back to the queue"},
    {"key": "node_ticks", "label": "cost", "fmt": "int", "better": "low",
     "help": "Machine-ticks paid for (booting machines cost money too)"},
]


def _pack(result, tenants, label):
    tindex = {t: i for i, t in enumerate(tenants)}
    ticks = result.ticks
    stride = max(1, ceil(ticks / 800))                       # keep the page small: at most ~800 chart points
    series = {k: v[::stride] for k, v in result.series.items()}
    return {
        "name": label,
        "ticks": ticks,
        "stride": stride,
        "metrics": {c["key"]: result.metrics[c["key"]] for c in COLUMNS},
        "jobs": [[j.id, j.arrival, j.duration, j.cpu, j.mem, tindex[j.tenant], j.first_start, j.finish]
                 for j in sorted(result.jobs, key=lambda j: j.id)],
        "segments": [list(s) for s in result.segments],
        "node_log": [list(e) for e in result.node_log],
        "series": series,
    }


def build_data(workload="bursty", seed=1, nodes=4, jobs=None, switch_cost=0, autoscale=False, schedulers=None):
    """Run the workload through every scheduler and collect everything the page needs."""
    job_list = make_workload(workload, n=jobs, seed=seed)
    tenants = sorted({j.tenant for j in job_list})
    runs = []
    for name in (schedulers or SCHEDULERS):
        scaler = cluster = None
        if autoscale:
            cluster = ClusterSpec.uniform(1)
            scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=8, boot_time=5, check_every=5, idle_ticks=30)
        else:
            cluster = ClusterSpec.uniform(nodes)
        result = Simulator(job_list, name, "best_fit", cluster=cluster, autoscaler=scaler,
                           switch_cost=switch_cost, check=True, record=True).run()
        runs.append(_pack(result, tenants, name))
    if autoscale:
        where = "an autoscaled cluster (1 to 8 machines, 8 cores and 32 GB each, 5 ticks to boot)"
    else:
        where = f"{nodes} machines (8 cores and 32 GB each)"
    extra = f", every resume after a preemption costs {switch_cost} tick(s)" if switch_cost else ""
    return {
        "title": f"MiniCloudOS: the {workload} workload",
        "subtitle": f"{len(job_list)} jobs from {len(tenants)} tenants on {where}{extra}. Seed {seed}.",
        "tenants": tenants,
        "columns": COLUMNS,
        "runs": runs,
    }


def render_html(data):
    """Fill the HTML template with the data and the two JavaScript files."""
    text = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")      # keep </script> out of the data
    for marker, content in (("__DATA__", payload),
                            ("__CORE__", (HERE / "dashboard_core.js").read_text(encoding="utf-8")),
                            ("__UI__", (HERE / "dashboard_ui.js").read_text(encoding="utf-8"))):
        text = text.replace(marker, content, 1) if marker != "__DATA__" else text.replace(marker, content, 1)
    return text


def write_dashboard(path, **options):
    html = render_html(build_data(**options))
    Path(path).write_text(html, encoding="utf-8")
    return len(html)
