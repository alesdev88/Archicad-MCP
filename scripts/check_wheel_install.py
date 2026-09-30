"""Install the built wheel the way a user does, then start it.

The test suite runs against uv.lock, so it proves the locked dependency set
works. A user's `pip install archicad-mcp-server` or `uv tool install` never
sees that lock: it resolves every dependency afresh, to the newest release
pyproject.toml allows. On 2026-09-29 multiconn-archicad 0.9.0 dropped the
module connection.py imports, and every fresh install of 0.7.0 crashed at
start while the suite, the lock and the bundles (which ship the lock) all
stayed green. Nothing here would have noticed; this script does.

It takes the one wheel in dist/ (so it runs after `uv build`), installs it into
a throwaway virtual environment without the lock, and runs both entry points
and the server import. Standard library only, like check_release_version.py,
and it finds the wheel itself because a Windows runner's shell does not expand
dist/*.whl.

    uv run --no-project python scripts/check_wheel_install.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS = os.name == "nt"


def run(cmd: list[str]) -> str:
    print("+", " ".join(cmd), flush=True)
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        raise SystemExit(f"failed ({done.returncode}): {' '.join(cmd)}")
    return done.stdout


def main() -> None:
    wheels = sorted(Path("dist").glob("archicad_mcp_server-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one archicad_mcp_server wheel in dist/, found {len(wheels)}")
    wheel = wheels[0].resolve()
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        run(["uv", "venv", str(venv)])
        bindir = venv / ("Scripts" if WINDOWS else "bin")
        python = bindir / ("python.exe" if WINDOWS else "python")
        run(["uv", "pip", "install", "--python", str(python), str(wheel)])
        for tool in ("archicad-mcp", "archicad-gdl"):
            run([str(bindir / (tool + (".exe" if WINDOWS else ""))), "--help"])
        run([str(python), "-c", "import archicad_mcp.server, archicad_mcp.gdl.cli"])
        versions = run([str(python), "-c",
                        "import importlib.metadata as m; "
                        "print(m.version('archicad-mcp-server'), m.version('multiconn-archicad'))"])
    server, multiconn = versions.split()
    print(f"{wheel.name}: installs and starts without the lock "
          f"(archicad-mcp-server {server}, multiconn-archicad {multiconn})")


if __name__ == "__main__":
    main()
