# Development

```bash
uv sync && uv run pytest          # offline suite
```

To run unreleased `main` instead of a release:

```bash
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git
```

A local checkout installed with `uv tool install --force .` can come out as the
previous build when the version has not changed, because uv caches a folder by
its `pyproject.toml`. Use
`uv tool install --force --reinstall-package archicad-mcp-server .` instead. A
running server keeps the old code until its client restarts it.

## Live tests

Live tests need a running Archicad. Open a **small, non-sensitive** test model
and pin the port. Never run them against a client or Teamwork project, and read
the crash warning in [Known issues](known-issues.md) first:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

## Tapir

After a Tapir release, refresh the bundled command definitions from its tag:

```bash
uv run python scripts/sync_tapir_defs.py
```

To build against commands merged into Tapir since its last release, generate the
definitions from a Tapir checkout with Tapir's own generator. That records the
checkout's version as unreleased, and `check_release_version.py` refuses a
release until the definitions come from a release again:

```bash
uv run python scripts/sync_tapir_defs.py --from-checkout ~/Developer/tapir-main
```

Commands Tapir registers under an older version than the release that first
shipped them are corrected in `CORRECTED_VERSIONS` in
`src/archicad_mcp/gateway/registry.py`.

To move `install_tapir` to another Tapir release, pin it. This writes the sizes
and sha256 hashes GitHub publishes for every add-on asset. Review the diff and
check the new version live before releasing:

```bash
uv run python scripts/pin_tapir_release.py <tapir-version>
```

The [API dashboard](https://alesdev88.github.io/Archicad-MCP/api-dashboard.html)
is generated; refresh it after a definitions sync and the push publishes it:

```bash
uv run python scripts/build_dashboard.py
```

## Bundles

Build the Claude Desktop extensions, one per platform, both from one machine
(Node is needed for the `mcpb` packer):

```bash
uv run python scripts/build_bundle.py --target all
```

Each bundle gets a relocatable CPython from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
with every locked dependency in its own site-packages, so it starts with no uv,
no system Python and no network. The tree holds compiled wheels, so a bundle is
platform-specific, but the build is not: `uv pip install --python-platform`
resolves wheels for a named target. Source builds are refused, because one would
compile for this machine and land in a bundle labelled for another. That is also
why there is no Intel macOS bundle. The script self-tests a bundle built for the
machine building it, and says so when it cannot, which is every time it
cross-builds for Windows.

A cross-built Windows bundle cannot be run by the machine that built it, so
install it on Windows before trusting a release. The 0.2.1 bundle was checked
that way.

`icon.png` is drawn from `icon.svg`; change the SVG, then redraw the PNG:

```bash
uv run python scripts/make_icon.py
```

## Releasing

The version is written in four places (`pyproject.toml`, `manifest.json`,
`server.json`, and the download names in the README) and the test suite fails if
they drift:

```bash
uv run python scripts/check_release_version.py
```

Releasing is a tag push. `.github/workflows/publish.yml` refuses the tag unless
all four files and the tag agree and the Tapir definitions come from the pinned
release. Then it builds both bundles, the wheel and the sdist, installs that wheel
without `uv.lock` and starts it, attaches everything to a GitHub release,
publishes the wheel and sdist to PyPI as `archicad-mcp-server`, stamps each
bundle's SHA-256 into `server.json` and publishes that to the MCP registry. Run
the check by hand first: a pushed tag has to be deleted before it can be
corrected, and PyPI and the registry refuse a version they already hold.

```bash
uv run python scripts/check_release_version.py vX.Y.Z
git tag vX.Y.Z && git push origin vX.Y.Z
```
