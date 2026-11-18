"""The simulator as a whole: errors, determinism, an independent referee over many runs, the autoscaler."""
import unittest

from minicloudos import (SCHEDULERS, ClusterSpec, InvariantError, Job, Simulator,
                         ThresholdAutoscaler, make_workload)
from minicloudos.placement import PLACEMENTS
from minicloudos.workloads import WORKLOADS
from tests.helpers import verify_result

SMALL = ClusterSpec.uniform(3, 8, 32)


class TestErrorsAndBasics(unittest.TestCase):
    def test_duplicate_job_ids_rejected(self):
        with self.assertRaises(ValueError):
            Simulator([Job(1, 0, 1, 1, 1), Job(1, 0, 1, 1, 1)])

    def test_job_that_fits_no_machine_is_rejected_up_front(self):
        with self.assertRaises(ValueError) as ctx:
            Simulator([Job(1, 0, 1, 99, 1)], cluster=SMALL)
        self.assertIn("fits on no machine", str(ctx.exception))

    def test_unknown_names_have_helpful_errors(self):
        with self.assertRaises(ValueError):
            Simulator([Job(1, 0, 1, 1, 1)], scheduler="nope")
        with self.assertRaises(ValueError):
            make_workload("nope")

    def test_the_callers_jobs_are_not_modified(self):
        jobs = make_workload("batch", n=20)
        Simulator(jobs, "rr", cluster=SMALL).run()
        self.assertTrue(all(j.first_start is None and j.finish is None and j.remaining == j.duration for j in jobs))

    def test_same_input_gives_exactly_the_same_result(self):
        jobs = make_workload("bursty", n=60, seed=3)
        a = Simulator(jobs, "mlfq", cluster=SMALL).run()
        b = Simulator(jobs, "mlfq", cluster=SMALL).run()
        self.assertEqual(a.metrics, b.metrics)
        self.assertEqual(a.segments, b.segments)

    def test_a_workload_is_repeatable_from_its_seed(self):
        a = make_workload("noisy", seed=5)
        b = make_workload("noisy", seed=5)
        self.assertEqual([(j.arrival, j.duration, j.cpu, j.tenant) for j in a],
                         [(j.arrival, j.duration, j.cpu, j.tenant) for j in b])
        c = make_workload("noisy", seed=6)
        self.assertNotEqual([(j.arrival, j.duration) for j in a], [(j.arrival, j.duration) for j in c])

    def test_utilization_is_exact_for_a_simple_case(self):
        # one 4-core machine; one job using 2 cores for 10 ticks -> 20 of 40 core-ticks = 50%
        r = Simulator([Job(0, 0, 10, 2, 1)], cluster=ClusterSpec.uniform(1, 4, 8), check=True).run()
        self.assertAlmostEqual(r.metrics["cpu_utilization"], 0.5)
        self.assertEqual((r.metrics["makespan"], r.metrics["node_ticks"]), (10, 10))

    def test_idle_gaps_are_simulated(self):
        r = Simulator([Job(0, 0, 2, 1, 1), Job(1, 50, 2, 1, 1)], cluster=ClusterSpec.uniform(1, 4, 8), check=True).run()
        self.assertEqual({j.id: j.first_start for j in r.jobs}, {0: 0, 1: 50})

    def test_the_self_check_really_catches_a_broken_machine(self):
        sim = Simulator([Job(0, 0, 5, 2, 2)], cluster=ClusterSpec.uniform(1, 4, 8), check=True)
        original = sim._start

        def broken(job, node):                   # secretly claims more cores than were reserved
            original(job, node)
            node.used_cpu += 1
        sim._start = broken
        with self.assertRaises(InvariantError):
            sim.run()


class TestGanttAndReferee(unittest.TestCase):
    def test_gantt_shows_waiting_and_running(self):
        from minicloudos.experiments import gantt
        r = Simulator([Job(0, 0, 3, 4, 4), Job(1, 0, 2, 4, 4)], "fcfs", cluster=ClusterSpec.uniform(1, 4, 16)).run()
        lines = gantt(r).splitlines()
        self.assertIn("job 0   default |AAA  |", lines[1])
        self.assertIn("job 1   default |...BB|".replace("B", "A"), lines[2])      # same machine, so also 'A'

    def test_gantt_keeps_short_jobs_visible_when_columns_are_wide(self):
        from minicloudos.experiments import gantt
        jobs = [Job(0, 0, 100, 4, 4), Job(1, 10, 1, 1, 1)]                        # a 1-tick job beside a long one
        r = Simulator(jobs, "fcfs-bf", cluster=ClusterSpec.uniform(1, 8, 16)).run()
        short_row = gantt(r, width=10).splitlines()[2]
        self.assertIn("A", short_row)                                             # it ran, so it must show up

    def test_the_referee_catches_a_cheating_result(self):
        from minicloudos.referee import RefereeError, verify_result as referee
        r = Simulator([Job(0, 0, 5, 2, 2), Job(1, 0, 5, 2, 2)], "fcfs-bf", cluster=ClusterSpec.uniform(1, 4, 8)).run()
        referee(r)                                                                # the honest result passes
        job_id, node_id, start, end = r.segments[0]
        cheat = [(job_id, node_id, start, end + 1)] + r.segments[1:]               # claim a job ran a tick longer
        r.segments[:] = cheat
        with self.assertRaises(RefereeError):
            referee(r)


