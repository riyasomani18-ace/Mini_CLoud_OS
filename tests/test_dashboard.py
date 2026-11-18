"""The dashboard: the page builds, embeds clean data, and its JavaScript agrees with the simulator."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from minicloudos import ClusterSpec, Simulator, ThresholdAutoscaler, make_workload
from minicloudos.dashboard import COLUMNS, build_data, render_html

HARNESS = Path(__file__).parent / "dashboard_check.js"
NODE = shutil.which("node")


def run_in_fake_browser(test, data):
    """Open the page in the Node.js fake browser; returns its JSON report."""
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(render_html(data), encoding="utf-8")
        done = subprocess.run([NODE, str(HARNESS), str(page)], capture_output=True, text=True, timeout=120)
    test.assertTrue(done.stdout.strip(), done.stderr)
    report = json.loads(done.stdout)
    failed = [c for c in report["checks"] if not c["ok"]]
    test.assertEqual(failed, [], f"dashboard checks failed: {failed}")
    return report


class TestPage(unittest.TestCase):
    def setUp(self):
        self.data = build_data("bursty", seed=3, nodes=3, jobs=50, schedulers=["fcfs-bf", "rr", "mlfq"])

    def test_page_contains_the_data_and_scripts(self):
        html = render_html(self.data)
        self.assertIn('id="mc-data"', html)
        self.assertIn("MiniCloudOS: the bursty workload", html)
        for marker in ("__DATA__", "__CORE__", "__UI__"):
            self.assertNotIn(marker, html)

    def test_the_embedded_data_cannot_break_out_of_its_script_tag(self):
        self.data["subtitle"] = "evil </script><script>alert(1)</script>"
        html = render_html(self.data)
        payload = re.search(r'id="mc-data">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn("</", payload)
        self.assertEqual(json.loads(payload)["subtitle"], self.data["subtitle"])     # and it still reads back exactly

    def test_works_offline(self):
        html = render_html(self.data)
        self.assertNotRegex(html, r'(src|href)="https?://')                          # no external files or CDNs

    def test_every_column_has_a_meaning(self):
        for column in COLUMNS:
            self.assertTrue(column["help"] and column["label"])
        self.assertEqual({c["key"] for c in COLUMNS}, set(self.data["runs"][0]["metrics"]))

    def test_sampled_series_match_their_stride(self):
        from math import ceil
        for stride_case in (build_data("bursty", seed=3, nodes=3, jobs=50, schedulers=["fcfs-bf"]),
                            build_data("batch", seed=1, nodes=1, schedulers=["fcfs"])):      # the second needs stride > 1
            for run in stride_case["runs"]:
                for name, values in run["series"].items():
                    self.assertEqual(len(values), ceil(run["ticks"] / run["stride"]), name)

    def test_series_are_kept_small(self):
        big = build_data("batch", seed=1, nodes=1, schedulers=["fcfs"])               # a long run on one machine
        run = big["runs"][0]
        self.assertLessEqual(len(run["series"]["pending"]), 801)
        self.assertGreater(run["stride"], 1)


@unittest.skipUnless(NODE, "Node.js is not installed")
class TestJavaScript(unittest.TestCase):
    def check_against_simulator(self, data, make_sim):
        report = run_in_fake_browser(self, data)
        raw = {}
        for name in {s["run"] for s in report["samples"]}:
            raw[name] = make_sim(name).run().series                                   # the simulator's own per-tick counts
        self.assertGreater(len(report["samples"]), 50)
        for s in report["samples"]:
            series = raw[s["run"]]
            if s["t"] >= len(series["pending"]):
                continue                                                              # the very last tick is after the run
            where = f"{s['run']} tick {s['t']}"
            self.assertEqual(s["waiting"], series["pending"][s["t"]], f"waiting jobs differ: {where}")
            self.assertEqual(s["used"], series["used_cpu"][s["t"]], f"busy cores differ: {where}")
            self.assertEqual(s["ready"], series["ready"][s["t"]], f"ready machines differ: {where}")
            self.assertEqual(s["booting"], series["booting"][s["t"]], f"booting machines differ: {where}")
            if s["stride"] == 1:                                                      # the chart data itself, point by point
                self.assertEqual(s["sp"], series["pending"][s["t"]], f"chart queue length differs: {where}")
                self.assertEqual(s["su"], series["used_cpu"][s["t"]], f"chart busy cores differ: {where}")

    def test_fixed_cluster(self):
        schedulers = ["fcfs", "fcfs-bf", "sjf", "rr", "mlfq", "drf", "drf-p", "priority"]
        data = build_data("bursty", seed=3, nodes=3, jobs=60, schedulers=schedulers)
        jobs = make_workload("bursty", n=60, seed=3)
        self.check_against_simulator(
            data, lambda name: Simulator(jobs, name, "best_fit", cluster=ClusterSpec.uniform(3), record=True))

    def test_autoscaled_cluster(self):
        data = build_data("bursty", seed=2, jobs=80, autoscale=True, schedulers=["fcfs-bf", "mlfq"])
        jobs = make_workload("bursty", n=80, seed=2)

        def sim(name):
            scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=8, boot_time=5, check_every=5, idle_ticks=30)
            return Simulator(jobs, name, "best_fit", cluster=ClusterSpec.uniform(1), autoscaler=scaler, record=True)
        report = run_in_fake_browser(self, data)
        self.assertTrue(any(s["booting"] > 0 for s in report["samples"]), "no sample caught a machine booting")
        self.check_against_simulator(data, sim)

    def test_every_workload_loads(self):
        for kind in ("batch", "web", "convoy", "noisy"):
            run_in_fake_browser(self, build_data(kind, seed=1, nodes=4, jobs=40, schedulers=["fcfs", "mlfq"]))


if __name__ == "__main__":
    unittest.main()
