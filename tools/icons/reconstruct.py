"""Reconstruct approved icons from hash-verified references, without importing IOPanel.

This explicit reconstruction replaces the SVG masters. Use build_icons.py to
export maintained masters without replacing their geometry.
"""

import itertools
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import resvg_py
from PIL import Image
from scipy.interpolate import BSpline
from scipy.sparse import csr_matrix, diags, eye, kron, vstack
from scipy.sparse.linalg import lsqr
from skimage.measure import find_contours

ROOT = Path(__file__).resolve().parents[2] / "assets" / "icons"
N = 1254


def field_fit(a, valid, step=70, reg=0.12):
    y, x = np.where(valid[::5, ::5])
    y = y * 5
    x = x * 5
    knots = np.r_[[0] * 4, np.arange(step, N, step), [N] * 4].astype(float)
    bx = BSpline.design_matrix(x, knots, 3)
    by = BSpline.design_matrix(y, knots, 3)
    m = bx.shape[1]
    rows = np.repeat(np.arange(len(x)), 16)
    cols = (by.indices.reshape(-1, 4)[:, :, None] * m + bx.indices.reshape(-1, 4)[:, None, :]).reshape(-1)
    vals = (by.data.reshape(-1, 4)[:, :, None] * bx.data.reshape(-1, 4)[:, None, :]).reshape(-1)
    mat = csr_matrix((vals, (rows, cols)), shape=(len(x), m * m))
    d = diags([np.ones(m - 2), -2 * np.ones(m - 2), np.ones(m - 2)], [0, 1, 2], shape=(m - 2, m))
    penalty = vstack([kron(d, eye(m)), kron(eye(m), d)]) * reg
    mat = vstack([mat, penalty]).tocsr()
    coeff = []
    for c in range(3):
        coeff.append(
            lsqr(mat, np.r_[a[y, x, c], np.zeros(penalty.shape[0])], atol=1e-7, btol=1e-7, iter_lim=600)[0].reshape(
                m, m
            )
        )

    def evaluate(xs, ys):
        xx = BSpline.design_matrix(xs, knots, 3).toarray()
        yy = BSpline.design_matrix(ys, knots, 3).toarray()
        return np.clip(np.stack([yy @ cc @ xx.T for cc in coeff], -1), 0, 255)

    return evaluate


def color(v):
    return "#" + "".join(f"{round(c):02x}" for c in np.clip(v, 0, 255))


def gradient(id, colors, xs, y=None):
    stops = "".join(f'<stop offset="{x / N:.6f}" stop-color="{color(c)}"/>' for x, c in zip(xs, colors))
    return f'<linearGradient id="{id}" gradientUnits="userSpaceOnUse" x1="0" x2="1254" y1="0" y2="0">{stops}</linearGradient>'


def paint_field(prefix, fn, defs, body, clip, rows=24, cols=32, ybounds=None):
    xs = np.linspace(0, N, cols + 1)
    ys = np.round(np.linspace(*(ybounds or (0, N)), rows + 1))
    cc = fn(xs, ys)
    for i in range(rows + 1):
        defs.append(gradient(f"{prefix}-row-{i}", cc[i], xs))
    body.append(f'<g id="{prefix}"' + (f' clip-path="url(#{clip})"' if clip else "") + ">")
    for i in range(rows):
        y0, y1 = ys[i : i + 2]
        mid = f"{prefix}-blend-{i}"
        defs.append(
            f'<linearGradient id="{mid}-g" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="{y0:.5f}" y2="{y1:.5f}"><stop stop-color="white" stop-opacity="0"/><stop offset="1" stop-color="white"/></linearGradient><mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="{y0:.5f}" width="1254" height="{y1 - y0:.5f}"><rect x="0" y="{y0:.5f}" width="1254" height="{y1 - y0:.5f}" fill="url(#{mid}-g)"/></mask>'
        )
        body.append(
            f'<g shape-rendering="crispEdges"><rect y="{y0:.5f}" width="1254" height="{y1 - y0:.5f}" fill="url(#{prefix}-row-{i})"/><rect y="{y0:.5f}" width="1254" height="{y1 - y0:.5f}" fill="url(#{prefix}-row-{i + 1})" mask="url(#{mid})"/></g>'
        )
    body.append("</g>")


