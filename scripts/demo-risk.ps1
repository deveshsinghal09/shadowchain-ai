param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1'
$null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/seed" -Method Post
$state = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A"
$replayBody = @{expected_generation=$state.generation} | ConvertTo-Json
$null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A/replay" -Method Post `
    -ContentType 'application/json' -Body $replayBody
$pathResult = $null
foreach ($hour in @(9, 10, 11, 12)) {
    $at = '2026-10-16T{0:00}:00:00Z' -f $hour
    $body = @{scenario='A'; at=$at} | ConvertTo-Json
    $pathResult = Invoke-RestMethod -Headers $apiHeaders "$base/risk/paths/historical" -Method Post `
        -ContentType 'application/json' -Body $body
    Write-Output "$at | potential paths=$($pathResult.findings.Count) | analysis=$($pathResult.analysis_id)"
}
if ($pathResult.findings.Count -ne 1) { throw 'Expected one potential sensitive path at 12:00' }
$finding = $pathResult.findings[0]
Write-Output "$($finding.classification) | score=$($finding.score.value) | $($finding.explanation)"
$window = @{scenario='A'; before='2026-10-16T09:00:00Z'; after='2026-10-16T12:00:00Z'} | ConvertTo-Json
$difference = Invoke-RestMethod -Headers $apiHeaders "$base/risk/differences" -Method Post -ContentType 'application/json' -Body $window
$difference.introductions | ConvertTo-Json -Depth 12
$removal = @{
    scenario='A'; at='2026-10-16T12:00:00Z'
    remove_relationship_ids=@($finding.relationship_ids[-3])
} | ConvertTo-Json
$simulation = Invoke-RestMethod -Headers $apiHeaders "$base/risk/what-if" -Method Post -ContentType 'application/json' -Body $removal
if ($simulation.after.findings.Count -ne 0 -or $simulation.graph_mutated) { throw 'Simulation failed' }
Write-Output "Simulated policy attachment removal: paths remaining=0; graph mutated=False"
