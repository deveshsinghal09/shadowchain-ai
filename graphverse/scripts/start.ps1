param([switch]$Dev, [switch]$CopyToken)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $appRoot -Parent
. (Join-Path $repoRoot 'scripts/api-client.ps1')
$tokenPath = Get-ShadowChainTokenPath
if (-not (Test-Path -LiteralPath $tokenPath)) {
    throw 'Run the repository scripts/setup.ps1 first to create the private operator token.'
}
$env:GRAPHVERSE_TOKEN_FILE = $tokenPath
if ($CopyToken) {
    Set-Clipboard -Value ([IO.File]::ReadAllText($tokenPath).Trim())
    Write-Host 'Operator token copied to clipboard. Paste into the local connection dialog, then clear your clipboard.'
}
Push-Location $appRoot
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) { npm ci; if ($LASTEXITCODE) { throw 'npm ci failed.' } }
    npm run seed
    if ($LASTEXITCODE) { throw 'FalkorDB seed failed. Check host, port, password and database availability.' }
    if ($Dev) { npm run dev }
    else {
        npm run build
        if ($LASTEXITCODE) { throw 'Frontend build failed.' }
        npm start
    }
    if ($LASTEXITCODE) { throw 'GraphVerse stopped with an error.' }
} finally { Pop-Location }
