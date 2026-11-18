"""
Mutation check: can the tests actually fail?

A test suite that has never been seen to fail proves little. This script breaks the code on purpose, one small change at a
time (a flipped comparison, a missing release, a reversed sort), runs the tests, and reports whether they noticed.
Every source file is restored afterwards, even if you press Ctrl-C.

    python mutation_check.py              the scheduler/engine changes, then the dashboard changes
    python mutation_check.py --engine     only the engine
    python mutation_check.py --dashboard  only the dashboard (needs Node.js)

A change that SURVIVES means the tests have a gap: add a test that fails on it.
"""
import os
import shutil
import subprocess
import sys

ENGINE = [
    ("minicloudos/model.py", 'return self.state == "ready" and job.cpu <= self.free_cpu and job.mem <= self.free_mem',
     'return self.state == "ready" and job.cpu <= self.free_cpu', "machines ignore memory limits"),
    ("minicloudos/model.py", "job.cpu <= self.free_cpu and job.mem <= self.free_mem",
     "job.cpu <= self.free_cpu + 1 and job.mem <= self.free_mem", "machines over-commit cores by one"),
    ("minicloudos/simulator.py", "                if useful:\n", "                if True:\n", "always preempt, even when pointless"),
    ("minicloudos/simulator.py", "job.overhead_left = self.switch_cost", "job.overhead_left = 0", "restore cost ignored"),
    ("minicloudos/simulator.py", "if q is None or job.slice_used < q:", "if q is None or job.slice_used <= q:", "time slice off by one"),
    ("minicloudos/simulator.py", "                elif not sched.backfill:\n                    break", "                elif False:\n                    break",
     "strict queue never blocks"),
    ("minicloudos/simulator.py", "        node.release(job)\n        job.node = None", "        job.node = None", "finished jobs never free their machine"),
    ("minicloudos/simulator.py", 'c["node_ticks"] += len(ready) + booting', 'c["node_ticks"] += len(ready)', "booting machines not billed"),
    ("minicloudos/simulator.py", '                self._counters["work_cpu_ticks"] += job.cpu\n', "", "useful-work counter dropped"),
    ("minicloudos/simulator.py", "if autoscaler and autoscaler.max_nodes > len(self.cluster.nodes):", "if autoscaler:",
     "validation trusts an autoscaler that cannot add machines"),
    ("minicloudos/placement.py", "    for node in nodes:\n        if node.fits(job):\n            return node\n    return None",
     "    for node in reversed(nodes):\n        if node.fits(job):\n            return node\n    return None", "first-fit takes the last machine"),
    ("minicloudos/placement.py", "return min(feasible, key=lambda n: (_leftover(n, job), n.id))",
     "return max(feasible, key=lambda n: (_leftover(n, job), n.id))", "best-fit picks the loosest"),
    ("minicloudos/schedulers.py", "tenants = sorted(by_tenant, key=lambda t: (self.dominant_share(t, sim),\n                                                   min(j.enqueued_at for j in by_tenant[t]), t))",
     "tenants = sorted(by_tenant, key=lambda t: (-self.dominant_share(t, sim), t))", "DRF serves the biggest tenant first"),
    ("minicloudos/schedulers.py", "return job.priority - (now - job.enqueued_at) // self.aging",
     "return job.priority + (now - job.enqueued_at) // self.aging", "priority aging goes the wrong way"),
    ("minicloudos/schedulers.py", "        job.level = min(job.level + 1, len(self.quanta) - 1)", "        pass", "MLFQ never demotes"),
    ("minicloudos/schedulers.py", "return sorted(pending, key=lambda j: (j.remaining, j.enqueued_at, j.id))",
     "return sorted(pending, key=lambda j: (-j.remaining, j.enqueued_at, j.id))", "SJF runs the longest job first"),
    ("minicloudos/autoscaler.py", "if not node.running and now - node.idle_since >= self.idle_ticks:", "if not node.running:",
     "autoscaler ignores the idle grace period"),
    ("minicloudos/autoscaler.py", 'actions.append(("add", min(needed, self.max_nodes - total)))', 'actions.append(("add", needed))',
     "autoscaler ignores its maximum"),
    ("minicloudos/autoscaler.py", "removable = [n for n in ready if (n.cpu, n.mem) == tuple(sim.cluster.new_node_shape)]",
     "removable = list(ready)", "autoscaler may remove the only big machine"),
    ("minicloudos/autoscaler.py", "if job.cpu <= slot[0] and job.mem <= slot[1]:", "if job.cpu <= slot[0]:", "scale-up ignores memory"),
    ("minicloudos/autoscaler.py", "slots = [[n.free_cpu, n.free_mem] for n in ready] + [[n.cpu, n.mem] for n in booting]",
     "slots = [[sum(n.free_cpu for n in ready), 10**9]]", "scale-up counts total free cores, not per machine"),
    ("minicloudos/metrics.py", "k = max(0, min(len(ordered) - 1, ceil(p / 100 * len(ordered)) - 1))",
     "k = max(0, min(len(ordered) - 1, ceil(p / 100 * len(ordered))))", "percentile off by one"),
    ("minicloudos/metrics.py", "return (sum(values) ** 2) / (len(values) * total_sq) if total_sq else 1.0",
     "return (sum(values) ** 2) / (len(values) * total_sq + 1) if total_sq else 1.0", "fairness formula slightly wrong"),
    ("minicloudos/model.py", "return None if self.finish is None else (self.finish - self.arrival) - self.ran_ticks",
     "return None if self.first_start is None else self.first_start - self.arrival", "wait measured only until the first run"),
]

