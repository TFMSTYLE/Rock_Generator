# SPDX-FileCopyrightText: 2026 The French Monkey
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lets an AI agent use this addon, through MCP, with nothing else installed.

Every TFMSTYLE addon carries a copy of this file. The first to start opens one door
on this PC and the others join it, so one address reaches them all. It stays closed
until the person turns it on, takes connections from this PC only, and runs only the
addons' own actions. Requests are answered on a Blender timer, so there are no
threads, and nothing sent to it is ever run as code.

Settings and ranges are read from Blender at each call. agent_guide.json, next to this
file, holds what a person learns by looking at the panel.
"""

import contextlib
import io
import json
import re
import socket
import time
import tomllib
import types
from pathlib import Path

import bpy
from mathutils import Vector

ADDRESS = ("127.0.0.1", 9877)
URL = f"http://{ADDRESS[0]}:{ADDRESS[1]}/mcp"
SHARED = "tfmstyle_agent_door"
SAVED = "tfmstyle_agent_door.json"
LOCAL = ("127.0.0.1", "localhost")
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_REQUEST = 1024 * 1024
CLIENT_TIMEOUT = 10.0

# These reach outside Blender or open a dialog that nobody is there to answer.
NEVER = re.compile(r"import|export|browse|url|link|open|file", re.I)
# These take work away, so the caller has to ask for it twice.
CONFIRM = re.compile(r"delete|clear|reset|remove", re.I)
# How a panel draws a setting. A name it never draws is the addon's private state.
PANEL_PROP = re.compile(r"\.prop\(\s*\w+\s*,\s*[\"'](\w+)[\"']")
# Open and close a section of a panel: a person's view of it, not a setting.
SECTION = re.compile(r"^show_")
# What Blender prints for an operator's own message. The rest is an addon's debug text.
REPORT = re.compile(r"^(Info|Warning|Error)\b")
SETTABLE = ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM")
SHOWN_ITEMS = 20
CURVE_SAMPLES = 12
HUGE_MESH = 100_000

INSTRUCTIONS = (
    "Uses the TFMSTYLE addons in a running Blender. Start with list_addons, then "
    "describe_addon before the first run_addon of an addon, and follow its guide: "
    "settings and ranges are read from Blender and can differ between versions. "
    "These tools only run the addons. Deleting or moving objects, setting keyframes "
    "and changing the frame need Blender's own tools, so say so if none are connected."
)
TOOLS = [
    dict(
        name="list_addons",
        description="List the TFMSTYLE addons running in Blender, with their versions.",
        inputSchema=dict(type="object", properties={}),
        annotations=dict(title="List TFMSTYLE addons", readOnlyHint=True),
    ),
    dict(
        name="describe_addon",
        description=(
            "Show one addon's actions and settings, with current values, ranges and a guide. "
            "`applies` says whether a setting does anything for the current choices. "
            "Settings kept per item appear under `lists`: set those with the name list[index].setting."
        ),
        inputSchema=dict(type="object", properties=dict(addon=dict(type="string")), required=["addon"]),
        annotations=dict(title="Describe a TFMSTYLE addon", readOnlyHint=True),
    ),
    dict(
        name="run_addon",
        description=(
            "Apply settings to an addon, run one of its actions, or both. Everything is checked first: "
            "one bad value refuses the whole call and changes nothing. An action that clears or resets "
            "something needs confirm=true. Settings stay as set, as if a person had moved the slider. "
            "The reply measures what changed under new_objects and changed_objects. ok only says the "
            "action ran, so read those and any warnings to judge the result."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(addon=dict(type="string"), action=dict(type="string"), settings=dict(type="object"), confirm=dict(type="boolean")),
            required=["addon"],
        ),
        annotations=dict(title="Run a TFMSTYLE addon", destructiveHint=True),
    ),
]

_key = None
_operator = None


def door():
    """What every addon's copy of this file shares."""
    return bpy.app.driver_namespace.setdefault(SHARED, dict(addons={}, listener=None, clients=[], error=""))


def is_open():
    return door()["listener"] is not None


def saved_path():
    return Path(bpy.utils.user_resource("CONFIG")) / SAVED


def saved_open():
    try:
        return json.loads(saved_path().read_text(encoding="utf-8")).get("open") is True
    except (OSError, ValueError, AttributeError):
        return False


def save_open(value):
    path = saved_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(open=value)), encoding="utf-8")


