# SPDX-FileCopyrightText: 2025-2026 The French Monkey
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "Rock",
    "author": "The French Monkey",
    "version": (1, 0, 2),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Rock",
    "description": "Generate parametric rocks",
    "category": "Add Mesh",
    "license": "GPL-3.0-or-later",
}

import bpy
import bmesh
import random
import math
import hashlib
from math import sqrt
from mathutils import Vector, noise

try:
    from . import relay_updater
except ImportError:  # the Addon Collection has its own updates
    relay_updater = None

try:
    from . import agent_door
except ImportError:  # the Addon Collection will carry its own door
    agent_door = None

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def _rand_unit_vector(rng: random.Random) -> Vector:
    while True:
        x = rng.uniform(-1.0, 1.0)
        y = rng.uniform(-1.0, 1.0)
        s = x * x + y * y
        if s >= 1.0 or s == 0.0:
            continue
        z = 1 - 2 * s
        f = 2 * math.sqrt(1 - s)
        v = Vector((x * f, y * f, z))
        return v.normalized()


def _safe_noise(v: Vector, seed_offset: int = 0, freq: float = 1.0) -> float:
    ofs = Vector((
        math.sin(seed_offset * 12.9898) * 43758.5453 % 1.0,
        math.sin(seed_offset * 78.233) * 12345.6789 % 1.0,
        math.sin(seed_offset * 45.164) * 9876.5432 % 1.0,
    ))
    return noise.noise((v * freq) + ofs)


def _turbulence_noise(v: Vector, seed_offset: int, octaves: int = 3, freq: float = 1.0) -> float:
    result = 0.0
    amplitude = 1.0
    frequency = freq
    for i in range(octaves):
        result += abs(_safe_noise(v, seed_offset + i * 777, frequency)) * amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return result

# ------------------------------------------------------------------------
# Rock Building
# ------------------------------------------------------------------------

def _build_base_bmesh(base_shape: str, subdivisions: int, size: float) -> bmesh.types.BMesh:
    bm = bmesh.new()
    if base_shape == "CUBE":
        bmesh.ops.create_cube(bm, size=1.0)
        cuts = max(0, min(5, subdivisions))
        if cuts > 0 and bm.edges:
            bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
    elif base_shape == "SPHERE":
        bmesh.ops.create_icosphere(bm, subdivisions=max(1, min(6, subdivisions + 1)), radius=1.0)
    else:  
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=max(8, 16),
            radius1=0.8,
            radius2=0.8,
            depth=2.0
        )
        cuts = max(0, min(3, subdivisions))
        if cuts > 0 and bm.edges:
            bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)

    if size != 1.0:
        for v in bm.verts:
            v.co *= size

    return bm


def _apply_anisotropic_scale(bm: bmesh.types.BMesh, sx: float, sy: float, sz: float):
    if sx == 1.0 and sy == 1.0 and sz == 1.0:
        return
    for v in bm.verts:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz


def _apply_weathering_bulge(bm: bmesh.types.BMesh, strength: float, freq: float, seed: int):
    if strength == 0.0:
        return
    
    for v in bm.verts:
        nval = _turbulence_noise(v.co, seed, octaves=2, freq=freq)
        v.normal_update()
        displacement = (nval - 0.5) * strength
        v.co += v.normal * displacement


def _apply_stratification(bm: bmesh.types.BMesh, layers: int, strength: float, seed: int):
    if layers == 0 or strength == 0.0:
        return
    
    for v in bm.verts:
        layer_pos = v.co.z * layers + _safe_noise(v.co * 0.5, seed) * 0.5
        layer_wave = math.sin(layer_pos * math.pi * 2) * strength
        v.co.z += layer_wave

