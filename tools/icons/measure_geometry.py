"""Compare localized geometry against the untouched references at native size."""

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree
from skimage.measure import find_contours

ROOT = Path(__file__).resolve().parents[2] / "assets" / "icons"


def contour_points(field, level=127.5):
    return np.vstack(find_contours(field, level))


def distance_statistics(original, rendered):
    distances = np.r_[cKDTree(original).query(rendered)[0], cKDTree(rendered).query(original)[0]]
    return {
        "mean_px": float(distances.mean()),
        "p95_px": float(np.percentile(distances, 95)),
        "maximum_px": float(distances.max()),
    }


def triangle_measurements(array):
    gray = array.min(2)
    records = []
    for y in [*range(390, 450), *range(780, 840)]:
        inds = np.flatnonzero(np.diff((gray[y] > 127.5).astype(int)))
        if len(inds) != 4:
            continue
        records.append([y + 0.5, *[x + 0.5 + (127.5 - gray[y, x]) / (gray[y, x + 1] - gray[y, x]) for x in inds]])
    records = np.array(records)
    lines = np.array([np.polyfit(records[:, 0], records[:, i], 1) for i in range(1, 5)])
    top_y = (lines[3, 1] - lines[0, 1]) / (lines[0, 0] - lines[3, 0])
    bottom = np.flatnonzero(gray[:, 600] > 127.5)[-1]
    return {
        "side_lines_x_equals_m_y_plus_b": lines.tolist(),
        "extrapolated_sharp_outer_apex_xy": [float(np.polyval(lines[0], top_y)), float(top_y)],
        "visible_rounded_apex_y": int(np.flatnonzero(gray[:, 627] > 127.5)[0]),
        "base_bottom_threshold_y": int(bottom),
    }


def main():
    output = {}
    for name in ("optical_burst", "prism_spectrum"):
        reference = np.array(Image.open(ROOT / "source" / f"{name}_reference.png"), dtype=float)
        # build_icons.py uses a black-composited native render for full-frame metrics.
        candidates = [ROOT / "validation" / name / "rendered_1254.png"]
        candidates = [p for p in candidates if p.exists()]
        if not candidates:
            raise FileNotFoundError("Run build_icons.py before localized geometry measurements")
        rgba = Image.open(candidates[0]).convert("RGBA")
        composite = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        composite.alpha_composite(rgba)
        rendered = np.array(composite.convert("RGB"), dtype=float)
        if reference.shape != rendered.shape:
            raise ValueError("The selected render is not native 1254 x 1254")
        measurements = {}
        alpha = np.asarray(rgba)[:, :, 3].astype(float) / 255
        rgba_rgb = np.array(rgba)[:, :, :3].astype(float)
        # Low-contrast source boundary has no alpha ground truth: estimate it from
        # nearest opaque background RGB and label this diagnostic accordingly.
        _, indices = distance_transform_edt(alpha < 1, return_indices=True)
        expected = rgba_rgb[indices[0], indices[1]]
        projection = (reference * expected).sum(2) / ((expected * expected).sum(2) + 1e-6)
        projection[reference.max(2) > 80] = 1
        projection = gaussian_filter(np.clip(projection, 0, 1), 0.65)
        source_boundaries = find_contours(projection, 0.5)
        alpha_boundaries = find_contours(alpha, 0.5)
        if name == "prism_spectrum":

            def corners_only(contours):
                return np.vstack(
                    [
                        c
                        for c in contours
                        if len(c) > 100
                        and (c[:, 0].max() < 350 or c[:, 0].min() > 900)
                        and (c[:, 1].max() < 350 or c[:, 1].min() > 900)
                    ]
                )

            source_boundary = corners_only(source_boundaries)
            alpha_boundary = corners_only(alpha_boundaries)
        else:
            source_boundary = max(source_boundaries, key=len)
            alpha_boundary = max(alpha_boundaries, key=len)
        measurements["squircle_estimated_boundary"] = {
            "method": "RGB-projection threshold compared with rendered alpha; estimated source edge, not original alpha ground truth",
            "distance": distance_statistics(source_boundary, alpha_boundary),
        }
        if name == "optical_burst":
            mask = (reference[:, :, 0] > 127.5).astype("uint8")
            count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
            components = []
            for i in range(1, count):
                x, y, w, h, area = stats[i]
                if area < 1000:
                    continue
                bounds = (max(0, x - 4), max(0, y - 4), min(1254, x + w + 4), min(1254, y + h + 4))
                x0, y0, x1, y1 = bounds
                original = contour_points(reference[y0:y1, x0:x1, 0])
                result = contour_points(rendered[y0:y1, x0:x1, 0])
                components.append(
                    {
                        "reference_bounds_xywh": [int(v) for v in (x, y, w, h)],
                        "colored_area_px": int(area),
                        "contour_distance": distance_statistics(original, result),
                    }
                )
            measurements["five_components_including_all_cut_boundaries"] = components
        else:
            measurements["reference_triangle"] = triangle_measurements(reference)
            measurements["rendered_triangle"] = triangle_measurements(rendered)
            beams = []
            for x in (100, 1100):
                orig = reference[:, x].max(1)
                final = rendered[:, x].max(1)

                def edges(values):
                    indices = np.flatnonzero(np.diff((values > 127.5).astype(int)))
                    indices = indices[(indices > 440) & (indices < 780)]
                    return np.array([p + 0.5 + (127.5 - values[p]) / (values[p + 1] - values[p]) for p in indices])

                oe, re = edges(orig), edges(final)
                if len(oe) != 16 or len(re) != 16:
                    raise AssertionError("Expected eight beams with sixteen edges")
                beams.append(
                    {
                        "x": x,
                        "reference_edges_y": oe.tolist(),
                        "rendered_edges_y": re.tolist(),
                        "absolute_edge_error_px": np.abs(oe - re).tolist(),
                        "maximum_edge_error_px": float(np.abs(oe - re).max()),
                    }
                )
            measurements["eight_ray_edges"] = beams
        output[name] = measurements
    (ROOT / "validation" / "localized_geometry.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