def bezier_contour(points, tol=0.28):
    # Recursive least-squares cubic fitting, tangent constrained at segment ends.
    p = np.asarray(points)[:, ::-1]  # xy
    p = np.vstack([p, p[0]]) if np.linalg.norm(p[0] - p[-1]) > 1e-6 else p
    # Simplify only at subpixel tolerance to avoid carrying quantization noise.
    p = cv2.approxPolyDP(p.astype("float32").reshape(-1, 1, 2), 0.12, True).reshape(-1, 2).astype(float)
    # Begin at strongest corner; retain closed cyclic order.
    v = np.roll(p, -1, axis=0) - p
    prev = p - np.roll(p, 1, axis=0)
    cos = (v * prev).sum(1) / (np.linalg.norm(v, axis=1) * np.linalg.norm(prev, axis=1) + 1e-9)
    k = np.argmin(cos)
    p = np.roll(p, -k, axis=0)
    p = np.vstack([p, p[0]])
    segments = []

    def fit(q):
        if len(q) <= 2:
            segments.append(("L", q[-1]))
            return
        dist = np.r_[0, np.cumsum(np.linalg.norm(np.diff(q, axis=0), axis=1))]
        t = dist / dist[-1]
        t0 = q[min(2, len(q) - 1)] - q[0]
        t1 = q[max(0, len(q) - 3)] - q[-1]
        t0 /= np.linalg.norm(t0)
        t1 /= np.linalg.norm(t1)
        b0 = (1 - t) ** 3
        b1 = 3 * t * (1 - t) ** 2
        b2 = 3 * t * t * (1 - t)
        b3 = t**3
        base = (b0 + b1)[:, None] * q[0] + (b2 + b3)[:, None] * q[-1]
        A = np.stack([b1[:, None] * t0, b2[:, None] * t1], axis=-1).reshape(-1, 2)
        ab = np.linalg.lstsq(A, (q - base).reshape(-1), rcond=None)[0]
        c1 = q[0] + max(0, ab[0]) * t0
        c2 = q[-1] + max(0, ab[1]) * t1
        curve = b0[:, None] * q[0] + b1[:, None] * c1 + b2[:, None] * c2 + b3[:, None] * q[-1]
        error = np.linalg.norm(curve - q, axis=1)
        split = np.argmax(error)
        if error[split] <= tol:
            segments.append(("C", np.r_[c1, c2, q[-1]]))
        else:
            split = max(1, min(len(q) - 2, split))
            fit(q[: split + 1])
            fit(q[split:])

    # Split coarse arc into spans, so cyclic endpoints and corners remain stable.
    cuts = [0]
    last = 0
    for i in range(1, len(p) - 1):
        if np.linalg.norm(p[i] - p[last]) > 65 or cos[(i + k) % len(cos)] < 0.75:
            cuts.append(i)
            last = i
    cuts.append(len(p) - 1)
    for lo, hi in itertools.pairwise(cuts):
        fit(p[lo : hi + 1])
    return "M" + ",".join(f"{x:.3f}" for x in p[0]) + "".join(
        cmd + " " + ",".join(f"{x:.3f}" for x in pt) for cmd, pt in segments
    ) + "Z", len(segments)


def paths_from(field, level, tol=0.28, min_area=20):
    paths = []
    counts = []
    for cont in find_contours(np.pad(field, 1), level):
        cont = cont - 1 + 0.5
        if abs(cv2.contourArea(cont[:, ::-1].astype("float32"))) < min_area:
            continue
        d, c = bezier_contour(cont, tol)
        paths.append(d)
        counts.append(c)
    return " ".join(paths), counts


