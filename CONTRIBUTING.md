# Contributing

This is a small teaching and portfolio project, so the bar is simple: keep it readable and keep the claims honest.

1. Run the tests before and after your change: `python -m unittest discover -v -s tests -t .`
   (the dashboard tests need Node.js and are skipped without it).
2. Anything that changes how jobs are scheduled, placed or measured must also pass the stress test:
   `python stress_test.py --cases 2000`
3. Add a test for every bug you fix and every feature you add. Prefer small scenarios whose answers you can work out by
   hand and write down in a comment.
4. If you change a number in the README or `docs/RESULTS.md`, regenerate the table with the command shown above it and
   paste the real output. Never retype numbers.
5. Keep the package dependency-free (Python standard library only; the dashboard is plain HTML and JavaScript).

To add a scheduler: write a small class in `minicloudos/schedulers.py` (`order`, optionally `quantum` and hooks) and add it
to `SCHEDULERS`. The stress test and the all-combinations test will pick it up automatically.

Ideas: job dependencies, machine failures, heterogeneous machine speeds, a real-trace loader, a cgroups demo that limits
an actual process on Linux.
