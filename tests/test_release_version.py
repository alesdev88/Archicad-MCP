from scripts.check_release_version import disagreements, normalise_tag, read_versions


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