def _chip_once(
    bm: bmesh.types.BMesh,
    rng: random.Random,
    chip_radius: float,
    chip_normal_jitter: float,
    chip_bias_outward: float,
    noise_freq: float,
    noise_amp: float,
    seed_offset: int,
    edge_chip_strength: float = 0.15,
    edge_chip_scale: float = 2.5,
    adaptive_subdiv: bool = True,
    restrict_bottom: bool = False,
    direction_bias: float = 0.35,
):
    if not bm.verts:
        return bm

    verts = list(bm.verts)
    if not verts:
        return bm

    minv = Vector((min(v.co.x for v in verts), min(v.co.y for v in verts), min(v.co.z for v in verts)))
    maxv = Vector((max(v.co.x for v in verts), max(v.co.y for v in verts), max(v.co.z for v in verts)))
    center = (minv + maxv) * 0.5
    extent = max((maxv - minv).length * 0.5, 1e-6)

    base_axes = [Vector((1,0,0)), Vector((0,1,0)), Vector((0,0,1)), Vector((-1,0,0)), Vector((0,-1,0)), Vector((0,0,-1))]
    if restrict_bottom:
        base_axes = [a for a in base_axes if a.z > -0.1]
    axis = base_axes[int(rng.random() * len(base_axes)) % len(base_axes)]
    direction_bias = max(0.0, min(direction_bias, 0.8))
    n = (_rand_unit_vector(rng) * (1.0 - direction_bias) + axis * direction_bias).normalized()
    if chip_normal_jitter > 0.0:
        n = (n + _rand_unit_vector(rng) * chip_normal_jitter).normalized()
    if restrict_bottom and n.z < -0.2:
        return bm

    r = max(0.05, min(2.0, chip_radius))
    outward_bias = chip_bias_outward * 0.5 + 0.5
    t = (r - 0.2) / (2.0 - 0.2)
    depth_fraction = (0.9 - 0.45 * t) * outward_bias
    depth_fraction = max(0.4, min(depth_fraction, 0.95))

    plane_co = center + n * (extent * depth_fraction)
    nval = noise.noise(plane_co * noise_freq)
    plane_co += n * (nval * noise_amp * extent * 0.4)

    if adaptive_subdiv:
        faces_near_plane = [f for f in bm.faces if abs((f.calc_center_median() - plane_co).dot(n)) < extent * 0.1]
        if faces_near_plane:
            try:
                edges_to_subd = {e for f in faces_near_plane for e in f.edges}
                if edges_to_subd:
                    bmesh.ops.subdivide_edges(bm, edges=list(edges_to_subd), cuts=1, use_grid_fill=True, smooth=0.0)
            except Exception:
                pass

    geom_all = list(bm.verts) + list(bm.edges) + list(bm.faces)
    bmesh.ops.bisect_plane(bm, geom=geom_all, plane_co=plane_co, plane_no=n, clear_inner=False, clear_outer=False, use_snap_center=False)
    faces_to_delete = [f for f in bm.faces if (f.calc_center_median() - plane_co).dot(n) > 0.0]
    if faces_to_delete:
        bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")

    if edge_chip_strength > 0.0:
        boundary_edges = [e for e in bm.edges if e.is_boundary]
        boundary_verts = {v for e in boundary_edges for v in e.verts}
        for v in boundary_verts:
            nval = noise.noise(v.co * (noise_freq * edge_chip_scale) + Vector((seed_offset * 0.13, seed_offset * 0.27, seed_offset * 0.39)))
            offset_dir = (_rand_unit_vector(rng) + v.normal * 0.5).normalized()
            v.co += offset_dir * nval * (noise_amp * extent * edge_chip_strength)

    try:
        boundary_edges = [e for e in bm.edges if e.is_boundary]
        if boundary_edges:
            bmesh.ops.holes_fill(bm, edges=boundary_edges)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    except Exception:
        pass

    return bm


def _micro_surface_noise(bm: bmesh.types.BMesh, amp: float, freq: float, seed: int, octaves: int = 2):
    if amp == 0.0:
        return
    
    for v in bm.verts:
        if not v.is_valid:
            continue
        v.normal_update()
        nrm = v.normal if v.normal.length > 0 else Vector((0, 0, 1))
        
        nval = _turbulence_noise(v.co, seed, octaves=octaves, freq=freq)
        v.co += nrm * (nval * amp * 0.5) 


def _apply_cracking(bm: bmesh.types.BMesh, density: float, depth: float, seed: int):
    if density == 0.0 or depth == 0.0:
        return
    
    rng = random.Random(seed + 5555)
    
    all_edges = list(bm.edges)
    num_cracks = int(len(all_edges) * density)
    
    if num_cracks > 0:
        crack_edges = rng.sample(all_edges, min(num_cracks, len(all_edges)))
        
        for edge in crack_edges:
            for v in edge.verts:
                v.normal_update()
                v.co -= v.normal * (depth * rng.uniform(0.5, 1.0))


