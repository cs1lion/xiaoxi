"""Small, reviewable image analysis for the project's fixed-camera eye captures.

This is a threshold tool inside manually annotated ROIs, not an eye detector.
It deliberately rejects comparisons whose capture fingerprints differ.
Requires only Pillow and NumPy; run with the bundled Python when needed.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
LABELS = {"daily": "中性", "happy": "开心", "sleepy": "困困", "curious": "疑惑"}


def read_json(path, fallback=None):
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).exists() else fallback


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def safe_name(value):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))


def roi_rect(roi, size):
    """Return a clipped pixel rectangle from a normalized manual annotation."""
    if isinstance(roi, dict):
        roi = roi.get("rect", roi.get("normalized", roi))
        if isinstance(roi, dict):
            roi = [roi["x"], roi["y"], roi.get("width", roi.get("w")), roi.get("height", roi.get("h"))]
    if len(roi) != 4 or any(not isinstance(x, (int, float)) for x in roi):
        raise ValueError(f"Expected normalized ROI [x,y,w,h], received {roi!r}")
    x, y, w, h = roi
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.00001 and y + h <= 1.00001):
        raise ValueError(f"ROI outside normalized viewport: {roi!r}")
    iw, ih = size
    return (max(0, math.floor(x * iw)), max(0, math.floor(y * ih)),
            min(iw, math.ceil((x + w) * iw)), min(ih, math.ceil((y + h) * ih)))


def hsv(rgb):
    """HSV with hue in degrees; saturation/value in [0,1]."""
    a = rgb.astype(float) / 255
    hi, lo = a.max(axis=2), a.min(axis=2)
    delta = hi - lo
    hue = np.zeros_like(hi)
    nz = delta > 0
    for channel, offset in ((0, 0), (1, 2), (2, 4)):
        chosen = nz & (a.argmax(axis=2) == channel)
        numerator = a[:, :, (channel + 1) % 3] - a[:, :, (channel + 2) % 3]
        hue[chosen] = (numerator[chosen] / delta[chosen] + offset) * 60
    sat = np.divide(delta, hi, out=np.zeros_like(hi), where=hi > 0)
    return np.mod(hue, 360), sat, hi


def color_mask(rgb, rule):
    method = rule.get("method", "hsv-range")
    if method == "any-of":
        rules = rule.get("rules", [])
        if not rules:
            raise ValueError("any-of segmentation requires explicit component rules")
        return np.logical_or.reduce([color_mask(rgb, item) for item in rules])
    if method == "hsv-range":
        hh, ss, vv = hsv(rgb)
        h0, h1 = rule.get("hueDegrees", [25, 65])
        s0, s1 = rule.get("saturation", [.25, 1])
        v0, v1 = rule.get("value", [.25, 1])
        hue_ok = (hh >= h0) & (hh <= h1) if h0 <= h1 else (hh >= h0) | (hh <= h1)
        return hue_ok & (ss >= s0) & (ss <= s1) & (vv >= v0) & (vv <= v1)
    if method == "rgb-range":
        low = np.asarray(rule.get("rgbMin", [0, 0, 0]))
        high = np.asarray(rule.get("rgbMax", [90, 85, 85]))
        return np.all((rgb >= low) & (rgb <= high), axis=2)
    raise ValueError(f"Unsupported mask method: {method}")


def components(mask, retain_points=False):
    """Four-connected components; output is intentionally visible to reviewers."""
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    found = []
    for yy, xx in zip(*np.nonzero(mask)):
        if seen[yy, xx]:
            continue
        queue = [(int(yy), int(xx))]
        seen[yy, xx] = True
        points = []
        while queue:
            y, x = queue.pop()
            points.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        pts = np.asarray(points)
        y0, x0 = pts.min(axis=0)
        y1, x1 = pts.max(axis=0) + 1
        record = {"areaPx": len(points), "bboxPx": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)],
                  "centroidPx": [float(pts[:, 1].mean()), float(pts[:, 0].mean())]}
        if retain_points:
            record["_pointsYx"] = pts
        found.append(record)
    return sorted(found, key=lambda item: item["areaPx"], reverse=True)


def select_annotated_components(mask, annotation):
    """Apply a reviewed ROI-specific rule, preserving raw components as evidence."""
    selection = annotation.get("componentSelection") if isinstance(annotation, dict) else None
    if not selection:
        return mask, None
    if selection.get("method") != "largest-component":
        raise ValueError("Only explicitly annotated largest-component selection is supported")
    regions = components(mask, retain_points=True)
    minimum = selection.get("minimumAreaPx", 1)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not math.isfinite(minimum) or minimum < 1:
        raise ValueError("Largest-component minimumAreaPx must be a positive finite area")
    selected = np.zeros_like(mask, dtype=bool)
    if regions and regions[0]["areaPx"] >= minimum:
        points = regions[0]["_pointsYx"]
        selected[points[:, 0], points[:, 1]] = True
    metadata = {"annotation": selection, "rawComponents": [{key: value for key, value in region.items() if not key.startswith("_")} for region in regions],
                "selectedAreaPx": int(selected.sum()), "excludedAreaPx": int(mask.sum() - selected.sum()), "reviewReasons": [],
                "definition": "Deterministic component selection inside a fixed manually reviewed ROI; no tracking or image registration. Raw threshold mask is retained."}
    if not selected.any():
        metadata["reviewReasons"].append("No component meets the manually declared minimum area")
    discarded_limit = selection.get("maxDiscardedComponentAreaPx")
    if discarded_limit is not None and any(region["areaPx"] > discarded_limit for region in regions[1:]):
        metadata["reviewReasons"].append("An excluded component exceeds the manually reviewed size; main-eyelash segmentation may be ambiguous")
    return selected, metadata


def anchor_xy(anchor):
    if isinstance(anchor, (tuple, list)) and len(anchor) >= 2:
        return [float(anchor[0]), float(anchor[1])]
    if isinstance(anchor, dict):
        point = anchor.get("normalized", anchor.get("screen", anchor))
        if isinstance(point, (tuple, list)):
            return anchor_xy(point)
        if isinstance(point, dict) and "x" in point and "y" in point:
            if anchor.get("units", "normalized") not in ("normalized", "viewport-fraction"):
                return None
            return [float(point["x"]), float(point["y"])]
    return None


def summarize_mask(mask, rect, image_size, anchor=None, kind="iris"):
    y, x = np.nonzero(mask)
    x0, y0, x1, y1 = rect
    iw, ih = image_size
    data = {"areaPx": int(len(x)), "roiAreaPx": int(mask.size), "areaFractionOfRoi": float(mask.mean()),
            "centroidPx": None, "centroidNormalized": None, "bboxPx": None, "bboxAspect": None,
            "anchorRelativeCentroidNormalized": None, "components": components(mask)}
    if len(x):
        cx, cy = float(x.mean() + x0), float(y.mean() + y0)
        bw, bh = int(x.max() - x.min() + 1), int(y.max() - y.min() + 1)
        data.update(centroidPx=[cx, cy], centroidNormalized=[cx / iw, cy / ih],
                    bboxPx=[int(x.min() + x0), int(y.min() + y0), bw, bh], bboxAspect=bw / bh)
        if anchor:
            data["anchorRelativeCentroidNormalized"] = [cx / iw - anchor[0], cy / ih - anchor[1]]
    if kind in ("brow", "upper_lid", "lower_lid"):
        data["outline"] = brow_outline(mask, rect, image_size, anchor)
        data["roiBoundaryContact"] = {"top": int(mask[0].sum()), "bottom": int(mask[-1].sum()),
                                      "left": int(mask[:, 0].sum()), "right": int(mask[:, -1].sum())}
        data["outlineReviewReasons"] = []
        if any(data["roiBoundaryContact"].values()):
            data["outlineReviewReasons"].append("Threshold pixels touch the fixed ROI border: possible clipping or hair contamination.")
    if kind == "brow":
        columns = mask.sum(axis=0)
        nonzero = columns[columns > 0]
        data["darkThicknessPx"] = {"meanOccupiedColumn": float(nonzero.mean()) if len(nonzero) else None,
                                   "p95OccupiedColumn": float(np.percentile(nonzero, 95)) if len(nonzero) else None,
                                   "occupiedColumns": int(len(nonzero)),
                                   "definition": "Thresholded dark-pixel count per nonempty ROI column; excludes no hair automatically."}
        if len(data["components"]) > 1:
            data["outlineReviewReasons"].append("Multiple components remain; inspect every component before interpreting extrema.")
    return data


def boundary(mask):
    pad = np.pad(mask, 1, constant_values=False)
    interior = (pad[1:-1, 1:-1] & pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
    return np.argwhere(mask & ~interior)


def exterior_boundary(mask):
    """Foreground pixel centres adjacent to external background, excluding holes.

    This is a complete set of raster boundary samples, not a smoothed/ordered
    vector path and not a fitted contour. Every foreground component remains.
    """
    padded = np.pad(mask, 1, constant_values=False)
    external = np.zeros_like(padded, dtype=bool)
    queue = [(0, 0)]
    external[0, 0] = True
    h, w = padded.shape
    while queue:
        y, x = queue.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and not padded[ny, nx] and not external[ny, nx]:
                external[ny, nx] = True
                queue.append((ny, nx))
    adjacent = (external[:-2, 1:-1] | external[2:, 1:-1] | external[1:-1, :-2] | external[1:-1, 2:])
    return np.argwhere(mask & adjacent)


def brow_outline(mask, rect, image_size, anchor=None):
    x0, y0 = rect[:2]
    iw, ih = image_size
    samples = exterior_boundary(mask)
    points = [[int(x + x0), int(y + y0)] for y, x in samples]
    outline = {"definition": "Complete 4-connected external-boundary foreground pixel centres; internal holes excluded; all components retained.",
               "coordinateConvention": "capture pixels x/y; y increases down; no contour fitting or image registration",
               "sampleCount": len(points), "externalBoundaryPx": points, "columnEnvelopePx": [], "endpoints": {},
               "anchorRelativeEndpointsPx": None}
    if not len(points):
        return outline
    xy = np.asarray(points)
    for name, axis, operation in (("top", 1, np.min), ("bottom", 1, np.max), ("left", 0, np.min), ("right", 0, np.max)):
        selected = xy[xy[:, axis] == operation(xy[:, axis])]
        outline["endpoints"][name] = {"representativePx": selected.mean(axis=0).tolist(), "extremeSamplesPx": selected.tolist(),
                                        "definition": "Mean of all tied outer-boundary extreme pixel centres; all tied samples retained."}
    for x in np.flatnonzero(mask.any(axis=0)):
        occupied = np.flatnonzero(mask[:, x])
        outline["columnEnvelopePx"].append([int(x + x0), int(occupied.min() + y0), int(occupied.max() + y0)])
    if anchor is not None:
        origin = [anchor[0] * iw, anchor[1] * ih]
        outline["anchorPx"] = origin
        outline["anchorRelativeEndpointsPx"] = {name: [record["representativePx"][0] - origin[0], record["representativePx"][1] - origin[1]]
                                                 for name, record in outline["endpoints"].items()}
    return outline


def apply_manual_mask_annotation(mask, annotation, rect, image_size):
    """Apply only explicitly supplied static polygons; never select/track a component."""
    if not isinstance(annotation, dict):
        return mask
    included = annotation.get("includePolygonNormalized")
    exclusions = annotation.get("excludePolygonsNormalized", [])
    if not included and not exclusions:
        return mask
    iw, ih = image_size
    x0, y0, x1, y1 = rect
    selected = Image.new("1", (x1 - x0, y1 - y0), 0 if included else 1)
    draw = ImageDraw.Draw(selected)
    def polygon(points):
        if len(points) < 3 or any(len(p) != 2 or any(not isinstance(n, (int, float)) or not 0 <= n <= 1 for n in p) for p in points):
            raise ValueError("Manual mask polygon requires at least three normalized viewport x/y points")
        return [(round(p[0] * iw) - x0, round(p[1] * ih) - y0) for p in points]
    if included:
        draw.polygon(polygon(included), fill=1)
    for excluded in exclusions:
        draw.polygon(polygon(excluded), fill=0)
    return mask & np.asarray(selected, dtype=bool)


def outline_displacement(before, after, image_size):
    """Observe shape in fixed columns, subtracting independently recorded head motion.

    Subtracting an anchor in measurements never changes images or re-registers
    masks. Envelope correspondences use the same fixed x column only.
    """
    a, b = before.get("outline", {}), after.get("outline", {})
    aa, ba = a.get("anchorPx"), b.get("anchorPx")
    result = {"status": "unavailable", "units": "actual capture pixels; screen y increases down",
              "definition": "Relative to the independent head anchor. Column envelope differences compare the same frozen screen x; no image alignment.",
              "endpointsDeltaPx": None, "columnEnvelopeDelta": None, "technicalMetrics": {}}
    if not aa or not ba or not a.get("sampleCount") or not b.get("sampleCount"):
        result["reason"] = "Nonempty brow outlines and independent head anchors are required"
        return result
    shift = np.asarray(ba) - np.asarray(aa)
    endpoint_delta = {name: (np.asarray(b["endpoints"][name]["representativePx"]) - np.asarray(a["endpoints"][name]["representativePx"]) - shift).tolist()
                      for name in ("top", "bottom", "left", "right")}
    ac = {row[0]: row[1:] for row in a["columnEnvelopePx"]}
    bc = {row[0]: row[1:] for row in b["columnEnvelopePx"]}
    shared = sorted(ac.keys() & bc.keys())
    envelope = np.asarray([[x, bc[x][0] - ac[x][0] - shift[1], bc[x][1] - ac[x][1] - shift[1]] for x in shared], dtype=float)
    centroid = np.asarray(after["centroidPx"]) - np.asarray(before["centroidPx"]) - shift
    ab, bb = before["bboxPx"], after["bboxPx"]
    # A flat/curved apex can gain or lose tied pixels under vertical rasterization.
    # Its representative x is not a corresponding geometric point, so it cannot
    # establish lateral translation. The complete apex samples remain recorded.
    technical = {"maxAbsHorizontalPx": max(abs(centroid[0]), abs(endpoint_delta["left"][0]), abs(endpoint_delta["right"][0])),
                 "maxAbsWidthChangePx": abs(bb[2] - ab[2]), "maxAbsHeightChangePx": abs(bb[3] - ab[3]),
                 "maxAbsAreaChangeFraction": abs(after["areaPx"] / before["areaPx"] - 1),
                 "maxAnchorDisplacementPx": float(np.linalg.norm(shift)),
                 "maxAbsCentroidDisplacementPx": float(np.linalg.norm(centroid)),
                 "maxAbsVerticalPx": max(abs(centroid[1]), *(abs(delta[1]) for delta in endpoint_delta.values()))}
    if len(shared):
        vertical = envelope[:, 1:].ravel()
        upper, lower = envelope[:, 1], envelope[:, 2]
        # All four independent vertical observations must move upward; a moving
        # centroid alone cannot make this conservative minUpwardPx positive.
        technical["minUpwardPx"] = float(min(-centroid[1], -endpoint_delta["top"][1], -endpoint_delta["bottom"][1], -np.median(upper), -np.median(lower)))
        technical["maxContourVerticalSpreadPx"] = float(vertical.max() - vertical.min())
        result["columnEnvelopeDelta"] = {"sharedColumns": len(shared), "beforeColumns": len(ac), "afterColumns": len(bc),
                                         "unpairedBeforeColumns": sorted(ac.keys() - bc.keys()), "unpairedAfterColumns": sorted(bc.keys() - ac.keys()),
                                         "upperMedianDeltaPx": float(np.median(upper)), "lowerMedianDeltaPx": float(np.median(lower)),
                                         "allVerticalMinPx": float(vertical.min()), "allVerticalMaxPx": float(vertical.max()),
                                         "rowsPx": envelope.tolist()}
    result.update(status="measured", anchorDeltaPx=shift.tolist(), centroidDeltaPx=centroid.tolist(), endpointsDeltaPx=endpoint_delta,
                  technicalMetrics=technical,
                  technicalMetricDefinitions={"maxAbsHorizontalPx": "Maximum absolute head-relative x difference of centroid and screen-left/right exterior extrema. Top/bottom tied extrema representatives are not corresponding geometric points and are retained separately.",
                                             "minUpwardPx": "Conservative minimum upward displacement across centroid, top/bottom extrema y and median upper/lower fixed-column envelopes.",
                                             "maxContourVerticalSpreadPx": "Maximum minus minimum head-relative upper/lower envelope y displacement over all shared fixed columns."},
                  reviewReasons=before.get("outlineReviewReasons", []) + after.get("outlineReviewReasons", []))
    return result


def eyelid_envelope_displacement(before, after, image_size):
    """Measure two deliberately different image surfaces; do not infer lid motion from area."""
    kind = before.get("kind")
    result = {"status": "unavailable", "kind": kind, "units": "capture pixel centres; screen y increases down",
              "surface": "outer upper silhouette of the explicitly selected main dark lash component" if kind == "upper_lid" else "lower silhouette of the configured visible sclera/iris color union",
              "definition": "Same frozen screen x column; subtract independent head-anchor motion only in reported measurements. No image registration or fitted opening polygon.",
              "technicalMetrics": {}, "reviewReasons": before.get("outlineReviewReasons", []) + after.get("outlineReviewReasons", [])}
    a, b = before.get("outline", {}), after.get("outline", {})
    aa, ba = a.get("anchorPx"), b.get("anchorPx")
    if kind not in ("upper_lid", "lower_lid") or kind != after.get("kind") or not aa or not ba or not a.get("sampleCount") or not b.get("sampleCount"):
        result["reason"] = "Matching nonempty lid masks and independent head anchors required; a closed eye has no color-union lower boundary"
        return result
    band = before.get("annotation", {}).get("columnBandNormalized", [0, 1]) if isinstance(before.get("annotation"), dict) else [0, 1]
    if len(band) != 2 or not 0 <= band[0] < band[1] <= 1:
        raise ValueError("columnBandNormalized must be a fixed normalized screen-x interval [start,end]")
    ac = {row[0]: row[1:] for row in a["columnEnvelopePx"]}
    bc = {row[0]: row[1:] for row in b["columnEnvelopePx"]}
    shared_all = sorted(ac.keys() & bc.keys())
    shared = [x for x in shared_all if band[0] <= x / image_size[0] < band[1]]
    result.update(columnBandNormalized=band, allSharedColumns=len(shared_all), selectedSharedColumns=len(shared),
                  beforeBandColumns=sum(band[0] <= x / image_size[0] < band[1] for x in ac),
                  afterBandColumns=sum(band[0] <= x / image_size[0] < band[1] for x in bc))
    if not shared:
        result["reason"] = "No shared threshold contour columns remain inside the declared fixed band"
        return result
    anchor_delta = np.asarray(ba) - np.asarray(aa)
    index = 0 if kind == "upper_lid" else 1
    rows = [[x, ac[x][index], bc[x][index], bc[x][index] - ac[x][index] - anchor_delta[1]] for x in shared]
    deltas = np.asarray([row[3] for row in rows], dtype=float)
    technical = {"minSharedEnvelopeColumns": len(shared), "maxAbsEnvelopeDeltaPx": float(np.max(np.abs(deltas))),
                 "minEnvelopeChangedColumnFraction": float(np.mean(np.abs(deltas) >= .5)),
                 "maxAnchorDisplacementPx": float(np.linalg.norm(anchor_delta))}
    if kind == "upper_lid":
        technical.update(maxAbsUpperEnvelopeDeltaPx=float(np.max(np.abs(deltas))),
                         minUpperEnvelopeMedianDownwardPx=float(np.median(deltas)))
    else:
        technical.update(minLowerEnvelopeMedianUpwardPx=float(-np.median(deltas)),
                         minLowerUpwardColumnFraction=float(np.mean(deltas <= -.5)))
    result.update(status="measured", anchorDeltaPx=anchor_delta.tolist(),
                  medianDeltaPx=float(np.median(deltas)), p10DeltaPx=float(np.percentile(deltas, 10)), p90DeltaPx=float(np.percentile(deltas, 90)),
                  minDeltaPx=float(deltas.min()), maxDeltaPx=float(deltas.max()), rowsPx=rows, technicalMetrics=technical,
                  metricDefinitions={"minLowerEnvelopeMedianUpwardPx": "Negative median head-relative y change of lower-color boundary in the explicitly fixed x band; not a rigid translation claim.",
                                     "minLowerUpwardColumnFraction": "Fraction of shared fixed-band lower-boundary columns with at least 0.5 px upward movement.",
                                     "maxAbsUpperEnvelopeDeltaPx": "Maximum absolute head-relative outer-upper-lash y change across every shared fixed-band column.",
                                     "minSharedEnvelopeColumns": "Number of directly comparable fixed screen-x columns in the declared band; fewer columns make the evidence narrower."})
    return result


def directed_distances(points, other):
    values = []
    for start in range(0, len(points), 128):
        block = points[start:start + 128].astype(float)
        d = np.sqrt(((block[:, None, :] - other[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
        values.append(d)
    return np.concatenate(values)


def compare_images(before, after, roi, before_mask=None, after_mask=None):
    a, b = np.asarray(before.crop(roi)).astype(float), np.asarray(after.crop(roi)).astype(float)
    absolute = np.abs(a - b)
    result = {"rgbMaeNormalized": float(absolute.mean() / 255), "rgbChangedPixels": int(np.any(a != b, axis=2).sum()),
              "rgbMaxAbsChannelDelta": int(absolute.max()),
              "units": {"rgbMaeNormalized": "fraction of 8-bit RGB range", "rgbMaxAbsChannelDelta": "8-bit channel levels", "edgeDistance": "capture pixels"}}
    if before_mask is not None and after_mask is not None:
        union = np.logical_or(before_mask, after_mask).sum()
        result["maskIoU"] = float(np.logical_and(before_mask, after_mask).sum() / union) if union else None
        active = np.logical_or(before_mask, after_mask)
        result["maskChangedPixels"] = int(np.logical_xor(before_mask, after_mask).sum())
        result["maskedRgbChangedPixels"] = int(np.any(a != b, axis=2)[active].sum()) if union else None
        result["maskedRgbMaeNormalized"] = float(np.abs(a - b)[active].mean() / 255) if union else None
        result["maskedRgbDefinition"] = "Actual RGB difference over the union of before/after masks in frozen coordinates; pixels are never moved or matched by fitting. Mask difference is reported separately."
        pa, pb = boundary(before_mask), boundary(after_mask)
        if len(pa) and len(pb):
            ab, ba = directed_distances(pa, pb), directed_distances(pb, pa)
            both = np.concatenate([ab, ba])
            result["edgeDistance"] = {"beforeToAfterMeanPx": float(ab.mean()), "afterToBeforeMeanPx": float(ba.mean()),
                                      "bidirectionalMeanPx": float(both.mean()), "bidirectionalP95Px": float(np.percentile(both, 95)),
                                      "beforeSamples": len(pa), "afterSamples": len(pb)}
        else:
            result["edgeDistance"] = None
            result["edgeDistanceUnavailableReason"] = "At least one threshold mask has no contour; empty masks do not prove a correct eyelid."
    return result


def font(size):
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw, text, max_width, text_font, limit=3):
    lines, line = [], ""
    for char in text:
        if draw.textlength(line + char, font=text_font) > max_width and line:
            lines.append(line)
            line = char
            if len(lines) >= limit:
                return lines
        else:
            line += char
    if line:
        lines.append(line)
    return lines[:limit]


def sheet(images, out, title, subtitle, columns=4, crop=None):
    tile_w, tile_h = 320, 415
    count = max(1, len(images))
    rows = math.ceil(count / columns)
    result = Image.new("RGB", (columns * tile_w + 32, rows * tile_h + 116), "#111722")
    draw = ImageDraw.Draw(result)
    draw.text((16, 16), title, font=font(26), fill="#f2f6ff")
    draw.text((16, 56), subtitle[:120], font=font(15), fill="#b0c0d3")
    for n, record in enumerate(images):
        label, image = record[:2]
        intent = record[2] if len(record) > 2 else ""
        item = image.crop(roi_rect(crop, image.size)) if crop else image.copy()
        item.thumbnail((300, 310), Image.Resampling.LANCZOS)
        x, y = 16 + (n % columns) * tile_w, 98 + (n // columns) * tile_h
        result.paste(item, (x + (300 - item.width) // 2, y))
        draw.text((x, y + 314), label[:35], font=font(15), fill="#d6e4f8")
        for lineno, line in enumerate(wrap_text(draw, intent, 298, font(13))):
            draw.text((x, y + 341 + lineno * 20), line, font=font(13), fill="#a9bdd8")
    if not images:
        draw.text((16, 140), "本次没有此类截图", font=font(20), fill="#d6e4f8")
    result.save(out)


def capture_label(capture):
    pose = capture.get("pose", {})
    name = pose.get("expression", pose.get("preset", "")) if isinstance(pose, dict) else str(pose)
    suffix = LABELS.get(name, name)
    stage = {"baseline": "基线", "candidate": "候选"}.get(capture.get("stage"), "")
    return f"{capture['id']} {stage} {suffix}".strip()


def capture_goal(capture, config):
    pose = capture.get("pose", {})
    name = pose.get("expression", pose.get("preset", "")) if isinstance(pose, dict) else str(pose)
    # Capture IDs also preserve the four core expressions for straightforward CLI use.
    if name not in config.get("goals", {}):
        name = next((key for key in config.get("goals", {}) if key in capture["id"].split("-")), name)
    return config.get("goals", {}).get(name, {})


def deferred_goal(goal):
    return goal.get("status", goal.get("state")) == "deferred" or goal.get("deferred") is True


def capture_intent(capture, config):
    goal = capture_goal(capture, config)
    prefix = "暂缓：本轮保留基线；" if deferred_goal(goal) else ""
    return prefix + goal.get("intent", "")


def blink_value(capture):
    pose = capture.get("pose", {})
    if isinstance(pose, dict):
        for key in ("blink", "blinkPhase", "closure"):
            if isinstance(pose.get(key), (int, float)):
                return float(pose[key])
    return capture.get("blink")


def normalized_config(config, capture):
    model = capture.get("model", config.get("model", "daily"))
    if isinstance(model, dict):
        model = model.get("key", model.get("id", "daily"))
    selected = config.get("models", {}).get(model, config)
    rois = selected.get("rois", config.get("rois", {}))
    rules = selected.get("segmentation", config.get("segmentation", {}))
    return rois, rules


def deformation_goals(manifest, config):
    deformation = manifest.get("deformation", config.get("deformation", {}))
    goals = deformation.get("goals", []) if isinstance(deformation, dict) else []
    if isinstance(goals, dict):
        return [dict(value, id=key) for key, value in goals.items()]
    return goals


def evaluate_deformation_goals(goals, comparisons):
    """Evaluate explicitly authored engineering thresholds, never visual fidelity."""
    supported = {"minUpwardPx", "maxAbsHorizontalPx", "maxContourVerticalSpreadPx", "maxAbsWidthChangePx",
                 "maxAbsHeightChangePx", "maxAbsAreaChangeFraction", "maxAnchorDisplacementPx",
                 "maxAbsCentroidDisplacementPx", "maxAbsVerticalPx", "maxRgbMaeNormalized", "minRgbMaeNormalized",
                 "minSharedEnvelopeColumns", "maxAbsEnvelopeDeltaPx", "minEnvelopeChangedColumnFraction",
                 "maxAbsUpperEnvelopeDeltaPx", "minUpperEnvelopeMedianDownwardPx", "minLowerEnvelopeMedianUpwardPx", "minLowerUpwardColumnFraction"}
    supported.update({"maxMaskChangedPixels", "maxMaskedRgbChangedPixels", "maxMaskedRgbMaeNormalized"})
    checks = []
    for goal in goals:
        row = dict(goal, status="review-required", artisticAccuracyVerified=False, humanReviewRequired=True,
                   definition="Only the manually declared technical deformation thresholds are evaluated; no character accuracy or artistic approval.", thresholdChecks=[])
        comparison = next((item for item in comparisons if item.get("before") == goal.get("before") and item.get("after") == goal.get("after") and item.get("roi") == goal.get("roi")), None)
        if not comparison or comparison.get("status") != "measured":
            row["reason"] = "Declared fixed-condition comparison absent or rejected"
            checks.append(row)
            continue
        measured = comparison.get("metrics", {}).get("browOutlineDisplacement", {})
        lid = comparison.get("metrics", {}).get("eyelidEnvelopeDisplacement", {})
        values = comparison.get("metrics", {}).get("roiDeformationMetrics", {}) | measured.get("technicalMetrics", {}) | lid.get("technicalMetrics", {})
        thresholds = goal.get("thresholds", {})
        if not thresholds:
            row["reason"] = "No manual technical thresholds supplied; retain observations for human review"
        for key, limit in thresholds.items():
            if key not in supported or not isinstance(limit, (int, float)) or not math.isfinite(limit):
                row["thresholdChecks"].append({"metric": key, "limit": limit, "status": "unavailable", "reason": "Unsupported metric or nonnumeric threshold"})
                continue
            value = values.get(key)
            passed = value >= limit if key.startswith("min") and value is not None else value <= limit if value is not None else None
            row["thresholdChecks"].append({"metric": key, "actual": value, "limit": limit,
                                           "operator": ">=" if key.startswith("min") else "<=", "status": "passed" if passed else "failed" if passed is False else "unavailable"})
        review = measured.get("reviewReasons", []) + lid.get("reviewReasons", [])
        row["segmentationReviewReasons"] = review
        if any(check["status"] == "failed" for check in row["thresholdChecks"]):
            row["status"] = "technical-threshold-failed"
        elif row["thresholdChecks"] and all(check["status"] == "passed" for check in row["thresholdChecks"]) and not review:
            row["status"] = "technical-thresholds-passed; artistic-review-required"
        elif review:
            row["reason"] = "Segmentation needs review before contour endpoints can support the declared contract"
        checks.append(row)
    return checks


def numeric_range(values):
    actual = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]
    return {"samples": len(actual), "min": min(actual) if actual else None, "max": max(actual) if actual else None,
            "first": actual[0] if actual else None, "last": actual[-1] if actual else None}


def performance_evidence(raw, raw_bytes=None):
    if not raw:
        return None
    scenes = []
    for scene in raw.get("scenes", []):
        row = {k: v for k, v in scene.items() if k in ("name", "elapsedMs", "summary", "measurement", "startState", "gpu", "gpuTimer", "environment",
                                                     "version", "metadata", "startedAt", "completedAt", "requestedDurationMs", "stopReason", "visibilityStateAtCompletion")}
        arrays = {}
        for key, value in scene.items():
            if not isinstance(value, list):
                continue
            record = {"jsonPath": f"scenes[{len(scenes)}].{key}", "records": len(value)}
            if all(isinstance(item, (int, float)) for item in value):
                record["valueRange"] = numeric_range(value)
            elif value and isinstance(value[0], dict):
                numeric_keys = sorted({field for item in value if isinstance(item, dict) for field, number in item.items() if isinstance(number, (int, float))})
                record["numericFieldRanges"] = {field: numeric_range([item.get(field) for item in value if isinstance(item, dict)]) for field in numeric_keys}
            arrays[key] = record
        row["rawArrays"] = arrays
        # Preserve optional GPU metadata rather than infer elapsed GPU time from
        # CPU/render cadence or from extension availability alone.
        measurement = scene.get("measurement", {})
        summaries = scene.get("summary", {})
        gpu_summary = next((summaries[key] for key in ("gpuRenderCommands", "gpuTime", "gpuTimer", "avatarFrameGpu") if key in summaries), {})
        gpu_samples = next((value for key, value in scene.items() if isinstance(value, list) and "gpu" in key.lower()), None)
        row["gpuEvidence"] = {"declaredMeasured": measurement.get("gpuTimeMeasured"), "summary": gpu_summary,
                              "metadata": scene.get("gpu", scene.get("gpuTimer", measurement.get("gpu"))),
                              "rawTopLevelGpuRecords": len(gpu_samples) if gpu_samples is not None else None,
                              "scope": measurement.get("gpuScope"),
                              "note": "GPU extension support or pending queries alone is not a valid timing sample. Inspect valid/disjoint/rejected query metadata in raw JSON."}
        scenes.append(row)
    return {"file": "performance.json", "schemaVersion": raw.get("schemaVersion", raw.get("version")),
            "bytes": len(raw_bytes) if raw_bytes is not None else None,
            "sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None,
            "environment": raw.get("environment"), "browser": raw.get("browser"), "note": raw.get("note"),
            "measurement": raw.get("measurement"), "gpu": raw.get("gpu", raw.get("gpuTimer")), "scenes": scenes,
            "rawScope": "Raw JSON includes every recorded array; numeric ranges below describe recorded values, not an assumed physical-device benchmark."}


def collect_inputs(run_dir, captures, manifest, session=None):
    inputs = {str(c["id"]): dict(c) for c in captures}
    local_references = {entry.get("manifestReference"): entry["file"] for entry in (session or {}).get("references", []) if entry.get("file")}
    for ref in manifest.get("references", []):
        role = ref.get("role")
        is_image = Path(ref.get("file", "")).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        if "file" in ref and ref["id"] not in inputs and is_image and role not in ("method-reference", "identity-only", "motion-only"):
            inputs[ref["id"]] = dict(ref, file=local_references.get(ref["file"], ref["file"]), sourceFile=ref["file"],
                                      stage=ref.get("stage", "approved-reference" if ref.get("approved") else "reference"))
    for capture in inputs.values():
        path = Path(capture["file"])
        capture["_path"] = path if path.is_absolute() else run_dir / path
    return inputs


def build_report(run_dir, metrics):
    esc = lambda value: html.escape(str(value))
    rows, cards = [], []
    for capture in metrics["captures"]:
        items = []
        lid_previews = []
        for name, roi in capture.get("rois", {}).items():
            items.append(f"<tr><td>{esc(name)}</td><td>{roi['areaPx']}</td><td>{roi['areaFractionOfRoi']:.4f}</td><td>{esc(roi['centroidNormalized'])}</td><td>{esc(roi['bboxAspect'])}</td></tr>")
            if roi.get("roiOverlayFile"):
                lid_previews.append(f"<figure><img style='width:100%;image-rendering:pixelated' src='{esc(roi['roiOverlayFile'])}' alt='{esc(name)}颜色mask外轮廓'><figcaption>{esc(name)}：{esc(roi['kind'])}；原始阈值 {roi['rawThresholdAreaPx']} px，选择后 {roi['areaPx']} px。<a href='{esc(roi.get('rawMaskFile',roi['maskFile']))}'>原始 mask</a></figcaption></figure>")
        cards.append(f"<article><h3>{esc(capture['id'])}</h3><div class='pair'><img src='{esc(capture['file'])}' alt='实际截图'><img src='{esc(capture.get('overlayFile',''))}' alt='人工 ROI 和阈值像素'></div>"
                     f"<p>阶段：{esc(capture.get('stage'))}；指纹：<code>{esc(canonical(capture.get('fingerprint')))}</code></p>"
                     "<table><tr><th>人工区域</th><th>阈值像素 px</th><th>占 ROI 比例</th><th>重心（视口归一化）</th><th>框宽高比</th></tr>" + "".join(items) + "</table>"
                     + ("<details><summary>上下眼睑分别测量的颜色mask / 外轮廓</summary><div class='pair'>" + "".join(lid_previews) + "</div></details>" if lid_previews else "")
                     + f"<details><summary>参数、原生输出、相机、独立头锚点及能力</summary><pre>{esc(json.dumps(capture,ensure_ascii=False,indent=2))}</pre></details></article>")
    for comparison in metrics["comparisons"]:
        rows.append(f"<tr><td>{esc(comparison.get('before'))} → {esc(comparison.get('after'))}</td><td>{esc(comparison.get('stage'))}<br>{esc(comparison.get('roi'))}</td><td>{esc(comparison.get('status'))}</td><td><pre>{esc(json.dumps(comparison,ensure_ascii=False,indent=2))}</pre></td></tr>")
    leakage = "".join(f"<li>{esc(item['id'])}：{esc(item['status'])}，虹膜阈值像素 {item['totalThresholdPixels']}。<a href='{esc(item['overlayFile'])}'>复查实际 mask</a></li>" for item in metrics["fullBlinkChecks"])
    content = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>眉眼固定条件校准报告</title><style>body{margin:0 auto;padding:24px;max-width:1280px;background:#111722;color:#dce6f3;font:15px/1.7 system-ui,sans-serif}h1,h2,h3{color:#fff}a{color:#9dc5ff}article{border:1px solid #344258;padding:16px;margin:20px 0}img{max-width:100%;height:auto} .pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;border:1px solid #344258;text-align:left;vertical-align:top}code{overflow-wrap:anywhere}.note{background:#263449;padding:16px} @media(max-width:700px){.pair{grid-template-columns:1fr}body{padding:12px}}</style>
<h1>眉眼固定条件校准报告</h1>
<p class='note'>这份报告保存真实渲染、输入与原生形变，并在人工标注区域内做颜色阈值分析。人工复核仍然必需。基线是现有输出，不能作为审美正确性的目标。</p>
<h2>参考与比较条件</h2>"""
    content += f"<p>独立已确认目标图：{'已登记；仍需检查其用途和比较条件' if metrics['hasApprovedTarget'] else '未提供；本次仅进行基线与候选对照'}。配准矩阵固定为 identity；指纹或尺寸不同即拒绝相关比较，禁止自动重新对齐。</p>"
    goal_items = []
    for name, goal in metrics["config"].get("goals", {}).items():
        is_deferred = deferred_goal(goal)
        tag = "【暂缓：本轮保持基线】" if is_deferred else ""
        detail = goal.get("deferredReason", goal.get("reason", "原生形变不足，目标尚未实现。")) if is_deferred else goal.get("hypothesis", "")
        goal_items.append(f"<li><b>{esc(goal.get('label', name))} {tag}</b>：{esc(goal.get('intent',''))} {esc(detail)}</li>")
    content += "<p>本轮的临时表达意图（待人工确认）：</p><ul>" + "".join(goal_items) + "</ul>"
    capabilities = metrics["session"].get("capabilities", {})
    if not capabilities and metrics["captures"]:
        capabilities = metrics["captures"][0].get("capabilities", {})
    if capabilities:
        channel_rows = "".join(f"<tr><td>{esc(name)}</td><td>{'有' if value.get('available') else '缺少'}</td><td>{esc(value.get('mode'))}</td><td>{'语义近似' if value.get('approximate') else '—'}</td></tr>" for name, value in capabilities.get("channels", {}).items())
        content += f"<h2>原生通道能力</h2><p>适配 profile：{esc(capabilities.get('profile'))}。缺少通道时，增加预设值无法获得独立的形变；bilateral 为双侧联动，independent 为左右独立。</p><table><tr><th>通道</th><th>可用性</th><th>模式</th><th>近似</th></tr>{channel_rows}</table>"
    content += f"<details><summary>session / reference manifest / 分析配置与脚本哈希 / 阈值 / 单位 / 限制</summary><pre>{esc(json.dumps({k:metrics[k] for k in ('session','referenceManifest','configProvenance','analysisProvenance','config','units','limitations')},ensure_ascii=False,indent=2))}</pre></details>"
    content += "<h2>表情对照</h2><img src='contact-sheet.png' alt='表情校准集'><h2>眨眼中间帧</h2><img src='blink-sheet.png' alt='眨眼中间帧'>"
    if (run_dir / "before-after.png").exists():
        content += "<h2>基线与候选</h2><img src='before-after.png' alt='基线与候选，同条件实际渲染'>"
    if (run_dir / "deformation-sheet.png").exists():
        content += "<h2>独立眉眼形变 · 分段强度</h2><p>左右隔离与双侧场景保留冻结坐标；检查未动对侧、眉毛和嘴部。画面强度及原生 morph 输出见逐图参数。</p><img src='deformation-sheet.png' alt='独立眉眼形变各强度实际渲染'>"
    if (run_dir / "stress-sheet.png").exists():
        content += "<h2>形变与极端注视 / 眨眼组合</h2><p>观察遮挡、发丝接近和回张；这组截图不能替代连续播放的自然程度检查。</p><img src='stress-sheet.png' alt='形变与极端注视眨眼实际渲染'>"
    content += "<h2>全闭眼检查</h2><p>只统计配置虹膜色阈值匹配像素，不能凭参数为零宣称没有虹膜泄漏；皮肤、材质、反光及阈值漏检都须看图。</p><ul>" + (leakage or "<li>本次没有完整闭合截图。</li>") + "</ul>"
    stability = metrics.get("anchorStability", {})
    content += "<h2>独立头锚点与局部变化</h2><p>锚点来自头骨投影，不能由眼睛对齐求得。下面仅记录同一冻结指纹组内的最大偏移，单位为实际截图像素；它不能证明模型脸型或眉眼审美正确。</p>"
    content += f"<pre>{esc(json.dumps(stability,ensure_ascii=False,indent=2))}</pre>"
    observation_rows = []
    for row in metrics.get("observations", []):
        change = row.get("thresholdAreaChangeFraction")
        change_text = f"{change:+.1%}" if change is not None else "—"
        observation_rows.append(f"<tr><td>{esc(row['before'])} → {esc(row['after'])}</td><td>{esc(row['roi'])}</td><td>{esc(row.get('goalStatus'))}</td><td>{row['beforeThresholdAreaPx']} → {row['afterThresholdAreaPx']} ({change_text})</td><td>{esc(row.get('anchorRelativeCentroidDeltaPx'))}</td></tr>")
    content += "<table><tr><th>同表情对照</th><th>ROI</th><th>目标状态</th><th>阈值像素 px 与变化比例</th><th>相对独立头锚点的重心差 px</th></tr>" + "".join(observation_rows) + "</table>"
    outline_rows = []
    for comparison in metrics["comparisons"]:
        outline = comparison.get("metrics", {}).get("browOutlineDisplacement")
        if not outline:
            continue
        outline_rows.append(f"<tr><td>{esc(comparison['before'])} → {esc(comparison['after'])}<br>{esc(comparison['roi'])}</td><td>{esc(outline.get('status'))}</td><td><pre>{esc(json.dumps({k:v for k,v in outline.items() if k != 'columnEnvelopeDelta'},ensure_ascii=False,indent=2))}</pre></td></tr>")
    if outline_rows:
        content += "<h2>眉毛完整外边界与上下端点</h2><p>以屏幕 y 向下为正，负数表示上移。记录所有外边界像素、上下极值、屏幕左右端点及逐列上下轮廓；头锚点由独立头骨投影取得。轮廓使用同一个固定 x 列比较，不移动图像或重新拟合。完整采样与文件 hash 可在各图的 outlineFile 和 metrics.json 中复查。mask 触碰边界或多个连通域时，需要先排查裁切、睫毛和发丝。</p><table><tr><th>固定对照</th><th>状态</th><th>端点 / 形状 / 头锚点</th></tr>" + "".join(outline_rows) + "</table>"
    brow_pixel_rows = []
    for comparison in metrics["comparisons"]:
        values = comparison.get("metrics", {})
        if "browOutlineDisplacement" in values:
            brow_pixel_rows.append(f"<tr><td>{esc(comparison['before'])} → {esc(comparison['after'])}<br>{esc(comparison['roi'])}</td><td>{esc(values.get('rgbChangedPixels'))}</td><td>{esc(values.get('rgbMaxAbsChannelDelta'))}</td><td>{esc(values.get('maskChangedPixels'))}</td><td>{esc(values.get('maskedRgbChangedPixels'))}</td></tr>")
    if brow_pixel_rows:
        content += "<h2>眉毛像素保护与邻近皮肤变化</h2><p>眉毛 mask 的所有标注像素、完整外轮廓及固定坐标下的真实 RGB 分开保护；任一眉毛像素颜色或形状改变都不满足零容差。ROI 内邻近皮肤的变化单列保留，不能被 mask 保护的通过掩盖。masked RGB 使用前后 mask 的 union，禁止移动 mask 或匹配拟合。</p><table><tr><th>固定对照</th><th>全 ROI 变化像素</th><th>最大通道变化（8-bit级）</th><th>眉毛 mask 变化像素</th><th>mask 内 RGB 变化像素</th></tr>" + "".join(brow_pixel_rows) + "</table>"
    if metrics.get("deformationChecks"):
        content += f"<h2>人工规定的形变技术阈值</h2><p>只检查清单明确规定的工程目标，不把数值变化解释成角色还原准确率。分割待复核时保留待复核状态。</p><pre>{esc(json.dumps(metrics['deformationChecks'],ensure_ascii=False,indent=2))}</pre>"
    lid_rows = []
    for comparison in metrics["comparisons"]:
        lid = comparison.get("metrics", {}).get("eyelidEnvelopeDisplacement")
        if lid:
            ranges = {key: lid.get(key) for key in ('minDeltaPx', 'maxDeltaPx', 'p10DeltaPx', 'p90DeltaPx')}
            lid_rows.append(f"<tr><td>{esc(comparison['before'])} → {esc(comparison['after'])}<br>{esc(comparison['roi'])}</td><td>{esc(lid.get('surface'))}<br>{esc(lid.get('status'))}</td><td>{esc(lid.get('selectedSharedColumns'))}</td><td>{esc(lid.get('medianDeltaPx'))}<pre>{esc(json.dumps(ranges,ensure_ascii=False,indent=2))}</pre></td><td><pre>{esc(json.dumps(lid.get('technicalMetrics',{}),ensure_ascii=False,indent=2))}</pre>{esc(lid.get('reason',''))}</td></tr>")
    if lid_rows:
        content += "<h2>上睑与下睑 · 分开观测</h2><p>上睑采用静态标注 ROI 内主深色睫毛连通域的外侧上轮廓；下睑采用巩膜浅色与金色虹膜 union 的下轮廓。两者是不同图像表面，不是完整开口重建。上半虹膜的深色不能当成上睑，所以不使用单个金色像素面积来判断上下睑。固定 x band 逐列比较，y 差为负表示上移。下缘颜色阈值可能跳过浅金色、暗色或反光边缘：每列极值与分位数全部保留，不能把较大的颜色边界跳跃当成真实几何位移。全闭眼的下缘颜色 mask 为空时，轮廓指标明确不可用，仍需检查实际闭眼画面。</p><table><tr><th>固定比较</th><th>实际测量表面</th><th>共有固定列</th><th>轮廓 y 差 median / 分位数 / 全范围 px</th><th>独立技术指标 / 范围限制</th></tr>" + "".join(lid_rows) + "</table>"
    performance = metrics.get("performance")
    if performance:
        perf_rows = []
        for scene in performance.get("scenes", []):
            summary = scene.get("summary", {})
            cpu, cadence = summary.get("avatarFrameCpuWall", {}), summary.get("rafCadence", {})
            fmt = lambda value: f"{value:.2f}" if isinstance(value, (int, float)) else "—"
            gpu = scene.get("gpuEvidence", {})
            gsummary = gpu.get("summary", {}) or {}
            gpu_text = "有采样声明" if gpu.get("declaredMeasured") is True else "未测 / 无有效采样声明" if gpu.get("declaredMeasured") is False else "schema 未声明，查看原始数据"
            perf_rows.append(f"<tr><td>{esc(scene.get('name'))}</td><td>{fmt(scene.get('elapsedMs',0)/1000)}</td><td>{esc(cpu.get('samples'))}</td><td>{fmt(summary.get('observedAvatarFramesPerSecond'))}</td><td>{fmt(cpu.get('meanMs'))} / {fmt(cpu.get('p95Ms'))}</td><td>{fmt(cadence.get('p95Ms'))}</td><td>{esc(gpu_text)}<br>{fmt(gsummary.get('meanMs'))} / {fmt(gsummary.get('p95Ms'))}</td></tr>")
        environment = performance.get("environment", {}) or {}
        gl_text = {key: environment.get(key) for key in ("webglRenderer", "webglVendor", "webglVersion", "webglShadingLanguageVersion") if environment.get(key) is not None}
        content += f"<h2>正常播放性能采样</h2><p>独立于固定姿态截图流程，采样实际 Avatar 更新和 WebGL 提交。CPU wall 包含驱动、VRM 更新和提交；GPU timer query 的有效结果单列，CPU 耗时和 rAF 间隔不能推算 GPU 时间。环境按运行记录展示，软件 GL 与 headless 结果不能承诺物理设备帧率。</p><p>实际 GL：<code>{esc(canonical(gl_text))}</code>；浏览器：<code>{esc(canonical(performance.get('browser')))}</code></p><table><tr><th>场景</th><th>时长 s</th><th>实际 Avatar 帧数</th><th>观测 FPS</th><th>CPU wall mean / P95 ms</th><th>rAF 间隔 P95 ms</th><th>GPU 采样 / mean / P95 ms</th></tr>" + "".join(perf_rows) + "</table><p><a href='performance.json'>全部原始帧间隔、逐帧与可选 GPU 查询记录</a></p>"
        content += f"<details><summary>原始 JSON schema、hash、数组长度 / 范围与 GPU 查询状态</summary><pre>{esc(json.dumps(performance,ensure_ascii=False,indent=2))}</pre></details>"
    content += "<h2>明确阶段和 ROI 的比较</h2><p>MAE 是局部 RGB 改变量；IoU 与边缘距离是颜色 mask 的形状变化。它们不相加为总准确率，也不用于比较不同表情的好坏。</p><table><tr><th>图像</th><th>阶段 / ROI</th><th>状态</th><th>独立指标 / 拒绝原因</th></tr>" + "".join(rows) + "</table>"
    content += "<h2>逐图证据与阈值标注</h2><p>粉色为虹膜阈值匹配，青色为眉部深色阈值匹配，黄色为眉部完整外边界，白色小圈为四个极值代表点。蓝框为人工 ROI。眉区可能混入睫毛或头发，全部剩余连通域均保留供复查，任何人工静态排除均记录在 annotation 中。</p>" + "".join(cards) + "</html>"
    (run_dir / "report.html").write_text(content, encoding="utf-8")


