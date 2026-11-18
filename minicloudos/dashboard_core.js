/* MiniCloudOS dashboard: the logic (no drawing). Pure functions over the data embedded in the page,
   so they can be tested in Node.js without a browser. */
(function (root) {
  'use strict';

  /* Add lookup tables to a run (idempotent). Jobs arrive as arrays:
     [id, arrival, duration, cpu, mem, tenantIndex, firstStart, finish]. */
  function indexRun(run) {
    if (run._jobs) return run;
    run._jobs = {};
    run.jobs.forEach(function (j) {
      run._jobs[j[0]] = { id: j[0], arrival: j[1], duration: j[2], cpu: j[3], mem: j[4],
                          tenant: j[5], first: j[6], finish: j[7] };
    });
    return run;
  }

  /* The machines that exist at tick t, with their state ('ready', 'booting', 'removed'). */
  function nodeStatesAt(run, t) {
    var nodes = {};
    for (var i = 0; i < run.node_log.length; i++) {
      var e = run.node_log[i];                    // [tick, id, cpu, mem, state]
      if (e[0] > t) break;
      nodes[e[1]] = { id: e[1], cpu: e[2], mem: e[3], state: e[4] };
    }
    return Object.keys(nodes).map(function (k) { return nodes[k]; })
                 .sort(function (a, b) { return a.id - b.id; });
  }

  /* {nodeId: [jobId, ...]} for the jobs running during tick t (that is, from t up to t+1). */
  function runningAt(run, t) {
    var by = {};
    for (var i = 0; i < run.segments.length; i++) {
      var s = run.segments[i];                    // [job, node, start, end]
      if (s[2] <= t && t < s[3]) { (by[s[1]] = by[s[1]] || []).push(s[0]); }
    }
    return by;
  }

  /* Ids of the jobs waiting during tick t: submitted, not finished, not running. Oldest first. */
  function waitingAt(run, t) {
    indexRun(run);
    var running = {};
    for (var i = 0; i < run.segments.length; i++) {
      var s = run.segments[i];
      if (s[2] <= t && t < s[3]) running[s[0]] = true;
    }
    var out = [];
    for (var id in run._jobs) {
      var j = run._jobs[id];
      if (j.arrival <= t && t < j.finish && !running[j.id]) out.push(j.id);
    }
    out.sort(function (a, b) { return run._jobs[a].arrival - run._jobs[b].arrival || a - b; });
    return out;
  }

  /* Cores in use across the cluster at tick t (from the running jobs). */
  function usedCoresAt(run, t) {
    indexRun(run);
    var by = runningAt(run, t), total = 0;
    Object.keys(by).forEach(function (n) {
      by[n].forEach(function (id) { total += run._jobs[id].cpu; });
    });
    return total;
  }

  /* The recorded per-tick series, sampled every `stride` ticks. */
  function seriesAt(run, t) {
    var i = Math.min(Math.floor(t / run.stride), run.series.pending.length - 1);
    return { pending: run.series.pending[i], used: run.series.used_cpu[i], cap: run.series.cap_cpu[i],
             ready: run.series.ready[i], booting: run.series.booting[i] };
  }

  /* For every column, the index of the best row (lowest or highest), or -1 if the column has no "better". */
  function bestPerColumn(runs, columns) {
    return columns.map(function (c) {
      if (!c.better) return -1;
      var best = 0;
      runs.forEach(function (r, i) {
        var v = r.metrics[c.key], b = runs[best].metrics[c.key];
        if (c.better === 'low' ? v < b : v > b) best = i;
      });
      return best;
    });
  }

  function format(value, kind) {
    if (kind === 'pct') return (value * 100).toFixed(0) + '%';
    if (kind === 'f2') return value.toFixed(2);
    if (kind === 'f1') return value.toFixed(1);
    return String(Math.round(value));
  }

  /* A round number at or above v, for the top of a chart axis. */
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    var step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * p;
  }

  var api = { indexRun: indexRun, nodeStatesAt: nodeStatesAt, runningAt: runningAt, waitingAt: waitingAt,
              usedCoresAt: usedCoresAt, seriesAt: seriesAt, bestPerColumn: bestPerColumn,
              format: format, niceMax: niceMax };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MC = api;
})(typeof window !== 'undefined' ? window : globalThis);
