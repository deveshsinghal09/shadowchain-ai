function Get-ShadowChainTokenPath {
    param([string]$PrincipalId = 'demo-operator')
    if ($PrincipalId -notmatch '^[A-Za-z0-9_-]{1,64}$') { throw 'Invalid API principal ID.' }
    $projectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot)) } finally { $sha.Dispose() }
    $name = ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant().Substring(0,16)
    return Join-Path (Join-Path $env:LOCALAPPDATA "ShadowChainAI\$name") "$PrincipalId.token"
}

function Get-ShadowChainHeaders {
    param([string]$PrincipalId = 'demo-operator')
    $token = $env:SHADOWCHAIN_API_TOKEN
    if (-not $token) {
        $path = Get-ShadowChainTokenPath -PrincipalId $PrincipalId
        if (-not (Test-Path -LiteralPath $path)) {
            throw 'Run scripts/setup-api-auth.ps1, or set SHADOWCHAIN_API_TOKEN in this shell.'
        }
        $token = [IO.File]::ReadAllText($path).Trim()
    }
    if ($token.Length -lt 32 -or $token.Length -gt 512) { throw 'Invalid API credential length.' }
    return @{Authorization="Bearer $token"}
}
