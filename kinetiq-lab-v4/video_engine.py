"""Video decoding, tracking, OCR and physics analysis for Kinetiq Lab.

PyAV/FFmpeg is preferred for presentation timestamps (PTS); OpenCV remains a
fallback. Object coordinates are measurements, never generated/predicted rows.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Iterator, Optional

import cv2
import numpy as np
import pytesseract
from scipy import optimize, signal

try:  # PyAV exposes frame presentation timestamps; OpenCV is fallback.
    import av  # type: ignore
except Exception:  # pragma: no cover - depends on user environment
    av = None


def _safe_fps(value, fallback=30.0):
    try:
        v = float(value)
        return v if math.isfinite(v) and 0.1 <= v <= 1000 else fallback
    except Exception:
        return fallback


def video_meta(path):
    path = str(path)
    if av is not None:
        try:
            with av.open(path) as container:
                streams = [s for s in container.streams if s.type == "video"]
                if streams:
                    s = streams[0]
                    rate = _safe_fps(float(s.average_rate) if s.average_rate else 30.0)
                    frames = int(s.frames or 0)
                    dur = float(s.duration * s.time_base) if s.duration and s.time_base else 0.0
                    if not dur and container.duration:
                        dur = float(container.duration / av.time_base)
                    return {
                        "fps": rate,
                        "frames": frames,
                        "duration": max(0.0, dur),
                        "width": int(s.codec_context.width or 0),
                        "height": int(s.codec_context.height or 0),
                        "decoder": "PyAV / FFmpeg",
                        "timestamp_source": "frame presentation timestamps (PTS) when available",
                    }
        except Exception:
            pass

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        raise ValueError("The video decoder could not open this file. Try exporting it as H.264 MP4.")
    fps = _safe_fps(cap.get(cv2.CAP_PROP_FPS))
    frames = int(max(0, cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    width = int(max(0, cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
    height = int(max(0, cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return {
        "fps": fps,
        "frames": frames,
        "duration": frames / fps if frames else 0.0,
        "width": width,
        "height": height,
        "decoder": "OpenCV fallback",
        "timestamp_source": "OpenCV milliseconds with nominal-FPS fallback",
    }


def iter_video_frames(path, start_time=0.0) -> Iterator[tuple[int, float, np.ndarray]]:
    """Yield (approximate global frame number, presentation time, BGR image).

    PyAV seeks once to a preceding keyframe and then decodes sequentially.
    Decode errors after frames are produced are surfaced rather than silently
    restarting with a different decoder and duplicating the timeline.
    """
    start_time = max(0.0, float(start_time or 0.0))
    container = None
    stream = None
    if av is not None:
        try:
            container = av.open(str(path))
            streams = [s for s in container.streams if s.type == "video"]
            if streams:
                stream = streams[0]
            else:
                container.close()
                container = None
        except Exception:
            if container is not None:
                try: container.close()
                except Exception: pass
            container = None

    if container is not None and stream is not None:
        with container:
            fps = _safe_fps(float(stream.average_rate) if stream.average_rate else 30.0)
            if start_time > 0 and stream.time_base:
                try:
                    offset = int(start_time / float(stream.time_base))
                    container.seek(offset, stream=stream, backward=True, any_frame=False)
                except Exception:
                    container.seek(0, stream=stream, backward=True, any_frame=False)
            decoded = iter(container.decode(stream))
            previous = None
            chosen = False
            last_t = -1.0
            local_index = 0
            for frame in decoded:
                frame_t = None
                if frame.pts is not None and frame.time_base is not None:
                    frame_t = float(frame.pts * frame.time_base)
                if frame_t is None or not math.isfinite(frame_t):
                    frame_t = (start_time + local_index / fps) if start_time else local_index / fps
                if frame_t <= last_t:
                    frame_t = max(last_t + 1.0 / fps, (start_time + local_index / fps) if start_time else local_index / fps)
                last_t = frame_t
                bgr = frame.to_ndarray(format="bgr24")
                item = (int(round(frame_t * fps)), frame_t, bgr)
                local_index += 1
                if not chosen:
                    if frame_t < start_time:
                        previous = item
                        continue
                    if previous is not None and start_time - previous[1] < frame_t - start_time:
                        yield previous
                        yield item
                    else:
                        yield item
                    chosen = True
                else:
                    yield item
        return

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise ValueError("Could not decode video frames. Try an H.264 MP4 export.")
    fps = _safe_fps(cap.get(cv2.CAP_PROP_FPS))
    idx = max(0, int(round(start_time * fps)))
    if start_time > 0:
        # Use a single initial frame-position seek, then decode sequentially.
        # It aligns better with CFR browser playback than repeated time seeks.
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    last_t = -1.0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        t_ms = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        # Some backends reset POS_MSEC to zero after seeking. Do not accept that
        # zero as the timestamp of a later frame; fall back to the nominal index.
        has_usable_ms = (math.isfinite(t_ms) and t_ms >= 0 and t_ms > last_t + 1e-7
                         and (t_ms > 0 or idx == 0))
        t = float(t_ms) if has_usable_ms else float(idx / fps)
        if t <= last_t:
            t = max(float(idx / fps), last_t + 1.0 / fps)
        last_t = t
        yield idx, float(t), bgr
        idx += 1
    cap.release()

def frame_at_time(path, seconds=0.0):
    for idx, t, frame in iter_video_frames(path, max(0.0, seconds)):
        return idx, t, frame
    raise ValueError("No decodable frame was found at that time.")


def sample_times(meta, count=24):
    dur = max(0.0, float(meta.get("duration") or 0))
    n = max(1, int(count))
    return np.unique(np.linspace(0.0, dur, n).round(5)).tolist()


def _preprocess(gray, clahe):
    # CLAHE reduces exposure changes without heavy denoising; mild blur suppresses codec speckle.
    return clahe.apply(cv2.GaussianBlur(gray, (3, 3), 0))


def _clamp_roi(roi, width, height):
    if len(roi) != 4:
        raise ValueError("Select a rectangular target in the video first.")
    x, y, w, h = [int(round(float(v))) for v in roi]
    x = max(0, min(x, width - 2))
    y = max(0, min(y, height - 2))
    w = max(6, min(w, width - x))
    h = max(6, min(h, height - y))
    if w < 6 or h < 6:
        raise ValueError("The target box is too small. Select a box at least 6×6 pixels.")
    return x, y, w, h


def _foreground_mask(frame_bgr, roi):
    """Estimate a target-only mask from contrast to the selection's border.

    Useful when the user draws a loose box around a high-contrast ball/object:
    static background pixels should not dominate the template match.
    Returns (mask or None, centroid x/y in the ROI coordinate system).
    """
    x, y, w, h = roi
    patch = frame_bgr[y:y+h, x:x+w]
    if patch.size == 0 or min(w, h) < 6:
        return None, (w / 2.0, h / 2.0)
    lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    border_width = max(2, min(w, h) // 8)
    border = np.concatenate([
        lab[:border_width, :, :].reshape(-1, 3),
        lab[-border_width:, :, :].reshape(-1, 3),
        lab[:, :border_width, :].reshape(-1, 3),
        lab[:, -border_width:, :].reshape(-1, 3),
    ], axis=0)
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(lab - bg[None, None, :], axis=2)
    border_dist = np.linalg.norm(border - bg[None, :], axis=1)
    threshold = max(13.0, float(np.percentile(border_dist, 95)) * 2.4 + 3.0)
    candidate = (dist > threshold).astype(np.uint8) * 255
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(candidate, 8)
    roi_area = float(w * h)
    center_ref = np.array([w / 2.0, h / 2.0])
    choices = []
    for lab_id in range(1, count):
        area = int(stats[lab_id, cv2.CC_STAT_AREA])
        if area < max(8, int(roi_area * 0.012)) or area > roi_area * 0.88:
            continue
        c = centroids[lab_id]
        distance = float(np.linalg.norm(c - center_ref))
        # Prefer substantial components near the centre of the user's box.
        quality = area / (1.0 + distance / max(1.0, min(w, h) * 0.35))
        choices.append((quality, lab_id))
    if not choices:
        return None, (w / 2.0, h / 2.0)
    _, selected = max(choices)
    mask = (labels == selected).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    moments = cv2.moments(mask, binaryImage=True)
    if moments["m00"] <= 0 or float(np.mean(mask > 0)) < 0.025 or float(np.mean(mask > 0)) > 0.85:
        return None, (w / 2.0, h / 2.0)
    centroid = (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))
    return mask, centroid


def _features(gray, roi, target_mask=None):
    x, y, w, h = roi
    patch = gray[y:y+h, x:x+w]
    if patch.size == 0:
        return None
    local_mask = np.full((h, w), 255, dtype=np.uint8)
    if target_mask is not None and target_mask.size:
        local_mask = target_mask
        if local_mask.shape[:2] != (h, w):
            local_mask = cv2.resize(local_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    pts = cv2.goodFeaturesToTrack(patch, maxCorners=80, qualityLevel=0.008,
                                  minDistance=3, mask=local_mask, blockSize=5,
                                  useHarrisDetector=False)
    if pts is not None:
        pts[:, 0, 0] += x
        pts[:, 0, 1] += y
    return pts

def _flow_prediction(prev_gray, gray, points, center, roi):
    """Estimate translation from forward/back-checked LK points in a local crop."""
    if points is None or len(points) < 4:
        return None, None, 0
    bx, by, bw, bh = roi
    pad = max(2 * max(bw, bh), 36)
    x0 = max(0, int(min(bx, center[0]) - pad))
    y0 = max(0, int(min(by, center[1]) - pad))
    x1 = min(gray.shape[1], int(max(bx + bw, center[0]) + pad))
    y1 = min(gray.shape[0], int(max(by + bh, center[1]) + pad))
    prev_crop = prev_gray[y0:y1, x0:x1]
    curr_crop = gray[y0:y1, x0:x1]
    if min(prev_crop.shape[:2], default=0) < 8 or prev_crop.shape != curr_crop.shape:
        return None, None, 0
    original_abs = points.reshape(-1, 2)
    original = (original_abs - np.array([x0, y0], dtype=np.float32)).reshape(-1, 1, 2)
    p1, st1, _ = cv2.calcOpticalFlowPyrLK(
        prev_crop, curr_crop, original, None, winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.02))
    if p1 is None or st1 is None:
        return None, None, 0
    p0_back, st2, _ = cv2.calcOpticalFlowPyrLK(
        curr_crop, prev_crop, p1, None, winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.02))
    if p0_back is None or st2 is None:
        return None, None, 0
    forward_local = p1.reshape(-1, 2)
    original_local = original.reshape(-1, 2)
    backward = p0_back.reshape(-1, 2)
    good = (st1.reshape(-1) > 0) & (st2.reshape(-1) > 0)
    good &= np.linalg.norm(original_local - backward, axis=1) < 1.75
    good &= np.isfinite(forward_local).all(axis=1)
    if good.sum() < 4:
        pts_abs = (forward_local[good] + np.array([x0, y0], dtype=np.float32)).reshape(-1, 1, 2) if good.any() else None
        return None, pts_abs, int(good.sum())
    delta = forward_local[good] - original_local[good]
    median = np.median(delta, axis=0)
    dist = np.linalg.norm(delta - median, axis=1)
    inliers = dist <= max(2.0, 3.5 * (np.median(dist) + 1e-6))
    if inliers.sum() < 4:
        pts_abs = (forward_local[good] + np.array([x0, y0], dtype=np.float32)).reshape(-1, 1, 2)
        return None, pts_abs, int(inliers.sum())
    shift = np.median(delta[inliers], axis=0)
    robust_spread = float(np.median(np.linalg.norm(delta[inliers] - shift, axis=1)))
    pred = (float(center[0] + shift[0]), float(center[1] + shift[1]))
    next_abs = (forward_local[good] + np.array([x0, y0], dtype=np.float32)).reshape(-1, 1, 2)
    return (pred, robust_spread), next_abs, int(inliers.sum())

def _match_template(search_img, template, target_mask=None, multi_scale=False):
    """Multi-scale normalized template matching; optionally score only target pixels."""
    th, tw = template.shape[:2]
    std = float(np.std(template))
    masked = target_mask is not None and target_mask.size and float(np.mean(target_mask > 0)) < 0.9
    method = cv2.TM_SQDIFF_NORMED if masked else (cv2.TM_CCOEFF_NORMED if std >= 3.0 else cv2.TM_CCORR_NORMED)
    best = None
    scales = (0.92, 1.0, 1.08) if multi_scale else (1.0,)
    for scale in scales:
        ww, hh = max(6, int(round(tw * scale))), max(6, int(round(th * scale)))
        tpl = template if (ww == tw and hh == th) else cv2.resize(template, (ww, hh), interpolation=cv2.INTER_LINEAR)
        mask = None
        if masked:
            mask = target_mask if (ww == tw and hh == th) else cv2.resize(target_mask, (ww, hh), interpolation=cv2.INTER_NEAREST)
            if mask.dtype != np.uint8:
                mask = mask.astype(np.uint8)
            # Masked matching in OpenCV supports TM_CCORR_NORMED and TM_SQDIFF.
            mask = np.where(mask > 0, 255, 0).astype(np.uint8)
        if search_img.shape[0] < hh or search_img.shape[1] < ww:
            continue
        try:
            res = cv2.matchTemplate(search_img, tpl, method, mask=mask) if mask is not None else cv2.matchTemplate(search_img, tpl, method)
        except cv2.error:
            # Some builds reject masked CCORR on a given image type; use the
            # unmasked normalized match as a graceful fallback.
            res = cv2.matchTemplate(search_img, tpl, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        if method == cv2.TM_SQDIFF_NORMED:
            min_value, _, loc, _ = cv2.minMaxLoc(res)
            score = 1.0 - float(min_value)
        else:
            _, score, _, loc = cv2.minMaxLoc(res)
        tmp = res.copy()
        lx, ly = loc
        x0, x1 = max(0, lx - max(2, ww // 2)), min(tmp.shape[1], lx + max(2, ww // 2) + 1)
        y0, y1 = max(0, ly - max(2, hh // 2)), min(tmp.shape[0], ly + max(2, hh // 2) + 1)
        tmp[y0:y1, x0:x1] = (1e6 if method == cv2.TM_SQDIFF_NORMED else -1.0)
        if tmp.size:
            if method == cv2.TM_SQDIFF_NORMED:
                second = 1.0 - float(cv2.minMaxLoc(tmp)[0])
            else:
                second = float(cv2.minMaxLoc(tmp)[1])
        else:
            second = -1.0
        margin = float(score - second)
        candidate = (float(score), margin, lx, ly, ww, hh, scale)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best

def track_object(path, roi, start_time=0.0, max_frames=6000):
    """Track the selected object with target-only masked template + checked LK flow.

    Coordinates are the centroid of the object mask when contrast segmentation
    is reliable, otherwise the centre of the selected template. Failed frames
    are missing—not synthetic predictions.
    """
    iterator = iter_video_frames(path, start_time)
    first = next(iterator, None)
    if first is None:
        raise ValueError("The selected time has no decodable frame.")
    first_idx, first_t, first_bgr = first
    h, w = first_bgr.shape[:2]
    x, y, rw, rh = _clamp_roi(roi, w, h)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    prev_gray_raw = cv2.cvtColor(first_bgr, cv2.COLOR_BGR2GRAY)
    prev_gray = _preprocess(prev_gray_raw, clahe)
    template = prev_gray[y:y + rh, x:x + rw].copy()
    if template.size == 0 or min(template.shape) < 6:
        raise ValueError("The target box is outside the decoded frame or too small.")
    target_mask, centroid = _foreground_mask(first_bgr, (x, y, rw, rh))
    if target_mask is not None:
        # Trim unused selection-box background around the segmented target; a
        # smaller masked template is faster and less tempted by static scenery.
        ys, xs = np.where(target_mask > 0)
        pad = 3
        crop_x = max(0, int(xs.min()) - pad); crop_y = max(0, int(ys.min()) - pad)
        crop_x2 = min(rw, int(xs.max()) + pad + 1); crop_y2 = min(rh, int(ys.max()) + pad + 1)
        if crop_x2 - crop_x >= 6 and crop_y2 - crop_y >= 6:
            template = template[crop_y:crop_y2, crop_x:crop_x2].copy()
            target_mask = target_mask[crop_y:crop_y2, crop_x:crop_x2].copy()
            x += crop_x; y += crop_y
            centroid = (centroid[0] - crop_x, centroid[1] - crop_y)
            rw, rh = template.shape[1], template.shape[0]
        else:
            target_mask = None
    if target_mask is None:
        centroid = (rw / 2.0, rh / 2.0)
    offset = (centroid[0] - rw / 2.0, centroid[1] - rh / 2.0)
    center = (float(x + centroid[0]), float(y + centroid[1]))
    box_origin = (x, y)
    points = _features(prev_gray_raw, (x, y, rw, rh), target_mask)
    observations = [{
        "frame": int(first_idx), "t": float(first_t), "x": center[0], "y": center[1],
        "score": 1.0, "peak_margin": 1.0, "method": "initial-selection",
        "box_w": float(rw), "box_h": float(rh),
    }]
    recent = [(float(first_t), center[0], center[1])]
    prev_t = float(first_t)
    prev_raw = prev_gray_raw
    failed = 0
    attempted = 0
    truncated = False
    fps = video_meta(path)["fps"]

    for frame_idx, t, bgr in iterator:
        if len(observations) >= max_frames:
            truncated = True
            break
        attempted += 1
        if t <= prev_t:
            continue
        gray_raw = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = _preprocess(gray_raw, clahe)
        dt = max(1e-5, float(t - prev_t))

        recent_arr = np.asarray(recent[-5:], dtype=float)
        if len(recent_arr) >= 2:
            tt = recent_arr[:, 0] - recent_arr[-1, 0]
            A = np.c_[np.ones(len(tt)), tt]
            cx = np.linalg.lstsq(A, recent_arr[:, 1], rcond=None)[0]
            cy = np.linalg.lstsq(A, recent_arr[:, 2], rcond=None)[0]
            pred_vel = (float(cx[0] + cx[1] * dt), float(cy[0] + cy[1] * dt))
            vx, vy = float(cx[1]), float(cy[1])
        else:
            pred_vel = center
            vx = vy = 0.0

        flow, flow_points, flow_count = _flow_prediction(prev_raw, gray_raw, points, center, (box_origin[0], box_origin[1], rw, rh))
        prediction = pred_vel
        flow_spread = None
        if flow is not None:
            flow_center, flow_spread = flow
            if flow_count >= 5 and flow_spread <= 2.8:
                prediction = (0.76 * flow_center[0] + 0.24 * pred_vel[0],
                              0.76 * flow_center[1] + 0.24 * pred_vel[1])

        box_size = max(rw, rh)
        displacement = math.hypot(vx, vy) * dt
        radius = max(1.45 * box_size, 1.8 * displacement + 10.0, 20.0)
        radius *= min(3.5, 1.0 + 0.9 * failed)
        # The selected template origin is offset from the object centroid when
        # the user-drawn rectangle contains extra background. Account for it.
        scale_x, scale_y = 1.0, 1.0
        pred_box_x = prediction[0] - rw / 2.0 - offset[0]
        pred_box_y = prediction[1] - rh / 2.0 - offset[1]
        sx, sy = max(0, int(round(pred_box_x - radius))), max(0, int(round(pred_box_y - radius)))
        ex, ey = min(w, int(round(pred_box_x + rw + radius))), min(h, int(round(pred_box_y + rh + radius)))
        search_img = gray[sy:ey, sx:ex]
        candidate = _match_template(search_img, template, target_mask, multi_scale=(attempted % 8 == 0)) if search_img.size else None
        # Re-run the three-scale search only if the quick single-scale result is
        # weak/ambiguous. Stable videos therefore need only one match per frame.
        if candidate is not None and search_img.size and (candidate[0] < 0.78 or candidate[1] < 0.015) and attempted % 8 != 0:
            wide_candidate = _match_template(search_img, template, target_mask, multi_scale=True)
            if wide_candidate is not None and wide_candidate[0] > candidate[0]:
                candidate = wide_candidate
        accepted = False
        margin = 0.0
        if candidate is not None:
            score, margin, lx, ly, new_w, new_h, scale_factor = candidate
            threshold = 0.43 if (target_mask is not None or float(np.std(template)) >= 3.0) else 0.52
            if score >= threshold and margin >= -0.01:
                box_x, box_y = sx + lx, sy + ly
                # Re-estimate the current frame's foreground centroid when the
                # object can be separated from the local background. This is
                # less biased by object rotation/scale changes than reusing a
                # fixed centroid measured from the first frame.
                frame_mask, frame_centroid = _foreground_mask(
                    bgr, (box_x, box_y, new_w, new_h)
                ) if target_mask is not None else (None, (new_w / 2.0, new_h / 2.0))
                if frame_mask is not None:
                    cx_obj = box_x + frame_centroid[0]
                    cy_obj = box_y + frame_centroid[1]
                else:
                    cx_obj = box_x + centroid[0] * scale_factor
                    cy_obj = box_y + centroid[1] * scale_factor
                new_center = (cx_obj, cy_obj)
                new_box_origin = (int(box_x), int(box_y))
                confidence = float(score)
                method = "masked-template+optical-flow" if flow_count >= 4 else "masked-multi-scale-template"
                accepted = True
            elif flow is not None and flow_count >= 6 and flow_spread is not None and flow_spread <= 1.8:
                new_center = flow[0]
                new_w, new_h = rw, rh
                new_box_origin = (int(round(new_center[0] - centroid[0])), int(round(new_center[1] - centroid[1])))
                confidence = max(0.35, min(0.60, 0.35 + flow_count / 100.0))
                method = "verified-optical-flow"
                accepted = True
        elif flow is not None and flow_count >= 6 and flow_spread is not None and flow_spread <= 1.8:
            new_center = flow[0]
            new_w, new_h = rw, rh
            new_box_origin = (int(round(new_center[0] - centroid[0])), int(round(new_center[1] - centroid[1])))
            confidence = max(0.35, min(0.60, 0.35 + flow_count / 100.0))
            method = "verified-optical-flow"
            accepted = True

        if accepted:
            jump = math.hypot(new_center[0] - center[0], new_center[1] - center[1])
            prediction_error = math.hypot(new_center[0] - prediction[0], new_center[1] - prediction[1])
            flow_agrees = (flow is not None and flow_count >= 5 and flow_spread is not None and
                           flow_spread <= 2.8 and
                           math.hypot(new_center[0] - flow[0][0], new_center[1] - flow[0][1]) <= max(8.0, 0.45 * box_size))
            max_jump = max(0.85 * box_size, 3.0 * displacement + 8.0)
            max_prediction_error = max(0.60 * box_size, 2.2 * displacement + 8.0)
            if (jump > max_jump or prediction_error > max_prediction_error) and not flow_agrees:
                accepted = False

        if accepted:
            center = (float(new_center[0]), float(new_center[1]))
            box_origin = new_box_origin
            if candidate is not None and method != "verified-optical-flow":
                sf = float(candidate[6])
                offset = (centroid[0] * sf - new_w / 2.0, centroid[1] * sf - new_h / 2.0)
            rw, rh = int(new_w), int(new_h)
            bx, by = box_origin
            bx, by = max(0, min(bx, w - rw)), max(0, min(by, h - rh))
            box_origin = (bx, by)
            observations.append({
                "frame": int(frame_idx), "t": float(t), "x": center[0], "y": center[1],
                "score": float(confidence), "peak_margin": float(margin), "method": method,
                "box_w": float(rw), "box_h": float(rh),
            })
            recent.append((float(t), center[0], center[1]))
            recent = recent[-8:]
            failed = 0
            if candidate is not None and confidence >= 0.82 and len(observations) % 8 == 0:
                bx, by = box_origin
                patch = gray[by:by + rh, bx:bx + rw]
                if patch.size and patch.shape == template.shape:
                    template = cv2.addWeighted(template, 0.98, patch, 0.02, 0)
            if len(observations) % 8 == 0 or flow_points is None or len(flow_points) < 8:
                points = _features(gray_raw, (bx, by, rw, rh), target_mask)
            else:
                valid_points = flow_points.reshape(-1, 2)
                local_mask = target_mask
                if local_mask is not None and local_mask.shape[:2] != (rh, rw):
                    local_mask = cv2.resize(local_mask, (rw, rh), interpolation=cv2.INTER_NEAREST)
                inside = ((valid_points[:, 0] >= bx) & (valid_points[:, 0] < bx + rw) &
                          (valid_points[:, 1] >= by) & (valid_points[:, 1] < by + rh))
                if local_mask is not None and inside.any():
                    subset = valid_points[inside]
                    xx = np.clip(subset[:, 0].astype(int) - bx, 0, rw - 1)
                    yy = np.clip(subset[:, 1].astype(int) - by, 0, rh - 1)
                    inside_idx = np.zeros(len(valid_points), dtype=bool)
                    candidates_idx = np.flatnonzero(inside)
                    inside_idx[candidates_idx] = local_mask[yy, xx] > 0
                    inside = inside_idx
                points = flow_points[inside].reshape(-1, 1, 2) if inside.any() else None
                if points is None or len(points) < 5:
                    points = _features(gray_raw, (bx, by, rw, rh), target_mask)
        else:
            failed += 1
            points = flow_points
            if failed > max(12, int(0.75 * fps)):
                break

        prev_raw = gray_raw
        prev_t = float(t)

    if observations:
        mean_score = float(np.mean([o["score"] for o in observations]))
    else:
        mean_score = 0.0
    tracked = len(observations)
    frames_examined = attempted + 1
    observed_times = np.asarray([o["t"] for o in observations], dtype=float)
    gaps = np.diff(observed_times) if len(observed_times) > 1 else np.asarray([], dtype=float)
    nominal_frame_dt = 1.0 / max(fps, 0.1)
    info = {
        "decoder": video_meta(path).get("decoder", "unknown"),
        "timestamp_source": video_meta(path).get("timestamp_source", "unknown"),
        "selection_time_s": float(start_time or 0.0),
        "actual_start_time_s": float(first_t),
        "frames_examined": int(frames_examined),
        "tracked_points": int(tracked),
        "tracking_coverage_pct": round(100.0 * tracked / max(1, frames_examined), 2),
        "mean_match_score": round(mean_score, 4),
        "low_confidence_points": int(sum(o["score"] < 0.55 for o in observations)),
        "median_observation_interval_s": float(np.median(gaps)) if gaps.size else None,
        "max_observation_gap_s": float(np.max(gaps)) if gaps.size else None,
        "expected_frame_interval_s": nominal_frame_dt,
        "target_mask_used": target_mask is not None,
        "truncated": bool(truncated),
    }
    if tracked < 8:
        raise ValueError("Too few reliable target positions were decoded. Draw a tighter box around the object, improve contrast, or use a shorter clip.")
    return observations, info

def ocr_frame(frame):
    """OCR multiple carefully-preprocessed variants and deduplicate boxes."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # Avoid huge allocations for 4K phone footage while preserving tiny digits.
    scale = 2.0 if max(gray.shape) < 1800 else 1.25
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    sharp = cv2.addWeighted(clahe, 1.5, cv2.GaussianBlur(clahe, (0, 0), 1.2), -0.5, 0)
    blur = cv2.GaussianBlur(sharp, (3, 3), 0)
    otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 7)
    variants = [(sharp, "gray", "--oem 3 --psm 11", "sparse"),
                (otsu, "otsu", "--oem 3 --psm 11", "sparse"),
                (cv2.bitwise_not(otsu), "otsu-inverse", "--oem 3 --psm 11", "sparse"),
                (adaptive, "adaptive", "--oem 3 --psm 6", "block")]
    found = []
    for image, variant_name, config, mode_name in variants:
        data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT, timeout=8)
        for i, raw in enumerate(data.get("text", [])):
            text = raw.strip()
            confidence = fnum(data.get("conf", [])[i], -1)
            if not text or not re.search(r"\d", text) or confidence < 30:
                continue
            x = int(data["left"][i] / scale); y = int(data["top"][i] / scale)
            w = int(data["width"][i] / scale); h = int(data["height"][i] / scale)
            found.append({"text": text, "confidence": round(float(confidence), 1),
                          "box": [x, y, w, h], "preprocess": variant_name, "psm": mode_name})
    # Deduplicate overlapping copies across preprocessing passes, keep the highest confidence.
    found.sort(key=lambda v: v["confidence"], reverse=True)
    out = []
    for item in found:
        x, y, w, h = item["box"]
        duplicate = False
        for old in out:
            ox, oy, ow, oh = old["box"]
            overlap_w = max(0, min(x + w, ox + ow) - max(x, ox))
            overlap_h = max(0, min(y + h, oy + oh) - max(y, oy))
            inter = overlap_w * overlap_h
            denom = max(1, min(w * h, ow * oh))
            if (item["text"].casefold() == old["text"].casefold() and inter / denom > 0.35):
                duplicate = True
                break
        if not duplicate:
            out.append(item)
    return sorted(out, key=lambda a: (a["box"][1], a["box"][0]))


