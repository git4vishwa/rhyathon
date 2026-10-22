from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

# Soft fallbacks
try:
    from PIL import Image
except Exception:
    Image = None

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = np = None

try:
    import pytesseract
except Exception:
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
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB max upload limit

DEMO_CASE = {
    "case_id": "RX-2026-0148",
    "source": "Demo prescription",
    "ocr_text": "Tab. Amoxicillin 500 mg — 1 tablet three times daily\nTab. Warfarin 5 mg — once daily evening\nCap. Omeprazole 20 mg — once daily morning\nTab. Paracetamol 500 mg — as needed",
}

# Standardized Medical Lexicon
# --- EXPANDED GENERAL-PURPOSE & BRAND MEDICINE LEXICON ---
DRUGS = {
    # --- Pain, Fever & Inflammation (Analgesics / NSAIDs) ---
    "paracetamol": {"display": "Paracetamol", "form": "tablet"},
    "acetaminophen": {"display": "Paracetamol", "form": "tablet"},
    "calpol": {"display": "Calpol (Paracetamol)", "form": "syrup/tablet"},
    "crocin": {"display": "Crocin (Paracetamol)", "form": "syrup/tablet"},
    "dolo": {"display": "Dolo 650 (Paracetamol)", "form": "tablet"},
    "cplpol": {"display": "Calpol (Paracetamol)", "form": "syrup"},
    "meftal": {"display": "Meftal-P (Mefenamic Acid)", "form": "syrup/tablet"},
    "meftal-p": {"display": "Meftal-P (Mefenamic Acid)", "form": "syrup/tablet"},
    "meftlp-p": {"display": "Meftal-P (Mefenamic Acid)", "form": "syrup"},
    "mefenamic": {"display": "Mefenamic Acid", "form": "suspension/tablet"},
    "ibuprofen": {"display": "Ibuprofen", "form": "tablet"},
    "combiflam": {"display": "Combiflam", "form": "tablet"},
    "aspirin": {"display": "Aspirin", "form": "tablet"},
    "disprin": {"display": "Disprin (Aspirin)", "form": "tablet"},
    "diclofenac": {"display": "Diclofenac", "form": "tablet/injection"},
    "voveran": {"display": "Voveran (Diclofenac)", "form": "tablet/injection"},
    "aceclofenac": {"display": "Aceclofenac", "form": "tablet"},
    "zerodol": {"display": "Zerodol", "form": "tablet"},
    "tramadol": {"display": "Tramadol", "form": "capsule"},
    "naproxen": {"display": "Naproxen", "form": "tablet"},

    # --- Cough, Cold, Allergy & Asthma (Pediatric & Adult) ---
    "delcon": {"display": "Delcon (Decongestant)", "form": "syrup"},
    "levolin": {"display": "Levolin (Levosalbutamol)", "form": "syrup/inhaler"},
    "salbutamol": {"display": "Salbutamol", "form": "syrup/inhaler"},
    "asthalin": {"display": "Asthalin (Salbutamol)", "form": "syrup/inhaler"},
    "ascoril": {"display": "Ascoril Cough Syrup", "form": "syrup"},
    "benadryl": {"display": "Benadryl Cough Syrup", "form": "syrup"},
    "cetirizine": {"display": "Cetirizine", "form": "tablet/syrup"},
    "okacet": {"display": "Okacet (Cetirizine)", "form": "tablet"},
    "levocetirizine": {"display": "Levocetirizine", "form": "tablet/syrup"},
    "montelukast": {"display": "Montelukast", "form": "tablet"},
    "montair": {"display": "Montair", "form": "tablet"},
    "allegra": {"display": "Allegra (Fexofenadine)", "form": "tablet"},
    "fexofenadine": {"display": "Fexofenadine", "form": "tablet"},
    "chlorpheniramine": {"display": "Chlorpheniramine", "form": "syrup/tablet"},

    # --- Antibiotics & Anti-Infectives ---
    "amoxicillin": {"display": "Amoxicillin", "form": "capsule/tablet"},
    "amoxil": {"display": "Amoxicillin", "form": "capsule"},
    "mox": {"display": "Mox (Amoxicillin)", "form": "capsule"},
    "amoxyclav": {"display": "Amoxyclav / Augmentin", "form": "tablet"},
    "augmentin": {"display": "Augmentin", "form": "tablet/syrup"},
    "ampicillin": {"display": "Ampicillin", "form": "capsule/injection"},
    "bicillin": {"display": "Bicillin", "form": "injection"},
    "azithromycin": {"display": "Azithromycin", "form": "tablet/syrup"},
    "azithral": {"display": "Azithral (Azithromycin)", "form": "tablet"},
    "ciprofloxacin": {"display": "Ciprofloxacin", "form": "tablet"},
    "ciplox": {"display": "Ciplox", "form": "tablet"},
    "ofloxacin": {"display": "Ofloxacin", "form": "tablet"},
    "norfloxacin": {"display": "Norfloxacin", "form": "tablet"},
    "cefixime": {"display": "Cefixime", "form": "tablet"},
    "taxim": {"display": "Taxim (Cefixime)", "form": "tablet"},
    "ceftriaxone": {"display": "Ceftriaxone", "form": "injection"},
    "metronidazole": {"display": "Metronidazole", "form": "tablet"},
    "flagyl": {"display": "Flagyl (Metronidazole)", "form": "tablet"},
    "doxycycline": {"display": "Doxycycline", "form": "capsule"},

    # --- Antacids, Antiulcer & GI Medications ---
    "pantoprazole": {"display": "Pantoprazole", "form": "tablet"},
    "pan": {"display": "Pan-40 (Pantoprazole)", "form": "tablet"},
    "pantocid": {"display": "Pantocid", "form": "tablet"},
    "omeprazole": {"display": "Omeprazole", "form": "capsule"},
    "ocid": {"display": "Ocid (Omeprazole)", "form": "capsule"},
    "rabeprazole": {"display": "Rabeprazole", "form": "tablet"},
    "rabeloc": {"display": "Rabeloc", "form": "tablet"},
    "ranitidine": {"display": "Ranitidine", "form": "tablet"},
    "zinetac": {"display": "Zinetac", "form": "tablet"},
    "famotidine": {"display": "Famotidine", "form": "tablet"},
    "ondansetron": {"display": "Ondansetron", "form": "tablet/syrup"},
    "emset": {"display": "Emset (Ondansetron)", "form": "tablet/syrup"},
    "domperidone": {"display": "Domperidone", "form": "tablet"},
    "digene": {"display": "Digene Antacid", "form": "syrup/tablet"},

    # --- Emergency Fluids, Rehydration & Anti-Hypoglycemics ---
    "dextrose": {"display": "Dextrose (IV Fluid)", "form": "infusion"},
    "d5": {"display": "5% Dextrose", "form": "infusion"},
    "d10": {"display": "10% Dextrose", "form": "infusion"},
    "d50": {"display": "50% Dextrose", "form": "infusion"},
    "ors": {"display": "Oral Rehydration Salts (ORS)", "form": "sachet"},
    "saline": {"display": "Normal Saline (0.9% NaCl)", "form": "infusion"},
    "normal saline": {"display": "Normal Saline (0.9% NaCl)", "form": "infusion"},
    "ringer lactate": {"display": "Ringer's Lactate (RL)", "form": "infusion"},

    # --- Antidiabetics ---
    "metformin": {"display": "Metformin", "form": "tablet"},
    "glycomet": {"display": "Glycomet (Metformin)", "form": "tablet"},
    "glimepiride": {"display": "Glimepiride", "form": "tablet"},
    "amaryl": {"display": "Amaryl (Glimepiride)", "form": "tablet"},
    "gliclazide": {"display": "Gliclazide", "form": "tablet"},
    "insulin": {"display": "Insulin", "form": "injection"},
    "lantus": {"display": "Lantus (Insulin)", "form": "injection"},
    "humalog": {"display": "Humalog (Insulin)", "form": "injection"},
    "teneligliptin": {"display": "Teneligliptin", "form": "tablet"},
    "sitagliptin": {"display": "Sitagliptin", "form": "tablet"},

    # --- Blood Pressure, Cardiac & Cholesterol ---
    "amlodipine": {"display": "Amlodipine", "form": "tablet"},
    "amlong": {"display": "Amlong (Amlodipine)", "form": "tablet"},
    "telmisartan": {"display": "Telmisartan", "form": "tablet"},
    "telma": {"display": "Telma (Telmisartan)", "form": "tablet"},
    "losartan": {"display": "Losartan", "form": "tablet"},
    "atenolol": {"display": "Atenolol", "form": "tablet"},
    "metoprolol": {"display": "Metoprolol", "form": "tablet"},
    "atorvastatin": {"display": "Atorvastatin", "form": "tablet"},
    "atorva": {"display": "Atorva (Atorvastatin)", "form": "tablet"},
    "rosuvastatin": {"display": "Rosuvastatin", "form": "tablet"},
    "clopidogrel": {"display": "Clopidogrel", "form": "tablet"},
    "warfarin": {"display": "Warfarin", "form": "tablet"},
    "furosemide": {"display": "Furosemide", "form": "tablet/injection"},
    "lasix": {"display": "Lasix (Furosemide)", "form": "tablet/injection"},

    # --- Thyroid & Neurological ---
    "thyronorm": {"display": "Thyronorm (Levothyroxine)", "form": "tablet"},
    "eltroxin": {"display": "Eltroxin (Levothyroxine)", "form": "tablet"},
    "levothyroxine": {"display": "Levothyroxine", "form": "tablet"},
    "gabapentin": {"display": "Gabapentin", "form": "capsule"},
    "pregabalin": {"display": "Pregabalin", "form": "capsule"},
}

