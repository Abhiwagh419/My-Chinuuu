$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Chinuuu virtual environment was not found. Create .venv and install the project extras first."
}
& $Python (Join-Path $PSScriptRoot "gui_entry.py")
exit $LASTEXITCODE