def _final_clean(bm: bmesh.types.BMesh, weld_dist: float):
    if not bm.verts:
        return
    
    bbox = [v.co for v in bm.verts]
    diag = max(v.length for v in bbox) if bbox else 1.0
    scaled = weld_dist * max(diag, 1e-6)

    try:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=scaled)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    except Exception:
        pass


def _apply_flat_bottom(bm: bmesh.types.BMesh):
    if not bm.verts:
        return
    
    plane_co = Vector((0, 0, 0))
    plane_no = Vector((0, 0, 1))  
    
    geom_all = list(bm.verts) + list(bm.edges) + list(bm.faces)
    
    try:
        bmesh.ops.bisect_plane(
            bm,
            geom=geom_all,
            plane_co=plane_co,
            plane_no=plane_no,
            clear_inner=True, 
            clear_outer=False, 
            use_snap_center=False,
        )
        
        boundary_edges = [e for e in bm.edges if e.is_boundary]
        if boundary_edges:
            bmesh.ops.holes_fill(bm, edges=boundary_edges)
        
        for v in bm.verts:
            if v.co.z < 0.001:
                v.co.z = 0.0
        
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
    except Exception:
        try:
            boundary_edges = [e for e in bm.edges if e.is_boundary]
            if boundary_edges:
                bmesh.ops.holes_fill(bm, edges=boundary_edges)
        except Exception:
            pass

# ------------------------------------------------------------------------
# Create / rebuild
# ------------------------------------------------------------------------

def _hash_seed(seed: int, i: int) -> int:
    data = f"{seed}_{i}".encode("utf-8")
    return int(hashlib.sha256(data).hexdigest(), 16) % 10_000_000

def create_rock_mesh(
    base_shape: str,
    size: float,
    scale_x: float,
    scale_y: float,
    scale_z: float,
    subdivisions: int,
    seed: int,
    chip_layers: int,
    chip_radius: float,
    chip_normal_jitter: float,
    chip_bias_outward: float,
    chip_noise_freq: float,
    chip_noise_amp: float,
    edge_chip_strength: float,
    edge_chip_scale: float,
    micro_amp: float,
    micro_freq: float,
    micro_octaves: int,
    weathering_strength: float,
    weathering_freq: float,
    stratification_layers: int,
    stratification_strength: float,
    crack_density: float,
    crack_depth: float,
    weld_dist: float,
    adaptive_subdiv: bool,
    flat_bottom: bool,
):
    rng = random.Random(seed)

    bm = _build_base_bmesh(base_shape, subdivisions, size)
    _apply_anisotropic_scale(bm, scale_x, scale_y, scale_z)

    _apply_weathering_bulge(bm, weathering_strength, weathering_freq, seed)
    _apply_stratification(bm, stratification_layers, stratification_strength, seed)

    if base_shape == "SPHERE":
        direction_bias = 0.0
    elif base_shape == "CYLINDER":
        direction_bias = 0.2
    else:  
        direction_bias = 0.35

    chip_layers = max(1, chip_layers)
    for i in range(chip_layers):
        layer_seed = _hash_seed(seed, i)
        local_rng = random.Random(layer_seed)

        offset_jitter = Vector((
            local_rng.uniform(-0.5, 0.5),
            local_rng.uniform(-0.5, 0.5),
            local_rng.uniform(-0.5, 0.5),
        )) * 0.1

        _chip_once(
            bm=bm,
            rng=local_rng,
            chip_radius=chip_radius,
            chip_normal_jitter=chip_normal_jitter,
            chip_bias_outward=chip_bias_outward,
            noise_freq=chip_noise_freq,
            noise_amp=chip_noise_amp,
            seed_offset=layer_seed,
            edge_chip_strength=edge_chip_strength,
            edge_chip_scale=edge_chip_scale,
            adaptive_subdiv=adaptive_subdiv,
            restrict_bottom=flat_bottom,
            direction_bias=direction_bias,
        )

        for v in bm.verts:
            v.co += offset_jitter * 0.2

    _apply_cracking(bm, crack_density, crack_depth, seed)
    _micro_surface_noise(
        bm,
        amp=micro_amp,
        freq=micro_freq,
        seed=seed + 999,
        octaves=micro_octaves,
    )

    if flat_bottom:
        _apply_flat_bottom(bm)

    _final_clean(bm, weld_dist=weld_dist)

    mesh = bpy.data.meshes.new("RockMesh")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return mesh

