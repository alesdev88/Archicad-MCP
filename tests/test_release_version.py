from scripts.check_release_version import (
    disagreements,
    normalise_tag,
    read_versions,
    readme_version_refs,
    server_json_versions,
)


def test_every_declared_version_in_this_repo_agrees():
    """The assertion that earns this file its place. The release workflow runs
    the same check against the tag, but only once the tag exists; here it runs
    on every push, so drift is caught while it is still a commit to amend
    rather than a tag to delete and push again."""
    assert disagreements(read_versions()) == []


def test_a_file_left_behind_by_a_version_bump_is_reported():
    problems = disagreements({"pyproject.toml": "0.2.0", "manifest.json": "0.1.0"})
    assert len(problems) == 2
    assert any("pyproject.toml" in line and "0.2.0" in line for line in problems)
    assert any("manifest.json" in line and "0.1.0" in line for line in problems)


def test_a_tag_that_disagrees_with_two_files_that_agree_is_reported():
    """The case the files alone cannot catch: both are 0.1.0 and consistent,
    and the tag says something else, which is how a release ends up named
    after a version that was never built."""
    versions = {"pyproject.toml": "0.1.0", "manifest.json": "0.1.0"}
    assert disagreements(versions, "v0.2.0")
    assert disagreements(versions, "v0.1.0") == []


def test_the_tag_is_read_through_either_of_the_forms_ci_hands_over():
    assert normalise_tag("v0.1.0") == "0.1.0"
    assert normalise_tag("0.1.0") == "0.1.0"
    assert normalise_tag("refs/tags/v0.1.0") == "0.1.0"


def test_only_the_leading_v_is_stripped():
    """A version is not required to start with a digit, and stripping every
    leading v would quietly turn a real mismatch into a pass."""
    assert normalise_tag("vv0.1.0") == "v0.1.0"


# ---------- README version references ----------

def test_the_readme_install_commands_are_found():
    text = """
    uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.3.0/archicad_mcp-0.3.0-py3-none-any.whl
    Download `archicad-mcp-0.3.0.mcpb` from the latest release.
    uv tool install git+https://github.com/alesdev88/Archicad-MCP.git@v0.3.0
    """
    assert {v for _, v in readme_version_refs(text)} == {"0.3.0"}
    # Four references: the tag in the URL, the wheel filename, the bundle
    # filename, and the pinned git tag.
    assert len(readme_version_refs(text)) == 4


def test_version_numbers_that_are_not_this_project_are_ignored():
    """The load-bearing test. The README is full of other version numbers, and
    a scanner that treated any of them as the project's own would block every
    release the moment Archicad or Tapir shipped an update."""
    text = """
    Requires **Archicad 29**, verified on Tapir 1.5.3 and build 4006.
    Needs Python 3.12+, fastmcp>=3.0, and mcpb 2.1.2.
    Ports 19723-19743. Version 0.1.0 of something unrelated.
    See docs/known-issues.md for Archicad 29.0 build 4006.
    """
    assert readme_version_refs(text) == []


def test_a_readme_left_behind_by_a_version_bump_is_reported():
    text = "curl -O https://github.com/alesdev88/Archicad-MCP/releases/download/v0.1.0/archicad_mcp-0.1.0-py3-none-any.whl"
    refs = readme_version_refs(text)
    assert {v for _, v in refs} == {"0.1.0"}
    # Which is what makes it a disagreement once the project moves on.
    versions = {"pyproject.toml": "0.2.0", "manifest.json": "0.2.0",
                "README.md line 1": "0.1.0"}
    problems = disagreements(versions)
    assert any("README.md" in line and "0.1.0" in line for line in problems)


def test_a_readme_reference_reports_its_line_number():
    text = "intro\nmore text\nsee archicad-mcp-0.9.9.mcpb here\n"
    assert readme_version_refs(text) == [(3, "0.9.9")]


def test_a_readme_with_no_references_is_not_a_failure():
    """A README that never names a version cannot drift, so it must not be
    turned into a phantom disagreement."""
    assert readme_version_refs("no versions of ours here at all") == []


def test_this_repo_readme_agrees_with_the_declared_version():
    versions = read_versions()
    readme_entries = {k: v for k, v in versions.items() if k.startswith("README.md")}
    assert readme_entries, "README version references should be picked up"
    assert set(readme_entries.values()) == {versions["pyproject.toml"]}


# ---------- server.json version references ----------

def _server_json(server_version: str, package_version: str, url_version: str) -> dict:
    """A server.json shaped like the real one, with each of its three kinds of
    version reference settable independently, which is the only way to write a
    test for one of them drifting on its own."""
    return {
        "version": server_version,
        "packages": [{
            "registryType": "mcpb",
            "version": package_version,
            "identifier": (
                "https://github.com/alesdev88/Archicad-MCP/releases/download/"
                f"v{url_version}/archicad-mcp-{url_version}.mcpb"
            ),
        }],
    }


def test_every_place_server_json_states_the_version_is_found():
    found = server_json_versions(_server_json("0.3.0", "0.3.0", "0.3.0"))
    assert set(found.values()) == {"0.3.0"}
    # Four references: the server's own version, the package's version, the tag
    # in the download URL, and the version inside the bundle's filename.
    assert len(found) == 4


def test_a_server_json_still_pointing_at_the_previous_release_is_reported():
    """The silent failure the whole check exists for. Both version fields were
    bumped and the download URL was not, so the registry keeps handing clients
    the old bundle from a URL that still resolves and never 404s to say so."""
    found = server_json_versions(_server_json("0.3.0", "0.3.0", "0.2.0"))
    problems = disagreements({"pyproject.toml": "0.3.0", **found})
    assert any("URL ref" in line and "0.2.0" in line for line in problems)


def test_the_two_versions_inside_one_url_are_reported_separately():
    """A URL whose tag and filename disagree is corrupt on its own terms, so
    the two have to be reported as two references rather than collapsed onto a
    single key where the second would overwrite the first."""
    data = {
        "version": "0.3.0",
        "packages": [{
            "version": "0.3.0",
            "identifier": (
                "https://github.com/alesdev88/Archicad-MCP/releases/download/"
                "v0.3.0/archicad-mcp-0.2.0.mcpb"
            ),
        }],
    }
    found = server_json_versions(data)
    assert sorted(found.values()) == ["0.2.0", "0.3.0", "0.3.0", "0.3.0"]
    assert disagreements(found)


def test_a_package_version_left_behind_is_reported():
    found = server_json_versions(_server_json("0.3.0", "0.2.0", "0.3.0"))
    problems = disagreements(found)
    assert any("packages[0] version" in line and "0.2.0" in line for line in problems)


def test_this_repo_server_json_agrees_with_the_declared_version():
    versions = read_versions()
    entries = {k: v for k, v in versions.items() if k.startswith("server.json")}
    assert entries, "server.json version references should be picked up"
    assert set(entries.values()) == {versions["pyproject.toml"]}


def test_server_json_description_fits_the_registry_limit():
    """The MCP registry caps description at 100 characters.

    It rejected a 127-character one with a 422 at the very last step of a
    release, after the GitHub release had already been created and could not be
    made again by re-running the job. The limit belongs to the registry, so
    nothing local enforced it and nothing could have: this test is the local
    copy of that rule.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    description = json.loads((root / "server.json").read_text())["description"]
    assert len(description) <= 100, (
        f"server.json description is {len(description)} characters; the registry "
        f"refuses anything over 100")
