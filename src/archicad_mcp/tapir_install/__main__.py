"""python -m archicad_mcp.tapir_install: the same as archicad-mcp install-tapir.

The .mcpb extension has no archicad-mcp command on PATH, so this is how its
bundled interpreter runs the installer from an administrator terminal.
"""
import sys

from archicad_mcp.tapir_install.cli import main

raise SystemExit(main(sys.argv[1:]))
