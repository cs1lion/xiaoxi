"""Validate source-preserving local eyelid candidate with independent rules."""
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
TARGETS = ("neuro_eye_smile_L", "neuro_eye_smile_R", "neuro_lower_lid_raise_L", "neuro_lower_lid_raise_R")


def _groups(source, primitive):
    count = source.json["accessors"][primitive["attributes"]["POSITION"]]["count"]
    neighbors = [set() for _ in range(count)]
    indices = [row[0] for row in source.accessor(primitive["indices"])]
    if primitive.get("mode", 4) != 4 or len(indices) % 3:
        raise ValueError("Source face is not indexed triangle geometry")
    for offset in range(0, len(indices), 3):
        triangle = indices[offset:offset + 3]
        if any(i < 0 or i >= count for i in triangle):
            raise ValueError("Source has an invalid triangle index")
        for i in triangle:
            neighbors[i].update(triangle)
    remaining = set(range(count))
    groups = []
    while remaining:
        stack, visited = [min(remaining)], set()
        while stack:
            index = stack.pop()
            if index in visited:
                continue
            visited.add(index)
            stack.extend(neighbors[index] - visited)
        remaining -= visited
        groups.append(visited)
    return groups


def _regions(source, mesh):
    names = mesh["extras"]["targetNames"]
    allowed = {}
    mouth, center = set(), set()
    for pi, primitive in enumerate(mesh["primitives"]):
        xyz = source.accessor(primitive["attributes"]["POSITION"])
        native = source.accessor(primitive["targets"][names.index("eye_smile")]["POSITION"])
        groups = _groups(source, primitive)
        if pi == 0:
            main = [group for group in groups if len(group) == 3250]
            inner = [group for group in groups if len(group) == 179 and all(
                1.37 < xyz[i][1] < 1.445 and xyz[i][2] > .05 for i in group)]
            if len(main) != 1 or len(inner) != 2:
                raise ValueError("Cannot independently identify safe eye-socket/internal-eye topology")
            allowed[pi] = set.union(*main, *inner)
            source_support = {i for i, row in enumerate(native) if any(abs(v) > 1e-8 for v in row)}
            mouth = source_support - allowed[pi]
            center = {i for i in source_support & allowed[pi] if abs(xyz[i][0]) <= .0001}
            if len(mouth) != 14 or len(center) != 3:
                raise ValueError("Unexpected source smile mouth or midline support")
            if not all(xyz[i][1] < 1.355 for i in mouth):
                raise ValueError("Excluded source smile vertices are not the mouth components")
        elif pi == 1:
            chosen = [group for group in groups if any(any(abs(v) > 1e-8 for v in native[i]) for i in group)]
            if sorted(map(len, chosen)) != [8, 8, 8, 8, 14, 14, 36, 36, 71, 71]:
                raise ValueError("Source eyelash/eye-line components differ from the expected topology")
            if not all(all(any(abs(v) > 1e-8 for v in native[i]) for i in group) for group in chosen):
                raise ValueError("Source eye-line smile is not complete-component support")
            allowed[pi] = set.union(*chosen)
        elif pi == 2:
            allowed[pi] = set()
        else:
            raise ValueError("Unexpected face primitive count")
    return allowed, mouth, center


def _weight(lo, hi, value):
    fraction = max(0, min(1, (value - lo) / (hi - lo)))
    return 3 * fraction * fraction - 2 * fraction * fraction * fraction


