#!/usr/bin/env python3
"""Build the Blender extension zip for drag-and-drop installation.

Produces dist/blender_mcp-<version>.zip containing the addon as an
extension package (blender_manifest.toml + __init__.py). Drag the zip
into a Blender 4.2+ window to install it.
"""

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
MANIFEST = ROOT / "blender_manifest.toml"
ADDON = ROOT / "blender_mcp_addon.py"


def main():
    version_match = re.search(
        r'^version\s*=\s*"([^"]+)"', MANIFEST.read_text(), re.MULTILINE
    )
    if not version_match:
        raise SystemExit("Could not find version in blender_manifest.toml")
    version = version_match.group(1)

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out_path = dist / f"blender_mcp-{version}.zip"

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(MANIFEST, "blender_manifest.toml")
        zf.write(ADDON, "__init__.py")

    print(f"Built {out_path}")


if __name__ == "__main__":
    main()
