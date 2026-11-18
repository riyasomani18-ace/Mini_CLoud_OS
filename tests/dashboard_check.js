/* Loads a generated dashboard page in a fake browser, drives every control, and prints a JSON report.
   Usage: node tests/dashboard_check.js dashboard.html          (exit code 1 if anything is wrong)
   The fake browser has no layout engine: it checks that the page's code runs and that its logic is right,
   not how it looks on screen. */
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
function grab(id) {
  const m = html.match(new RegExp('<script[^>]*id="' + id + '"[^>]*>([\\s\\S]*?)</script>'));
  if (!m) throw new Error('page has no <script id="' + id + '">');
  return m[1];
}
const dataText = grab('mc-data'), coreCode = grab('mc-core'), uiCode = grab('mc-ui');

let drawCalls = 0, timerFn = null;
const ctx2d = new Proxy({}, { get: (t, k) => (...a) => { drawCalls++; }, set: () => true });
const elements = {};
function El(id) {
  this.id = id; this.style = {}; this.textContent = ''; this.value = ''; this.max = '100'; this.dataset = {};
  this.listeners = {}; this.width = 900; this.height = 300; this._html = '';
  Object.defineProperty(this, 'innerHTML', { get() { return this._html; }, set(v) { this._html = v; } });
}
El.prototype.addEventListener = function (ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); };
El.prototype.getContext = function () { return ctx2d; };
El.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, width: 900, height: 300 }; };
El.prototype.fire = function (ev, extra) {
  (this.listeners[ev] || []).forEach(fn => fn(Object.assign({ target: this, clientX: 0 }, extra || {})));
};
const document = {
  documentElement: {},
  getElementById(id) {
    if (id === 'mc-data') return { textContent: dataText };
    return elements[id] || (elements[id] = new El(id));
  },
};
const sandbox = {
  document, console,
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  setInterval: (fn) => { timerFn = fn; return 1; },
  clearInterval: () => { timerFn = null; },
};
vm.createContext(sandbox);
vm.runInContext(coreCode, sandbox);
vm.runInContext(uiCode, sandbox);

const MC = sandbox.MC, STATE = sandbox.MC_STATE, E = id => document.getElementById(id);
const data = JSON.parse(dataText);
const checks = [];
function check(name, ok, detail) { checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) }); }

// --- the page drew itself
check('machines are listed', /Machine 0/.test(E('machines').innerHTML));
check('table lists every scheduler', data.runs.every(r => E('table').innerHTML.indexOf('>' + r.name + '<') >= 0));
check('the best value in a column is highlighted', /class="best"/.test(E('table').innerHTML));
check('the charts drew something', drawCalls > 20, drawCalls);
check('the scheduler menu has every scheduler', (E('scheduler').innerHTML.match(/<option/g) || []).length === data.runs.length);
check('the legend shows every tenant', data.tenants.every(t => E('legend').innerHTML.indexOf(t) >= 0));

// --- drive the controls
E('slider').value = '40'; E('slider').fire('input');
check('moving the slider changes the tick', STATE.t === Math.min(40, data.runs[0].ticks) && /tick \d+ of \d+/.test(E('tick-label').textContent), E('tick-label').textContent);
const last = data.runs.length - 1;
E('scheduler').value = String(last); E('scheduler').fire('change');
check('choosing a scheduler switches the replay', E('scheduler-name').textContent === data.runs[last].name, E('scheduler-name').textContent);
E('table').fire('click', { target: { closest: () => ({ dataset: { run: '1' } }) } });
check('clicking a table row selects that scheduler', STATE.run === 1 && E('scheduler').value === '1');
E('slider').value = String(Math.floor(data.runs[1].ticks / 2)); E('slider').fire('input');   // start from the middle
const before = STATE.t;
E('step-forward').fire('click');
check('step forward moves one tick', STATE.t === before + 1, STATE.t + ' after ' + before);
E('step-back').fire('click');
check('step back moves one tick', STATE.t === before, STATE.t + ' vs ' + before);
E('slider').value = '0'; E('slider').fire('input');
E('step-back').fire('click');
check('step back stops at tick 0', STATE.t === 0);
E('speed').value = '20'; E('speed').fire('change');
E('play').fire('click');
check('play starts a timer', STATE.playing && typeof timerFn === 'function');
const t0 = STATE.t; timerFn();
check('the timer advances time by the chosen speed', STATE.t === Math.min(t0 + 20, data.runs[1].ticks), STATE.t);
E('play').fire('click');
check('pause stops it', !STATE.playing && timerFn === null);
E('series').fire('click', { clientX: 46 + (900 - 92) / 2 });
const mid = Math.round(data.runs[1].ticks / 2);
check('clicking the load chart jumps to that time', Math.abs(STATE.t - mid) <= 2, STATE.t + ' vs ' + mid);
E('metric').value = 'makespan'; E('metric').fire('change');
check('choosing a metric redraws the bar chart', /makespan/.test(E('bars-title').textContent), E('bars-title').textContent);
E('slider').value = String(data.runs[1].ticks); E('slider').fire('input');
check('the last tick shows an empty queue', /nobody is waiting|Waiting: 0/.test(E('queue').innerHTML + E('queue-title').textContent));