INTERACTIONS = {
    frozenset({"warfarin", "amoxicillin"}): {
        "level": "HIGH",
        "title": "Potential drug interaction",
        "summary": "Warfarin + Amoxicillin increases bleeding risk.",
        "action": "Confirm indication, dose, duration, and patient factors.",
    },
    frozenset({"warfarin", "aspirin"}): {
        "level": "HIGH",
        "title": "Bleeding-risk review",
        "summary": "Warfarin + Aspirin significantly elevates hemorrhage risk.",
        "action": "Confirm whether combined therapy is intended.",
    },
    frozenset({"warfarin", "ibuprofen"}): {
        "level": "HIGH",
        "title": "Bleeding-risk review",
        "summary": "Warfarin + NSAID combination detected.",
        "action": "Confirm indication and patient-specific bleeding risk.",
    },
    frozenset({"aspirin", "ibuprofen"}): {
        "level": "REVIEW",
        "title": "Duplicate / therapy review",
        "summary": "Two medicines with overlapping pain/inflammation use detected.",
        "action": "Cross-check medication history.",
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

def preprocess_image_for_ocr(file_bytes: bytes) -> bytes:
    if cv2 is None or np is None:
        return file_bytes

    try:
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dilated = cv2.dilate(gray, np.ones((7, 7), np.uint8))
        bg_img = cv2.medianBlur(dilated, 21)
        diff_img = 255 - cv2.absdiff(gray, bg_img)
        norm_img = cv2.normalize(diff_img, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(norm_img)

        _, encoded_img = cv2.imencode('.jpg', enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return encoded_img.tobytes()
    except Exception:
        return file_bytes

def clean_text(text: str) -> str:
    text = text.replace("\x0c", " ")
    text = re.sub(r"[|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def vision_ocr_image(file_bytes: bytes) -> str:
    if requests is None:
        return ""
    
    processed_bytes = preprocess_image_for_ocr(file_bytes)
    model = os.getenv("SCRIPTSENSE_MODEL", "gemma3:4b")
    
    try:
        if Image is not None:
            img = Image.open(io.BytesIO(processed_bytes)).convert("RGB")
            max_side = 2048
            scale = min(1.0, max_side / max(img.width, img.height))
            if scale < 1.0:
                img = img.resize((int(img.width * scale), int(img.height * scale)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95, optimize=True)
            payload = base64.b64encode(buf.getvalue()).decode("utf-8")
        else:
            payload = base64.b64encode(processed_bytes).decode("utf-8")

        prompt = (
            "Transcribe ONLY the prescribed medication lines from this prescription image.\n"
            "Do NOT include header introductions, metadata, form numbers, or patient personal info.\n"
            "Format: Output each distinct medication line on a new line."
        )
        r = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt, "images": [payload]}],
                "stream": False,
                "keep_alive": -1,
                "options": {"temperature": 0.0},
            },
            timeout=40,
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
        processed_bytes = preprocess_image_for_ocr(file_bytes)
        img = Image.open(io.BytesIO(processed_bytes)).convert("L")
        text = pytesseract.image_to_string(img, config="--psm 6")
        return clean_text(text)
    except Exception:
        return ""

def ocr_image(file_bytes: bytes) -> str:
    text = vision_ocr_image(file_bytes)
    if text and len(text) > 10:
        return text
    
    tess_text = tesseract_ocr_image(file_bytes)
    return tess_text if tess_text else text

def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())

def strict_drug_match(token: str) -> tuple[str | None, float]:
    """Strict similarity matching algorithm requiring clean sequence alignment."""
    norm_token = normalize_token(token)
    
    # Must be at least 4 letters long to avoid matching 2-letter tokens like "in" to "insulin"
    if not norm_token or len(norm_token) < 4:
        return None, 0.0
    
    best_name, best_score = None, 0.0
    for key in DRUGS:
        norm_key = normalize_token(key)
        
        # Calculate standard Sequence Matcher ratio without artificial inflation
        score = SequenceMatcher(None, norm_token, norm_key).ratio()
        
        if score > best_score:
            best_name, best_score = key, score
            
    # Require a high similarity threshold (>= 0.72)
    if best_score >= 0.72:
        return best_name, best_score
    return None, 0.0

def parse_frequency(line: str) -> str:
    low = line.lower()
    if any(x in low for x in ["stat", "immediately"]):
        return "stat (immediately)"
    if any(x in low for x in ["tid", "t.i.d", "three times", "3 times", "3x"]):
        return "3× daily"
    if any(x in low for x in ["bid", "b.i.d", "twice", "2 times", "2x"]):
        return "2× daily"
    if any(x in low for x in ["qid", "q.i.d", "four times"]):
        return "4× daily"
    if any(x in low for x in ["prn", "p.r.n", "as needed"]):
        return "as needed"
    if any(x in low for x in ["night", "evening", "hs"]):
        return "1× evening"
    if any(x in low for x in ["morning", "qam"]):
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
    
    # Split text into lines
    raw_lines = [l.strip() for l in re.split(r"[\n.;]", ocr_text) if l.strip()]
    
    # Ignore administrative lines commonly found on military / standardized forms
    ignore_keywords = ["dd form", "usn", "u.s.s", "medical facility", "date", "exp", "filled by", "signature", "edition of", "prescription for"]

    for line in raw_lines:
        line_low = line.lower()
        if any(kw in line_low for kw in ignore_keywords):
            continue

        words = re.findall(r"[A-Za-z]{4,}", line)
        for word in words:
            candidate, score = strict_drug_match(word)
            if candidate:
                meta = DRUGS[candidate]
                dose_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|sachets|sachet|units|iu|%)\b", line, re.I)
                strength = f"{dose_match.group(1)} {dose_match.group(2)}" if dose_match else "dosage specified"
                frequency = parse_frequency(line)
                confidence = round(min(99.0, score * 100.0), 1)

                meds.append({
                    "name": meta["display"],
                    "strength": strength,
                    "form": meta["form"],
                    "frequency": frequency,
                    "confidence": confidence,
                    "needs_review": confidence < 85.0,
                })

    # Deduplicate medication entries
    unique: dict[str, dict[str, Any]] = {}
    for med in meds:
        key = med["name"].lower()
        if key not in unique or med["confidence"] > unique[key]["confidence"]:
            unique[key] = med
            
    return list(unique.values())

def compute_findings(meds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [normalize_token(m["name"]) for m in meds]
    findings: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            rule = INTERACTIONS.get(frozenset({names[i], names[j]}))
            if rule:
                findings.append(dict(rule))

    analgesics = {"paracetamol", "aspirin", "ibuprofen"}
    if len(set(names) & analgesics) >= 2:
        findings.append({
            "level": "REVIEW",
            "title": "Duplicate / therapy review",
            "summary": "Multiple pain-relief candidates were detected.",
            "action": "Confirm intended therapy before dispensing.",
        })
    return findings

def build_schedule(meds: list[dict[str, Any]]) -> list[dict[str, str]]:
    schedule: list[dict[str, str]] = []
    used_slots = {"1× morning": "08:00 AM", "1× evening": "06:00 PM", "1× daily": "09:00 AM", "stat (immediately)": "Immediate"}
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
        elif freq == "stat (immediately)":
            schedule.append({"time": "08:00 AM", "label": "STAT", "name": med["name"], "detail": f'{med["strength"]} · immediate administration'})
        else:
            label = "Morning" if "morning" in freq else "Evening" if "evening" in freq else "Daily"
            time = used_slots.get(freq, "09:00 AM")
            schedule.append({"time": time, "label": label, "name": med["name"], "detail": f'{med["strength"]} · scheduled dose'})
            
    try:
        schedule.sort(key=lambda x: datetime.strptime(x["time"], "%I:%M %p"))
    except Exception:
        pass
    return schedule

def save_case(case_id: str, source: str, ocr_text: str, meds: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    conn = db()
    conn.execute("DELETE FROM medications WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM findings WHERE case_id = ?", (case_id,))
    avg = round(sum(float(m["confidence"]) for m in meds) / max(1, len(meds)), 1) if meds else 0.0
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
    overall = round(sum(float(m["confidence"]) for m in meds) / max(1, len(meds)), 1) if meds else 0.0
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
    prompt = f"""You are the explanation layer for a healthcare prototype called ScriptSense.
Use ONLY the structured evidence below. Do not invent medication facts. Summarize what was recognized from the prescription, including medications and findings. Max 120 words.
Evidence:
{json.dumps(data, indent=2)}"""
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

    review_bits = [f"{data['stats']['medications_detected']} item(s) extracted."]
    if data["stats"]["needs_review"]:
        review_bits.append(f"{data['stats']['needs_review']} candidate(s) flagged for manual check.")
    if data["stats"]["findings"]:
        review_bits.append(f"{data['stats']['findings']} safety finding(s) triggered.")
    return " ".join(review_bits) + " Prototype output only.", "ScriptSense rules · fallback"

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ocr_available": (pytesseract is not None or requests is not None),
        "opencv_enabled": cv2 is not None,
        "vision_ocr": requests is not None,
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