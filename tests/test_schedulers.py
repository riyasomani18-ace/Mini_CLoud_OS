"""Schedulers, checked against small scenarios whose answers were worked out by hand."""
import unittest

from minicloudos import ClusterSpec, Job, Simulator
from minicloudos.schedulers import DRFPreemptive, MLFQ, Priority, RoundRobin, make_scheduler


def run(jobs, scheduler, nodes=((4, 16),), **kw):
    cluster = ClusterSpec(nodes=list(nodes), new_node_shape=nodes[0])
    result = Simulator(jobs, scheduler, cluster=cluster, check=True, **kw).run()
    return {j.id: j for j in result.jobs}, result


class TestFCFSandSJF(unittest.TestCase):
    def jobs(self):
        return [Job(0, 0, 5, 4, 4), Job(1, 0, 2, 4, 4)]       # a long job and a short one, one machine

    def test_fcfs_runs_in_arrival_order(self):
        j, _ = run(self.jobs(), "fcfs")
        self.assertEqual((j[0].first_start, j[0].finish), (0, 5))
        self.assertEqual((j[1].first_start, j[1].finish), (5, 7))

    def test_sjf_runs_the_short_job_first(self):
        j, _ = run(self.jobs(), "sjf")
        self.assertEqual((j[1].first_start, j[1].finish), (0, 2))
        self.assertEqual((j[0].first_start, j[0].finish), (2, 7))

    def test_sjf_has_the_lower_average_wait(self):
        _, fcfs = run(self.jobs(), "fcfs")
        _, sjf = run(self.jobs(), "sjf")
        self.assertEqual(fcfs.metrics["mean_wait"], 2.5)          # (0 + 5) / 2
        self.assertEqual(sjf.metrics["mean_wait"], 1.0)           # (0 + 2) / 2


class TestHeadOfLineBlocking(unittest.TestCase):
    def jobs(self):
        # one 8-core machine. A uses 4 cores. B needs all 8 (cannot start yet). C needs 2 (would fit right now).
        return [Job(0, 0, 10, 4, 4), Job(1, 0, 5, 8, 8), Job(2, 0, 2, 2, 2)]

    def test_strict_fcfs_makes_the_small_job_wait_behind_the_blocked_one(self):
        j, _ = run(self.jobs(), "fcfs", nodes=((8, 32),))
        self.assertEqual((j[1].first_start, j[1].finish), (10, 15))
        self.assertEqual((j[2].first_start, j[2].finish), (15, 17))      # stuck behind B

    def test_backfill_lets_the_small_job_use_the_gap(self):
        j, _ = run(self.jobs(), "fcfs-bf", nodes=((8, 32),))
        self.assertEqual((j[2].first_start, j[2].finish), (0, 2))
        self.assertEqual((j[1].first_start, j[1].finish), (10, 15))      # the big job is not delayed


class TestPriority(unittest.TestCase):
    def jobs(self):
        urgent = [Job(i + 1, i, 1, 1, 1, priority=0) for i in range(10)]    # one urgent job every tick
        return [Job(0, 0, 1, 1, 1, priority=5)] + urgent

    def test_without_aging_the_low_priority_job_starves_until_the_end(self):
        j, _ = run(self.jobs(), Priority(aging=None), nodes=((1, 4),))
        self.assertEqual(j[0].first_start, 10)

    def test_aging_lets_it_overtake(self):
        j, _ = run(self.jobs(), Priority(aging=1), nodes=((1, 4),))
        self.assertEqual(j[0].first_start, 5)                              # priority 5 - 5 ticks waited = 0