// --- logic samples, to be cross-checked against the simulator's own records
const samples = [];
data.runs.forEach((run, ri) => {
  const step = Math.max(1, Math.floor(run.ticks / 30));
  for (let t = 0; t <= run.ticks; t += step) {
    const running = MC.runningAt(run, t), nodes = MC.nodeStatesAt(run, t);
    let busy = 0; Object.keys(running).forEach(n => { busy += running[n].length; });
    samples.push({ run: run.name, t: t, waiting: MC.waitingAt(run, t).length, running: busy,
                   used: MC.usedCoresAt(run, t),
                   sp: MC.seriesAt(run, t).pending, su: MC.seriesAt(run, t).used,
                   stride: run.stride,
                   ready: nodes.filter(n => n.state === 'ready').length,
                   booting: nodes.filter(n => n.state === 'booting').length });
  }
});

// --- the clock never ends up past the end of a shorter run
let longest = 0, shortest = 0;
data.runs.forEach((r, i) => { if (r.ticks > data.runs[longest].ticks) longest = i; if (r.ticks < data.runs[shortest].ticks) shortest = i; });
E('scheduler').value = String(longest); E('scheduler').fire('change');
E('slider').value = String(data.runs[longest].ticks); E('slider').fire('input');
E('scheduler').value = String(shortest); E('scheduler').fire('change');
check('switching to a shorter run keeps the clock inside it', STATE.t <= data.runs[shortest].ticks, STATE.t + ' > ' + data.runs[shortest].ticks);

// --- which row is best in each column
const fakeRuns = [{ metrics: { a: 3, b: 1, c: 9 } }, { metrics: { a: 1, b: 5, c: 9 } }, { metrics: { a: 2, b: 2, c: 0 } }];
const picked = MC.bestPerColumn(fakeRuns, [{ key: 'a', better: 'low' }, { key: 'b', better: 'high' }, { key: 'c', better: null }]);
check('best value: lowest for "low", highest for "high", none otherwise', JSON.stringify(picked) === '[1,1,-1]', JSON.stringify(picked));

// --- the chart series follows the time (every run, a few ticks)
let seriesOk = true;
data.runs.forEach(run => {
  [0, Math.floor(run.ticks / 3), Math.floor(run.ticks / 2), run.ticks - 1].forEach(t => {
    const i = Math.min(Math.floor(t / run.stride), run.series.pending.length - 1), s = MC.seriesAt(run, t);
    if (s.pending !== run.series.pending[i] || s.used !== run.series.used_cpu[i] || s.cap !== run.series.cap_cpu[i]) seriesOk = false;
  });
});
check('seriesAt reads the right point for each tick', seriesOk);

// --- small pure functions
check('niceMax rounds up', MC.niceMax(0) === 1 && MC.niceMax(7) === 10 && MC.niceMax(130) === 200 && MC.niceMax(2) === 2);
check('format handles each kind', MC.format(0.5, 'pct') === '50%' && MC.format(2.345, 'f2') === '2.35' && MC.format(3.14, 'f1') === '3.1' && MC.format(7.4, 'int') === '7');

const ok = checks.every(c => c.ok);
console.log(JSON.stringify({ ok: ok, checks: checks, samples: samples }));
process.exit(ok ? 0 : 1);
