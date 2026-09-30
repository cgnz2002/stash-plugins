#!/usr/bin/env python3
"""Run every test in this directory.

    python3 tests/run.py            # all of them
    python3 tests/run.py embed      # only those whose name contains "embed"

Each test is a standalone script that asserts its way to a final "ALL OK", run
in its own process so one failure can't poison another's imports. Standard
library only, like the plugin itself -- there is no pytest in the Stash
container and none is needed here.

They exercise the plugin's pure logic against temporary fixtures; nothing talks
to a running Stash, and the dry-run test asserts that no request ever leaves the
process.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    pattern = argv[0] if argv else ""
    names = sorted(
        n for n in os.listdir(HERE)
        if n.startswith("test_") and n.endswith(".py") and pattern in n
    )
    if not names:
        print("no tests match {!r}".format(pattern))
        return 1

    failed = []
    for name in names:
        # Don't leave __pycache__ beside the plugin source: build_site.sh zips
        # a plugin's whole directory, so bytecode written by a test run would
        # be packaged into the published plugin.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, name)],
            capture_output=True, text=True, env=env,
        )
        ok = proc.returncode == 0 and proc.stdout.strip().endswith("ALL OK")
        print("{:<28} {}".format(name, "ok" if ok else "FAIL"))
        if not ok:
            failed.append(name)
            out = (proc.stdout + proc.stderr).strip()
            print("\n".join("    " + line for line in out.splitlines()[-25:]))

    print("\n{}/{} passed".format(len(names) - len(failed), len(names)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