DASHBOARD = [
    ("minicloudos/dashboard_core.js", "if (s[2] <= t && t < s[3]) { (by[s[1]]", "if (s[2] <= t && t <= s[3]) { (by[s[1]]",
     "a job still shows as running on the tick it finished"),
    ("minicloudos/dashboard_core.js", "if (j.arrival <= t && t < j.finish && !running[j.id]) out.push(j.id);",
     "if (j.arrival <= t && t < j.finish) out.push(j.id);", "running jobs also appear in the waiting queue"),
    ("minicloudos/dashboard_core.js", "if (j.arrival <= t && t < j.finish && !running[j.id])", "if (j.arrival < t && t < j.finish && !running[j.id])",
     "a job is not waiting on the tick it arrives"),
    ("minicloudos/dashboard_core.js", "if (e[0] > t) break;", "if (e[0] >= t) break;", "a machine appears one tick late"),
    ("minicloudos/dashboard_core.js", "total += run._jobs[id].cpu;", "total += 1;", "busy cores counted as jobs"),
    ("minicloudos/dashboard_core.js", "if (c.better === 'low' ? v < b : v > b) best = i;", "if (c.better === 'low' ? v > b : v > b) best = i;",
     "'best' is the highest even for 'lower is better'"),
    ("minicloudos/dashboard_core.js", "var i = Math.min(Math.floor(t / run.stride), run.series.pending.length - 1);", "var i = 0;",
     "chart series ignores the time"),
    ("minicloudos/dashboard_ui.js", "setTick(state.t - 1); });", "setTick(state.t + 1); });", "step back moves forward"),
    ("minicloudos/dashboard_ui.js", "setTick(state.t + state.speed);", "setTick(state.t + 1);", "play ignores the chosen speed"),
    ("minicloudos/dashboard_ui.js", "state.t = Math.min(state.t, run().ticks); refresh(); }", "refresh(); }",
     "switching scheduler can leave the clock past the end"),
    ("minicloudos/dashboard_ui.js", "$('scheduler').addEventListener('change', function () { selectRun(parseInt($('scheduler').value, 10)); });", "",
     "the scheduler menu does nothing"),
    ("minicloudos/dashboard_ui.js", "var best = MC.bestPerColumn(data.runs, data.columns);", "var best = data.columns.map(function () { return -1; });",
     "no best-value highlighting"),
    ("minicloudos/dashboard.py", 'payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\\\/")', 'payload = json.dumps(data, separators=(",", ":"))',
     "data can break out of its script tag"),
    ("minicloudos/dashboard.py", "series = {k: v[::stride] for k, v in result.series.items()}", "series = {k: v[::stride + 1] for k, v in result.series.items()}",
     "chart sampling does not match the stride"),
]


def run_set(name, mutations, test_command):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    caught, survivors = 0, []
    print(f"\n{name}: {len(mutations)} deliberate breakages")
    for path, old, new, what in mutations:
        original = open(path, encoding="utf-8").read()
        if old not in original:
            print(f"  SKIPPED   {what} (the code it targets has changed: update this script)")
            continue
        try:
            open(path, "w", encoding="utf-8").write(original.replace(old, new, 1))
            try:
                done = subprocess.run(test_command, capture_output=True, text=True, env=env, timeout=180)
                noticed = done.returncode != 0
            except subprocess.TimeoutExpired:
                noticed = True                                    # a hang counts as noticed
        finally:
            open(path, "w", encoding="utf-8").write(original)   # always put the original back
        caught += noticed
        if not noticed:
            survivors.append(what)
        print(f"  {'caught  ' if noticed else 'SURVIVED'}  {what}")
    print(f"  -> {caught} of {len(mutations)} caught")
    return caught, len(mutations), survivors


def main():
    only_engine, only_dash = "--engine" in sys.argv, "--dashboard" in sys.argv
    base = [sys.executable, "-m", "unittest"]
    totals = [0, 0]
    survivors = []
    if not only_dash:
        c, n, s = run_set("Engine and schedulers", ENGINE, base + ["discover", "-s", "tests", "-t", "."])
        totals[0] += c; totals[1] += n; survivors += s
    if not only_engine:
        if shutil.which("node"):
            c, n, s = run_set("Dashboard", DASHBOARD, base + ["tests.test_dashboard"])
            totals[0] += c; totals[1] += n; survivors += s
        else:
            print("\nDashboard: skipped (Node.js is not installed)")
    print(f"\nTOTAL: {totals[0]} of {totals[1]} deliberate breakages caught")
    if survivors:
        print("The tests missed: " + "; ".join(survivors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
