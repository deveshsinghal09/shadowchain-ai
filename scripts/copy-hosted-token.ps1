param([switch]$FromRailway)
$ErrorActionPreference = 'Stop'
$tokenFile = Join-Path $env:LOCALAPPDATA 'ShadowChainAI/railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01/operator.token'
$token = $null
if (-not $FromRailway -and (Test-Path -LiteralPath $tokenFile)) {
    try { $token = [IO.File]::ReadAllText($tokenFile).Trim() }
    catch { $token = $null }
}
if (-not $token) {
    # Use the Windows command shim so this also works in Windows PowerShell 5.1.
    # Capture all CLI output: variable JSON includes other secrets and must never
    # be printed, included in exceptions, or written to a temporary file.
    $npxCommand = Get-Command npx.cmd -ErrorAction SilentlyContinue
    if (-not $npxCommand) {
        throw 'Node.js/npm is required to retrieve the hosted token. Alternatively, copy GRAPHVERSE_OPERATOR_TOKEN from Railway > graphverse-api > Variables.'
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $variableOutput = & $npxCommand.Source --yes '@railway/cli@5.70.0' variable list --project '2f4a53bd-bd2a-4db6-8098-45fe33570a01' --environment production --service graphverse-api --json 2>$null
        $commandExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($commandExitCode -ne 0) {
        throw 'Railway token retrieval failed. Run npx.cmd --yes @railway/cli@5.70.0 login, sign into the deployment account, then retry. You can also copy GRAPHVERSE_OPERATOR_TOKEN directly from Railway > graphverse-api > Variables.'
    }
    try {
        $variables = ($variableOutput -join "`n") | ConvertFrom-Json
        $token = ([string]$variables.GRAPHVERSE_OPERATOR_TOKEN).Trim()
    } catch {
        throw 'Railway did not return readable configuration. Copy GRAPHVERSE_OPERATOR_TOKEN directly from Railway > graphverse-api > Variables.'
    } finally {
        $variableOutput = $null
        $variables = $null
    }
}
if ($token.Length -lt 32 -or $token.Length -gt 512 -or $token -match '\s') {
    throw 'The hosted token is missing or invalid. Check GRAPHVERSE_OPERATOR_TOKEN in Railway > graphverse-api > Variables.'
}
Set-Clipboard -Value $token
$token = $null
Write-Host 'Hosted operator token copied. Paste it into the connection dialog at https://shadowchain-ai.vercel.app, then clear your clipboard.'
