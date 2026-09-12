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

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Text chat : python agent.py"
Write-Host "  Voice     : python agent.py --voice   (or double-click START.bat)"
