"""
Autoscaler: add machines when the cluster is overloaded, remove idle ones to save money.

Only machines of the autoscaler's own shape are ever removed (see decide()).

Adding a machine is not instant: a new node spends `boot_time` ticks starting up (and costs money
meanwhile) before it can run jobs. That delay is what makes autoscaling a real trade-off.
"""

class ThresholdAutoscaler:
    def __init__(self, min_nodes=1, max_nodes=8, boot_time=5, check_every=5, idle_ticks=30):
        if not 1 <= min_nodes <= max_nodes:
            raise ValueError("need 1 <= min_nodes <= max_nodes")
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.boot_time = boot_time
        self.check_every = check_every
        self.idle_ticks = idle_ticks

    @staticmethod
    def _machines_needed(pending, ready, booting, shape):
        """How many NEW machines the waiting jobs would need. Pretend to place every waiting job (biggest
        first) into the free space of running machines and into machines that are still booting; each job
        that fits nowhere opens one virtual new machine (which later jobs may also use).
        Looking at each job separately matters: 10 free cores spread over three machines do not help a job
        that needs 8, and plenty of free cores do not help a job that needs more memory than any machine has."""
        slots = [[n.free_cpu, n.free_mem] for n in ready] + [[n.cpu, n.mem] for n in booting]
        extra = 0
        for job in sorted(pending, key=lambda j: (-j.cpu, -j.mem, j.id)):
            for slot in slots:
                if job.cpu <= slot[0] and job.mem <= slot[1]:
                    slot[0] -= job.cpu
                    slot[1] -= job.mem
                    break
            else:
                if job.cpu <= shape[0] and job.mem <= shape[1]:        # a new machine would help
                    slots.append([shape[0] - job.cpu, shape[1] - job.mem])
                    extra += 1
        return extra

    def decide(self, now, sim):
        """Returns a list of actions: ('add', how_many) and ('remove', node_id)."""
        if now % self.check_every:
            return []
        ready = sim.nodes_in_state("ready")
        booting = sim.nodes_in_state("booting")
        total = len(ready) + len(booting)
        actions = []

        # --- scale up: some waiting job fits on no machine that exists or is starting
        pending = sim.pending
        if pending and total < self.max_nodes:
            needed = self._machines_needed(pending, ready, booting, tuple(sim.cluster.new_node_shape))
            if needed > 0:
                actions.append(("add", min(needed, self.max_nodes - total)))

        # --- scale down: nothing waiting, and a node has been empty for a while.
        # Only machines of the autoscaler's own shape may be removed: it can always add one back. A machine of
        # another shape (say the one big machine you started with) is pinned, because removing it could leave a
        # big job with nowhere to run, ever.
        elif not pending and total > self.min_nodes:
            removable = [n for n in ready if (n.cpu, n.mem) == tuple(sim.cluster.new_node_shape)]
            for node in sorted(removable, key=lambda n: -n.id):
                if not node.running and now - node.idle_since >= self.idle_ticks:
                    actions.append(("remove", node.id))
                    break
        return actions