class TestRoundRobin(unittest.TestCase):
    def jobs(self):
        return [Job(0, 0, 4, 4, 4), Job(1, 0, 4, 4, 4)]

    def test_two_jobs_take_turns(self):
        j, _ = run(self.jobs(), RoundRobin(q=2))
        self.assertEqual((j[0].first_start, j[0].finish), (0, 6))
        self.assertEqual((j[1].first_start, j[1].finish), (2, 8))
        self.assertEqual((j[0].preemptions, j[1].preemptions), (1, 1))

    def test_switching_costs_time(self):
        # A runs 0-1 and is pushed back at 2; B runs 2-3 and is pushed back at 4. A resumes: tick 4 is spent
        # restoring (no work, and the slice has not started), ticks 5-6 are its slice: it finishes at 7.
        # B resumes at 7: restore at 7, work 8-9: it finishes at 10.
        j, _ = run(self.jobs(), RoundRobin(q=2), switch_cost=1)
        self.assertEqual((j[0].finish, j[1].finish), (7, 10))
        self.assertEqual((j[0].preemptions, j[1].preemptions), (1, 1))
        for job in j.values():
            self.assertEqual(job.ran_ticks, job.duration + job.preemptions * 1)

    def test_tiny_time_slices_waste_time_but_still_make_progress(self):
        # slice 1 and a restore cost of 1: half of every resume is wasted, yet both jobs finish
        j, r = run([Job(0, 0, 2, 4, 4), Job(1, 0, 2, 4, 4)], RoundRobin(q=1), switch_cost=1)
        self.assertEqual((j[0].finish, j[1].finish), (4, 6))
        # the cores were allocated all 6 ticks, but only 4 of those ticks were real work
        self.assertAlmostEqual(r.metrics["cpu_utilization"], 1.0)
        self.assertAlmostEqual(r.metrics["cpu_useful"], 4 / 6)

    def test_a_thrashing_scheduler_is_stopped_with_a_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            run([Job(0, 0, 5, 4, 4), Job(1, 0, 5, 4, 4)], RoundRobin(q=0), switch_cost=1)
        self.assertIn("progress", str(ctx.exception))

    def test_wait_counts_all_the_time_in_the_queue_not_just_the_first_wait(self):
        # job 0 ran at once (response 0) but sat out ticks 2-3 (wait 2); job 1 first ran at tick 2 and sat out 0-1 and 4-5
        j, r = run(self.jobs(), RoundRobin(q=2))
        self.assertEqual((j[0].response, j[0].wait), (0, 2))
        self.assertEqual((j[1].response, j[1].wait), (2, 4))
        self.assertEqual((r.metrics["mean_response"], r.metrics["mean_wait"]), (1.0, 3.0))

    def test_without_preemption_wait_and_response_are_the_same(self):
        j, _ = run([Job(0, 0, 5, 4, 4), Job(1, 0, 2, 4, 4)], "fcfs")
        for job in j.values():
            self.assertEqual(job.wait, job.response)

    def test_a_job_alone_is_never_preempted(self):
        j, _ = run([Job(0, 0, 9, 4, 4)], RoundRobin(q=2))
        self.assertEqual((j[0].finish, j[0].preemptions), (9, 0))

    def test_no_pointless_preemption_when_the_waiting_job_cannot_use_the_room(self):
        # 4-core machine: job 0 holds 2 cores, job 2 holds 1. Job 1 needs all 4. Freeing EITHER running job
        # still leaves too little room, so pushing them back would only waste time: nobody is preempted.
        jobs = [Job(0, 0, 6, 2, 2), Job(2, 0, 6, 1, 1), Job(1, 0, 1, 4, 4)]
        j, _ = run(jobs, RoundRobin(q=2))
        self.assertEqual((j[0].preemptions, j[2].preemptions), (0, 0))
        self.assertEqual((j[0].finish, j[2].finish), (6, 6))
        self.assertEqual(j[1].first_start, 6)


