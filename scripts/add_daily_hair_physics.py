import copy
import hashlib
import io
import json
import math
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/models/seed-daily.vrm"
OUTPUT = ROOT / "web/models/seed-daily-hair.vrm"
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
    count = int(a["count"]) * width
    offset = int(v.get("byteOffset", 0)) + int(a.get("byteOffset", 0))
    return np.frombuffer(binary, dtype=dtype, count=count, offset=offset).copy().reshape(a["count"], width)


def append_bytes(data, target=None):
    binary = globals()["binary"]
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
    a = {"bufferView": append_bytes(values.tobytes(), 34963 if kind == "SCALAR" else 34962), "componentType": 5123 if integer else 5126, "count": len(values), "type": kind}
    if bounds:
        a.update(min=values.min(axis=0).tolist(), max=values.max(axis=0).tolist())
    index = len(doc["accessors"])
    doc["accessors"].append(a)
    return index


def mat4_to_array(m):
    return [m[0, :].tolist(), m[1, :].tolist(), m[2, :].tolist(), m[3, :].tolist()]


def world_matrix(node):
    t = np.eye(4)
    t[0:3, 3] = node.get("translation", [0, 0, 0])
    q = node.get("rotation", [0, 0, 0, 1])
    r = np.eye(4)
    r[0:3, 0:3] = np.array([
        [1 - 2*q[1]*q[1] - 2*q[2]*q[2], 2*q[0]*q[1] - 2*q[2]*q[3], 2*q[0]*q[2] + 2*q[1]*q[3]],
        [2*q[0]*q[1] + 2*q[2]*q[3], 1 - 2*q[0]*q[0] - 2*q[2]*q[2], 2*q[1]*q[2] - 2*q[0]*q[3]],
        [2*q[0]*q[2] - 2*q[1]*q[3], 2*q[1]*q[2] + 2*q[0]*q[3], 1 - 2*q[0]*q[0] - 2*q[1]*q[1]],
    ])
    s = np.eye(4)
    s[0:3, 0:3] = np.diag(node.get("scale", [1, 1, 1]))
    return t @ r @ s


nodes = doc["nodes"]
parents = {}
for i, node in enumerate(nodes):
    for child in node.get("children", []):
        parents[child] = i

# 世界矩阵必须**递归**求：glTF 不保证父节点下标小于子节点，按下标顺序
# 单趟累乘会在父节点尚未计算时读到单位矩阵，算出来的逆绑定矩阵整块是错的。
# 上一版就是这样把 8 个发束关节的 IBM 写成了垃圾值，头发被拉成头顶的
# 八根竖条（2026-10-18 修复）。
worlds = {}


def node_world(index):
    if index not in worlds:
        local = world_matrix(nodes[index])
        parent = parents.get(index)
        worlds[index] = (node_world(parent) @ local) if parent is not None else local
    return worlds[index]


for i in range(len(nodes)):
    node_world(i)
worlds = [worlds[i] for i in range(len(nodes))]

skin = doc["skins"][4]
head_index = skin["joints"].index(45)
hair_map = {
    "Daily swept fringe 00": 46,
    "Daily swept fringe 01": 49,
    "Daily swept fringe 02": 52,
    "Daily swept fringe 03": 55,
    "Daily swept fringe 04": 58,
    "Daily short part 00": 61,
    "Daily short part 01": 64,
    "Daily short part 02": 67,
}
hair_bone_nodes = set(hair_map.values())
modified = []

