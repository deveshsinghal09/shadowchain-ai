param(
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$PrincipalId = 'demo-operator',
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$TenantId = 'shadowchain-synthetic-demo',
    [ValidateSet('read','write','review','aws:sync')][string[]]$Permissions = @('read','write','review')
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'api-client.ps1')
$projectRoot = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Run scripts/setup.ps1 to create .env first.' }
$config = [IO.File]::ReadAllText($envPath)
$match = [regex]::Match($config, '(?m)^API_CLIENTS=([^\r\n]*)')
$clients = @()
if ($match.Success -and $match.Groups[1].Value.Trim()) {
    $clients = @(ConvertFrom-Json $match.Groups[1].Value)
}
$keyPath = Get-ShadowChainTokenPath -PrincipalId $PrincipalId
if (@($clients | Where-Object {$_.principal_id -eq $PrincipalId}).Count -gt 0) {
    if (-not (Test-Path -LiteralPath $keyPath)) {
        throw 'Principal already configured; restore its operator credential or add a distinct principal.'
    }
    Write-Host 'Existing API principal and local credential retained.'
    return
}
$directory = Split-Path $keyPath -Parent
$null = New-Item -ItemType Directory -Path $directory -Force
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$null = & icacls.exe $directory /inheritance:r /grant:r "*${sid}:(OI)(CI)F"
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the local credential directory.' }
$randomBytes = New-Object byte[] 32
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
$token = ([BitConverter]::ToString($randomBytes)).Replace('-', '').ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try { $digest = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($token)) } finally { $sha.Dispose() }
$entry = [PSCustomObject]@{
    principal_id=$PrincipalId; tenant_id=$TenantId; permissions=$Permissions
    token_sha256=([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
}
$clients = @($clients) + @($entry)
$json = ConvertTo-Json -InputObject $clients -Depth 5 -Compress
$line = "API_CLIENTS=$json"
if ($match.Success) {
    $config = $config.Substring(0,$match.Index) + $line + $config.Substring($match.Index+$match.Length)
} else { $config += "`r`n$line`r`n" }
[IO.File]::WriteAllText($keyPath,$token,(New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText($envPath,$config,(New-Object Text.UTF8Encoding($false)))
Write-Host 'Configured a scoped API principal; token stored privately under LOCALAPPDATA. Restart backend.'
