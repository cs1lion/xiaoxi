"""Regression checks that additive brow validation rejects harmful revisions."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from build_seed_eye_expression import build, DEFAULT_SOURCE, DEFAULT_OUTPUT, TARGETS
from validate_seed_daily import Model
from validate_seed_eye_expression import validate


class BrowAssetContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = Model(DEFAULT_SOURCE)

    def candidate(self):
        return Model(DEFAULT_OUTPUT)

    def new_accessor(self, model, name):
        mesh = model.json["meshes"][2]
        target_index = mesh["extras"]["targetNames"].index(name)
        return mesh["primitives"][1]["targets"][target_index]["POSITION"]

    def change_first_delta(self, model, name, delta):
        accessor = model.json["accessors"][self.new_accessor(model, name)]
        view = model.json["bufferViews"][accessor["sparse"]["values"]["bufferView"]]
        binary = bytearray(model.binary)
        struct.pack_into("<fff", binary, view["byteOffset"], *delta)
        model.binary = bytes(binary)
        model.cache.clear()

    def test_valid_candidate_and_reproducible_build(self):
        result = validate(self.reference, self.candidate())
        self.assertTrue(result["passed"])
        self.assertEqual(result["browVerticesPerSide"], {"L": 41, "R": 41})
        original_hash = hashlib.sha256(DEFAULT_SOURCE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "candidate.vrm"
            manifest = Path(folder) / "build.json"
            build(DEFAULT_SOURCE, output, manifest)
            self.assertEqual(output.read_bytes(), DEFAULT_OUTPUT.read_bytes())
        self.assertEqual(hashlib.sha256(DEFAULT_SOURCE.read_bytes()).hexdigest(), original_hash)

    def test_rejects_original_binary_edits(self):
        candidate = self.candidate()
        binary = bytearray(candidate.binary)
        binary[0] ^= 1
        candidate.binary = bytes(binary)
        with self.assertRaisesRegex(ValueError, "Original GLB binary"):
            validate(self.reference, candidate)

    def test_rejects_license_and_expression_edits(self):
        for field in ("meta", "expressions"):
            candidate = self.candidate()
            vrm = candidate.json["extensions"]["VRMC_vrm"]
            if field == "meta":
                vrm["meta"]["authors"] = ["Changed author"]
            else:
                vrm["expressions"]["preset"]["blink"]["morphTargetBinds"][0]["weight"] = 0.25
            with self.assertRaisesRegex(ValueError, "Protected model JSON"):
                validate(self.reference, candidate)

    def test_rejects_nonzero_neutral_weight(self):
        candidate = self.candidate()
        candidate.json["meshes"][2]["weights"][-1] = 0.1
        with self.assertRaisesRegex(ValueError, "default morph weights"):
            validate(self.reference, candidate)

    def test_rejects_side_leakage(self):
        candidate = self.candidate()
        accessor = candidate.json["accessors"][self.new_accessor(candidate, TARGETS[0])]
        view = candidate.json["bufferViews"][accessor["sparse"]["indices"]["bufferView"]]
        binary = bytearray(candidate.binary)
        struct.pack_into("<H", binary, view["byteOffset"], 704)
        candidate.binary = bytes(binary)
        with self.assertRaisesRegex(ValueError, "complete single-side eyebrow"):
            validate(self.reference, candidate)

    def test_rejects_partial_component(self):
        candidate = self.candidate()
        self.change_first_delta(candidate, TARGETS[0], (0, 0, 0))
        with self.assertRaisesRegex(ValueError, "complete single-side eyebrow"):
            validate(self.reference, candidate)

    def test_rejects_nonvertical_or_distorting_delta(self):
        for delta in ((0.001, 0.0045, 0), (0, 0.0045, 0.001), (0, 0.0055, 0)):
            candidate = self.candidate()
            self.change_first_delta(candidate, TARGETS[0], delta)
            with self.assertRaisesRegex(ValueError, "uniform pure-Y"):
                validate(self.reference, candidate)

    def test_rejects_original_morph_rebinding(self):
        candidate = self.candidate()
        primitive = candidate.json["meshes"][2]["primitives"][1]
        primitive["targets"][0]["POSITION"] = primitive["targets"][-1]["POSITION"]
        with self.assertRaisesRegex(ValueError, "existing morph declarations changed"):
            validate(self.reference, candidate)

    def test_builder_rejects_overwrite_and_excessive_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "build.json"
            with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
                build(DEFAULT_SOURCE, DEFAULT_SOURCE, manifest)
            with self.assertRaisesRegex(ValueError, "between 3 and 6 mm"):
                build(DEFAULT_SOURCE, Path(folder) / "candidate.vrm", manifest, 0.02)


if __name__ == "__main__":
    unittest.main()