class TestMLFQ(unittest.TestCase):
    def test_long_job_sinks_and_the_short_job_gets_in(self):
        jobs = [Job(0, 0, 10, 1, 1), Job(1, 3, 2, 1, 1)]
        j, _ = run(jobs, MLFQ(quanta=(2, 4), boost_every=0), nodes=((1, 4),))
        # L runs 0-5 (demoted after tick 2, so its slice is 4), is pushed back at tick 6, S runs 6-8, L finishes at 12
        self.assertEqual((j[1].first_start, j[1].finish), (6, 8))
        self.assertEqual((j[0].finish, j[0].preemptions), (12, 1))

    def test_demotion_and_boost_hooks(self):
        sched = MLFQ(quanta=(2, 4, 8), boost_every=10)
        job = Job(0, 0, 50, 1, 1)

        class Fake:                                  # the only thing the hooks need from the simulator
            @staticmethod
            def active_jobs():
                return [job]
        sched.on_quantum_expired(job, Fake)
        sched.on_quantum_expired(job, Fake)
        sched.on_quantum_expired(job, Fake)
        self.assertEqual(job.level, 2)               # cannot sink below the last level
        self.assertEqual(sched.quantum(job), 8)
        sched.on_tick(9, Fake)
        self.assertEqual(job.level, 2)
        sched.on_tick(10, Fake)
        self.assertEqual(job.level, 0)               # boosted back to the top


class TestDRF(unittest.TestCase):
    def jobs(self):
        return [Job(0, 0, 5, 6, 1, tenant="A"),      # A holds 60% of the cores
                Job(1, 1, 5, 4, 1, tenant="A"),
                Job(2, 1, 5, 4, 1, tenant="B")]

    def test_drf_serves_the_tenant_with_the_smaller_share_first(self):
        j, _ = run(self.jobs(), "drf", nodes=((10, 10),))
        self.assertEqual(j[2].first_start, 1)        # B has nothing yet, so B goes first
        self.assertEqual(j[1].first_start, 5)

    def test_plain_fcfs_backfill_serves_arrival_order(self):
        j, _ = run(self.jobs(), "fcfs-bf", nodes=((10, 10),))
        self.assertEqual(j[1].first_start, 1)
        self.assertEqual(j[2].first_start, 5)


class TestPreemptiveDRF(unittest.TestCase):
    def jobs(self):
        # tenant A's one big job fills the whole machine for 20 ticks; tenant B arrives with a tiny job
        return [Job(0, 0, 20, 10, 1, tenant="A"), Job(1, 1, 2, 2, 1, tenant="B")]

    def test_plain_drf_cannot_take_the_machine_back(self):
        j, _ = run(self.jobs(), "drf", nodes=((10, 10),))
        self.assertEqual((j[1].first_start, j[1].finish), (20, 22))

    def test_drf_with_time_slices_rebalances(self):
        # Slice of 2: A is pushed back at tick 2. Both tenants now hold nothing (equal shares), and B has waited
        # longer, so B goes next (a tie broken by tenant NAME would hand the machine straight back to A).
        # B runs 2-3, then A resumes and needs its remaining 18 ticks: it ends at 22.
        j, _ = run(self.jobs(), DRFPreemptive(q=2), nodes=((10, 10),))
        self.assertEqual((j[1].first_start, j[1].finish), (2, 4))
        self.assertEqual((j[0].finish, j[0].preemptions), (22, 1))

    def test_equal_shares_are_broken_by_who_waited_longest(self):
        from minicloudos.schedulers import DRF

        class Sim:
            now = 5

            @staticmethod
            def tenant_usage(tenant):
                return 0, 0                               # nobody holds anything: every share is equal

            @staticmethod
            def total_capacity():
                return 10, 10
        older = Job(1, 0, 1, 1, 1, tenant="zeta")
        newer = Job(2, 0, 1, 1, 1, tenant="alpha")
        older.enqueued_at, newer.enqueued_at = 1, 3
        self.assertEqual(DRF().order([newer, older], Sim)[0].id, 1)     # not alphabetical: "zeta" waited longer


class TestFactory(unittest.TestCase):
    def test_unknown_scheduler_has_a_helpful_error(self):
        with self.assertRaises(ValueError) as ctx:
            make_scheduler("nope")
        self.assertIn("fcfs", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
