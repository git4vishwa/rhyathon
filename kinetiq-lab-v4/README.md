# Kinetiq Lab v4 — polished video-to-physics prototype

Kinetiq Lab is a local-first Python + JavaScript web application that converts an experiment video into reviewable numerical observations, physics estimates and a lab-report draft.

## What's new in v4

- Refreshed, responsive dark-lab interface with a clearer four-stage workflow, compact status indicators, metric cards, model-fit chart and report panel.
- Drag-and-drop upload, plus built-in free-fall, pendulum and spring sample clips for a quick demonstration.
- Custom play/pause and timeline controls. The target-selection canvas no longer covers native video controls.
- Inline success/error notifications and improved validation instead of interruptive browser alerts.
- Safe lightweight formatting for generated reports and clear status labels for the local decoder and OCR engine.
- Clear JSON response when a video exceeds the 500 MB upload limit.

## Architecture

1. **Video decoding:** PyAV/FFmpeg presentation timestamps are preferred; an OpenCV sequential-decoding fallback is available.
2. **Motion extraction:** User-selected region, forward/backward-checked Lucas–Kanade optical flow, local template matching, motion prediction, foreground-centroid refinement and conservative failure handling.
3. **Numerical observations:** measured `(frame, time, x, y, score, method)` points with tracking diagnostics. Tesseract separately reads visible digital/printed numerical text from sampled frames.
4. **Physics:** Python/SciPy calculates free-fall acceleration, pendulum gravity from period/length and spring constant from period/mass. Robust fits and fit-quality indicators are exposed.
5. **Report:** deterministic offline report by default; optional Ollama local model can explain already-calculated values and warnings.

This is a robust prototype, not a universal scientific measurement system. It does not contain a trained PINN or a universal object detector. Automatic ruler recognition, full camera-motion compensation, lens/perspective correction and a full systematic-error budget remain future work. A fit uncertainty is not the same thing as total experimental uncertainty.

## Install on Windows (PowerShell)

Python 3.11+ is recommended. Open PowerShell in the extracted project folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, you can use the venv's Python directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Start the app:

```powershell
python app.py
```

Open `http://127.0.0.1:5000` in a browser. The app listens on localhost and does not publish your uploaded videos to the internet.

### Tesseract OCR

Install the Tesseract desktop executable separately. If installed at the default location, Kinetiq attempts to find it automatically. For a custom location, set the variable before launching the app:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
python app.py
```

The Python package `pytesseract` is only a wrapper; the actual Tesseract executable must be installed. The status badge at the top of the UI reports whether it can be found.

### Optional Ollama report

Install/start Ollama locally and pull a text model, such as `qwen3:8b`. In the report-provider control, select **Ollama local language model**, then confirm the URL and model name. Ollama is optional; the deterministic offline report does not require it.

## Use the application

1. Drop a video onto the upload area or choose a file. Use **Free fall**, **Pendulum** or **Spring** to load an included sample clip.
2. Pause on a frame where the target is clearly visible and draw a tight box around it. Use the custom timeline to choose a frame first; changing frame resets the target box deliberately.
3. For free fall, select **Calibrate ruler** and draw a line over a known ruler segment, then ensure the known length field is correct. Manual pixels/metre is available as a fallback. Pendulum and spring period fits use the provided physical length/mass inputs and do not require a spatial scale.
4. Select **Analyse video**. Review the measured observations, OCR output, tracking coverage, fit and warnings.
5. Generate an offline or Ollama-assisted report. Export the complete result as JSON or the motion points as CSV.

## Accuracy guidance

- Prefer a fixed camera, bright lighting, short shutter/motion blur, high-contrast target and no digital zoom.
- Calibrate at the same depth and plane as the moving object; draw the reference line across the actual known length, not the entire ruler unless its entire length is known.
- Periodic motion should include multiple complete cycles and enough frames per cycle.
- Inspect target selection, coverage, match scores, fit residuals and warnings. Repeat recordings that show weak tracking or poor fits.
- Do not report more precision than the video/calibration supports. Camera movement, parallax, rolling shutter, distortion, blur, occlusion and manual calibration can cause systematic bias.

## Tests

```powershell
python -m unittest discover -s tests -v
node --check static/app.js
```

The engine test suite covers timestamp/frame decoding, synthetic free-fall tracking and gravity recovery, pendulum/spring period fits, resistance to deliberately corrupted low-confidence points, and Tesseract OCR of visible digits when the executable is available. Passing synthetic tests does not guarantee equivalent accuracy for arbitrary real videos.
