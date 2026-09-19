# One-time setup for the Personal Agent (Windows).
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File .\setup.ps1

$ErrorActionPreference = "Continue"

Write-Host "== 1/3 Python packages ==" -ForegroundColor Cyan
python -m pip install -U pip
python -m pip install -r requirements.txt

Write-Host "== 2/3 Ollama models (skipped automatically if Ollama is missing) ==" -ForegroundColor Cyan
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    ollama pull qwen3:4b        # chat + tools
    ollama pull ornith-1.5:9b   # vision (look_at_screen)
} else {
    Write-Warning "Ollama not found. Install it from https://ollama.com and re-run this script."
}

Write-Host "== 3/3 Checking Ollama server ==" -ForegroundColor Cyan
try {
    $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3
    Write-Host "Ollama is running." -ForegroundColor Green
} catch {
    Write-Warning "Ollama is not reachable at localhost:11434 - start the Ollama app before running the agent."
}

Write-Host "== 4/3 Neural voice model (Kokoro, ~340 MB, stored outside the repo) ==" -ForegroundColor Cyan
$ttsDir = Join-Path $PSScriptRoot ".agent\tts"
New-Item -ItemType Directory -Force -Path $ttsDir | Out-Null
if ((Test-Path "$ttsDir\kokoro-v1.0.onnx") -and (Test-Path "$ttsDir\voices-v1.0.bin")) {
    Write-Host "Kokoro model already present." -ForegroundColor Green
} else {
    curl.exe -sL -o "$ttsDir\kokoro-v1.0.onnx" "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    curl.exe -sL -o "$ttsDir\voices-v1.0.bin" "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    Write-Host "Kokoro voice model downloaded." -ForegroundColor Green
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Text chat : python agent.py"
Write-Host "  Voice     : python agent.py --voice   (or double-click START.bat)"
