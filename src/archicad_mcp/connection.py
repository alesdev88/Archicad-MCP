from __future__ import annotations

from dataclasses import asdict, dataclass, field

from multiconn_archicad.basic_types import Port
from multiconn_archicad.core.core_commands import CoreCommands
from multiconn_archicad.errors import (
    APIConnectionError,
    APIErrorBase,
    CommandTimeoutError,
    RequestError,
    TapirCommandError,
)

PORT_RANGE = range(19723, 19744)

# Seconds a discovery probe may wait on one port. The desktop app gives the
# server only a few seconds to answer initialize, and discovery used to run
# before the transport with no timeout at all, so one Archicad that accepted
# TCP but did not answer (mid-launch, opening a big project) stalled startup
# past that deadline (seen live 2026-09-09: an idle Archicad 30 beside AC 29).
PROBE_TIMEOUT = 3.0

def _tapir_probe(command: str = "GetAddOnVersion") -> dict:
    return {"addOnCommandId": {"commandNamespace": "TapirCommand",
                               "commandName": command}}


_TAPIR_PROBE = _tapir_probe()


class ArchicadUnavailableError(Exception):
    """str(exc) is a user-facing, actionable message."""


# Live-verified: Archicad answers on its port but refuses even
# API.GetProductInfo with code 4001 ("Invalid program status") when no project
# is open. An open modal dialog (e.g. Object Settings) blocks the API the same
# way while a project IS open (seen live 2026-08-31, AC 29.0/5101), and
# community reports give that case the same code 4001, so a refused probe
# cannot be pinned on a missing project.
NO_OPEN_PROJECT_CODE = 4001


@dataclass
class InstanceInfo:
    port: int
    version: int
    build: int
    project_name: str | None
    tapir_available: bool
    tapir_version: str | None
    project_open: bool = True
    # The raw refusal ("code 4001: Invalid program status") when project_open
    # is False, so the real reason survives into errors and list_instances.
    status_error: str | None = None
    # How to install or update Tapir for this Archicad, when it needs that.
    tapir_hint: str | None = None
    # Gated tools whose Tapir command this add-on lacks (probed, not guessed
    # from the version number, so a custom build is judged by what it has).
    tools_needing_newer_tapir: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ArchicadConnection:
    def __init__(self, port: int, core=None, timeout: float | None = None):
        self.port = port
        self._core = core if core is not None else CoreCommands(Port(port))
        # None means "wait as long as Archicad takes": right for real commands
        # (a big publish can run minutes), wrong for probes, which set it.
        self.timeout = timeout
        self._tapir_available: bool | None = None
        self._command_availability: dict[str, bool] = {}
        self._archicad_version: int | None = None
        self._version_known = False

    def official(self, command: str, parameters: dict | None = None) -> dict:
        return self._core.post_command(command, parameters, timeout=self.timeout)

    def tapir_available(self) -> bool:
        """True when the Tapir add-on answers on this port.

        Only a command-level failure means "add-on absent". A transport failure
        means Archicad itself is gone (closed or crashed) and must surface as
        such. Reporting that as "Tapir not installed" sends people off
        reinstalling a working add-on.
        """
        if self._tapir_available is None:
            try:
                response = self.official("API.IsAddOnCommandAvailable", _TAPIR_PROBE)
                self._tapir_available = bool(response.get("available"))
            except (APIConnectionError, RequestError, CommandTimeoutError) as exc:
                raise ArchicadUnavailableError(
                    f"Archicad is not responding on port {self.port}. It may have "
                    "been closed or crashed. Restart it and reopen the project."
                ) from exc
            except APIErrorBase:
                self._tapir_available = False
        return self._tapir_available

    def tapir_command_available(self, command: str) -> bool:
        """True when THIS Tapir command is registered in the running add-on.

        Tapir gains commands over releases, so an installed add-on can be older
        than the bundled command definitions (e.g. 1.4.0 has no
        GetIFCPropertiesOfElements). Callers that can degrade should ask here
        rather than discover it via a 4010 error mid-fetch.
        """
        if not self.tapir_available():
            return False
        cached = self._command_availability.get(command)
        if cached is None:
            try:
                response = self.official("API.IsAddOnCommandAvailable",
                                         _tapir_probe(command))
                cached = bool(response.get("available"))
            except APIErrorBase:
                cached = False
            self._command_availability[command] = cached
        return cached

    def archicad_version(self) -> int | None:
        """The Archicad major version on this port, or None if it will not say."""
        if not self._version_known:
            try:
                version = int(self.official("API.GetProductInfo").get("version", 0))
                self._archicad_version = version or None
            except (APIErrorBase, ValueError, TypeError):
                self._archicad_version = None
            self._version_known = True
        return self._archicad_version

    def tapir(self, command: str, parameters: dict | None = None) -> dict:
        if not self.tapir_available():
            raise ArchicadUnavailableError(tapir_missing_message(self, f"'{command}'"))
        return self._core.post_tapir_command(command, parameters, timeout=self.timeout)


