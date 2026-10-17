param(
    [ValidateRange(5, 100)][int]$Repetitions = 20,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Install the Python 3.12 environment using scripts/setup.ps1 first.'
}
$previousPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'backend'
    $arguments = @('-u', '-m', 'app.demo.hackathon', '--repetitions', "$Repetitions")
    if ($OutputDirectory) { $arguments += @('--output', $OutputDirectory) }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Hackathon evaluation failed; see docs/TROUBLESHOOTING.md.' }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
