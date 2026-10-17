param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1'
$cases = Invoke-RestMethod -Headers $apiHeaders "$base/investigations"
if ($cases.count -eq 0) {
    $null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/seed" -Method Post
    $state = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A"
    $body = @{expected_generation=$state.generation} | ConvertTo-Json
    $null = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A/replay" -Method Post `
        -ContentType 'application/json' -Body $body
    $snapshot = Invoke-RestMethod -Headers $apiHeaders "$base/demo/scenarios/A/snapshot?at=2026-10-16T12%3A00%3A00Z"
    $event = $snapshot.nodes | Where-Object { $_.kind -eq 'SecurityEvent' -and $_.sequence -eq 3 } |
        Select-Object -First 1
    if ($null -eq $event) { throw 'Could not locate the synthetic 12:00 source event' }
    $request = @{request_id='demo/phase7/powershell'; event_id=$event.id} | ConvertTo-Json
    $workflow = Invoke-RestMethod -Headers $apiHeaders "$base/agents/investigations" -Method Post `
        -ContentType 'application/json' -Body $request
    if ($workflow.status -ne 'completed') { throw 'The agent workflow did not complete' }
    $cases = Invoke-RestMethod -Headers $apiHeaders "$base/investigations"
}
if ($cases.count -eq 0) { throw 'Risk Oracle did not create an investigation' }
$caseId = ($cases.investigations | Select-Object -Last 1).investigation_id
$case = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId"
$timeline = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/timeline"
$graph = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/graph"
$history = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/history"
$activity = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/activity"
$memories = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/memories"
if ($case.verified_evidence.Count -eq 0 -or $case.current_hypotheses.Count -eq 0) {
    throw 'Missing case evidence or hypothesis'
}
$evidenceId = $case.verified_evidence[0].id
$evidence = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/evidence/$evidenceId"
if ($evidence.content_is_executable -ne $false) { throw 'Evidence must remain data' }
$handoffNode = $graph.nodes | Where-Object {
    $_.kind -eq 'Handoff' -and $_.properties.case_id -eq $caseId
} | Select-Object -First 1
if ($null -ne $handoffNode) {
    $handoff = Invoke-RestMethod -Headers $apiHeaders "$base/investigations/$caseId/handoffs/$($handoffNode.id)"
    Write-Output "Handoff=$($handoff.handoff_id) | completed checks at handoff=$($handoff.checkpoint.completed_task_ids.Count)"
}
[PSCustomObject]@{
    Investigation=$caseId
    Status=$case.status
    Severity=$case.severity
    CompletedTasks=$case.completed_tasks.Count
    PendingTasks=$case.pending_tasks.Count
    AgentRuns=$activity.runs.Count
    TimelineEntries=$timeline.items.Count
    GraphNodes=$graph.nodes.Count
    GraphRelationships=$graph.relationships.Count
    PriorIncidents=$history.previous_incidents.Count
    ValidatedHistoricalChecks=$history.useful_historical_checks.Count
    SharedMemories=$memories.memories.Count
    VerifiedEvidence=$case.verified_evidence.Count
    ContradictoryEvidence=$case.contradictory_evidence.Count
    AuditEntries=$case.audit_history.Count
} | Format-List
Write-Output 'POTENTIAL ATTACK PATHS; no AWS changes executed. Closure requires explicit review.'