class TestEverythingTogether(unittest.TestCase):
    """Every scheduler x every placement x every workload, refereed independently."""

    def test_all_combinations(self):
        runs = 0
        for kind in WORKLOADS:
            jobs = make_workload(kind, n=40, seed=2)
            for sched in SCHEDULERS:
                for place in PLACEMENTS:
                    with self.subTest(workload=kind, scheduler=sched, placement=place):
                        r = Simulator(jobs, sched, place, cluster=SMALL, check=True).run()
                        verify_result(self, r)
                        runs += 1
        self.assertEqual(runs, len(WORKLOADS) * len(SCHEDULERS) * len(PLACEMENTS))

    def test_with_switching_cost(self):
        jobs = make_workload("bursty", n=50, seed=4)
        for sched in ("rr", "mlfq"):
            r = Simulator(jobs, sched, cluster=SMALL, switch_cost=2, check=True).run()
            verify_result(self, r, switch_cost=2)
            self.assertGreater(r.metrics["preemptions"], 0)

    def test_nonpreemptive_schedulers_never_preempt(self):
        jobs = make_workload("batch", n=50, seed=1)
        for sched in ("fcfs", "fcfs-bf", "sjf", "priority", "drf"):
            r = Simulator(jobs, sched, cluster=SMALL, check=True).run()
            self.assertEqual(r.metrics["preemptions"], 0, sched)

    def test_known_ranking_on_the_convoy_workload(self):
        # tiny jobs stuck behind whole-machine giants: strict FCFS is the worst, and preemption is the cure
        jobs = make_workload("convoy", n=60, seed=1)
        slow = {s: Simulator(jobs, s, cluster=ClusterSpec.uniform(4), check=True).run().metrics["mean_slowdown"]
                for s in ("fcfs", "sjf", "rr", "mlfq")}
        self.assertEqual(max(slow, key=slow.get), "fcfs")
        self.assertLess(slow["mlfq"], slow["fcfs"] / 5)
        self.assertLess(slow["rr"], slow["fcfs"] / 5)


