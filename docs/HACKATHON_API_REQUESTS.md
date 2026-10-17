# Example API requests for the backend demonstration

These use the ordinary backend and your existing scoped credential. The single-command
evaluator uses an isolated graph and owns its restart processes; these manual API calls
do not silently reset or restart anything. Start a fresh demo graph for the clean baseline.
All example cloud identifiers belong to the synthetic fixture.

```powershell
. .\scripts\api-client.ps1
$headers = Get-ShadowChainHeaders
$base = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/ready"
Invoke-RestMethod "$base/demo/seed" -Method Post -Headers $headers

$state = Invoke-RestMethod "$base/demo/scenarios/A" -Headers $headers
foreach ($hour in 9, 10, 11, 12) {
    $at = "2026-10-16T${hour}:00:00Z"
    if ($hour -eq 9) { $at = '2026-10-16T09:00:00Z' }
    $replay = @{scenario='A'; expected_generation=$state.generation; through=$at} |
        ConvertTo-Json
    Invoke-RestMethod "$base/demo/replay" -Method Post -Headers $headers `
        -ContentType application/json -Body $replay
    $analysis = @{scenario='A'; at=$at} | ConvertTo-Json
    $paths = Invoke-RestMethod "$base/risk/paths/discover" -Method Post -Headers $headers `
        -ContentType application/json -Body $analysis
    Write-Output "At $at : $($paths.findings.Count) potential sensitive paths"
}
```

The last `$paths.findings[0]` contains identity, roles, permission ID, resource, modeled
relationship IDs, contributing events, score factors and uncertainty. Query its evidence
and visualization projection:

```powershell
$riskId = $paths.findings[0].finding_id
Invoke-RestMethod "$base/risks/$riskId" -Headers $headers
Invoke-RestMethod "$base/graph/path?risk_id=$riskId" -Headers $headers
Invoke-RestMethod "$base/graph/timeline?before=2026-10-16T09%3A00%3A00Z&after=2026-10-16T12%3A00%3A00Z" -Headers $headers

$remove = @{scenario='A'; at='2026-10-16T12:00:00Z';
    remove_relationship_ids=@($paths.findings[0].relationship_ids[-3])} | ConvertTo-Json
Invoke-RestMethod "$base/what-if/remove-grant" -Method Post -Headers $headers `
    -ContentType application/json -Body $remove
```

Discover the latest synthetic scenario event, trigger the complete six-agent workflow,
then inspect shared memory, handoffs and cases:

```powershell
$snapshot = Invoke-RestMethod "$base/demo/scenarios/A/snapshot?at=2026-10-16T12%3A00%3A00Z" -Headers $headers
$event = $snapshot.nodes | Where-Object { $_.kind -eq 'SecurityEvent' -and $_.sequence -eq 3 } |
    Select-Object -First 1
$trigger = @{request_id='demo/phase11/manual'; event_id=$event.id} | ConvertTo-Json
$workflow = Invoke-RestMethod "$base/agents/run" -Method Post -Headers $headers `
    -ContentType application/json -Body $trigger
Invoke-RestMethod "$base/agents/memory?investigation_id=$($workflow.id)" -Headers $headers
Invoke-RestMethod "$base/agents/investigations/$($workflow.id)/handoffs" -Headers $headers
$cases = Invoke-RestMethod "$base/investigations" -Headers $headers
$caseId = $cases.investigations[0].investigation_id
Invoke-RestMethod "$base/investigations/$caseId" -Headers $headers
Invoke-RestMethod "$base/dashboard/summary" -Headers $headers
```

`POST /agents/run` completes synchronously; it is not a partial-run injection API. The
trusted evaluator stops at explicit durable stage boundaries to test restart recovery.
Public requests cannot choose an arbitrary agent, tenant, Cypher statement or command.
Use [the evaluator](HACKATHON_DEMO.md) for the actual restart and conflicting-memory proof.
See [API_CONTRACT.md](API_CONTRACT.md) and [OPENAPI_V1.json](OPENAPI_V1.json) for request
schemas, pagination, authentication and WebSocket replay. Recorded, unedited case output
is in [hackathon_investigation_example.json](hackathon_investigation_example.json).
