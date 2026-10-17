param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1/remediation'
$request = @{request_id='demo/phase8/powershell'; analysis=@{
    at='2026-10-16T13:00:00Z'; scenario='A'
}; limit=20} | ConvertTo-Json -Depth 5
$recommendations = Invoke-RestMethod -Headers $apiHeaders "$base/recommendations" -Method Post `
    -ContentType 'application/json' -Body $request
$proposal = $recommendations.proposals | Where-Object {
    $_.target_relationship.kind -eq 'GRANTS' -and
    $_.target_relationship.edge_key -eq 'grant/sensitive-read/objects' -and
    $_.category -eq 'remove_excessive_permission'
} | Select-Object -First 1
if ($null -eq $proposal) { throw 'Run the trusted remediation_proof CLI to prepare the synthetic graph first' }
$body = @{request_id='demo/phase8/powershell/simulation'} | ConvertTo-Json
$simulation = Invoke-RestMethod -Headers $apiHeaders "$base/proposals/$($proposal.proposal_id)/simulations" `
    -Method Post -ContentType 'application/json' -Body $body
$saved = Invoke-RestMethod -Headers $apiHeaders "$base/simulations/$($simulation.simulation_id)"
if ($saved.simulation_id -ne $simulation.simulation_id -or $saved.execution_allowed -ne $false) {
    throw 'The saved simulation did not satisfy the non-execution contract'
}
[PSCustomObject]@{
    Proposal=$proposal.proposal_id
    Status=$proposal.status
    Simulation=$saved.simulation_id
    BeforePaths=$saved.before.findings.Count
    AfterPaths=$saved.after.findings.Count
    AlternatePaths=$saved.alternate_paths.Count
    PotentialAccessImpacts=$saved.access_impact.Count
    HistoricalEvidence=$saved.historical_evidence_ids.Count
    GraphNodes=$saved.visualization.nodes.Count
    GraphRelationships=$saved.visualization.relationships.Count
    CloudGraphMutated=$saved.source_cloud_graph_mutated
    ExecutionAllowed=$saved.execution_allowed
} | Format-List
Write-Output $saved.explanation
Write-Output 'No recommendation was approved by this script; no AWS write is available.'
