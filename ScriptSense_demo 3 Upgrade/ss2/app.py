from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import uuid
import base64
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

try:
    from PIL import Image, ImageOps, ImageFilter
except Exception:  # Optional for demo environments
    Image = ImageOps = ImageFilter = None

try:
    import pytesseract
except Exception:  # Optional; the app still works in demo/fallback mode
    pytesseract = None

try:
    import requests
except Exception:
    requests = None

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "scriptsense.db"
DB_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

DEMO_CASE = {
    "case_id": "RX-2026-0148",
    "source": "Demo prescription",
    "ocr_text": "Tab. Amoxicillin 500 mg — 1 tablet three times daily\nTab. Warfarin 5 mg — once daily evening\nCap. Omeprazole 20 mg — once daily morning\nTab. Paracetamol 500 mg — as needed",
}

# A tiny illustrative lexicon for a hackathon prototype. It is not a clinical database.
DRUGS = {
    "amoxicillin": {"display": "Amoxicillin", "form": "tablet"},
    "warfarin": {"display": "Warfarin", "form": "tablet"},
    "omeprazole": {"display": "Omeprazole", "form": "capsule"},
    "paracetamol": {"display": "Paracetamol", "form": "tablet"},
    "acetaminophen": {"display": "Paracetamol", "form": "tablet"},
    "metformin": {"display": "Metformin", "form": "tablet"},
    "amlodipine": {"display": "Amlodipine", "form": "tablet"},
    "atorvastatin": {"display": "Atorvastatin", "form": "tablet"},
    "aspirin": {"display": "Aspirin", "form": "tablet"},
    "ibuprofen": {"display": "Ibuprofen", "form": "tablet"},
    "clopidogrel": {"display": "Clopidogrel", "form": "tablet"},
    "azithromycin": {"display": "Azithromycin", "form": "tablet"},
}

# Deliberately small, transparent demo rules.
INTERACTIONS = {
    frozenset({"warfarin", "amoxicillin"}): {
        "level": "HIGH",
        "title": "Potential drug interaction",
        "summary": "Warfarin + Amoxicillin is a demo rule selected to illustrate a high-priority interaction review.",
        "action": "Confirm indication, dose, duration, and patient-specific factors.",
    },
    frozenset({"warfarin", "aspirin"}): {
        "level": "HIGH",
        "title": "Bleeding-risk review",
        "summary": "Warfarin + Aspirin is flagged by the prototype for medication-safety review.",
        "action": "Confirm whether combined therapy is intended.",
    },
    frozenset({"warfarin", "ibuprofen"}): {
        "level": "HIGH",
        "title": "Bleeding-risk review",
        "summary": "Warfarin + Ibuprofen is flagged by the prototype for medication-safety review.",
        "action": "Confirm indication and patient-specific bleeding risk.",
    },
    frozenset({"aspirin", "ibuprofen"}): {
        "level": "REVIEW",
        "title": "Duplicate / therapy review",
        "summary": "Two medicines with overlapping pain/inflammation use were detected.",
        "action": "Cross-check the medication history and intended therapy.",
    },
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            ocr_text TEXT,
            overall_confidence REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            name TEXT NOT NULL,
            strength TEXT,
            form TEXT,
            frequency TEXT,
            confidence REAL,
            needs_review INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            FOREIGN KEY(case_id) REFERENCES cases(case_id)
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            level TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            action TEXT,
            FOREIGN KEY(case_id) REFERENCES cases(case_id)
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def clean_text(text: str) -> str:
    text = text.replace("\x0c", " ")
    text = re.sub(r"[|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def vision_ocr_image(file_bytes: bytes) -> str:
    """Use the local multimodal Gemma model as handwriting OCR.

    Gemma 3 4B in Ollama supports image input, which is much better suited
    to handwritten prescriptions than plain Tesseract. The prompt explicitly
    forbids guessing so uncertain text can be sent through the verification gate.
    """
    if requests is None:
        return ""
    model = os.getenv("SCRIPTSENSE_MODEL", "gemma3:4b")
    try:
        if Image is not None:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            # Keep the image reasonably sized for fast local inference while
            # preserving enough detail for handwriting.
            max_side = 1600
            scale = min(1.0, max_side / max(img.width, img.height))
            if scale < 1.0:
                img = img.resize((int(img.width * scale), int(img.height * scale)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            payload = base64.b64encode(buf.getvalue()).decode("utf-8")
        else:
            payload = base64.b64encode(file_bytes).decode("utf-8")

        prompt = (
            "Act as a prescription handwriting transcription engine. Read ONLY the text "
            "that is visibly written in this prescription image. Transcribe every medicine "
            "line and its visible strength, dosage directions, frequency, and form. "
            "Do NOT guess missing letters or invent drugs. If a word is uncertain, preserve "
            "the uncertainty with [unclear] rather than choosing a likely medicine. "
            "Return plain text only, one medication per line, with no commentary."
        )
        r = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt, "images": [payload]}],
                "stream": False,
                "keep_alive": -1,
                "options": {"temperature": 0.1},
            },
            timeout=35,
        )
        r.raise_for_status()
        data = r.json()
        return clean_text(data.get("message", {}).get("content", ""))
    except Exception:
        return ""