class TestAutoscaler(unittest.TestCase):
    def setUp(self):
        jobs = [Job(i, 0, 10, 4, 4) for i in range(6)] + [Job(6, 100, 2, 4, 4)]
        self.scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=3, boot_time=3, check_every=1, idle_ticks=5)
        cluster = ClusterSpec(nodes=[(4, 16)], new_node_shape=(4, 16))
        self.result = Simulator(jobs, "fcfs-bf", cluster=cluster, autoscaler=self.scaler, check=True).run()
        self.jobs = {j.id: j for j in self.result.jobs}

    def test_scales_up_to_the_maximum_but_no_further(self):
        self.assertEqual(max(self.result.series["ready"]), 3)

    def test_new_machines_need_boot_time_before_they_can_work(self):
        self.assertEqual(self.jobs[0].first_start, 0)
        self.assertEqual((self.jobs[1].first_start, self.jobs[2].first_start), (3, 3))     # booted at tick 3
        self.assertEqual(self.jobs[3].first_start, 10)                                      # no 4th machine allowed
        self.assertEqual(self.result.series["booting"][:4], [2, 2, 2, 0])

    def test_scales_back_down_when_idle_but_keeps_the_minimum(self):
        self.assertEqual(self.result.series["ready"][50], 1)
        self.assertEqual(self.result.series["ready"][-1], 1)
        self.assertEqual(self.jobs[6].first_start, 100)

    def test_idle_machines_are_removed_in_the_order_they_became_idle(self):
        # node 0 finished its last job at tick 20, nodes 1 and 2 at tick 23. With a 5-tick idle limit:
        # node 0 goes at tick 25, node 2 at tick 28, and node 1 stays because the minimum is 1 machine.
        removed = {node_id: tick for tick, node_id, _, _, state in self.result.node_log if state == "removed"}
        self.assertEqual(removed, {0: 25, 2: 28})

    def test_cost_is_counted_exactly(self):
        # 3 machines for ticks 0-24 (the 2 new ones are billed while booting), 2 for ticks 25-27, 1 for ticks 28-101
        self.assertEqual(self.result.metrics["node_ticks"], 25 * 3 + 3 * 2 + 74 * 1)

    def test_referee_agrees(self):
        verify_result(self, self.result)

    def test_instant_boot(self):
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=2, boot_time=0, check_every=1)
        jobs = [Job(0, 0, 5, 4, 4), Job(1, 0, 5, 4, 4)]
        r = Simulator(jobs, "fcfs-bf", cluster=ClusterSpec(nodes=[(4, 16)], new_node_shape=(4, 16)),
                      autoscaler=scaler, check=True).run()
        self.assertEqual({j.id: j.first_start for j in r.jobs}, {0: 0, 1: 0})

    def test_scales_up_for_a_job_that_needs_more_memory_than_any_machine_has(self):
        # plenty of free cores in total, but the job needs 20 GB and every machine has 8: only a new machine helps
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=3, boot_time=2, check_every=1)
        cluster = ClusterSpec(nodes=[(8, 8), (8, 8)], new_node_shape=(8, 32))
        r = Simulator([Job(0, 0, 3, 1, 20)], "fcfs-bf", cluster=cluster, autoscaler=scaler, check=True).run()
        self.assertEqual(r.jobs[0].first_start, 2)                  # had to wait for the new machine to boot
        verify_result(self, r)

    def test_scales_up_when_free_cores_are_spread_over_too_many_machines(self):
        # 8 cores are free in total (4 + 4) but no machine has the 6 the job needs
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=3, boot_time=1, check_every=1)
        cluster = ClusterSpec(nodes=[(4, 16), (4, 16)], new_node_shape=(16, 64))
        r = Simulator([Job(0, 0, 3, 6, 4)], "fcfs-bf", cluster=cluster, autoscaler=scaler, check=True).run()
        self.assertEqual(r.jobs[0].first_start, 1)
        verify_result(self, r)

    def test_adds_only_as_many_machines_as_the_waiting_jobs_need(self):
        # three 4-core jobs and a machine already booting for one of them: only 1 more machine is needed
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=8, boot_time=3, check_every=1)
        cluster = ClusterSpec(nodes=[(4, 16)], new_node_shape=(4, 16))
        r = Simulator([Job(i, 0, 5, 4, 4) for i in range(3)], "fcfs-bf", cluster=cluster,
                      autoscaler=scaler, check=True).run()
        self.assertEqual(max(r.series["ready"]), 3)                 # 1 + exactly 2 new, not more

    def test_the_only_big_machine_is_never_removed(self):
        # machines: a small one and a big one (16 cores). New machines would be small. Job B needs 12 cores and
        # arrives long after everything went idle: if the big machine had been scaled away, B could never run.
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=3, boot_time=2, check_every=1, idle_ticks=5)
        cluster = ClusterSpec(nodes=[(8, 32), (16, 64)], new_node_shape=(8, 32))
        r = Simulator([Job(0, 0, 2, 1, 1), Job(1, 100, 3, 12, 8)], "fcfs-bf", cluster=cluster,
                      autoscaler=scaler, check=True).run()
        removed = {node_id for _, node_id, _, _, state in r.node_log if state == "removed"}
        self.assertEqual(removed, {0})                      # the small one went, the big one (node 1) stayed
        self.assertEqual({j.id: j.first_start for j in r.jobs}[1], 100)
        verify_result(self, r)

    def test_a_job_that_needs_a_machine_nobody_can_add_is_rejected_up_front(self):
        # the autoscaler is already at its maximum, and the job fits only the shape it would add
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=2, boot_time=1)
        cluster = ClusterSpec(nodes=[(4, 16), (4, 16)], new_node_shape=(16, 64))
        with self.assertRaises(ValueError) as ctx:
            Simulator([Job(0, 0, 3, 12, 8)], cluster=cluster, autoscaler=scaler)
        self.assertIn("fits on no machine", str(ctx.exception))

    def test_bad_settings_rejected(self):
        with self.assertRaises(ValueError):
            ThresholdAutoscaler(min_nodes=5, max_nodes=2)

    def test_autoscaling_is_refereed_on_a_random_workload(self):
        jobs = make_workload("bursty", n=80, seed=7)
        scaler = ThresholdAutoscaler(min_nodes=1, max_nodes=6, boot_time=5, check_every=5, idle_ticks=20)
        r = Simulator(jobs, "mlfq", cluster=ClusterSpec.uniform(1), autoscaler=scaler, check=True).run()
        verify_result(self, r)
        self.assertGreater(max(r.series["ready"]), 1)


if __name__ == "__main__":
    unittest.main()
