from scripts.check_release_version import (
    disagreements,
    normalise_tag,
    read_versions,
    readme_version_refs,
)


def test_the_two_declared_versions_in_this_repo_agree():
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
