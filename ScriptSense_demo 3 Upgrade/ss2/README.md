# ScriptSense Working Prototype

This version uses **Gemma 3 4B multimodal vision through Ollama as the first-pass handwriting OCR**, with Tesseract retained as a fallback for ordinary printed/clear text. Gemma 3 4B supports image input in Ollama.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Ollama

Make sure Ollama is running and the model is installed:

```powershell
ollama list
```

Look for:

```text
gemma3:4b
```

If it is missing:

```powershell
ollama pull gemma3:4b
```

The app calls Ollama locally at `http://localhost:11434` and keeps the model warm between requests.

## OCR behavior

The prototype deliberately asks the vision model **not to guess** uncertain handwriting. Low-confidence medication matches remain in the manual-verification workflow.

This is a hackathon demonstration and not a clinical-grade OCR or medication-safety system.
