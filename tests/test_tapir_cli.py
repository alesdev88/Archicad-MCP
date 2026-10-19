import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from archicad_mcp.tapir_install import cli
from archicad_mcp.tapir_install.detect import Installation
from archicad_mcp.tapir_install.release import Asset, InstallError, Release
from archicad_mcp.tapir_install.run import run
from tests.test_tapir_detect import fake_bundle
from tests.test_tapir_plan import fake_release, inst, release_zip

WIN_RELEASE_BYTES = {29: b"MZ release 29", 30: b"MZ release 30"}


def listing(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def serving(files: dict[str, bytes], fail: set[str] = frozenset()) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name in fail or name not in files:
            return httpx.Response(404)
        return httpx.Response(200, content=files[name])
    return httpx.Client(transport=httpx.MockTransport(handler))


def win_setup(tmp_path, existing: dict[int, bytes] | None = None):
    existing = existing or {}
    installs, assets, files = [], {}, {}
    for v, data in WIN_RELEASE_BYTES.items():
        folder = tmp_path / "GRAPHISOFT" / f"Archicad {v}"
        (folder / "Add-Ons").mkdir(parents=True)
        if v in existing:
            (folder / "Add-Ons" / f"TapirAddOn_AC{v}_Win.apx").write_bytes(existing[v])
        installs.append(Installation(v, folder, folder / "Add-Ons"))
        name = f"TapirAddOn_AC{v}_Win.apx"
        assets[name] = Asset(name, len(data), hashlib.sha256(data).hexdigest(),
                             f"https://x/download/1.7.1/{name}")
        files[name] = data
    return installs, Release("1.7.1", "1.7.1", assets, True), files


def win_run(tmp_path, installs, release, files, **kw):
    kw.setdefault("client", serving(files))
    return run(installations=installs, release=release, running={}, platform="win32",
               backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs", **kw)


def test_run_dry_run_downloads_checks_and_writes_nothing(tmp_path):
    zips = {v: release_zip(tmp_path / f"r{v}", v) for v in (29, 30)}
    i29, i30 = inst(tmp_path, 29), inst(tmp_path, 30)
    fake_bundle(i29.add_ons, "TapirAddOn_AC29_Mac.bundle", b"a fork")
    before = listing(tmp_path / "Applications")
    report = run(installations=[i29, i30], release=fake_release(zips), running={},
                 platform="darwin", dry_run=True, prefs_dir=tmp_path / "noprefs",
                 client=serving({p.name: p.read_bytes() for p in zips.values()}),
                 backups=tmp_path / "backups")
    assert listing(tmp_path / "Applications") == before
    assert not (tmp_path / "backups").exists()
    assert [(r.inst.version, r.action) for r in report.rows] == [(29, "skip"), (30, "install")]
    assert list(report.rows[0].identical.values()) == [False]
    assert report.outcomes == [] and report.errors == {}
    data = report.to_dict()
    json.dumps(data)
    assert data["tapir_version"] == "1.7.1" and data["dry_run"] is True
    assert data["installations"][1]["sha256_checked"] is True


def test_run_installs_missing_and_leaves_different_alone(tmp_path):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    report = win_run(tmp_path, installs, release, files, dry_run=False)
    assert [(o.version, o.error) for o in report.outcomes] == [(30, None)]
    assert (installs[1].add_ons / "Tapir" / "TapirAddOn_AC30_Win.apx").read_bytes() == b"MZ release 30"
    assert listing(installs[0].add_ons) == ["TapirAddOn_AC29_Win.apx"]


def test_run_one_failed_download_does_not_stop_the_others(tmp_path):
    installs, release, files = win_setup(tmp_path)
    report = win_run(tmp_path, installs, release, files, dry_run=False,
                     client=serving(files, fail={"TapirAddOn_AC29_Win.apx"}))
    assert 29 in report.errors and "404" in report.errors[29]
    assert [r.action for r in report.rows] == ["skip", "install"]
    assert [o.version for o in report.outcomes] == [30]


def test_run_checks_the_hash_before_installing(tmp_path):
    installs, release, files = win_setup(tmp_path)
    files = dict(files, **{"TapirAddOn_AC30_Win.apx": b"MZ evil 30!!"})
    report = win_run(tmp_path, installs, release, files, dry_run=False)
    assert "sha256" in report.errors[30]
    assert not (installs[1].add_ons / "Tapir").exists()


def test_from_file_needs_one_version_and_the_exact_name(tmp_path):
    installs, release, files = win_setup(tmp_path)
    wrong = tmp_path / "tapir.apx"
    wrong.write_bytes(files["TapirAddOn_AC29_Win.apx"])
    with pytest.raises(Exception, match="one --archicad"):
        win_run(tmp_path, installs, release, files, from_file=wrong)
    with pytest.raises(Exception, match="TapirAddOn_AC29_Win.apx"):
        win_run(tmp_path, installs, release, files, from_file=wrong, versions={29})
    right = tmp_path / "TapirAddOn_AC29_Win.apx"
    right.write_bytes(files["TapirAddOn_AC29_Win.apx"])
    report = win_run(tmp_path, installs, release, files, from_file=right, versions={29},
                     dry_run=False, client=serving({}))
    assert [(o.version, o.error) for o in report.outcomes] == [(29, None)]


def test_add_ons_folder_names_one_installation(tmp_path):
    installs, release, files = win_setup(tmp_path)
    folder = tmp_path / "Elsewhere" / "Add-Ons"
    folder.mkdir(parents=True)
    with pytest.raises(Exception, match="one --archicad"):
        win_run(tmp_path, None, release, files, add_ons_folder=folder)
    report = win_run(tmp_path, None, release, files, add_ons_folder=folder, versions={30})
    assert [(r.inst.version, r.inst.add_ons) for r in report.rows] == [(30, folder)]


def test_report_text_names_states_and_restart(tmp_path):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    report = win_run(tmp_path, installs, release, files, dry_run=False)
    text = cli.format_report(report)
    assert "Archicad 29" in text and "different Tapir present" in text
    assert "Restart Archicad 30 to load Tapir." in text


def _cli(tmp_path, argv, monkeypatch, *, tty: bool, answers=()):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    code = cli.main(argv, installations=installs, release=release, running={},
                    platform="win32", backups=tmp_path / "backups",
                    prefs_dir=tmp_path / "noprefs", client=serving(files))
    return code, installs


def test_cli_without_terminal_or_yes_writes_nothing(tmp_path, monkeypatch, capsys):
    code, installs = _cli(tmp_path, [], monkeypatch, tty=False)
    assert code == 2
    assert not (installs[1].add_ons / "Tapir").exists()
    assert "Pass --yes" in capsys.readouterr().out


def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    code, installs = _cli(tmp_path, ["--dry-run", "--yes"], monkeypatch, tty=True)
    assert code == 0 and not (installs[1].add_ons / "Tapir").exists()
    assert "Dry run" in capsys.readouterr().out


def test_cli_prompt_no_means_nothing(tmp_path, monkeypatch):
    code, installs = _cli(tmp_path, [], monkeypatch, tty=True, answers=["n"])
    assert code == 0 and not (installs[1].add_ons / "Tapir").exists()


def test_cli_prompt_yes_installs(tmp_path, monkeypatch):
    code, installs = _cli(tmp_path, [], monkeypatch, tty=True, answers=["y"])
    assert code == 0 and (installs[1].add_ons / "Tapir" / "TapirAddOn_AC30_Win.apx").exists()


def test_cli_replace_asks_per_installation(tmp_path, monkeypatch, capsys):
    code, installs = _cli(tmp_path, ["--replace"], monkeypatch, tty=True, answers=["y", "n"])
    # First prompt: install into 30 (yes); second: replace 29 (no).
    assert code == 0
    assert (installs[0].add_ons / "TapirAddOn_AC29_Win.apx").read_bytes() == b"MZ fork"
    assert (installs[1].add_ons / "Tapir").exists()


def test_cli_yes_never_implies_replace(tmp_path, monkeypatch):
    code, installs = _cli(tmp_path, ["--yes"], monkeypatch, tty=False)
    assert code == 0
    assert (installs[0].add_ons / "TapirAddOn_AC29_Win.apx").read_bytes() == b"MZ fork"


def test_server_main_dispatches_install_tapir(monkeypatch):
    import archicad_mcp.server as server_mod
    seen = {}
    monkeypatch.setattr(sys, "argv", ["archicad-mcp", "install-tapir", "--dry-run"])
    monkeypatch.setattr(cli, "main", lambda argv: seen.setdefault("argv", argv) and 7)
    monkeypatch.setattr(server_mod, "build_server",
                        lambda **kw: pytest.fail("the server must not be built"))
    with pytest.raises(SystemExit) as exc:
        server_mod.main()
    assert exc.value.code == 7 and seen["argv"] == ["--dry-run"]


def test_add_ons_folder_ignores_the_running_archicad(tmp_path):
    # The running Archicad 30 loads its Tapir from its own folder, not this one.
    installs, release, files = win_setup(tmp_path)
    folder = tmp_path / "Elsewhere" / "Add-Ons"
    folder.mkdir(parents=True)
    report = run(add_ons_folder=folder, versions={30}, release=release, running={30: "1.7.1"},
                 platform="win32", client=serving(files), prefs_dir=tmp_path / "noprefs",
                 backups=tmp_path / "backups")
    assert [(r.state, r.action) for r in report.rows] == [("no Tapir", "install")]


# ---------- final review fixes ----------

def test_cli_interactive_shows_every_row_and_the_check_failures(tmp_path, monkeypatch, capsys):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    code = cli.main([], installations=installs, release=release, running={}, platform="win32",
                    backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs",
                    client=serving(files))
    out = capsys.readouterr().out
    assert code == 0
    assert "Archicad 29" in out and "different Tapir present" in out


def test_cli_interactive_with_nothing_to_do_still_reports_hash_failures(tmp_path, monkeypatch, capsys):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork", 30: b"MZ fork"})
    files = dict(files, **{"TapirAddOn_AC30_Win.apx": b"<html>prox</>"})  # same size
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    code = cli.main([], installations=installs, release=release, running={}, platform="win32",
                    backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs",
                    client=serving(files))
    out = capsys.readouterr().out
    assert code == 1
    assert "No Archicad installation found" not in out
    assert "sha256" in out and "Archicad 30" in out


def test_cli_with_stdin_closed_writes_nothing(tmp_path, monkeypatch, capsys):
    installs, release, files = win_setup(tmp_path)
    monkeypatch.setattr(sys, "stdin", None)
    code = cli.main([], installations=installs, release=release, running={}, platform="win32",
                    backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs",
                    client=serving(files))
    assert code == 2 and not (installs[1].add_ons / "Tapir").exists()
    assert "Pass --yes" in capsys.readouterr().out


def test_cli_non_interactive_check_failure_exits_1(tmp_path, monkeypatch):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork", 30: b"MZ fork"})
    files = dict(files, **{"TapirAddOn_AC30_Win.apx": b"<html>proxy</>"})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = cli.main([], installations=installs, release=release, running={}, platform="win32",
                    backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs",
                    client=serving(files))
    assert code == 1


def test_from_file_that_does_not_exist_is_a_clean_error(tmp_path):
    installs, release, files = win_setup(tmp_path)
    with pytest.raises(InstallError, match="does not exist"):
        win_run(tmp_path, installs, release, files, from_file=tmp_path / "TapirAddOn_AC29_Win.apx",
                versions={29})


def test_add_ons_folder_must_exist(tmp_path):
    installs, release, files = win_setup(tmp_path)
    with pytest.raises(InstallError, match="not a folder"):
        win_run(tmp_path, None, release, files, add_ons_folder=tmp_path / "Typo" / "Add-Ons",
                versions={30})


def test_an_os_error_while_installing_one_row_does_not_lose_the_report(tmp_path, monkeypatch):
    installs, release, files = win_setup(tmp_path)
    import archicad_mcp.tapir_install.run as run_mod

    def stage(downloaded, row, work, platform=None):
        if row.inst.version == 29:
            raise OSError("no space left")
        return real_stage(downloaded, row, work, platform=platform)

    real_stage = run_mod.stage
    monkeypatch.setattr(run_mod, "stage", stage)
    report = win_run(tmp_path, installs, release, files, dry_run=False)
    assert [(o.version, bool(o.error)) for o in report.outcomes] == [(29, True), (30, False)]


def test_replace_prompt_names_the_backup_folder(tmp_path, monkeypatch):
    prompts = []
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "n")
    cli.main(["--replace"], installations=installs, release=release, running={},
             platform="win32", backups=tmp_path / "backups", prefs_dir=tmp_path / "noprefs",
             client=serving(files))
    assert any(str(tmp_path / "backups") in p for p in prompts)


def test_restore_hint_removes_the_new_copy_first(tmp_path):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    report = win_run(tmp_path, installs, release, files, dry_run=False, replace=True)
    (o29,) = [o for o in report.outcomes if o.version == 29]
    hint = run_mod_restore(o29)
    assert hint.index("remove") < hint.index("move")


def run_mod_restore(outcome):
    from archicad_mcp.tapir_install.run import restore_hint
    return restore_hint(outcome)


def test_admin_command_from_a_replace_run_keeps_replace(tmp_path, monkeypatch):
    installs, release, files = win_setup(tmp_path, existing={29: b"MZ fork"})
    monkeypatch.setattr("archicad_mcp.tapir_install.plan.is_writable", lambda p: False)
    report = win_run(tmp_path, installs, release, files, dry_run=False, replace=True,
                     versions={29})
    (o,) = report.outcomes
    assert "--archicad 29 --replace" in o.error
