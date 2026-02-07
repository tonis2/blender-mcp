"""
Blender MCP Addon - Simplified Version
Socket server addon for Blender that accepts commands from the MCP server

Installation:
1. Open Blender
2. Edit > Preferences > Add-ons > Install
3. Select this file
4. Enable "Interface: Blender MCP"
5. Click "Start Server" in the 3D View sidebar (N panel) > BlenderMCP tab
"""

import bpy
import json
import os
import socket
import threading
import time
import traceback
import io
from contextlib import redirect_stdout
from bpy.props import IntProperty, BoolProperty, StringProperty

bl_info = {
    "name": "Blender MCP",
    "author": "Tonis",
    "version": (2, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP protocol",
    "category": "Interface",
}


class BlenderMCPServer:
    """Simple socket server for Blender MCP communication"""

    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("BlenderMCP: Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP: Server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"BlenderMCP: Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP: Server stopped")

    def _server_loop(self):
        """Main server loop"""
        self.socket.settimeout(1.0)

        while self.running:
            try:
                try:
                    client, address = self.socket.accept()
                    print(f"BlenderMCP: Client connected from {address}")

                    # Handle in separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"BlenderMCP: Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                if self.running:
                    print(f"BlenderMCP: Error in server loop: {str(e)}")
                time.sleep(0.5)

    def _handle_client(self, client):
        """Handle client connection"""
        client.settimeout(None)
        buffer = b''

        try:
            while self.running:
                try:
                    data = client.recv(8192)
                    if not data:
                        break

                    buffer += data
                    try:
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Execute in main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    pass
                            except Exception as e:
                                print(f"BlenderMCP: Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        pass
                except Exception as e:
                    print(f"BlenderMCP: Error receiving data: {str(e)}")
                    break
        finally:
            try:
                client.close()
            except:
                pass

    def execute_command(self, command):
        """Execute a command"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            handlers = {
                "get_scene_info": self.get_scene_info,
                "get_object_info": self.get_object_info,
                "get_viewport_screenshot": self.get_viewport_screenshot,
                "execute_code": self.execute_code,
                "get_asset_libraries": self.get_asset_libraries,
                "list_assets": self.list_assets,
                "append_asset": self.append_asset,
                "get_modifiers": self.get_modifiers,
                "add_modifier": self.add_modifier,
                "remove_modifier": self.remove_modifier,
                "apply_modifier": self.apply_modifier,
                "set_geometry_nodes_input": self.set_geometry_nodes_input,
            }

            handler = handlers.get(cmd_type)
            if handler:
                result = handler(**params)
                return {"status": "success", "result": result}
            else:
                return {"status": "error", "message": f"Unknown command: {cmd_type}"}

        except Exception as e:
            print(f"BlenderMCP: Error in execute_command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def get_scene_info(self):
        """Get scene information"""
        scene_info = {
            "name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects),
            "objects": [],
            "materials_count": len(bpy.data.materials),
        }

        for i, obj in enumerate(bpy.context.scene.objects):
            if i >= 20:
                break

            obj_info = {
                "name": obj.name,
                "type": obj.type,
                "location": [round(float(obj.location.x), 2),
                            round(float(obj.location.y), 2),
                            round(float(obj.location.z), 2)],
            }
            scene_info["objects"].append(obj_info)

        return scene_info

    def get_object_info(self, name):
        """Get object information"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def get_viewport_screenshot(self, max_size=800):
        """Capture viewport screenshot and return as base64"""
        import tempfile
        import base64
        import os

        temp_path = tempfile.mktemp(suffix='.png')

        try:
            scene = bpy.context.scene

            # Save current render settings
            old_filepath = scene.render.filepath
            old_format = scene.render.image_settings.file_format
            old_res_x = scene.render.resolution_x
            old_res_y = scene.render.resolution_y
            old_pct = scene.render.resolution_percentage

            # Configure render output
            scene.render.image_settings.file_format = 'PNG'
            scene.render.filepath = temp_path
            scene.render.resolution_percentage = 100

            # Find 3D viewport
            view3d_area = None
            view3d_region = None
            target_window = None

            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        view3d_area = area
                        target_window = window
                        for region in area.regions:
                            if region.type == 'WINDOW':
                                view3d_region = region
                                break
                        break
                if view3d_area:
                    break

            if not view3d_area or not view3d_region:
                raise Exception("No 3D viewport found")

            # Calculate resolution maintaining viewport aspect ratio
            vp_width = view3d_region.width
            vp_height = view3d_region.height

            if vp_width >= vp_height:
                new_width = min(max_size, vp_width)
                new_height = int(new_width * vp_height / vp_width)
            else:
                new_height = min(max_size, vp_height)
                new_width = int(new_height * vp_width / vp_height)

            scene.render.resolution_x = new_width
            scene.render.resolution_y = new_height

            # OpenGL viewport render - works reliably from timer context
            override = {
                'window': target_window,
                'screen': target_window.screen,
                'area': view3d_area,
                'region': view3d_region,
            }
            with bpy.context.temp_override(**override):
                bpy.ops.render.opengl(write_still=True)

            # Restore settings
            scene.render.filepath = old_filepath
            scene.render.image_settings.file_format = old_format
            scene.render.resolution_x = old_res_x
            scene.render.resolution_y = old_res_y
            scene.render.resolution_percentage = old_pct

            # Read and encode
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                with open(temp_path, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode('utf-8')
                return {"image_data": image_data}
            else:
                raise Exception("Screenshot file was not created or is empty")

        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except:
                pass

    def execute_code(self, code):
        """Execute Python code"""
        try:
            namespace = {"bpy": bpy}
            capture_buffer = io.StringIO()

            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            output = capture_buffer.getvalue()
            return {"executed": True, "output": output}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")

    def get_asset_libraries(self):
        """List all asset libraries configured in Blender preferences"""
        libraries = []
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            libraries.append({
                "name": lib.name,
                "path": lib.path,
            })
        return libraries

    def list_assets(self, library_name, search="", offset=0, limit=50):
        """List assets available in a specific library"""
        # Find the library path from preferences
        library_path = None
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            if lib.name == library_name:
                library_path = lib.path
                break

        if library_path is None:
            raise ValueError(f"Asset library not found: {library_name}")

        if not os.path.isdir(library_path):
            raise ValueError(f"Asset library path does not exist: {library_path}")

        # Scan for asset folders containing .blend files
        assets = []
        for entry in sorted(os.listdir(library_path)):
            entry_path = os.path.join(library_path, entry)
            if os.path.isdir(entry_path):
                blend_files = [f for f in os.listdir(entry_path) if f.endswith('.blend')]
                if blend_files:
                    assets.append({
                        "name": entry,
                        "blend_file": blend_files[0],
                    })
            elif entry.endswith('.blend'):
                # Also handle .blend files directly in the library root
                assets.append({
                    "name": os.path.splitext(entry)[0],
                    "blend_file": entry,
                })

        # Apply search filter
        if search:
            search_lower = search.lower()
            assets = [a for a in assets if search_lower in a["name"].lower()]

        total = len(assets)
        assets = assets[offset:offset + limit]

        return {
            "library": library_name,
            "total": total,
            "offset": offset,
            "limit": limit,
            "assets": assets,
        }

    def append_asset(self, library_name, asset_name, location=None):
        """Append an asset from a library into the current scene"""
        if location is None:
            location = [0, 0, 0]

        # Find the library path from preferences
        library_path = None
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            if lib.name == library_name:
                library_path = lib.path
                break

        if library_path is None:
            raise ValueError(f"Asset library not found: {library_name}")

        # Find the .blend file for this asset
        blend_path = None
        asset_dir = os.path.join(library_path, asset_name)
        if os.path.isdir(asset_dir):
            for f in os.listdir(asset_dir):
                if f.endswith('.blend'):
                    blend_path = os.path.join(asset_dir, f)
                    break
        else:
            # Check for .blend file directly in library root
            candidate = os.path.join(library_path, asset_name + '.blend')
            if os.path.isfile(candidate):
                blend_path = candidate

        if blend_path is None:
            raise ValueError(f"No .blend file found for asset: {asset_name}")

        # Track objects before append
        objects_before = set(bpy.data.objects.keys())

        # Discover and append objects/collections from the .blend file
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            # Prefer collections if available, otherwise append objects
            if data_from.collections:
                data_to.collections = list(data_from.collections)
            elif data_from.objects:
                data_to.objects = list(data_from.objects)

        # Link appended collections/objects to the scene
        appended_objects = []
        if hasattr(data_to, 'collections'):
            for coll in data_to.collections:
                if coll is not None:
                    bpy.context.scene.collection.children.link(coll)
                    for obj in coll.all_objects:
                        appended_objects.append(obj.name)

        if hasattr(data_to, 'objects'):
            for obj in data_to.objects:
                if obj is not None and obj.name not in objects_before:
                    bpy.context.collection.objects.link(obj)
                    appended_objects.append(obj.name)

        # Also catch any new objects that appeared (from collections)
        if not appended_objects:
            objects_after = set(bpy.data.objects.keys())
            appended_objects = list(objects_after - objects_before)

        # Set location on appended objects
        for obj_name in appended_objects:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.parent is None:
                obj.location = location

        return {
            "appended_objects": appended_objects,
            "location": location,
        }

    def get_modifiers(self, object_name):
        """List all modifiers on an object with their properties"""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")

        modifiers = []
        for mod in obj.modifiers:
            mod_info = {
                "name": mod.name,
                "type": mod.type,
                "properties": {},
            }

            # Common properties by type
            prop_map = {
                'SUBSURF': ['levels', 'render_levels', 'uv_smooth', 'quality'],
                'BEVEL': ['width', 'segments', 'limit_method', 'offset_type'],
                'ARRAY': ['count', 'use_relative_offset', 'use_constant_offset', 'relative_offset_displace', 'constant_offset_displace'],
                'MIRROR': ['use_axis', 'use_bisect_axis', 'merge_threshold'],
                'BOOLEAN': ['operation', 'solver'],
                'SOLIDIFY': ['thickness', 'offset', 'use_even_offset'],
                'WIREFRAME': ['thickness', 'use_replace', 'use_even_offset'],
                'DECIMATE': ['decimate_type', 'ratio', 'angle_limit'],
                'REMESH': ['mode', 'octree_depth', 'voxel_size'],
                'SMOOTH': ['factor', 'iterations'],
                'SHRINKWRAP': ['wrap_method', 'wrap_mode', 'offset'],
                'CURVE': ['deform_axis'],
            }

            props_to_read = prop_map.get(mod.type, [])
            for prop_name in props_to_read:
                try:
                    val = getattr(mod, prop_name)
                    # Convert Blender types to JSON-serializable
                    if isinstance(val, bpy.types.ID):
                        val = val.name if val else None
                    elif hasattr(val, '__iter__') and not isinstance(val, str):
                        val = list(val)
                    mod_info["properties"][prop_name] = val
                except AttributeError:
                    pass

            # Geometry Nodes special handling
            if mod.type == 'NODES' and mod.node_group:
                mod_info["node_group"] = mod.node_group.name
                mod_info["inputs"] = []
                for item in mod.node_group.interface.items_tree:
                    if item.item_type == 'SOCKET' and item.in_out == 'INPUT':
                        input_info = {
                            "identifier": item.identifier,
                            "name": item.name,
                            "socket_type": item.socket_type,
                        }
                        try:
                            val = mod[item.identifier]
                            if isinstance(val, bpy.types.ID):
                                val = val.name if val else None
                            elif hasattr(val, '__iter__') and not isinstance(val, str):
                                val = list(val)
                            input_info["value"] = val
                        except (KeyError, TypeError):
                            input_info["value"] = None
                        mod_info["inputs"].append(input_info)

            modifiers.append(mod_info)

        return modifiers

    def add_modifier(self, object_name, modifier_type, modifier_name=None, properties=None):
        """Add a modifier to an object"""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")

        name = modifier_name or modifier_type
        mod = obj.modifiers.new(name=name, type=modifier_type)

        if properties:
            for key, value in properties.items():
                try:
                    setattr(mod, key, value)
                except Exception as e:
                    print(f"BlenderMCP: Warning - could not set {key}={value}: {e}")

        # Read back properties
        result_props = {}
        for key in (properties or {}):
            try:
                val = getattr(mod, key)
                if hasattr(val, '__iter__') and not isinstance(val, str):
                    val = list(val)
                result_props[key] = val
            except AttributeError:
                pass

        return {
            "modifier_name": mod.name,
            "type": mod.type,
            "properties": result_props,
        }

    def remove_modifier(self, object_name, modifier_name):
        """Remove a modifier from an object"""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")

        mod = obj.modifiers.get(modifier_name)
        if not mod:
            raise ValueError(f"Modifier not found: {modifier_name}")

        obj.modifiers.remove(mod)
        return {"removed": modifier_name}

    def apply_modifier(self, object_name, modifier_name):
        """Apply a modifier (bake it into the mesh)"""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")

        mod = obj.modifiers.get(modifier_name)
        if not mod:
            raise ValueError(f"Modifier not found: {modifier_name}")

        # Need context override for modifier_apply
        window = bpy.context.window_manager.windows[0]
        override = {
            'window': window,
            'screen': window.screen,
            'area': window.screen.areas[0],
            'object': obj,
            'active_object': obj,
        }
        with bpy.context.temp_override(**override):
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=modifier_name)

        return {"applied": modifier_name}

    def set_geometry_nodes_input(self, object_name, modifier_name, input_name, value):
        """Set an input value on a Geometry Nodes modifier"""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ValueError(f"Object not found: {object_name}")

        mod = obj.modifiers.get(modifier_name)
        if not mod:
            raise ValueError(f"Modifier not found: {modifier_name}")

        if mod.type != 'NODES':
            raise ValueError(f"Modifier '{modifier_name}' is not a Geometry Nodes modifier (type: {mod.type})")

        if not mod.node_group:
            raise ValueError(f"Modifier '{modifier_name}' has no node group assigned")

        # Find input by identifier or display name
        target_identifier = None
        for item in mod.node_group.interface.items_tree:
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT':
                if item.identifier == input_name or item.name == input_name:
                    target_identifier = item.identifier
                    break

        if target_identifier is None:
            available = [
                f"{item.identifier} ({item.name})"
                for item in mod.node_group.interface.items_tree
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
            ]
            raise ValueError(
                f"Input not found: '{input_name}'. Available inputs: {available}"
            )

        # Determine socket type to handle ID properties (Object, Collection, etc.)
        socket_type = None
        for item in mod.node_group.interface.items_tree:
            if item.identifier == target_identifier:
                socket_type = item.socket_type
                break

        if socket_type == 'NodeSocketObject' and isinstance(value, str):
            ref = bpy.data.objects.get(value)
            if ref is None:
                raise ValueError(f"Object not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == 'NodeSocketCollection' and isinstance(value, str):
            ref = bpy.data.collections.get(value)
            if ref is None:
                raise ValueError(f"Collection not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == 'NodeSocketMaterial' and isinstance(value, str):
            ref = bpy.data.materials.get(value)
            if ref is None:
                raise ValueError(f"Material not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == 'NodeSocketImage' and isinstance(value, str):
            ref = bpy.data.images.get(value)
            if ref is None:
                raise ValueError(f"Image not found: '{value}'")
            mod[target_identifier] = ref
        else:
            mod[target_identifier] = value

        # Force UI update
        obj.update_tag()

        return {
            "modifier": modifier_name,
            "input": target_identifier,
            "value": str(value),
        }


# Global server instance
_server = None


# Blender UI Classes

class BLENDERMCP_PT_Panel(bpy.types.Panel):
    """BlenderMCP control panel"""
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="MCP Server", icon='NETWORK_DRIVE')

        row = box.row()
        row.prop(scene, "blendermcp_port")

        global _server
        if _server and _server.running:
            row = box.row()
            row.label(text=f"Status: Running on port {scene.blendermcp_port}", icon='CHECKMARK')
            row = box.row()
            row.operator("blendermcp.stop_server", icon='PAUSE')
        else:
            row = box.row()
            row.label(text="Status: Stopped", icon='CANCEL')
            row = box.row()
            row.operator("blendermcp.start_server", icon='PLAY')


class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    """Start the MCP server"""
    bl_idname = "blendermcp.start_server"
    bl_label = "Start Server"

    def execute(self, context):
        global _server
        if not _server:
            _server = BlenderMCPServer(port=context.scene.blendermcp_port)

        _server.start()
        self.report({'INFO'}, f"MCP Server started on port {context.scene.blendermcp_port}")
        return {'FINISHED'}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    """Stop the MCP server"""
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop Server"

    def execute(self, context):
        global _server
        if _server:
            _server.stop()
            self.report({'INFO'}, "MCP Server stopped")
        return {'FINISHED'}


# Registration

classes = (
    BLENDERMCP_PT_Panel,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for MCP server",
        default=9876,
        min=1024,
        max=65535,
    )


def unregister():
    global _server
    if _server:
        _server.stop()
        _server = None

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.blendermcp_port


if __name__ == "__main__":
    register()