def fnum(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def ocr_video(path, n=24):
    """OCR evenly spaced frames from a single sequential decode pass.

    Decoding each OCR sample with a fresh random seek is slower and can choose
    different keyframe-adjacent frames across containers. Sequential decoding
    uses one consistent timestamp stream and picks the decoded frame nearest
    each requested sample time.
    """
    info = video_meta(path)
    targets = sample_times(info, max(1, min(40, int(n))))
    iterator = iter_video_frames(path, 0.0)
    current = next(iterator, None)
    previous = None
    out = []
    processed_indices = set()
    for target_t in targets:
        while current is not None and current[1] < target_t:
            previous = current
            current = next(iterator, None)
        choices = [item for item in (previous, current) if item is not None]
        if not choices:
            break
        idx, actual_t, frame = min(choices, key=lambda item: abs(item[1] - target_t))
        if idx in processed_indices:
            continue
        processed_indices.add(idx)
        tokens = ocr_frame(frame)
        if tokens:
            out.append({"frame": int(idx), "t": round(float(actual_t), 5), "tokens": tokens})
    return out


def robust_quadratic(t, y, noise_floor, weights=None):
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    t0 = float(t[0])
    tau = t - t0
    A = np.c_[np.ones_like(tau), tau, tau * tau]
    w = np.ones_like(y) if weights is None else np.clip(np.asarray(weights, dtype=float), 0.08, 1.0)
    if w.shape != y.shape:
        raise ValueError("The fit weights must match the observation count.")
    sw = np.sqrt(w)
    initial = np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)[0]
    fscale = max(float(noise_floor), 1e-5)
    fit = optimize.least_squares(lambda c: (A @ c - y) * sw, initial, loss="soft_l1", f_scale=fscale,
                                 max_nfev=3000)
    coef = fit.x
    predicted = A @ coef
    residual = y - predicted
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - np.sum(residual ** 2) / ss_tot) if ss_tot > 1e-15 else 1.0
    med = float(np.median(residual))
    mad = float(np.median(np.abs(residual - med)) * 1.4826)
    inliers = (np.abs(residual - med) <= max(3.5 * mad, 2.5 * fscale)) & (w >= 0.12)
    if inliers.sum() >= 4:
        Ai = A[inliers]
        ri = residual[inliers]
        wi = w[inliers]
        Aw = Ai * np.sqrt(wi)[:, None]
        dof = max(1, int(inliers.sum()) - 3)
        s2 = float(np.sum(wi * ri ** 2) / dof)
        cov = s2 * np.linalg.pinv(Aw.T @ Aw)
        coef_uncertainty = float(math.sqrt(max(0.0, cov[2, 2])))
    else:
        coef_uncertainty = float("nan")
    return {"coef": coef, "predicted": predicted, "residual": residual,
            "rmse": rmse, "r2": r2, "inlier_fraction": float(np.mean(inliers)),
            "coefficient_uncertainty": coef_uncertainty, "t0": t0}


