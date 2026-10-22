import math
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_engine import video_meta, frame_at_time, track_object, analyze_physics, fit_period, ocr_frame

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


class VideoEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "known_freefall.mp4"
        self.fps = 30.0
        self.px_per_m = 40.0
        self.g = 9.81
        self.frames = 36
        writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (640, 480))
        if not writer.isOpened():
            self.skipTest("OpenCV video writer unavailable")
        rng = np.random.default_rng(1234)
        for i in range(self.frames):
            t = i / self.fps
            # Slight fixed texture plus compression-friendly high-contrast target.
            base = np.full((480, 640, 3), 25, dtype=np.uint8)
            grain = rng.integers(0, 3, size=(480, 640, 1), dtype=np.uint8)
            base = np.clip(base.astype(np.int16) + grain, 0, 255).astype(np.uint8)
            x = 320 + 2 * math.sin(t * 2.3)
            y = 70 + 15 * t + 0.5 * self.g * self.px_per_m * t * t
            cv2.circle(base, (int(round(x)), int(round(y))), 13, (30, 30, 235), -1, cv2.LINE_AA)
            cv2.circle(base, (int(round(x - 4)), int(round(y - 4))), 3, (245, 245, 245), -1, cv2.LINE_AA)
            cv2.circle(base, (int(round(x + 4)), int(round(y + 4))), 2, (5, 5, 5), -1, cv2.LINE_AA)
            writer.write(base)
        writer.release()

    def tearDown(self):
        self.tmp.cleanup()

    def test_meta_and_frame_decode(self):
        meta = video_meta(self.path)
        self.assertGreaterEqual(meta["width"], 640)
        self.assertGreaterEqual(meta["height"], 480)
        self.assertAlmostEqual(meta["fps"], self.fps, delta=0.2)
        idx, t, frame = frame_at_time(self.path, 0.3)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape[:2], (480, 640))
        self.assertAlmostEqual(t, 0.3, delta=1 / self.fps + 0.01)

    def test_hybrid_track_recovers_freefall(self):
        # The ROI is source-pixel coordinates around the object at t=0.
        motion, info = track_object(self.path, [300, 50, 40, 40], start_time=0.0, max_frames=1000)
        self.assertGreaterEqual(len(motion), 28)
        self.assertGreater(info["tracking_coverage_pct"], 75)
        result = analyze_physics("freefall", motion, self.px_per_m, {})
        self.assertAlmostEqual(result["g"], self.g, delta=0.8)
        self.assertGreater(result["r2"], 0.97)
        # Assert tracked centres remain close to the synthetic object's known path.
        errors = []
        for p in motion:
            expected = 70 + 15 * p["t"] + 0.5 * self.g * self.px_per_m * p["t"] ** 2
            errors.append(abs(p["y"] - expected))
        self.assertLess(float(np.median(errors)), 2.2)


    def test_bundled_spring_sample_tracks_motion_axis_automatically(self):
        path = SAMPLES / "synthetic-spring-oscillator.mp4"
        if not path.exists():
            self.skipTest("Bundled sample clip missing")
        # The synthetic target appears at (about) 360,175 in the first frame.
        observations, info = track_object(path, [335, 150, 50, 50], start_time=0.0)
        fit = fit_period(observations, "auto")
        self.assertEqual(fit["axis_used"], "x")
        self.assertAlmostEqual(fit["period"], 0.8, delta=0.035)
        self.assertGreater(fit["period_fit_r2"], 0.98)
        self.assertGreater(info["tracking_coverage_pct"], 95)

    def test_bundled_pendulum_sample_tracks_visible_bob(self):
        path = SAMPLES / "synthetic-pendulum.mp4"
        if not path.exists():
            self.skipTest("Bundled sample clip missing")
        observations, info = track_object(path, [330, 170, 45, 45], start_time=0.0)
        fit = fit_period(observations, "x")
        self.assertAlmostEqual(fit["period"], 1.62, delta=0.12)
        self.assertGreater(fit["period_fit_r2"], 0.90)
        self.assertGreater(info["tracking_coverage_pct"], 95)

    def test_nonzero_start_uses_video_timestamps(self):
        start = 0.4
        _, actual_t, frame = frame_at_time(self.path, start)
        self.assertIsNotNone(frame)
        self.assertAlmostEqual(actual_t, start, delta=1 / self.fps + 0.015)
        # Object centre at 0.4 seconds is approximately y=101.4 px.
        observations, info = track_object(self.path, [300, 85, 40, 40], start_time=start, max_frames=1000)
        self.assertAlmostEqual(observations[0]["t"], actual_t, delta=0.01)
        self.assertAlmostEqual(observations[0]["y"], 70 + 15 * actual_t + 0.5 * self.g * self.px_per_m * actual_t ** 2, delta=2.5)


    def test_weighted_robust_freefall_fit_resists_bad_tracking_points(self):
        times = np.linspace(0.0, 1.1, 80)
        observations = []
        for i, t in enumerate(times):
            y = 60.0 + 14.0 * t + 0.5 * 9.81 * 40.0 * t * t
            score = 0.96
            if i in {9, 24, 39, 54, 69}:
                y += 27.0 if i % 2 else -31.0
                score = 0.34
            observations.append({"frame": i, "t": float(t), "x": 320.0,
                                 "y": float(y), "score": score})
        result = analyze_physics("freefall", observations, 40.0, {})
        self.assertAlmostEqual(result["g"], 9.81, delta=0.55)
        self.assertGreater(result["inlier_fraction"], 0.85)

    def test_ocr_reads_large_visible_numeric_display(self):
        import pytesseract
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            self.skipTest("Tesseract executable is not installed on this machine")
        frame = np.full((400, 800, 3), 245, dtype=np.uint8)
        cv2.putText(frame, "TIME 1.25 s", (40, 150), cv2.FONT_HERSHEY_SIMPLEX,
                    2.2, (5, 5, 5), 5, cv2.LINE_AA)
        cv2.putText(frame, "DISTANCE 0.742 m", (40, 280), cv2.FONT_HERSHEY_SIMPLEX,
                    1.8, (5, 5, 5), 4, cv2.LINE_AA)
        tokens = ocr_frame(frame)
        values = {token["text"] for token in tokens}
        self.assertIn("1.25", values)
        self.assertIn("0.742", values)
        self.assertTrue(all(token["confidence"] >= 30 for token in tokens))

    def test_continuous_period_refinement(self):
        rng = np.random.default_rng(9)
        times = np.sort(np.linspace(0, 5.2, 250) + rng.normal(0, 0.002, 250))
        obs = []
        for i, t in enumerate(times):
            y = 130 + 37 * math.sin(2 * math.pi * 1.25 * t + 0.3) + 0.02 * t + rng.normal(0, 0.25)
            obs.append({"frame": i, "t": float(t), "x": 320.0, "y": y, "score": 0.9})
        fit = fit_period(obs, "y")
        self.assertAlmostEqual(fit["period"], 0.8, delta=0.015)
        self.assertGreater(fit["period_fit_r2"], 0.99)


if __name__ == "__main__":
    unittest.main(verbosity=2)
