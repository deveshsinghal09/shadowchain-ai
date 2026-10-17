param()
$ErrorActionPreference = 'Stop'
$tokenFile = Join-Path $env:LOCALAPPDATA 'ShadowChainAI/railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01/operator.token'
if (-not (Test-Path -LiteralPath $tokenFile)) {
    throw 'The hosted operator token is not on this computer. Retrieve GRAPHVERSE_OPERATOR_TOKEN from your Railway graphverse-api service Variables instead.'
}
Set-Clipboard -Value ([IO.File]::ReadAllText($tokenFile).Trim())
Write-Host 'Hosted operator token copied. Paste it into the connection dialog at https://shadowchain-ai.vercel.app, then clear your clipboard.'
