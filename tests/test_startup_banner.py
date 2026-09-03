from archicad_mcp.connection import InstanceInfo
from archicad_mcp.server import (
    emit_startup_banner,
    format_startup_banner,
    resolve_mode,
    resolve_rules_dir,
)

WITH_TAPIR = InstanceInfo(port=19723, version=29, build=4006,
                          project_name="Test House", tapir_available=True,
                          tapir_version="1.5.3")
NO_TAPIR = InstanceInfo(port=19723, version=29, build=4006,
                        project_name=None, tapir_available=False,
                        tapir_version=None)
NO_PROJECT = InstanceInfo(port=19724, version=0, build=0, project_name=None,
                          tapir_available=False, tapir_version=None,
                          project_open=False)


def test_banner_reports_mode_and_rule_count():
    banner = format_startup_banner("full", 12, [WITH_TAPIR])
    assert "mode=full" in banner
    assert "12 rules" in banner


def test_resolve_rules_dir_treats_blank_as_unset():
    # Path("") is Path("."), which is truthy, so a blank env var silently
    # scanned the working directory and loaded zero rules instead of falling
    # back to the bundled examples. Caught end-to-end in an .mcpb bundle.
    assert resolve_rules_dir(None) is None
    assert resolve_rules_dir("") is None
    assert resolve_rules_dir("   ") is None


def test_resolve_rules_dir_passes_a_real_path_through(tmp_path):
    assert resolve_rules_dir(str(tmp_path)) == tmp_path


def test_resolve_mode_defaults_when_unset():
    assert resolve_mode(None) == "full"


def test_resolve_mode_treats_empty_as_unset():
    # An .mcpb bundle substitutes an unfilled user_config field as "". Letting
    # that reach argparse turns an optional setting into a startup crash.
    assert resolve_mode("") == "full"
    assert resolve_mode("   ") == "full"


def test_resolve_mode_passes_valid_values_through():
    assert resolve_mode("verdicts") == "verdicts"
    assert resolve_mode("full") == "full"


def test_banner_reports_rejected_rule_files():
    banner = format_startup_banner("full", 10, [WITH_TAPIR], rule_errors=2)
    assert "2 rule file(s) rejected" in banner
    assert "list_rules" in banner


def test_banner_stays_quiet_about_rule_errors_when_there_are_none():
    assert "rejected" not in format_startup_banner("full", 10, [WITH_TAPIR])


def test_banner_reports_port_version_project_and_tapir():
    banner = format_startup_banner("full", 3, [WITH_TAPIR])
    assert "19723" in banner
    assert "29" in banner and "4006" in banner
    assert "Test House" in banner
    assert "1.5.3" in banner


def test_banner_without_tapir_names_what_is_lost():
    banner = format_startup_banner("full", 3, [NO_TAPIR])
    assert "Tapir" in banner
    # Must not read as a fatal error: say which tools degrade.
    assert "not installed" in banner.lower()
    assert "1.5.3" not in banner


def test_banner_with_no_instances_is_actionable_and_not_fatal():
    banner = format_startup_banner("full", 3, [])
    assert "19723" in banner and "19743" in banner
    assert "Start Archicad" in banner
    # The server runs fine without Archicad; tools connect on demand. A banner
    # that reads as a startup failure sends people restarting a working server.
    assert "on demand" in banner


def test_banner_reports_an_instance_with_no_project_open():
    banner = format_startup_banner("full", 3, [NO_PROJECT])
    assert "19724" in banner
    assert "no project open" in banner.lower()
    # A modal dialog blocks the API the same way (live 2026-08-31), so the
    # banner must not flatly claim the project is missing.
    assert "modal dialog" in banner.lower()


def test_banner_lists_every_instance_when_several_are_running():
    second = InstanceInfo(port=19724, version=29, build=4006,
                          project_name="Other", tapir_available=True,
                          tapir_version="1.5.3")
    banner = format_startup_banner("full", 3, [WITH_TAPIR, second])
    assert "19723" in banner and "19724" in banner


def test_verdicts_mode_keeps_the_project_name_out_of_the_banner():
    banner = format_startup_banner("verdicts", 3, [WITH_TAPIR])
    assert "Test House" not in banner
    assert "19723" in banner


def test_emit_writes_to_stderr_never_stdout(capsys, monkeypatch):
    # stdout is the JSON-RPC channel under stdio transport. A single stray byte
    # there corrupts the stream and the client drops the server.
    monkeypatch.setattr("archicad_mcp.server.discover_instances",
                        lambda: [WITH_TAPIR])
    emit_startup_banner("full", 3)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "19723" in captured.err


def test_emit_survives_a_discovery_failure(capsys, monkeypatch):
    def boom():
        raise OSError("socket layer exploded")

    monkeypatch.setattr("archicad_mcp.server.discover_instances", boom)
    emit_startup_banner("full", 3)  # must not raise: the banner is diagnostics
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "mode=full" in captured.err


def test_banner_with_gdl_workspace_set(tmp_path):
    from pathlib import Path
    banner = format_startup_banner("full", 3, [WITH_TAPIR], gdl_workspace=tmp_path)
    assert "GDL workspace" in banner
    assert str(tmp_path) in banner


def test_banner_without_gdl_workspace_says_off():
    banner = format_startup_banner("full", 3, [WITH_TAPIR], gdl_workspace=None)
    assert "GDL tools off" in banner
    assert "no workspace folder set" in banner


def test_emit_with_discovery_failure_includes_gdl_workspace_status(capsys, monkeypatch, tmp_path):
    def boom():
        raise OSError("socket layer exploded")

    monkeypatch.setattr("archicad_mcp.server.discover_instances", boom)
    emit_startup_banner("full", 3, gdl_workspace=tmp_path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "GDL workspace" in captured.err
    assert str(tmp_path) in captured.err


def test_emit_with_discovery_failure_and_no_gdl_workspace(capsys, monkeypatch):
    def boom():
        raise OSError("socket layer exploded")

    monkeypatch.setattr("archicad_mcp.server.discover_instances", boom)
    emit_startup_banner("full", 3, gdl_workspace=None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "GDL tools off" in captured.err
    assert "no workspace folder set" in captured.err