def analyze(run_dir, config_path):
    run_dir, config_path = Path(run_dir).resolve(), Path(config_path).resolve()
    config = read_json(config_path)
    if not config:
        raise ValueError(f"Missing calibration annotations: {config_path}")
    config_bytes = config_path.read_bytes()
    (run_dir / "analysis-config.json").write_bytes(config_bytes)
    script_bytes = Path(__file__).read_bytes()
    script_hash = hashlib.sha256(script_bytes).hexdigest()
    source_name = "analysis-source.py"
    initial_source = run_dir / source_name
    if initial_source.exists() and initial_source.read_bytes() != script_bytes:
        # Retain the first analysis source when a display-only correction is
        # rerun; current metrics point at the exact revised source/hash.
        source_name = f"analysis-source-{script_hash[:12]}.py"
    (run_dir / source_name).write_bytes(script_bytes)
    raw = read_json(run_dir / "captures.json")
    captures = raw.get("captures", []) if isinstance(raw, dict) else raw
    if not isinstance(captures, list) or not captures:
        raise ValueError("captures.json must contain a nonempty captures array")
    session = read_json(run_dir / "session.json", {})
    manifest = read_json(run_dir / "reference-manifest.json", {})
    performance_raw = read_json(run_dir / "performance.json", None)
    inputs = collect_inputs(run_dir, captures, manifest, session)
    images, masks, summaries = {}, {}, {}
    mask_dir = run_dir / "masks"
    mask_dir.mkdir(exist_ok=True)
    metrics = {"schemaVersion": 2, "humanReviewRequired": True,
               "hasApprovedTarget": any(r.get("approved") and r.get("role") in ("approved-target", "target", "approved-expression") for r in manifest.get("references", [])),
               "registration": {"matrix": [1, 0, 0, 1, 0, 0], "policy": "fixed-identity; reject differing fingerprints/dimensions"},
               "session": session, "referenceManifest": manifest, "config": config,
               "configProvenance": {"sourcePath": str(config_path), "file": "analysis-config.json", "sha256": hashlib.sha256(config_bytes).hexdigest(),
                                    "annotationPolicy": "Manual ROIs may be refined without recapturing the frozen image; this snapshot records the thresholds actually used."},
               "analysisProvenance": {"script": source_name, "sha256": script_hash, "dependencies": "Pillow + NumPy"},
               "units": {"areaPx": "pixels", "areaFractionOfRoi": "fraction in [0,1] of manual ROI", "centroidNormalized": "x/image width, y/image height", "anchorRelativeCentroidNormalized": "independent head anchor subtracted from normalized centroid", "bboxAspect": "width/height", "darkThicknessPx": "dark pixels per occupied ROI column", "rgbMaeNormalized": "mean absolute RGB delta / 255", "maskIoU": "intersection / union of threshold masks", "edgeDistance": "pixel Euclidean distance between 4-connected mask boundaries", "browOutlineDisplacement": "pixel centres relative to independent head anchor; screen y increases down; no registration", "deformationChecks": "Only explicitly authored engineering thresholds; no aesthetic accuracy"},
               "limitations": ["Manual model-specific ROIs and color thresholds; not a general eye/brow detector.", "Seed/Daily golden iris threshold can include similarly colored skin and omit dark iris or highlight.", "Brow threshold includes every component inside the ROI; hair/lashes must be excluded by annotation and reviewed.", "Anchor is independently projected head position, not an eye-derived registration anchor; it cannot validate head geometry.", "Empty mask is no evidence of perfect eyelid closure; inspect actual mask and rendered image.", "Baseline is not an approved artistic target. Numerical change is not aesthetic accuracy.", "Capture size, framing and lighting are preserved; no automatic image alignment or aggregate score."],
               "captures": [], "comparisons": [], "fullBlinkChecks": []}
    metrics["performance"] = performance_evidence(performance_raw, (run_dir / "performance.json").read_bytes() if performance_raw else None)
    for cid, capture in inputs.items():
        if not capture["_path"].exists():
            raise ValueError(f"Missing capture image {capture['_path']}")
        image = Image.open(capture["_path"]).convert("RGB")
        images[cid] = image
        rois, rules = normalized_config(config, capture)
        summary = {k: v for k, v in capture.items() if not k.startswith("_")}
        summary.update(imageSizePx=list(image.size), rois={}, protectionRois={})
        summary["provisionalIntent"] = capture_intent(capture, config)
        overlay = np.asarray(image).copy()
        masks[cid] = {}
        for name, annotation in rois.items():
            kind = annotation.get("kind") if isinstance(annotation, dict) else None
            kind = kind or ("iris" if "iris" in name else "brow" if "brow" in name else "display-only")
            if kind == "protection":
                summary["protectionRois"][name] = {"kind": "image-only-protection", "rectPx": list(roi_rect(annotation, image.size)),
                                                    "annotation": annotation, "definition": "Frozen neighborhood measured by actual RGB change only; no threshold segmentation.",
                                                    "humanReviewRequired": True}
                continue
            if kind not in ("iris", "brow", "upper_lid", "lower_lid"):
                continue
            rect = roi_rect(annotation, image.size)
            x0, y0, x1, y1 = rect
            crop = np.asarray(image)[y0:y1, x0:x1]
            rule = annotation.get("segmentation", rules.get(kind, {})) if isinstance(annotation, dict) else rules.get(kind, {})
            if not rule:
                continue
            raw_mask = color_mask(crop, rule)
            annotated_mask = apply_manual_mask_annotation(raw_mask, annotation, rect, image.size)
            mask, selection = select_annotated_components(annotated_mask, annotation)
            masks[cid][name] = mask
            part = summarize_mask(mask, rect, image.size, anchor_xy(capture.get("headAnchor")), kind)
            if selection:
                part["componentSelection"] = selection
                part["outlineReviewReasons"].extend(selection["reviewReasons"])
            if kind == "brow" and isinstance(annotation, dict) and "maxDetachedComponentAreaPx" in annotation:
                limit = annotation["maxDetachedComponentAreaPx"]
                if not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
                    raise ValueError("maxDetachedComponentAreaPx must be an explicitly annotated nonnegative finite pixel area")
                detached = part["components"][1:]
                accepted = all(component["areaPx"] <= limit for component in detached)
                part["detachedComponentPolicy"] = {"manualMaxAreaPx": limit, "detachedAreasPx": [component["areaPx"] for component in detached],
                                                    "withinManualLimit": accepted, "removedPixels": 0,
                                                    "definition": "Explicit annotation permits detached regions up to this area in engineering checks. All pixels and extrema remain measured."}
                if accepted:
                    part["outlineReviewReasons"] = [reason for reason in part["outlineReviewReasons"] if not reason.startswith("Multiple components")]
            mask_path = f"masks/{safe_name(cid)}-{safe_name(name)}.png"
            Image.fromarray((mask * 255).astype(np.uint8)).save(run_dir / mask_path)
            part.update(kind=kind, rectPx=list(rect), annotation=annotation, threshold=rule, maskFile=mask_path,
                        rawThresholdAreaPx=int(raw_mask.sum()), manualExcludedThresholdAreaPx=int(raw_mask.sum() - annotated_mask.sum()),
                        componentExcludedThresholdAreaPx=int(annotated_mask.sum() - mask.sum()),
                        humanReviewRequired=True, method="color threshold inside fixed manual ROI; any static polygons/component selection are explicitly recorded; no tracking or registration")
            if selection or not np.array_equal(raw_mask, mask):
                raw_mask_path = f"masks/{safe_name(cid)}-{safe_name(name)}-raw.png"
                Image.fromarray((raw_mask * 255).astype(np.uint8)).save(run_dir / raw_mask_path)
                part["rawMaskFile"] = raw_mask_path
            if "outline" in part:
                outline_file = f"masks/{safe_name(cid)}-{safe_name(name)}-outline.json"
                outline_bytes = json.dumps(part["outline"], ensure_ascii=False, indent=2).encode("utf-8")
                (run_dir / outline_file).write_bytes(outline_bytes)
                part.update(outlineFile=outline_file, outlineSha256=hashlib.sha256(outline_bytes).hexdigest())
                # The complete sample arrays stay in the hashed JSON rather than
                # copying every sample into the main report for every frame.
                part["outline"] = {key: value for key, value in part["outline"].items() if key != "externalBoundaryPx"}
            summary["rois"][name] = part
            color = np.asarray([255, 75, 165] if kind == "iris" else [50, 230, 210])
            if kind in ("upper_lid", "lower_lid"):
                roi_overlay = crop.copy()
                roi_overlay[mask] = (roi_overlay[mask].astype(float) * .35 + color * .65).astype(np.uint8)
                for yy, xx in exterior_boundary(mask):
                    roi_overlay[yy, xx] = [255, 208, 80]
                part["roiOverlayFile"] = f"masks/{safe_name(cid)}-{safe_name(name)}-roi-overlay.png"
                Image.fromarray(roi_overlay).save(run_dir / part["roiOverlayFile"])
                continue
            region = overlay[y0:y1, x0:x1]
            region[mask] = (region[mask].astype(float) * .35 + color * .65).astype(np.uint8)
            if kind == "brow":
                for yy, xx in exterior_boundary(mask):
                    region[yy, xx] = [255, 208, 80]
        overlay_image = Image.fromarray(overlay)
        draw = ImageDraw.Draw(overlay_image)
        for name, part in summary["rois"].items():
            if part.get("kind") in ("upper_lid", "lower_lid"):
                continue
            x0, y0, x1, y1 = part["rectPx"]
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline="#66a7ff", width=1)
            draw.text((x0, max(0, y0 - 14)), name, font=font(11), fill="#66a7ff")
            for endpoint in part.get("outline", {}).get("endpoints", {}).values():
                ex, ey = endpoint["representativePx"]
                draw.ellipse((ex - 2, ey - 2, ex + 2, ey + 2), outline="#ffffff", width=1)
        for name, part in summary["protectionRois"].items():
            x0, y0, x1, y1 = part["rectPx"]
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline="#ffa766", width=1)
            draw.text((x0, max(0, y0 - 14)), name, font=font(11), fill="#ffa766")
        anchor = anchor_xy(capture.get("headAnchor"))
        if anchor:
            ax, ay = anchor[0] * image.width, anchor[1] * image.height
            draw.line((ax - 5, ay, ax + 5, ay), fill="#ffffff", width=2)
            draw.line((ax, ay - 5, ax, ay + 5), fill="#ffffff", width=2)
        summary["overlayFile"] = f"masks/{safe_name(cid)}-overlay.png"
        overlay_image.save(run_dir / summary["overlayFile"])
        summaries[cid] = summary
        metrics["captures"].append(summary)
        blink = blink_value(capture)
        params = capture.get("params", {})
        closed = blink is not None and blink >= .999
        closed = closed or ("ParamEyeLOpen" in params and "ParamEyeROpen" in params and max(params["ParamEyeLOpen"], params["ParamEyeROpen"]) <= .001)
        if closed:
            iris_parts = {k: v["areaPx"] for k, v in summary["rois"].items() if v["kind"] == "iris"}
            total = sum(iris_parts.values())
            metrics["fullBlinkChecks"].append({"id": cid, "status": "threshold-pixels-present-review-required" if total else "no-threshold-pixels-found-review-required",
                                              "totalThresholdPixels": total, "roiThresholdPixels": iris_parts, "overlayFile": summary["overlayFile"], "parameterClosureRequested": True,
                                              "visualClosureVerified": False})
    declared = list(manifest.get("comparisons", config.get("comparisons", [])))
    goals = deformation_goals(manifest, config)
    for goal in goals:
        if not any(item.get("before") == goal.get("before") and item.get("after") == goal.get("after") and (not item.get("roi") or item.get("roi") == goal.get("roi")) for item in declared):
            declared.append({key: goal.get(key) for key in ("before", "after", "roi")} | {"stage": "deformation-contract", "purpose": goal.get("label", goal.get("id"))})
    comparisons = []
    for item in declared:
        if item.get("roi"):
            comparisons.append(item)
        else:
            # A declared global four-ROI protocol is expanded into separate measures,
            # never into an aggregate score or an implicit image-wide comparison.
            for name in config.get("rois", {}):
                comparisons.append(dict(item, roi=name, roiSource="declared calibration config"))
    paired = []
    for comparison in comparisons:
        item = dict(comparison, registration="identity", humanReviewRequired=True)
        before_id, after_id = item.get("before"), item.get("after")
        if before_id not in inputs or after_id not in inputs:
            item.update(status="rejected", reason="One or both declared capture IDs are absent")
        else:
            a, b = inputs[before_id], inputs[after_id]
            ar, br = normalized_config(config, a)[0], normalized_config(config, b)[0]
            roi = item.get("roi")
            if not item.get("stage") or not roi:
                item.update(status="rejected", reason="Comparison must explicitly declare stage and ROI")
            elif not a.get("fingerprint") or not b.get("fingerprint") or canonical(a["fingerprint"]) != canonical(b["fingerprint"]):
                item.update(status="rejected", reason="Capture fingerprint missing or different; alignment is frozen")
            elif images[before_id].size != images[after_id].size:
                item.update(status="rejected", reason="Viewport/image dimensions differ")
            elif roi not in ar or roi not in br or canonical(ar[roi]) != canonical(br[roi]):
                item.update(status="rejected", reason="Manual ROI missing or annotations differ")
            else:
                rect = roi_rect(ar[roi], images[before_id].size)
                item.update(status="measured", rectPx=list(rect), beforeStage=a.get("stage"), afterStage=b.get("stage"))
                item["metrics"] = compare_images(images[before_id], images[after_id], rect, masks[before_id].get(roi), masks[after_id].get(roi))
                ac = summaries[before_id]["rois"].get(roi, {}).get("anchorRelativeCentroidNormalized")
                bc = summaries[after_id]["rois"].get(roi, {}).get("anchorRelativeCentroidNormalized")
                item["metrics"]["anchorRelativeCentroidDisplacementNormalized"] = [bc[0] - ac[0], bc[1] - ac[1]] if ac and bc else None
                apart, bpart = summaries[before_id]["rois"].get(roi, {}), summaries[after_id]["rois"].get(roi, {})
                iw, ih = images[before_id].size
                generic = {"maxRgbMaeNormalized": item["metrics"]["rgbMaeNormalized"]}
                generic["minRgbMaeNormalized"] = generic["maxRgbMaeNormalized"]
                for source_key, target_key in (("maskChangedPixels", "maxMaskChangedPixels"), ("maskedRgbChangedPixels", "maxMaskedRgbChangedPixels"), ("maskedRgbMaeNormalized", "maxMaskedRgbMaeNormalized")):
                    if item["metrics"].get(source_key) is not None:
                        generic[target_key] = item["metrics"][source_key]
                if apart.get("areaPx"):
                    generic["maxAbsAreaChangeFraction"] = abs(bpart.get("areaPx", 0) / apart["areaPx"] - 1)
                if ac and bc:
                    generic["maxAbsCentroidDisplacementPx"] = math.hypot((bc[0] - ac[0]) * iw, (bc[1] - ac[1]) * ih)
                ahead, bhead = anchor_xy(a.get("headAnchor")), anchor_xy(b.get("headAnchor"))
                if ahead and bhead:
                    generic["maxAnchorDisplacementPx"] = math.hypot((bhead[0] - ahead[0]) * iw, (bhead[1] - ahead[1]) * ih)
                item["metrics"]["roiDeformationMetrics"] = generic
                if apart.get("kind") == "brow" and bpart.get("kind") == "brow":
                    item["metrics"]["browOutlineDisplacement"] = outline_displacement(apart, bpart, images[before_id].size)
                if apart.get("kind") in ("upper_lid", "lower_lid") and bpart.get("kind") == apart.get("kind"):
                    item["metrics"]["eyelidEnvelopeDisplacement"] = eyelid_envelope_displacement(apart, bpart, images[before_id].size)
                if a.get("stage") == "baseline" and b.get("stage") == "candidate" and (before_id, after_id) not in paired:
                    paired.append((before_id, after_id))
        metrics["comparisons"].append(item)
    metrics["deformationChecks"] = evaluate_deformation_goals(goals, metrics["comparisons"])
    metrics["observations"] = []
    for comparison in metrics["comparisons"]:
        if comparison["status"] != "measured":
            continue
        before_id, after_id, roi = comparison["before"], comparison["after"], comparison["roi"]
        a, b = summaries[before_id]["rois"].get(roi), summaries[after_id]["rois"].get(roi)
        if not a or not b:
            continue
        delta = comparison["metrics"].get("anchorRelativeCentroidDisplacementNormalized")
        iw, ih = images[before_id].size
        goal = capture_goal(inputs[after_id], config)
        metrics["observations"].append({"before": before_id, "after": after_id, "stage": comparison["stage"], "roi": roi,
                                        "goalStatus": "deferred; baseline preserved" if deferred_goal(goal) else goal.get("status", "provisional visual review"),
                                        "beforeThresholdAreaPx": a["areaPx"], "afterThresholdAreaPx": b["areaPx"],
                                        "thresholdAreaChangeFraction": b["areaPx"] / a["areaPx"] - 1 if a["areaPx"] else None,
                                        "anchorRelativeCentroidDeltaPx": [delta[0] * iw, delta[1] * ih] if delta else None})
    anchor_groups = {}
    for capture in captures:
        point = anchor_xy(capture.get("headAnchor"))
        if not point or not capture.get("fingerprint"):
            continue
        group = anchor_groups.setdefault(canonical(capture["fingerprint"]), {"fingerprint": capture["fingerprint"], "originNormalized": point, "captureCount": 0, "maxDisplacementPx": 0, "maxAbsoluteDeltaPx": [0, 0]})
        iw, ih = images[capture["id"]].size
        dx, dy = (point[0] - group["originNormalized"][0]) * iw, (point[1] - group["originNormalized"][1]) * ih
        group["captureCount"] += 1
        group["maxDisplacementPx"] = max(group["maxDisplacementPx"], math.hypot(dx, dy))
        group["maxAbsoluteDeltaPx"] = [max(group["maxAbsoluteDeltaPx"][0], abs(dx)), max(group["maxAbsoluteDeltaPx"][1], abs(dy))]
    metrics["anchorStability"] = {"source": "independent projected humanoid head bone", "units": "actual capture pixels", "groups": list(anchor_groups.values()),
                                  "limitation": "A stable bone anchor freezes comparison coordinates; it does not establish face or eyelid correctness."}
    render_inputs = [c for c in captures if c["id"] in images]
    face_roi = config.get("displayRoi", config.get("rois", {}).get("face"))
    expression_captures = [c for c in render_inputs if c["id"].startswith(("baseline-", "candidate-"))]
    if not expression_captures:
        expression_captures = [c for c in render_inputs if blink_value(c) in (None, 0)]
    sheet([(capture_label(c), images[c["id"]], capture_intent(c, config)) for c in expression_captures], run_dir / "contact-sheet.png", "眉眼校准 · 实际渲染", "人工复核必需；基线与候选不是审美目标", crop=face_roi)
    blink_captures = [c for c in render_inputs if "-blink-" in c["id"]]
    if not blink_captures:
        blink_captures = [c for c in render_inputs if blink_value(c) is not None and blink_value(c) > 0]
    sheet([(f"{c['id'].split('-blink-')[0]} · {'回张' if blink_value(c) == 0 else '闭合 ' + format(blink_value(c), '.0%')}", images[c["id"]], capture_intent(c, config)) for c in blink_captures], run_dir / "blink-sheet.png", "眨眼中间帧 · 遮挡检查", "检查虹膜残留、眼皮穿插与回张；阈值结果需看 mask", columns=5, crop=face_roi)
    deformation_captures = [c for c in render_inputs if c.get("stage") == "deformation-isolated" or isinstance(c.get("pose"), dict) and c["pose"].get("deformation") or c.get("id", "").startswith(("brow-y-", "brow-isolated-"))]
    stress_captures = [c for c in render_inputs if c.get("stage") == "deformation-stress" or "stress" in c.get("id", "") or "gaze-blink" in c.get("id", "")]
    lid_model = config.get("model") == "daily-lids"
    isolated_title = "独立眼睑与笑眼：固定坐标观察" if lid_model else "独立抬眉 · 固定坐标观察"
    isolated_subtitle = "上下睑分别观测 / 左右隔离 / 眉毛与嘴部保护；不是艺术还原评分" if lid_model else "完整轮廓 / 上下端点 / 对侧与眼区保持；不是艺术还原评分"
    for selected, filename, title, subtitle in ((deformation_captures, "deformation-sheet.png", isolated_title, isolated_subtitle),
                                                (stress_captures, "stress-sheet.png", "极端注视与眨眼 · 形变组合", "检查轮廓裁切、发丝污染、眼皮遮挡与完整闭合")):
        if selected:
            sheet([(capture_label(c), images[c["id"]], canonical(c.get("pose", {}))) for c in selected], run_dir / filename, title, subtitle, columns=3, crop=face_roi)
        elif (run_dir / filename).exists():
            (run_dir / filename).unlink()
    if paired:
        pair_images = []
        for a, b in paired:
            pair_images.extend([(f"基线 · {a}", images[a], capture_intent(inputs[a], config)), (f"候选 · {b}", images[b], capture_intent(inputs[b], config))])
        sheet(pair_images, run_dir / "before-after.png", "同条件 · 基线与候选", "冻结 camera / pose / viewport / model；不自动重新配准", columns=2, crop=face_roi)
    elif (run_dir / "before-after.png").exists():
        (run_dir / "before-after.png").unlink()
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    build_report(run_dir, metrics)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Directory containing captures.json and session.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config/avatar-eye-calibration.json")
    args = parser.parse_args()
    metrics = analyze(args.run_dir, args.config)
    print(json.dumps({"report": str(args.run_dir.resolve() / "report.html"), "captures": len(metrics["captures"]),
                      "measuredComparisons": sum(x["status"] == "measured" for x in metrics["comparisons"]),
                      "rejectedComparisons": sum(x["status"] == "rejected" for x in metrics["comparisons"]), "humanReviewRequired": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
