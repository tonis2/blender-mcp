# Blender MCP Server - Improved Version

A clean, focused MCP (Model Context Protocol) server for controlling Blender from Claude Code. This lets you take screenshots, inspect scenes, create/modify objects, and execute Python code in Blender directly from Claude.

## Features

✅ **Scene Inspection**
- Get detailed scene information
- Inspect individual objects
- View object transforms, materials, and mesh data

✅ **Viewport Screenshot**
- Capture 3D viewport (with proper context handling - fixes the original issue!)
- Automatic resizing
- Returns images directly to Claude

✅ **Object Manipulation**
- Create primitives (cube, sphere, cylinder, cone, torus, plane, monkey)
- Add cameras and lights
- Modify object transforms (location, rotation, scale)
- Delete objects
- Select/deselect objects

✅ **Rendering**
- Render current scene
- Configure resolution and samples
- Returns rendered image to Claude

✅ **Python Execution**
- Execute arbitrary Python code in Blender
- Full access to `bpy` module
- Captured stdout for debugging

## Installation

### Step 1: Install the Blender Addon

1. Open Blender
2. Go to `Edit` > `Preferences` > `Add-ons`
3. Click `Install...`
4. Select `blender_mcp_addon.py`
5. Enable the addon by checking the box next to "Interface: Blender MCP"
6. In the 3D Viewport, press `N` to open the sidebar
7. Click the "BlenderMCP" tab
8. Click **Start Server** (default port: 9876)

### Step 2: Install Python Dependencies

```bash
pip install mcp
```

### Step 3: Configure Claude Code

Add the MCP server to your Claude configuration file:

**Linux/Mac**: `~/.config/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "blender": {
      "command": "python3",
      "args": ["/home/tonis/Downloads/blender-mcp-server.py"],
      "env": {
        "BLENDER_HOST": "localhost",
        "BLENDER_PORT": "9876"
      }
    }
  }
}
```

**Important**: Update the path to `blender-mcp-server.py` to match where you saved it!

### Step 4: Restart Claude Code

Restart Claude Code to load the new MCP server.

## Usage Examples

### Take a Screenshot

```
Can you show me a screenshot of my Blender scene?
```

Claude will use the `get_viewport_screenshot` tool and display the image inline.

### Inspect Scene

```
What objects are in my Blender scene?
```

Claude will use `get_scene_info` to list all objects with their types and positions.

### Create Objects

```
Create a sphere at position (2, 0, 1) named "MySphere"
```

Claude will use the `create_object` tool.

### Modify Objects

```
Move the cube to position (0, 0, 5) and scale it to 2x size
```

Claude will use `modify_object` to update transforms.

### Execute Python Code

```
Run this Python code in Blender:
import bpy
for obj in bpy.context.selected_objects:
    obj.location.z += 1
```

Claude will execute the code using `execute_python` and show the output.

### Render Scene

```
Render the current scene at 1920x1080 with 256 samples
```

Claude will configure render settings and return the rendered image.

## Available Tools

| Tool | Description |
|------|-------------|
| `get_scene_info` | Get scene overview with all objects |
| `get_viewport_screenshot` | Capture 3D viewport screenshot |
| `get_object_info` | Get detailed info about a specific object |
| `execute_python` | Run Python code in Blender |
| `create_object` | Create new mesh primitives, cameras, or lights |
| `modify_object` | Change object transform or visibility |
| `delete_object` | Remove an object from the scene |
| `select_objects` | Select/deselect objects |
| `render_image` | Render the scene and return the image |

## Comparison with Original

This version is **cleaner and more focused**:

### What's Removed
- ❌ PolyHaven integration
- ❌ Hyper3D integration
- ❌ Sketchfab integration
- ❌ Hunyuan3D integration
- ❌ Complex telemetry system

### What's Improved
- ✅ **Fixed screenshot context issue** - properly overrides Blender context
- ✅ **Proper MCP protocol** - follows MCP SDK standards
- ✅ **Better error handling** - clearer error messages
- ✅ **Cleaner code** - ~600 lines vs 2600+ lines
- ✅ **Direct image returns** - screenshots/renders shown inline in Claude
- ✅ **More flexible object creation** - easier to create and modify objects
- ✅ **Better selection handling** - proper selection management

## Troubleshooting

### Server won't start in Blender
- Check that port 9876 isn't already in use
- Try a different port (change in both addon and MCP config)
- Check Blender console for error messages

### Claude can't connect to Blender
- Make sure the Blender addon server is running (green checkmark in UI)
- Verify the port in `claude_desktop_config.json` matches Blender
- Restart Claude Code after config changes

### Screenshots fail
- Make sure you have a 3D viewport visible in Blender
- The improved version should fix the context issues from the original

### Python execution errors
- Check Blender's console for detailed traceback
- Verify the code is valid Python
- Remember that `bpy` is available in the namespace

## Architecture

```
┌─────────────┐         MCP Protocol          ┌──────────────────┐
│             │◄─────── (stdio/JSON) ─────────│                  │
│ Claude Code │                                │  MCP Server      │
│             │                                │  (Python script) │
└─────────────┘                                └────────┬─────────┘
                                                        │
                                                Socket (JSON)
                                                        │
                                              ┌─────────▼─────────┐
                                              │  Blender Addon    │
                                              │  (Socket Server)  │
                                              │                   │
                                              │  Executes in      │
                                              │  main thread via  │
                                              │  bpy.app.timers   │
                                              └───────────────────┘
```

1. **Claude Code** sends MCP requests to the server
2. **MCP Server** translates MCP protocol to Blender socket commands
3. **Blender Addon** receives commands via socket, executes in main thread
4. Results flow back: Blender → MCP Server → Claude

## Security Notes

- The `execute_python` tool can run arbitrary code in Blender
- Only run this on trusted local Blender instances
- The socket server only listens on localhost by default
- Consider firewall rules if changing host binding

## License

Free to use and modify. Based on the original Blender MCP addon by Siddharth Ahuja.

## Contributing

Improvements welcome! This is a simplified, focused version for core Blender control.