def freefall(observations, px_per_m):
    if len(observations) < 8:
        raise ValueError("Free-fall fitting needs at least 8 tracked frames.")
    if not math.isfinite(px_per_m) or px_per_m <= 0:
        raise ValueError("Pixels per metre must be positive. Calibrate against a known length in the same plane as the motion.")
    obs = sorted(observations, key=lambda o: o["t"])
    t = np.asarray([o["t"] for o in obs], dtype=float)
    # Screen y grows downwards; positive g therefore represents downward acceleration.
    y = np.asarray([o["y"] for o in obs], dtype=float) / px_per_m
    # Down-weight optical-flow fallback points and other weak visual matches;
    # robust loss separately limits the influence of large residual outliers.
    match_scores = np.asarray([fnum(o.get("score"), 0.7) or 0.7 for o in obs], dtype=float)
    weights = np.clip((match_scores - 0.25) / 0.65, 0.08, 1.0)
    fit = robust_quadratic(t, y, noise_floor=2.0 / px_per_m, weights=weights)
    coef = fit["coef"]
    g = float(2.0 * coef[2])
    g_u = float(2.0 * fit["coefficient_uncertainty"]) if math.isfinite(fit["coefficient_uncertainty"]) else None
    result = {
        "model": "freefall", "g": g, "g_fit_uncertainty": g_u,
        "uncertainty_scope": "fit-only estimate; excludes calibration, frame-timing, parallax and tracking systematic errors",
        "rmse": fit["rmse"], "r2": fit["r2"], "inlier_fraction": fit["inlier_fraction"],
        "t": t.tolist(), "observed": y.tolist(), "fitted": fit["predicted"].tolist(),
        "residuals": fit["residual"].tolist(), "time_origin_s": fit["t0"],
        "px_per_m": float(px_per_m), "fit_method": "robust soft-L1 quadratic least squares",
    }
    if g < 0:
        result["warnings"] = ["The fitted acceleration points upward in image coordinates. Check object selection, image y-axis direction, and experiment setup."]
    if fit["r2"] < 0.90:
        result.setdefault("warnings", []).append("The trajectory does not fit a quadratic model well; tracking, timing, perspective, or the experiment may be unsuitable.")
    if fit["inlier_fraction"] < 0.8:
        result.setdefault("warnings", []).append("Many points differ from the fitted trajectory. Inspect the overlay/observations before trusting the result.")
    return result


