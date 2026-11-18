"""
The desktop UI: a tiny local web server (standard library only) that serves the OS-style shell and runs
simulations on demand, so nothing is written to disk each time.

  python -m minicloudos ui            # opens http://127.0.0.1:8765
"""
import json
import os
import shlex
import subprocess
import sys
import threading
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .dashboard import build_data
from .workloads import WORKLOADS

HERE = Path(__file__).parent
ROOT = HERE.parent
DOCS = {"README": "README.md", "Architecture": "docs/ARCHITECTURE.md", "Results": "docs/RESULTS.md",
        "Changelog": "CHANGELOG.md", "Contributing": "CONTRIBUTING.md", "License": "LICENSE"}
CLI = {"run", "compare", "matrix", "quantum", "autoscale", "placement", "gantt"}
TASKS = {"tests": ([sys.executable, "-m", "unittest", "discover", "-v", "-s", "tests", "-t", "."], 180),
         "stress": ([sys.executable, "stress_test.py", "--cases", "300"], 180),
         "mutation": ([sys.executable, "mutation_check.py"], 900),
         "demo": ([sys.executable, "demo.py"], 120)}


@lru_cache(maxsize=24)
def _simulate(workload, seed, nodes, jobs, switch_cost, autoscale):
    data = build_data(workload=workload, seed=seed, nodes=nodes, jobs=jobs, switch_cost=switch_cost, autoscale=autoscale)
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def _params(query):
    """Validate the query string; raises ValueError with a readable message."""
    q = {k: v[0] for k, v in parse_qs(query).items()}

    def num(key, default, lo, hi):
        raw = q.get(key, "")
        if raw == "":
            return default
        value = int(raw)
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return value

    workload = q.get("workload", "convoy")
    if workload not in WORKLOADS:
        raise ValueError("unknown workload: " + workload)
    return (workload, num("seed", 1, 0, 10**6), num("nodes", 4, 1, 16), num("jobs", None, 5, 2000),
            num("switch_cost", 0, 0, 20), q.get("autoscale") == "1")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                page = (HERE / "os_ui.html").read_text(encoding="utf-8").replace("__WORKLOADS__", json.dumps(list(WORKLOADS))).replace("__DOCS__", json.dumps([k for k, v in DOCS.items() if (ROOT / v).exists()]))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/core.js":
                self._send(200, (HERE / "dashboard_core.js").read_bytes(), "application/javascript; charset=utf-8")
            elif url.path == "/api/run":
                self._send(200, _simulate(*_params(url.query)), "application/json")
            elif url.path == "/api/doc":
                name = parse_qs(url.query).get("name", [""])[0]
                if name not in DOCS or not (ROOT / DOCS[name]).exists():
                    raise ValueError("no such document")
                self._send(200, (ROOT / DOCS[name]).read_bytes(), "text/plain; charset=utf-8")
            elif url.path == "/api/exec":
                self._exec(parse_qs(url.query).get("line", [""])[0])
            else:
                self._send(404, b"not found", "text/plain")
        except ValueError as e:
            self._send(400, str(e).encode("utf-8"), "text/plain")

    def _exec(self, line):
        """Run one whitelisted command and stream its output to the browser as it is produced."""
        try:
            tokens = shlex.split(line)
        except ValueError:
            raise ValueError("could not parse that command")
        if not tokens:
            raise ValueError("empty command")
        if tokens[0] in TASKS and len(tokens) == 1:
            if not (ROOT / "tests").exists():
                raise ValueError("this needs the full project folder (tests/ not found)")
            argv, limit = TASKS[tokens[0]]
        elif tokens[0] in CLI:
            argv, limit = [sys.executable, "-m", "minicloudos", *tokens], 120
        else:
            raise ValueError(f"unknown command: {tokens[0]} (type help)")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        timer = threading.Timer(limit, proc.kill)
        timer.start()
        try:
            for out in proc.stdout:
                self.wfile.write(out.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            proc.kill()
        finally:
            timer.cancel()
            proc.wait()

    def log_message(self, *args):          # keep the terminal quiet
        pass


def serve(port=8765, open_browser=True):
    """Start the server (finds the next free port if needed) and block until Ctrl+C."""
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)      # localhost only
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"no free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"MiniCloudOS desktop running at {url}   (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0