def open_door():
    shared = door()
    if shared["listener"] is not None or bpy.app.background:
        return None
    if not bpy.app.online_access:
        shared["error"] = "Needs Allow Online Access in Preferences, System"
        return None
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(ADDRESS)
        listener.listen(8)
    except OSError as e:
        listener.close()
        shared["error"] = f"Could not open port {ADDRESS[1]}: {e}"
        return None
    listener.setblocking(False)
    shared.update(listener=listener, error="")
    bpy.app.timers.register(tick, first_interval=0.1, persistent=True)
    return None


def close_door():
    shared = door()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    for client in shared["clients"]:
        client["conn"].close()
    shared["clients"].clear()
    if shared["listener"] is not None:
        shared["listener"].close()
        shared["listener"] = None


def tick():
    """Blender's main thread, between redraws. A running action holds Blender up, the
    same as pressing its button would."""
    shared = door()
    listener = shared["listener"]
    if listener is None:
        return None
    with contextlib.suppress(BlockingIOError):
        while True:
            conn, _ = listener.accept()
            conn.setblocking(False)
            shared["clients"].append(dict(conn=conn, data=bytearray(), since=time.monotonic()))
    for client in list(shared["clients"]):
        if serve(client):
            client["conn"].close()
            shared["clients"].remove(client)
    return 0.05 if shared["clients"] else 0.2


def serve(client):
    """Read what has arrived, and answer once the request is whole. True when done."""
    try:
        chunk = client["conn"].recv(65536)
        if not chunk:
            return True
        client["data"].extend(chunk)
    except BlockingIOError:
        return time.monotonic() - client["since"] > CLIENT_TIMEOUT
    except OSError:
        return True
    if len(client["data"]) > MAX_REQUEST:
        send(client, 413)
        return True
    request = parse(bytes(client["data"]))
    if request is None:
        return False
    send(client, *answer(*request))
    return True


def parse(data):
    head, found, body = data.partition(b"\r\n\r\n")
    if not found:
        return None
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    headers = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
    length = int(headers.get("content-length", "0") or 0)
    if len(body) < length:
        return None
    return parts[0], parts[1] if len(parts) > 1 else "", headers, body[:length]


def send(client, status, body=b""):
    reason = {200: "OK", 202: "Accepted", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Too Large"}[status]
    head = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(body)}", "Connection: close"]
    if body:
        head.append("Content-Type: application/json")
    if status == 405:
        head.append("Allow: POST")
    client["conn"].setblocking(True)
    client["conn"].settimeout(5.0)
    with contextlib.suppress(OSError):
        client["conn"].sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)


def from_this_pc(headers):
    """A web page can reach a local port, so its Host and Origin decide. MCP asks for this."""
    host = headers.get("host", "").rsplit(":", 1)[0]
    origin = headers.get("origin", "")
    return host in LOCAL and (not origin or origin.split("//")[-1].split(":")[0] in LOCAL)


def answer(method, path, headers, body):
    if path.split("?")[0] != "/mcp":
        return 404,
    if not from_this_pc(headers):
        return 403,
    if method != "POST":
        return 405,
    try:
        message = json.loads(body)
    except ValueError:
        return 400,
    if not isinstance(message, dict):
        return 400,
    if "id" not in message or "method" not in message:
        return 202,  # a notification
    reply = dict(jsonrpc="2.0", id=message["id"])
    try:
        reply["result"] = handle(message["method"], message.get("params") or {})
    except LookupError as e:
        reply["error"] = dict(code=-32601, message=str(e))
    return 200, json.dumps(reply).encode("utf-8")


def handle(method, params):
    if method == "initialize":
        asked = params.get("protocolVersion")
        return dict(
            protocolVersion=asked if asked in VERSIONS else VERSIONS[0],
            capabilities=dict(tools={}),
            serverInfo=dict(name="tfmstyle-blender-door", version=_manifest()["version"]),
            instructions=INSTRUCTIONS,
        )
    if method == "ping":
        return {}
    if method == "tools/list":
        return dict(tools=TOOLS)
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments") or {})
    raise LookupError(f"Unknown method {method}")


def call_tool(name, arguments):
    tools = dict(list_addons=list_addons, describe_addon=describe_addon, run_addon=run_addon)
    try:
        if name not in tools:
            raise ValueError(f"{name} is not one of: {sorted(tools)}")
        return dict(content=[dict(type="text", text=json.dumps(tools[name](**arguments)))], isError=False)
    except Exception as e:
        return dict(content=[dict(type="text", text=f"{type(e).__name__}: {e}")], isError=True)


def _manifest():
    return tomllib.loads(Path(__file__).with_name("blender_manifest.toml").read_text(encoding="utf-8"))