def rebuild_rock_object(obj: bpy.types.Object):
    if not obj or obj.type != 'MESH':
        return False

    params = {
        'size': float(obj.get("size", 2.0)),
        'subdivisions': int(obj.get("subdivisions", 2)),
        'base_shape': str(obj.get("base_shape", "CUBE")),
        'seed': int(obj.get("seed", 0)),
        'scale_x': float(obj.get("scale_x", 1.0)),
        'scale_y': float(obj.get("scale_y", 1.0)),
        'scale_z': float(obj.get("scale_z", 1.0)),
        'chip_layers': int(obj.get("chip_layers", 24)),
        'chip_radius': float(obj.get("chip_radius", 1.0)),
        'chip_normal_jitter': float(obj.get("chip_normal_jitter", 0.25)),
        'chip_bias_outward': float(obj.get("chip_bias_outward", 0.8)),
        'chip_noise_freq': float(obj.get("chip_noise_freq", 0.75)),
        'chip_noise_amp': float(obj.get("chip_noise_amp", 0.08)),
        'edge_chip_strength': float(obj.get("edge_chip_strength", 0.15)),
        'edge_chip_scale': float(obj.get("edge_chip_scale", 2.5)),
        'micro_amp': float(obj.get("micro_amp", 0.01)),
        'micro_freq': float(obj.get("micro_freq", 3.0)),
        'micro_octaves': int(obj.get("micro_octaves", 2)),
        'weathering_strength': float(obj.get("weathering_strength", 0.0)),
        'weathering_freq': float(obj.get("weathering_freq", 1.0)),
        'stratification_layers': int(obj.get("stratification_layers", 0)),
        'stratification_strength': float(obj.get("stratification_strength", 0.02)),
        'crack_density': float(obj.get("crack_density", 0.0)),
        'crack_depth': float(obj.get("crack_depth", 0.005)),
        'weld_dist': float(obj.get("weld_dist", 0.0005)),
        'adaptive_subdiv': bool(obj.get("adaptive_subdiv", True)),
        'flat_bottom': bool(obj.get("flat_bottom", False)),
    }

    new_mesh = create_rock_mesh(**params)

    old = obj.data
    obj.data = new_mesh
    obj.name = obj.data.name = "Rock"
    if old and old.users == 0:
        bpy.data.meshes.remove(old)
    return True

# ------------------------------------------------------------------------
#  Live Update
# ------------------------------------------------------------------------

def _update_rock(self, context):
    p = context.scene.rockgen_props
    if not p.live_update:
        return

    o = context.active_object
    if not (o and o.type == "MESH" and "rock_generator" in o and o.select_get()):
        return

    prop_keys = (
        "size", "subdivisions", "base_shape", "seed",
        "scale_x", "scale_y", "scale_z",
        "chip_layers", "chip_radius", "chip_normal_jitter",
        "chip_bias_outward", "chip_noise_freq", "chip_noise_amp",
        "edge_chip_strength", "edge_chip_scale",
        "micro_amp", "micro_freq", "micro_octaves",
        "weathering_strength", "weathering_freq",
        "stratification_layers", "stratification_strength",
        "crack_density", "crack_depth",
        "weld_dist", "adaptive_subdiv", "flat_bottom"
    )
    
    for key in prop_keys:
        o[key] = getattr(p, key)

    rebuild_rock_object(o)
    
# ------------------------------------------------------------------------
# Properties 
# ------------------------------------------------------------------------

