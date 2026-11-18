"""
A guided tour of MiniCloudOS: the headline experiments, then two interactive dashboards.

Run:  python demo.py            everything at once (about 30 seconds)
      python demo.py --step     wait for ENTER between sections (good for presenting)

In VS Code: Run and Debug panel > "Demo tour (step by step)".
"""
import os
import sys

from minicloudos import ClusterSpec, Simulator, make_workload
from minicloudos import experiments as ex

HERE = os.path.dirname(os.path.abspath(__file__))
STEP = "--step" in sys.argv


def section(title):
    if STEP:
        input("\n[press ENTER to continue]")
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def main():
    os.chdir(HERE)

    section("1. The convoy: six giant jobs arrive first, then 114 tiny ones (4 machines)")
    rows = ex.compare("convoy", check=True)
    print(ex.format_table(rows))
    print("\nwinners:")
    for what, who in ex.winners(rows).items():
        print(f"  {what:<26s} {who}")
    print("\nStrict first-come-first-served leaves tiny jobs stuck behind the giants (slowdown 64.5).")
    print("Anything that can pause a running job brings it down to about 1 to 1.6.")

    section("2. What that looks like: 14 jobs on 2 machines with round robin")
    jobs = make_workload("convoy", n=14)
    result = Simulator(jobs, "rr", cluster=ClusterSpec.uniform(2), check=True).run()
    print(ex.gantt(result))

    section("3. How long should a time slice be? (every resume costs 1 tick)")
    print(ex.format_table(ex.quantum_sweep("bursty"), "time slice"))
    print("\nToo small wastes time restoring; too big lets long jobs hold the machines.")

    section("4. Autoscaling: cost against waiting (bursty workload)")
    print(ex.format_cost_table(ex.autoscaling_comparison("bursty")))
    print("\nAutoscaling gets almost the waiting time of 8 permanent machines for about 22% less cost.")

    section("5. The interactive desktop UI")
    print("  python -m minicloudos ui        opens the live desktop-style app in your browser (writes no files)")
    print("  Open Replay, press Play, and watch the machines fill up and the queue drain.")
    print("  (Need a one-off shareable file? python -m minicloudos dashboard --out dashboard.html still works.)")


if __name__ == "__main__":
    main()