def validate(source: Model, candidate: Model) -> dict:
    before, after = source.json, candidate.json
    checks = 0

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise ValueError(message)

    check(candidate.binary[:len(source.binary)] == source.binary, "Original binary prefix changed")
    old_accessors, old_views = len(before["accessors"]), len(before["bufferViews"])
    check(after["accessors"][:old_accessors] == before["accessors"], "Original accessor declarations changed")
    check(after["bufferViews"][:old_views] == before["bufferViews"], "Original buffer views changed")
    heads = [i for i, node in enumerate(before["nodes"]) if node.get("name") == "head" and "mesh" in node]
    check(len(heads) == 1, "Source head mesh is not unique")
    node_index = heads[0]
    mesh_index = before["nodes"][node_index]["mesh"]
    old_mesh, new_mesh = before["meshes"][mesh_index], after["meshes"][mesh_index]
    names = old_mesh["extras"]["targetNames"]
    old_count = len(names)
    check(all(name in names for name in ("neuro_brow_raise_L", "neuro_brow_raise_R", "neuro_brow_lower_L", "neuro_brow_lower_R")),
          "Source lacks V2 independent brow channels")
    check(len(old_mesh["primitives"]) == len(new_mesh["primitives"]) == 3, "Face primitive count changed")
    check(new_mesh["extras"]["targetNames"] == names + list(TARGETS), "Only four specified V3 names may be appended")
    check(new_mesh.get("weights") == old_mesh.get("weights", [0.] * old_count) + [0.] * 4,
          "New default weights are not zero or existing weights changed")
    normalized = copy.deepcopy(after)
    normalized["accessors"] = normalized["accessors"][:old_accessors]
    normalized["bufferViews"] = normalized["bufferViews"][:old_views]
    check(len(after["buffers"]) == len(before["buffers"]) == 1, "Buffer count changed")
    check(0 <= len(candidate.binary) - after["buffers"][0]["byteLength"] < 4, "Padded buffer size is invalid")
    normalized["buffers"][0]["byteLength"] = before["buffers"][0]["byteLength"]
    nm = normalized["meshes"][mesh_index]
    nm["extras"]["targetNames"] = names[:]
    if "weights" in old_mesh:
        nm["weights"] = old_mesh["weights"][:]
    else:
        nm.pop("weights", None)
    for pi, (old, new) in enumerate(zip(old_mesh["primitives"], new_mesh["primitives"])):
        check(len(new["targets"]) == old_count + 4 and new["targets"][:old_count] == old["targets"],
              f"Primitive {pi} original morph declarations changed")
        nm["primitives"][pi]["targets"] = new["targets"][:old_count]
    for index, old_node in enumerate(before["nodes"]):
        if old_node.get("mesh") == mesh_index and "weights" in old_node:
            check(after["nodes"][index].get("weights") == old_node["weights"] + [0.] * 4,
                  f"Node {index} weights changed beyond zero append")
            normalized["nodes"][index]["weights"] = old_node["weights"][:]
    check(normalized == before, "Protected JSON changed outside declared additions")
    check(node_index in candidate.active_nodes(), "Source face is no longer in the active scene")
    allowed, mouth, center = _regions(source, old_mesh)
    used_accessors, used_views = set(), set()
    support_counts, repeated_counts = {}, {}
    for pi, (old, new) in enumerate(zip(old_mesh["primitives"], new_mesh["primitives"])):
        xyz = source.accessor(old["attributes"]["POSITION"])
        smile = source.accessor(old["targets"][names.index("eye_smile")]["POSITION"])
        close = source.accessor(old["targets"][names.index("eye_close")]["POSITION"])
        brow_support = set()
        for brow in ("neuro_brow_raise_L", "neuro_brow_raise_R"):
            values = source.accessor(old["targets"][names.index(brow)]["POSITION"])
            brow_support |= {i for i, row in enumerate(values) if any(v != 0 for v in row)}
        duplicate_positions = {}
        for index, position in enumerate(xyz):
            duplicate_positions.setdefault(position, []).append(index)
        groups = [indices for indices in duplicate_positions.values() if len(indices) > 1]
        repeated_counts[str(pi)] = len(groups)
        for ti, name in enumerate(TARGETS):
            target = new["targets"][old_count + ti]
            check(set(target) == {"POSITION"}, f"{name} adds unapproved attributes")
            ai = target["POSITION"]
            check(old_accessors <= ai < len(after["accessors"]), f"{name} must use a new accessor")
            used_accessors.add(ai)
            accessor = after["accessors"][ai]
            check(accessor.get("type") == "VEC3" and accessor.get("componentType") == 5126
                  and accessor.get("count") == len(xyz) and "bufferView" not in accessor
                  and not accessor.get("normalized", False), f"{name} accessor declaration is invalid")
            values = candidate.accessor(ai)
            check(len(values) == len(xyz) and all(math.isfinite(v) for row in values for v in row),
                  f"{name} has invalid data")
            sign = 1 if name.endswith("_L") else -1
            expected = []
            for index, position in enumerate(xyz):
                side = sign * position[0]
                if index not in allowed[pi] or side <= .0001:
                    expected.append((0., 0., 0.))
                    continue
                side_gain = _weight(.0001, .008, side)
                if "lower_lid" in name:
                    gain = .30 * side_gain * _weight(0., .001, close[index][1])
                    native = close[index]
                else:
                    gain, native = side_gain, smile[index]
                expected.append(tuple(struct.unpack("<f", struct.pack("<f", v * gain))[0] for v in native))
            # The two independent smoothstep implementations may round at the
            # final float32 boundary; allow <1 nm, far below a rendered pixel.
            check(all(all(abs(a - b) <= 1e-9 for a, b in zip(actual, wanted))
                      for actual, wanted in zip(values, expected)),
                  f"{name} violates filtered source-derived ocular rule")
            support = {index for index, row in enumerate(values) if any(v != 0 for v in row)}
            check(not support & brow_support, f"{name} moves eyebrow vertices")
            check(all(sign * xyz[index][0] > .0001 for index in support), f"{name} leaks to opposite side/midline")
            if pi == 0:
                check(not support & (mouth | center), f"{name} leaks into excluded mouth/midline")
            if "lower_lid" in name:
                check(all(close[index][1] > 0 for index in support), f"{name} moves negative-dy upper contribution")
                check(max((row[1] for row in values), default=0) <= .0036,
                      f"{name} exceeds conservative lower contribution displacement")
            check(all(len({values[index] for index in indices}) == 1 for indices in groups),
                  f"{name} introduces an exact-position/UV seam displacement mismatch")
            support_counts[f"{pi}:{name}"] = len(support)
            expected_min = [min(row[k] for row in values) for k in range(3)]
            expected_max = [max(row[k] for row in values) for k in range(3)]
            check(accessor.get("min") == expected_min and accessor.get("max") == expected_max,
                  f"{name} accessor bounds differ from stored data")
            if support:
                sparse = accessor.get("sparse", {})
                check(sparse.get("count") == len(support) and sparse.get("indices", {}).get("componentType") == 5123,
                      f"{name} sparse count/type is invalid")
                iv, vv = sparse["indices"]["bufferView"], sparse["values"]["bufferView"]
                used_views |= {iv, vv}
                check(all(old_views <= vi < len(after["bufferViews"]) for vi in (iv, vv)), f"{name} sparse view is not newly appended")
                check(all("target" not in after["bufferViews"][vi] for vi in (iv, vv)), f"{name} sparse view has illegal GPU target")
                indices = candidate._read(iv, sparse["indices"].get("byteOffset", 0), sparse["count"], 5123, 1)
                check([row[0] for row in indices] == sorted(support), f"{name} sparse indices are not exact/strictly increasing")
            else:
                check("sparse" not in accessor, f"{name} unexpectedly contains zero sparse support")
    check(used_accessors == set(range(old_accessors, len(after["accessors"]))), "Unapproved unused accessor added")
    check(used_views == set(range(old_views, len(after["bufferViews"]))), "Unapproved unused buffer view added")
    for vi in used_views:
        view = after["bufferViews"][vi]
        check(view.get("buffer", 0) == 0 and view.get("byteOffset", 0) >= len(source.binary)
              and view.get("byteOffset", 0) % 4 == 0, "New sparse view overlaps original bytes or is misaligned")
    return {
        "passed": True, "checks": checks,
        "originalBinaryPrefixExact": True, "originalJsonExactOutsideDeclaredAdditions": True,
        "neutralGeometryTopologyMaterialsTexturesExact": True, "allV2MorphDataExact": True,
        "rigSkinBindExpressionsLookAtLicenseExact": True, "newDefaultWeightsZero": True,
        "modelSidesDisjoint": True, "browLeakage": False,
        "sourceSmileMouthVerticesExcluded": len(mouth), "sourceSmileMidlineVerticesExcluded": len(center),
        "lowerPositiveDyContributionOnly": True, "continuousDyAndMidlineFeather": True,
        "exactPositionSeamDeltaMismatches": 0, "exactPositionDuplicateGroups": repeated_counts,
        "normalMorphsAdded": False, "supportCounts": support_counts,
        "anatomicLowerLidProven": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", nargs="?", type=Path, default=ROOT / "web/models/seed-daily-lids.vrm")
    parser.add_argument("--reference", type=Path, default=ROOT / "web/models/seed-daily-eyes.vrm")
    args = parser.parse_args()
    result = validate(Model(args.reference), Model(args.candidate))
    result["referenceSHA256"] = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    result["candidateSHA256"] = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