def reconstruct(name):
    (ROOT / "validation").mkdir(parents=True, exist_ok=True)
    import hashlib

    expected = {
        "optical_burst": "c0d0e0509c5246ac7eb3f4ab72005d6bca648787762a4e82f7632af22a7dc136",
        "prism_spectrum": "ac953fa5170d99bac3f264b764a93dfbe98943f787b968cf95d3d5ea041bd25c",
    }
    ref = ROOT / "source" / f"{name}_reference.png"
    if hashlib.sha256(ref.read_bytes()).hexdigest() != expected[name]:
        raise ValueError("Authoritative reference SHA-256 mismatch: " + str(ref))
    a = np.array(Image.open(ROOT / "source" / f"{name}_reference.png")).astype(float)
    defs = []
    body = []
    stats = {}
    optical = name == "optical_burst"
    fg = (a[:, :, 0] > 80) & (np.ptp(a, axis=2) > 70) if optical else (a.max(2) > 80)
    valid = (a[:, :, 2] > 5) & (~cv2.dilate(fg.astype("uint8"), np.ones((9, 9), np.uint8)).astype(bool))
    bg = field_fit(a, valid, step=110, reg=0.6)
    # Distinguish the low-luminance squircle from the nearly black exterior.
    expected = bg(np.arange(N) + 0.5, np.arange(N) + 0.5)
    ratio = (a * expected).sum(2) / ((expected * expected).sum(2) + 1e-6)
    ratio[fg] = 1
    ratio = cv2.GaussianBlur(np.clip(ratio, 0, 1).astype("float32"), (0, 0), 0.65)
    # Keep only exterior contour; enclosed foreground holes cannot influence the boundary.
    binary = (ratio > 0.5).astype("uint8")
    # Ringing beside canvas-spanning prism rays can create narrow dark channels
    # connected to the exterior. Close those channels before fitting the outline.
    # This is boundary classification only; reference pixels remain untouched.
    if not optical:
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        # All beams and false channels are in this central band. Preserve the
        # corner classifications exactly, including tangencies at canvas edges.
        binary[400:800] = closed[400:800]
    conts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    largest = max(conts, key=cv2.contourArea)
    bmask = np.zeros((N, N), np.uint8)
    cv2.drawContours(bmask, [largest], -1, 1, -1)
    ratio = np.where(cv2.erode(bmask, np.ones((5, 5), np.uint8)) > 0, 1, ratio)
    bd, bc = paths_from(ratio, 0.5, tol=0.45, min_area=100000)
    defs.append(f'<clipPath id="squircle"><path d="{bd}"/></clipPath>')
    stats["background_path_segments"] = bc
    paint_field("background", bg, defs, body, None, rows=24, cols=24)
    if optical:
        interior = cv2.erode(fg.astype("uint8"), np.ones((7, 7), np.uint8)).astype(bool)
        fn = field_fit(a, interior, step=110, reg=0.8)
        occupancy = (a[:, :, 0] - expected[:, :, 0]) / (252 - expected[:, :, 0])
        occupancy = np.where(fg | cv2.dilate(fg.astype("uint8"), np.ones((3, 3), np.uint8)), occupancy, 0)
        d, ct = paths_from(occupancy, 0.5, tol=0.24, min_area=1000)
        defs.append(f'<clipPath id="burst-silhouette"><path d="{d}"/></clipPath>')
        # Large lobes share a field; the three small components have distinct color fields.
        num, labels, components, _ = cv2.connectedComponentsWithStats(fg.astype("uint8"))
        big = np.isin(labels, np.flatnonzero(components[:, 4] > 50000)[1:])
        dd, _ = paths_from(np.where(big, occupancy, 0), 0.5, tol=0.24, min_area=1000)
        defs.append(f'<clipPath id="burst-main"><path d="{dd}"/></clipPath>')
        fn = field_fit(a, interior & big, step=110, reg=0.8)
        paint_field("optical-burst-gradient", fn, defs, body, "burst-main", rows=32, cols=40)
        for j in range(1, num):
            if components[j, 4] > 50000:
                continue
            cm = labels == j
            expanded = cv2.dilate(cm.astype("uint8"), np.ones((3, 3), np.uint8)).astype(bool)
            dd, _ = paths_from(np.where(expanded, occupancy, 0), 0.5, tol=0.24, min_area=1000)
            clip = f"burst-component-{j}"
            defs.append(f'<clipPath id="{clip}"><path d="{dd}"/></clipPath>')
            # Local quadratic color field avoids unsupported extrapolation outside the component.
            y, x = np.where(interior & cm)
            xx = (x - 627) / 627
            yy = (y - 627) / 627
            M = np.array([np.ones(len(x)), xx, yy, xx * xx, xx * yy, yy * yy]).T
            coeff = np.linalg.lstsq(M, a[y, x], rcond=None)[0]

            def local(xs, ys, coeff=coeff):
                xx, yy = np.meshgrid((xs - 627) / 627, (ys - 627) / 627)
                return np.clip(np.stack([np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy], -1) @ coeff, 0, 255)

            cy0 = components[j, 1]
            cy1 = cy0 + components[j, 3]
            paint_field(
                f"component-{j}-gradient",
                local,
                defs,
                body,
                clip,
                rows=max(3, int((cy1 - cy0) / 30)),
                cols=32,
                ybounds=(cy0 - 2, cy1 + 2),
            )
        stats["component_path_segments"] = ct
    else:
        # Measure every beam at the left and right borders; fit observed slight thickness taper.
        edges = []
        for x in [100, 1100]:
            v = a[:, x].max(1)
            on = v > 100
            idx = np.flatnonzero(np.diff(on.astype(int)))
            edges.append([(i + 0.5 + (127.5 - v[i]) / (v[i + 1] - v[i])) for i in idx])
        centers = (np.array(edges[0])[::2] + np.array(edges[0])[1::2]) / 2
        outer = [(627.15, 332.36), (332.70, 868.14), (911.92, 868.14)]
        inner = [(627.10, 385.08), (374.70, 842.95), (870.04, 842.95)]
        # Corner contours are recovered from pixels; these are coarse localization seeds.
        for i, cy in enumerate(centers):
            lo = round(cy - 5)
            hi = round(cy + 5)
            xs = np.arange(N)
            cc = np.median(a[lo:hi], axis=0)
            # Exclude outline crossings and interpolate only across occluded samples.
            627.15 + (332.7 - 627.15) * (cy - 332.36) / (868.14 - 332.36)
            627.15 + (911.92 - 627.15) * (cy - 332.36) / (868.14 - 332.36)
            le = -0.55044913 * cy + 800.68933
            li = -0.55212040 * cy + 839.48895
            ri = 0.53039388 * cy + 423.47789
            re = 0.52877980 * cy + 462.28730
            good = ~(((xs > le - 4) & (xs < li + 4)) | ((xs > ri - 4) & (xs < re + 4)))
            for c in range(3):
                cc[:, c] = np.interp(xs, xs[good], cc[good, c])
            cc = cv2.GaussianBlur(cc[None, :, :], (0, 0), 3)[0]
            sample = np.unique(np.r_[np.arange(0, N, 16), N - 1])
            defs.append(gradient(f"beam-{i}", cc[sample], sample))
            lt, lb = edges[0][2 * i : 2 * i + 2]
            rt, rb = edges[1][2 * i : 2 * i + 2]
            # Straight, near-horizontal boundaries: extrapolate measured taper to canvas edges.
            lt - (rt - lt) * 0.1
            rt + (rt - lt) * 0.154
            lb - (rb - lb) * 0.1
            rb + (rb - lb) * 0.154
            # Recover the observed nearly horizontal edges; bridge only outline occlusions.
            edgevals = []
            for x in range(N):
                window = a[int(cy - 18) : int(cy + 19), x].max(1)
                inds = np.flatnonzero(np.diff((window > 127.5).astype(int)))
                if len(inds) == 2 and good[x]:
                    edgevals.append(
                        [
                            x + 0.5,
                            *[int(cy - 18) + k + 0.5 + (127.5 - window[k]) / (window[k + 1] - window[k]) for k in inds],
                        ]
                    )
            ev = np.array(edgevals)
            grid = np.r_[0, np.arange(4, N, 4), N]
            from scipy.ndimage import gaussian_filter1d

            top = gaussian_filter1d(np.interp(grid, ev[:, 0], ev[:, 1]), 1.2)
            bottom = gaussian_filter1d(np.interp(grid, ev[:, 0], ev[:, 2]), 1.2)
            contour = np.vstack([np.c_[top, grid], np.c_[bottom[::-1], grid[::-1]]])
            beamd, _ = bezier_contour(contour, 0.12)
            body.append(f'<path id="spectral-ray-{i + 1}" d="{beamd}" fill="url(#beam-{i})"/>')
            stats.setdefault("beams", []).append(
                {
                    "center_left": cy,
                    "edges_left": [lt, lb],
                    "edges_right": [rt, rb],
                    "rgb_at_1100": a[round(cy), 1100].tolist(),
                }
            )
        # Extract the outline independently, clearing rays outside triangle geometry.
        yy, xx = np.mgrid[:N, :N]
        pts = np.array(outer)
        inn = np.array(inner)
        tri = np.zeros((N, N), np.uint8)
        cv2.fillPoly(tri, [pts.astype("int32")], 1)
        hole = np.zeros((N, N), np.uint8)
        cv2.fillPoly(hole, [inn.astype("int32")], 1)
        ring = (tri - hole).astype("uint8")
        near = cv2.dilate(ring, np.ones((25, 25), np.uint8)).astype(bool)
        lum = a.min(2)
        alpha = np.clip((lum - 10) / 244, 0, 1)
        # Ray intersections obscure one edge: analytic constraints fill only those short spans.
        rayzone = np.zeros((N, N), bool)
        for cy in centers:
            rayzone |= abs(yy - cy) < 18
        alpha = np.where(near, alpha, 0)
        # Recover four straight side boundaries from unobscured rows, subpixel crossings.
        measurements = []
        for y in range(390, 840):
            if rayzone[y, 0]:
                continue
            crossings = np.flatnonzero(np.diff((lum[y] > 127).astype(int)))
            if len(crossings) != 4:
                continue
            values = [j + 0.5 + (127 - lum[y, j]) / (lum[y, j + 1] - lum[y, j]) for j in crossings]
            measurements.append([y + 0.5, *values])
        mm = np.array(measurements)
        lines = [np.polyfit(mm[:, 0], mm[:, i], 1) for i in range(1, 5)]
        for y in np.flatnonzero(rayzone[:, 0]):
            le, li, ri, re = [np.polyval(line, y + 0.5) for line in lines]
            xx1 = np.arange(N) + 0.5
            alpha[y] = np.maximum(
                np.clip(xx1 - le + 0.5, 0, 1) * np.clip(li - xx1 + 0.5, 0, 1),
                np.clip(xx1 - ri + 0.5, 0, 1) * np.clip(re - xx1 + 0.5, 0, 1),
            )
        td, tc = paths_from(alpha, 0.5, tol=0.28, min_area=100000)
        body.append(f'<path id="prism-outline" d="{td}" fill="#fefefe" fill-rule="evenodd"/>')
        stats["triangle_outer"] = outer
        stats["triangle_inner"] = inner
        stats["outline_segments"] = tc
        stats["measured_side_lines"] = np.array(lines).tolist()
    # All artwork is clipped to its observed background silhouette, including border beams.
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1254" height="1254" viewBox="0 0 1254 1254"><title>{name.replace("_", " ")}</title><desc>Vector reconstruction of the approved PNG; contours and sampled gradient fields. Exterior is transparent.</desc><defs>'
        + "".join(defs)
        + '</defs><g clip-path="url(#squircle)">'
        + "".join(body)
        + "</g></svg>"
    )
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    vector = ET.fromstring(svg)
    ET.indent(vector, space="  ")
    svg = ET.tostring(vector, encoding="unicode") + "\n"
    (ROOT / "svg" / f"{name}.svg").write_text(svg, encoding="utf8")
    (ROOT / "validation" / f"{name}_geometry.json").write_text(json.dumps(stats, indent=2))
    data = resvg_py.svg_to_bytes(svg_string=svg, width=N, height=N, skip_system_fonts=True)
    (ROOT / "validation" / f"{name}_direct_render.png").write_bytes(data)
    print(name, stats, "svg bytes", len(svg), flush=True)


if __name__ == "__main__":
    for name in ["optical_burst", "prism_spectrum"]:
        reconstruct(name)
