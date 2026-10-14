"""Append filtered one-sided smile and lower-lid contribution to Daily Eyes.

Build a separate candidate. Existing geometry, morphs and VRM metadata remain
byte-exact. New controls are source-derived POSITION morphs, not a new rig or
a reconstruction of separate upper/lower eyelid topology.
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
SOURCE = ROOT / "web/models/seed-daily-eyes.vrm"
OUTPUT = ROOT / "web/models/seed-daily-lids.vrm"
MANIFEST = ROOT / "web/models/seed-daily-lids-source/build.json"
TARGETS = ("neuro_eye_smile_L", "neuro_eye_smile_R", "neuro_lower_lid_raise_L", "neuro_lower_lid_raise_R")
LOWER_SCALE = 0.30
LOWER_FEATHER = 0.001
SIDE_FEATHER = 0.008
SIDE_DEAD_ZONE = 0.0001


def smoothstep(edge0, edge1, value):
    t = min(1.0, max(0.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def components(model, primitive):
    count = model.json["accessors"][primitive["attributes"]["POSITION"]]["count"]
    parent = list(range(count))

    def find(index):
        while index != parent[index]:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    indices = [row[0] for row in model.accessor(primitive["indices"])]
    if primitive.get("mode", 4) != 4 or len(indices) % 3:
        raise ValueError("The source head must use indexed triangle geometry")
    for offset in range(0, len(indices), 3):
        a, b, c = indices[offset:offset + 3]
        if not all(0 <= i < count for i in (a, b, c)):
            raise ValueError("Invalid triangle index in source head")
        parent[find(a)] = find(b)
        parent[find(b)] = find(c)
    groups = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda ids: ids[0])


def select_regions(source):
    doc = source.json
    nodes = [i for i, n in enumerate(doc["nodes"]) if n.get("name") == "head" and "mesh" in n]
    if len(nodes) != 1:
        raise ValueError("Expected exactly one head mesh node")
    node = nodes[0]
    mesh_index = doc["nodes"][node]["mesh"]
    mesh = doc["meshes"][mesh_index]
    names = mesh["extras"]["targetNames"]
    if any(name in names for name in TARGETS):
        raise ValueError("Source already contains V3 eyelid channels")
    if not all(name in names for name in ("neuro_brow_raise_L", "neuro_brow_raise_R", "neuro_brow_lower_L", "neuro_brow_lower_R")):
        raise ValueError("V3 source must retain all V2 independent brow controls")
    if len(mesh["primitives"]) != 3:
        raise ValueError("Unexpected Daily Eyes primitive count")
    allowed, evidence = {}, []
    for pi, primitive in enumerate(mesh["primitives"]):
        positions = source.accessor(primitive["attributes"]["POSITION"])
        native = source.accessor(primitive["targets"][names.index("eye_smile")]["POSITION"])
        groups = components(source, primitive)
        material = doc["materials"][primitive["material"]]["name"]
        if (pi, material) == (0, "body_bake"):
            main = [group for group in groups if len(group) == 3250]
            inner = [group for group in groups if len(group) == 179 and all(
                1.37 < positions[i][1] < 1.445 and positions[i][2] > 0.05 for i in group)]
            if len(main) != 1 or len(inner) != 2:
                raise ValueError("Could not identify the face eye-socket and two internal-eye components")
            chosen = main + inner
        elif (pi, material) == (1, "eye"):
            chosen = [group for group in groups if any(any(abs(v) > 1e-8 for v in native[i]) for i in group)]
            if sorted(len(group) for group in chosen) != [8, 8, 8, 8, 14, 14, 36, 36, 71, 71]:
                raise ValueError("Native smile does not isolate the ten expected eyelash/eye-line components")
            if not all(all(any(abs(v) > 1e-8 for v in native[i]) for i in group) for group in chosen):
                raise ValueError("A smile eye-line component is only partially supported")
        elif (pi, material) == (2, "eye_trans"):
            chosen = []
        else:
            raise ValueError("Unexpected source face material/primitive layout")
        allowed[pi] = {i for group in chosen for i in group}
        support = {i for i, row in enumerate(native) if any(abs(v) > 1e-8 for v in row)}
        excluded = support - allowed[pi]
        midline = {i for i in support & allowed[pi] if abs(positions[i][0]) <= SIDE_DEAD_ZONE}
        if pi == 0 and (len(excluded) != 14 or len(midline) != 3):
            raise ValueError("Unexpected mouth/midline contamination in source smile")
        evidence.append({
            "primitive": pi, "material": material,
            "allowedComponents": [{"firstVertex": min(group), "vertexCount": len(group),
                                   "vertices": group} for group in chosen],
            "excludedSourceSmileVertices": sorted(excluded),
            "excludedSourceSmileMidlineVertices": sorted(midline),
            "sourceHasNormalMorph": "NORMAL" in primitive["targets"][names.index("eye_smile")],
        })
    return {"node": node, "mesh": mesh_index, "oldTargetCount": len(names),
            "allowed": allowed, "evidence": evidence}


def compute_targets(source, regions):
    mesh = source.json["meshes"][regions["mesh"]]
    names = mesh["extras"]["targetNames"]
    results = {}
    for pi, primitive in enumerate(mesh["primitives"]):
        positions = source.accessor(primitive["attributes"]["POSITION"])
        smile = source.accessor(primitive["targets"][names.index("eye_smile")]["POSITION"])
        close = source.accessor(primitive["targets"][names.index("eye_close")]["POSITION"])
        results[pi] = {}
        for name in TARGETS:
            sign = 1 if name.endswith("_L") else -1
            output = []
            for i, position in enumerate(positions):
                side = sign * position[0]
                if i not in regions["allowed"][pi] or side <= SIDE_DEAD_ZONE:
                    output.append((0.0, 0.0, 0.0))
                    continue
                side_weight = smoothstep(SIDE_DEAD_ZONE, SIDE_FEATHER, side)
                if "lower_lid" in name:
                    gain = LOWER_SCALE * side_weight * smoothstep(0.0, LOWER_FEATHER, close[i][1])
                    row = close[i]
                else:
                    gain = side_weight
                    row = smile[i]
                # Explicit float32 quantization makes the manifest, sparse
                # accessor and independent validator compare the stored data.
                output.append(tuple(struct.unpack("<f", struct.pack("<f", v * gain))[0] for v in row))
            results[pi][name] = output
    return results


def build(source_path=SOURCE, output_path=OUTPUT, manifest_path=MANIFEST):
    source_path, output_path, manifest_path = map(Path, (source_path, output_path, manifest_path))
    if source_path.resolve() == output_path.resolve():
        raise ValueError("Source asset cannot be overwritten")
    source = Model(source_path)
    regions = select_regions(source)
    targets = compute_targets(source, regions)
    doc = copy.deepcopy(source.json)
    binary = bytearray(source.binary)
    mesh = doc["meshes"][regions["mesh"]]
    if len(doc["buffers"]) != 1 or "uri" in doc["buffers"][0]:
        raise ValueError("Source must be a self-contained GLB")

    def view(data):
        binary.extend(b"\0" * (-len(binary) % 4))
        index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary.extend(data)
        return index

    zeros, summaries = {}, []
    for pi, primitive in enumerate(mesh["primitives"]):
        count = doc["accessors"][primitive["attributes"]["POSITION"]]["count"]
        for name in TARGETS:
            values = targets[pi][name]
            indices = [i for i, row in enumerate(values) if any(v != 0 for v in row)]
            if not indices and count in zeros:
                accessor_index = zeros[count]
            else:
                accessor = {"componentType": 5126, "count": count, "type": "VEC3",
                            "min": [min(row[k] for row in values) for k in range(3)],
                            "max": [max(row[k] for row in values) for k in range(3)]}
                if indices:
                    accessor["sparse"] = {
                        "count": len(indices),
                        "indices": {"bufferView": view(struct.pack("<" + "H" * len(indices), *indices)), "componentType": 5123},
                        "values": {"bufferView": view(b"".join(struct.pack("<fff", *values[i]) for i in indices))},
                    }
                accessor_index = len(doc["accessors"])
                doc["accessors"].append(accessor)
                if not indices:
                    zeros[count] = accessor_index
            primitive["targets"].append({"POSITION": accessor_index})
            positions = source.accessor(primitive["attributes"]["POSITION"])
            summaries.append({"primitive": pi, "target": name, "vertexCount": len(indices),
                              "support": indices,
                              "supportBounds": [[min(positions[i][k] for i in indices), max(positions[i][k] for i in indices)]
                                                for k in range(3)] if indices else None,
                              "deltaMin": [min(row[k] for row in values) for k in range(3)],
                              "deltaMax": [max(row[k] for row in values) for k in range(3)]})
    old_count = regions["oldTargetCount"]
    mesh["extras"]["targetNames"].extend(TARGETS)
    mesh["weights"] = mesh.get("weights", [0.0] * old_count) + [0.0] * 4
    for node in doc["nodes"]:
        if node.get("mesh") == regions["mesh"] and "weights" in node:
            node["weights"].extend([0.0] * 4)
    doc["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    raw = (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
           + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
           + struct.pack("<II", len(binary), 0x004E4942) + binary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    from validate_seed_eye_lids import validate
    validation = validate(source, Model(output_path))
    manifest = {
        "schemaVersion": 1, "candidate": True, "userApprovedCharacter": False,
        "source": source_path.name, "output": output_path.name,
        "sourceSHA256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "outputSHA256": hashlib.sha256(raw).hexdigest(), "outputBytes": len(raw),
        "builder": "scripts/build_seed_eye_lids.py", "validator": "scripts/validate_seed_eye_lids.py",
        "targets": list(TARGETS),
        "sideConvention": {"L": "source native/model local X positive; screen right in current Daily front camera",
                           "R": "source native/model local X negative; screen left in current Daily front camera"},
        "smileDefinition": "filtered original eye_smile ocular region, one model side, smooth near midline; mouth/midline/hidden effect excluded",
        "lowerDefinition": "source eye_close positive-dy local ocular contribution; continuous dy feather; not proof of anatomically separated eyelid rig",
        "lowerScale": LOWER_SCALE, "lowerDyFeatherMetres": LOWER_FEATHER,
        "sideFeatherMetres": [SIDE_DEAD_ZONE, SIDE_FEATHER],
        "runtimeWeightLimits": {"smile": [0, 1], "lower": [0, 1]},
        "blinkComposition": "runtime must attenuate new smile/lower by per-eye remaining opening and recover original weights after blink",
        "normalHandling": "source smile/close targets contain POSITION only; preserve base normals and add POSITION only, same rendering assumption as source",
        "regions": regions["evidence"], "targetSupport": summaries,
        "protection": validation,
        "browserVisualAcceptance": "pending: single side 0/0.5/1; upper edge stability and lower edge rising; asymmetric smile; layered contours/UV seams; staged blink and recovery; look-at plus mouth",
        "license": copy.deepcopy(source.json["extensions"]["VRMC_vrm"]["meta"]),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    manifest = build(args.source, args.output, args.manifest)
    print(json.dumps({k: manifest[k] for k in ("output", "outputSHA256", "outputBytes", "targets", "protection")}, indent=2))


if __name__ == "__main__":
    main()
