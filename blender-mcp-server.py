#!/usr/bin/env python3
"""
Blender MCP Server - Improved Version
Connects Claude to Blender via MCP protocol for scene manipulation, screenshots, and more.

Installation:
1. Install the MCP SDK: pip install mcp
2. Make sure Blender is running with the socket server addon
3. Add to claude_desktop_config.json:
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
"""

import asyncio
import base64
import json
import os
import socket
import tempfile
from typing import Any, Optional
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)


class BlenderMCPServer:
    """MCP Server for Blender integration"""

    def __init__(self, host: str = "localhost", port: int = 9876):
        self.host = host
        self.port = port
        self.server = Server("blender-mcp")
        self._setup_handlers()

    def _setup_handlers(self):
        """Register all MCP handlers"""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List all available tools"""
            return [
                Tool(
                    name="get_scene_info",
                    description="Get detailed information about the current Blender scene including objects, camera, lights, and materials",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                ),
                Tool(
                    name="get_viewport_screenshot",
                    description="Capture a screenshot of the current Blender 3D viewport. Returns the image as base64-encoded PNG.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "max_size": {
                                "type": "integer",
                                "description": "Maximum size in pixels for the largest dimension (default: 800)",
                                "default": 800,
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="get_object_info",
                    description="Get detailed information about a specific object in the scene",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object to inspect",
                            },
                        },
                        "required": ["object_name"],
                    },
                ),
                Tool(
                    name="execute_python",
                    description="Execute arbitrary Python code in Blender. Use with caution. Code runs in Blender's context with 'bpy' available.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute",
                            },
                        },
                        "required": ["code"],
                    },
                ),
                Tool(
                    name="create_object",
                    description="Create a new object in the scene (mesh primitive, light, camera, etc.)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_type": {
                                "type": "string",
                                "enum": ["CUBE", "SPHERE", "CYLINDER", "CONE", "TORUS", "PLANE", "MONKEY", "CAMERA", "LIGHT"],
                                "description": "Type of object to create",
                            },
                            "name": {
                                "type": "string",
                                "description": "Name for the new object",
                            },
                            "location": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Location [x, y, z] in world space",
                                "default": [0, 0, 0],
                            },
                            "rotation": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Rotation [x, y, z] in radians",
                                "default": [0, 0, 0],
                            },
                            "scale": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Scale [x, y, z]",
                                "default": [1, 1, 1],
                            },
                        },
                        "required": ["object_type"],
                    },
                ),
                Tool(
                    name="modify_object",
                    description="Modify an existing object's transform, visibility, or other properties",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object to modify",
                            },
                            "location": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "New location [x, y, z]",
                            },
                            "rotation": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "New rotation [x, y, z] in radians",
                            },
                            "scale": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "New scale [x, y, z]",
                            },
                            "visible": {
                                "type": "boolean",
                                "description": "Set visibility",
                            },
                        },
                        "required": ["object_name"],
                    },
                ),
                Tool(
                    name="delete_object",
                    description="Delete an object from the scene",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object to delete",
                            },
                        },
                        "required": ["object_name"],
                    },
                ),
                Tool(
                    name="select_objects",
                    description="Select or deselect objects in the scene",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of object names to select",
                            },
                            "deselect_all": {
                                "type": "boolean",
                                "description": "Deselect all objects first",
                                "default": True,
                            },
                        },
                        "required": ["object_names"],
                    },
                ),
                Tool(
                    name="render_image",
                    description="Render the current scene and return the rendered image",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "resolution_x": {
                                "type": "integer",
                                "description": "Render width in pixels",
                                "default": 1920,
                            },
                            "resolution_y": {
                                "type": "integer",
                                "description": "Render height in pixels",
                                "default": 1080,
                            },
                            "samples": {
                                "type": "integer",
                                "description": "Number of render samples (higher = better quality, slower)",
                                "default": 128,
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="get_modifiers",
                    description="List all modifiers on an object with their properties",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object to inspect",
                            },
                        },
                        "required": ["object_name"],
                    },
                ),
                Tool(
                    name="add_modifier",
                    description="Add a modifier to an object",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object to add the modifier to",
                            },
                            "modifier_type": {
                                "type": "string",
                                "enum": [
                                    "SUBSURF", "BEVEL", "ARRAY", "MIRROR", "BOOLEAN",
                                    "SOLIDIFY", "WIREFRAME", "DECIMATE", "REMESH",
                                    "SMOOTH", "SHRINKWRAP", "CURVE", "NODES",
                                ],
                                "description": "Type of modifier to add",
                            },
                            "modifier_name": {
                                "type": "string",
                                "description": "Custom name for the modifier (optional)",
                            },
                            "properties": {
                                "type": "object",
                                "description": "Property name-value pairs to set on the modifier (e.g. {\"levels\": 3})",
                            },
                        },
                        "required": ["object_name", "modifier_type"],
                    },
                ),
                Tool(
                    name="remove_modifier",
                    description="Remove a modifier from an object",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object",
                            },
                            "modifier_name": {
                                "type": "string",
                                "description": "Name of the modifier to remove",
                            },
                        },
                        "required": ["object_name", "modifier_name"],
                    },
                ),
                Tool(
                    name="apply_modifier",
                    description="Apply a modifier (bakes it into the mesh)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object",
                            },
                            "modifier_name": {
                                "type": "string",
                                "description": "Name of the modifier to apply",
                            },
                        },
                        "required": ["object_name", "modifier_name"],
                    },
                ),
                Tool(
                    name="set_geometry_nodes_input",
                    description="Set an input value on a Geometry Nodes modifier",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "object_name": {
                                "type": "string",
                                "description": "Name of the object",
                            },
                            "modifier_name": {
                                "type": "string",
                                "description": "Name of the Geometry Nodes modifier",
                            },
                            "input_name": {
                                "type": "string",
                                "description": "Input identifier (e.g. 'Socket_2') or display name",
                            },
                            "value": {
                                "description": "Value to set (number, array, string, or boolean)",
                            },
                        },
                        "required": ["object_name", "modifier_name", "input_name", "value"],
                    },
                ),
                Tool(
                    name="list_asset_libraries",
                    description="List all asset libraries configured in Blender preferences",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                ),
                Tool(
                    name="list_assets",
                    description="List/search assets available in a specific asset library",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "library_name": {
                                "type": "string",
                                "description": "Name of the asset library (from list_asset_libraries)",
                            },
                            "search": {
                                "type": "string",
                                "description": "Filter asset names (case-insensitive substring match)",
                                "default": "",
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Pagination offset",
                                "default": 0,
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max results to return",
                                "default": 50,
                            },
                        },
                        "required": ["library_name"],
                    },
                ),
                Tool(
                    name="add_asset_to_scene",
                    description="Append an asset from a library into the current scene",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "library_name": {
                                "type": "string",
                                "description": "Name of the asset library",
                            },
                            "asset_name": {
                                "type": "string",
                                "description": "Name of the asset (folder name or file stem)",
                            },
                            "location": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Where to place the asset [x, y, z]",
                                "default": [0, 0, 0],
                            },
                        },
                        "required": ["library_name", "asset_name"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent | ImageContent]:
            """Handle tool calls"""
            try:
                if name == "get_scene_info":
                    result = await self._send_command("get_scene_info", {})
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "get_viewport_screenshot":
                    max_size = arguments.get("max_size", 800)
                    result = await self._send_command("get_viewport_screenshot", {"max_size": max_size})

                    image_data = result.get("image_data", "")
                    if image_data:
                        return [
                            ImageContent(
                                type="image",
                                data=image_data,
                                mimeType="image/png",
                            )
                        ]
                    else:
                        return [TextContent(type="text", text="Screenshot failed: no image data returned")]

                elif name == "get_object_info":
                    result = await self._send_command("get_object_info", {
                        "name": arguments["object_name"]
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "execute_python":
                    result = await self._send_command("execute_code", {
                        "code": arguments["code"]
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "create_object":
                    obj_type = arguments["object_type"]
                    name = arguments.get("name", obj_type.lower())
                    loc = arguments.get("location", [0, 0, 0])
                    rot = arguments.get("rotation", [0, 0, 0])
                    scale = arguments.get("scale", [1, 1, 1])

                    code = f'''
import bpy
import mathutils

# Create object based on type
obj_type = "{obj_type}"

if obj_type == "CUBE":
    bpy.ops.mesh.primitive_cube_add()
elif obj_type == "SPHERE":
    bpy.ops.mesh.primitive_uv_sphere_add()
elif obj_type == "CYLINDER":
    bpy.ops.mesh.primitive_cylinder_add()
elif obj_type == "CONE":
    bpy.ops.mesh.primitive_cone_add()
elif obj_type == "TORUS":
    bpy.ops.mesh.primitive_torus_add()
elif obj_type == "PLANE":
    bpy.ops.mesh.primitive_plane_add()
elif obj_type == "MONKEY":
    bpy.ops.mesh.primitive_monkey_add()
elif obj_type == "CAMERA":
    bpy.ops.object.camera_add()
elif obj_type == "LIGHT":
    bpy.ops.object.light_add(type='POINT')

obj = bpy.context.active_object
obj.name = "{name}"
obj.location = {loc}
obj.rotation_euler = {rot}
obj.scale = {scale}

print(f"Created {{obj_type}}: {{obj.name}} at {{obj.location}}")
'''
                    result = await self._send_command("execute_code", {"code": code})
                    return [TextContent(type="text", text=f"Object created successfully\n{result}")]

                elif name == "modify_object":
                    obj_name = arguments["object_name"]
                    modifications = []

                    code_parts = [f'import bpy\nobj = bpy.data.objects.get("{obj_name}")\nif not obj:\n    print("ERROR: Object not found")\nelse:']

                    if "location" in arguments:
                        code_parts.append(f'    obj.location = {arguments["location"]}')
                        modifications.append("location")
                    if "rotation" in arguments:
                        code_parts.append(f'    obj.rotation_euler = {arguments["rotation"]}')
                        modifications.append("rotation")
                    if "scale" in arguments:
                        code_parts.append(f'    obj.scale = {arguments["scale"]}')
                        modifications.append("scale")
                    if "visible" in arguments:
                        code_parts.append(f'    obj.hide_set({not arguments["visible"]})')
                        modifications.append("visibility")

                    code_parts.append(f'    print("Modified {obj_name}: {", ".join(modifications)}")')
                    code = "\n".join(code_parts)

                    result = await self._send_command("execute_code", {"code": code})
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "delete_object":
                    obj_name = arguments["object_name"]
                    code = f'''
import bpy
obj = bpy.data.objects.get("{obj_name}")
if obj:
    bpy.data.objects.remove(obj, do_unlink=True)
    print(f"Deleted object: {obj_name}")
else:
    print(f"ERROR: Object not found: {obj_name}")
'''
                    result = await self._send_command("execute_code", {"code": code})
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "select_objects":
                    obj_names = arguments["object_names"]
                    deselect_all = arguments.get("deselect_all", True)

                    code = f'''
import bpy

if {deselect_all}:
    bpy.ops.object.select_all(action='DESELECT')

selected = []
for name in {obj_names}:
    obj = bpy.data.objects.get(name)
    if obj:
        obj.select_set(True)
        selected.append(name)
    else:
        print(f"WARNING: Object not found: {{name}}")

if selected:
    bpy.context.view_layer.objects.active = bpy.data.objects[selected[0]]

print(f"Selected {{len(selected)}} objects: {{selected}}")
'''
                    result = await self._send_command("execute_code", {"code": code})
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "render_image":
                    res_x = arguments.get("resolution_x", 1920)
                    res_y = arguments.get("resolution_y", 1080)
                    samples = arguments.get("samples", 128)

                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    temp_path = temp_file.name
                    temp_file.close()

                    code = f'''
import bpy

# Save current settings
old_res_x = bpy.context.scene.render.resolution_x
old_res_y = bpy.context.scene.render.resolution_y
old_samples = bpy.context.scene.cycles.samples if bpy.context.scene.render.engine == 'CYCLES' else None

# Set render settings
bpy.context.scene.render.resolution_x = {res_x}
bpy.context.scene.render.resolution_y = {res_y}
bpy.context.scene.render.filepath = "{temp_path}"
bpy.context.scene.render.image_settings.file_format = 'PNG'

if bpy.context.scene.render.engine == 'CYCLES':
    bpy.context.scene.cycles.samples = {samples}

# Render
bpy.ops.render.render(write_still=True)

# Restore settings
bpy.context.scene.render.resolution_x = old_res_x
bpy.context.scene.render.resolution_y = old_res_y
if old_samples:
    bpy.context.scene.cycles.samples = old_samples

print("Render complete: {temp_path}")
'''
                    result = await self._send_command("execute_code", {"code": code})

                    # Read rendered image
                    if os.path.exists(temp_path):
                        with open(temp_path, "rb") as f:
                            image_data = base64.b64encode(f.read()).decode("utf-8")
                        os.unlink(temp_path)

                        return [
                            ImageContent(
                                type="image",
                                data=image_data,
                                mimeType="image/png",
                            )
                        ]
                    else:
                        return [TextContent(type="text", text=f"Render failed: {result}")]

                elif name == "get_modifiers":
                    result = await self._send_command("get_modifiers", {
                        "object_name": arguments["object_name"],
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "add_modifier":
                    params = {
                        "object_name": arguments["object_name"],
                        "modifier_type": arguments["modifier_type"],
                    }
                    if "modifier_name" in arguments:
                        params["modifier_name"] = arguments["modifier_name"]
                    if "properties" in arguments:
                        params["properties"] = arguments["properties"]
                    result = await self._send_command("add_modifier", params)
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "remove_modifier":
                    result = await self._send_command("remove_modifier", {
                        "object_name": arguments["object_name"],
                        "modifier_name": arguments["modifier_name"],
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "apply_modifier":
                    result = await self._send_command("apply_modifier", {
                        "object_name": arguments["object_name"],
                        "modifier_name": arguments["modifier_name"],
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "set_geometry_nodes_input":
                    result = await self._send_command("set_geometry_nodes_input", {
                        "object_name": arguments["object_name"],
                        "modifier_name": arguments["modifier_name"],
                        "input_name": arguments["input_name"],
                        "value": arguments["value"],
                    })
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "list_asset_libraries":
                    result = await self._send_command("get_asset_libraries", {})
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "list_assets":
                    params = {
                        "library_name": arguments["library_name"],
                        "search": arguments.get("search", ""),
                        "offset": arguments.get("offset", 0),
                        "limit": arguments.get("limit", 50),
                    }
                    result = await self._send_command("list_assets", params)
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                elif name == "add_asset_to_scene":
                    params = {
                        "library_name": arguments["library_name"],
                        "asset_name": arguments["asset_name"],
                        "location": arguments.get("location", [0, 0, 0]),
                    }
                    result = await self._send_command("append_asset", params)
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

            except Exception as e:
                return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def _send_command(self, cmd_type: str, params: dict) -> dict:
        """Send a command to the Blender socket server"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_command_sync, cmd_type, params)

    def _send_command_sync(self, cmd_type: str, params: dict) -> dict:
        """Send a command to the Blender socket server (blocking)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30.0)
        try:
            sock.connect((self.host, self.port))

            command = {
                "type": cmd_type,
                "params": params,
            }
            sock.sendall(json.dumps(command).encode("utf-8"))

            response_data = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                response_data += chunk
                try:
                    response = json.loads(response_data.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue

            if not response_data:
                raise Exception("Connection closed without response")

            try:
                response = json.loads(response_data.decode("utf-8"))
            except json.JSONDecodeError:
                raise Exception(f"Invalid JSON response: {response_data[:200]}")

            if response.get("status") == "error":
                raise Exception(response.get("message", "Unknown error"))

            return response.get("result", {})

        except Exception as e:
            raise Exception(f"Failed to communicate with Blender: {str(e)}")
        finally:
            sock.close()

    async def run(self):
        """Run the MCP server"""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )


async def main():
    """Main entry point"""
    host = os.environ.get("BLENDER_HOST", "localhost")
    port = int(os.environ.get("BLENDER_PORT", "9876"))

    server = BlenderMCPServer(host=host, port=port)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
