"""Build an editable, self-contained VRM derivative; keep seed-san.vrm intact.

The source meshes are in the inverse-bind A pose, while the node rest pose is a
T pose. New garments transfer the existing body's skin weights in bind space.
Run with Python + numpy + Pillow (scipy accelerates weight transfer if present).
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import struct
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/models/seed-san.vrm"
OUTPUT = ROOT / "web/models/seed-daily.vrm"
SOURCE_DIR = ROOT / "web/models/seed-daily-source"
SOURCE_DIR.mkdir(exist_ok=True)
raw = SOURCE.read_bytes()
json_length = struct.unpack_from("<I", raw, 12)[0]
doc = json.loads(raw[20 : 20 + json_length])
bin_start = 20 + json_length
binary = bytearray(raw[bin_start + 8 : bin_start + 8 + struct.unpack_from("<I", raw, bin_start)[0]])


def read_accessor(index):
    a = doc["accessors"][index]
    v = doc["bufferViews"][a["bufferView"]]
    dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    return np.ndarray((a["count"], width), dtype=dtype, buffer=binary,
                      offset=v.get("byteOffset", 0) + a.get("byteOffset", 0),
                      strides=(v.get("byteStride", np.dtype(dtype).itemsize * width), np.dtype(dtype).itemsize)).copy()


def append_bytes(data, target=None):
    binary.extend(b"\0" * (-len(binary) % 4))
    view = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)}
    if target:
        view["target"] = target
    index = len(doc["bufferViews"])
    doc["bufferViews"].append(view)
    binary.extend(data)
    return index


def accessor(values, kind, integer=False, bounds=False):
    values = np.asarray(values, dtype="<u2" if integer else "<f4")
    a = {"bufferView": append_bytes(values.tobytes(), 34963 if kind == "SCALAR" else 34962),
         "componentType": 5123 if integer else 5126, "count": len(values), "type": kind}
    if bounds:
        a.update(min=values.min(axis=0).tolist(), max=values.max(axis=0).tolist())
    index = len(doc["accessors"])
    doc["accessors"].append(a)
    return index


def linear(hex_color):
    rgb = np.array([int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)])
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4).tolist()


def material(name, color, shade, texture=None, outline=.00045):
    base = linear(color)
    toon = {"specVersion": "1.0", "shadeColorFactor": linear(shade), "shadingToonyFactor": .75,
            "shadingShiftFactor": -.08, "giEqualizationFactor": .9, "outlineWidthMode": "worldCoordinates",
            "outlineWidthFactor": outline, "outlineColorFactor": linear(shade), "outlineLightingMixFactor": .35,
            "parametricRimColorFactor": [0, 0, 0]}
    pbr = {"baseColorFactor": base + [1], "metallicFactor": 0, "roughnessFactor": 1}
    if texture is not None:
        pbr["baseColorTexture"] = {"index": texture}
        toon["shadeMultiplyTexture"] = {"index": texture}
    index = len(doc["materials"])
    doc["materials"].append({"name": name, "doubleSided": True, "alphaMode": "OPAQUE",
                             "pbrMetallicRoughness": pbr,
                             "extensions": {"KHR_materials_unlit": {}, "VRMC_materials_mtoon": toon}})
    return index


def texture_image(name, image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    data = stream.getvalue()
    (SOURCE_DIR / (name + ".png")).write_bytes(data)
    image_index = len(doc["images"])
    doc["images"].append({"bufferView": append_bytes(data), "mimeType": "image/png", "name": name})
    texture_index = len(doc["textures"])
    doc["textures"].append({"source": image_index, "sampler": 0})
    return texture_index


# A restrained rib knit: no baked shadows, logos, hardware or badge assets.
knit = Image.new("RGB", (256, 256), (255, 255, 255))
drawing = ImageDraw.Draw(knit)
for x in range(0, 256, 8):
    drawing.line([(x, 0), (x, 256)], fill=(238, 240, 240), width=2)
    drawing.line([(x + 3, 0), (x + 3, 256)], fill=(249, 250, 250))
knit_texture = texture_image("rib-knit", knit)
hair_mat = material("Daily chestnut hair", "57423f", "302d35", outline=.00055)
hair_light = material("Daily hair soft strands", "65504a", "382f34", outline=0)
coat_mat = material("Daily cream jacket", "e9dfcf", "b5b1aa", outline=.0006)
collar_mat = material("Daily folded collar", "f2e8da", "bab3a9", outline=.00045)
knit_mat = material("Daily slate knit", "536670", "394a54", knit_texture, outline=.0003)
hem_mat = material("Daily knit edges", "485b65", "354750", knit_texture, outline=.0003)
pants_mat = material("Daily charcoal trousers", "4a505d", "303641", outline=.0005)
skin_mat = material("Daily natural arms and palms", "ffe8ce", "e0b8a6", outline=.0005)

wear = doc["meshes"][4]
body_parts = wear["primitives"][:2]
body_positions = np.concatenate([read_accessor(p["attributes"]["POSITION"]) for p in body_parts])
body_joints = np.concatenate([read_accessor(p["attributes"]["JOINTS_0"]) for p in body_parts])
body_weights = np.concatenate([read_accessor(p["attributes"]["WEIGHTS_0"]) for p in body_parts])
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from scipy.spatial import cKDTree
    weight_tree = cKDTree(body_positions)
except (ImportError, ValueError):
    weight_tree = None


def skin_weights(positions):
    if weight_tree is not None:
        distances, nearest = weight_tree.query(positions, k=8)
    else:
        nearest_batches, distance_batches = [], []
        for start in range(0, len(positions), 128):
            distance2 = ((np.asarray(positions)[start:start + 128, None] - body_positions[None]) ** 2).sum(axis=-1)
            nearest_batch = np.argpartition(distance2, 8, axis=-1)[:, :8]
            nearest_batches.append(nearest_batch)
            distance_batches.append(np.sqrt(np.take_along_axis(distance2, nearest_batch, axis=-1)))
        nearest = np.concatenate(nearest_batches)
        distances = np.concatenate(distance_batches)
    influence = 1 / np.maximum(distances, .0005) ** 2
    influence /= influence.sum(axis=1, keepdims=True)
    joints, weights = [], []
    for idx, blend in zip(nearest, influence):
        pool = {}
        for vertex, amount in zip(idx, blend):
            for bone, weight in zip(body_joints[vertex], body_weights[vertex]):
                pool[int(bone)] = pool.get(int(bone), 0) + float(weight * amount)
        top = sorted(pool.items(), key=lambda p: p[1], reverse=True)[:4]
        top += [(0, 0)] * (4 - len(top))
        total = sum(p[1] for p in top)
        joints.append([p[0] for p in top])
        weights.append([p[1] / total for p in top])
    return joints, weights


stats = []


def mesh(name, verts, faces, mat, uv=None, head=False):
    verts = np.asarray(verts, dtype=np.float64)
    triangles = []
    for f in faces:
        for i in range(1, len(f) - 1):
            triangles.append([f[0], f[i], f[i + 1]])
    triangles = np.asarray(triangles, dtype=np.uint16)
    normals = np.zeros_like(verts)
    for a, b, c in triangles:
        normal = np.cross(verts[b] - verts[a], verts[c] - verts[a])
        for i in (a, b, c):
            normals[i] += normal
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-10)
    if head:
        joints = [[37, 0, 0, 0] for _ in verts]
        weights = [[1, 0, 0, 0] for _ in verts]
    else:
        joints, weights = skin_weights(verts)
        # Transfer respects garment anatomy: finger movement must not tug cuffs,
        # opposite thighs must not pull a trouser leg, and neck ribbing belongs
        # to the chest rather than the nearby shoulder surface.
        humanoid = doc["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]
        skin_joint_nodes = doc["skins"][4]["joints"]
        def joint_for(bone):
            return skin_joint_nodes.index(humanoid[bone]["node"])
        for i in range(len(verts)):
            pool = {}
            for joint, weight in zip(joints[i], weights[i]):
                if "jacket sleeve" in name or "ribbed cuff" in name:
                    if 35 <= joint <= 54:
                        joint = joint_for("leftHand")
                    elif 60 <= joint <= 78:
                        joint = joint_for("rightHand")
                if "straight trouser leg" in name:
                    side = "left" if name.endswith(" 1") else "right"
                    allowed = {joint_for("hips"), joint_for("spine")}
                    allowed.update(joint_for(side + part) for part in ("UpperLeg", "LowerLeg", "Foot", "Toes"))
                    if joint not in allowed:
                        joint = joint_for("hips")
                if "ribbed neckline" in name or "soft collar" in name or ("crew neck" in name and verts[i, 1] > 1.25):
                    joint = joint_for("chest")
                pool[joint] = pool.get(joint, 0) + weight
            top = sorted(pool.items(), key=lambda p: p[1], reverse=True)
            top += [(0, 0)] * (4 - len(top))
            total = sum(p[1] for p in top)
            joints[i] = [p[0] for p in top]
            weights[i] = [p[1] / total for p in top]
    attributes = {"POSITION": accessor(verts, "VEC3", bounds=True), "NORMAL": accessor(normals, "VEC3"),
                  "JOINTS_0": accessor(joints, "VEC4", integer=True), "WEIGHTS_0": accessor(weights, "VEC4")}
    if uv is not None:
        attributes["TEXCOORD_0"] = accessor(uv, "VEC2")
    primitive = {"attributes": attributes, "indices": accessor(triangles.reshape(-1), "SCALAR", integer=True),
                 "material": mat, "mode": 4}
    mesh_index = len(doc["meshes"])
    doc["meshes"].append({"name": name, "primitives": [primitive]})
    node_index = len(doc["nodes"])
    doc["nodes"].append({"name": name, "mesh": mesh_index, "skin": 4})
    doc["scenes"][0]["nodes"].append(node_index)
    stats.append({"name": name, "vertices": len(verts), "triangles": len(triangles), "headRigid": head})
    return node_index


def grid_faces(rows, cols):
    return [(j * cols + i, j * cols + i + 1, (j + 1) * cols + i + 1, (j + 1) * cols + i)
            for j in range(rows - 1) for i in range(cols - 1)]


# Continuous bob silhouette. The face opening sits above the brows; side/back
# lengths reach the shoulder line, with individually tapered locks at the ends.
verts, uvs = [], []
rows, cols = 30, 97
for j in range(rows):
    t = .002 + .998 * j / (rows - 1)
    for i in range(cols):
        phi = 2 * math.pi * i / (cols - 1)
        front_distance = min(phi, 2 * math.pi - phi)
        opening = np.clip((front_distance - .72) / .52, 0, 1)
        opening = opening * opening * (3 - 2 * opening)
        bottom = 1.527 * (1 - opening) + (1.235 + .009 * math.cos(7 * phi)) * opening
        y = 1.593 - (1.593 - bottom) * t
        cap = math.sqrt(max(.003, 1 - ((max(y, 1.453) - 1.453) / .141) ** 2))
        fullness = 1 + .065 * math.sin(math.pi * np.clip((1.45 - y) / .22, 0, 1))
        # Fine broad channels support the shape, avoiding spaghetti-like strands.
        channel = 1 + .007 * math.cos(12 * phi + t * .4) * t
        rx = .121 * cap * fullness * channel
        rz = .125 * cap * fullness * channel
        curl = max(0, (t - .86) / .14) * opening
        x = rx * math.sin(phi) * (1 - curl * .10)
        z = -.014 + rz * math.cos(phi) + curl * .006
        verts.append((x, y, z))
        uvs.append((i / (cols - 1), t))
faces = [tuple(reversed(f)) for f in grid_faces(rows, cols)]
peak = len(verts)
verts.append((0, 1.594, -.014)); uvs.append((.5, 0))
faces.extend((peak, i, i + 1) for i in range(cols - 1))
mesh("Daily shoulder bob crown", verts, faces, hair_mat, uvs, head=True)


def lock(name, points, width, mat=hair_mat):
    points = np.asarray(points)
    verts, uv = [], []
    rows, cols = 25, 13
    for j in range(rows):
        t = j / (rows - 1)
        center = (1 - t) ** 2 * points[0] + 2 * (1 - t) * t * points[1] + t * t * points[2]
        tangent = 2 * (1 - t) * (points[1] - points[0]) + 2 * t * (points[2] - points[1])
        tangent /= np.linalg.norm(tangent)
        horizontal = np.cross(tangent, [0, 0, 1.0])
        horizontal /= max(np.linalg.norm(horizontal), 1e-10)
        depth = np.cross(horizontal, tangent)
        radius = width * (.22 + .90 * math.sin(t * math.pi) ** .7) * (1 - t) ** .42 + .0003
        for i in range(cols):
            phi = 2 * math.pi * i / (cols - 1)
            p = center + horizontal * math.cos(phi) * radius + depth * math.sin(phi) * radius * .24
            verts.append(p)
            uv.append((i / (cols - 1), t))
    mesh(name, verts, [tuple(reversed(f)) for f in grid_faces(rows, cols)], mat, uv, head=True)


# A visible off-centre part and diagonal sweep, with forehead and eyes exposed.
for i in range(5):
    lock("Daily swept fringe %02d" % i,
         [(-.036 + i * .019, 1.573 - i * .001, .055),
          (.037 + i * .014, 1.548 - i * .008, .119),
          (.076 + i * .009, 1.473 - i * .009, .103 - i * .013)], .026 - i * .001)
for i in range(3):
    lock("Daily short part %02d" % i,
         [(-.041 - i * .008, 1.568, .061), (-.078 - i * .009, 1.537, .109),
          (-.105 - i * .007, 1.465 - i * .020, .071 - i * .019)], .021)
for side in (-1, 1):
    for i in range(4):
        lock("Daily soft side lock %s %s" % (side, i),
             [(side * (.095 + i * .006), 1.507 - i * .018, .065 - i * .040),
              (side * (.136 + i * .002), 1.354, .075 - i * .043),
              (side * (.113 + i * .003), 1.237 + i * .004, .048 - i * .039)],
             .019 if i == 0 else .023, hair_light if i == 2 else hair_mat)


def torso(name, sections, mat, front_gap=0, neckline=False):
    verts, uv = [], []
    cols = 73
    for j, (y, rx, rz, cz) in enumerate(sections):
        for i in range(cols):
            phi = front_gap + (2 * math.pi - 2 * front_gap) * i / (cols - 1)
            yy = y
            if neckline and j == len(sections) - 1:
                yy -= .021 * max(0, math.cos(phi)) ** 3
            verts.append((rx * math.sin(phi), yy, cz + rz * math.cos(phi)))
            uv.append((i / (cols - 1) * 4, j / (len(sections) - 1) * 3))
    mesh(name, verts, grid_faces(len(sections), cols), mat, uv)


torso("Daily crew neck knitted top",
      [(.865, .121, .074, .003), (.93, .129, .082, .006), (1.02, .123, .091, .005),
       (1.10, .135, .095, .004), (1.16, .147, .089, .001), (1.22, .158, .079, -.004),
       (1.255, .099, .053, -.010), (1.288, .047, .043, -.009)], knit_mat, neckline=True)
torso("Daily ribbed neckline", [(1.283, .050, .046, -.009), (1.288, .048, .044, -.009)],
      hem_mat, neckline=True)
torso("Daily open cropped jacket",
      [(.935, .145, .088, .001), (.955, .146, .089, .001), (1.01, .143, .098, .001),
       (1.10, .153, .104, .0), (1.16, .166, .096, -.001), (1.215, .17, .082, -.005),
       (1.249, .151, .063, -.009), (1.284, .051, .048, -.009)], coat_mat, front_gap=.42)

# Folded soft collar and front facings are actual surfaces, with modest thickness.
for side in (-1, 1):
    verts = [(side * .021, 1.278, .039), (side * .051, 1.277, .030),
             (side * .096, 1.241, .068), (side * .074, 1.193, .100),
             (side * .036, 1.226, .105)]
    faces = [(0, 1, 2, 3, 4)] if side == -1 else [(4, 3, 2, 1, 0)]
    mesh("Daily soft collar %s" % side, verts, faces, collar_mat)
    verts, uv = [], []
    sections = [(.935, .145, .088, .001), (1.01, .143, .098, .001),
                (1.10, .153, .104, 0), (1.18, .164, .091, -.001), (1.226, .162, .077, -.007)]
    for y, rx, rz, cz in sections:
        for phi in (.42, .48):
            verts.append((side * rx * math.sin(phi), y, cz + rz * math.cos(phi) + .001))
            uv.append((phi, y))
    faces = grid_faces(len(sections), 2)
    if side == -1:
        faces = [tuple(reversed(f)) for f in faces]
    mesh("Daily jacket front facing %s" % side, verts, faces, collar_mat, uv)


def sleeve(side, cuff=False):
    centers = np.array([[side * .112, 1.252, -.009], [side * .136, 1.241, -.010], [side * .163, 1.214, -.010],
                        [side * .235, 1.145, -.011], [side * .326, 1.057, -.009],
                        [side * .408, .980, -.010], [side * .461, .929, -.011]])
    radii = [.029, .045, .058, .054, .048, .043, .037]
    if cuff:
        centers = np.array([[side * .455, .935, -.011], [side * .483, .907, -.011]])
        radii = [.043, .041]
    # Add intermediate rows for flexion; copied skin weights retain twist bones.
    v, uv = [], []
    row_centers, row_radii = [], []
    for i in range(len(centers) - 1):
        for t in np.linspace(0, 1, 5, endpoint=False):
            row_centers.append(centers[i] * (1 - t) + centers[i + 1] * t)
            row_radii.append(radii[i] * (1 - t) + radii[i + 1] * t)
    row_centers.append(centers[-1]); row_radii.append(radii[-1])
    cols = 33
    for j, (center, radius) in enumerate(zip(row_centers, row_radii)):
        direction = row_centers[min(j + 1, len(row_centers) - 1)] - row_centers[max(0, j - 1)]
        direction /= np.linalg.norm(direction)
        across = np.cross(direction, [0., 0., 1.]); across /= np.linalg.norm(across)
        depth = np.cross(direction, across)
        for i in range(cols):
            phi = 2 * math.pi * i / (cols - 1)
            fold = 1 + .022 * math.sin(3 * phi + j * .8) * math.sin(j / (len(row_centers) - 1) * math.pi)
            point = center + across * math.cos(phi) * radius * fold + depth * math.sin(phi) * radius * .91
            v.append(point); uv.append((i / (cols - 1) * 2, j / (len(row_centers) - 1) * 3))
    faces = grid_faces(len(row_centers), cols)
    if not cuff:
        center_index = len(v)
        v.append(row_centers[0]); uv.append((0, 0))
        faces.extend((center_index, i + 1, i) for i in range(cols - 1))
    mesh("Daily %s %s" % ("left" if side == 1 else "right", "ribbed cuff" if cuff else "soft jacket sleeve"),
         v, faces, hem_mat if cuff else coat_mat, uv)


for side in (-1, 1):
    sleeve(side)
    sleeve(side, cuff=True)

# Plain tailored lower garment to avoid retaining uniform straps or shirt tails.
torso("Daily trouser waistband", [(.79, .14, .088, -.010), (.85, .142, .088, -.010),
                                    (.909, .133, .081, -.002)], pants_mat)
for side in (-1, 1):
    verts, uv = [], []
    cols = 41
    sections = [(.824, .072, .084, .083), (.76, .073, .081, .084), (.66, .076, .073, .08),
                (.55, .078, .067, .072), (.45, .079, .063, .068), (.35, .078, .062, .066),
                (.24, .077, .059, .062), (.16, .077, .054, .056)]
    for j, (y, x, rx, rz) in enumerate(sections):
        for i in range(cols):
            phi = 2 * math.pi * i / (cols - 1)
            verts.append((side * x + rx * math.sin(phi), y, -.014 + rz * math.cos(phi)))
            uv.append((i / (cols - 1), j / (len(sections) - 1)))
    mesh("Daily straight trouser leg %s" % side, verts,
         [tuple(reversed(f)) for f in grid_faces(len(sections), cols)], pants_mat, uv)

# Detach source hair and machinery, retaining bones (and all face expressions).
for index in (0, 1, 144):
    doc["nodes"][index].pop("mesh", None)
    doc["nodes"][index].pop("skin", None)
wear["primitives"] = body_parts
# Covered skin is deliberately removed from render topology. This prevents
# garment intersections at flexing shoulders, stomach and trouser waist while
# keeping neck, hands and feet. Source vertex weights remain unchanged.
for primitive in wear["primitives"]:
    attrs = primitive["attributes"]
    positions = read_accessor(attrs["POSITION"])
    joints = read_accessor(attrs["JOINTS_0"])
    weights = read_accessor(attrs["WEIGHTS_0"])
    triangles = read_accessor(primitive["indices"]).reshape(-1, 3)
    hand_bones = ((joints >= 34) & (joints <= 54)) | ((joints >= 59) & (joints <= 78))
    hand_weight = (weights * hand_bones).sum(axis=1)
    exposed = ((positions[:, 1] > 1.262) & (np.abs(positions[:, 0]) < .073)) | (positions[:, 1] < .159)
    exposed |= hand_weight > .32
    keep = exposed[triangles].all(axis=1)
    primitive["indices"] = accessor(triangles[keep].reshape(-1), "SCALAR", integer=True)
    primitive["material"] = skin_mat

meta = doc["extensions"]["VRMC_vrm"]["meta"]
meta["name"] = "Seed-san Daily Study"
meta["version"] = "daily-1"
meta.pop("thumbnailImage", None)
meta["references"] = list(dict.fromkeys(meta.get("references", []) + [
    "Based on Seed-san, copyright VirtualCast, Inc.",
    "Derivative: shoulder bob, daily jacket, knit top and trousers; generated in this project.",
    "https://github.com/vrm-c/vrm-specification/tree/master/samples/Seed-san",
]))
doc.setdefault("asset", {})["generator"] = "build_seed_daily.py / original Seed-san retained"
doc["asset"]["extras"] = {"sourceSHA256": hashlib.sha256(raw).hexdigest(), "style": "Shoulder bob and daily jacket"}
doc["buffers"][0]["byteLength"] = len(binary)
json_bytes = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
json_bytes += b" " * (-len(json_bytes) % 4)
binary.extend(b"\0" * (-len(binary) % 4))
glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(binary))
glb += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
glb += struct.pack("<II", len(binary), 0x004E4942) + binary
OUTPUT.write_bytes(glb)
(SOURCE_DIR / "build.json").write_text(json.dumps({"sourceSHA256": hashlib.sha256(raw).hexdigest(),
    "outputSHA256": hashlib.sha256(glb).hexdigest(), "outputBytes": len(glb), "newMeshes": stats,
    "weightMethod": "Eight nearest body vertices; normalized top four, cuffs exclude fingers, trousers exclude opposite leg, neckline bound to chest",
    "faceMorphs": "Unchanged", "humanoid": "Unchanged", "actionDrivers": "Unchanged",
    "hairPhysics": "Head-bound bob; no new spring chains"}, indent=2), encoding="utf-8")
print("Built", OUTPUT, len(glb), "bytes;", sum(s["triangles"] for s in stats), "new triangles")
