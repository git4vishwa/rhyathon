$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path '.\.venv\Scripts\python.exe')) {
    Write-Host 'Creating the Kinetiq virtual environment...' -ForegroundColor Cyan
    python -m venv .venv
}
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Write-Host 'Checking/installing Python dependencies...' -ForegroundColor Cyan
& $venvPython -m pip install -r requirements.txt

if (-not $env:TESSERACT_CMD) {
    $candidate = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if (Test-Path $candidate) { $env:TESSERACT_CMD = $candidate }
}
Write-Host 'Starting Kinetiq Lab at http://127.0.0.1:5000' -ForegroundColor Green
& $venvPython app.py