class RockGenProperties(bpy.types.PropertyGroup):
    
    # Live update
    live_update: bpy.props.BoolProperty(name="Live Update", default=False, description="Automatically regenerate rock when parameters change")

    # Base properties
    base_shape: bpy.props.EnumProperty(name="Base Shape", items=[("CUBE", "Cube", "Cubic base shape"), ("SPHERE", "Sphere", "Spherical base shape"), ("CYLINDER", "Cylinder", "Cylindrical base shape (good for tall rocks)")], default="CUBE", update=_update_rock)
    size: bpy.props.FloatProperty(name="Size", default=2.0, min=0.1, max=50.0, subtype='DISTANCE', update=_update_rock, description="Overall size of the rock")
    subdivisions: bpy.props.IntProperty(name="Subdivisions", default=0, min=0, max=5, update=_update_rock, description="Base mesh detail (higher = more geometry, slower)")
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0, max=999999, update=_update_rock, description="Random seed for reproducible results")

    # Anisotropic scale
    scale_x: bpy.props.FloatProperty(name="Scale X", default=1.0, min=0.1, max=10.0, update=_update_rock, description="Non-uniform scaling on X axis")
    scale_y: bpy.props.FloatProperty(name="Scale Y", default=1.0, min=0.1, max=10.0, update=_update_rock, description="Non-uniform scaling on Y axis")
    scale_z: bpy.props.FloatProperty(name="Scale Z", default=1.0, min=0.1, max=10.0, update=_update_rock, description="Non-uniform scaling on Z axis")

    # Erosive chipping
    chip_layers: bpy.props.IntProperty(name="Chip Layers", default=24, min=1, max=256, update=_update_rock, description="Number of erosion cuts (more = rougher surface, slower)")
    chip_radius: bpy.props.FloatProperty(name="Chip Radius", default=1.0, min=0.2, max=2.0, update=_update_rock, description="How deep cuts penetrate (lower = more aggressive erosion)")
    chip_normal_jitter: bpy.props.FloatProperty(name="Normal Jitter", default=0.25, min=0.0, max=1.0, update=_update_rock, description="Random variation of cut angles")
    chip_bias_outward: bpy.props.FloatProperty(name="Outward Bias", default=0.8, min=0.0, max=1.0, update=_update_rock, description="Prefer cutting outer parts (higher keeps more core intact)")
    chip_noise_freq: bpy.props.FloatProperty(name="Cut Noise Freq", default=0.75, min=0.01, max=10.0, update=_update_rock, description="Frequency of noise applied to cut planes")
    chip_noise_amp: bpy.props.FloatProperty(name="Cut Noise Amp", default=0.08, min=0.0, max=0.5, update=_update_rock, description="Amplitude of noise applied to cut planes")
    edge_chip_strength: bpy.props.FloatProperty(name="Edge Chip Strength", default=0.15, min=0.0, max=1.0, update=_update_rock, description="Intensity of fractured edge displacement")
    edge_chip_scale: bpy.props.FloatProperty(name="Edge Chip Scale", default=2.5, min=0.1, max=10.0, update=_update_rock, description="Detail scale of chipped edges")

    # Weathering
    weathering_strength: bpy.props.FloatProperty(name="Weathering Strength", default=0.0, min=0.0, max=0.5, update=_update_rock, description="Organic bulges and erosion patterns for weathered look")
    weathering_freq: bpy.props.FloatProperty(name="Weathering Frequency", default=1.0, min=0.1, max=5.0, update=_update_rock, description="Scale of weathering patterns")

    # Stratification
    stratification_layers: bpy.props.IntProperty(name="Strata Layers", default=0, min=0, max=20, update=_update_rock, description="Number of sedimentary layers (0 = disabled)")
    stratification_strength: bpy.props.FloatProperty(name="Strata Strength", default=0.02, min=0.0, max=0.2, update=_update_rock, description="Depth of stratification grooves")

    # Surface cracks
    crack_density: bpy.props.FloatProperty(name="Crack Density", default=0.0, min=0.0, max=0.3, update=_update_rock, description="How many surface cracks to add (0 = none)")
    crack_depth: bpy.props.FloatProperty(name="Crack Depth", default=0.005, min=0.0, max=0.05, update=_update_rock, description="Depth of surface cracks")

    # Surface detail
    micro_amp: bpy.props.FloatProperty(name="Surface Detail Amp", default=0.01, min=0.0, max=0.2, update=_update_rock, description="Amplitude of micro surface detail")
    micro_freq: bpy.props.FloatProperty(name="Surface Detail Freq", default=3.0, min=0.01, max=20.0, update=_update_rock, description="Frequency of micro surface detail")
    micro_octaves: bpy.props.IntProperty(name="Detail Octaves", default=2, min=1, max=4, update=_update_rock, description="Complexity of surface detail (higher = more detailed, slower)")

    # Performance
    adaptive_subdiv: bpy.props.BoolProperty(name="Adaptive Subdivision", default=False, update=_update_rock, description="Add geometry only where needed (recommended for performance)")

    # Cleanup
    weld_dist: bpy.props.FloatProperty(name="Weld Distance", default=0.0005, min=0.0, max=0.01, update=_update_rock, description="Merge nearby vertices (cleanup)")

    # Flat bottom
    flat_bottom: bpy.props.BoolProperty(name="Flat Bottom", default=False, update=_update_rock, description="Cut everything below ground plane for a flat bottom surface")

    # Section toggles
    show_erosion_settings: bpy.props.BoolProperty(name="Erosion Settings", default=False, description="Show erosion and chipping controls")
    show_weathering_settings: bpy.props.BoolProperty(name="Weathering Settings", default=False, description="Show weathering and realism options")
    show_detail_settings: bpy.props.BoolProperty(name="Surface Detail", default=False, description="Show surface detail and microstructure controls")

