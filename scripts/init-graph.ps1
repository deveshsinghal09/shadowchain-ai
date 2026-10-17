param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $arguments = @('compose', 'exec', '-T', 'backend', 'python', '-m', 'app.db.initialize')
    if ($VerifyOnly) { $arguments += '--verify-only' }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Graph schema initialization/verification failed.' }
} finally { Pop-Location }