def _sinusoid_at_frequency(t, y, freq, weights=None):
    tau = t - t[0]
    w = 2.0 * math.pi * freq
    A = np.c_[np.ones_like(tau), tau, np.cos(w * tau), np.sin(w * tau)]
    base_w = np.ones_like(y) if weights is None else np.clip(np.asarray(weights, dtype=float), 0.08, 1.0)
    co = np.linalg.lstsq(A * np.sqrt(base_w)[:, None], y * np.sqrt(base_w), rcond=None)[0]
    # Huber-style IRLS prevents a small number of bad tracking locations from
    # choosing the period. The reported RMSE/R² are still computed on raw data.
    for _ in range(4):
        residual = y - A @ co
        med = float(np.median(residual))
        scale = max(0.5, 1.4826 * float(np.median(np.abs(residual - med))))
        limit = 1.345 * scale
        robust_w = np.ones_like(residual)
        large = np.abs(residual - med) > limit
        robust_w[large] = limit / np.maximum(np.abs(residual[large] - med), 1e-9)
        combined = base_w * robust_w
        sw = np.sqrt(np.clip(combined, 0.01, 1.0))
        co = np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)[0]
    residual = y - A @ co
    med = float(np.median(residual))
    scale = max(0.5, 1.4826 * float(np.median(np.abs(residual - med))) )
    limit = 1.345 * scale
    abs_r = np.abs(residual - med)
    loss = np.where(abs_r <= limit, abs_r ** 2, 2.0 * limit * abs_r - limit ** 2)
    objective = float(np.sum(base_w * loss))
    return objective, co, residual, A


