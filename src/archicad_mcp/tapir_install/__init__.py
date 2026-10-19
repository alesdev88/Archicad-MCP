"""Install the Tapir add-on into Archicad's Add-Ons folder.

Knows nothing about the MCP server: the CLI (archicad-mcp install-tapir), the
install_tapir tool and the missing-Tapir messages all build on it.
"""
from archicad_mcp.tapir_install.release import InstallError

__all__ = ["InstallError"]
