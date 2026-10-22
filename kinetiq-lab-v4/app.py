from __future__ import annotations

from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename
from pathlib import Path
import os, uuid, json, math, base64, shutil, re
import cv2
import numpy as np
import pytesseract
import requests

from video_engine import video_meta, frame_at_time, track_object, ocr_video, analyze_physics

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
RESULTS = BASE / "results"
UPLOADS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)
ALLOWED = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify({"error": "This video exceeds the 500 MB upload limit. Trim or compress the clip, then try again."}), 413

# Explicit known Windows locations first, then rely on PATH.
def configure_tesseract():
    candidates = [
        os.environ.get("TESSERACT_CMD", ""),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate
    return None


TESSERACT_PATH = configure_tesseract()


def fnum(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def json_clean(value):
    """Convert values to strict JSON-safe Python primitives (no NaN/Infinity)."""
    if isinstance(value, dict):
        return {str(k): json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def result_quality(result, tracking):
    score = fnum(tracking.get("mean_match_score"), 0.0) or 0.0
    coverage = fnum(tracking.get("tracking_coverage_pct"), 0.0) or 0.0
    r2 = fnum(result.get("r2", result.get("period_fit_r2")), None)
    warnings = list(result.get("warnings", []))
    tier = "needs review"
    if score >= 0.78 and coverage >= 90 and (r2 is None or r2 >= 0.95):
        tier = "high tracking / model fit"
    elif score >= 0.58 and coverage >= 70 and (r2 is None or r2 >= 0.80):
        tier = "moderate — inspect data"
    else:
        warnings.append("Low tracking or model-fit quality: inspect the selected target, scale calibration, graph and residuals before using the result.")
    if tracking.get("truncated"):
        warnings.append("Processing reached the frame limit; the result uses a partial clip.")
    return {"tier": tier, "mean_tracking_score": score, "tracking_coverage_pct": coverage,
            "model_fit_r2": r2, "warnings": list(dict.fromkeys(warnings))}


def report_local(result):
    model = result.get("model")
    if model == "freefall":
        headline = f"Estimated gravitational acceleration: {result.get('g', float('nan')):.4f} m/s²"
        detail = f"Fit-only uncertainty estimate: ±{result.get('g_fit_uncertainty'):.4f} m/s²" if result.get("g_fit_uncertainty") is not None else "Fit-only uncertainty estimate unavailable."
    elif model == "pendulum":
        headline = f"Estimated gravitational acceleration: {result.get('g', float('nan')):.4f} m/s²"
        detail = f"Period: {result.get('period', 0):.5f} s; observed cycles: {result.get('cycles_observed', 0):.2f}"
    else:
        headline = f"Estimated spring constant: {result.get('k', float('nan')):.4f} N/m"
        detail = f"Period: {result.get('period', 0):.5f} s; observed cycles: {result.get('cycles_observed', 0):.2f}"
    quality = result.get("quality", {})
    warnings = quality.get("warnings", [])
    warning_text = "\n".join(f"- {w}" for w in warnings) if warnings else "- No automated warning triggered; this is not a guarantee of laboratory-grade accuracy."
    return f"""# Kinetiq Lab — Experiment Report

## Result
{headline}

{detail}

## Method
The uploaded file was decoded using {result.get('tracking', {}).get('decoder', 'the available video decoder')}. Video observations were extracted using hybrid optical-flow and multi-scale template tracking. Visible numeric readings were separately inspected with Tesseract OCR when available. Python performed the physics fit; the language model is not the source of the numerical result.

## Data quality
- Tracked observations: {result.get('motion_count', 0)}
- Tracking coverage: {quality.get('tracking_coverage_pct', 0):.1f}%
- Mean tracking score: {quality.get('mean_tracking_score', 0):.3f}
- Model fit R²: {quality.get('model_fit_r2') if quality.get('model_fit_r2') is not None else 'not available'}
- Video decoder timing: {result.get('tracking', {}).get('timestamp_source', 'unknown')}
- OCR frames with visible numeric tokens: {len(result.get('ocr', []))}

## Warnings and limitations
{warning_text}

Fit uncertainty is not total experimental uncertainty. It does not include the full uncertainty in pixel-to-metre calibration, parallax, lens distortion, frame timestamp accuracy, target-selection bias or air resistance.

## Recommendations
1. Calibrate using a known ruler length in the same plane/depth as the moving object.
2. Inspect low-confidence frames and the trajectory residuals, not only the final number.
3. Record a fixed-camera clip with high contrast and adequate lighting; for periodic motion, capture at least three complete cycles.
4. Repeat the experiment and compare independent runs before interpreting the result.
"""


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    try:
        tess_version = str(pytesseract.get_tesseract_version())
        tess_ok = True
    except Exception as exc:
        tess_version = str(exc)
        tess_ok = False
    try:
        import av
        pyav_ok = True
        pyav_version = getattr(av, "__version__", "available")
    except Exception:
        pyav_ok = False
        pyav_version = None
    return jsonify({"ok": True, "opencv": cv2.__version__, "pyav": pyav_ok,
                    "pyav_version": pyav_version, "tesseract": tess_ok,
                    "tesseract_path": TESSERACT_PATH, "tesseract_version": tess_version})


@app.post("/api/upload")
def upload():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "No video supplied."}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED:
        return jsonify({"error": f"Unsupported format {ext}. Use MP4/MOV/AVI/MKV/WebM."}), 400
    uid = uuid.uuid4().hex
    path = UPLOADS / (uid + ext)
    f.save(path)
    try:
        info = video_meta(path)
        if info["width"] < 10 or info["height"] < 10:
            raise ValueError("The decoder returned invalid frame dimensions.")
        _, preview_time, first = frame_at_time(path, 0.0)
        ok, encoded = cv2.imencode(".jpg", first, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise ValueError("Could not create a video preview image.")
    except Exception as exc:
        path.unlink(missing_ok=True)
        return jsonify({"error": f"Video could not be decoded: {exc}. Try exporting an H.264 MP4."}), 422
    return jsonify({"id": uid, "filename": secure_filename(f.filename), "meta": info,
                    "preview_time": preview_time,
                    "preview": "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")})


@app.post("/api/analyze")
def analyze():
    try:
        d = request.get_json(force=True)
        uid = str(d.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", uid):
            return jsonify({"error": "Video session expired or invalid. Load the video again."}), 400
        path = next(UPLOADS.glob(uid + ".*"), None)
        if not path or not path.is_file():
            return jsonify({"error": "Uploaded video not found. Load it again."}), 404
        roi = d.get("roi")
        if not isinstance(roi, list) or len(roi) != 4:
            return jsonify({"error": "Draw a tight target box on the video frame first."}), 400
        model = str(d.get("model", "freefall"))
        if model not in {"freefall", "pendulum", "oscillator"}:
            return jsonify({"error": "Unknown experiment model."}), 400
        px = fnum(d.get("px_per_m"), None)
        if model == "freefall" and (px is None or px <= 0):
            return jsonify({"error": "Free fall requires a calibrated pixels-per-metre value. Draw a known ruler line or enter a measured scale."}), 400
        # Period-only models compute from timestamps plus known length/mass; their
        # graph may stay in pixels and does not require a spatial scale.
        if px is None or px <= 0:
            px = 1.0
        start_time = fnum(d.get("start_time_s"), 0.0)
        if start_time is None or start_time < 0:
            return jsonify({"error": "Starting video time must be zero or positive."}), 400
        observations, tracking = track_object(path, roi, start_time=start_time, max_frames=6000)
        params = {"length_m": fnum(d.get("length_m"), 1.0), "mass_kg": fnum(d.get("mass_kg"), 0.1)}
        result = analyze_physics(model, observations, px, params)
        result["raw_motion"] = observations
        result["video"] = video_meta(path)
        result["motion_count"] = len(observations)
        result["tracking"] = tracking
        result["scale_calibration"] = d.get("calibration") or ({"source": "manual pixels-per-metre input", "px_per_m": px} if model == "freefall" else {"source": "not required by period-based model", "px_per_m": None})

        # OCR contributes visible readings only. It never blocks the physics fit.
        try:
            result["ocr"] = ocr_video(path, int(max(4, min(40, fnum(d.get("ocr_count"), 18)))))
        except Exception as exc:
            result["ocr"] = []
            result["ocr_error"] = str(exc)

        result["quality"] = result_quality(result, tracking)
        rid = uuid.uuid4().hex
        result["_result_id"] = rid
        clean = json_clean(result)
        (RESULTS / (rid + ".json")).write_text(json.dumps(clean, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return jsonify({"id": rid, "result": clean})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        app.logger.exception("Analysis failed")
        return jsonify({"error": f"Analysis failed safely: {exc}"}), 500


@app.post("/api/report")
def report():
    d = request.get_json(force=True)
    rid = str(d.get("id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", rid):
        return jsonify({"error": "Analysis report not found. Run the analysis again."}), 404
    p = RESULTS / (rid + ".json")
    if not p.exists():
        return jsonify({"error": "Analysis report not found. Run the analysis again."}), 404
    result = json.loads(p.read_text(encoding="utf-8"))
    if d.get("provider") == "ollama":
        try:
            base = str(d.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
            model = str(d.get("model", "qwen3:8b"))
            summary_keys = ["model", "g", "k", "period", "frequency_hz", "cycles_observed", "axis_used",
                            "period_fit_rmse_px", "period_fit_r2", "rmse", "r2", "g_fit_uncertainty",
                            "px_per_m", "fit_method", "quality", "scale_calibration", "tracking", "warnings"]
            summary = {k: result[k] for k in summary_keys if k in result}
            summary["ocr_readings"] = [
                {"t": row.get("t"), "tokens": [{"text": tok.get("text"), "confidence": tok.get("confidence")} for tok in row.get("tokens", [])]}
                for row in result.get("ocr", [])[:20]
            ]
            prompt = ("Write a concise educational physics lab report. Use only the supplied Python-calculated values; "
                      "never alter, infer, or invent numerical measurements. Distinguish observed data from interpretation. "
                      "Discuss reported warnings and state that fit-only uncertainty excludes systematic errors.\n\n" +
                      json.dumps(summary, ensure_ascii=False))
            response = requests.post(base + "/api/generate", json={"model": model, "prompt": prompt,
                                      "stream": False, "options": {"temperature": 0.1}}, timeout=90)
            response.raise_for_status()
            text = response.json().get("response", "").strip()
            if not text:
                raise ValueError("Ollama returned an empty report.")
            return jsonify({"provider": "ollama", "report": text})
        except Exception as exc:
            return jsonify({"provider": "template-fallback", "report": report_local(result), "warning": str(exc)})
    return jsonify({"provider": "template", "report": report_local(result)})


@app.get("/samples/<path:name>")
def samples(name):
    return send_from_directory(BASE / "samples", name)


@app.get("/api/sample-frame")
def sample_frame():
    # Useful diagnostics endpoint: returns the decoded frame nearest the requested time.
    name = request.args.get("name", "")
    if Path(name).name != name or not name.endswith(".mp4"):
        return jsonify({"error": "Invalid sample name."}), 400
    path = BASE / "samples" / name
    if not path.exists():
        return jsonify({"error": "Sample not found."}), 404
    t = fnum(request.args.get("t", 0), 0.0) or 0.0
    _, actual_t, frame = frame_at_time(path, max(0.0, t))
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        return jsonify({"error": "Could not encode sample frame."}), 500
    return jsonify({"t": actual_t, "image": "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
