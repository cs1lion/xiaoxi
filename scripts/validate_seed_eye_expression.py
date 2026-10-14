"""Strictly validate the additive Daily brow candidate against its source.

This is a separate contract from validate_seed_daily.py, whose original
no-face-change contract is intentionally kept intact.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct

from validate_seed_daily import Model


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("neuro_brow_raise_L", "neuro_brow_raise_R", "neuro_brow_lower_L", "neuro_brow_lower_R")


def _brow_support(reference: Model, mesh: dict) -> tuple[int, dict[str, set[int]]]:
    """Select complete high, isolated eye-material components independently."""
    candidates = []
    for pi, primitive in enumerate(mesh["primitives"]):
        if reference.json["materials"][primitive["material"]].get("name") != "eye":
            continue
        positions = reference.accessor(primitive["attributes"]["POSITION"])
        adjacent = [set() for _ in positions]
        indices = [row[0] for row in reference.accessor(primitive["indices"])]
        if primitive.get("mode", 4) != 4 or len(indices) % 3:
            raise ValueError("Source eye primitive is not indexed triangle geometry")
        for offset in range(0, len(indices), 3):
            triangle = indices[offset:offset + 3]
            if not all(0 <= i < len(positions) for i in triangle):
                raise ValueError("Invalid source eye index")
            for i in triangle:
                adjacent[i].update(triangle)
        seen, components = set(), []
        for start in range(len(positions)):
            if start in seen:
                continue
            todo, component = [start], set()
            while todo:
                i = todo.pop()
                if i in component:
                    continue
                component.add(i)
                todo.extend(adjacent[i] - component)
            seen.update(component)
            components.append(component)
        sides = {}
        for side, sign in (("L", 1), ("R", -1)):
            matches = [component for component in components if len(component) == 41 and all(
                sign * positions[i][0] > 0.015 and 1.445 < positions[i][1] < 1.47
                and 0.07 < positions[i][2] < 0.11 for i in component)]
            if len(matches) != 1:
                raise ValueError(f"Independent complete {side} brow component was not uniquely identified")
            sides[side] = matches[0]
        names = mesh["extras"]["targetNames"]
        for side, support in sides.items():
            for name in ("eye_brow_up_" + side, "eye_brow_down_" + side):
                old_values = reference.accessor(primitive["targets"][names.index(name)]["POSITION"])
                nonzero = {i for i, row in enumerate(old_values) if any(abs(value) > 1e-8 for value in row)}
                if nonzero != support:
                    raise ValueError(f"{name} does not identify exactly the independent full brow component")
            for name in ("eye_close", "eye_open", "eye_smile", "blink_L", "blink_R"):
                values = reference.accessor(primitive["targets"][names.index(name)]["POSITION"])
                if any(any(abs(value) > 1e-8 for value in values[i]) for i in support):
                    raise ValueError(f"{side} brow support intersects protected {name} geometry")
        if sides["L"] & sides["R"]:
            raise ValueError("Source eyebrow components overlap")
        candidates.append((pi, sides))
    if len(candidates) != 1:
        raise ValueError("Could not independently identify the unique eyebrow primitive")
    return candidates[0]


def validate(reference: Model, revised: Model, displacement: float = 0.0045) -> dict:
    before, after = reference.json, revised.json
    checks = 0

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise ValueError(message)

    check(math.isfinite(displacement) and 0.003 <= displacement <= 0.006,
          "Displacement is not a conservative finite 3–6 mm amount")
    # This prefix check includes every original accessor, texture and padding
    # byte, not just the subset used by the active face mesh.
    check(revised.binary[:len(reference.binary)] == reference.binary,
          "Original GLB binary data changed")
    old_accessors = len(before["accessors"])
    old_views = len(before["bufferViews"])
    check(after.get("accessors", [])[:old_accessors] == before["accessors"],
          "An original accessor declaration changed")
    check(after.get("bufferViews", [])[:old_views] == before["bufferViews"],
          "An original buffer view declaration changed")
    head_nodes = [i for i, node in enumerate(before["nodes"]) if node.get("name") == "head" and "mesh" in node]
    check(len(head_nodes) == 1, "Source lacks exactly one head mesh")
    head_node = head_nodes[0]
    mesh_index = before["nodes"][head_node]["mesh"]
    mesh_before = before["meshes"][mesh_index]
    mesh_after = after["meshes"][mesh_index]
    old_names = mesh_before["extras"]["targetNames"]
    old_count = len(old_names)
    check(mesh_after.get("extras", {}).get("targetNames") == old_names + list(TARGETS),
          "Only the four specified additive morph names may be appended")
    check(len(mesh_after["primitives"]) == len(mesh_before["primitives"]),
          "Original head primitive count changed")
    check(mesh_after.get("weights") == mesh_before.get("weights", [0.0] * old_count) + [0.0] * 4,
          "New default morph weights must be zero and original weights preserved")

    # Restore only the explicitly permitted declarations, then compare the
    # entire JSON document. This protects all scenes/nodes/rig/skins/materials/
    # extensions/old expression binds/license fields, including unused data.
    normalized = copy.deepcopy(after)
    normalized["accessors"] = normalized["accessors"][:old_accessors]
    normalized["bufferViews"] = normalized["bufferViews"][:old_views]
    check(len(normalized.get("buffers", [])) == 1 and len(before["buffers"]) == 1,
          "The GLB must retain one self-contained buffer")
    check(normalized["buffers"][0].get("byteLength", 0) <= len(revised.binary)
          and len(revised.binary) - normalized["buffers"][0]["byteLength"] < 4,
          "Revised buffer length does not match the padded binary chunk")
    normalized["buffers"][0]["byteLength"] = before["buffers"][0]["byteLength"]
    normalized_mesh = normalized["meshes"][mesh_index]
    normalized_mesh["extras"]["targetNames"] = old_names[:]
    if "weights" in mesh_before:
        normalized_mesh["weights"] = copy.deepcopy(mesh_before["weights"])
    else:
        normalized_mesh.pop("weights", None)
    for pi, (old_p, new_p) in enumerate(zip(mesh_before["primitives"], mesh_after["primitives"])):
        check(len(new_p.get("targets", [])) == old_count + 4,
              f"Primitive {pi} does not declare exactly four new targets")
        check(new_p["targets"][:old_count] == old_p["targets"],
              f"Primitive {pi} existing morph declarations changed")
        normalized_mesh["primitives"][pi]["targets"] = new_p["targets"][:old_count]
    for i, old_node in enumerate(before["nodes"]):
        if old_node.get("mesh") == mesh_index and "weights" in old_node:
            check(after["nodes"][i].get("weights") == old_node["weights"] + [0.0] * 4,
                  f"Node {i} morph weight declaration changed beyond zero extension")
            normalized["nodes"][i]["weights"] = old_node["weights"][:]
    check(normalized == before,
          "Protected model JSON changed outside permitted targets, weights and buffer lengths")
    check(head_node in revised.active_nodes(), "The unchanged source head is not in the active scene")

    primitive_index, support = _brow_support(reference, mesh_before)
    primitive = mesh_before["primitives"][primitive_index]
    skin_index = before["nodes"][head_node]["skin"]
    skin = before["skins"][skin_index]
    check(skin["joints"] == [before["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["head"]["node"]],
          "Brow source skin is not the rigid humanoid head skin")
    joints = reference.accessor(primitive["attributes"]["JOINTS_0"])
    weights = reference.accessor(primitive["attributes"]["WEIGHTS_0"])
    for side in ("L", "R"):
        check(all(joints[i] == (0, 0, 0, 0) and weights[i] == (1.0, 0.0, 0.0, 0.0)
                  for i in support[side]), f"{side} brow vertices are not rigidly head-bound")

    float_amount = struct.unpack("<f", struct.pack("<f", displacement))[0]
    used_accessors, used_views = set(), set()
    for pi, (old_p, new_p) in enumerate(zip(mesh_before["primitives"], mesh_after["primitives"])):
        count = before["accessors"][old_p["attributes"]["POSITION"]]["count"]
        for ti, name in enumerate(TARGETS):
            target = new_p["targets"][old_count + ti]
            check(set(target) == {"POSITION"}, f"{name} primitive {pi} contains unapproved morph attributes")
            ai = target["POSITION"]
            check(old_accessors <= ai < len(after["accessors"]), f"{name} must use a new valid accessor")
            used_accessors.add(ai)
            accessor = after["accessors"][ai]
            check(accessor.get("componentType") == 5126 and accessor.get("type") == "VEC3"
                  and accessor.get("count") == count and "bufferView" not in accessor
                  and not accessor.get("normalized", False), f"{name} has an invalid sparse POSITION declaration")
            values = revised.accessor(ai)
            check(len(values) == count and all(math.isfinite(v) for row in values for v in row),
                  f"{name} data is non-finite or has the wrong vertex count")
            side = name[-1]
            amount = float_amount if "raise" in name else -float_amount
            expected_support = support[side] if pi == primitive_index else set()
            actual_support = {i for i, row in enumerate(values) if row != (0, 0, 0)}
            check(actual_support == expected_support,
                  f"{name} primitive {pi} support differs from the complete single-side eyebrow")
            check(all(row == (0.0, amount, 0.0) for i, row in enumerate(values) if i in expected_support),
                  f"{name} is not a uniform pure-Y displacement")
            expected_min = [min(row[k] for row in values) for k in range(3)]
            expected_max = [max(row[k] for row in values) for k in range(3)]
            check(all(abs(a - b) <= 1e-9 for a, b in zip(accessor.get("min", []), expected_min))
                  and len(accessor.get("min", [])) == 3
                  and all(abs(a - b) <= 1e-9 for a, b in zip(accessor.get("max", []), expected_max))
                  and len(accessor.get("max", [])) == 3, f"{name} accessor bounds do not match the data")
            if expected_support:
                sparse = accessor.get("sparse", {})
                check(sparse.get("count") == len(expected_support), f"{name} sparse count differs from full eyebrow support")
                check(sparse.get("indices", {}).get("componentType") == 5123,
                      f"{name} sparse indices are not UINT16")
                iv, vv = sparse["indices"]["bufferView"], sparse["values"]["bufferView"]
                used_views.update((iv, vv))
                check(old_views <= iv < len(after["bufferViews"]) and old_views <= vv < len(after["bufferViews"]),
                      f"{name} references an old or invalid sparse buffer view")
                check(all("target" not in after["bufferViews"][view] for view in (iv, vv)),
                      f"{name} sparse view declares an illegal GPU buffer target")
                index_rows = revised._read(iv, sparse["indices"].get("byteOffset", 0),
                                           sparse["count"], 5123, 1)
                check([row[0] for row in index_rows] == sorted(expected_support),
                      f"{name} sparse indices are not strictly increasing and exact")
            else:
                check("sparse" not in accessor, f"{name} unrelated primitive has sparse data")
    check(used_accessors == set(range(old_accessors, len(after["accessors"]))),
          "Unapproved unused accessors were appended")
    check(used_views == set(range(old_views, len(after["bufferViews"]))),
          "Unapproved unused buffer views were appended")
    for index in used_views:
        view = after["bufferViews"][index]
        check(view.get("buffer", 0) == 0 and view.get("byteOffset", 0) >= len(reference.binary)
              and view.get("byteOffset", 0) % 4 == 0,
              f"New view {index} overlaps original data or is not four-byte aligned")
    return {
        "passed": True, "checks": checks,
        "originalBinaryPrefixExact": True, "originalJsonExactOutsideDeclaredAdditions": True,
        "neutralGeometryTopologyMaterialsTexturesExact": True,
        "allOriginalMorphDataExact": True,
        "rigAndSkinBindExact": True, "vrmExpressionsLookAtMetaLicenseExact": True,
        "newTargetsPureLocalY": True, "newTargetsFullBrowComponentsOnly": True,
        "newTargetsLeftRightDisjoint": True, "newDefaultWeightsZero": True,
        "browPrimitive": primitive_index, "browVerticesPerSide": {side: len(indices) for side, indices in support.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", nargs="?", type=Path, default=ROOT / "web/models/seed-daily-eyes.vrm")
    parser.add_argument("--reference", type=Path, default=ROOT / "web/models/seed-daily.vrm")
    parser.add_argument("--displacement", type=float, default=0.0045)
    args = parser.parse_args()
    result = validate(Model(args.reference), Model(args.candidate), args.displacement)
    result["referenceSHA256"] = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    result["candidateSHA256"] = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
