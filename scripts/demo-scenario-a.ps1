param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
Invoke-RestMethod -Headers $apiHeaders "$BaseUrl/api/v1/demo/seed" -Method Post | Out-Null
$state = Invoke-RestMethod -Headers $apiHeaders "$BaseUrl/api/v1/demo/scenarios/A"
$body = @{expected_generation=$state.generation} | ConvertTo-Json
$state = Invoke-RestMethod -Headers $apiHeaders "$BaseUrl/api/v1/demo/scenarios/A/reset" -Method Post -ContentType 'application/json' -Body $body
foreach ($hour in @('09','10','11','12')) {
    $timestamp = "2026-10-16T${hour}:00:00Z"
    $body = @{expected_generation=$state.generation; through=$timestamp} | ConvertTo-Json
    Invoke-RestMethod -Headers $apiHeaders "$BaseUrl/api/v1/demo/scenarios/A/replay" -Method Post -ContentType 'application/json' -Body $body | Out-Null
    $snapshot = Invoke-RestMethod -Headers $apiHeaders "$BaseUrl/api/v1/demo/scenarios/A/snapshot?at=$timestamp"
    $roles = @($snapshot.reachable_roles.'identity/alice').Count
    $paths = @($snapshot.potential_sensitive_paths).Count
    Write-Host "$timestamp | Alice reachable roles=$roles | potential sensitive paths=$paths | $($snapshot.semantic_sha256)"
}