def _guide():
    try:
        return json.loads(Path(__file__).with_name("agent_guide.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(actions={}, notes=[])


def addons():
    return door()["addons"]


def panels_of(addon):
    return [c for c in bpy.types.Panel.__subclasses__() if c.__module__ == addon["module"]]


def space_of(addon):
    """The editor its panel lives in, which is where its actions expect to run."""
    return next((c.bl_space_type for c in panels_of(addon)), "VIEW_3D")


def window_for(space):
    """Requests arrive on a timer, where Blender has no window in context."""
    windows = bpy.context.window_manager.windows
    if not windows:
        return {}
    window = windows[0]
    for area in window.screen.areas:
        if area.type == space:
            return dict(window=window, area=area, region=next((r for r in area.regions if r.type == "WINDOW"), None))
    return dict(window=window)


def groups_of(addon):
    """The addon's settings groups on the scene."""
    ours = {c.__name__ for c in bpy.types.PropertyGroup.__subclasses__() if c.__module__ == addon["module"]}
    scene = bpy.context.scene
    return [getattr(scene, p.identifier) for p in bpy.types.Scene.bl_rna.properties if p.type == "POINTER" and p.fixed_type.identifier in ours]


def actions_of(addon):
    found = {}
    for cls in bpy.types.Operator.__subclasses__():
        if cls.__module__ != addon["module"] or not cls.is_registered or not hasattr(cls, "execute"):
            continue
        if NEVER.search(cls.bl_idname) or "filepath" in cls.bl_rna.properties:
            continue
        found[cls.bl_idname] = cls
    return found


class Recorder:
    """Stands in for a panel's layout and notes which settings it draws."""

    def __init__(self):
        self.drawn = set()

    def prop(self, data, name, *args, **kwargs):
        self.drawn.add(name)
        return self

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


def drawn_now(addon, groups):
    """The settings its panels show for the current choices, with every section open.
    None when a panel will not draw here, and the caller then marks nothing."""
    closed = [(g, p.identifier) for g in groups for p in g.bl_rna.properties if SECTION.match(p.identifier) and p.type == "BOOLEAN" and not getattr(g, p.identifier)]
    recorder = Recorder()
    try:
        for group, name in closed:
            setattr(group, name, True)
        for panel in panels_of(addon):
            panel.draw(types.SimpleNamespace(layout=recorder), bpy.context)
    except Exception:
        return None
    finally:
        for group, name in closed:
            setattr(group, name, False)
    return recorder.drawn


def settable(rna, public):
    props = [p for p in rna.properties if p.identifier not in ("rna_type", "name") and p.type in SETTABLE and not p.is_readonly and not p.is_hidden]
    return props if public is None else [p for p in props if p.identifier in public]


def lists_of(group):
    return [p for p in group.bl_rna.properties if p.type == "COLLECTION" and p.identifier != "rna_type"]


def enum_values(group, prop):
    values = [i.identifier for i in prop.enum_items]
    if values or group is None:
        return values
    try:  # a callback fills this one, and the refusal lists what it allows
        setattr(group, prop.identifier, "__none__")
    except TypeError as e:
        return re.findall(r"'([^']+)'", str(e).split("not found in", 1)[-1])
    return []


def value_of(group, prop):
    value = getattr(group, prop.identifier)
    if prop.type == "ENUM" and prop.is_enum_flag:
        return sorted(value)
    return list(value) if getattr(prop, "array_length", 0) else value


def default_of(prop):
    if prop.type == "ENUM":
        return sorted(prop.default_flag) if prop.is_enum_flag else prop.default
    return list(prop.default_array) if getattr(prop, "array_length", 0) else prop.default


def field(prop, group=None, drawn=None):
    """One setting. With no group it is the default, for a list with nothing in it yet."""
    item = dict(name=prop.identifier, label=prop.name, type=prop.type.lower(), value=default_of(prop) if group is None else value_of(group, prop))
    if prop.description:
        item["description"] = prop.description
    if prop.type in ("INT", "FLOAT"):
        item.update(min=prop.soft_min, max=prop.soft_max)
    if prop.type == "ENUM":
        item["options"] = enum_values(group, prop)
    if drawn is not None:
        item["applies"] = prop.identifier in drawn
    return item


def number_problem(prop, value):
    whole = prop.type == "INT"
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        return "must be a whole number" if whole else "must be a number"
    if not prop.soft_min <= value <= prop.soft_max:
        return f"must be between {prop.soft_min} and {prop.soft_max}"
    return None


def problem(group, prop, value):
    if prop.type == "BOOLEAN":
        return None if isinstance(value, bool) else "must be true or false"
    if prop.type == "STRING":
        return None if isinstance(value, str) else "must be text"
    if prop.type == "ENUM":
        options = enum_values(group, prop)
        chosen = value if prop.is_enum_flag and isinstance(value, list) else [value]
        return None if all(v in options for v in chosen) else f"must be one of {options}"
    if not prop.array_length:
        return number_problem(prop, value)
    if not isinstance(value, list) or len(value) != prop.array_length:
        return f"must be a list of {prop.array_length} numbers"
    return next(filter(None, (number_problem(prop, v) for v in value)), None)


def resolve(groups, key, public):
    """A plain name, or list[index].name for settings kept per item, like Graph's."""
    match = re.fullmatch(r"(\w+)\[(\d+)\]\.(\w+)", key)
    for group in groups:
        holder, name = group, key
        if match:
            items = getattr(group, match[1], None)
            if not isinstance(items, bpy.types.bpy_prop_collection) or int(match[2]) >= len(items):
                continue
            holder, name = items[int(match[2])], match[3]
        prop = next((p for p in settable(holder.bl_rna, public) if p.identifier == name), None)
        if prop:
            return holder, prop
    return None, None


def refused_addon(key):
    return dict(ok=False, refused=[f"{key} is not one of: {sorted(addons())}"])


def rounded(values):
    return [round(v, 3) for v in values]


def curves_of(data):
    if not hasattr(data.action, "layers"):
        return list(data.action.fcurves)
    found = []
    for layer in data.action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(data.action_slot)
            found += list(bag.fcurves) if bag else []
    return found


def channels(obj):
    """What keyframes the object has, one row per animated value, with enough points
    along the way to see its shape."""
    data = obj.animation_data
    if not data or not data.action or not getattr(data, "action_slot", True):
        return {}
    found = {}
    for curve in curves_of(data):
        points = [k.co for k in curve.keyframe_points]
        if points:
            step = max(1, len(points) // CURVE_SAMPLES)
            found[f"{curve.data_path}[{curve.array_index}]"] = dict(
                keys=len(points),
                lowest=round(min(p.y for p in points), 3),
                highest=round(max(p.y for p in points), 3),
                frames_and_values=[rounded(p) for p in points[::step]] + [rounded(points[-1])],
            )
    return found


def measure(obj):
    row = dict(name=obj.name_full, type=obj.type, location=rounded(obj.location), size=rounded(obj.dimensions))
    row["lowest_z"] = round(min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box), 3)
    if obj.type == "MESH":
        row["vertices"] = len(obj.data.vertices)
        if obj.modifiers:
            row["vertices_with_modifiers"] = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
    keys = channels(obj)
    if keys:
        row["keyframes"] = keys
    return row


def snapshot():
    bpy.context.view_layer.update()
    return {o.name_full: measure(o) for o in bpy.data.objects}


def stored(groups):
    """Everything an addon holds, so saving something counts as a change."""
    return [(p.identifier, value_of(g, p)) for g in groups for p in settable(g.bl_rna, None)]


def list_addons():
    rows = [dict(addon=k, name=v["name"], version=v["version"], editor=space_of(v)) for k, v in sorted(addons().items())]
    return dict(addons=rows, has_window=not bpy.app.background)


def describe_addon(addon):
    info = addons().get(addon)
    if info is None:
        return refused_addon(addon)
    space = space_of(info)
    public = info["public"]
    with bpy.context.temp_override(**window_for(space)):
        groups = groups_of(info)
        drawn = drawn_now(info, groups)
        settings = [field(p, g, drawn) for g in groups for p in settable(g.bl_rna, public)]
        lists = []
        for group in groups:
            for prop in lists_of(group):
                items = getattr(group, prop.identifier)
                shape = settable(prop.fixed_type, public)
                if not shape:
                    continue
                fields = [field(p, items[0], drawn) for p in shape] if len(items) else [field(p) for p in shape]
                lists.append(dict(name=prop.identifier, label=prop.name, count=len(items), fields=fields, items=[{p.identifier: value_of(i, p) for p in shape} for i in items[:SHOWN_ITEMS]]))
    written = info["guide"].get("actions", {})
    actions = [dict(id=i, label=c.bl_label, description=getattr(c, "bl_description", "") or written.get(i, ""), needs_confirm=bool(CONFIRM.search(i))) for i, c in sorted(actions_of(info).items())]
    return dict(addon=addon, name=info["name"], version=info["version"], editor=space, guide=info["guide"].get("notes", []), actions=actions, settings=settings, lists=lists)


def run_addon(addon, action="", settings=None, confirm=False):
    info = addons().get(addon)
    if info is None:
        return refused_addon(addon)
    public = info["public"]
    with bpy.context.temp_override(**window_for(space_of(info))):
        groups = groups_of(info)
        plan, refused = [], []
        for key, value in (settings or {}).items():
            holder, prop = resolve(groups, key, public)
            why = "is not a setting of this addon" if prop is None else problem(holder, prop, value)
            if why:
                refused.append(f"{key} {why}")
            else:
                plan.append((holder, prop.identifier, value))
        actions = actions_of(info)
        if action and action not in actions:
            refused.append(f"{action} is not one of: {sorted(actions)}")
        elif action and CONFIRM.search(action) and not confirm:
            refused.append(f"{action} takes work away: pass confirm=true if that is what you want")
        elif action and hasattr(actions[action], "modal") and any(getattr(g, "is_running", False) for g in groups):
            refused.append(f"{action} is already running: set is_running to false first")
        if refused or not (plan or action):
            return dict(ok=False, refused=refused or ["Nothing to do: pass an action, settings or both"])

        before, held = snapshot(), stored(groups)
        log = io.StringIO()
        status = []
        error = None
        try:
            with contextlib.redirect_stdout(log):
                for holder, name, value in plan:
                    setattr(holder, name, set(value) if isinstance(value, list) and holder.bl_rna.properties[name].type == "ENUM" else value)
                if action:
                    group, name = action.split(".", 1)
                    status = sorted(getattr(getattr(bpy.ops, group), name)("EXEC_DEFAULT"))
        except Exception as e:
            error = str(e)[-800:]
        after = snapshot()
        same = stored(groups) == held

    lines = log.getvalue().splitlines()
    reply = dict(ok=error is None and (not action or bool({"FINISHED", "RUNNING_MODAL"} & set(status))), result=status, messages=list(dict.fromkeys(line for line in lines if REPORT.match(line))))
    if error:
        reply.update(error=error, settings_set=[n for _, n, _ in plan])
    made = [row for name, row in after.items() if name not in before]
    changed = [row for name, row in after.items() if name in before and row != before[name]]
    reply.update(new_objects=made, changed_objects=changed, removed_objects=sorted(set(before) - set(after)))
    warnings = [f"{row['name']} has {row['vertices']:,} vertices, which can slow Blender down: lower the detail settings" for row in made + changed if row.get("vertices", 0) > HUGE_MESH]
    if action and reply["ok"] and "RUNNING_MODAL" not in status and same and not (made or changed or reply["removed_objects"]):
        warnings.append("The action finished, but nothing changed")
    if warnings:
        reply["warnings"] = warnings
    return reply


def _toggle(self, context):
    if is_open():
        close_door()
        save_open(False)
    else:
        open_door()
        save_open(is_open())
        if not is_open():
            self.report({"WARNING"}, door()["error"])
    return {"FINISHED"}


def draw(layout):
    """One line in the addon's panel."""
    if not _operator:
        return
    box = layout.box()
    if is_open():
        box.operator(_operator.bl_idname, text="Close Agent Door", icon="UNLOCKED")
        box.label(text=URL)
    else:
        box.operator(_operator.bl_idname, text="Open Agent Door", icon="LOCKED")
        if door()["error"]:
            box.label(text=door()["error"], icon="ERROR")


def _source():
    return Path(__file__).with_name("__init__.py").read_text(encoding="utf-8")


def register(key):
    global _key, _operator
    _key = key
    manifest = _manifest()
    public = {n for n in PANEL_PROP.findall(_source()) if not SECTION.match(n)}
    addons()[key] = dict(module=__package__, name=manifest["name"], version=manifest["version"], public=public or None, guide=_guide(), take_over=open_door)
    _operator = type(
        f"TFMSTYLE_OT_agent_door_{key}",
        (bpy.types.Operator,),
        {"bl_idname": f"tfmstyle_door.{key}", "bl_label": "Agent Door", "bl_description": "Let AI agents on this PC use the TFMSTYLE addons", "execute": _toggle},
    )
    bpy.utils.register_class(_operator)
    if not is_open() and saved_open() and not bpy.app.background:
        bpy.app.timers.register(open_door, first_interval=1.0)


def unregister():
    global _operator
    if bpy.app.timers.is_registered(open_door):
        bpy.app.timers.unregister(open_door)
    addons().pop(_key, None)
    if _operator:
        bpy.utils.unregister_class(_operator)
        _operator = None
    if bpy.app.timers.is_registered(tick):  # this copy holds the door: pass it on
        close_door()
        for addon in addons().values():
            addon["take_over"]()
            break
