"""
Placement: WHICH machine does a job go to?  (The scheduler decides WHICH job goes next.)

These are the classic bin-packing heuristics. Each takes a job and the list of ready nodes and
returns the chosen node, or None if the job fits nowhere right now.
"""


def _leftover(node, job):
    """How much of the machine would stay free after placing the job (cores and memory, as fractions)."""
    return (node.free_cpu - job.cpu) / node.cpu + (node.free_mem - job.mem) / max(node.mem, 1)


def first_fit(job, nodes):
    """The first machine (lowest id) with room. Simple and fast."""
    for node in nodes:
        if node.fits(job):
            return node
    return None


def best_fit(job, nodes):
    """The machine that ends up with the LEAST space left over: packs tightly, keeps other machines empty."""
    feasible = [n for n in nodes if n.fits(job)]
    return min(feasible, key=lambda n: (_leftover(n, job), n.id)) if feasible else None


def worst_fit(job, nodes):
    """The machine that ends up with the MOST space left over: spreads the load out."""
    feasible = [n for n in nodes if n.fits(job)]
    return max(feasible, key=lambda n: (_leftover(n, job), -n.id)) if feasible else None


PLACEMENTS = {"first_fit": first_fit, "best_fit": best_fit, "worst_fit": worst_fit}
