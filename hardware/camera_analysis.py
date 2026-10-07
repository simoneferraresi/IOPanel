"""Numerical analysis helpers for offline and operator-supervised camera tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FrameTimingMetrics:
    count: int
    measured_fps: float | None
    mean_interval_ms: float | None
    std_interval_ms: float | None
    median_interval_ms: float | None
    p95_interval_ms: float | None
    p99_interval_ms: float | None
    max_interval_ms: float | None
    frame_id_gaps: int | None


def frame_timing_metrics(timestamps_s: Iterable[float], frame_ids: Iterable[int] | None = None) -> FrameTimingMetrics:
    timestamps = np.asarray(tuple(timestamps_s), dtype=np.float64)
    if timestamps.size < 2:
        return FrameTimingMetrics(int(timestamps.size), None, None, None, None, None, None, None, None)
    intervals = np.diff(timestamps) * 1000.0
    if np.any(~np.isfinite(intervals)) or np.any(intervals < 0):
        raise ValueError("timestamps must be finite and nondecreasing")
    gaps = None
    if frame_ids is not None:
        ids = np.asarray(tuple(frame_ids), dtype=np.int64)
        if ids.size != timestamps.size:
            raise ValueError("frame_ids and timestamps must have equal lengths")
        gaps = int(np.maximum(np.diff(ids) - 1, 0).sum())
    mean = float(intervals.mean())
    return FrameTimingMetrics(
        count=int(timestamps.size),
        measured_fps=(1000.0 / mean if mean > 0 else None),
        mean_interval_ms=mean,
        std_interval_ms=float(intervals.std()),
        median_interval_ms=float(np.median(intervals)),
        p95_interval_ms=float(np.percentile(intervals, 95)),
        p99_interval_ms=float(np.percentile(intervals, 99)),
        max_interval_ms=float(intervals.max()),
        frame_id_gaps=gaps,
    )


def _region(frame_shape: tuple[int, ...], region: tuple[int, int, int, int] | None) -> tuple[slice, slice]:
    if len(frame_shape) < 2:
        raise ValueError("frames must have at least two dimensions")
    height, width = frame_shape[:2]
    if region is None:
        return slice(0, height), slice(0, width)
    x, y, w, h = region
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError(f"ROI {region} is outside frame bounds {width}x{height}")
    return slice(y, y + h), slice(x, x + w)


def analyze_frames(
    frames: Iterable[np.ndarray],
    *,
    signal_roi: tuple[int, int, int, int] | None = None,
    background_roi: tuple[int, int, int, int] | None = None,
    saturation_threshold: float = 250.0,
    near_zero_threshold: float = 2.0,
    centroid: bool = False,
) -> dict[str, float | int | None]:
    """Compute temporal intensity/noise, clipping, contrast, and optional centroid metrics."""
    # Retain source frame dtype to keep a 200–500 frame Mono8 run bounded in
    # memory; NumPy performs reductions in floating point without a full-size
    # float64 copy of the entire acquisition stack.
    stack = np.asarray(tuple(np.asarray(frame) for frame in frames))
    if stack.ndim < 3 or stack.shape[0] == 0:
        raise ValueError("at least one non-empty 2D frame is required")
    if np.any(~np.isfinite(stack)):
        raise ValueError("frames must contain only finite values")
    if not np.isfinite(saturation_threshold) or not np.isfinite(near_zero_threshold):
        raise ValueError("intensity thresholds must be finite")
    sy, sx = _region(stack.shape[1:], signal_roi)
    signal = stack[:, sy, sx]
    mean_img = float(signal.mean())
    sigma_img = float(signal.std(axis=0, ddof=0).mean())
    temporal_snr = mean_img / sigma_img if sigma_img > 0 else None
    result: dict[str, float | int | None] = {
        "frame_count": int(stack.shape[0]),
        "mean_intensity": mean_img,
        "median_intensity": float(np.median(signal)),
        "temporal_sigma": sigma_img,
        "coefficient_of_variation": sigma_img / abs(mean_img) if mean_img else None,
        "temporal_snr": temporal_snr,
        "near_zero_fraction": float(np.mean(signal <= near_zero_threshold)),
        "near_saturated_fraction": float(np.mean(signal >= saturation_threshold)),
        "full_frame_temporal_sigma": float(stack.std(axis=0, ddof=0).mean()),
    }
    if background_roi is not None:
        by, bx = _region(stack.shape[1:], background_roi)
        background = stack[:, by, bx]
        bg_mean = float(background.mean())
        bg_sigma = float(background.std(axis=0, ddof=0).mean())
        result["background_mean"] = bg_mean
        result["background_temporal_sigma"] = bg_sigma
        result["signal_background_contrast"] = mean_img - bg_mean
        result["signal_background_ratio"] = mean_img / bg_mean if bg_mean != 0 else None
    if centroid:
        centroids = []
        integrated = []
        yy, xx = np.mgrid[sy, sx]
        for frame in stack:
            view = frame[sy, sx]
            weights = np.maximum(view - float(view.min()), 0.0)
            total = float(weights.sum())
            integrated.append(float(view.sum()))
            centroids.append(
                (None, None)
                if total == 0
                else (float((xx * weights).sum() / total), float((yy * weights).sum() / total))
            )
        valid = np.asarray([c for c in centroids if c[0] is not None], dtype=np.float64)
        result["centroid_mean_x"] = float(valid[:, 0].mean()) if valid.size else None
        result["centroid_mean_y"] = float(valid[:, 1].mean()) if valid.size else None
        result["centroid_sigma_x"] = float(valid[:, 0].std()) if valid.size else None
        result["centroid_sigma_y"] = float(valid[:, 1].std()) if valid.size else None
        result["integrated_signal_mean"] = float(np.mean(integrated))
        result["integrated_signal_sigma"] = float(np.std(integrated))
    return result