# ------------------------------------------------------------------------
# Operators
# ------------------------------------------------------------------------

def _push_props_to_obj(props: RockGenProperties, obj: bpy.types.Object):
    prop_keys = (
        "size", "subdivisions", "base_shape", "seed",
        "scale_x", "scale_y", "scale_z",
        "chip_layers", "chip_radius", "chip_normal_jitter",
        "chip_bias_outward", "chip_noise_freq", "chip_noise_amp",
        "edge_chip_strength", "edge_chip_scale",
        "micro_amp", "micro_freq", "micro_octaves",
        "weathering_strength", "weathering_freq",
        "stratification_layers", "stratification_strength",
        "crack_density", "crack_depth",
        "weld_dist", "adaptive_subdiv", "flat_bottom"
    )
    
    for k in prop_keys:
        obj[k] = getattr(props, k)

def _generate_or_update(context) -> bpy.types.Object:
    p = context.scene.rockgen_props
    obj = context.active_object

    if obj and obj.type == "MESH" and "rock_generator" in obj and obj.select_get():
        _push_props_to_obj(p, obj)
        rebuild_rock_object(obj)
        obj.name = obj.data.name = "Rock"
        return obj

    params = {
        "base_shape": p.base_shape,
        "size": p.size,
        "scale_x": p.scale_x,
        "scale_y": p.scale_y,
        "scale_z": p.scale_z,
        "subdivisions": p.subdivisions,
        "seed": p.seed,
        "chip_layers": p.chip_layers,
        "chip_radius": p.chip_radius,
        "chip_normal_jitter": p.chip_normal_jitter,
        "chip_bias_outward": p.chip_bias_outward,
        "chip_noise_freq": p.chip_noise_freq,
        "chip_noise_amp": p.chip_noise_amp,
        "edge_chip_strength": p.edge_chip_strength,
        "edge_chip_scale": p.edge_chip_scale,
        "micro_amp": p.micro_amp,
        "micro_freq": p.micro_freq,
        "micro_octaves": p.micro_octaves,
        "weathering_strength": p.weathering_strength,
        "weathering_freq": p.weathering_freq,
        "stratification_layers": p.stratification_layers,
        "stratification_strength": p.stratification_strength,
        "crack_density": p.crack_density,
        "crack_depth": p.crack_depth,
        "weld_dist": p.weld_dist,
        "adaptive_subdiv": p.adaptive_subdiv,
        "flat_bottom": p.flat_bottom,  
    }

    mesh = create_rock_mesh(**params)
    new_obj = bpy.data.objects.new("Rock", mesh)
    context.collection.objects.link(new_obj)
    context.view_layer.objects.active = new_obj
    new_obj.select_set(True)
    new_obj["rock_generator"] = True

    _push_props_to_obj(p, new_obj)
    return new_obj

class ROCK_OT_generate(bpy.types.Operator):
    bl_idname = "rock.generate"
    bl_label = "Generate Rock"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _generate_or_update(context)
        return {"FINISHED"}


class ROCK_OT_randomize_seed(bpy.types.Operator):
    bl_idname = "rock.randomize_seed"
    bl_label = "Randomize Seed"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import random
        p = context.scene.rockgen_props
        p.seed = random.randint(0, 999999)
        return {"FINISHED"}
    
class ROCK_OT_reset_settings(bpy.types.Operator):
    bl_idname = "rock.reset_settings"
    bl_label = "Reset Settings"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.rockgen_props

        keep_values = {
            "live_update": props.live_update,
            "show_erosion_settings": props.show_erosion_settings,
            "show_weathering_settings": props.show_weathering_settings,
            "show_detail_settings": props.show_detail_settings,
        }

        for prop_name, prop_def in props.__annotations__.items():
            default_value = prop_def.keywords.get("default", None)
            if default_value is not None:
                setattr(props, prop_name, default_value)

        for name, val in keep_values.items():
            setattr(props, name, val)

        if props.live_update:
            obj = context.active_object
            if obj and obj.type == "MESH" and "rock_generator" in obj:
                _push_props_to_obj(props, obj)
                rebuild_rock_object(obj)

        self.report({'INFO'}, "Rock Generator settings reset to defaults.")
        return {"FINISHED"}

