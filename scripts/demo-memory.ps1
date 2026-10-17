param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1/memory'

# Bootstrap first with the trusted CLI: docker compose exec -T backend python -m app.demo.memory_proof
# This script reads graph state; it does not grant roles or import an authoritative local file.
$context = Invoke-RestMethod -Headers $apiHeaders "$base/context" -Method Post `
    -ContentType 'application/json' -Body '{}'
$history = Invoke-RestMethod -Headers $apiHeaders "$base/retrieve" -Method Post `
    -ContentType 'application/json' -Body '{"include_superseded":true,"limit":100}'
$conflicts = Invoke-RestMethod -Headers $apiHeaders "$base/conflicts" -Method Post `
    -ContentType 'application/json' -Body '{"include_superseded":true,"limit":100}'
$types = @($context.observations.memory_type | Sort-Object -Unique)
foreach ($required in @('episodic', 'semantic', 'procedural')) {
    if ($required -notin $types) { throw "Missing $required memory; run the trusted memory proof CLI first" }
}
if ($context.privileged_actions.Count -ne 0 -or $context.source_content_is_instructions) {
    throw 'Memory context must contain data and read-only guidance'
}
if (@($context.observations | Where-Object { $_.state -ne 'current' -or $_.executable }).Count) {
    throw 'Current context contains an invalid memory state'
}
Write-Output "Agent=$($context.agent_id) | space=$($context.space_id)"
Write-Output "Current=$($context.observations.Count) | historical versions=$($history.Count) | types=$($types -join ', ')"
Write-Output "Verified semantic facts=$($context.established_facts.Count) | validated procedures=$($context.validated_procedures.Count)"
Write-Output "Retained conflict edges=$($conflicts.Count) | privileged actions=$($context.privileged_actions.Count)"
$context.observations | Select-Object id, memory_type, version, verification_status, state |
    Format-Table -AutoSize
