"""Model, placement and metrics: the small building blocks."""
import unittest

from minicloudos.metrics import jain_index, mean, percentile
from minicloudos.model import ClusterSpec, Job, Node
from minicloudos.placement import best_fit, first_fit, worst_fit


class TestJob(unittest.TestCase):
    def test_invalid_jobs_rejected(self):
        for bad in (dict(duration=0), dict(cpu=0), dict(mem=-1), dict(arrival=-1)):
            args = dict(id=1, arrival=0, duration=3, cpu=1, mem=1)
            args.update(bad)
            with self.assertRaises(ValueError):
                Job(**args)

    def test_fresh_copy_is_reset_and_independent(self):
        j = Job(1, 5, 10, 2, 4)
        j.remaining, j.first_start, j.finish, j.preemptions = 0, 6, 16, 3
        copy = j.fresh()
        self.assertEqual((copy.remaining, copy.first_start, copy.finish, copy.preemptions), (10, None, None, 0))
        self.assertEqual((copy.id, copy.arrival, copy.duration, copy.cpu, copy.mem), (1, 5, 10, 2, 4))

    def test_wait_turnaround_slowdown(self):
        j = Job(1, arrival=10, duration=5, cpu=1, mem=1)
        self.assertIsNone(j.wait)
        j.first_start, j.finish, j.ran_ticks = 14, 30, 5
        self.assertEqual((j.response, j.wait, j.turnaround, j.slowdown), (4, 15, 20, 4.0))
        # a job that was paused: it first ran after 4 ticks, but spent 15 ticks of its 20 waiting in total


class TestNode(unittest.TestCase):
    def test_allocate_and_release(self):
        n = Node(0, cpu=8, mem=32)
        j = Job(1, 0, 5, 3, 10)
        n.allocate(j)
        self.assertEqual((n.free_cpu, n.free_mem), (5, 22))
        n.release(j)
        self.assertEqual((n.free_cpu, n.free_mem, n.running), (8, 32, {}))

    def test_fits_respects_cpu_memory_and_state(self):
        n = Node(0, cpu=4, mem=8)
        self.assertTrue(n.fits(Job(1, 0, 1, 4, 8)))
        self.assertFalse(n.fits(Job(2, 0, 1, 5, 1)))
        self.assertFalse(n.fits(Job(3, 0, 1, 1, 9)))
        n.state = "booting"
        self.assertFalse(n.fits(Job(4, 0, 1, 1, 1)))

    def test_cluster_uniform(self):
        c = ClusterSpec.uniform(3, 4, 16)
        self.assertEqual(c.nodes, [(4, 16)] * 3)


class TestPlacement(unittest.TestCase):
    def setUp(self):
        # node 0 has 8 free cores, node 1 has 6, node 2 has 2
        self.nodes = [Node(i, cpu=8, mem=32) for i in range(3)]
        self.nodes[1].allocate(Job(100, 0, 1, 2, 1))
        self.nodes[2].allocate(Job(101, 0, 1, 6, 1))

    def test_first_fit_takes_the_first_node_with_room(self):
        self.assertEqual(first_fit(Job(1, 0, 1, 2, 1), self.nodes).id, 0)

    def test_best_fit_packs_tightest(self):
        self.assertEqual(best_fit(Job(1, 0, 1, 2, 1), self.nodes).id, 2)      # node 2 would have 0 cores left
        self.assertEqual(best_fit(Job(1, 0, 1, 4, 1), self.nodes).id, 1)      # node 2 too small, node 1 tighter than 0

    def test_worst_fit_spreads(self):
        self.assertEqual(worst_fit(Job(1, 0, 1, 2, 1), self.nodes).id, 0)

    def test_nothing_fits(self):
        big = Job(1, 0, 1, 9, 1)
        for fn in (first_fit, best_fit, worst_fit):
            self.assertIsNone(fn(big, self.nodes))

    def test_memory_matters_too(self):
        self.assertIsNone(first_fit(Job(1, 0, 1, 1, 33), self.nodes))


class TestMetrics(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        self.assertEqual(percentile(list(range(1, 101)), 95), 95.0)
        self.assertEqual(percentile([5], 99), 5.0)
        self.assertEqual(percentile([], 50), 0.0)

    def test_mean(self):
        self.assertEqual(mean([1, 2, 3]), 2.0)
        self.assertEqual(mean([]), 0.0)

    def test_jain_index(self):
        self.assertAlmostEqual(jain_index([1, 1, 1]), 1.0)
        self.assertAlmostEqual(jain_index([3, 1]), 0.8)               # 16 / (2 * 10)
        self.assertAlmostEqual(jain_index([1, 0, 0, 0]), 0.25)         # one of four gets everything
        self.assertEqual(jain_index([]), 1.0)


if __name__ == "__main__":
    unittest.main()
