"""Append isolated vertical brow controls to a separate Daily VRM candidate.

Only four POSITION morph targets and their zero weights are added. The neutral
mesh, existing morphs, embedded images, rig, VRM metadata and expressions stay
byte-identical. No renderer, NumPy or Blender dependency is required.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct

from validate_seed_daily import Model


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "web/models/seed-daily.vrm"
DEFAULT_OUTPUT = ROOT / "web/models/seed-daily-eyes.vrm"
DEFAULT_MANIFEST = ROOT / "web/models/seed-daily-eyes-source/build.json"
TARGETS = (
    "neuro_brow_raise_L", "neuro_brow_raise_R",
    "neuro_brow_lower_L", "neuro_brow_lower_R",
)
DEFAULT_DISPLACEMENT = 0.0045


def connected_components(model: Model, primitive: dict) -> list[list[int]]:
    count = model.json["accessors"][primitive["attributes"]["POSITION"]]["count"]
    parent = list(range(count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    indices = [row[0] for row in model.accessor(primitive["indices"])]
    if primitive.get("mode", 4) != 4 or len(indices) % 3:
        raise ValueError("Brow selection requires indexed triangle geometry")
    for offset in range(0, len(indices), 3):
        a, b, c = indices[offset:offset + 3]
        if not all(0 <= index < count for index in (a, b, c)):
            raise ValueError("Source triangle index exceeds vertex count")
        parent[find(a)] = find(b)
        parent[find(b)] = find(c)
    groups = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda indices: indices[0])


def identify_brows(model: Model) -> dict:
    doc = model.json
    matches = [(i, node) for i, node in enumerate(doc["nodes"])
               if node.get("name") == "head" and "mesh" in node]
    if len(matches) != 1:
        raise ValueError("Expected exactly one source head mesh node")
    node_index, node = matches[0]
    mesh_index = node["mesh"]
    mesh = doc["meshes"][mesh_index]
    names = mesh.get("extras", {}).get("targetNames", [])
    if any(name in names for name in TARGETS):
        raise ValueError("Source already contains the proposed targets")
    material_primitives = [
        (index, primitive) for index, primitive in enumerate(mesh["primitives"])
        if doc["materials"][primitive["material"]].get("name") == "eye"
    ]
    if len(material_primitives) != 1:
        raise ValueError("Expected one source eye material primitive")
    primitive_index, primitive = material_primitives[0]
    positions = model.accessor(primitive["attributes"]["POSITION"])
    components = connected_components(model, primitive)
    selections = {}
    for side, sign in (("L", 1), ("R", -1)):
        up = model.accessor(primitive["targets"][names.index("eye_brow_up_" + side)]["POSITION"])
        down = model.accessor(primitive["targets"][names.index("eye_brow_down_" + side)]["POSITION"])
        old_support = {index for index, row in enumerate(up) if any(abs(x) > 1e-8 for x in row)}
        candidates = [indices for indices in components if set(indices) == old_support]
        if len(candidates) != 1:
            raise ValueError(f"{side} old brow support is not one complete connected component")
        indices = candidates[0]
        if len(indices) != 41:
            raise ValueError(f"Unexpected source {side} brow vertex count")
        if not all(sign * positions[i][0] > 0.015 and 1.445 < positions[i][1] < 1.47
                   and 0.07 < positions[i][2] < 0.11 for i in indices):
            raise ValueError(f"{side} component is outside the independent eyebrow geometry bounds")
        if {index for index, row in enumerate(down) if any(abs(x) > 1e-8 for x in row)} != set(indices):
            raise ValueError(f"{side} raise/lower source support differs")
        for name in ("eye_close", "eye_open", "eye_smile", "blink_L", "blink_R"):
            values = model.accessor(primitive["targets"][names.index(name)]["POSITION"])
            if any(any(abs(x) > 1e-8 for x in values[i]) for i in indices):
                raise ValueError(f"{side} selection intersects {name} geometry")
        joints = model.accessor(primitive["attributes"]["JOINTS_0"])
        weights = model.accessor(primitive["attributes"]["WEIGHTS_0"])
        if not all(joints[i] == (0, 0, 0, 0) and weights[i] == (1.0, 0.0, 0.0, 0.0)
                   for i in indices):
            raise ValueError(f"{side} brow is not rigidly bound to skin joint zero")
        skin = doc["skins"][node["skin"]]
        if len(skin["joints"]) != 1 or skin["joints"][0] != doc["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["head"]["node"]:
            raise ValueError("Selected primitive is not rigidly bound to the humanoid head")
        selections[side] = {
            "vertices": indices,
            "vertexCount": len(indices),
            "bounds": [[min(positions[i][k] for i in indices), max(positions[i][k] for i in indices)]
                       for k in range(3)],
            "sourceUpSupportComplete": True,
            "sourceDownSupportComplete": True,
            "blinkAndEyeOpenSupportDisjoint": True,
            "rigidHeadJoint": skin["joints"][0],
            "localXSign": sign,
            "nativeSideMapping": "eye_brow_up_" + side,
        }
    if set(selections["L"]["vertices"]) & set(selections["R"]["vertices"]):
        raise ValueError("Brow selections overlap")
    return {"node": node_index, "mesh": mesh_index, "primitive": primitive_index,
            "oldTargetCount": len(names), "sides": selections}


def build(source: Path, output: Path, manifest_path: Path,
          displacement: float = DEFAULT_DISPLACEMENT) -> dict:
    if source.resolve() == output.resolve():
        raise ValueError("The original asset cannot be overwritten")
    if not 0.003 <= displacement <= 0.006:
        raise ValueError("Full-weight brow displacement must remain between 3 and 6 mm")
    model = Model(source)
    selection = identify_brows(model)
    doc = copy.deepcopy(model.json)
    if len(doc.get("buffers", [])) != 1 or "uri" in doc["buffers"][0]:
        raise ValueError("Only a self-contained GLB buffer is supported")
    binary = bytearray(model.binary)
    mesh = doc["meshes"][selection["mesh"]]
    old_count = selection["oldTargetCount"]
    if any(len(p.get("targets", [])) != old_count for p in mesh["primitives"]):
        raise ValueError("Source primitives have inconsistent morph target counts")

    def append_view(data: bytes) -> int:
        binary.extend(b"\0" * (-len(binary) % 4))
        index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary.extend(data)
        return index

    def target_accessor(count: int, indices: list[int] | None, amount: float) -> int:
        accessor = {"componentType": 5126, "count": count, "type": "VEC3",
                    "min": [0.0, min(0.0, amount), 0.0],
                    "max": [0.0, max(0.0, amount), 0.0]}
        if indices:
            index_bytes = struct.pack("<" + "H" * len(indices), *indices)
            value_bytes = b"".join(struct.pack("<fff", 0.0, amount, 0.0) for _ in indices)
            accessor["sparse"] = {
                "count": len(indices),
                "indices": {"bufferView": append_view(index_bytes), "componentType": 5123},
                "values": {"bufferView": append_view(value_bytes)},
            }
        index = len(doc["accessors"])
        doc["accessors"].append(accessor)
        return index

    zero_accessors = {}
    for primitive_index, primitive in enumerate(mesh["primitives"]):
        count = doc["accessors"][primitive["attributes"]["POSITION"]]["count"]
        for target_index, name in enumerate(TARGETS):
            if primitive_index == selection["primitive"]:
                side = name[-1]
                amount = displacement if "raise" in name else -displacement
                accessor_index = target_accessor(count, selection["sides"][side]["vertices"], amount)
            else:
                if count not in zero_accessors:
                    zero_accessors[count] = target_accessor(count, None, 0.0)
                accessor_index = zero_accessors[count]
            primitive["targets"].append({"POSITION": accessor_index})
    mesh["extras"]["targetNames"].extend(TARGETS)
    weights = mesh.get("weights", [0.0] * old_count)
    if len(weights) != old_count:
        raise ValueError("Source default morph weights have an invalid count")
    mesh["weights"] = weights + [0.0] * len(TARGETS)
    for node in doc["nodes"]:
        if node.get("mesh") == selection["mesh"] and "weights" in node:
            if len(node["weights"]) != old_count:
                raise ValueError("Source node morph weights have an invalid count")
            node["weights"].extend([0.0] * len(TARGETS))
    doc["buffers"][0]["byteLength"] = len(binary)
    json_bytes = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    output_bytes = (struct.pack("<III", 0x46546C67, 2, 28 + len(json_bytes) + len(binary))
                    + struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
                    + struct.pack("<II", len(binary), 0x004E4942) + binary)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(output_bytes)
    # Independent validation must succeed before the build manifest is emitted.
    from validate_seed_eye_expression import validate
    validation = validate(model, Model(output), displacement)
    manifest = {
        "schemaVersion": 1,
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "output": str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else str(output),
        "sourceSHA256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "outputSHA256": hashlib.sha256(output_bytes).hexdigest(),
        "outputBytes": len(output_bytes),
        "builder": "scripts/build_seed_eye_expression.py",
        "validator": "scripts/validate_seed_eye_expression.py",
        "candidate": True,
        "userApprovedCharacter": False,
        "browserVisualAcceptance": "pending; inspect fixed camera, one side at a time, half/full weights, blink and look-at",
        "targets": list(TARGETS),
        "fullWeightDisplacementMetres": displacement,
        "localDelta": "(0, +displacement, 0) raise; (0, -displacement, 0) lower",
        "normals": "unchanged: each affected component translates rigidly",
        "screenHalfWeightEstimatePixels": [3, 6],
        "screenEstimateBasis": "720px fixed Daily eye-focus scene; estimate, not a measured acceptance result",
        "selection": selection,
        "skin": {
            "skin": model.json["nodes"][selection["node"]]["skin"],
            "inverseBindMatrix": list(model.accessor(model.json["skins"][model.json["nodes"][selection["node"]]["skin"]]["inverseBindMatrices"])[0]),
            "axisNote": "Pure model-local Y. Actual camera-space displacement follows unchanged head skinning and pose; browser checks required.",
        },
        "protection": validation,
        "license": copy.deepcopy(model.json["extensions"]["VRMC_vrm"]["meta"]),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--displacement", type=float, default=DEFAULT_DISPLACEMENT)
    args = parser.parse_args()
    manifest = build(args.source.resolve(), args.output.resolve(), args.manifest.resolve(), args.displacement)
    print(json.dumps({"output": manifest["output"], "sha256": manifest["outputSHA256"],
                      "targets": manifest["targets"], "validation": manifest["protection"]}, indent=2))


if __name__ == "__main__":
    main()
