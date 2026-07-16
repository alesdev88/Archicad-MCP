from __future__ import annotations

from dataclasses import asdict, dataclass

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

_TAPIR_PROBE = {"addOnCommandId": {"commandNamespace": "TapirCommand",
                                   "commandName": "GetAddOnVersion"}}


class ArchicadUnavailableError(Exception):
    """str(exc) is a user-facing, actionable message."""


@dataclass
class InstanceInfo:
    port: int
    version: int
    build: int
    project_name: str | None
    tapir_available: bool
    tapir_version: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ArchicadConnection:
    def __init__(self, port: int, core=None):
        self.port = port
        self._core = core if core is not None else CoreCommands(Port(port))
        self._tapir_available: bool | None = None

    def official(self, command: str, parameters: dict | None = None) -> dict:
        return self._core.post_command(command, parameters)

    def tapir_available(self) -> bool:
        if self._tapir_available is None:
            try:
                response = self.official("API.IsAddOnCommandAvailable", _TAPIR_PROBE)
                self._tapir_available = bool(response.get("available"))
            except APIErrorBase:
                self._tapir_available = False
        return self._tapir_available

    def tapir(self, command: str, parameters: dict | None = None) -> dict:
        if not self.tapir_available():
            raise ArchicadUnavailableError(
                f"'{command}' requires the Tapir add-on, which is not installed in "
                f"the Archicad instance on port {self.port}. Install it from "
                "https://github.com/ENZYME-APD/tapir-archicad-automation/releases "
                "(Options > Add-On Manager), then retry."
            )
        return self._core.post_tapir_command(command, parameters)


def probe_port(port: int, core=None) -> InstanceInfo | None:
    conn = ArchicadConnection(port, core=core)
    try:
        product = conn.official("API.GetProductInfo")
    except (APIConnectionError, RequestError, CommandTimeoutError):
        return None
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
    return InstanceInfo(
        port=port,
        version=int(product.get("version", 0)),
        build=int(product.get("buildNumber", 0)),
        project_name=project_name,
        tapir_available=tapir,
        tapir_version=tapir_version,
    )


def discover_instances() -> list[InstanceInfo]:
    found = []
    for port in PORT_RANGE:
        info = probe_port(port)
        if info is not None:
            found.append(info)
    return found


def get_connection(port: int | None) -> ArchicadConnection:
    if port is not None:
        if probe_port(port) is None:
            raise ArchicadUnavailableError(
                f"No Archicad answering on port {port}. Is it running with a project open?")
        return ArchicadConnection(port)
    instances = discover_instances()
    if not instances:
        raise ArchicadUnavailableError(
            "No running Archicad found. Start Archicad 29 and open a project.")
    if len(instances) > 1:
        ports = ", ".join(str(i.port) for i in instances)
        raise ArchicadUnavailableError(
            f"Multiple Archicad instances running (ports {ports}). "
            "Pass the 'port' parameter to choose one; call list_instances to see them.")
    return ArchicadConnection(instances[0].port)