def fit_period(observations, axis="x"):
    obs = sorted(observations, key=lambda o: o["t"])
    t = np.asarray([o["t"] for o in obs], dtype=float)
    if axis == "auto":
        x_series = np.asarray([o["x"] for o in obs], dtype=float)
        y_series = np.asarray([o["y"] for o in obs], dtype=float)
        x_var = float(np.var(signal.detrend(x_series, type="linear")))
        y_var = float(np.var(signal.detrend(y_series, type="linear")))
        axis = "x" if x_var >= y_var else "y"
    y = np.asarray([o[axis] for o in obs], dtype=float)
    if len(t) < 30 or (t[-1] - t[0]) < 1.0:
        raise ValueError("Period fitting needs more frames and a longer clip; capture at least 3 complete oscillations.")
    # Use actual timestamps, resampling only to locate an initial frequency.
    dt = float(np.median(np.diff(t)))
    if dt <= 0:
        raise ValueError("Frame timestamps are not strictly increasing.")
    uniform_t = np.arange(t[0], t[-1] + dt * 0.25, dt)
    uniform_y = np.interp(uniform_t, t, y)
    uniform_y = signal.detrend(uniform_y, type="linear")
    fs = 1.0 / dt
    freqs, power = signal.periodogram(uniform_y, fs=fs, window="hann", nfft=max(4096, 16 * len(uniform_y)))
    duration = float(t[-1] - t[0])
    min_freq = max(0.12, 1.25 / duration)  # avoid mistaking a partial arc/trend for an oscillation
    max_freq = min(8.0, 0.45 * fs)
    valid = (freqs >= min_freq) & (freqs <= max_freq)
    if not valid.any():
        raise ValueError("The video frame rate or duration is insufficient to identify an oscillation period.")
    f0 = float(freqs[valid][np.argmax(power[valid])])
    # Refine the period continuously on the true (possibly irregular) timestamps.
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.01
    low = max(min_freq, f0 - 2.5 * df)
    high = min(max_freq, f0 + 2.5 * df)
    if high <= low:
        low, high = max(min_freq, f0 * 0.8), min(max_freq, f0 * 1.2)
    match_scores = np.asarray([fnum(o.get("score"), 0.7) or 0.7 for o in obs], dtype=float)
    weights = np.clip((match_scores - 0.25) / 0.65, 0.08, 1.0)
    opt = optimize.minimize_scalar(lambda f: _sinusoid_at_frequency(t, y, f, weights)[0],
                                   bounds=(low, high), method="bounded",
                                   options={"xatol": 1e-8, "maxiter": 200})
    freq = float(opt.x if opt.success else f0)
    _, co, residual, A = _sinusoid_at_frequency(t, y, freq, weights)
    sse = float(np.sum(residual ** 2))
    rmse_px = float(np.sqrt(np.mean(residual ** 2)))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - sse / ss_tot) if ss_tot > 1e-12 else 0.0
    period = 1.0 / freq
    cycles = duration / period
    if cycles < 2.0:
        raise ValueError(f"Only {cycles:.1f} oscillation cycles are visible. Record at least 2–3 complete cycles.")
    return {"period": float(period), "frequency_hz": freq, "cycles_observed": float(cycles),
            "period_fit_rmse_px": rmse_px, "period_fit_r2": r2, "axis_used": axis,
            "oscillation_amplitude_px": float(math.hypot(co[2], co[3]))}


