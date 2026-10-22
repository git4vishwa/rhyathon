# ScriptSense — Working Vision Prototype

A hackathon-ready Flask prototype for the ScriptSense problem statement: photograph a handwritten prescription, extract medication candidates, show confidence, generate a draft dosage schedule, flag illustrative safety rules, and ask a local LLM to explain the structured results.

## Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Important OCR change

The upload pipeline is now **vision-first** and is no longer restricted to a tiny hard-coded medicine list.

- `gemma3:4b` in Ollama receives the prescription image directly.
- The prompt asks for a transcription plus medication candidates in JSON.
- The model may return **any medicine name it can read**; unfamiliar names are preserved rather than discarded.
- A small local vocabulary is used only to normalize close matches such as spelling variations. It is **not a whitelist**.
- The app sends both an original image and a contrast-enhanced view to the vision model to help with faint handwriting.
- The raw transcription is displayed in **Medication recognition → Raw prescription readback** so you can immediately see what the model actually read.
- If vision extraction fails, Tesseract remains an optional fallback.

This improves prototype coverage, but it is not clinically reliable handwriting recognition. Low-confidence or uncertain readings are explicitly marked for human verification.

## Model

The default model is:

```text
gemma3:4b
```

You can change it with the `SCRIPTSENSE_MODEL` environment variable.

Verify the model with:

```powershell
ollama list
```

## What works

- **Load demo** calls `/api/analyze-demo` and fills the full UI with a deterministic case.
- **Upload prescription** calls `/api/analyze` with a real image.
- **Vision handwriting extraction** returns arbitrary medication names, strength, form, directions, and a prototype visual-read confidence estimate.
- The app normalizes known names but does not reject unknown medication names.
- Dosage entries are generated from recognized frequency text.
- A small transparent interaction-rule table generates illustrative safety findings. It is not a clinical drug database.
- `/api/ai-review` sends only structured evidence to local Ollama.
- `/api/verify` marks a low-confidence medication as manually verified.
- SQLite stores the case, medications, and findings in `database/scriptsense.db`.

> Decision-support prototype only. The original prescription and all medication/safety results must be verified by a qualified clinician or pharmacist before use.
