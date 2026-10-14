"""Meaningful source-preservation and local eyelid asset tamper tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from build_seed_eye_lids import build, SOURCE, OUTPUT, TARGETS
from validate_seed_daily import Model
from validate_seed_eye_lids import validate


class LocalEyelidContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Model(SOURCE)

    def candidate(self):
        return Model(OUTPUT)

    def target_accessor(self, candidate, name, primitive=0):
        mesh = candidate.json["meshes"][2]
        ti = mesh["extras"]["targetNames"].index(name)
        return mesh["primitives"][primitive]["targets"][ti]["POSITION"]

    def modify_sparse_index(self, candidate, name, vertex, primitive=0):
        ai = self.target_accessor(candidate, name, primitive)
        sparse = candidate.json["accessors"][ai]["sparse"]
        view = candidate.json["bufferViews"][sparse["indices"]["bufferView"]]
        binary = bytearray(candidate.binary)
        struct.pack_into("<H", binary, view["byteOffset"], vertex)
        candidate.binary = bytes(binary)

    def test_candidate_protection_and_deterministic_rebuild(self):
        result = validate(self.source, self.candidate())
        self.assertEqual(result["sourceSmileMouthVerticesExcluded"], 14)
        self.assertEqual(result["sourceSmileMidlineVerticesExcluded"], 3)
        self.assertEqual(result["exactPositionSeamDeltaMismatches"], 0)
        self.assertEqual(result["supportCounts"]["0:neuro_lower_lid_raise_L"], 137)
        self.assertEqual(result["supportCounts"]["1:neuro_lower_lid_raise_L"], 29)
        source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            output, manifest = Path(folder) / "candidate.vrm", Path(folder) / "build.json"
            build(SOURCE, output, manifest)
            self.assertEqual(output.read_bytes(), OUTPUT.read_bytes())
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), source_hash)

    def test_rejects_source_geometry_or_texture_byte_change(self):
        candidate = self.candidate()
        binary = bytearray(candidate.binary)
        binary[len(self.source.binary) // 2] ^= 1
        candidate.binary = bytes(binary)
        with self.assertRaisesRegex(ValueError, "Original binary prefix"):
            validate(self.source, candidate)

    def test_rejects_meta_or_expression_change(self):
        for field in ("meta", "expressions"):
            candidate = self.candidate()
            extension = candidate.json["extensions"]["VRMC_vrm"]
            if field == "meta":
                extension["meta"]["creditNotation"] = "unnecessary"
            else:
                extension["expressions"]["preset"]["blink"]["morphTargetBinds"][0]["weight"] = .5
            with self.assertRaisesRegex(ValueError, "Protected JSON"):
                validate(self.source, candidate)

    def test_rejects_existing_brow_morph_change(self):
        candidate = self.candidate()
        mesh = candidate.json["meshes"][2]
        ti = mesh["extras"]["targetNames"].index("neuro_brow_raise_L")
        mesh["primitives"][1]["targets"][ti]["POSITION"] = self.target_accessor(candidate, TARGETS[2], 1)
        with self.assertRaisesRegex(ValueError, "original morph declarations"):
            validate(self.source, candidate)

    def test_rejects_neutral_activation(self):
        candidate = self.candidate()
        candidate.json["meshes"][2]["weights"][-4] = .1
        with self.assertRaisesRegex(ValueError, "default weights"):
            validate(self.source, candidate)

    def test_rejects_smile_opposite_side_mouth_or_midline_leak(self):
        source_mesh = self.source.json["meshes"][2]
        positions = self.source.accessor(source_mesh["primitives"][0]["attributes"]["POSITION"])
        for vertex in (4153, 2527, next(i for i, position in enumerate(positions) if abs(position[0]) < .0001)):
            candidate = self.candidate()
            self.modify_sparse_index(candidate, TARGETS[0], vertex)
            with self.assertRaisesRegex(ValueError, "filtered source-derived ocular rule"):
                validate(self.source, candidate)

    def test_rejects_lower_upper_or_brow_leak(self):
        for vertex in (0, 578):
            candidate = self.candidate()
            self.modify_sparse_index(candidate, TARGETS[2], vertex, 1)
            with self.assertRaisesRegex(ValueError, "filtered source-derived ocular rule"):
                validate(self.source, candidate)

    def test_rejects_excessive_or_seam_distorting_delta(self):
        candidate = self.candidate()
        accessor = candidate.json["accessors"][self.target_accessor(candidate, TARGETS[2])]
        view = candidate.json["bufferViews"][accessor["sparse"]["values"]["bufferView"]]
        binary = bytearray(candidate.binary)
        struct.pack_into("<fff", binary, view["byteOffset"], .001, .02, .001)
        candidate.binary = bytes(binary)
        with self.assertRaisesRegex(ValueError, "filtered source-derived ocular rule"):
            validate(self.source, candidate)

    def test_rejects_new_normal_morph(self):
        candidate = self.candidate()
        mesh = candidate.json["meshes"][2]
        mesh["primitives"][0]["targets"][-1]["NORMAL"] = mesh["primitives"][0]["attributes"]["NORMAL"]
        with self.assertRaisesRegex(ValueError, "unapproved attributes"):
            validate(self.source, candidate)

    def test_builder_never_overwrites_source(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
                build(SOURCE, SOURCE, Path(folder) / "manifest.json")


if __name__ == "__main__":
    unittest.main()
