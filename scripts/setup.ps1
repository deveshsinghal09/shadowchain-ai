param([switch]$SkipPython)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $randomBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    $localPassword = ([BitConverter]::ToString($randomBytes)).Replace('-', '').ToLowerInvariant()
    $template = [IO.File]::ReadAllText((Join-Path $projectRoot '.env.example'))
    $template = $template.Replace('replace-with-random-local-password', $localPassword)
    [IO.File]::WriteAllText($envPath, $template, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Created .env with a generated local FalkorDB password.'
} else {
    Write-Host 'Existing .env retained.'
}

& (Join-Path $PSScriptRoot 'setup-api-auth.ps1')

if (-not $SkipPython) {
    Push-Location (Join-Path $projectRoot 'backend')
    try {
        if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
            py -3.12 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, or use -SkipPython for Docker only.' }
        }
        & .\.venv\Scripts\python.exe -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
        if ($LASTEXITCODE -ne 0) { throw 'The existing virtual environment must use Python 3.12.' }
        # uv-created virtual environments may not include pip yet.
        & .\.venv\Scripts\python.exe -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip in the virtual environment.' }
        & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & .\.venv\Scripts\python.exe -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Dependency compatibility check failed.' }
    } finally { Pop-Location }
}
