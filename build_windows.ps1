$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "The Chinuuu executable build must run on Windows."
}
Set-Location -LiteralPath $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Create the .venv virtual environment and install project extras before building."
}

Write-Host "Running the complete test suite before packaging..." -ForegroundColor Cyan
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed. No executable will be built."
}

& $Python -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing. Install with: python -m pip install -e '.[installer]'"
}

$Common = @(
    "--noconfirm", "--clean", "--paths", $PSScriptRoot,
    "--hidden-import", "pycaw.pycaw",
    "--hidden-import", "winrt.windows.devices.radios",
    "--hidden-import", "pywinauto",
    "--hidden-import", "mss",
    "--hidden-import", "PIL",
    "--hidden-import", "pypdf",
    "--hidden-import", "docx",
    "--hidden-import", "pptx",
    "--hidden-import", "faster_whisper",
    "--hidden-import", "sounddevice",
    "--hidden-import", "soundfile",
    "--hidden-import", "pyttsx3",
    "--hidden-import", "openwakeword",
    "--hidden-import", "onnxruntime"
)

Write-Host "Building console application..." -ForegroundColor Cyan
& $Python -m PyInstaller @Common --name Chinuuu --console (Join-Path $PSScriptRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "Console executable build failed." }

Write-Host "Building desktop GUI application..." -ForegroundColor Cyan
& $Python -m PyInstaller @Common --name ChinuuuDesktop --windowed (Join-Path $PSScriptRoot "gui_entry.py")
if ($LASTEXITCODE -ne 0) { throw "Desktop executable build failed." }

$ConsoleRoot = Join-Path $PSScriptRoot "dist\Chinuuu"
$GuiRoot = Join-Path $PSScriptRoot "dist\ChinuuuDesktop"
New-Item -ItemType Directory -Force -Path $ConsoleRoot, $GuiRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "config.example.toml") -Destination (Join-Path $ConsoleRoot "config.example.toml") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "config.example.toml") -Destination (Join-Path $GuiRoot "config.example.toml") -Force
Write-Host "Builds created under .\dist\Chinuuu and .\dist\ChinuuuDesktop." -ForegroundColor Green
Write-Host "Before first launch, create %LOCALAPPDATA%\Chinuuu and copy config.example.toml there as config.toml." -ForegroundColor Yellow
Write-Host "Keep the Ollama service and local model installed separately; user data, workspace and speech models are not bundled." -ForegroundColor Green
