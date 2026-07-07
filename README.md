# Blender MCP

Connect Blender to AI via the [Model Context Protocol](https://modelcontextprotocol.io/), allowing AI to directly interact with your Blender scene.

## What it does

This addon lets AI control Blender through natural language. AI can:

- **Inspect scenes** — get info about objects, materials, and scene structure
- **Create and modify objects** — add primitives, move/rotate/scale, delete objects
- **Take screenshots** — capture any editor panel (3D viewport, node editor, etc.)
- **Render images** — trigger renders and view the result
- **Manage modifiers** — add, remove, apply, and configure modifiers including Geometry Nodes
- **Browse asset libraries** — list, search, and import assets from your Blender libraries
- **Execute Python** — run arbitrary Blender Python code

## How it works

Two components communicate over a local socket using null-byte-delimited JSON frames:

1. **Blender addon** (`blender_mcp_addon.py`) — runs a non-blocking socket server inside Blender, polled from a `bpy.app.timers` callback so the UI never freezes on socket I/O. Commands always execute on Blender's main thread.
2. **MCP server** (`blender_mcp_server.py`) — translates MCP tool calls from AI into socket commands

Notable behavior:

- **Deferred responses** — long-running jobs keep the connection open and respond when done (up to 1 hour). `render_image` uses this so renders don't block the UI, and `execute_python` code can opt in by defining a `check_is_finished()` callable that returns `None` while pending and a result dict when finished.
- **Output capture** — `execute_python` returns both stdout and stderr, and errors include the full traceback.
- **Weak sandbox** — `sys.exit()` and a few known-destructive operators (`wm.quit_blender`, factory-reset ops) are blocked in executed code. This is guidance, not a security boundary.
- **Adaptive polling** — the addon polls at 50 ms while active and backs off to 1 s after 5 s of inactivity.
- **Limits** — requests are capped at 10 MiB; clients that don't complete a request within 10 s are evicted.

> **Note:** the framed protocol was introduced in addon v2.1.0. The addon and MCP server must both be from the same version — after updating, reinstall/reload the addon in Blender.

## Setup

### 1. Install the Blender addon

**Option A — drag & drop (Blender 4.2+, recommended)**

1. Download `blender_mcp-<version>.zip` from the [latest release](https://github.com/tonis2/blender-mcp/releases/latest) — it's built automatically by CI. (To build locally instead: `python3 build_extension.py` → `dist/`.)
2. Drag the zip from your file manager into the Blender window
3. Confirm the **Install from Disk** dialog (keep "Enable Add-on" checked)

How this works: since 4.2, Blender installs extensions via drag & drop. Dropping a `.zip` extension package installs it into your *User Default* (local) repository — no update notifications, you update by dropping a newer zip. (Dropping a repository *URL* — the way the official blender.org Lab MCP extension installs — instead registers a remote repository and enables update checks; that requires hosting a repository index, so a local zip is the practical route for this addon.)

**Option B — classic add-on install**

1. Open Blender
2. Go to **Edit > Preferences > Add-ons > Install**
3. Select `blender_mcp_addon.py`
4. Enable **"Interface: Blender MCP"**

With either option, the socket server auto-starts on port 9876. You can also start/stop it manually: open the sidebar in the 3D Viewport (press `N`), find the **BlenderMCP** tab.

### 2. Install the MCP server dependency

```bash
pip install mcp
```

### 3. Claude configure example

Add the server to your Claude config (e.g. `claude_desktop_config.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "blender": {
      "command": "python3",
      "args": ["/path/to/blender-mcp-server.py"],
      "env": {
        "BLENDER_HOST": "localhost",
        "BLENDER_PORT": "9876"
      }
    }
  }
}
```

Replace `/path/to/blender-mcp-server.py` with the actual path to the file.

### 4. Use it

1. Start the server in Blender (BlenderMCP sidebar panel)
2. Open AI console and ask it to interact with your scene

## Requirements

- Blender 3.0+ (4.2+ for drag & drop extension install)
- Python 3.10+
- `mcp` Python package

## Releasing

CI (`.github/workflows/build-extension.yml`) builds the extension zip on every push and PR (available as a workflow artifact). To publish a release: bump `version` in `blender_manifest.toml` and `bl_info` in `blender_mcp_addon.py`, then push a matching tag (e.g. `v2.1.0`) — CI verifies the versions match and attaches the zip to a GitHub release.