for node_index, node in enumerate(nodes):
    if "mesh" not in node:
        continue
    mesh_name = node.get("name", "")
    hair_bone = hair_map.get(mesh_name)
    if hair_bone is None:
        continue
    prim = doc["meshes"][node["mesh"]]["primitives"][0]
    if "TEXCOORD_0" not in prim["attributes"]:
        continue
    uvs = read_accessor(prim["attributes"]["TEXCOORD_0"])
    joints = read_accessor(prim["attributes"]["JOINTS_0"])
    weights = read_accessor(prim["attributes"]["WEIGHTS_0"])
    new_joints = joints.copy()
    new_weights = weights.copy()
    hair_joint_index = None
    for i in range(len(uvs)):
        t = float(uvs[i, 1])
        w = max(0.0, min(1.0, (t - 0.25) / 0.5))
        w = w * w * (3 - 2 * w)
        head_w = 1 - w
        new_weights[i] = [w, head_w, 0, 0]
        new_joints[i] = [0, head_index, 0, 0]
    # We will fill hair_joint_index after extending skin.

    modified.append({"node_index": node_index, "primitive": prim, "new_joints": new_joints, "new_weights": new_weights, "hair_bone": hair_bone})

# Extend skin joints and inverse bind matrices.
#
# glTF 的 MAT4 按列主序存储。之前把 numpy 行主序的矩阵直接按 16 个 float 写出，
# 等于把每个 IBM 转置了：绑定姿势下 boneWorld @ IBM 不再是单位阵，发束被
# 骨骼的平移整块推离头顶。这里统一在**行主序（数学惯例）**里算，只在写盘前
# 转置一次；老矩阵先转回来再拼，读入-写出的往返字节不变。
new_joints = skin["joints"] + sorted(hair_bone_nodes)
old_inverse = read_accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
head_bind = old_inverse[head_index]
assert np.allclose(head_bind, np.linalg.inv(worlds[45]), atol=2e-3), (
    "IBM 约定漂移：现有关节的逆绑定矩阵与新算的世界矩阵不一致，"
    "说明节点世界矩阵或列主序约定其中之一错了，先查清再写盘。")
new_inverse = np.concatenate(
    [old_inverse, np.array([np.linalg.inv(worlds[joint]) for joint in sorted(hair_bone_nodes)])], axis=0)
skin["joints"] = new_joints
skin["inverseBindMatrices"] = accessor(new_inverse.transpose(0, 2, 1).reshape(-1, 16), "MAT4")

# Rewrite hair mesh attributes with updated joint indices.
for item in modified:
    item["new_joints"][:, 0] = new_joints.index(item["hair_bone"])
    item["primitive"]["attributes"]["JOINTS_0"] = accessor(item["new_joints"].astype("<u2"), "VEC4", integer=True)
    item["primitive"]["attributes"]["WEIGHTS_0"] = accessor(item["new_weights"].astype("<f4"), "VEC4")

# Add spring bone extension.
springs = []
for name, parent in [
    ("FrontHairA", 46), ("FrontHairB", 49), ("FrontHairC", 52), ("FrontHairD", 55), ("FrontHairE", 58),
    ("FrontHairF", 61), ("FrontHairG", 64), ("SideHairA", 67)
]:
    child = parent + 1
    springs.append({
        "name": name,
        "joints": [
            {"node": child, "stiffness": 1.2, "dragForce": 1.0, "gravityDir": [0, -1, 0], "gravityPower": 0.0, "hitRadius": 0.01},
        ],
    })
doc.setdefault("extensions", {}).setdefault("VRMC_vrm", {})["springBone"] = {
    "specVersion": "1.0",
    "springs": springs,
}

# Update asset info.
doc["asset"]["generator"] = "build_seed_daily.py + add_daily_hair_physics.py"
doc["asset"]["extras"] = {"sourceSHA256": hashlib.sha256(raw).hexdigest(), "style": "Shoulder bob and daily jacket", "hairPhysics": "Front/short springs"}

json_bytes = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
json_bytes += b" " * (-len(json_bytes) % 4)
binary.extend(b"\0" * (-len(binary) % 4))
glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(binary))
glb += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
glb += struct.pack("<II", len(binary), 0x004E4942) + binary
OUTPUT.write_bytes(glb)
print("Wrote", OUTPUT, len(glb), "bytes; springs", len(springs))

