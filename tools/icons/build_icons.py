#!/usr/bin/env python3
"""Render IOPanel icon SVG masters and produce reproducible Windows assets.

This is deliberately a development tool.  It neither imports IOPanel nor adds
dependencies to its runtime environment.  Invoke with an environment containing
requirements-icon-tools.txt, for example:

  PYTHONPATH=../work/deps python tools/icons/build_icons.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from resvg_py import svg_to_bytes
from scipy.ndimage import distance_transform_edt, uniform_filter
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.feature import canny
from skimage.metrics import structural_similarity

ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "assets" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
NATIVE_SIZE = 1254
THRESHOLDS = (1, 5, 10, 25)
FONT = ImageFont.load_default()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(svg: Path, width: int, height: int | None = None, supersample: int = 4) -> Image.Image:
    """Render with resvg then deterministic BOX area integration to target pixels."""
    height = height or width
    png = svg_to_bytes(
        svg_path=str(svg),
        width=width * supersample,
        height=height * supersample,
        skip_system_fonts=True,
        shape_rendering="geometric_precision",
        text_rendering="geometric_precision",
        image_rendering="optimize_quality",
    )
    from io import BytesIO

    image = Image.open(BytesIO(png)).convert("RGBA")
    return image.resize((width, height), Image.Resampling.BOX) if supersample != 1 else image


def save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, compress_level=9)


def write_ico(pngs: dict[int, bytes], out: Path) -> None:
    """Write an ICO containing PNG payloads, including an exact 256px entry."""
    entries = []
    offset = 6 + 16 * len(pngs)
    for size, payload in sorted(pngs.items()):
        entries.append((size, payload, offset))
        offset += len(payload)
    data = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    for size, payload, payload_offset in entries:
        # A zero width/height is the documented ICO encoding for 256.
        encoded = 0 if size == 256 else size
        data.extend(struct.pack("<BBBBHHII", encoded, encoded, 0, 0, 1, 32, len(payload), payload_offset))
    for _, payload, _ in entries:
        data.extend(payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def parse_ico(path: Path) -> list[dict[str, Any]]:
    blob = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", blob, 0)
    if (reserved, kind) != (0, 1):
        raise ValueError(f"{path} is not an ICO")
    entries = []
    for idx in range(count):
        width, height, _palette, _reserved2, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", blob, 6 + idx * 16
        )
        width = width or 256
        height = height or 256
        payload = blob[offset : offset + length]
        decoded = Image.open(__import__("io").BytesIO(payload)).convert("RGBA")
        if decoded.size != (width, height):
            raise ValueError(f"ICO payload {idx} header/payload dimensions differ")
        entries.append(
            {
                "size": width,
                "height": height,
                "bits_per_pixel": bpp,
                "planes": planes,
                "payload_bytes": length,
                "png_signature": payload.startswith(b"\x89PNG\r\n\x1a\n"),
            }
        )
    return entries


def svg_checks(svg: Path) -> dict[str, Any]:
    text = svg.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "svg":
        raise ValueError(f"{svg} root is not SVG")
    viewbox = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(viewbox) != 4:
        raise ValueError(f"{svg} has no valid viewBox")
    vals = [float(v) for v in viewbox]
    square = math.isclose(vals[2], vals[3])
    if not square:
        raise ValueError(f"{svg} viewBox is not square: {viewbox}")
    if vals != [0.0, 0.0, float(NATIVE_SIZE), float(NATIVE_SIZE)]:
        raise ValueError(f"{svg} has an unexpected canvas viewBox: {viewbox}")
    lowered = text.lower()
    raster = any(el.tag.rsplit("}", 1)[-1] == "image" for el in root.iter()) or "data:image" in lowered
    # Namespace URLs are required SVG metadata, so inspect actual resource attributes.
    external = False
    for el in root.iter():
        for key, value in el.attrib.items():
            if key.rsplit("}", 1)[-1] in {"href", "src"} and not value.startswith("#"):
                external = True
            for target in re.findall(r"url\(([^)]+)\)", value):
                if not target.strip("'\"").startswith("#"):
                    external = True
    if raster or external:
        raise ValueError(f"{svg} contains raster image or external reference")
    ids = [el.attrib["id"] for el in root.iter() if "id" in el.attrib]
    duplicate_ids = len(ids) != len(set(ids))
    if duplicate_ids:
        raise ValueError(f"{svg} has duplicate IDs")
    references = re.findall(r"url\(#([^)]+)\)", text)
    if set(references) - set(ids):
        raise ValueError(f"{svg} references missing resources: {set(references) - set(ids)}")
    vector_tags = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "use"}
    vector_count = sum(el.tag.rsplit("}", 1)[-1] in vector_tags for el in root.iter())
    if not vector_count:
        raise ValueError(f"{svg} has no vector geometry")
    return {
        "viewBox": vals,
        "square_viewBox": square,
        "embedded_raster": raster,
        "external_reference": external,
        "unique_ids": not duplicate_ids,
        "all_internal_references_resolve": True,
        "vector_element_count": vector_count,
    }


def rgba_black(image: Image.Image) -> np.ndarray:
    """Composite RGBA onto opaque black, returning uint8 RGB."""
    a = np.asarray(image, dtype=np.float32)
    return np.rint(a[..., :3] * (a[..., 3:4] / 255.0)).astype(np.uint8)


def edge_metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    # Canny detects shape edges without treating the transparent exterior as data.
    ref_edge = canny(reference.mean(axis=2) / 255.0, sigma=1.0)
    can_edge = canny(candidate.mean(axis=2) / 255.0, sigma=1.0)
    if not ref_edge.any() or not can_edge.any():
        return {
            "edge_pixels_reference": int(ref_edge.sum()),
            "edge_pixels_render": int(can_edge.sum()),
            "symmetric_chamfer_px": float("nan"),
            "max95_chamfer_px": float("nan"),
        }
    r_to_c = distance_transform_edt(~can_edge)[ref_edge]
    c_to_r = distance_transform_edt(~ref_edge)[can_edge]
    all_distances = np.concatenate((r_to_c, c_to_r))
    return {
        "edge_pixels_reference": int(ref_edge.sum()),
        "edge_pixels_render": int(can_edge.sum()),
        "symmetric_chamfer_px": float(all_distances.mean()),
        "max95_chamfer_px": float(np.percentile(all_distances, 95)),
    }


def metrics(reference: Image.Image, candidate: Image.Image) -> dict[str, Any]:
    ref = rgba_black(reference)
    ren = rgba_black(candidate)
    delta = np.abs(ref.astype(np.int16) - ren.astype(np.int16))
    per_pixel = delta.max(axis=2)
    # SSIM is calculated on the full, black-composited original canvas.
    ssim = structural_similarity(ref, ren, channel_axis=2, data_range=255)
    downsample_ssims: dict[str, float] = {}
    for side in (627, 314, 157):
        a = np.asarray(Image.fromarray(ref).resize((side, side), Image.Resampling.LANCZOS))
        b = np.asarray(Image.fromarray(ren).resize((side, side), Image.Resampling.LANCZOS))
        downsample_ssims[str(side)] = float(structural_similarity(a, b, channel_axis=2, data_range=255))
    # Broad foreground errors include edge coverage; report an eroded interior separately.
    chroma_mask = (ref.max(axis=2) - ref.min(axis=2) > 20) | (ref.max(axis=2) > 100)
    if chroma_mask.any():
        lab_ref = rgb2lab(ref / 255.0)
        lab_ren = rgb2lab(ren / 255.0)
        de = deltaE_ciede2000(lab_ref, lab_ren)[chroma_mask]
        color = {
            "sample_pixels": int(chroma_mask.sum()),
            "deltaE2000_mean": float(de.mean()),
            "deltaE2000_p95": float(np.percentile(de, 95)),
            "deltaE2000_max": float(de.max()),
            "includes_geometry_at_region_edges": True,
        }
    else:
        color = {"sample_pixels": 0, "deltaE2000_mean": None, "deltaE2000_p95": None, "deltaE2000_max": None}
    safe_interior = uniform_filter(chroma_mask.astype(float), size=7) > 0.999
    ren_chroma = (ren.max(axis=2) - ren.min(axis=2) > 20) | (ren.max(axis=2) > 100)
    safe_interior &= uniform_filter(ren_chroma.astype(float), size=7) > 0.999
    all_de = deltaE_ciede2000(rgb2lab(ref / 255.0), rgb2lab(ren / 255.0))
    interior = all_de[safe_interior]
    return {
        "comparison": "RGB after alpha compositing both images over black; original bytes unchanged",
        "mae": float(delta.mean()),
        "rmse": float(np.sqrt(np.mean(delta.astype(np.float64) ** 2))),
        "max_channel_difference": int(delta.max()),
        "ssim": float(ssim),
        "percent_pixels_exceeding_channel_threshold": {
            str(t): float((per_pixel > t).mean() * 100.0) for t in THRESHOLDS
        },
        "downsample_ssim_not_multiscale_ssim": downsample_ssims,
        "edge_geometry": edge_metrics(ref, ren),
        "region_color": color,
        "foreground_interior_color": {
            "sample_pixels": int(safe_interior.sum()),
            "edge_exclusion_radius_px": 3,
            "deltaE2000_mean": float(interior.mean()),
            "deltaE2000_p95": float(np.percentile(interior, 95)),
        },
    }


def diff_images(reference: Image.Image, rendered: Image.Image, directory: Path) -> dict[str, str]:
    ref = rgba_black(reference)
    ren = rgba_black(rendered)
    diff = np.abs(ref.astype(np.int16) - ren.astype(np.int16)).astype(np.uint8)
    side = ref.shape[0]
    save_png(Image.fromarray(np.concatenate((ref, ren), axis=1)), directory / "side_by_side.png")
    overlay = np.rint(ref * 0.5 + ren * 0.5).astype(np.uint8)
    save_png(Image.fromarray(overlay), directory / "overlay_50pct.png")
    magnitude = diff.max(axis=2).astype(np.float32)
    # A simple deterministic blue->cyan->yellow->red heat scale.
    normal = np.clip(magnitude / max(1.0, np.percentile(magnitude, 99.5)), 0, 1)
    heat = np.stack((255 * normal, 255 * np.sqrt(normal), 255 * (1 - normal)), axis=2).astype(np.uint8)
    save_png(Image.fromarray(heat), directory / "absolute_difference_heatmap.png")
    ref_edge = canny(ref.mean(axis=2) / 255.0, sigma=1)
    ren_edge = canny(ren.mean(axis=2) / 255.0, sigma=1)
    edges = np.zeros((side, side, 3), dtype=np.uint8)
    edges[ref_edge] = (0, 255, 255)
    edges[ren_edge] = np.maximum(edges[ren_edge], (255, 0, 255))
    edges[ref_edge & ren_edge] = (255, 255, 255)
    save_png(Image.fromarray(edges), directory / "edge_overlay.png")
    # Produce four difference-centred crops with deterministic non-maximum selection.
    window = min(256, side)
    score = uniform_filter(magnitude, size=window, mode="constant")
    selected: list[tuple[int, int]] = []
    for _ in range(4):
        y, x = np.unravel_index(np.argmax(score), score.shape)
        x0 = max(0, min(side - window, x - window // 2))
        y0 = max(0, min(side - window, y - window // 2))
        selected.append((x0, y0))
        score[max(0, y - window) : min(side, y + window), max(0, x - window) : min(side, x + window)] = -1
    for index, (x0, y0) in enumerate(selected, 1):
        strip = np.concatenate(
            (
                ref[y0 : y0 + window, x0 : x0 + window],
                ren[y0 : y0 + window, x0 : x0 + window],
                heat[y0 : y0 + window, x0 : x0 + window],
            ),
            axis=1,
        )
        save_png(
            Image.fromarray(strip).resize((window * 6, window * 2), Image.Resampling.NEAREST),
            directory / f"discrepancy_crop_{index}.png",
        )
    return {
        "original": "original_reference_copy.png",
        "render": "rendered_1254.png",
        "side_by_side": "side_by_side.png",
        "overlay": "overlay_50pct.png",
        "heatmap": "absolute_difference_heatmap.png",
        "edge_overlay": "edge_overlay.png",
    }


def contact_sheet(icon: str, exports: dict[int, Image.Image], out: Path) -> None:
    backgrounds = [
        ("White", (255, 255, 255)),
        ("Light gray", (220, 220, 220)),
        ("Dark gray", (55, 55, 55)),
        ("Black", (0, 0, 0)),
        ("Synthetic Windows blue", (20, 75, 142)),
    ]
    sizes = tuple(s for s in SIZES if s <= 256)
    widths = [max(120, size + 24) for size in sizes]
    positions = np.r_[0, np.cumsum(widths)].astype(int)
    cell_h = 320
    canvas = Image.new("RGB", (int(positions[-1]), cell_h * len(backgrounds)), "white")
    draw = ImageDraw.Draw(canvas)
    for col, size in enumerate(sizes):
        cell_w = widths[col]
        draw.text((int(positions[col]) + 3, 2), f"{size} x {size}", fill="black", font=FONT)
        for row, (label, bg) in enumerate(backgrounds):
            x, y = int(positions[col]), row * cell_h + 18
            canvas.paste(bg, (x, y, x + cell_w, y + cell_h - 18))
            image = exports[size]
            px = x + (cell_w - size) // 2
            py = y + 10
            canvas.paste(image, (px, py), image)
            draw.text((x + 3, y + size + 18), label, fill=(255, 255, 255) if sum(bg) < 270 else "black", font=FONT)
    save_png(canvas, out / f"{icon}_native_size_contact_sheet.png")


def html_report(results: dict[str, Any], out: Path) -> None:
    rows = []
    for name, data in results.items():
        m = data["metrics"]
        rel = f"{name}/"
        previews = " ".join(
            f'<a href="{rel}{img}"><img src="{rel}{img}" alt="{img}"></a>'
            for img in (
                "original_reference_copy.png",
                "rendered_1254.png",
                "side_by_side.png",
                "absolute_difference_heatmap.png",
                "edge_overlay.png",
                "overlay_50pct.png",
                "discrepancy_crop_1.png",
                "discrepancy_crop_2.png",
                "discrepancy_crop_3.png",
                "discrepancy_crop_4.png",
            )
        )
        export_links = " · ".join(f'<a href="../png/{name}/{size}.png">{size} × {size}</a>' for size in data["exports"])
        native_rows = ""
        for label, bg in (
            ("White", "#ffffff"),
            ("Light gray", "#dcdcdc"),
            ("Dark gray", "#373737"),
            ("Black", "#000000"),
            ("Synthetic Windows blue (representative, not a desktop capture)", "#144b8e"),
        ):
            native_images = "".join(
                f'<figure><figcaption>{size} × {size}</figcaption><img src="../png/{name}/{size}.png" width="{size}" height="{size}" style="background:{bg}" alt="{name} {size} pixels on {label}"></figure>'
                for size in data["exports"]
            )
            native_rows += f'<h4>{label}</h4><div class="native-row" style="background:{bg}">{native_images}</div>'
        slider = f'<p>Full-size comparison on black: move the slider to reveal the vector render over the original. Browser zoom must be 100% for native pixels.</p><input type="range" min="0" max="100" value="50" aria-label="Comparison split" oninput="document.getElementById(\'{name}-split\').style.clipPath=\'inset(0 \'+(100-this.value)+\'% 0 0)\'"><div class="comparison-scroll"><div class="comparison"><img src="{rel}original_reference_copy.png" width="1254" height="1254" alt="Original"><img id="{name}-split" class="split" src="{rel}rendered_1254.png" width="1254" height="1254" alt="Vector render" style="clip-path:inset(0 50% 0 0)"></div></div>'
        rows.append(
            f"<section><h2>{name}</h2><table><tr><th>MAE</th><td>{m['mae']:.4f}</td><th>RMSE</th><td>{m['rmse']:.4f}</td><th>SSIM</th><td>{m['ssim']:.7f}</td></tr>"
            f"<tr><th>Max channel difference</th><td>{m['max_channel_difference']}</td><th>Chamfer px</th><td>{m['edge_geometry']['symmetric_chamfer_px']:.3f}</td><th>95th percentile edge distance</th><td>{m['edge_geometry']['max95_chamfer_px']:.3f}</td></tr></table>"
            f"<p>Comparison: {m['comparison']}. Downsample SSIM values are diagnostics, not MS-SSIM.</p><div class=previews>{previews}</div>"
            f'<p>Native PNG exports (actual dimensions): {export_links}</p><p><a href="{name}/metrics.json">Machine-readable metrics</a> · <a href="{name}/{name}_native_size_contact_sheet.png">native-size contact sheet (16–256)</a></p>{slider}<details><summary>All nine native sizes on five backgrounds</summary>{native_rows}</details></section>'
        )
    out.write_text(
        "<!doctype html><meta charset=utf-8><title>IOPanel icon validation</title><style>body{font:14px system-ui;margin:28px;color:#17202a}table{border-collapse:collapse}th,td{border:1px solid #b8c2cc;padding:6px;text-align:left}.previews img{width:180px;height:180px;object-fit:contain;background:#ddd;margin:4px}section{border-top:1px solid #ccd;padding-top:12px}.native-row{display:flex;align-items:flex-start;overflow:auto;padding:16px;gap:24px}.native-row figure{margin:0;flex:none}.native-row figcaption{color:white;background:#30343b;padding:4px}.native-row img{max-width:none;display:block}.comparison-scroll{overflow:auto}.comparison{position:relative;width:1254px;height:1254px;background:black}.comparison img{position:absolute;top:0;left:0;max-width:none;background:black}.split{z-index:1}</style>"
        "<h1>IOPanel icon reconstruction validation</h1><p>SVG render comparison at 1254 × 1254 pixels. Reference copies are byte-for-byte copies of the supplied PNG sources.</p>"
        "<p>These vectors are below the aspirational 0.995 SSIM target. Remaining source texture, edge ringing, gradient and coverage differences are documented in FINAL_REPORT.md. Technical ICO validation does not imply artwork approval or taskbar validation.</p>"
        + "\n".join(rows),
        encoding="utf-8",
    )


def build_icon(name: str, source: Path, svg: Path, png_root: Path, windows: Path, validation: Path) -> dict[str, Any]:
    info = svg_checks(svg)
    expected_hashes = {
        "optical_burst": "c0d0e0509c5246ac7eb3f4ab72005d6bca648787762a4e82f7632af22a7dc136",
        "prism_spectrum": "ac953fa5170d99bac3f264b764a93dfbe98943f787b968cf95d3d5ea041bd25c",
    }
    if sha256(source) != expected_hashes[name]:
        raise ValueError(f"Authoritative source hash mismatch: {source}")
    reference = Image.open(source).convert("RGBA")
    if reference.size != (NATIVE_SIZE, NATIVE_SIZE):
        raise ValueError(f"{source} is {reference.size}; expected {NATIVE_SIZE}x{NATIVE_SIZE}")
    rendered = render(svg, NATIVE_SIZE, supersample=4)
    rendered_direct = render(svg, NATIVE_SIZE, supersample=1)
    icon_dir = png_root / name
    icon_dir.mkdir(parents=True, exist_ok=True)
    exports: dict[int, Image.Image] = {}
    ico_payloads: dict[int, bytes] = {}
    for size in SIZES:
        image = render(svg, size, supersample=4)
        exports[size] = image
        target = icon_dir / f"{size}.png"
        save_png(image, target)
        if size in ICO_SIZES:
            ico_payloads[size] = target.read_bytes()
    ico = windows / f"{name}.ico"
    write_ico(ico_payloads, ico)
    entries = parse_ico(ico)
    if tuple(entry["size"] for entry in entries) != ICO_SIZES or any(
        entry["bits_per_pixel"] != 32 or not entry["png_signature"] for entry in entries
    ):
        raise ValueError(f"ICO intended frame validation failed: {ico}")
    blob = ico.read_bytes()
    for idx, size in enumerate(ICO_SIZES):
        _, _, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", blob, 6 + idx * 16)
        if blob[offset : offset + length] != ico_payloads[size]:
            raise ValueError(f"ICO payload differs from exported PNG: {ico} {size}")
    for size, image in exports.items():
        if image.size != (size, size) or image.mode != "RGBA":
            raise ValueError(f"bad PNG export {name} {size}")
        alpha = np.array(image.getchannel("A"))
        if alpha[[0, 0, -1, -1], [0, -1, 0, -1]].max() != 0 or alpha[size // 2, size // 2] != 255:
            raise ValueError(f"PNG alpha validation failed: {name} {size}")
    # Confirm transparent exterior and opaque interior from the actual render.
    rgba = np.asarray(rendered)
    corners = rgba[[0, 0, -1, -1], [0, -1, 0, -1], 3]
    center_alpha = int(rgba[NATIVE_SIZE // 2, NATIVE_SIZE // 2, 3])
    if corners.max() != 0 or center_alpha != 255:
        raise ValueError(f"{name} alpha boundary check failed: corners={corners.tolist()}, center={center_alpha}")
    # The central square lies wholly inside both observed squircles. No rays,
    # gradients, masks, or background ringing may create transparency there.
    interior_alpha = rgba[300:954, 300:954, 3]
    if interior_alpha.min() != 255:
        raise ValueError(f"{name} has unexpected internal transparency")
    # Pad with exterior pixels so canvas-edge tangencies are treated as boundaries.
    deep_interior = distance_transform_edt(np.pad(rgba[:, :, 3] >= 128, 1))[1:-1, 1:-1] > 3
    internal_partial_alpha = int(np.count_nonzero(rgba[:, :, 3][deep_interior] != 255))
    if internal_partial_alpha:
        raise ValueError(f"{name} contains partially transparent pixels inside its boundary")
    val = validation / name
    val.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, val / "original_reference_copy.png")
    save_png(rendered, val / "rendered_1254.png")
    diff = diff_images(reference, rendered, val)
    contact_sheet(name, exports, val)
    # A deterministic repeat render guards against nondeterministic renderer output.
    repeat = render(svg, NATIVE_SIZE, supersample=4)
    deterministic = (
        sha256(val / "rendered_1254.png")
        == hashlib.sha256(__import__("io").BytesIO(_image_bytes(repeat)).getvalue()).hexdigest()
    )
    if not deterministic:
        raise ValueError(f"Nondeterministic native render: {name}")
    result = {
        "icon": name,
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        "svg": str(svg.relative_to(ROOT)),
        "svg_sha256": sha256(svg),
        "svg_checks": info,
        "native_size": NATIVE_SIZE,
        "exports": list(SIZES),
        "ico": {"path": str(ico.relative_to(ROOT)), "entries": parse_ico(ico)},
        "alpha_validation": {
            "corner_alpha": [int(x) for x in corners],
            "center_alpha": center_alpha,
            "central_square_min_alpha": int(interior_alpha.min()),
            "partial_alpha_interior_count": internal_partial_alpha,
        },
        "rendering": {
            "renderer": "resvg-py",
            "primary": "4x raster render followed by Pillow BOX area integration",
            "direct_native_metric_is_supplemental": True,
        },
        "deterministic_repeat_render": deterministic,
        "metrics": metrics(reference, rendered),
        "direct_native_metrics_supplemental": metrics(reference, rendered_direct),
        "visuals": diff,
    }
    (val / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    return result


def _image_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    out = BytesIO()
    image.save(out, format="PNG", optimize=False, compress_level=9)
    return out.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="replace generated icon directories")
    parser.add_argument(
        "--icons",
        nargs="+",
        choices=("optical_burst", "prism_spectrum"),
        default=("optical_burst", "prism_spectrum"),
        help="build only named icon(s); useful for iterative reconstruction",
    )
    args = parser.parse_args()
    png_root, windows, validation = ICONS / "png", ICONS / "windows", ICONS / "validation"
    if args.clean:
        # Deliberately only remove known generated roots below assets/icons.
        for folder in (png_root, windows, validation):
            if folder.resolve().parent != ICONS.resolve():
                raise ValueError(f"Refusing to clean outside the known icon roots: {folder}")
            if folder.exists():
                shutil.rmtree(folder)
    results = {}
    for name in args.icons:
        print(f"Building {name}...", flush=True)
        results[name] = build_icon(
            name,
            ICONS / "source" / f"{name}_reference.png",
            ICONS / "svg" / f"{name}.svg",
            png_root,
            windows,
            validation,
        )
        print(f"Built {name}.", flush=True)
    (validation / "metrics.json").write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")
    html_report(results, validation / "report.html")
    print(
        json.dumps(
            {name: {"ssim": data["metrics"]["ssim"], "mae": data["metrics"]["mae"]} for name, data in results.items()},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