def pendulum(observations, length_m):
    if length_m <= 0:
        raise ValueError("Pendulum length must be positive and measured from pivot to the bob's centre.")
    f = fit_period(observations, "x")
    g = 4.0 * math.pi ** 2 * float(length_m) / f["period"] ** 2
    warnings = []
    if f["cycles_observed"] < 2.5:
        warnings.append("Fewer than 2.5 cycles were recorded; period and gravity estimates may be unstable.")
    if f["period_fit_r2"] < 0.55:
        warnings.append("The oscillation is not strongly sinusoidal in the tracked x coordinate. Check the target and camera view.")
    return {"model": "pendulum", "g": float(g), "length_m": float(length_m),
            **f, "warnings": warnings,
            "t": [float(o["t"]) for o in observations],
            "observed": [float(o["x"]) for o in observations], "fitted": None,
            "fit_method": "frequency-domain initial estimate + continuous-time sinusoidal least-squares refinement"}


def oscillator(observations, mass_kg):
    if mass_kg <= 0:
        raise ValueError("Oscillating mass must be positive and in kilograms.")
    f = fit_period(observations, "auto")
    k = 4.0 * math.pi ** 2 * float(mass_kg) / f["period"] ** 2
    warnings = []
    if f["cycles_observed"] < 2.5:
        warnings.append("Fewer than 2.5 cycles were recorded; the period may be unstable.")
    if f["period_fit_r2"] < 0.55:
        warnings.append(f"The motion is not strongly sinusoidal on the selected {f['axis_used']} axis. Check the target and tracking quality.")
    return {"model": "oscillator", "k": float(k), "mass_kg": float(mass_kg),
            **f, "warnings": warnings,
            "t": [float(o["t"]) for o in observations],
            "observed": [float(o[f["axis_used"]]) for o in observations], "fitted": None,
            "fit_method": "frequency-domain initial estimate + continuous-time sinusoidal least-squares refinement"}


def analyze_physics(model, observations, px_per_m, params):
    px = fnum(px_per_m, 1.0)
    if model == "freefall":
        if px is None or not math.isfinite(px) or px <= 0:
            raise ValueError("Pixels per metre must be positive. Use frame calibration or enter a measured value.")
        return freefall(observations, px)
    # Period-based models use known length/mass and timestamps; they do not
    # require pixel scale to compute g or k. Their chart remains in pixels.
    if model == "pendulum":
        return pendulum(observations, float(params.get("length_m", 1.0)))
    if model == "oscillator":
        return oscillator(observations, float(params.get("mass_kg", 0.1)))
    raise ValueError("Unknown experiment model.")