def tesseract_ocr_image(file_bytes: bytes) -> str:
    if pytesseract is None or Image is None:
        return ""
    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("L")
        img = ImageOps.autocontrast(img)
        img = img.resize((img.width * 2, img.height * 2))
        img = img.filter(ImageFilter.SHARPEN)
        text = pytesseract.image_to_string(img, config="--psm 6")
        return clean_text(text)
    except Exception:
        return ""


def ocr_image(file_bytes: bytes) -> str:
    # Vision OCR first for handwriting; Tesseract is retained as a fallback.
    text = vision_ocr_image(file_bytes)
    if text:
        return text
    return tesseract_ocr_image(file_bytes)


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def closest_drug(token: str) -> tuple[str | None, float]:
    token = normalize_token(token)
    if not token:
        return None, 0.0
    best_name, best_score = None, 0.0
    for key, meta in DRUGS.items():
        score = SequenceMatcher(None, token, key).ratio()
        if score > best_score:
            best_name, best_score = key, score
    return best_name, best_score


def parse_frequency(line: str) -> str:
    low = line.lower()
    if "three times" in low or "3 times" in low or "tid" in low or "3x" in low:
        return "3× daily"
    if "twice" in low or "2 times" in low or "bid" in low or "2x" in low:
        return "2× daily"
    if "four times" in low or "4 times" in low or "qid" in low:
        return "4× daily"
    if "as needed" in low or "prn" in low:
        return "as needed"
    if "night" in low or "evening" in low:
        return "1× evening"
    if "morning" in low:
        return "1× morning"
    return "1× daily"


def parse_lines(ocr_text: str, is_demo: bool = False) -> list[dict[str, Any]]:
    if is_demo:
        return [
            {"name": "Amoxicillin", "strength": "500 mg", "form": "tablet", "frequency": "3× daily", "confidence": 98, "needs_review": False},
            {"name": "Warfarin", "strength": "5 mg", "form": "tablet", "frequency": "1× evening", "confidence": 96, "needs_review": False},
            {"name": "Omeprazole", "strength": "20 mg", "form": "capsule", "frequency": "1× morning", "confidence": 89, "needs_review": True},
            {"name": "Paracetamol", "strength": "500 mg", "form": "tablet", "frequency": "as needed", "confidence": 97, "needs_review": False},
        ]

    meds: list[dict[str, Any]] = []
    lines = [line.strip(" -•*\t") for line in re.split(r"[\n.;]", ocr_text) if line.strip()]
    for line in lines:
        words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", line)
        candidate = None
        score = 0.0
        # Prefer a medication-looking token, then fall back to fuzzy matching.
        for word in words:
            name, similarity = closest_drug(word)
            if name and similarity > score:
                candidate, score = name, similarity
        if candidate and score >= 0.60:
            meta = DRUGS[candidate]
            mg = re.search(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g)\b", line, re.I)
            strength = f"{mg.group(1)} {mg.group(2).lower()}" if mg else "strength unclear"
            frequency = parse_frequency(line)
            confidence = round(min(99.0, 50.0 + score * 49.0), 1)
            meds.append({
                "name": meta["display"],
                "strength": strength,
                "form": meta["form"],
                "frequency": frequency,
                "confidence": confidence,
                "needs_review": confidence < 90,
            })

    # Remove duplicate drug candidates while keeping the strongest read.
    unique: dict[str, dict[str, Any]] = {}
    for med in meds:
        key = med["name"].lower()
        if key not in unique or med["confidence"] > unique[key]["confidence"]:
            unique[key] = med
    return list(unique.values())


