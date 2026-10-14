"""Contract tests for image evidence, fixed registration, and closure reporting."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

spec = importlib.util.spec_from_file_location("eye_analysis", Path(__file__).with_name("analyze-eye-calibration.py"))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class CalibrationAnalysisTests(unittest.TestCase):
    def test_normalized_roi_uses_actual_image_dimensions(self):
        self.assertEqual(analysis.roi_rect([.25, .1, .5, .4], (120, 80)), (30, 8, 90, 40))
        with self.assertRaises(ValueError):
            analysis.roi_rect([.8, .1, .5, .4], (120, 80))

    def test_threshold_components_anchor_and_contour_units(self):
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:5, 3:7] = True
        mask[8, 8] = True
        result = analysis.summarize_mask(mask, (20, 10, 30, 20), (100, 100), [.25, .15])
        self.assertEqual(result["areaPx"], 13)
        self.assertEqual([x["areaPx"] for x in result["components"]], [12, 1])
        self.assertAlmostEqual(result["areaFractionOfRoi"], .13)
        shifted = np.roll(mask, 1, axis=1)
        image = Image.new("RGB", (10, 10), "white")
        distance = analysis.compare_images(image, image, (0, 0, 10, 10), mask, shifted)
        self.assertEqual(distance["rgbMaeNormalized"], 0)
        self.assertLess(distance["maskIoU"], 1)
        self.assertGreater(distance["edgeDistance"]["bidirectionalMeanPx"], 0)

    def test_closure_depends_on_image_and_comparison_rejects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            config = {"rois": {"iris_left": [.1, .1, .5, .5]}, "segmentation": {"iris": {"method": "hsv-range", "hueDegrees": [25, 65], "saturation": [.25, 1], "value": [.25, 1]}}}
            (run / "config.json").write_text(json.dumps(config))
            image = Image.new("RGB", (100, 80), "white")
            image.putpixel((20, 20), (190, 135, 30))
            image.save(run / "a.png")
            image.save(run / "b.png")
            captures = [{"id": "a", "file": "a.png", "stage": "baseline", "fingerprint": "same-camera", "pose": {"blink": 1}, "params": {"ParamEyeLOpen": 0, "ParamEyeROpen": 0}},
                        {"id": "b", "file": "b.png", "stage": "candidate", "fingerprint": "different-camera", "pose": {"blink": 0}}]
            (run / "captures.json").write_text(json.dumps(captures))
            manifest = {"references": [{"id": "a", "role": "baseline", "approved": False}], "comparisons": [{"before": "a", "after": "b", "stage": "same-expression", "roi": "iris_left"}]}
            (run / "reference-manifest.json").write_text(json.dumps(manifest))
            result = analysis.analyze(run, run / "config.json")
            self.assertFalse(result["hasApprovedTarget"])
            self.assertTrue(result["humanReviewRequired"])
            self.assertEqual(result["comparisons"][0]["status"], "rejected")
            closure = result["fullBlinkChecks"][0]
            self.assertEqual(closure["totalThresholdPixels"], 1)
            self.assertFalse(closure["visualClosureVerified"])
            self.assertTrue((run / closure["overlayFile"]).exists())
            self.assertTrue((run / "report.html").exists())
            captures[1]["fingerprint"] = "same-camera"
            (run / "captures.json").write_text(json.dumps(captures))
            result = analysis.analyze(run, run / "config.json")
            self.assertEqual(result["comparisons"][0]["status"], "measured")
            self.assertTrue((run / "before-after.png").exists())

    def test_complete_external_boundary_excludes_holes_and_retains_components(self):
        mask = np.zeros((12, 12), dtype=bool)
        mask[2:8, 2:8] = True
        mask[4:6, 4:6] = False
        mask[10, 10] = True
        points = {tuple(point) for point in analysis.exterior_boundary(mask)}
        self.assertEqual(len(points), 21)  # Twenty outer square samples plus one detached pixel.
        self.assertNotIn((3, 4), points)  # A pixel next to the internal hole is not exterior.
        self.assertIn((10, 10), points)
        outline = analysis.brow_outline(mask, (20, 30, 32, 42), (100, 100), [.5, .5])
        self.assertEqual(outline["endpoints"]["top"]["representativePx"], [24.5, 32])
        self.assertEqual(outline["endpoints"]["bottom"]["extremeSamplesPx"], [[30, 40]])
        self.assertEqual(outline["anchorRelativeEndpointsPx"]["bottom"], [-20, -10])

    def test_pure_translation_requires_both_envelopes_and_independent_anchor(self):
        mask = np.zeros((20, 30), dtype=bool)
        mask[8:11, 4:24] = True
        rect = (10, 20, 40, 40)
        before = analysis.summarize_mask(mask, rect, (100, 100), [.5, .5], "brow")
        after = analysis.summarize_mask(np.roll(mask, -3, axis=0), rect, (100, 100), [.5, .5], "brow")
        delta = analysis.outline_displacement(before, after, (100, 100))
        self.assertEqual(delta["technicalMetrics"]["minUpwardPx"], 3)
        self.assertEqual(delta["technicalMetrics"]["maxContourVerticalSpreadPx"], 0)
        self.assertEqual(delta["technicalMetrics"]["maxAbsHorizontalPx"], 0)
        self.assertEqual(delta["endpointsDeltaPx"]["top"], [0, -3])
        # If the independent head moved equally, this is no head-relative lift.
        moved_anchor = analysis.summarize_mask(np.roll(mask, -3, axis=0), rect, (100, 100), [.5, .47], "brow")
        self.assertAlmostEqual(analysis.outline_displacement(before, moved_anchor, (100, 100))["technicalMetrics"]["minUpwardPx"], 0)
        missing_anchor = analysis.summarize_mask(mask, rect, (100, 100), None, "brow")
        self.assertEqual(analysis.outline_displacement(missing_anchor, after, (100, 100))["status"], "unavailable")

    def test_manual_static_exclusion_does_not_select_or_align_components(self):
        mask = np.zeros((20, 20), dtype=bool)
        mask[2:6, 15:19] = True  # Hair near one corner.
        mask[10:13, 3:15] = True
        annotation = {"excludePolygonsNormalized": [[[.7, 0], [1, 0], [1, .4], [.7, .4]]]}
        selected = analysis.apply_manual_mask_annotation(mask, annotation, (0, 0, 20, 20), (20, 20))
        self.assertEqual(int(selected.sum()), 36)
        self.assertTrue(np.array_equal(selected[10:13], mask[10:13]))
        self.assertEqual(int(mask.sum()), 52)  # Input evidence stays unchanged.
        with self.assertRaises(ValueError):
            analysis.apply_manual_mask_annotation(mask, {"excludePolygonsNormalized": [[[0, 0], [2, 0], [0, 1]]]}, (0, 0, 20, 20), (20, 20))

    def test_brow_protection_distinguishes_surrounding_skin_from_actual_brow_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            protection = {"maxMaskChangedPixels": 0, "maxMaskedRgbChangedPixels": 0,
                          "maxMaskedRgbMaeNormalized": 0, "maxAnchorDisplacementPx": 0}
            config = {"rois": {"brow_left": [.1, .1, .7, .6]},
                      "segmentation": {"brow": {"method": "rgb-range", "rgbMax": [90, 85, 85]}},
                      "deformation": {"goals": [
                          {"id": "whole-rectangle", "before": "neutral", "after": "skin", "roi": "brow_left", "thresholds": {"maxRgbMaeNormalized": 0}},
                          *[{"id": f"brow-protection-{cid}", "before": "neutral", "after": cid, "roi": "brow_left", "thresholds": protection}
                            for cid in ("skin", "brow-color", "brow-shape")]]}}
            (run / "config.json").write_text(json.dumps(config))
            neutral = np.full((100, 100, 3), [255, 228, 201], dtype=np.uint8)
            neutral[20:24, 20:60] = [33, 21, 12]
            frames = {cid: neutral.copy() for cid in ("neutral", "skin", "brow-color", "brow-shape")}
            frames["skin"][50, 70, 0] -= 1  # Same fixed ROI, outside the complete brow mask.
            frames["brow-color"][21, 25, 0] += 1  # Still dark enough to leave the mask unchanged.
            frames["brow-shape"][24, 30] = [33, 21, 12]  # A new exterior pixel; no registration or refitting.
            captures = []
            for cid, pixels in frames.items():
                Image.fromarray(pixels).save(run / f"{cid}.png")
                captures.append({"id": cid, "file": f"{cid}.png", "stage": "deformation-isolated",
                                 "fingerprint": "frozen", "headAnchor": [.5, .5]})
            (run / "captures.json").write_text(json.dumps(captures))
            result = analysis.analyze(run, run / "config.json")
            checks = {item["id"]: item for item in result["deformationChecks"]}
            self.assertEqual(checks["whole-rectangle"]["status"], "technical-threshold-failed")
            self.assertEqual(checks["brow-protection-skin"]["status"], "technical-thresholds-passed; artistic-review-required")
            self.assertEqual(checks["brow-protection-brow-color"]["status"], "technical-threshold-failed")
            self.assertEqual(checks["brow-protection-brow-shape"]["status"], "technical-threshold-failed")
            metrics = {item["after"]: item["metrics"] for item in result["comparisons"]}
            self.assertGreater(metrics["skin"]["rgbMaeNormalized"], 0)
            self.assertEqual(metrics["skin"]["maskChangedPixels"], 0)
            self.assertEqual(metrics["skin"]["maskedRgbChangedPixels"], 0)
            self.assertEqual(metrics["skin"]["maskedRgbMaeNormalized"], 0)
            self.assertEqual(metrics["brow-color"]["maskChangedPixels"], 0)
            self.assertEqual(metrics["brow-color"]["maskedRgbChangedPixels"], 1)
            self.assertGreater(metrics["brow-color"]["maskedRgbMaeNormalized"], 0)
            self.assertEqual(metrics["brow-shape"]["maskChangedPixels"], 1)
            self.assertEqual(metrics["brow-shape"]["maskedRgbChangedPixels"], 1)
            threshold_status = {item["metric"]: item["status"] for item in checks["brow-protection-brow-shape"]["thresholdChecks"]}
            self.assertEqual(threshold_status["maxMaskChangedPixels"], "failed")
            self.assertEqual(threshold_status["maxMaskedRgbChangedPixels"], "failed")

    def test_deformation_contract_and_raw_optional_gpu_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            config = {"rois": {"brow_left": [.1, .1, .6, .5], "iris_left": [.1, .6, .3, .2]},
                      "segmentation": {"brow": {"method": "rgb-range", "rgbMax": [90, 85, 85]}, "iris": {"method": "hsv-range"}},
                      "deformation": {"goals": [{"id": "lift", "before": "a", "after": "b", "roi": "brow_left", "thresholds": {"minUpwardPx": 3, "maxAbsHorizontalPx": 0, "maxContourVerticalSpreadPx": 0}},
                                                   {"id": "iris-fixed", "before": "a", "after": "b", "roi": "iris_left", "thresholds": {"maxAbsCentroidDisplacementPx": 0, "maxRgbMaeNormalized": 0}}]}}
            (run / "config.json").write_text(json.dumps(config))
            captures = []
            for cid, top in (("a", 40), ("b", 36)):
                image = Image.new("RGB", (100, 100), "white")
                pixels = np.asarray(image).copy()
                pixels[top:top + 4, 20:50] = [30, 20, 20]
                pixels[65:70, 20:25] = [190, 135, 30]
                Image.fromarray(pixels).save(run / f"{cid}.png")
                captures.append({"id": cid, "file": f"{cid}.png", "stage": "deformation-isolated", "fingerprint": "frozen", "headAnchor": [.5, .5],
                                 "pose": {"deformation": {"side": "left", "value": 0 if cid == "a" else 1}}})
            (run / "captures.json").write_text(json.dumps(captures))
            (run / "performance.json").write_text(json.dumps({"version": 2, "environment": {"webglRenderer": "actual-test-renderer"},
                    "scenes": [{"name": "forward", "elapsedMs": 100, "measurement": {"gpuTimeMeasured": True}, "summary": {"gpuTime": {"samples": 2, "meanMs": 2.5}},
                                "avatarFrames": [{"timestampMs": 0, "cpuWallMs": 1}, {"timestampMs": 20, "cpuWallMs": 2}], "gpuSamplesMs": [2, 3], "rafIntervalsMs": [20, 21]}]}))
            result = analysis.analyze(run, run / "config.json")
            self.assertEqual(result["schemaVersion"], 2)
            self.assertEqual([goal["status"] for goal in result["deformationChecks"]], ["technical-thresholds-passed; artistic-review-required"] * 2)
            self.assertTrue((run / "deformation-sheet.png").exists())
            part = result["captures"][0]["rois"]["brow_left"]
            self.assertTrue((run / part["outlineFile"]).exists())
            self.assertEqual(len(part["outlineSha256"]), 64)
            self.assertNotIn("externalBoundaryPx", part["outline"])
            perf = result["performance"]
            self.assertEqual(perf["environment"]["webglRenderer"], "actual-test-renderer")
            self.assertTrue(perf["scenes"][0]["gpuEvidence"]["declaredMeasured"])
            self.assertEqual(perf["scenes"][0]["rawArrays"]["gpuSamplesMs"]["valueRange"]["max"], 3)
            self.assertEqual(perf["scenes"][0]["rawArrays"]["avatarFrames"]["numericFieldRanges"]["timestampMs"]["last"], 20)
            report = (run / "report.html").read_text(encoding="utf-8")
            self.assertIn("actual-test-renderer", report)
            self.assertNotIn("未测 GPU 时间", report)
            # Unsupported or absent technical observations must not silently pass.
            goal = {"before": "a", "after": "b", "roi": "iris_left", "thresholds": {"minUpwardPx": 1}}
            checks = analysis.evaluate_deformation_goals([goal], result["comparisons"])
            self.assertEqual(checks[0]["status"], "review-required")

    def test_cpu_only_schema_never_infers_gpu_samples(self):
        result = analysis.performance_evidence({"version": 1, "scenes": [{"name": "forward", "measurement": {"gpuTimeMeasured": False}, "avatarFrames": [{"cpuWallMs": 1}], "rafIntervalsMs": [16, 17]}]})
        self.assertFalse(result["scenes"][0]["gpuEvidence"]["declaredMeasured"])
        self.assertIsNone(result["scenes"][0]["gpuEvidence"]["rawTopLevelGpuRecords"])

    def test_shape_change_cannot_pass_from_upward_centroid_alone(self):
        before_mask = np.zeros((30, 30), dtype=bool)
        before_mask[10:15, 3:25] = True
        after_mask = np.zeros_like(before_mask)
        after_mask[6:15, 3:25] = True  # Centre rises, but lower endpoint remains fixed.
        before = analysis.summarize_mask(before_mask, (0, 0, 30, 30), (30, 30), [.5, .5], "brow")
        after = analysis.summarize_mask(after_mask, (0, 0, 30, 30), (30, 30), [.5, .5], "brow")
        displacement = analysis.outline_displacement(before, after, (30, 30))
        self.assertLess(displacement["centroidDeltaPx"][1], 0)
        self.assertEqual(displacement["technicalMetrics"]["minUpwardPx"], 0)
        checks = analysis.evaluate_deformation_goals([{"before": "a", "after": "b", "roi": "brow", "thresholds": {"minUpwardPx": 1}}],
                 [{"before": "a", "after": "b", "roi": "brow", "status": "measured", "metrics": {"browOutlineDisplacement": displacement}}])
        self.assertEqual(checks[0]["status"], "technical-threshold-failed")

    def test_tied_apex_representative_is_not_a_horizontal_motion_anchor(self):
        mask = np.zeros((20, 30), dtype=bool)
        mask[6:12, 5:25] = True
        mask[5, 10:21] = True
        shifted = np.roll(mask, -2, axis=0)
        shifted[3, 10:14] = False  # Rasterized apex has fewer tied top pixels.
        before = analysis.summarize_mask(mask, (0, 0, 30, 20), (30, 20), [.5, .5], "brow")
        after = analysis.summarize_mask(shifted, (0, 0, 30, 20), (30, 20), [.5, .5], "brow")
        result = analysis.outline_displacement(before, after, (30, 20))
        self.assertGreater(result["endpointsDeltaPx"]["top"][0], 1.5)
        self.assertEqual(result["endpointsDeltaPx"]["left"][0], 0)
        self.assertEqual(result["endpointsDeltaPx"]["right"][0], 0)
        self.assertLess(result["technicalMetrics"]["maxAbsHorizontalPx"], .5)

    def test_explicit_upper_component_selection_keeps_raw_evidence(self):
        mask = np.zeros((25, 30), dtype=bool)
        mask[3, 10:18] = True  # Independent upper fold.
        mask[10:15, 4:25] = True  # Main lash silhouette.
        annotation = {"componentSelection": {"method": "largest-component", "minimumAreaPx": 20, "maxDiscardedComponentAreaPx": 10}}
        selected, evidence = analysis.select_annotated_components(mask, annotation)
        self.assertEqual(int(selected.sum()), 105)
        self.assertEqual([part["areaPx"] for part in evidence["rawComponents"]], [105, 8])
        self.assertFalse(evidence["reviewReasons"])
        self.assertEqual(int(mask.sum()), 113)
        annotation["componentSelection"]["maxDiscardedComponentAreaPx"] = 5
        _, evidence = analysis.select_annotated_components(mask, annotation)
        self.assertTrue(evidence["reviewReasons"])

    def test_sclera_iris_union_does_not_reconstruct_dark_upper_iris(self):
        pixels = np.asarray([[[255, 250, 245], [190, 135, 30], [255, 228, 201], [33, 21, 12]]], dtype=np.uint8)
        result = analysis.color_mask(pixels, {"method": "any-of", "rules": [{"method": "rgb-range", "rgbMin": [225, 237, 227], "rgbMax": [255, 255, 255]},
                                                                              {"method": "hsv-range", "hueDegrees": [25, 65], "saturation": [.45, 1], "value": [.25, 1]}]})
        # Lower-color surface includes sclera and bright iris, but does not
        # silently treat deep iris/pupil color as a measured upper eyelid.
        self.assertEqual(result.tolist(), [[True, True, False, False]])
        with self.assertRaises(ValueError):
            analysis.color_mask(pixels, {"method": "any-of", "rules": []})

    def test_lower_boundary_moves_with_unchanged_gold_area_and_upper_lash(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            upper_rule = {"method": "rgb-range", "rgbMin": [0, 0, 0], "rgbMax": [90, 85, 85]}
            gold_rule = {"method": "hsv-range", "hueDegrees": [25, 65], "saturation": [.45, 1], "value": [.25, 1]}
            lower_rule = {"method": "any-of", "rules": [{"method": "rgb-range", "rgbMin": [225, 237, 227], "rgbMax": [255, 255, 255]}, gold_rule]}
            rect = [.1, .1, .6, .6]
            config = {"rois": {"iris_left": rect,
                       "upper_lid_left": {"kind": "upper_lid", "rect": rect, "columnBandNormalized": [.2, .5], "componentSelection": {"method": "largest-component", "minimumAreaPx": 20, "maxDiscardedComponentAreaPx": 10}},
                       "lower_lid_left": {"kind": "lower_lid", "rect": rect, "columnBandNormalized": [.2, .5]},
                       "mouth_protection": {"kind": "protection", "rect": [.3, .75, .35, .15]}},
                      "segmentation": {"iris": gold_rule, "upper_lid": upper_rule, "lower_lid": lower_rule},
                      "deformation": {"goals": [{"id": "lower-raises", "before": "a", "after": "b", "roi": "lower_lid_left", "thresholds": {"minLowerEnvelopeMedianUpwardPx": 4, "minSharedEnvelopeColumns": 20}},
                                                   {"id": "upper-fixed", "before": "a", "after": "b", "roi": "upper_lid_left", "thresholds": {"maxAbsUpperEnvelopeDeltaPx": 0, "minSharedEnvelopeColumns": 20}},
                                                   {"id": "mouth-fixed", "before": "a", "after": "b", "roi": "mouth_protection", "thresholds": {"maxRgbMaeNormalized": 0}}]}}
            (run / "config.json").write_text(json.dumps(config))
            captures = []
            for cid, bottom in (("a", 45), ("b", 40)):
                pixels = np.full((100, 100, 3), [255, 228, 201], dtype=np.uint8)
                pixels[20:bottom, 20:50] = [255, 250, 245]
                pixels[25:38, 26:44] = [190, 135, 30]  # Identical golden iris area.
                pixels[15:20, 20:50] = [33, 21, 12]
                pixels[12, 32:40] = [33, 21, 12]  # Reviewed separate crease.
                Image.fromarray(pixels).save(run / f"{cid}.png")
                captures.append({"id": cid, "file": f"{cid}.png", "stage": "deformation-isolated", "fingerprint": "frozen", "headAnchor": [.5, .5]})
            (run / "captures.json").write_text(json.dumps(captures))
            result = analysis.analyze(run, run / "config.json")
            self.assertEqual([goal["status"] for goal in result["deformationChecks"]], ["technical-thresholds-passed; artistic-review-required"] * 3)
            self.assertEqual(result["captures"][0]["rois"]["iris_left"]["areaPx"], result["captures"][1]["rois"]["iris_left"]["areaPx"])
            upper = result["captures"][0]["rois"]["upper_lid_left"]
            self.assertEqual(upper["componentSelection"]["excludedAreaPx"], 8)
            self.assertTrue((run / upper["rawMaskFile"]).exists())
            self.assertTrue((run / upper["roiOverlayFile"]).exists())
            lower = result["comparisons"][0]["metrics"]["eyelidEnvelopeDisplacement"]
            self.assertEqual(lower["medianDeltaPx"], -5)
            empty = analysis.summarize_mask(np.zeros((60, 60), dtype=bool), (10, 10, 70, 70), (100, 100), [.5, .5], "lower_lid")
            empty.update(kind="lower_lid", annotation={"columnBandNormalized": [.2, .5]})
            self.assertEqual(analysis.eyelid_envelope_displacement(result["captures"][0]["rois"]["lower_lid_left"], empty, (100, 100))["status"], "unavailable")
            self.assertIn("mouth_protection", result["captures"][0]["protectionRois"])
            changed_mouth = Image.open(run / "b.png").convert("RGB")
            changed_mouth.putpixel((45, 80), (30, 20, 20))
            changed_mouth.save(run / "b.png")
            changed = analysis.analyze(run, run / "config.json")
            self.assertEqual(changed["deformationChecks"][2]["status"], "technical-threshold-failed")


if __name__ == "__main__":
    unittest.main()
