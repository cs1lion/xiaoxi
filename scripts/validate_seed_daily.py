"""Validate a geometry revision of Seed-san without loading a renderer.

Usage: python scripts/validate_seed_daily.py web/models/seed-daily.vrm
The original face, humanoid rig, and skin bind transforms are the reference.
Equipment is checked only on meshes reachable from the active scene: unused
source buffers/material definitions may safely remain in an incremental GLB.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import sys


COMPONENTS = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
DIMENSIONS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
EQUIPMENT_MATERIALS = {
    "arm_mat", "arm_plastic", "green_emit", "wear_metal",
    "armgear_plastic", "robo_face", "glass", "backpack_metal",
    "backpack_nm", "backpack_plastic", "anim_logo",
}


class Model:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        magic, version, length = struct.unpack_from("<III", raw)
        if magic != 0x46546C67 or version != 2 or length != len(raw):
            raise ValueError("Invalid GLB header or declared file length")
        chunks = {}
        offset = 12
        while offset < len(raw):
            size, kind = struct.unpack_from("<II", raw, offset)
            end = offset + 8 + size
            if end > len(raw) or size % 4:
                raise ValueError("Invalid GLB chunk length")
            chunks[kind] = raw[offset + 8:end]
            offset = end
        self.json = json.loads(chunks[0x4E4F534A])
        self.binary = chunks[0x004E4942]
        self.cache = {}
        for view in self.json.get("bufferViews", []):
            if view.get("buffer", 0) != 0:
                raise ValueError("Unexpected external buffer")
            if view.get("byteOffset", 0) + view["byteLength"] > len(self.binary):
                raise ValueError("A buffer view extends beyond the GLB binary chunk")

    def _read(self, view_index, byte_offset, count, component_type, dimensions):
        view = self.json["bufferViews"][view_index]
        fmt, width = COMPONENTS[component_type]
        step = view.get("byteStride", width * dimensions)
        start = view.get("byteOffset", 0) + byte_offset
        extent = (count - 1) * step + width * dimensions if count else 0
        if byte_offset + extent > view["byteLength"]:
            raise ValueError("An accessor extends beyond its buffer view")
        return [
            struct.unpack_from("<" + fmt * dimensions, self.binary, start + i * step)
            for i in range(count)
        ]

    def accessor(self, index):
        if index in self.cache:
            return self.cache[index]
        accessor = self.json["accessors"][index]
        dimensions = DIMENSIONS[accessor["type"]]
        if "bufferView" in accessor:
            rows = self._read(
                accessor["bufferView"], accessor.get("byteOffset", 0),
                accessor["count"], accessor["componentType"], dimensions,
            )
        else:
            rows = [(0,) * dimensions for _ in range(accessor["count"])]
        if "sparse" in accessor:
            sparse = accessor["sparse"]
            indices = sparse["indices"]
            values = sparse["values"]
            index_rows = self._read(
                indices["bufferView"], indices.get("byteOffset", 0),
                sparse["count"], indices["componentType"], 1,
            )
            value_rows = self._read(
                values["bufferView"], values.get("byteOffset", 0),
                sparse["count"], accessor["componentType"], dimensions,
            )
            for index_row, value_row in zip(index_rows, value_rows):
                if not 0 <= index_row[0] < len(rows):
                    raise ValueError("Sparse accessor index out of range")
                rows[index_row[0]] = value_row
        self.cache[index] = rows
        return rows

    def active_nodes(self):
        todo = list(self.json["scenes"][self.json.get("scene", 0)]["nodes"])
        seen = set()
        while todo:
            index = todo.pop()
            if index in seen:
                continue
            seen.add(index)
            todo.extend(self.json["nodes"][index].get("children", []))
        return seen


def validate(reference: Model, revised: Model):
    before, after = reference.json, revised.json
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    old_vrm = before["extensions"]["VRMC_vrm"]
    vrm = after.get("extensions", {}).get("VRMC_vrm", {})
    human_bones = old_vrm["humanoid"]["humanBones"]
    check(vrm.get("humanoid", {}).get("humanBones") == human_bones,
          "Humanoid bone mapping changed")
    for name, bind in human_bones.items():
        index = bind["node"]
        for transform in ("translation", "rotation", "scale", "matrix"):
            check(before["nodes"][index].get(transform) == after["nodes"][index].get(transform),
                  f"Humanoid {name} {transform} changed")
    for index, old_skin in enumerate(before["skins"]):
        if index >= len(after.get("skins", [])):
            errors.append(f"Reference skin {index} missing")
            continue
        skin = after["skins"][index]
        check(old_skin["joints"] == skin["joints"], f"Skin {index} joint mapping changed")
        check(reference.accessor(old_skin["inverseBindMatrices"])
              == revised.accessor(skin["inverseBindMatrices"]),
              f"Skin {index} inverse bind matrices changed")

    old_face_node = next(i for i, n in enumerate(before["nodes"]) if n.get("name") == "head" and "mesh" in n)
    check(old_face_node in revised.active_nodes(), "Face mesh is not reachable from the active scene")
    old_face = before["meshes"][before["nodes"][old_face_node]["mesh"]]
    new_face = after["meshes"][after["nodes"][old_face_node]["mesh"]]
    check(old_face.get("extras") == new_face.get("extras"), "Face morph target names changed")
    check(len(old_face["primitives"]) == len(new_face["primitives"]), "Face primitive count changed")
    for primitive_index, (old_p, new_p) in enumerate(zip(old_face["primitives"], new_face["primitives"])):
        check(set(old_p["attributes"]) == set(new_p["attributes"]), f"Face primitive {primitive_index} attributes changed")
        for attribute, old_index in old_p["attributes"].items():
            if attribute in new_p["attributes"]:
                check(reference.accessor(old_index) == revised.accessor(new_p["attributes"][attribute]),
                      f"Face primitive {primitive_index} {attribute} data changed")
        check(reference.accessor(old_p["indices"]) == revised.accessor(new_p["indices"]),
              f"Face primitive {primitive_index} topology changed")
        check(len(old_p.get("targets", [])) == len(new_p.get("targets", [])),
              f"Face primitive {primitive_index} morph count changed")
        for target_index, (old_t, new_t) in enumerate(zip(old_p.get("targets", []), new_p.get("targets", []))):
            check(set(old_t) == set(new_t), f"Face morph {target_index} attributes changed")
            for attribute, old_index in old_t.items():
                if attribute in new_t:
                    check(reference.accessor(old_index) == revised.accessor(new_t[attribute]),
                          f"Face primitive {primitive_index} morph {target_index} {attribute} changed")
        old_material, new_material = before["materials"][old_p["material"]], after["materials"][new_p["material"]]
        check(old_material == new_material, f"Face primitive {primitive_index} material changed")

    old_expressions = old_vrm.get("expressions", {}).get("preset", {})
    expressions = vrm.get("expressions", {}).get("preset", {})
    for name, expression in old_expressions.items():
        check(expressions.get(name, {}).get("morphTargetBinds", []) == expression.get("morphTargetBinds", []),
              f"Expression {name} facial morph binding changed")
    meta = vrm.get("meta", {})
    check("VirtualCast, Inc." in meta.get("authors", []), "Required source author attribution is missing")
    check(meta.get("creditNotation") == "required", "Source required-credit license flag changed")
    check(meta.get("modification") == old_vrm["meta"]["modification"], "Source modification license flag changed")

    positions = []
    vertices = triangles = active_primitives = 0
    mesh_nodes = []
    for node_index in sorted(revised.active_nodes()):
        node = after["nodes"][node_index]
        if "mesh" not in node:
            continue
        mesh = after["meshes"][node["mesh"]]
        mesh_nodes.append({"node": node_index, "name": node.get("name"), "mesh": node["mesh"]})
        check(mesh.get("name") != "robo_arm", "Mechanical arm mesh is still active")
        for primitive_index, primitive in enumerate(mesh["primitives"]):
            prefix = f"Node {node_index} primitive {primitive_index}"
            active_primitives += 1
            material_name = after["materials"][primitive["material"]].get("name", "")
            check(material_name not in EQUIPMENT_MATERIALS, f"{prefix} still uses equipment material {material_name}")
            attributes = primitive["attributes"]
            xyz = revised.accessor(attributes["POSITION"])
            vertices += len(xyz)
            positions.extend(xyz)
            check(bool(xyz) and all(math.isfinite(x) for row in xyz for x in row),
                  f"{prefix} has empty or non-finite positions")
            if "indices" in primitive:
                indices = [row[0] for row in revised.accessor(primitive["indices"])]
                check(primitive.get("mode", 4) == 4, f"{prefix} is not triangle geometry")
                check(len(indices) % 3 == 0 and all(0 <= index < len(xyz) for index in indices),
                      f"{prefix} has invalid triangle indices")
                triangles += len(indices) // 3
            for attribute, index in attributes.items():
                values = revised.accessor(index)
                check(len(values) == len(xyz), f"{prefix} {attribute} count differs from positions")
                check(all(math.isfinite(x) for row in values for x in row), f"{prefix} {attribute} is not finite")
            if "skin" in node:
                check("JOINTS_0" in attributes and "WEIGHTS_0" in attributes,
                      f"{prefix} lacks skin joints or weights")
                if "JOINTS_0" not in attributes or "WEIGHTS_0" not in attributes:
                    continue
                weights = revised.accessor(attributes["WEIGHTS_0"])
                joints = revised.accessor(attributes["JOINTS_0"])
                joint_count = len(after["skins"][node["skin"]]["joints"])
                check(all(abs(sum(row) - 1) < 2e-5 and min(row) >= 0 for row in weights),
                      f"{prefix} weights are negative or not normalized")
                check(all(0 <= joint < joint_count for row in joints for joint in row),
                      f"{prefix} joint index exceeds its skin")

    bounds = {"min": [min(row[i] for row in positions) for i in range(3)],
              "max": [max(row[i] for row in positions) for i in range(3)]} if positions else None
    if bounds:
        check(bounds["min"][1] > -0.03 and bounds["max"][1] < 1.8,
              "Model height is outside expected metre units")
        check(max(abs(bounds["min"][0]), abs(bounds["max"][0])) < 0.8,
              "Active raw geometry still extends to the mechanical arm")
        check(bounds["min"][2] > -0.3,
              "Active raw geometry still extends to the backpack")
    return {
        "ok": not errors,
        "errors": errors,
        "humanoid_bones": len(human_bones),
        "face_morph_targets": len(old_face.get("extras", {}).get("targetNames", [])),
        "active_mesh_nodes": mesh_nodes,
        "active_primitives": active_primitives,
        "vertices": vertices,
        "triangles": triangles,
        "raw_bind_geometry_bounds": bounds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "web/models/seed-san.vrm")
    args = parser.parse_args()
    try:
        result = validate(Model(args.source), Model(args.model))
    except (KeyError, ValueError, IndexError, struct.error) as exc:
        result = {"ok": False, "errors": [f"Invalid model structure: {exc}"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