def tapir_missing_message(conn: ArchicadConnection, what: str) -> str:
    from archicad_mcp.tapir_install.hint import install_hint
    version = conn.archicad_version()
    where = (f"Archicad {version} on port {conn.port}" if version else
             f"the Archicad instance on port {conn.port}")
    return (f"{what} requires the Tapir add-on, which is not installed in {where}. "
            + install_hint(version))


def probe_port(port: int, core=None, timeout: float | None = PROBE_TIMEOUT,
               diagnose: bool = True) -> InstanceInfo | None:
    """The instance on port, or None. diagnose=False skips tapir_hint and
    tools_needing_newer_tapir: a tool call connecting needs neither, and the
    hint costs a scan of the Archicad folders."""
    conn = ArchicadConnection(port, core=core, timeout=timeout)
    try:
        product = conn.official("API.GetProductInfo")
    except (APIConnectionError, RequestError, CommandTimeoutError):
        return None
    except APIErrorBase as exc:
        # Archicad is there but refuses even GetProductInfo: either no project
        # is open, or a modal dialog is blocking the API while a project IS
        # open. Both refuse with code 4001, so keep the raw refusal instead of
        # guessing the cause. Report the instance rather than hiding it, and
        # never let one refusing instance break discovery of the others.
        return InstanceInfo(port=port, version=0, build=0, project_name=None,
                            tapir_available=False, tapir_version=None,
                            project_open=False,
                            status_error=f"code {exc.code}: {exc.message}")
    tapir = conn.tapir_available()
    project_name = None
    tapir_version = None
    if tapir:
        try:
            info = conn.tapir("GetProjectInfo")
            project_name = info.get("projectName")
            tapir_version = conn.tapir("GetAddOnVersion").get("version")
        except (APIErrorBase, TapirCommandError, ArchicadUnavailableError):
            pass
    version = int(product.get("version", 0))
    from archicad_mcp.tapir_install.hint import install_hint
    hint = None
    needing: list[str] = []
    if diagnose and not tapir:
        hint = install_hint(version or None)
    elif diagnose:
        from archicad_mcp.tapir_requirements import unavailable_tools
        try:
            needing = unavailable_tools(conn, tapir_version)
        except (APIErrorBase, ArchicadUnavailableError):
            needing = []
        if needing:
            hint = (f"Tapir {tapir_version or '(version unknown)'} here lacks commands "
                    f"that {', '.join(needing)} need. "
                    + install_hint(version or None, replace=True))
    return InstanceInfo(
        port=port,
        version=version,
        build=int(product.get("buildNumber", 0)),
        project_name=project_name,
        tapir_available=tapir,
        tapir_version=tapir_version,
        tapir_hint=hint,
        tools_needing_newer_tapir=needing,
    )


def discover_instances(diagnose: bool = True) -> list[InstanceInfo]:
    found = []
    for port in PORT_RANGE:
        info = probe_port(port, diagnose=diagnose)
        if info is not None:
            found.append(info)
    return found


def _require_open_project(info: InstanceInfo) -> None:
    # A refused probe has two indistinguishable causes (same error code 4001):
    # no open project, or a modal dialog blocking the API while a project is
    # open. Claiming just "no project is open" misdiagnoses the dialog case
    # (seen live 2026-08-31), so the message must name both.
    if not info.project_open:
        detail = f" ({info.status_error})" if info.status_error else ""
        raise ArchicadUnavailableError(
            f"Archicad is running on port {info.port} but refused the API "
            f"request{detail}. Either no project is open, or a modal dialog "
            "(e.g. Object Settings) is blocking the API even though a project "
            "is open. Close any open dialog in Archicad, or open a project, "
            "and retry.")


def get_connection(port: int | None) -> ArchicadConnection:
    if port is not None:
        info = probe_port(port, diagnose=False)
        if info is None:
            raise ArchicadUnavailableError(
                f"No Archicad answering on port {port}. Is it running with a project open?")
        _require_open_project(info)
        return ArchicadConnection(port)
    instances = discover_instances(diagnose=False)
    if not instances:
        raise ArchicadUnavailableError(
            "No running Archicad found. Start Archicad 29 and open a project.")
    # Prefer instances that actually have a project open; a project-less one
    # can't answer anything useful and shouldn't force a 'pick a port' error.
    with_project = [i for i in instances if i.project_open]
    if len(with_project) == 1:
        return ArchicadConnection(with_project[0].port)
    if not with_project:
        _require_open_project(instances[0])
    instances = with_project
    if len(instances) > 1:
        ports = ", ".join(str(i.port) for i in instances)
        raise ArchicadUnavailableError(
            f"Multiple Archicad instances running (ports {ports}). "
            "Pass the 'port' parameter to choose one; call list_instances to see them.")
    return ArchicadConnection(instances[0].port)
