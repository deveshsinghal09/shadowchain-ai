param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1'
$null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/seed" -Method Post
$state = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A"
$body = @{expected_generation=$state.generation} | ConvertTo-Json
$null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A/replay" -Method Post `
    -ContentType 'application/json' -Body $body
$snapshot = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A/snapshot?at=2026-10-16T12%3A00%3A00Z"
$event = $snapshot.nodes | Where-Object { $_.kind -eq 'SecurityEvent' -and $_.sequence -eq 3 } |
    Select-Object -First 1
if ($null -eq $event) { throw 'Could not locate the synthetic 12:00 source event' }
$request = @{request_id='demo/phase6/powershell'; event_id=$event.id} | ConvertTo-Json
$result = Invoke-RestMethod -Headers $apiHeaders "$base/agents/investigations" -Method Post `
    -ContentType 'application/json' -Body $request
if ($result.status -ne 'completed' -or $result.completed_steps -ne 6) {
    throw 'The six-agent investigation did not complete'
}
Write-Output "Investigation=$($result.id) | status=$($result.status) | agents=$($result.runs.Count)"
$result.runs | Select-Object stage, status, id | Format-Table -AutoSize
$response = $result.runs | Where-Object { $_.stage -eq 'response_architect' }
$response.output.proposals | ConvertTo-Json -Depth 8
$memories = Invoke-RestMethod -Headers $apiHeaders "$base/agents/investigations/$($result.id)/memories"
$handoffs = Invoke-RestMethod -Headers $apiHeaders "$base/agents/investigations/$($result.id)/handoffs"
Write-Output "Shared memories=$($memories.Count) | durable handoffs=$($handoffs.Count)"
$duplicate = Invoke-RestMethod -Headers $apiHeaders "$base/agents/investigations" -Method Post `
    -ContentType 'application/json' -Body $request
if ($duplicate.id -ne $result.id -or $duplicate.tasks.Count -ne $result.tasks.Count) {
    throw 'Duplicate investigation trigger created unexpected tasks'
}
Write-Output 'Duplicate trigger reused the investigation and tasks; no remediation was executed'