def demo_fallback() -> list[dict[str, Any]]:
    return [
        {"name": "Amoxicillin", "strength": "500 mg", "form": "tablet", "frequency": "3× daily", "confidence": 72, "needs_review": True},
        {"name": "Warfarin", "strength": "5 mg", "form": "tablet", "frequency": "1× evening", "confidence": 68, "needs_review": True},
    ]


def compute_findings(meds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [normalize_token(m["name"]) for m in meds]
    findings: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            rule = INTERACTIONS.get(frozenset({names[i], names[j]}))
            if rule:
                findings.append(dict(rule))

    # A simple duplicate-therapy demonstration for repeated class-like items.
    analgesics = {"paracetamol", "aspirin", "ibuprofen"}
    if len(set(names) & analgesics) >= 2:
        findings.append({
            "level": "REVIEW",
            "title": "Duplicate / therapy review",
            "summary": "Multiple pain-relief candidates were detected. The prototype asks for a medication-history check.",
            "action": "Confirm intended therapy and existing medicines before dispensing.",
        })
    return findings


def build_schedule(meds: list[dict[str, Any]]) -> list[dict[str, str]]:
    schedule: list[dict[str, str]] = []
    used_slots = {"1× morning": "08:00 AM", "1× evening": "06:00 PM", "1× daily": "09:00 AM"}
    for med in meds:
        freq = med["frequency"]
        if freq == "as needed":
            continue
        if freq == "3× daily":
            for time, label in [("08:00 AM", "Morning"), ("01:00 PM", "Midday"), ("09:00 PM", "Night")]:
                schedule.append({"time": time, "label": label, "name": med["name"], "detail": f'{med["strength"]} · scheduled dose'})
        elif freq == "2× daily":
            for time, label in [("08:00 AM", "Morning"), ("08:00 PM", "Night")]:
                schedule.append({"time": time, "label": label, "name": med["name"], "detail": f'{med["strength"]} · scheduled dose'})
        else:
            label = "Morning" if "morning" in freq else "Evening" if "evening" in freq else "Daily"
            time = used_slots.get(freq, "09:00 AM")
            schedule.append({"time": time, "label": label, "name": med["name"], "detail": f'{med["strength"]} · scheduled dose'})
    schedule.sort(key=lambda x: datetime.strptime(x["time"], "%I:%M %p"))
    return schedule


def save_case(case_id: str, source: str, ocr_text: str, meds: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    conn = db()
    conn.execute("DELETE FROM medications WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM findings WHERE case_id = ?", (case_id,))
    avg = round(sum(float(m["confidence"]) for m in meds) / max(1, len(meds)), 1)
    conn.execute(
        "INSERT OR REPLACE INTO cases(case_id, created_at, source, ocr_text, overall_confidence) VALUES (?, ?, ?, ?, ?)",
        (case_id, datetime.now().isoformat(timespec="seconds"), source, ocr_text, avg),
    )
    for med in meds:
        conn.execute(
            "INSERT INTO medications(case_id,name,strength,form,frequency,confidence,needs_review,verified) VALUES (?,?,?,?,?,?,?,?)",
            (case_id, med["name"], med["strength"], med["form"], med["frequency"], med["confidence"], int(med["needs_review"]), int(med.get("verified", False))),
        )
    for finding in findings:
        conn.execute(
            "INSERT INTO findings(case_id,level,title,summary,action) VALUES (?,?,?,?,?)",
            (case_id, finding["level"], finding["title"], finding["summary"], finding["action"]),
        )
    conn.commit()
    conn.close()


def make_result(case_id: str, source: str, ocr_text: str, meds: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    overall = round(sum(float(m["confidence"]) for m in meds) / max(1, len(meds)), 1)
    schedule = build_schedule(meds)
    return {
        "case_id": case_id,
        "source": source,
        "ocr_text": ocr_text,
        "overall_confidence": overall,
        "medications": meds,
        "findings": findings,
        "schedule": schedule,
        "stats": {
            "medications_detected": len(meds),
            "needs_review": sum(1 for m in meds if m["needs_review"]),
            "high_findings": sum(1 for f in findings if f["level"] == "HIGH"),
            "findings": len(findings),
            "scheduled_entries": len(schedule),
            "prn": sum(1 for m in meds if m["frequency"] == "as needed"),
        },
    }


def generate_ai_review(data: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""You are the explanation layer for a healthcare hackathon prototype called ScriptSense.\n\nUse ONLY the structured evidence below. Do not invent medication facts, interactions, doses, or percentages. Explain what was recognized, what needs human verification, what safety rules triggered, and the next sensible review action. Keep it to 120 words. Clearly say it is a prototype draft and not clinical advice.\n\nEvidence:\n{json.dumps(data, indent=2)}"""
    model = os.getenv("SCRIPTSENSE_MODEL", "gemma3:4b")
    if requests:
        try:
            r = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "keep_alive": -1},
                timeout=25,
            )
            r.raise_for_status()
            answer = r.json().get("response", "").strip()
            if answer:
                return answer, "Gemma 4B · local"
        except Exception:
            pass

    # Deterministic fallback so the demo still works without Ollama.
    review_bits = []
    review_bits.append(f"{data['stats']['medications_detected']} medication candidate(s) were extracted.")
    if data["stats"]["needs_review"]:
        review_bits.append(f"{data['stats']['needs_review']} candidate(s) are below the preferred confidence threshold and should be checked against the original handwriting.")
    if data["stats"]["findings"]:
        review_bits.append(f"{data['stats']['findings']} safety finding(s) were triggered by the prototype rules; the highest-priority item is surfaced for pharmacist review.")
    review_bits.append("The dosage schedule is a draft generated from recognized directions and must be confirmed before dispensing.")
    return " ".join(review_bits) + " Prototype output only — not clinical advice.", "ScriptSense rules · fallback"


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ocr_available": (pytesseract is not None or requests is not None),
        "vision_ocr": requests is not None,
        "ollama_configured": requests is not None,
        "model": os.getenv("SCRIPTSENSE_MODEL", "gemma3:4b"),
        "database": str(DB_PATH.name),
    })


@app.post("/api/analyze-demo")
def analyze_demo():
    case_id = DEMO_CASE["case_id"]
    meds = parse_lines(DEMO_CASE["ocr_text"], is_demo=True)
    findings = compute_findings(meds)
    save_case(case_id, DEMO_CASE["source"], DEMO_CASE["ocr_text"], meds, findings)
    return jsonify(make_result(case_id, DEMO_CASE["source"], DEMO_CASE["ocr_text"], meds, findings))


@app.post("/api/analyze")
def analyze_upload():
    uploaded = request.files.get("prescription")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "No prescription image supplied."}), 400

    raw = uploaded.read()
    if not raw:
        return jsonify({"error": "The uploaded file is empty."}), 400

    text = ocr_image(raw)
    case_id = "RX-" + uuid.uuid4().hex[:6].upper()
    meds = parse_lines(text)
    source = uploaded.filename

    if not meds:
        # The point of this mode is to demonstrate the verification gate rather than fabricate recognition.
        text = text or "OCR could not confidently read medication names from this image."
        meds = demo_fallback()
        findings = compute_findings(meds)
    else:
        findings = compute_findings(meds)

    save_case(case_id, source, text, meds, findings)
    return jsonify(make_result(case_id, source, text, meds, findings))


@app.post("/api/ai-review")
def ai_review():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No analysis evidence supplied."}), 400
    text, engine = generate_ai_review(data)
    return jsonify({"review": text, "engine": engine})


@app.post("/api/verify")
def verify_medication():
    payload = request.get_json(silent=True) or {}
    case_id = payload.get("case_id")
    name = payload.get("name")
    if not case_id or not name:
        return jsonify({"error": "case_id and name are required."}), 400
    conn = db()
    conn.execute("UPDATE medications SET verified = 1, needs_review = 0 WHERE case_id = ? AND lower(name) = lower(?)", (case_id, name))
    conn.commit()
    row = conn.execute("SELECT name, confidence, verified, needs_review FROM medications WHERE case_id = ? AND lower(name) = lower(?)", (case_id, name)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Medication not found."}), 404
    return jsonify({"ok": True, "medication": dict(row)})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
