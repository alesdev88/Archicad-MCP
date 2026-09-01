"""Build a self-contained .mcpb bundle: interpreter, dependencies, server.

The bundle this produces has no prerequisites on the target machine. Not uv,
not Python, not network access at first launch. That is the whole point of it.
The previous bundle declared server type "uv" and spawned a bare `uv`, which
Claude Desktop does not ship, so every machine needed `winget install
astral-sh.uv` before the extension could start. On a managed office fleet that
is a per-machine visit, which is exactly the friction a one-click extension is
supposed to remove.

What goes in:

  runtime/    a relocatable CPython from astral-sh/python-build-standalone,
              with every dependency installed into its own site-packages, so
              the interpreter can run with -I (isolated) and nothing on the
              host can reach into it: no PYTHONPATH, no user site directory,
              no ambient virtualenv.
  manifest.json  this repository's manifest with the server block rewritten to
              point at that interpreter, and platforms narrowed to the one
              target this bundle was built for.

One bundle per platform, not one bundle for all of them. The dependency tree
includes compiled extensions (pydantic-core, cryptography, cffi, watchfiles,
rpds), so a single cross-platform tree is impossible, and shipping every
platform in one file would triple the download for no one's benefit.

Cross-building works and is the expected use. `uv pip install
--python-platform` resolves wheels for a foreign target, so the Windows bundle
builds on macOS with correct cp312-win_amd64 binaries inside it. That keeps the
release a single job rather than a runner matrix.

Usage:
    uv run python scripts/build_bundle.py --target win32
    uv run python scripts/build_bundle.py --target darwin-arm64
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Pinned, both of them, and for the same reason the mcpb packer is pinned in
# release.yml: these two decide what the shipped interpreter actually is. A
# floating "latest" would mean two releases built a week apart contain
# different Pythons, with nothing in the repository recording that they do.
PBS_TAG = "20260825"
PYTHON_VERSION = "3.12.14"

# install_only_stripped rather than install_only: same interpreter, debug
# symbols removed. It is the difference between a 21 MB and a 60 MB download
# for a bundle that never gets debugged on the user's machine anyway.
PBS_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "{tag}/cpython-{ver}+{tag}-{triple}-install_only_stripped.tar.gz"
)

# Everything that differs between targets, in one table, so adding a platform
# is adding a row rather than finding the four places that branch on sys.platform.
#
# "uv_platform" is what uv resolves wheels for. "mcpb_platform" is what Claude
# Desktop matches against when it decides whether this bundle can be installed.
# They are different vocabularies for the same thing and neither derives from
# the other, so both are written out.
TARGETS = {
    "win32": {
        "triple": "x86_64-pc-windows-msvc",
        "uv_platform": "x86_64-pc-windows-msvc",
        "mcpb_platform": "win32",
        # python-build-standalone lays Windows out the way python.org does:
        # interpreter at the root, packages under Lib. Nothing to configure,
        # but nothing shared with the Unix layout either.
        "interpreter": "runtime/python.exe",
        "site_packages": "runtime/Lib/site-packages",
        "sep": "\\",
    },
    "darwin-arm64": {
        "triple": "aarch64-apple-darwin",
        "uv_platform": "aarch64-apple-darwin",
        "mcpb_platform": "darwin",
        "interpreter": "runtime/bin/python3",
        "site_packages": f"runtime/lib/python{PYTHON_VERSION.rsplit('.', 1)[0]}/site-packages",
        "sep": "/",
    },
}

# No darwin-x86_64 row, and it is not an oversight. cryptography, which the
# dependency tree pulls in through fastmcp's auth support, stopped publishing
# macOS x86_64 wheels: 49.0.0 ships macosx_11_0_arm64 and nothing else. Without
# a wheel the only way to produce that bundle is to compile it, and compiling
# here would produce arm64 binaries inside a bundle labelled x86_64, which is
# worse than not shipping it. Intel Macs use the manual uv install in README
# instead, where the build happens on the machine that will run it.


def run(cmd: list[str], **kw) -> None:
    """Run a command, echo it first, and fail the build if it fails.

    Echoed because half of what this script does is delegate to uv and npx, and
    when a build breaks the useful question is which of those calls broke and
    with which arguments.
    """
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def fetch_runtime(target: dict, cache: Path) -> Path:
    """Download the standalone interpreter for `target`, cached by filename.

    The cache is keyed on the archive name, which already contains the pinned
    tag, version and triple, so a cache hit cannot be a hit on a different
    interpreter than the one asked for.
    """
    url = PBS_URL.format(tag=PBS_TAG, ver=PYTHON_VERSION, triple=target["triple"])
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / url.rsplit("/", 1)[-1]
    if archive.exists():
        print(f"  cached {archive.name}")
        return archive
    print(f"  downloading {archive.name}")
    # urlretrieve rather than a streaming copy: this is a release-time build
    # step fetching one pinned artifact over HTTPS from a known URL, and the
    # extra control a manual read loop would buy is control nothing here uses.
    urllib.request.urlretrieve(url, archive)
    return archive


def build(target_name: str, out_dir: Path, cache: Path, keep_staging: bool) -> Path:
    target = TARGETS[target_name]
    manifest = json.loads((REPO / "manifest.json").read_text())
    version = manifest["version"]

    staging = REPO / "build" / f"{target_name}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print(f"[1/6] interpreter  CPython {PYTHON_VERSION} for {target['triple']}")
    archive = fetch_runtime(target, cache)
    with tarfile.open(archive) as tf:
        # Every archive from python-build-standalone unpacks into a single
        # top-level "python" directory. Extract, then rename, so the bundle
        # says what the directory is for rather than what it happens to hold.
        tf.extractall(staging, filter="data")
    (staging / "python").rename(staging / "runtime")

    print("[2/6] wheel        building the project wheel")
    wheel_dir = staging / "_wheel"
    run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir)], cwd=REPO)
    wheel = next(wheel_dir.glob("*.whl"))

    print("[3/6] lockfile     exporting runtime dependencies")
    reqs = staging / "_requirements.txt"
    # --no-dev keeps pytest out of a bundle nobody runs tests in.
    # --no-emit-project because uv would write an editable reference to this
    # checkout, which is meaningless on the machine the bundle lands on. The
    # project goes in as a built wheel in the next step instead.
    run(["uv", "export", "--no-dev", "--no-hashes", "--no-emit-project",
         "--format", "requirements-txt", "-o", str(reqs)], cwd=REPO)

    site = staging / target["site_packages"]
    print(f"[4/6] dependencies installing into {target['site_packages']}")
    # Into the interpreter's own site-packages, not a separate lib/ reached
    # through PYTHONPATH. That is what lets the launch command use -I: an
    # isolated interpreter ignores PYTHONPATH, so anything placed outside its
    # own tree would be invisible to it.
    base = ["uv", "pip", "install", "--quiet",
            "--python-platform", target["uv_platform"],
            "--python-version", PYTHON_VERSION.rsplit(".", 1)[0],
            # Wheels only, never a source build. This is a correctness guard
            # before it is a speed one: a source build runs on THIS machine and
            # produces THIS machine's binaries, which would then ship inside a
            # bundle labelled for a different platform and fail at import on
            # the user's machine with nothing to explain why. Refusing the
            # build surfaces a missing wheel here, as a missing wheel.
            "--only-binary", ":all:",
            "--target", str(site)]
    run([*base, "--requirement", str(reqs)], cwd=REPO)
    # --no-deps: the lockfile above already pinned the entire tree. Letting the
    # wheel re-resolve its own dependencies here would let a second, unlocked
    # resolution quietly overwrite what the lock just decided.
    run([*base, "--no-deps", str(wheel)], cwd=REPO)

    # Compiled for whichever interpreter uv happened to run under, never read
    # by the bundled one, and several megabytes of it.
    for pycache in site.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)

    print("[5/6] manifest     rewriting the server block")
    sep = target["sep"]
    interpreter = "${__dirname}" + sep + target["interpreter"].replace("/", sep)
    manifest["server"] = {
        "type": "python",
        "entry_point": f"{target['site_packages']}/archicad_mcp/server.py",
        "mcp_config": {
            "command": interpreter,
            # -I is isolated mode: no user site directory, no PYTHONPATH, no
            # PYTHONHOME. It cannot pick up a stray Archicad or Python install
            # on the host, which is the failure this bundle exists to prevent.
            # It does not touch os.environ, so the user_config values below
            # still arrive.
            "args": ["-I", "-m", "archicad_mcp.server"],
            "env": manifest["server"]["mcp_config"]["env"],
        },
    }
    # Narrowed to the platform actually inside this file. A bundle carrying
    # win_amd64 .pyd files must not offer to install on a Mac, and the
    # manifest is the only thing Claude Desktop checks before unpacking.
    manifest["compatibility"]["platforms"] = [target["mcpb_platform"]]
    # The interpreter ships in the bundle, so this stops being a requirement on
    # the user and starts being a false claim about one.
    manifest["compatibility"].pop("runtimes", None)
    # _meta explains that the committed server block is a template and describes
    # the darwin shape. Both statements are about the repository, and inside a
    # built bundle the second one is simply wrong, so it does not travel.
    manifest.pop("_meta", None)
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy(REPO / "icon.png", staging / "icon.png")

    # Build inputs, not bundle contents. They live in the staging directory
    # because that is what gets deleted; leaving them would ship the lockfile
    # export and a copy of the wheel inside every download.
    shutil.rmtree(wheel_dir)
    reqs.unlink()

    print("[6/6] pack")
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"archicad-mcp-{version}-{target_name}.mcpb"
    run(["npx", "--yes", "@anthropic-ai/mcpb@2.1.2", "validate",
         str(staging / "manifest.json")])
    run(["npx", "--yes", "@anthropic-ai/mcpb@2.1.2", "pack",
         str(staging), str(bundle)])

    # Only when the bundle targets the machine doing the building. It is a
    # cheap check that the tree actually imports, and it is the only check
    # available here: a Windows bundle cross-built on macOS cannot be run
    # until it reaches Windows, which is what the release checklist is for.
    if target_name == host_target():
        print("verify        running the bundled interpreter")
        run([str(staging / target["interpreter"]), "-I", "-m",
             "archicad_mcp.server", "--help"], stdout=subprocess.DEVNULL)
        print("              imports clean, entry point reachable")
    else:
        print(f"verify        skipped, {target_name} is not this host")

    size = bundle.stat().st_size / 1_000_000
    print(f"\n{bundle.relative_to(REPO)}  {size:.1f} MB")
    if not keep_staging:
        shutil.rmtree(staging)
    return bundle


def host_target() -> str | None:
    """Which target, if any, this machine can actually launch."""
    import platform
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin-arm64" if platform.machine() == "arm64" else "darwin-x86_64"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", choices=[*TARGETS, "all"], required=True)
    ap.add_argument("--out-dir", type=Path, default=REPO / "dist")
    ap.add_argument("--cache", type=Path, default=REPO / "build" / "_runtimes")
    ap.add_argument("--keep-staging", action="store_true",
                    help="leave build/<target>/ in place for inspection")
    a = ap.parse_args()
    targets = list(TARGETS) if a.target == "all" else [a.target]
    for name in targets:
        print(f"\n=== {name} ===")
        build(name, a.out_dir, a.cache, a.keep_staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
