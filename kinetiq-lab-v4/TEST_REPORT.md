# Kinetiq Lab v4 — verification report

## Checks run for this package

- `node --check static/app.js` — passed.
- `python -m py_compile app.py video_engine.py tests/test_video_engine.py` — passed.
- `python -m unittest discover -s tests -v` — 8 tests passed after the interface refresh.
- DOM/JS consistency scan — 54 JS element references checked; no referenced IDs missing and no duplicate HTML IDs.

The engine tests cover video metadata/frame decoding, start timestamps, synthetic free-fall tracking and gravity recovery, spring and pendulum samples, robust fitting with deliberately corrupted low-confidence observations, and OCR of a high-contrast numeric display using the available Tesseract executable.

## Controlled sample clips (previous engine baseline)

These results were measured on included synthetic clips. Timings are machine-specific and are not performance guarantees.

| Sample | Tracked observations | Coverage | Mean match score | Wall time | Recovered period |
|---|---:|---:|---:|---:|---:|
| Synthetic free fall | 54 | 100% | 0.9990 | 0.518 s | n/a |
| Synthetic pendulum | 180 | 100% | 0.9431 | 1.607 s | 1.62025 s |
| Synthetic spring oscillator | 120 | 100% | 0.9994 | 0.837 s | 0.80028 s |

## Integration limits

Flask and PyAV are not installed in this build environment and package downloads are unavailable. The Python backend has been syntax-checked, and the video-engine tests were executed, but the Flask HTTP routes and browser-to-backend flow could not be launched end-to-end here. Install `requirements.txt` locally and verify the health badge and sample-video workflow. Chromium was available but did not complete a headless screenshot in this environment; the UI has been structurally checked, not visually browser-certified here.

All clips are controlled synthetic videos. Accuracy on real recordings depends on camera stability, shutter/motion blur, compression, object distinctiveness, calibration, perspective, frame timing and occlusion. Fit uncertainty is not total experimental uncertainty. The current package does not include a trained PINN or automatic ruler recognition.