# ------------------------------------------------------------------------
# UI
# ------------------------------------------------------------------------

class VIEW3D_PT_rock_generator(bpy.types.Panel):
    bl_label = "Rock"
    bl_idname = "VIEW3D_PT_rock_generator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Rock" 

    def draw(self, context):
        if agent_door:
            agent_door.draw(self.layout)
        if relay_updater:
            relay_updater.draw(self.layout)
        layout = self.layout
        p = context.scene.rockgen_props
        o = context.object

        layout.operator("rock.generate", text="Generate Rock", icon="META_CUBE")

        row = layout.row(align=True)
        row.prop(p, "live_update", toggle=True)
        row.operator("rock.reset_settings", text="Reset")

        layout.separator(factor=0.5)

        layout.prop(p, "base_shape", expand=True)
        layout.prop(p, "seed", slider=True)

        layout.prop(p, "size", slider=True)
        layout.prop(p, "subdivisions", slider=True)

        col = layout.column(align=True)
        col.prop(p, "scale_x", slider=True)
        col.prop(p, "scale_y", slider=True)
        col.prop(p, "scale_z", slider=True)

        layout.separator(factor=0.5)

        layout.prop(p, "show_erosion_settings", toggle=True, text="Erosion Settings")
        if p.show_erosion_settings:
            col = layout.column(align=True)
            col.prop(p, "chip_layers", slider=True)
            col.prop(p, "chip_radius", slider=True)
            col.prop(p, "chip_normal_jitter", slider=True)
            col.prop(p, "chip_bias_outward", slider=True)

            col.separator(factor=0.5)
            col.prop(p, "chip_noise_freq", slider=True)
            col.prop(p, "chip_noise_amp", slider=True)

            col.separator(factor=0.5)
            col.prop(p, "edge_chip_strength", slider=True)
            col.prop(p, "edge_chip_scale", slider=True)

        layout.prop(p, "show_weathering_settings", toggle=True, text="Weathering Settings")
        if p.show_weathering_settings:
            col = layout.column(align=True)
            col.prop(p, "weathering_strength", slider=True)
            col.prop(p, "weathering_freq", slider=True)

            col.separator(factor=0.5)
            col.prop(p, "stratification_layers", slider=True)
            if p.stratification_layers > 0:
                col.prop(p, "stratification_strength", slider=True)

            col.separator(factor=0.5)
            col.prop(p, "crack_density", slider=True)
            if p.crack_density > 0:
                col.prop(p, "crack_depth", slider=True)

        layout.prop(p, "show_detail_settings", toggle=True, text="Surface Detail")
        if p.show_detail_settings:
            col = layout.column(align=True)
            col.prop(p, "micro_amp", slider=True)
            col.prop(p, "micro_freq", slider=True)
            col.prop(p, "micro_octaves", slider=True)
            col.prop(p, "weld_dist", slider=True)

        layout.separator(factor=0.5)

        col = layout.column(align=True)
        col.prop(p, "adaptive_subdiv", toggle=True)
        col.separator(factor=0.5)
        col.prop(p, "flat_bottom", toggle=True)

        layout.separator(factor=0.5)

        if o and o.type == "MESH" and "rock_generator" in o and o.select_get():
            layout.label(text=f"Editing: {o.name}", icon="META_CUBE")
        else:
            layout.label(text="No rock selected", icon="INFO")

# ------------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------------

classes = (
    RockGenProperties,
    ROCK_OT_generate,
    ROCK_OT_randomize_seed,
    ROCK_OT_reset_settings,
    VIEW3D_PT_rock_generator, 
)

def register():
    if relay_updater:
        relay_updater.register("blender-rock")
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.rockgen_props = bpy.props.PointerProperty(type=RockGenProperties)
    if agent_door:
        agent_door.register("rock")

def unregister():
    if agent_door:
        agent_door.unregister()
    if relay_updater:
        relay_updater.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.rockgen_props

if __name__ == "__main__":
    register()