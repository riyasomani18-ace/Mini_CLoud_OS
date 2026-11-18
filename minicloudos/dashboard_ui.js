/* MiniCloudOS dashboard: drawing and controls. Uses the logic in dashboard_core.js (MC). */
(function () {
  'use strict';
  var MC = (typeof window !== 'undefined' ? window : globalThis).MC;
  var $ = function (id) { return document.getElementById(id); };
  var data = JSON.parse($('mc-data').textContent);
  var PALETTE = ['#2f6fdf', '#e07b00', '#1f9d6b', '#c43e7e', '#7a54c9', '#b5a100', '#2aa4b8', '#d1493f'];
  var state = { run: 0, t: 0, playing: false, speed: 5, metric: 'mean_slowdown', timer: null };
  data.runs.forEach(MC.indexRun);

  function run() { return data.runs[state.run]; }
  function colour(tenant) { return PALETTE[tenant % PALETTE.length]; }
  function css(name, fallback) {
    try { var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; }
    catch (e) { return fallback; }
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  /* ---------- comparison table ---------- */
  function renderTable() {
    var best = MC.bestPerColumn(data.runs, data.columns);
    var h = '<tr><th>scheduler</th>' + data.columns.map(function (c) { return '<th title="' + esc(c.help) + '">' + esc(c.label) + '</th>'; }).join('') + '</tr>';
    data.runs.forEach(function (r, i) {
      h += '<tr data-run="' + i + '" class="' + (i === state.run ? 'sel' : '') + '"><td class="name">' + esc(r.name) + '</td>' +
        data.columns.map(function (c, ci) {
          return '<td class="' + (best[ci] === i ? 'best' : '') + '">' + MC.format(r.metrics[c.key], c.fmt) + '</td>';
        }).join('') + '</tr>';
    });
    $('table').innerHTML = h;
  }

  /* ---------- machines ---------- */
  function renderMachines() {
    var r = run(), t = state.t, running = MC.runningAt(r, t), html = '';
    MC.nodeStatesAt(r, t).forEach(function (n) {
      var jobs = (running[n.id] || []).map(function (id) { return r._jobs[id]; });
      var used = jobs.reduce(function (a, j) { return a + j.cpu; }, 0);
      var pct = n.cpu ? Math.round(100 * used / n.cpu) : 0;
      var chips = jobs.slice(0, 14).map(function (j) {
        return '<span class="chip" style="background:' + colour(j.tenant) + '" title="job ' + j.id + ': ' + j.cpu + ' cores, ' + j.mem + ' GB">' +
          j.id + ' <small>' + j.cpu + 'c</small></span>';
      }).join('') + (jobs.length > 14 ? '<span class="more">+' + (jobs.length - 14) + ' more</span>' : '');
      html += '<div class="machine ' + n.state + '"><div class="mhead"><b>Machine ' + n.id + '</b> <span>' + n.cpu + ' cores / ' + n.mem + ' GB</span>' +
        '<span class="tag">' + n.state + '</span></div><div class="bar"><div class="fill" style="width:' + pct + '%"></div></div>' +
        '<div class="mfoot">' + used + ' of ' + n.cpu + ' cores busy (' + pct + '%)</div><div class="chips">' + (chips || '<span class="idle">idle</span>') + '</div></div>';
    });
    $('machines').innerHTML = html;
  }

  function renderQueue() {
    var r = run(), waiting = MC.waitingAt(r, state.t);
    var shown = waiting.slice(0, 18).map(function (id) {
      var j = r._jobs[id];
      return '<span class="chip" style="background:' + colour(j.tenant) + '" title="submitted at tick ' + j.arrival + '">' + id + ' <small>' + j.cpu + 'c/' + j.mem + 'G</small></span>';
    }).join('');
    $('queue-title').textContent = 'Waiting: ' + waiting.length + ' job' + (waiting.length === 1 ? '' : 's');
    $('queue').innerHTML = shown + (waiting.length > 18 ? '<span class="more">+' + (waiting.length - 18) + ' more</span>' : '') || '<span class="idle">nobody is waiting</span>';
  }

  /* ---------- charts ---------- */
  function setup(canvas) {
    var ctx = canvas.getContext('2d');
    return { ctx: ctx, w: canvas.width, h: canvas.height };
  }

  function drawSeries() {
    var c = $('series'), g = setup(c), ctx = g.ctx, r = run();
    var pad = { l: 46, r: 46, t: 16, b: 26 }, W = g.w - pad.l - pad.r, H = g.h - pad.t - pad.b;
    ctx.clearRect(0, 0, g.w, g.h);
    var n = r.series.pending.length, maxCores = 1, maxQ = 1, i;
    for (i = 0; i < n; i++) { maxCores = Math.max(maxCores, r.series.cap_cpu[i]); maxQ = Math.max(maxQ, r.series.pending[i]); }
    maxCores = MC.niceMax(maxCores); maxQ = MC.niceMax(maxQ);
    var x = function (k) { return pad.l + W * k / Math.max(1, n - 1); };
    var yc = function (v) { return pad.t + H * (1 - v / maxCores); };
    var yq = function (v) { return pad.t + H * (1 - v / maxQ); };
    ctx.strokeStyle = css('--grid', '#8884'); ctx.fillStyle = css('--muted', '#777'); ctx.font = '12px sans-serif';
    for (i = 0; i <= 4; i++) {
      var gy = pad.t + H * i / 4;
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(pad.l + W, gy); ctx.stroke();
      ctx.fillText(String(Math.round(maxCores * (1 - i / 4))), 6, gy + 4);
      ctx.fillText(String(Math.round(maxQ * (1 - i / 4))), pad.l + W + 6, gy + 4);
    }
    ctx.fillText('cores', 6, pad.t - 4); ctx.fillText('waiting jobs', pad.l + W - 56, pad.t - 4);
    ctx.fillText('tick', pad.l + W / 2 - 10, g.h - 6);
    // cluster capacity (outline), cores in use (filled), waiting jobs (line)
    ctx.strokeStyle = css('--muted', '#888'); ctx.beginPath();
    for (i = 0; i < n; i++) { (i ? ctx.lineTo : ctx.moveTo).call(ctx, x(i), yc(r.series.cap_cpu[i])); } ctx.stroke();
    ctx.fillStyle = css('--accent', '#2f6fdf') + '99'; ctx.beginPath(); ctx.moveTo(x(0), yc(0));
    for (i = 0; i < n; i++) ctx.lineTo(x(i), yc(r.series.used_cpu[i]));
    ctx.lineTo(x(n - 1), yc(0)); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = css('--warn', '#d1493f'); ctx.lineWidth = 2; ctx.beginPath();
    for (i = 0; i < n; i++) { (i ? ctx.lineTo : ctx.moveTo).call(ctx, x(i), yq(r.series.pending[i])); } ctx.stroke(); ctx.lineWidth = 1;
    var cx = x(Math.min(n - 1, Math.floor(state.t / r.stride)));        // the cursor
    ctx.strokeStyle = css('--text', '#000'); ctx.beginPath(); ctx.moveTo(cx, pad.t); ctx.lineTo(cx, pad.t + H); ctx.stroke();
  }

  function drawBars() {
    var c = $('bars'), g = setup(c), ctx = g.ctx, col = data.columns.filter(function (k) { return k.key === state.metric; })[0];
    var pad = { l: 12, r: 12, t: 22, b: 30 }, W = g.w - pad.l - pad.r, H = g.h - pad.t - pad.b;
    ctx.clearRect(0, 0, g.w, g.h);
    var vals = data.runs.map(function (r) { return r.metrics[state.metric]; }), top = MC.niceMax(Math.max.apply(null, vals) || 1);
    var bw = W / data.runs.length;
    ctx.font = '12px sans-serif';
    data.runs.forEach(function (r, i) {
      var h = H * vals[i] / top, bx = pad.l + i * bw + bw * 0.15;
      ctx.fillStyle = i === state.run ? css('--accent', '#2f6fdf') : css('--muted', '#999');
      ctx.fillRect(bx, pad.t + H - h, bw * 0.7, h);
      ctx.fillStyle = css('--text', '#000');
      ctx.fillText(r.name, bx, g.h - 10);
      ctx.fillText(MC.format(vals[i], col.fmt), bx, pad.t + H - h - 4);
    });
    $('bars-title').textContent = col.label + (col.better === 'low' ? ' (lower is better)' : col.better === 'high' ? ' (higher is better)' : '');
  }

  /* ---------- everything that depends on the tick or the chosen scheduler ---------- */
  function refresh() {
    var r = run();
    $('slider').max = r.ticks;
    $('slider').value = state.t;
    $('tick-label').textContent = 'tick ' + state.t + ' of ' + r.ticks;
    $('scheduler-name').textContent = r.name;
    renderMachines(); renderQueue(); drawSeries(); drawBars(); renderTable();
  }

  function setTick(t) { state.t = Math.max(0, Math.min(run().ticks, t)); refresh(); }
  function play() {
    state.playing = true; $('play').textContent = 'Pause';
    state.timer = setInterval(function () {
      if (state.t >= run().ticks) { pause(); return; }
      setTick(state.t + state.speed);
    }, 120);
  }
  function pause() { state.playing = false; $('play').textContent = 'Play'; if (state.timer) clearInterval(state.timer); state.timer = null; }

  function selectRun(i) { state.run = i; state.t = Math.min(state.t, run().ticks); refresh(); }

  /* ---------- wiring ---------- */
  $('title').textContent = data.title;
  $('subtitle').textContent = data.subtitle;
  $('scheduler').innerHTML = data.runs.map(function (r, i) { return '<option value="' + i + '">' + esc(r.name) + '</option>'; }).join('');
  $('metric').innerHTML = data.columns.map(function (c) { return '<option value="' + c.key + '">' + esc(c.label) + '</option>'; }).join('');
  $('metric').value = state.metric;
  $('legend').innerHTML = data.tenants.map(function (t, i) { return '<span class="chip" style="background:' + colour(i) + '">' + esc(t) + '</span>'; }).join('');
  $('scheduler').addEventListener('change', function () { selectRun(parseInt($('scheduler').value, 10)); });
  $('metric').addEventListener('change', function () { state.metric = $('metric').value; drawBars(); });
  $('slider').addEventListener('input', function () { setTick(parseInt($('slider').value, 10)); });
  $('play').addEventListener('click', function () { state.playing ? pause() : play(); });
  $('speed').addEventListener('change', function () { state.speed = parseInt($('speed').value, 10); });
  $('step-back').addEventListener('click', function () { setTick(state.t - 1); });
  $('step-forward').addEventListener('click', function () { setTick(state.t + 1); });
  $('table').addEventListener('click', function (e) {
    var tr = e.target && e.target.closest ? e.target.closest('tr') : null;
    if (tr && tr.dataset && tr.dataset.run !== undefined) { $('scheduler').value = tr.dataset.run; selectRun(parseInt(tr.dataset.run, 10)); }
  });
  $('series').addEventListener('click', function (e) {
    var rect = $('series').getBoundingClientRect(), r = run(), pad = 46;
    var frac = (e.clientX - rect.left - pad) / Math.max(1, rect.width - 2 * pad);
    setTick(Math.round(Math.max(0, Math.min(1, frac)) * r.ticks));
  });
  refresh();
  (typeof window !== 'undefined' ? window : globalThis).MC_STATE = state;     // handy for the console and for tests
})();
