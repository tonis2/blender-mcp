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

Two components communicate over a local socket:

1. **Blender addon** (`blender_mcp_addon.py`) — runs a socket server inside Blender that executes commands
2. **MCP server** (`blender-mcp-server.py`) — translates MCP tool calls from AI into socket commands

## Setup

### 1. Install the Blender addon

1. Open Blender
2. Go to **Edit > Preferences > Add-ons > Install**
3. Select `blender_mcp_addon.py`
4. Enable **"Interface: Blender MCP"**
5. Open the sidebar in the 3D Viewport (press `N`), find the **BlenderMCP** tab, and click **Start Server**

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

- Blender 3.0+
- Python 3.10+
- `mcp` Python package
