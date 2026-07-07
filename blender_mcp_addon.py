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

import base64
import bpy
import io
import json
import math
import os
import socket
import sys
import time
import traceback
from bpy.props import IntProperty

bl_info = {
    "name": "Blender MCP",
    "author": "Tonis",
    "version": (2, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP protocol",
    "category": "Interface",
}


# ---------------------------------------------------------------------------
# Protocol constants
#
# Requests and responses are null-byte-delimited JSON over TCP. The socket
# server is non-blocking and polled from a bpy.app.timers callback, so all
# command execution happens on Blender's main thread and socket I/O never
# blocks the UI.

MAX_REQUEST_BYTES = 10 * 1024 * 1024  # 10 MiB
RECV_BUFFER_SIZE = 4096
LISTEN_BACKLOG = 5
# Seconds before a client that has not sent a complete request is evicted.
CLIENT_TIMEOUT = 10.0
# Seconds allowed for sending a response before giving up on the client.
SEND_TIMEOUT = 30.0
# Wall-time allowed for a deferred operation (e.g. a render) to complete.
DEFERRED_TIMEOUT = 60.0 * 60.0
# Timer tick interval while there is pending work.
TIMER_INTERVAL_ACTIVE = 0.05
# Timer tick interval after TIMER_IDLE_DELAY seconds without work.
TIMER_INTERVAL_IDLE = 1.0
TIMER_IDLE_DELAY = 5.0


def _encode_response(response):
    """Serialize a response dict as null-byte-delimited JSON bytes."""
    try:
        payload = json.dumps(response)
    except (TypeError, ValueError):
        payload = json.dumps(response, default=repr)
    return (payload + "\0").encode("utf-8")


class CommandError(Exception):
    """Error whose message is already formatted for the client (no traceback wrapping)."""


class Deferred:
    """Returned by a command handler when the response must wait for a background job.

    ``check_fn`` is polled on the server timer; it returns None while pending
    and a result dict when done. ``extra`` keys are merged into the final
    result (without overwriting keys the checker returned).
    """

    def __init__(self, check_fn, extra=None):
        self.check_fn = check_fn
        self.extra = extra or {}


class _Tee(io.TextIOBase):
    """Write to both a StringIO buffer and the original stream."""

    def __init__(self, original):
        self._buffer = io.StringIO()
        self._original = original

    def write(self, s):
        self._original.write(s)
        return self._buffer.write(s)

    def flush(self):
        self._original.flush()
        self._buffer.flush()

    def getvalue(self):
        return self._buffer.getvalue()


class CaptureOutput:
    """Capture stdout/stderr while still forwarding to the real streams."""

    def __enter__(self):
        self._original_out = sys.stdout
        self._original_err = sys.stderr
        self._tee_out = _Tee(self._original_out)
        self._tee_err = _Tee(self._original_err)
        sys.stdout = self._tee_out
        sys.stderr = self._tee_err
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._original_out
        sys.stderr = self._original_err

    @property
    def stdout(self):
        return self._tee_out.getvalue()

    @property
    def stderr(self):
        return self._tee_err.getvalue()


def _blocked_exit(*args, **kwargs):
    raise RuntimeError("sys.exit() is not allowed in LLM-generated code")


# Operators guaranteed to cause problems when called from LLM-generated code.
_BLOCKED_OPS = {
    "wm.quit_blender": "Terminates the Blender process, use bpy.app.quit() if you must",
    "wm.read_factory_settings": (
        "Resets all user preferences and startup file, "
        "use bpy.ops.wm.read_homefile() instead"
    ),
    "wm.read_factory_userpref": (
        "Resets all user preferences, use bpy.ops.wm.read_homefile() instead"
    ),
    "wm.read_userpref": "May reset user preferences disabling this add-on, avoid calling",
}


class WeakSandboxForLLM:
    """Guidance-level guard for LLM-generated code. Not a security boundary:
    a motivated caller can trivially work around it."""

    def __enter__(self):
        self._saved_exit = sys.exit
        sys.exit = _blocked_exit

        self._saved_op_create = None
        op_create = getattr(bpy.ops, "_op_create_function", None)
        if op_create is not None:

            def _filtered(module, func):
                key = f"{module}.{func}"
                reason = _BLOCKED_OPS.get(key)
                if reason is not None:

                    def _blocked(*args, **kwargs):
                        raise RuntimeError(
                            f"Operator 'bpy.ops.{key}' is not allowed "
                            f"in LLM-generated code: {reason}"
                        )

                    return _blocked
                return op_create(module, func)

            self._saved_op_create = op_create
            bpy.ops._op_create_function = _filtered
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.exit = self._saved_exit
        if self._saved_op_create is not None:
            bpy.ops._op_create_function = self._saved_op_create


class _Client:
    """A connection that has not yet sent a complete request."""

    def __init__(self, conn, timeout_ticks):
        self.conn = conn
        self.buffer = bytearray()
        self.timeout = timeout_ticks


class _DeferredClient:
    """A connection held open while a background job completes."""

    def __init__(self, conn, deferred):
        self.conn = conn
        self.deferred = deferred
        self.deadline = time.monotonic() + DEFERRED_TIMEOUT


class BlenderMCPServer:
    """Non-blocking socket server for Blender MCP communication.

    Polled from a bpy.app.timers callback: commands always execute on the
    main thread, and no socket operation blocks the UI.
    """

    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.clients = []
        self.deferred = []
        self._timer_handle = None
        self._idle_countdown_reset = math.ceil(TIMER_IDLE_DELAY / TIMER_INTERVAL_ACTIVE)
        self._idle_countdown = self._idle_countdown_reset
        self._client_timeout_ticks = max(
            2, math.ceil(CLIENT_TIMEOUT / TIMER_INTERVAL_ACTIVE)
        )

    def start(self):
        if self.running:
            print("BlenderMCP: Server is already running")
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind((self.host, self.port))
            sock.listen(LISTEN_BACKLOG)
        except OSError as e:
            sock.close()
            print(f"BlenderMCP: Failed to start server: {e}")
            return

        self.socket = sock
        self.running = True
        self._idle_countdown = self._idle_countdown_reset
        self._timer_handle = self._poll_timer
        bpy.app.timers.register(
            self._timer_handle, first_interval=TIMER_INTERVAL_ACTIVE, persistent=True
        )
        print(f"BlenderMCP: Server started on {self.host}:{self.port}")

    def stop(self):
        self.running = False

        if self._timer_handle is not None:
            try:
                bpy.app.timers.unregister(self._timer_handle)
            except ValueError:
                pass
            self._timer_handle = None

        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        for client in self.clients:
            try:
                client.conn.close()
            except OSError:
                pass
        self.clients.clear()

        for dc in self.deferred:
            try:
                dc.conn.close()
            except OSError:
                pass
        self.deferred.clear()

        print("BlenderMCP: Server stopped")

    # ------------------------------------------------------------------
    # Polling (runs on the main thread via bpy.app.timers)

    def _poll_timer(self):
        # Without exception handling here, any error would remove the timer
        # and silently kill the server.
        try:
            did_work = self._poll()
        except Exception:
            print("BlenderMCP: Unhandled exception in server timer, continuing:")
            traceback.print_exc()
            did_work = True

        if not self.running:
            self._timer_handle = None
            return None

        if did_work:
            self._idle_countdown = self._idle_countdown_reset
        if self._idle_countdown > 0:
            self._idle_countdown -= 1
            return TIMER_INTERVAL_ACTIVE
        return TIMER_INTERVAL_IDLE

    def _poll(self):
        self._accept_clients()
        did_work = self._service_clients()
        if self._poll_deferred():
            did_work = True
        # Stay in active polling mode while deferred jobs are pending.
        if self.deferred:
            did_work = True
        return did_work

    def _accept_clients(self):
        if self.socket is None:
            return
        while True:
            try:
                conn, _addr = self.socket.accept()
            except (BlockingIOError, OSError):
                break
            conn.setblocking(False)
            self.clients.append(_Client(conn, self._client_timeout_ticks))

    def _close_client(self, client):
        try:
            client.conn.close()
        except OSError:
            pass
        try:
            self.clients.remove(client)
        except ValueError:
            pass

    def _send_response(self, conn, response):
        # Responses can be large (base64 images); switch the socket to
        # blocking-with-timeout so sendall can drain the whole payload.
        try:
            conn.settimeout(SEND_TIMEOUT)
            conn.sendall(_encode_response(response))
        except OSError:
            pass

    def _service_clients(self):
        did_work = False
        # Iterate over a copy since clients may be removed during the loop.
        for client in self.clients[:]:
            client.timeout -= 1
            if client.timeout <= 0:
                self._send_response(
                    client.conn, {"status": "error", "message": "Client timed out"}
                )
                self._close_client(client)
                continue

            # Drain everything currently available from this client.
            closed = False
            while b"\0" not in client.buffer and len(client.buffer) <= MAX_REQUEST_BYTES:
                try:
                    chunk = client.conn.recv(RECV_BUFFER_SIZE)
                except BlockingIOError:
                    break
                except OSError:
                    closed = True
                    break
                if not chunk:
                    closed = True
                    break
                client.buffer.extend(chunk)

            if closed:
                self._close_client(client)
                continue

            if len(client.buffer) > MAX_REQUEST_BYTES:
                self._send_response(
                    client.conn,
                    {
                        "status": "error",
                        "message": f"Request exceeds {MAX_REQUEST_BYTES} byte limit",
                    },
                )
                self._close_client(client)
                continue

            if b"\0" not in client.buffer:
                continue

            request_data = bytes(client.buffer[: client.buffer.index(b"\0")])
            response = self._handle_request(request_data)

            if isinstance(response, Deferred):
                # Hand the connection over to the deferred list without closing it.
                self.deferred.append(_DeferredClient(client.conn, response))
                try:
                    self.clients.remove(client)
                except ValueError:
                    pass
            else:
                self._send_response(client.conn, response)
                self._close_client(client)
            did_work = True

        return did_work

    def _poll_deferred(self):
        did_work = False
        for dc in self.deferred[:]:
            if self._is_disconnected(dc.conn):
                try:
                    dc.conn.close()
                except OSError:
                    pass
                try:
                    self.deferred.remove(dc)
                except ValueError:
                    pass
                did_work = True
                continue

            if time.monotonic() > dc.deadline:
                self._finish_deferred(
                    dc,
                    {
                        "status": "error",
                        "message": (
                            f"Deferred operation timed out after "
                            f"{DEFERRED_TIMEOUT:.0f} seconds"
                        ),
                    },
                )
                did_work = True
                continue

            try:
                result = dc.deferred.check_fn()
            except CommandError as e:
                self._finish_deferred(dc, {"status": "error", "message": str(e)})
                did_work = True
                continue
            except Exception:
                self._finish_deferred(
                    dc, {"status": "error", "message": traceback.format_exc()}
                )
                did_work = True
                continue

            if result is None:
                # Still pending.
                continue

            if not isinstance(result, dict):
                self._finish_deferred(
                    dc,
                    {
                        "status": "error",
                        "message": (
                            f"Deferred check returned {type(result).__name__}, "
                            f"expected None or dict"
                        ),
                    },
                )
                did_work = True
                continue

            for key, value in dc.deferred.extra.items():
                result.setdefault(key, value)
            self._finish_deferred(dc, {"status": "success", "result": result})
            did_work = True

        return did_work

    @staticmethod
    def _is_disconnected(conn):
        try:
            data = conn.recv(1, socket.MSG_PEEK)
            # Empty data means the peer closed the connection.
            return len(data) == 0
        except BlockingIOError:
            return False
        except OSError:
            return True

    def _finish_deferred(self, dc, response):
        self._send_response(dc.conn, response)
        try:
            dc.conn.close()
        except OSError:
            pass
        try:
            self.deferred.remove(dc)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Command dispatch

    def _handle_request(self, data):
        try:
            command = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return {"status": "error", "message": f"Invalid request: {e}"}
        return self.execute_command(command)

    def execute_command(self, command):
        """Execute a command and return a response dict (or a Deferred)."""
        cmd_type = command.get("type")
        params = command.get("params", {})

        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "render_image": self.render_image,
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
        if handler is None:
            return {"status": "error", "message": f"Unknown command: {cmd_type}"}

        try:
            result = handler(**params)
        except (ValueError, CommandError) as e:
            return {"status": "error", "message": str(e)}
        except Exception:
            print(f"BlenderMCP: Error in command '{cmd_type}':")
            traceback.print_exc()
            return {"status": "error", "message": traceback.format_exc()}

        if isinstance(result, Deferred):
            return result
        return {"status": "success", "result": result}

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
                "location": [
                    round(float(obj.location.x), 2),
                    round(float(obj.location.y), 2),
                    round(float(obj.location.z), 2),
                ],
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
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        if obj.type == "MESH" and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def get_viewport_screenshot(self, max_size=800, area_type="VIEW_3D"):
        """Capture screenshot of a specific editor area and return as base64"""
        import tempfile
        import base64
        import os

        temp_path = tempfile.mktemp(suffix=".png")

        try:
            # Find the target area
            target_area = None
            target_region = None
            target_window = None

            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == area_type:
                        target_area = area
                        target_window = window
                        for region in area.regions:
                            if region.type == "WINDOW":
                                target_region = region
                                break
                        break
                if target_area:
                    break

            if not target_area or not target_region:
                raise Exception(f"No {area_type} area found")

            # Store screenshot result
            screenshot_result = {"success": False, "error": None}

            def capture_screenshot():
                """Capture screenshot from main thread"""
                try:
                    override = {
                        "window": target_window,
                        "screen": target_window.screen,
                        "area": target_area,
                        "region": target_region,
                    }
                    with bpy.context.temp_override(**override):
                        # Save screenshot to temp file
                        bpy.ops.screen.screenshot_area(filepath=temp_path)

                    screenshot_result["success"] = True
                except Exception as e:
                    screenshot_result["error"] = str(e)
                    print(f"BlenderMCP: Screenshot capture failed: {e}")

            # Execute capture immediately since we're already in main thread via timer
            capture_screenshot()

            if not screenshot_result["success"]:
                raise Exception(
                    f"Screenshot capture failed: {screenshot_result['error']}"
                )

            # Read and process the screenshot
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                # Load image and resize if needed
                image = bpy.data.images.load(temp_path)

                try:
                    # Calculate new dimensions maintaining aspect ratio
                    width, height = image.size
                    if width > max_size or height > max_size:
                        if width > height:
                            new_width = max_size
                            new_height = int(height * (max_size / width))
                        else:
                            new_height = max_size
                            new_width = int(width * (max_size / height))

                        # Resize image
                        image.scale(new_width, new_height)

                    # Save resized image to temp file
                    image.filepath_raw = temp_path
                    image.file_format = "PNG"
                    image.save()

                    # Read the resized image
                    with open(temp_path, "rb") as f:
                        image_data = base64.b64encode(f.read()).decode("utf-8")

                    return {"image_data": image_data}
                finally:
                    # Remove the loaded image from Blender
                    bpy.data.images.remove(image)
            else:
                raise Exception("Screenshot file was not created or is empty")

        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except:
                pass

    _exec_namespace = {"bpy": bpy}

    def execute_code(self, code):
        """Execute Python code with shared state across calls.

        If the code defines a callable ``check_is_finished``, the response is
        deferred: the callable is polled on the server timer until it returns
        a result dict (return None while still pending). Use this for
        background jobs like renders or bakes.
        """
        # Ensure bpy is always available; drop any stale checker from a
        # previous call so it can't accidentally defer this one.
        self._exec_namespace["bpy"] = bpy
        self._exec_namespace.pop("check_is_finished", None)

        with CaptureOutput() as captured, WeakSandboxForLLM():
            try:
                exec(code, self._exec_namespace)
            except Exception:
                message = "Code execution error:\n" + traceback.format_exc()
                if captured.stdout:
                    message += "\n--- stdout ---\n" + captured.stdout
                if captured.stderr:
                    message += "\n--- stderr ---\n" + captured.stderr
                raise CommandError(message) from None

        check_fn = self._exec_namespace.pop("check_is_finished", None)
        if callable(check_fn):
            extra = {"executed": True}
            if captured.stdout:
                extra["output"] = captured.stdout
            if captured.stderr:
                extra["stderr"] = captured.stderr
            return Deferred(check_fn, extra=extra)

        output = captured.stdout
        if not output.strip():
            output = "Code executed successfully (no output)"
        result = {"executed": True, "output": output}
        if captured.stderr:
            result["stderr"] = captured.stderr
        return result

    def render_image(self, resolution_x=1920, resolution_y=1080, samples=None):
        """Render the current scene to PNG and return it base64-encoded.

        In an interactive session the render runs as a background job and the
        response is deferred until it completes, so the UI stays responsive.
        """
        import tempfile

        scene = bpy.context.scene
        render = scene.render
        is_cycles = render.engine == "CYCLES"

        saved = {
            "resolution_x": render.resolution_x,
            "resolution_y": render.resolution_y,
            "filepath": render.filepath,
            "file_format": render.image_settings.file_format,
            "samples": scene.cycles.samples if is_cycles else None,
        }

        temp_path = tempfile.mktemp(suffix=".png")
        render.resolution_x = resolution_x
        render.resolution_y = resolution_y
        render.filepath = temp_path
        render.image_settings.file_format = "PNG"
        if samples is not None and is_cycles:
            scene.cycles.samples = samples

        state = {"done": False, "cancelled": False}

        def _on_complete(*_args):
            state["done"] = True

        def _on_cancel(*_args):
            state["cancelled"] = True

        def _restore():
            for handler_list, fn in (
                (bpy.app.handlers.render_complete, _on_complete),
                (bpy.app.handlers.render_cancel, _on_cancel),
            ):
                try:
                    handler_list.remove(fn)
                except ValueError:
                    pass
            render.resolution_x = saved["resolution_x"]
            render.resolution_y = saved["resolution_y"]
            render.filepath = saved["filepath"]
            render.image_settings.file_format = saved["file_format"]
            if saved["samples"] is not None:
                scene.cycles.samples = saved["samples"]

        def _read_result():
            if not (os.path.exists(temp_path) and os.path.getsize(temp_path) > 0):
                # write_still is not honored on every render path; fall back
                # to saving the Render Result image manually.
                render_result = bpy.data.images.get("Render Result")
                if render_result is not None:
                    render_result.save_render(temp_path)
            if not (os.path.exists(temp_path) and os.path.getsize(temp_path) > 0):
                raise CommandError("Render finished but no output file was written")
            with open(temp_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return {
                "rendered": True,
                "resolution": [resolution_x, resolution_y],
                "image_data": image_data,
            }

        bpy.app.handlers.render_complete.append(_on_complete)
        bpy.app.handlers.render_cancel.append(_on_cancel)

        try:
            op_result = bpy.ops.render.render("INVOKE_DEFAULT", write_still=True)
        except Exception:
            _restore()
            raise

        if "CANCELLED" in op_result:
            _restore()
            raise CommandError(
                "Could not start render (is another render already running?)"
            )

        if state["done"] or "FINISHED" in op_result:
            # Ran synchronously (e.g. background mode); respond immediately.
            _restore()
            return _read_result()

        def check_is_finished():
            if state["cancelled"]:
                _restore()
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise CommandError("Render was cancelled")
            if not state["done"]:
                return None
            _restore()
            return _read_result()

        return Deferred(check_is_finished)

    def get_asset_libraries(self):
        """List all asset libraries configured in Blender preferences"""
        libraries = []
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            libraries.append(
                {
                    "name": lib.name,
                    "path": lib.path,
                }
            )
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
                blend_files = [
                    f for f in os.listdir(entry_path) if f.endswith(".blend")
                ]
                if blend_files:
                    assets.append(
                        {
                            "name": entry,
                            "blend_file": blend_files[0],
                        }
                    )
            elif entry.endswith(".blend"):
                # Also handle .blend files directly in the library root
                assets.append(
                    {
                        "name": os.path.splitext(entry)[0],
                        "blend_file": entry,
                    }
                )

        # Apply search filter
        if search:
            search_lower = search.lower()
            assets = [a for a in assets if search_lower in a["name"].lower()]

        total = len(assets)
        assets = assets[offset : offset + limit]

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
                if f.endswith(".blend"):
                    blend_path = os.path.join(asset_dir, f)
                    break
        else:
            # Check for .blend file directly in library root
            candidate = os.path.join(library_path, asset_name + ".blend")
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
        if hasattr(data_to, "collections"):
            for coll in data_to.collections:
                if coll is not None:
                    bpy.context.scene.collection.children.link(coll)
                    for obj in coll.all_objects:
                        appended_objects.append(obj.name)

        if hasattr(data_to, "objects"):
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
                "SUBSURF": ["levels", "render_levels", "uv_smooth", "quality"],
                "BEVEL": ["width", "segments", "limit_method", "offset_type"],
                "ARRAY": [
                    "count",
                    "use_relative_offset",
                    "use_constant_offset",
                    "relative_offset_displace",
                    "constant_offset_displace",
                ],
                "MIRROR": ["use_axis", "use_bisect_axis", "merge_threshold"],
                "BOOLEAN": ["operation", "solver"],
                "SOLIDIFY": ["thickness", "offset", "use_even_offset"],
                "WIREFRAME": ["thickness", "use_replace", "use_even_offset"],
                "DECIMATE": ["decimate_type", "ratio", "angle_limit"],
                "REMESH": ["mode", "octree_depth", "voxel_size"],
                "SMOOTH": ["factor", "iterations"],
                "SHRINKWRAP": ["wrap_method", "wrap_mode", "offset"],
                "CURVE": ["deform_axis"],
            }

            props_to_read = prop_map.get(mod.type, [])
            for prop_name in props_to_read:
                try:
                    val = getattr(mod, prop_name)
                    # Convert Blender types to JSON-serializable
                    if isinstance(val, bpy.types.ID):
                        val = val.name if val else None
                    elif hasattr(val, "__iter__") and not isinstance(val, str):
                        val = list(val)
                    mod_info["properties"][prop_name] = val
                except AttributeError:
                    pass

            # Geometry Nodes special handling
            if mod.type == "NODES" and mod.node_group:
                mod_info["node_group"] = mod.node_group.name
                mod_info["inputs"] = []
                for item in mod.node_group.interface.items_tree:
                    if item.item_type == "SOCKET" and item.in_out == "INPUT":
                        input_info = {
                            "identifier": item.identifier,
                            "name": item.name,
                            "socket_type": item.socket_type,
                        }
                        try:
                            val = mod[item.identifier]
                            if isinstance(val, bpy.types.ID):
                                val = val.name if val else None
                            elif hasattr(val, "__iter__") and not isinstance(val, str):
                                val = list(val)
                            input_info["value"] = val
                        except (KeyError, TypeError):
                            input_info["value"] = None
                        mod_info["inputs"].append(input_info)

            modifiers.append(mod_info)

        return modifiers

    def add_modifier(
        self, object_name, modifier_type, modifier_name=None, properties=None
    ):
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
        for key in properties or {}:
            try:
                val = getattr(mod, key)
                if hasattr(val, "__iter__") and not isinstance(val, str):
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
            "window": window,
            "screen": window.screen,
            "area": window.screen.areas[0],
            "object": obj,
            "active_object": obj,
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

        if mod.type != "NODES":
            raise ValueError(
                f"Modifier '{modifier_name}' is not a Geometry Nodes modifier (type: {mod.type})"
            )

        if not mod.node_group:
            raise ValueError(f"Modifier '{modifier_name}' has no node group assigned")

        # Find input by identifier or display name
        target_identifier = None
        for item in mod.node_group.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "INPUT":
                if item.identifier == input_name or item.name == input_name:
                    target_identifier = item.identifier
                    break

        if target_identifier is None:
            available = [
                f"{item.identifier} ({item.name})"
                for item in mod.node_group.interface.items_tree
                if item.item_type == "SOCKET" and item.in_out == "INPUT"
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

        if socket_type == "NodeSocketObject" and isinstance(value, str):
            ref = bpy.data.objects.get(value)
            if ref is None:
                raise ValueError(f"Object not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == "NodeSocketCollection" and isinstance(value, str):
            ref = bpy.data.collections.get(value)
            if ref is None:
                raise ValueError(f"Collection not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == "NodeSocketMaterial" and isinstance(value, str):
            ref = bpy.data.materials.get(value)
            if ref is None:
                raise ValueError(f"Material not found: '{value}'")
            mod[target_identifier] = ref
        elif socket_type == "NodeSocketImage" and isinstance(value, str):
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
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="MCP Server", icon="NETWORK_DRIVE")

        row = box.row()
        row.prop(scene, "blendermcp_port")

        global _server
        if _server and _server.running:
            row = box.row()
            row.label(
                text=f"Status: Running on port {scene.blendermcp_port}",
                icon="CHECKMARK",
            )
            row = box.row()
            row.operator("blendermcp.stop_server", icon="PAUSE")
        else:
            row = box.row()
            row.label(text="Status: Stopped", icon="CANCEL")
            row = box.row()
            row.operator("blendermcp.start_server", icon="PLAY")


class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    """Start the MCP server"""

    bl_idname = "blendermcp.start_server"
    bl_label = "Start Server"

    def execute(self, context):
        global _server
        if not _server:
            _server = BlenderMCPServer(port=context.scene.blendermcp_port)

        _server.start()
        self.report(
            {"INFO"}, f"MCP Server started on port {context.scene.blendermcp_port}"
        )
        return {"FINISHED"}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    """Stop the MCP server"""

    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop Server"

    def execute(self, context):
        global _server
        if _server:
            _server.stop()
            self.report({"INFO"}, "MCP Server stopped")
        return {"FINISHED"}


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

    def _autostart_server():
        global _server
        if not _server:
            _server = BlenderMCPServer(port=9876)
        _server.start()
        return None

    bpy.app.timers.register(_autostart_server, first_interval=0.1)


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
